"""
噴灑策略模組 - 直接控制設備版本 (適配新uav/info格式)
支援輔助校正功能和完整生命週期管理
使用run_step方式實現
支援debug模式
增強調試版本 - 詳細追蹤MQTT發送和狀態機流程
"""

import time
import threading
import json
import traceback
from typing import Dict, Any, Callable
from modules.coordinate_conversion import calculate_target_position

class SprayStrategyController:
    def __init__(self, data_sources, config, logger, notify_complete):
        self.data_sources = data_sources
        self.config = config
        self.logger = logger
        self.notify_complete = notify_complete
        
        # 取得噴灑策略配置
        self.spray_config = config.get("spray_strategy", {})
        self.enabled = self.spray_config.get("enabled", False)
        self.enable_aux_correction = self.spray_config.get("enable_aux_correction", False)
        self.target_angle = self.spray_config.get("target_angle", 1)
        self.suspension_time = self.spray_config.get("Suspension_time", 10)
        self.flight_speed = self.spray_config.get("flight_speed", 0.5)  # 新增飛行速度參數
        
        # 取得服務實例
        self.device = data_sources["device_controller"]
        self.mqtt = data_sources["mqtt_service"]
        self.flow_controller = data_sources["flow_controller"]
        
        # UAV 資訊 - 更新為新格式
        self.uav_info = None
        self.uav_info_received_count = 0  # 🔧 新增：追蹤收到的UAV資訊數量
        
        # 狀態控制
        self.running = True
        self.completed = False
        self.start_time = time.time()
        self._stop_requested = False
        self.stopped = False
        
        # 狀態機
        self.current_state = "INIT"
        self.angle_commands = []
        
        # 時間追蹤
        self.spray_start_time = 0
        self.suspension_start_time = 0
        
        # 🔧 新增：UAV資訊等待超時機制
        self.uav_info_timeout = 10  # 等待UAV資訊的最大時間（秒）
        self.last_uav_info_check = time.time()
        
        # 註冊 MQTT 處理器
        self.log("正在註冊MQTT處理器...")
        try:
            self.mqtt.add_handler("uav/info", self.on_mqtt_message)
            self.log("✅ MQTT處理器註冊成功：監聽 uav/info")
        except Exception as e:
            self.log(f"MQTT處理器註冊失敗: {e}", level="error")
        
        # 🔧 檢查MQTT服務狀態
        self.check_mqtt_service()
        
        self.log("噴灑策略控制器已初始化")
        self.log(f"配置: 進階模式={self.enable_aux_correction}, 角度={self.target_angle}°, 速度={self.flight_speed}m/s")

    def check_mqtt_service(self):
        """檢查MQTT服務狀態"""
        try:
            if not self.mqtt:
                self.log("MQTT服務為None", level="error")
                return False
            
            if not hasattr(self.mqtt, 'publish'):
                self.log("MQTT服務沒有publish方法", level="error")
                return False
            
            if not hasattr(self.mqtt, 'add_handler'):
                self.log("MQTT服務沒有add_handler方法", level="error")
                return False
            
            # 檢查MQTT配置
            uav_topic = self.config.get("uav_info_topic", "uav/info")
            ai_topic = self.config.get("ai_target_topic", "ai/target")
            self.log(f"MQTT配置: uav_info_topic={uav_topic}, ai_target_topic={ai_topic}")
            
            self.log("MQTT服務檢查通過")
            return True
            
        except Exception as e:
            self.log(f"MQTT服務檢查失敗: {e}", level="error")
            return False

    def log(self, message, level="info"):
        """記錄日誌"""
        self.logger.log(f"[SprayStrategy] {message}", step="006", level=level)

    def on_mqtt_message(self, topic, payload):
        """處理來自 MQTT 的 uav_info 訊息 - 增強調試版本"""
        if self.stopped or self.completed:
            self.log(f"忽略MQTT訊息 - 已停止:{self.stopped}, 已完成:{self.completed}", level="debug")
            return
            
        try:
            # 增強：詳細記錄收到的訊息
            self.uav_info_received_count += 1
            self.log(f"收到MQTT訊息 #{self.uav_info_received_count}: topic={topic}", level="debug")
            
            # 檢查payload格式
            if not isinstance(payload, dict):
                self.log(f"MQTT payload格式錯誤: {type(payload)}", level="warning")
                return
            
            # 檢查是否首次收到UAV資訊
            first_time = self.uav_info is None
            self.uav_info = payload
            self.last_uav_info_check = time.time()  # 更新最後收到時間
            
            if first_time:
                self.log("🛰️ 首次收到UAV資訊，開始處理狀態機", level="info")
                # 顯示UAV基本資訊
                nav = payload.get("navigation", {})
                pos = nav.get("global_position", {})
                sensors = payload.get("sensors", {})
                imu = sensors.get("imu", {})
                
                self.log(f"UAV位置: lat={pos.get('lat', 0):.6f}, lon={pos.get('lon', 0):.6f}, alt={pos.get('alt', 0):.2f}m")
                self.log(f"UAV航向: {imu.get('yaw', 0):.2f}°")
                self.log(f"UAV模式: {nav.get('flight_mode', 'unknown')}, 狀態: {payload.get('status', 'unknown')}")
                
                # 🔧 驗證UAV資訊完整性
                self.validate_uav_info(payload)
            else:
                self.log(f"📡 收到UAV資訊更新 #{self.uav_info_received_count}", level="debug")
            
            # 收到資訊後繼續處理狀態機邏輯
            self.handle_step()
            
        except Exception as e:
            self.log(f"處理 MQTT 訊息時錯誤: {e}", level="error")
            import traceback
            self.log(f"錯誤詳情: {traceback.format_exc()}", level="error")

    def validate_uav_info(self, payload):
        """驗證UAV資訊的完整性"""
        try:
            # 檢查必要的字段
            required_fields = ["navigation", "status", "sensors"]
            missing_fields = []
            
            for field in required_fields:
                if field not in payload:
                    missing_fields.append(field)
            
            if missing_fields:
                self.log(f"UAV資訊缺少字段: {missing_fields}", level="warning")
            
            # 檢查navigation字段
            nav = payload.get("navigation", {})
            if "global_position" not in nav:
                self.log("navigation缺少global_position", level="warning")
            if "flight_mode" not in nav:
                self.log("navigation缺少flight_mode", level="warning")
            
            # 檢查sensors字段
            sensors = payload.get("sensors", {})
            if "imu" not in sensors:
                self.log("sensors缺少imu", level="warning")
            else:
                imu = sensors.get("imu", {})
                if "yaw" not in imu:
                    self.log("imu缺少yaw", level="warning")
            
            # 檢查位置資訊
            pos = nav.get("global_position", {})
            lat = pos.get("lat", 0)
            lon = pos.get("lon", 0)
            alt = pos.get("alt", 0)
            
            if lat == 0 and lon == 0:
                self.log("UAV位置似乎無效 (0, 0)", level="warning")
            
            self.log("UAV資訊驗證完成")
            
        except Exception as e:
            self.log(f"UAV資訊驗證失敗: {e}", level="error")

    def stop_spray(self):
        """停止噴灑策略"""
        if not self.completed:
            self.log("外部請求停止噴灑策略", level="warning")
            self._stop_requested = True
            self.device.stop_spray()
            self.completed = True
            # 移除 MQTT 處理器
            self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
            self.stopped = True
            self.notify_complete("006_complete", 1)  # 強制完成

    def handle_step(self):
        """處理噴灑策略步驟"""
        try:
            # 🔧 新增：檢查UAV資訊等待超時
            if not self.uav_info:
                time_waiting = time.time() - self.start_time
                if time_waiting > self.uav_info_timeout:
                    self.log(f"⏰ 等待UAV資訊超時 ({time_waiting:.1f}s > {self.uav_info_timeout}s)", level="error")
                    self.log("💡 建議檢查：1) 模擬器是否運行 2) MQTT連接狀態 3) uav/info topic配置", level="error")
                    # 超時處理：可以選擇繼續等待或報錯退出
                    self.completed = True
                    self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                    self.stopped = True
                    self.notify_complete("006_complete", 99)  # 超時錯誤
                    return
                else:
                    # 還在等待期間，顯示等待狀態
                    if int(time_waiting) % 2 == 0:  # 每2秒顯示一次，避免日誌過多
                        self.log(f"⏳ 等待UAV資訊中... ({time_waiting:.1f}s/{self.uav_info_timeout}s)", level="info")
                    return
            
            # 收到UAV資訊後的正常處理邏輯
            if self.enable_aux_correction:
                self.log("啟用進階噴灑策略（含輔助校正）")
                self.handle_aux_correction_spray()
            else:
                self.log("使用基本噴灑策略")
                self.handle_basic_spray()
            
            # 完成後通知流程控制器 (僅適用於基本噴灑策略或未在狀態機中處理的情況)
            if self.completed and not self._stop_requested and not self.stopped:
                # 移除 MQTT 處理器
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 0)
                
        except Exception as e:
            self.log(f"噴灑策略執行錯誤: {e}", "error")
            self.log(f"錯誤詳情: {traceback.format_exc()}", "error")
            self.completed = True
            if not self._stop_requested and not self.stopped:
                # 移除 MQTT 處理器
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 1)  # 錯誤狀態

    def handle_basic_spray(self):
        """處理基本噴灑策略"""
        current_time = time.time()
        
        if self.current_state == "INIT":
            # 觸發設備噴灑
            self.log("💧 觸發設備噴灑")
            self.device.start_spray()
            self.spray_start_time = current_time
            self.current_state = "SPRAYING"
            
        elif self.current_state == "SPRAYING":
            # 檢查噴灑時間是否結束
            spray_duration = self.config["device"].get("active_duration", 10)
            if current_time - self.spray_start_time >= spray_duration:
                if not self._stop_requested:
                    self.device.stop_spray()
                    self.log(f"等待懸停時間: {self.suspension_time}秒")
                    self.suspension_start_time = current_time
                    self.current_state = "SUSPENDING"
            
        elif self.current_state == "SUSPENDING":
            # 檢查懸停時間是否結束
            if current_time - self.suspension_start_time >= self.suspension_time:
                self.log("基本噴灑流程完成")
                self.completed = True
                # 移除 MQTT 處理器
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 0)
                
        # 檢查中斷請求
        if self._stop_requested:
            self.device.stop_spray()
            self.completed = True
            if not self.stopped:
                # 移除 MQTT 處理器
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 1)  # 強制停止

    def handle_aux_correction_spray(self):
        """處理進階噴灑策略 - 增強調試版本"""
        self.log(f"進階噴灑策略狀態機: {self.current_state}", level="debug")

        if self._stop_requested:
            self.device.stop_spray()
            self.completed = True
            if not self.stopped:
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 1)
            return
            
        if self.current_state == "INIT":
            # 步驟1: 負角度旋轉
            self.log("步驟1: 負角度旋轉開始")
            angle = -self.target_angle
            self.send_rotation_command(angle, 0)
            self.current_state = "WAIT_FOR_FIRST_ROTATION"
            self.log("⏳ 狀態轉換: INIT -> WAIT_FOR_FIRST_ROTATION")
            
        elif self.current_state == "WAIT_FOR_FIRST_ROTATION":
            # 檢查是否完成第一次旋轉
            if self.check_mission_complete():
                self.log("第一次旋轉完成")
                self.current_state = "START_SPRAY"
                self.log("狀態轉換: WAIT_FOR_FIRST_ROTATION -> START_SPRAY")
            else:
                self.log("等待第一次旋轉完成...", level="debug")
        
        elif self.current_state == "START_SPRAY":
            # 步驟2: 啟動噴灑
            self.log("步驟2: 啟動噴灑")
            self.device.start_spray()
            # 步驟3: 正角度旋轉（兩倍角度）
            self.log("步驟3: 正角度旋轉（兩倍角度）")
            angle = 2 * self.target_angle
            self.send_rotation_command(angle, 0)
            self.current_state = "WAIT_FOR_SECOND_ROTATION"
            self.log("狀態轉換: START_SPRAY -> WAIT_FOR_SECOND_ROTATION")
        
        elif self.current_state == "WAIT_FOR_SECOND_ROTATION":
            # 檢查是否完成第二次旋轉
            if self.check_mission_complete():
                self.log("第二次旋轉完成")
                self.current_state = "STOP_SPRAY"
                self.log("狀態轉換: WAIT_FOR_SECOND_ROTATION -> STOP_SPRAY")
            else:
                self.log("等待第二次旋轉完成...", level="debug")
        
        elif self.current_state == "STOP_SPRAY":
            # 步驟4: 停止噴灑
            self.log("步驟4: 停止噴灑")
            self.device.stop_spray()
            # 步驟5: 負角度旋轉（返回原位）並懸停
            self.log("步驟5: 負角度旋轉（返回原位）並懸停")
            angle = -self.target_angle
            self.send_rotation_command(angle, self.suspension_time)
            self.current_state = "WAIT_FOR_RETURN"
            self.log("狀態轉換: STOP_SPRAY -> WAIT_FOR_RETURN")
        
        elif self.current_state == "WAIT_FOR_RETURN":
            # 檢查是否完成返回旋轉
            if self.check_mission_complete():
                self.log("返回旋轉完成")
                self.current_state = "COMPLETE"
                self.log("狀態轉換: WAIT_FOR_RETURN -> COMPLETE")
            else:
                self.log("等待返回旋轉完成...", level="debug")
        
        elif self.current_state == "COMPLETE":
            self.log("輔助校正噴灑流程完成")
            self.completed = True
            self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
            self.stopped = True
            self.notify_complete("006_complete", 0)
        
        # 檢查錯誤狀態
        if self.flow_controller.get_flow_state() == "ERROR":
            self.log("流程處於錯誤狀態，終止噴灑", "error")
            self.device.stop_spray()
            self.completed = True
            if not self.stopped:
                self.mqtt.remove_handler("uav/info", self.on_mqtt_message)
                self.stopped = True
                self.notify_complete("006_complete", 99)

    def send_rotation_command(self, angle, loiter_time=0):
        """發送旋轉命令 - 增強調試版本"""
        self.log(f"調用 send_rotation_command: angle={angle}°, loiter_time={loiter_time}s")
        
        try:
            navigation = self.uav_info.get("navigation", {})
            global_pos = navigation.get("global_position", {})
            
            # 從sensors獲取yaw角度
            sensors = self.uav_info.get("sensors", {})
            imu = sensors.get("imu", {})
            uav_yaw = imu.get("yaw", 0)

            # 構建GPS資訊
            uav_gps = {
                "lat": global_pos.get("lat", 0),
                "lon": global_pos.get("lon", 0),
                "alt": global_pos.get("alt", 0)
            }
            
            # 驗證GPS資訊有效性
            if uav_gps["lat"] == 0 and uav_gps["lon"] == 0:
                self.log("GPS位置無效 (0, 0)，但仍嘗試發送命令", level="warning")
            
            self.log(f"當前UAV位置: lat={uav_gps['lat']:.8f}, lon={uav_gps['lon']:.8f}, alt={uav_gps['alt']:.2f}m", level="debug")
            self.log(f"當前UAV航向: {uav_yaw:.2f}°", level="debug")
            
            # 構建移動指令
            command = [
                0,  # 經度不偏移
                0,  # 緯度不偏移
                0,  # 高度不下降
                angle,  # 角度偏移
                self.flight_speed,  # 飛行速度
                loiter_time  # 懸停時間
            ]
            
            self.log(f"構建移動指令: {command}", level="debug")

            mission = calculate_target_position(uav_gps, uav_yaw, command)
            mission["timestamp"] = time.time()
            
            # 確保mission格式正確
            if 'mission' not in mission:
                mission['mission'] = []
            
            # 關鍵: 發送指令到 ai/target
            self.log("正在發送 Mission 到 ai/target...", level="info")
            
            # 發送Mission
            self.mqtt.publish("ai/target", mission)
            self.log(f"Mission 已成功發送至 ai/target", level="info")
            self.log(f"旋轉命令詳情: angle={angle}°, speed={self.flight_speed}m/s, loiter={loiter_time}s")
            
        except Exception as e:
            self.log(f"發送旋轉命令時發生異常: {e}", "error")
            self.log(f"錯誤詳情: {traceback.format_exc()}", "error")
            
            # 使用備份方法
            self.send_rotation_command_with_defaults(angle, loiter_time)

    def send_rotation_command_with_defaults(self, angle, loiter_time=0):
        """使用預設值發送旋轉命令的備份方法"""
        self.log("🔄 使用備份方法發送旋轉命令...", level="warning")
        
        try:
            # 直接發送簡單指令作為備份
            backup_cmd = {
                "type": 4,  # Theta 角度調整
                "Theta": angle,
                "flight_speed": self.flight_speed,
                "loiter_time": loiter_time,
                "timestamp": time.time()
            }
            
            self.log("備份命令內容:", level="info")
            backup_json = json.dumps(backup_cmd, indent=2, ensure_ascii=False)
            for line in backup_json.split('\n'):
                self.log(f"  {line}", level="info")
            
            self.mqtt.publish("ai/target", backup_cmd)
            self.log(f"備份旋轉命令已發送: type=4, Theta={angle}°", level="info")
            return True
            
        except Exception as backup_error:
            self.log(f"備份命令發送也失敗: {backup_error}", "error")
            return False

    def check_mission_complete(self):
        """檢查任務是否完成 - 參考step001邏輯"""
        # 從UAV信息獲取狀態 - 使用新格式，類似step001的做法
        if self.uav_info:
            navigation = self.uav_info.get("navigation", {})
            status = self.uav_info.get("status", "").lower()
            flight_mode = navigation.get("flight_mode", "").lower()
            
            self.log(f"🔍 UAV狀態檢查: flight_mode={flight_mode}, status={status}", level="debug")
            
            # 檢查任務完成狀態 - 主要依賴uav/info的狀態，類似step001
            # 當status為complete時，表示任務完成
            is_complete = (status == "complete")
            
            if is_complete:
                self.log(f"檢測到任務完成: flight_mode={flight_mode}, status={status}")
                self.log("任務完成條件達成 - 準備進入下一狀態")
                # 設置data_sources標記，保持一致性（雖然不再主要依賴它）
                self.data_sources["mission_complete"] = True
            else:
                # 僅在debug模式下顯示等待信息，避免日誌過多
                if status != "complete":
                    self.log(f"⏳ 等待任務完成: 當前狀態={status}", level="debug")
            
            return is_complete
            
        self.log("無UAV資訊，無法檢查任務狀態", level="debug")
        return False

def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_006", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="006", level=level)
    
    log("step_006_spray_strategy 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    # 🔧 詳細檢查必要的服務
    mqtt_service = data_sources.get("mqtt_service")
    if not mqtt_service:
        log("MQTT服務未可用", level="error")
        return None

    device_controller = data_sources.get("device_controller")
    if not device_controller:
        log("設備控制器未可用", level="warning")

    flow_controller = data_sources.get("flow_controller")
    if not flow_controller:
        log("流程控制器未可用", level="warning")

    log("📋 服務檢查完成：開始執行step_006")
    logger.log("執行 step_006_spray_strategy...", step="006", source="step_module")
    
    # 檢查流程是否處於錯誤狀態
    if flow_controller and flow_controller.get_flow_state() == "ERROR":
        logger.log("流程處於錯誤狀態，立即停止", step="006", level="error")
        notify_complete("006_complete", 99)
        return None

    # 初始化噴灑策略控制器
    controller = SprayStrategyController(data_sources, config, logger, notify_complete)
    
    # 返回停止函數
    def stop():
        controller.stop_spray()
        logger.log("step_006_spray_strategy 已停止", step="006", source="step_module")
    
    return {
        "stop": stop
    }

def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="006", level=level)
    
    log("🧪 執行 step_006 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬噴灑策略過程"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬噴灑策略過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 006", level="debug")
            # 模擬結果 (0=噴灑完成, 99=錯誤)
            result = 0  # 預設模擬噴灑完成
            notify_complete("006_complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬噴灑策略計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬噴灑策略計時器", level="debug")
        
        # 確保停止噴灑
        device_controller = data_sources.get("device_controller")
        if device_controller:
            device_controller.stop_spray()
        
        log("DEBUG: 步驟006停止，資源已釋放")
    
    return {
        "stop": stop
    }