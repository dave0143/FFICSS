"""
DeltaD 噴灑策略模組
實現複雜的多步驟協調位置調整流程
支援debug模式
整合雲台控制和檢查功能
"""

import time
import threading
import traceback
import json
import paho.mqtt.client as mqtt
from modules.target_tracing import start_tracing_service, stop_tracing_service, get_tracing_service
from modules.ktgGimbalControl import KTGGimbalController
from modules.utils import cal_yaw_angle, cal_next_position
from modules.coordinate_conversion import calculate_target_position, calculate_required_angle, calculate_required_distance

def run_step(data_sources, config, logger, notify_complete):
    force_debug = config.get("debug_config", {}).get("step_005", False)
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="005", level=level)
    log("step_005_DeltaD 啟動")

    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    logger.log("執行 step_005_DeltaD...", step="005", source="step_module")

    # 初始化火源目標狀態
    fire_target_detected = False
    fire_target_coordinates = None
    uav_info_current = None
    completed = False
    mqtt_client = None
    
    def on_mqtt_connect(client, userdata, flags, rc):
        """MQTT 連線成功"""
        if rc == 0:
            print("[MQTT] 連線成功")
            log("MQTT 連線成功")
            client.subscribe("ai_fire/target", qos=1)
            client.subscribe("uav/info", qos=1)
            print("[MQTT] 已訂閱 ai_fire/target, uav/info")
        else:
            print(f"[MQTT] 連線失敗，代碼: {rc}")
            log(f"MQTT 連線失敗，代碼: {rc}", level="error")
    
    def on_mqtt_message(client, userdata, msg):
        """處理 MQTT 訊息"""
        nonlocal fire_target_detected, fire_target_coordinates, uav_info_current, completed
        
        if completed:
            return
            
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            
            if msg.topic == "ai_fire/target":
                print(f"[AI] 收到火源訊息: {payload}")
                if payload.get("status") is True:
                    fire_target_detected = True
                    if "coordinates" in payload:
                        coords = payload["coordinates"]
                        if coords and "X" in coords and "Y" in coords:
                            fire_target_coordinates = {"x": coords["X"], "y": coords["Y"]}
                    log(f"檢測到火源目標: 座標={fire_target_coordinates}")
                else:
                    fire_target_detected = False
                    
            elif msg.topic == "uav/info":
                uav_info_current = payload
                data_sources["uav_info"] = payload  # 同時更新 data_sources
                print(f"[UAV] 收到UAV資訊: 狀態={payload.get('status', 'unknown')}")
                
        except Exception as e:
            log(f"處理 MQTT 訊息錯誤: {e}", level="error")

    # 創建直接的 MQTT client
    try:
        mqtt_config = config.get("mqtt", {})
        broker = mqtt_config.get("broker", "localhost")
        port = mqtt_config.get("port", 1883)
        
        mqtt_client = mqtt.Client()
        mqtt_client.on_connect = on_mqtt_connect
        mqtt_client.on_message = on_mqtt_message
        
        print(f"[MQTT] 嘗試連接到 {broker}:{port}")
        mqtt_client.connect(broker, port, keepalive=60)
        mqtt_client.loop_start()
        log("MQTT client 已啟動")
        
    except Exception as e:
        log(f"MQTT 初始化失敗: {e}", level="error")
        notify_complete("005_complete", 99)
        return

    # 等待UAV資訊
    print(f"⏳ [初始化] 等待UAV資訊...")
    log("等待UAV資訊...")
    wait_start_time = time.time()
    for i in range(10):  # 等待5秒
        if uav_info_current:
            elapsed = time.time() - wait_start_time
            print(f"✅ [接收] UAV資訊已接收 (耗時: {elapsed:.1f}s)")
            break
        elapsed = time.time() - wait_start_time
        print(f"⏳ [等待] {elapsed:.1f}s - 等待UAV資訊...")
        time.sleep(0.5)
    else:
        print(f"⚠️ [超時] 5秒內未收到UAV資訊，繼續執行")

    # 等待gimbal與UAV資訊齊全
    print(f"📊 [解析] 開始解析UAV資訊...")
    gimbal_controller = data_sources.get("gimbal_controller")
    uav_info = uav_info_current or data_sources.get("uav_info")
    uav_yaw = None
    uav_gps = None
    
    if uav_info:
        print(f"📊 [資料] UAV資訊可用，開始解析各項參數...")
        
        # 獲取UAV yaw
        if "attitude" in uav_info and "yaw" in uav_info["attitude"]:
            uav_yaw = uav_info["attitude"]["yaw"]
            print(f"📊 [yaw] 從 attitude.yaw 獲取: {uav_yaw:.2f}°")
        elif "sensors" in uav_info and "imu" in uav_info["sensors"] and "yaw" in uav_info["sensors"]["imu"]:
            uav_yaw = uav_info["sensors"]["imu"]["yaw"]
            print(f"📊 [yaw] 從 sensors.imu.yaw 獲取: {uav_yaw:.2f}°")
        else:
            print(f"❌ [yaw] 無法獲取UAV yaw資訊")
        
        # 獲取UAV GPS
        navigation = uav_info.get("navigation", {})
        global_pos = navigation.get("global_position", {})
        if global_pos:
            uav_gps = {
                "lat": global_pos.get("lat", 0),
                "lon": global_pos.get("lon", 0),
                "alt": global_pos.get("alt", 0)
            }
            print(f"📊 [GPS] 位置資訊: lat={uav_gps['lat']:.6f}, lon={uav_gps['lon']:.6f}, alt={uav_gps['alt']:.2f}m")
        else:
            print(f"❌ [GPS] 無法獲取UAV GPS資訊")
    else:
        print(f"❌ [資料] UAV資訊不可用")

    # UAV轉向和雲台設置
    print(f"🚁 [準備] 檢查UAV和雲台狀態...")
    print(f"🚁 [狀態] 雲台控制器: {'✅可用' if gimbal_controller else '❌不可用'}")
    if gimbal_controller:
        print(f"🚁 [雲台] 連接狀態: {'✅已連接' if getattr(gimbal_controller, 'connected', False) else '❌未連接'}")
        print(f"🚁 [雲台] IP地址: {getattr(gimbal_controller, 'ip', 'N/A')}")
        print(f"🚁 [雲台] 端口: {getattr(gimbal_controller, 'port', 'N/A')}")
    print(f"🚁 [狀態] UAV yaw: {uav_yaw:.2f}° {'✅可用' if uav_yaw is not None else '❌不可用'}")
    print(f"🚁 [狀態] UAV GPS: {'✅可用' if uav_gps else '❌不可用'}")
    
    if gimbal_controller and uav_yaw is not None:
        try:
            # 首先檢查雲台連接狀態
            if not getattr(gimbal_controller, 'connected', False):
                print(f"❌ [錯誤] 雲台未連接，嘗試重新連接...")
                try:
                    if hasattr(gimbal_controller, 'connect'):
                        connect_result = gimbal_controller.connect()
                        print(f"🔗 [連接] 重連結果: {connect_result}")
                        if not connect_result:
                            print(f"❌ [錯誤] 雲台重連失敗，跳過雲台操作")
                            raise Exception("雲台重連失敗")
                    else:
                        raise Exception("雲台控制器無connect方法")
                except Exception as connect_e:
                    print(f"❌ [錯誤] 雲台連接異常: {connect_e}")
                    raise connect_e
            
            print(f"🎮 [雲台] 開始獲取雲台資訊...")
            # 獲取雲台資訊並計算轉向角度
            gimbal_info = gimbal_controller.listen_gimbal_info(max_attempts=3)
            if gimbal_info and gimbal_info.get("success"):
                yaw = gimbal_info.get("yaw", 0.0)
                angle = cal_yaw_angle(uav_yaw, yaw)
                
                print(f"🎮 [雲台] 當前雲台yaw: {yaw:.2f}°")
                print(f"🎮 [計算] UAV需轉向角度: {angle:.2f}°")
                
                # 發送UAV轉向指令
                if uav_gps:
                    command = [0, 0, 0, angle, 1.0, 0]
                    mission = calculate_target_position(uav_gps, float(uav_yaw), command)
                    mission["timestamp"] = time.time()
                    print(f"🚁 [指令] UAV轉向: {json.dumps(mission)}")
                    mqtt_client.publish("ai/target", json.dumps(mission))
                    log(f"已發送UAV轉向指令: angle={angle}")
                else:
                    print(f"❌ [錯誤] GPS資訊不可用，跳過UAV轉向")
            else:
                print(f"❌ [錯誤] 無法獲取雲台資訊: {gimbal_info}")
            
            # 設置雲台固定角度
            print(f"🎮 [雲台] 設置固定角度: yaw=0°, pitch=-15°")
            print(f"🎮 [指令] 發送yaw設置指令: mode=1, angle=0, reference=2")
            yaw_result = gimbal_controller.eo_rotate_to_angle(1, 0, 2)    # mode=1(yaw), angle=0, reference=2
            time.sleep(0.2)  # 給雲台一點時間處理指令
            
            print(f"🎮 [指令] 發送pitch設置指令: mode=2, angle=-15, reference=2")
            pitch_result = gimbal_controller.eo_rotate_to_angle(2, -15, 2)  # mode=2(pitch), angle=-15, reference=2
            time.sleep(0.2)  # 給雲台一點時間處理指令
            
            print(f"🎮 [結果] yaw設置: {yaw_result}")
            print(f"🎮 [結果] pitch設置: {pitch_result}")
            print(f"🎮 [狀態] yaw指令: {'✅成功' if yaw_result.get('success') else '❌失敗'}")
            print(f"🎮 [狀態] pitch指令: {'✅成功' if pitch_result.get('success') else '❌失敗'}")
            
            # 驗證設置是否成功
            print(f"🎮 [驗證] 等待1秒後驗證雲台角度...")
            time.sleep(1.0)
            verify_info = gimbal_controller.listen_gimbal_info(max_attempts=2)
            if verify_info and verify_info.get("success"):
                actual_yaw = verify_info.get("yaw", 0.0)
                actual_pitch = verify_info.get("pitch", 0.0)
                print(f"🎮 [驗證] 實際角度: yaw={actual_yaw:.2f}°, pitch={actual_pitch:.2f}°")
                yaw_error = abs(actual_yaw - 0.0)
                pitch_error = abs(actual_pitch - (-15.0))
                print(f"🎮 [誤差] yaw誤差: {yaw_error:.2f}°, pitch誤差: {pitch_error:.2f}°")
                if yaw_error < 2.0 and pitch_error < 2.0:
                    print(f"✅ [成功] 雲台角度設置成功 (誤差在2°以內)")
                else:
                    print(f"⚠️ [警告] 雲台角度設置可能不準確 (誤差超過2°)")
            else:
                print(f"❌ [驗證] 無法驗證雲台角度設置")
            
            log("雲台已設置為固定角度: yaw=0, pitch=-15")
            
        except Exception as e:
            print(f"❌ [錯誤] 雲台操作失敗: {e}")
            print(f"❌ [詳細] 錯誤類型: {type(e).__name__}")
            import traceback
            print(f"❌ [堆疊] {traceback.format_exc()}")
            log(f"雲台操作錯誤: {e}", level="warning")
    else:
        print(f"⚠️ [跳過] 雲台或UAV資訊不完整，跳過轉向設置")

    # 檢查火源目標
    print(f"🔍 [檢查] 火源檢測狀態: {'✅已檢測' if fire_target_detected else '❌未檢測'}")
    if fire_target_coordinates:
        print(f"🔍 [檢查] 火源座標: X={fire_target_coordinates.get('x', 'N/A')}, Y={fire_target_coordinates.get('y', 'N/A')}")
    else:
        print(f"🔍 [檢查] 火源座標: 無")
    
    if not fire_target_detected:
        print(f"❌ [錯誤] 未檢測到火源目標，無法進行位置校正")
        log("未檢測到火源目標，結束step", level="warning")
        completed = True
        notify_complete("005_complete", 1)
        return

    print(f"🎯 [開始] 火源已確認，開始精確位置校正流程")
    print(f"🎯 [參數] 螢幕尺寸: 1920x1080, 中心點: (960, 540)")
    print(f"🎯 [參數] 容許誤差: ±10 pixels")
    log("開始位置校正流程")

    # 左右旋轉校正 (fire_x 到中心)
    if fire_target_coordinates and "x" in fire_target_coordinates and uav_yaw and uav_gps:
        fire_x = fire_target_coordinates["x"]
        x_angle = calculate_required_angle(fire_x, 960, 1920, 75)
        new_ang = float(uav_yaw) - x_angle
        
        print(f"🎯 [校正] 火源X座標: {fire_x} (中心: 960)")
        print(f"🎯 [校正] 偏離距離: {abs(fire_x - 960)} pixels")
        print(f"🎯 [校正] 需要轉向角度: {x_angle:.2f}°")
        print(f"🎯 [校正] UAV當前yaw: {uav_yaw:.2f}° → 目標yaw: {new_ang:.2f}°")
        log(f"左右校正: fire_x={fire_x}, 偏離={abs(fire_x - 960)}px, 轉向角度={x_angle:.2f}°")
        
        # 發送旋轉指令
        lla_ref = [uav_gps["lat"], uav_gps["lon"], uav_gps["alt"]]
        new_lla = cal_next_position(lla_ref, [0, 0, 0])
        mission = {
            "task": "process",
            "mission": [{
                "waypoint": 1,
                "lat": float(new_lla[0]),
                "lon": float(new_lla[1]),
                "alt": float(new_lla[2]),
                "ang": new_ang,
                "flight_speed": 1.0,
                "loiter_time": 0
            }]
        }
        mqtt_client.publish("ai/target", json.dumps(mission))
        print(f"🚁 [MQTT] 已發送旋轉指令: {json.dumps(mission)}")
        log(f"發送旋轉校正指令: target_yaw={new_ang}")
        
        # 等待旋轉完成
        print(f"⏳ [等待] 開始等待旋轉完成，最多10秒...")
        wait_count = 0
        for _ in range(100):  # 最多等10秒
            wait_count += 1
            time.sleep(0.1)
            uav_info = uav_info_current or data_sources.get("uav_info")
            if not uav_info:
                if wait_count % 10 == 0:  # 每秒顯示一次
                    print(f"⏳ [等待] {wait_count/10:.1f}s - 等待UAV資訊...")
                continue
            
            current_status = uav_info.get("status", "")
            if wait_count % 20 == 0:  # 每2秒顯示一次
                print(f"⏳ [等待] {wait_count/10:.1f}s - UAV狀態: {current_status}")
                if fire_target_coordinates:
                    current_fire_x = fire_target_coordinates.get("x", 0)
                    distance_to_center = abs(current_fire_x - 960)
                    print(f"🎯 [監控] 當前fire_x: {current_fire_x}, 距離中心: {distance_to_center}px")
            
            if current_status.lower() == "complete":
                print(f"✅ [完成] UAV狀態為complete，旋轉校正完成！")
                log("旋轉校正完成")
                break
            if fire_target_coordinates and abs(fire_target_coordinates.get("x", 0) - 960) < 10:
                final_fire_x = fire_target_coordinates.get("x", 0)
                print(f"🎯 [完成] fire_x已在中心: {final_fire_x} (誤差: {abs(final_fire_x - 960)}px)")
                log("fire_x已在中心，旋轉完成")
                mqtt_client.publish("ai/target", json.dumps({"task": "stop"}))
                print(f"🚁 [MQTT] 已發送停止指令")
                break
        else:
            print(f"⏰ [超時] 旋轉校正等待超時 (10秒)")
            log("旋轉校正等待超時", level="warning")

    # 前後移動校正 (fire_y 到中心)
    if fire_target_coordinates and "y" in fire_target_coordinates and uav_yaw and uav_gps:
        fire_y = fire_target_coordinates["y"]
        y_distance = calculate_required_distance(fire_y, 540, 1080, uav_gps["alt"], 60)
        
        print(f"🎯 [校正] 火源Y座標: {fire_y} (中心: 540)")
        print(f"🎯 [校正] 偏離距離: {abs(fire_y - 540)} pixels")
        print(f"🎯 [校正] 需要移動距離: {y_distance:.2f}m ({'前進' if y_distance > 0 else '後退'})")
        print(f"🎯 [校正] UAV當前高度: {uav_gps['alt']:.2f}m")
        log(f"前後校正: fire_y={fire_y}, 偏離={abs(fire_y - 540)}px, 移動距離={y_distance:.2f}m")
        
        # 發送前後移動指令
        lla_ref = [uav_gps["lat"], uav_gps["lon"], uav_gps["alt"]]
        ned = [0, y_distance, 0]
        new_lla = cal_next_position(lla_ref, ned)
        mission = {
            "task": "process",
            "mission": [{
                "waypoint": 1,
                "lat": float(new_lla[0]),
                "lon": float(new_lla[1]),
                "alt": float(new_lla[2]),
                "ang": float(uav_yaw),
                "flight_speed": 1.0,
                "loiter_time": 0
            }]
        }
        print(f"📍 [位置] 當前GPS: lat={uav_gps['lat']:.6f}, lon={uav_gps['lon']:.6f}")
        print(f"📍 [位置] 目標GPS: lat={new_lla[0]:.6f}, lon={new_lla[1]:.6f}")
        mqtt_client.publish("ai/target", json.dumps(mission))
        print(f"🚁 [MQTT] 已發送移動指令: {json.dumps(mission)}")
        log(f"發送前後移動校正指令: y_distance={y_distance}")
        
        # 等待移動完成
        print(f"⏳ [等待] 開始等待前後移動完成，最多10秒...")
        wait_count = 0
        for _ in range(100):  # 最多等10秒
            wait_count += 1
            time.sleep(0.1)
            uav_info = uav_info_current or data_sources.get("uav_info")
            if not uav_info:
                if wait_count % 10 == 0:  # 每秒顯示一次
                    print(f"⏳ [等待] {wait_count/10:.1f}s - 等待UAV資訊...")
                continue
            
            current_status = uav_info.get("status", "")
            if wait_count % 20 == 0:  # 每2秒顯示一次
                print(f"⏳ [等待] {wait_count/10:.1f}s - UAV狀態: {current_status}")
                if fire_target_coordinates:
                    current_fire_y = fire_target_coordinates.get("y", 0)
                    distance_to_center = abs(current_fire_y - 540)
                    print(f"🎯 [監控] 當前fire_y: {current_fire_y}, 距離中心: {distance_to_center}px")
            
            if current_status.lower() == "complete":
                print(f"✅ [完成] UAV狀態為complete，前後移動校正完成！")
                log("前後移動校正完成")
                break
            if fire_target_coordinates and abs(fire_target_coordinates.get("y", 0) - 540) < 10:
                final_fire_y = fire_target_coordinates.get("y", 0)
                print(f"🎯 [完成] fire_y已在中心: {final_fire_y} (誤差: {abs(final_fire_y - 540)}px)")
                log("fire_y已在中心，前後移動完成")
                mqtt_client.publish("ai/target", json.dumps({"task": "stop"}))
                print(f"🚁 [MQTT] 已發送停止指令")
                break
        else:
            print(f"⏰ [超時] 前後移動校正等待超時 (10秒)")
            log("前後移動校正等待超時", level="warning")

    print(f"🎉 [完成] step_005_DeltaD 位置校正流程全部完成！")
    print(f"🎉 [總結] 左右校正: {'✅完成' if fire_target_coordinates and abs(fire_target_coordinates.get('x', 0) - 960) < 10 else '⚠️未達標'}")
    print(f"🎉 [總結] 前後校正: {'✅完成' if fire_target_coordinates and abs(fire_target_coordinates.get('y', 0) - 540) < 10 else '⚠️未達標'}")
    if fire_target_coordinates:
        final_x = fire_target_coordinates.get('x', 0)
        final_y = fire_target_coordinates.get('y', 0)
        print(f"🎉 [最終] 火源座標: X={final_x} (誤差:{abs(final_x-960)}px), Y={final_y} (誤差:{abs(final_y-540)}px)")
    log("step_005_DeltaD 執行完成")
    completed = True
    notify_complete("005_complete", 0)

    def stop():
        nonlocal completed
        logger.log("步驟005停止", step="005")
        completed = True
        
        # 清理MQTT資源
        if mqtt_client:
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
                log("MQTT client 已斷線")
            except:
                pass
    
    return {"stop": stop}

def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="005", level=level)
    
    log("執行 step_005 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬DeltaD計算過程"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬DeltaD計算過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
        
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 005", level="debug")
            # 模擬結果 (0=DeltaD計算完成, 99=錯誤)
            result = 0  # 預設模擬DeltaD計算完成
            notify_complete("005_complete", result)
            completed = True
        
        # 重置計時器3
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬DeltaD計算計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬DeltaD計算計時器", level="debug")
        
        log("DEBUG: 步驟005停止，資源已釋放")
    
    return {
        "stop": stop
    }
