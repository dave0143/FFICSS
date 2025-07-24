import math
import time
import json
import threading
from typing import Dict, Any, Optional, Tuple
import paho.mqtt.client as mqtt

# 修復import路徑問題
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from modules.tracking import GimbalTracker
    from modules.flow_step import FlowState
    from modules.coordinate_conversion import calculate_spray_path
    from modules.utils import cal_next_position, cal_yaw_angle, calculate_laser_vector, cal_target_location, generate_waypoints, get_gimbal_angles
except ImportError as e:
    print(f"Import error: {e}")
    # 如果模組無法導入，使用備用方案
    GimbalTracker = None


class TargetLockController:
    """目標鎖定控制器 - 使用獨立 MQTT 客戶端，不影響 tracking 模組"""
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        
        # 初始記錄的資訊
        self.initial_uav_info = None
        self.initial_gimbal_info = None
        self.original_target_position = None
        
        # 狀態控制
        self.target_path_sent = False
        self.completed = False
        self.target_tracing_started = False
        self.test_mode = config.get("debug_config", {}).get("test_mode", True)
        
        # 增強目標追蹤設定
        self.enhance_target = config.get("spray_strategy", {}).get("enhance_target", False)
        self.waypoint_num = config.get("fine_navi", {}).get("waypoint_num", 5)
        self.position_threshold = config.get("spray_strategy", {}).get("position_threshold", 5.0)
        self.integration_topic = config.get("mqtt", {}).get("integration_topic", "integration/info")
        
        # 時間控制
        self.last_integration_time = 0
        self.integration_interval = 1.0
        
        # 執行緒安全
        self.lock = threading.Lock()

        # 演算法相關變數
        self.history_waypoints = []
        self.recorded_waypoint_list = []
        
        # MQTT 連接管理（與 tracking 完全隔離）
        self._mqtt_clients = {}
        self._client_lock = threading.Lock()
        self._client_counter = 0
        
        # MQTT 配置
        self.mqtt_config = config.get("mqtt", {})
        self.broker = self.mqtt_config.get("broker", "localhost")
        self.port = self.mqtt_config.get("port", 1883)
        self.qos = self.mqtt_config.get("qos", 1)
        self.timeout = self.mqtt_config.get("query_timeout", 5)
        
        # Topic 配置
        self.drone_topic = self.mqtt_config.get("topics", {}).get("read2", "uav/info")
        self.gimbal_topic = "gimbal/info"
        self.ai_topic = self.mqtt_config.get("topics", {}).get("read1", "ai_fire/target")
        
        # 新增：共享服務支援
        self.shared_mqtt_service = None
        self.prefer_shared_service = config.get("mqtt", {}).get("prefer_shared_service", True)
        
        self.logger.log(f"[TargetLock] 控制器初始化完成 - "
                       f"航點數: {self.waypoint_num}, 測試模式: {self.test_mode}", 
                       step="004", level="info")
        
    def log(self, msg, level="info"):
        self.logger.log(f"[TargetLock] {msg}", step="004", level=level)

    def _create_unique_mqtt_client(self):
        """創建唯一的 MQTT 客戶端，避免與 tracking 衝突"""
        with self._client_lock:
            self._client_counter += 1
            # 使用時間戳、計數器和執行緒 ID 確保唯一性
            client_id = f"step004_query_{int(time.time() * 1000)}_{self._client_counter}_{threading.get_ident()}"
            
            # 創建客戶端並設置選項 - 修復 callback API 版本問題
            try:
                # 嘗試使用較新的 API
                try:
                    from paho.mqtt.client import CallbackAPIVersion
                    client = mqtt.Client(
                        client_id=client_id, 
                        callback_api_version=CallbackAPIVersion.VERSION1,
                        clean_session=True
                    )
                except (ImportError, AttributeError):
                    # 舊版本 paho-mqtt
                    client = mqtt.Client(client_id=client_id, clean_session=True)
                
                # 設置較短的 keepalive 避免長時間佔用
                client._keepalive = 30
                
                # 設置連接選項
                client.max_inflight_messages_set(20)
                client.max_queued_messages_set(0)  # 不緩存訊息
                
                self.log(f"創建 MQTT 查詢客戶端: {client_id}", level="debug")
                return client, client_id
                
            except Exception as e:
                self.log(f"創建 MQTT 客戶端失敗: {e}", level="error")
                return None, None

    def _cleanup_mqtt_client(self, client, client_id):
        """清理 MQTT 客戶端"""
        try:
            if client and client.is_connected():
                client.loop_stop()
                client.disconnect()
            self.log(f"清理 MQTT 查詢客戶端: {client_id}", level="debug")
        except Exception as e:
            self.log(f"清理 MQTT 客戶端錯誤 [{client_id}]: {e}", level="error")

    def get_drone_info(self, timeout=None):
        """智能選擇查詢方式"""
        if timeout is None:
            timeout = self.timeout
            
        # 優先使用共享服務（如果可用且穩定）
        if self.prefer_shared_service and self.shared_mqtt_service:
            try:
                result = self._query_via_shared_service(timeout)
                if result and self.validate_drone_info(result):
                    self.log("使用共享 MQTT 服務查詢成功", level="debug")
                    return result
                else:
                    self.log("共享服務查詢失敗，切換到獨立客戶端", level="warning")
            except Exception as e:
                self.log(f"共享服務查詢錯誤，切換到獨立客戶端: {e}", level="warning")

        # 備案：使用獨立客戶端
        result = self._query_via_independent_client(timeout)
        if result:
            self.log("使用獨立 MQTT 客戶端查詢成功", level="debug")
        return result

    def _query_via_shared_service(self, timeout):
        """使用共享 MQTT 服務進行查詢"""
        if not self.shared_mqtt_service:
            raise ValueError("共享 MQTT 服務未初始化")

        result = {}
        received = {"drone": False, "gimbal": False, "ai": False}
        event = threading.Event()
        handlers = []

        def check_all_received():
            if all(received.values()):
                event.set()

        # 定義處理函數
        def on_drone_info(topic, payload):
            try:
                nav = payload.get("navigation", {})
                sensors = payload.get("sensors", {})
                global_pos = nav.get("global_position", {})
                local_pos = nav.get("local_position", {})
                imu = sensors.get("imu", {})
                
                result.update({
                    "UAVLat": global_pos.get("lat"),
                    "UAVLong": global_pos.get("lon"),
                    "UAValt": global_pos.get("alt"),
                    "UAVroll": imu.get("roll"),
                    "UAVpitch": imu.get("pitch"),
                    "UAVyaw": imu.get("yaw"),
                    "UAVx": local_pos.get("x"),
                    "UAVy": local_pos.get("y"),
                    "UAVz": local_pos.get("alt")
                })
                received["drone"] = True
                check_all_received()
            except Exception as e:
                self.log(f"解析 UAV 資料錯誤: {e}", level="error")

        def on_gimbal_info(topic, payload):
            try:
                result.update({
                    "gimbal_yaw": payload.get("yaw", 0.0),
                    "gimbal_pitch": payload.get("pitch", 0.0),
                    "gimbal_roll": payload.get("roll", 0.0),
                    "gimbal_distance": payload.get("range_finding", {}).get("distance", 0.0)
                })
                received["gimbal"] = True
                check_all_received()
            except Exception as e:
                self.log(f"解析雲台資料錯誤: {e}", level="error")

        def on_ai_info(topic, payload):
            try:
                coordinates = payload.get("coordinates", {})
                result.update({
                    "status": payload.get("status", False),
                    "target_pixel_x": coordinates.get("X", 0),
                    "target_pixel_y": coordinates.get("Y", 0)
                })
                received["ai"] = True
                check_all_received()
            except Exception as e:
                self.log(f"解析 AI 資料錯誤: {e}", level="error")

        try:
            # 註冊臨時處理器
            handlers = [
                self.shared_mqtt_service.add_handler(self.drone_topic, on_drone_info),
                self.shared_mqtt_service.add_handler(self.gimbal_topic, on_gimbal_info),
                self.shared_mqtt_service.add_handler(self.ai_topic, on_ai_info)
            ]

            # 等待資料收集
            if event.wait(timeout):
                missing = [k for k, v in received.items() if not v]
                if missing:
                    self.log(f"共享服務查詢不完整，缺少: {missing}", level="warning")
                return result
            else:
                self.log("共享服務查詢超時", level="warning")
                return {}

        finally:
            # 清理臨時處理器
            for handler_id in handlers:
                if handler_id:
                    try:
                        self.shared_mqtt_service.remove_handler(handler_id)
                    except Exception as e:
                        self.log(f"移除處理器失敗: {e}", level="error")

    def _query_via_independent_client(self, timeout):
        """使用獨立 MQTT 客戶端查詢"""
        result = {}
        event = threading.Event()
        received = {"drone": False, "gimbal": False, "ai": False}
        client = None
        client_id = None
        connection_success = False

        def check_all_received():
            if received["drone"] and received["gimbal"] and received["ai"]:
                event.set()

        def on_connect(client, userdata, flags, rc):
            nonlocal connection_success
            if rc == 0:
                connection_success = True
                self.log(f"MQTT 查詢連接成功 [{client_id}]", level="debug")
                try:
                    client.subscribe(self.drone_topic, qos=self.qos)
                    client.subscribe(self.gimbal_topic, qos=self.qos)
                    client.subscribe(self.ai_topic, qos=self.qos)
                except Exception as e:
                    self.log(f"MQTT 訂閱失敗 [{client_id}]: {e}", level="error")
            else:
                self.log(f"MQTT 查詢連接失敗 [{client_id}]: {rc}", level="error")

        def on_disconnect(client, userdata, rc):
            if rc != 0:
                self.log(f"MQTT 查詢意外斷開 [{client_id}]: {rc}", level="warning")

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload.decode("utf-8"))
                
                if msg.topic == self.drone_topic:
                    # 詳細解析 UAV 資料
                    nav = data.get("navigation", {})
                    sensors = data.get("sensors", {})
                    global_pos = nav.get("global_position", {})
                    local_pos = nav.get("local_position", {})
                    imu = sensors.get("imu", {})
                    
                    result.update({
                        "UAVLat": global_pos.get("lat"),
                        "UAVLong": global_pos.get("lon"),
                        "UAValt": global_pos.get("alt"),
                        "UAVroll": imu.get("roll"),
                        "UAVpitch": imu.get("pitch"),
                        "UAVyaw": imu.get("yaw"),
                        "UAVx": local_pos.get("x"),
                        "UAVy": local_pos.get("y"),
                        "UAVz": local_pos.get("alt")
                    })
                    received["drone"] = True
                    self.log(f"收到 UAV 資訊 [{client_id}]", level="debug")

                elif msg.topic == self.gimbal_topic:
                    # 詳細解析雲台資料
                    result.update({
                        "gimbal_yaw": data.get("yaw", 0.0),
                        "gimbal_pitch": data.get("pitch", 0.0),
                        "gimbal_roll": data.get("roll", 0.0),
                        "gimbal_distance": data.get("range_finding", {}).get("distance", 0.0)
                    })
                    received["gimbal"] = True
                    self.log(f"收到雲台資訊 [{client_id}]", level="debug")

                elif msg.topic == self.ai_topic:
                    # 詳細解析 AI 資料
                    coordinates = data.get("coordinates", {})
                    result.update({
                        "status": data.get("status", False),
                        "target_pixel_x": coordinates.get("X", 0),
                        "target_pixel_y": coordinates.get("Y", 0)
                    })
                    received["ai"] = True
                    self.log(f"收到 AI 資訊 [{client_id}]", level="debug")

                check_all_received()

            except json.JSONDecodeError as e:
                self.log(f"MQTT 訊息 JSON 解析錯誤 [{client_id}] ({msg.topic}): {e}", level="error")
            except Exception as e:
                self.log(f"MQTT 訊息處理錯誤 [{client_id}] ({msg.topic}): {e}", level="error")

        try:
            # 創建唯一的客戶端
            client, client_id = self._create_unique_mqtt_client()
            if not client:
                return {}
            
            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.on_message = on_message

            # 連接並等待資料
            self.log(f"開始 MQTT 查詢 [{client_id}]", level="debug")
            client.connect(self.broker, self.port, keepalive=30)
            client.loop_start()

            # 等待連接成功
            connect_timeout = min(timeout / 2, 3)
            start_time = time.time()
            while not connection_success and time.time() - start_time < connect_timeout:
                time.sleep(0.1)

            if not connection_success:
                self.log(f"MQTT 連接超時 [{client_id}]", level="error")
                return {}

            # 等待資料
            if event.wait(timeout):
                self.log(f"成功獲取所有 MQTT 資訊 [{client_id}] - "
                        f"UAV: {received['drone']}, 雲台: {received['gimbal']}, AI: {received['ai']}", 
                        level="debug")
                return result
            else:
                missing = [k for k, v in received.items() if not v]
                self.log(f"獲取 MQTT 資訊超時 ({timeout}s) [{client_id}] - 缺少: {missing}", level="warning")
                return {}
                
        except Exception as e:
            self.log(f"MQTT 連接錯誤 [{client_id}]: {e}", level="error")
            return {}
        finally:
            # 確保客戶端被正確清理
            if client:
                self._cleanup_mqtt_client(client, client_id)

    def validate_drone_info(self, result):
        """驗證無人機資訊的完整性"""
        required_fields = [
            "UAVLat", "UAVLong", "UAValt", "UAVroll", "UAVpitch", "UAVyaw",
            "UAVx", "UAVy", "UAVz", "gimbal_yaw", "gimbal_pitch", "gimbal_roll", 
            "gimbal_distance", "status", "target_pixel_x", "target_pixel_y"
        ]
        
        missing_fields = []
        for field in required_fields:
            if field not in result or result[field] is None:
                missing_fields.append(field)
        
        if missing_fields:
            self.log(f"無人機資訊不完整，缺少欄位: {missing_fields}", level="warning")
            return False
        
        # 檢查座標範圍的合理性
        if not (-90 <= result["UAVLat"] <= 90):
            self.log(f"UAV 緯度超出範圍: {result['UAVLat']}", level="warning")
            return False
            
        if not (-180 <= result["UAVLong"] <= 180):
            self.log(f"UAV 經度超出範圍: {result['UAVLong']}", level="warning")
            return False
        
        return True
    
    def process_waypoint_detection(self, current_waypoint_index, ai_detected, flight_speed, loiter_time, mqtt_service):
        """在每個航點處理目標檢測和路徑更新"""
        
        remaining_waypoints = self.waypoint_num - current_waypoint_index
            
        if ai_detected and remaining_waypoints > 0:
            self.log(f"在第 {current_waypoint_index} 個航點檢測到目標，開始重新計算路徑", level="info")
            
            # 使用改進的 get_drone_info，不會與 tracking 衝突
            result = self.get_drone_info()
            if not result or not self.validate_drone_info(result):
                self.log("無法獲取完整的 UAV 資訊，維持原路徑", level="warning")
                return
                
            # 提取資訊
            UAV_Lat = result["UAVLat"]
            UAV_Lon = result["UAVLong"]
            UAV_alt = result["UAValt"]
            UAV_roll = result["UAVroll"]
            UAV_pitch = result["UAVpitch"]
            UAV_yaw = result["UAVyaw"]
            UAV_x = result["UAVx"]
            UAV_y = result["UAVy"]
            UAV_z = result["UAVz"]

            gimbal_yaw = result["gimbal_yaw"]
            gimbal_pitch = result["gimbal_pitch"]
            gimbal_roll = result["gimbal_roll"]
            gimbal_distance = result["gimbal_distance"]

            # 記錄當前檢測資訊
            self.log(f"記錄第 {current_waypoint_index} 次 UAV 位置: [{UAV_Lat:.6f}, {UAV_Lon:.6f}, {UAV_alt:.2f}]", level="info")
            self.log(f"記錄第 {current_waypoint_index} 次雲台資訊: yaw={gimbal_yaw:.2f}, pitch={gimbal_pitch:.2f}, distance={gimbal_distance:.2f}", level="info")

            # 將當前檢測資訊加入歷史記錄
            if current_waypoint_index != 1:
                waypoint_data = (UAV_Lat, UAV_Lon, UAV_alt, UAV_x, UAV_y, UAV_z, 
                               UAV_yaw, UAV_pitch, UAV_roll, gimbal_pitch, gimbal_yaw, gimbal_roll)
                self.history_waypoints.append(waypoint_data)
            
            self.log(f"歷史檢測點數量: {len(self.history_waypoints)}", level="debug")

            # 計算目標位置 (簡化版本，避免依賴外部模組)
            try:
                if len(self.history_waypoints) >= 2:
                    # 簡化的目標計算
                    target_lat = UAV_Lat + (gimbal_distance * 0.00001)  # 簡化計算
                    target_lon = UAV_Lon + (gimbal_distance * 0.00001)
                    target_alt = UAV_alt - 10  # 假設目標在地面
                    
                    target_point_lla = [target_lat, target_lon, target_alt]
                    
                    self.log(f"第 {len(self.history_waypoints)} 次熱源位置計算完成: {target_point_lla}", level="info")
                    
                    # 發送簡化的路徑更新
                    self.send_simple_path_update(target_point_lla, current_waypoint_index, mqtt_service)
                    
                else:
                    # 單點估算
                    self.log("僅有一個檢測點，進行單點目標估算", level="info")
                    target_lat = UAV_Lat + (gimbal_distance * 0.00001)
                    target_lon = UAV_Lon + (gimbal_distance * 0.00001)
                    target_alt = UAV_alt - 10
                    target_point_lla = [target_lat, target_lon, target_alt]
                    
                    self.send_simple_path_update(target_point_lla, current_waypoint_index, mqtt_service)
                    
            except Exception as e:
                self.log(f"計算目標位置錯誤: {e}", level="error")
                return
        
        elif not ai_detected and remaining_waypoints > 0:
            self.log(f"在第 {current_waypoint_index} 個航點未檢測到目標，維持原路徑", level="debug")

    def send_simple_path_update(self, target_point_lla, current_waypoint_index, mqtt_service):
        """發送簡化的路徑更新"""
        try:
            path_update = {
                "mission": {
                    "target_location": {
                        "lat": float(target_point_lla[0]),
                        "lon": float(target_point_lla[1]),
                        "alt": float(target_point_lla[2])
                    }
                },
                "task": "update_target",
                "current_waypoint": current_waypoint_index,
                "timestamp": time.time()
            }
            
            mqtt_service.publish("ai/target", path_update)
            self.log(f"發送簡化目標路徑更新 (航點 {current_waypoint_index})", level="info")
            
        except Exception as e:
            self.log(f"發送路徑更新失敗: {e}", level="error")

    def _wait_for_waypoint_arrival(self, current_waypoint):
        """等待 UAV 到達指定航點"""
        if self.test_mode:
            # Debug 模式下直接模擬到達
            time.sleep(1)  # 模擬飛行時間
            self.log(f"Debug模式：已模擬到達第 {current_waypoint + 1} 個航點", level="debug")
            return

        # 實際等待邏輯 (簡化版本)
        self.log(f"等待到達第 {current_waypoint + 1} 個航點...", level="debug")
        time.sleep(5)  # 簡化的等待時間

    def handle_step(self, data_sources, notify_complete):
        """主要步驟處理邏輯"""
        self.log("開始處理 step_004_target_lock", level="info")
        
        try:
            # 檢查是否已完成
            if self.completed:
                self.log("步驟已完成，跳過處理", level="debug")
                return
            
            # 檢查流程狀態
            if self._check_error_state(data_sources, notify_complete):
                return

            # 設置共享服務（如果可用）
            mqtt_service = data_sources.get("mqtt_service")
            if mqtt_service and self.prefer_shared_service:
                self.shared_mqtt_service = mqtt_service
                self.log("已設置共享 MQTT 服務", level="debug")

            # 等待 MQTT 服務就緒
            self.log("等待 MQTT 服務就緒...", level="info")
            result = self.get_drone_info()
            wait_count = 0
            max_wait = 30  # 減少等待時間到30秒
            
            while not result and wait_count < max_wait:
                if wait_count == 0:
                    self.log("等待 MQTT 服務啟動...", level="info")
                elif wait_count % 5 == 0:
                    self.log(f"已等待 MQTT 服務 {wait_count} 秒...", level="info")
                
                time.sleep(1)
                wait_count += 1
                result = self.get_drone_info()
            
            if not result:
                self.log(f"等待 MQTT 服務超時 ({max_wait}s)，無法繼續", level="error")
                notify_complete("004_complete", 99)
                self.completed = True
                return
            
            # 記錄初始資訊
            if self.initial_uav_info is None and self.initial_gimbal_info is None:
                self._record_initial_info(data_sources, result)

            # 主處理邏輯
            if self.initial_uav_info and self.initial_gimbal_info:
                self.log("開始處理目標鎖定任務", level="info")
                
                flight_speed = self.config.get("spray_strategy", {}).get("flight_speed", 3.0)
                loiter_time = self.config.get("spray_strategy", {}).get("Suspension_time", 0.0)

                if not mqtt_service:
                    self.log("MQTT 服務未找到，無法發送路徑", level="error")
                    notify_complete("004_complete", 99)
                    self.completed = True
                    return

                # 處理每個航點
                for current_waypoint in range(1, self.waypoint_num + 1):
                    self.log(f"處理第 {current_waypoint}/{self.waypoint_num} 個航點", level="info")
                    
                    # 檢查是否檢測到目標
                    result = self.get_drone_info()
                    ai_detected = result.get("status", False) if result else False
                    
                    self.log(f"第 {current_waypoint} 個航點 AI 檢測狀態: {ai_detected}", level="debug")
                    
                    # 處理當前航點的檢測和路徑更新
                    self.process_waypoint_detection(current_waypoint, ai_detected, flight_speed, loiter_time, mqtt_service)
                    
                    if current_waypoint < self.waypoint_num:
                        # 等待 UAV 到達該航點
                        self._wait_for_waypoint_arrival(current_waypoint)

                    if current_waypoint == self.waypoint_num:
                        self.log("已到達最後一個航點，任務完成", level="info")
                        notify_complete("004_complete", 0)
                        self.completed = True
                        return

                # 檢查任務完成
                if self._check_mission_complete(data_sources, notify_complete):
                    return
            else:
                self.log("無法獲取初始 MQTT 資訊", level="error")
                notify_complete("004_complete", 99)
                self.completed = True

        except Exception as e:
            self.log(f"步驟處理錯誤: {e}", level="error")
            self._cleanup_resources()
            notify_complete("004_complete", 99)
            self.completed = True

    def _check_error_state(self, data_sources, notify_complete) -> bool:
        """檢查是否處於錯誤狀態"""
        flow_controller = data_sources.get("flow_controller")
        if flow_controller and flow_controller.get_flow_state().value == "error":
            self.log("flow_state=FlowState.ERROR，程式即全部停止", level="error")
            self._cleanup_resources()
            notify_complete("004_complete", 99)
            self.completed = True
            return True
        return False
    
    def _record_initial_info(self, data_sources, result) -> bool:
        """記錄初始資訊"""
        try:
            if result and self.validate_drone_info(result):
                self.log("成功收取初始 UAV 和雲台資訊", level="info")
                
                # 記錄初始資訊
                if not self.initial_uav_info:
                    self.initial_uav_info = result.copy()
                    self.log(f"記錄初始 UAV 位置: lat={result['UAVLat']:.6f}, "
                            f"lon={result['UAVLong']:.6f}, alt={result['UAValt']:.2f}")
                
                if not self.initial_gimbal_info:
                    self.initial_gimbal_info = result.copy()
                    self.log(f"記錄初始雲台資訊: yaw={result['gimbal_yaw']:.2f}, "
                            f"pitch={result['gimbal_pitch']:.2f}, distance={result['gimbal_distance']:.2f}")

                # 加入歷史記錄
                initial_waypoint = (
                    result['UAVLat'], result['UAVLong'], result['UAValt'],
                    result['UAVx'], result['UAVy'], result['UAVz'],
                    result['UAVyaw'], result['UAVpitch'], result['UAVroll'],
                    result['gimbal_pitch'], result['gimbal_yaw'], result['gimbal_roll']
                )
                self.history_waypoints.append(initial_waypoint)

            return self.initial_uav_info is not None and self.initial_gimbal_info is not None
            
        except Exception as e:
            self.log(f"記錄初始資訊錯誤: {e}", level="error")
            return False
    
    def _check_mission_complete(self, data_sources, notify_complete) -> bool:
        """檢查任務是否完成"""
        try:
            last_topic = data_sources.get("last_topic")
            last_payload = data_sources.get("last_payload")
            
            if last_topic == "uav/info" and last_payload:
                nav = last_payload.get("navigation", {})
                errors = last_payload.get("errors", [{}])
                
                if (nav.get("flight_mode") == "process" and 
                    errors and errors[-1].get("status") == "Complete"):
                    self.log("UAV status=Complete，目標位置已到達")
                    self._cleanup_resources()
                    notify_complete("004_complete", 0)
                    self.completed = True
                    return True
            
            return False
            
        except Exception as e:
            self.log(f"檢查任務完成錯誤: {e}", level="error")
            return False
    
    def _cleanup_resources(self):
        """清理資源"""
        try:
            # 清理所有 MQTT 客戶端
            with self._client_lock:
                if self._mqtt_clients:
                    self.log(f"清理 {len(self._mqtt_clients)} 個 MQTT 查詢客戶端")
                    for client_id, client in self._mqtt_clients.items():
                        self._cleanup_mqtt_client(client, client_id)
                    self._mqtt_clients.clear()
            
            self.log("資源清理完成")
            
        except Exception as e:
            self.log(f"清理資源錯誤: {e}", level="error")

    def get_status(self):
        """獲取控制器狀態"""
        return {
            "completed": self.completed,
            "test_mode": self.test_mode,
            "waypoint_num": self.waypoint_num,
            "history_waypoints_count": len(self.history_waypoints),
            "recorded_waypoints_count": len(self.recorded_waypoint_list),
            "has_initial_uav_info": self.initial_uav_info is not None,
            "has_initial_gimbal_info": self.initial_gimbal_info is not None,
            "target_path_sent": self.target_path_sent,
            "mqtt_clients_count": len(self._mqtt_clients),
            "using_shared_service": self.shared_mqtt_service is not None
        }


def run_step(data_sources, config, logger, notify_complete):
    """步驟運行入口點 - 確保 tracking 和 step_004 獨立運行"""
    
    # 檢查是否強制啟用 DEBUG 模式
    force_debug = config.get("debug_config", {}).get("step_004", False)
    
    # 內部日誌函數
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="004", level=level)
    
    log("step_004_target_lock 啟動")

    # 如果 DEBUG 模式啟用，執行 debug 測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    logger.log("執行 step_004_target_lock...", step="004", source="step_module")
    
    # 初始化控制器（使用改進的版本）
    if "target_lock_controller" not in data_sources:
        data_sources["target_lock_controller"] = TargetLockController(config, logger)

    # 1. 驗證 MQTT 服務可用性
    mqtt_service = data_sources.get("mqtt_service")
    if not mqtt_service:
        log("MQTT 服務未找到，無法繼續", level="error")
        notify_complete("004_complete", 99)
        return {"stop": lambda: None}

    # 2. 啟動 tracking（如果模組可用）
    tracker = None
    try:
        if GimbalTracker:  # 檢查模組是否成功導入
            log("啟動雲台追蹤器...")
            tracker = GimbalTracker(mqtt_service, logger, config)
            
            # 驗證 tracking 啟動成功
            if tracker.start():
                log("雲台追蹤器啟動成功，使用共享 MQTT 服務")
                
                # 等待 tracker 穩定
                time.sleep(0.5)
                
                # 驗證 tracking 狀態
                if not tracker.running:
                    raise Exception("追蹤器未正確啟動")
            else:
                raise Exception("雲台追蹤器啟動失敗")
        else:
            log("GimbalTracker 模組不可用，跳過追蹤器啟動", level="warning")

    except Exception as e:
        log(f"啟動雲台追蹤器失敗: {e}", level="error")
        
        # 如果 tracking 啟動失敗，仍然繼續 step_004（可選）
        tracker = None

    # 3. 初始化並執行 step_004 控制器
    controller = data_sources["target_lock_controller"]
    
    # 將共享服務提供給 controller
    controller.shared_mqtt_service = mqtt_service
    
    # 執行步驟邏輯
    controller.handle_step(data_sources, notify_complete)
    
    # 4. 返回清理函數（確保正確的停止順序）
    def cleanup():
        log("開始清理 step_004 資源...")
        
        try:
            # 步驟1: 停止 controller 的查詢活動
            log("停止目標鎖定控制器...")
            controller._cleanup_resources()
            log("目標鎖定控制器已停止")

            # 步驟2: 停止 tracker（最後停止，避免中斷即時控制）
            if tracker:
                log("停止雲台追蹤器...")
                tracker.stop()
                log("雲台追蹤器已停止")

            log("step_004 所有資源清理完成")

        except Exception as e:
            log(f"清理資源時發生錯誤: {e}", level="error")

    return {
        "stop": cleanup,
        "data": {
            "controller": controller,
            "tracker": tracker,
            "mqtt_service": mqtt_service
        }
    }


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG 模式測試函數"""
    def log(msg, level="info"):
        full_msg = f"[DEBUG] {msg}"
        logger.log(full_msg, step="004", level=level)
    
    log("執行 step_004 DEBUG 測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    
    def _debug_mode_scan():
        nonlocal completed, stopped
        
        log("DEBUG 模式啟動 - 模擬目標鎖定過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if not stopped and not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 004", level="debug")
            result = 0  # 模擬成功
            notify_complete("004_complete", result)
            completed = True
    
    # 啟動模擬
    log("DEBUG: 啟動模擬目標鎖定計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped
        stopped = True
        log("DEBUG: 步驟004停止，資源已釋放")
    
    return {
        "stop": stop
    }