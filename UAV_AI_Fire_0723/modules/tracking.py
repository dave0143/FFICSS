import threading
import time
import numpy as np
import paho.mqtt.client as mqtt
import json

# 修復import路徑 - 使用相對路徑或絕對路徑
try:
    from .ktgGimbalControl import KTGGimbalController
except ImportError:
    from modules.ktgGimbalControl import KTGGimbalController


class PIDController:
    """PID 控制器 - 線程安全版本"""
    def __init__(self, kp, ki, kd):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.prev_error = 0
        self.integral = 0
        self.last_time = time.time()
        self.lock = threading.Lock()
        
        # 新增：積分項限制和濾波
        self.integral_limit = 100.0
        self.derivative_filter_alpha = 0.7  # 低通濾波係數
        self.filtered_derivative = 0

    def compute(self, error):
        """計算 PID 控制輸出"""
        with self.lock:
            current_time = time.time()
            dt = current_time - self.last_time
            if dt <= 0:
                dt = 1e-6  # 防止除以 0

            # 積分項計算並限制
            self.integral += error * dt
            self.integral = np.clip(self.integral, -self.integral_limit, self.integral_limit)
            
            # 微分項計算並濾波
            raw_derivative = (error - self.prev_error) / dt
            self.filtered_derivative = (self.derivative_filter_alpha * self.filtered_derivative + 
                                       (1 - self.derivative_filter_alpha) * raw_derivative)

            output = (self.kp * error +
                      self.ki * self.integral +
                      self.kd * self.filtered_derivative)

            self.prev_error = error
            self.last_time = current_time

            return output

    def reset(self):
        """重置 PID 控制器"""
        with self.lock:
            self.prev_error = 0
            self.integral = 0
            self.filtered_derivative = 0
            self.last_time = time.time()

    def update_parameters(self, kp=None, ki=None, kd=None):
        """動態更新 PID 參數"""
        with self.lock:
            if kp is not None:
                self.kp = kp
            if ki is not None:
                self.ki = ki
            if kd is not None:
                self.kd = kd

    def get_state(self):
        """獲取 PID 狀態"""
        with self.lock:
            return {
                "kp": self.kp,
                "ki": self.ki,
                "kd": self.kd,
                "prev_error": self.prev_error,
                "integral": self.integral,
                "filtered_derivative": self.filtered_derivative
            }


class GimbalTracker:
    """雲台追蹤器 - 獨立運行，專注於即時追蹤控制"""
    
    def __init__(self, mqtt_service, logger, config):
        self.shared_mqtt_service = mqtt_service  # 使用共享的 MQTT 服務
        self.logger = logger
        self.config = config
        self.controller = None  # 延遲初始化雲台控制器
        self.running = False
        self.thread = None

        # 目標與狀態資訊（線程安全）
        self.target = {'x': None, 'y': None, 'timestamp': 0, 'confidence': 0}
        self.gimbal_info = {'yaw': 0, 'pitch': 0, 'roll': 0, 'timestamp': 0}
        self.last_x = None
        self.last_y = None

        # 載入控制參數
        self._load_config(config)
        
        # 初始化 PID 控制器
        self._init_pid_controllers()

        # 線程安全鎖
        self.data_lock = threading.RLock()
        
        # MQTT 處理器 ID（用於清理）
        self.handler_ids = []
        
        # 獨立 MQTT 客戶端（用於接收 ai_fire/target）
        self.local_mqtt = None
        self.local_mqtt_connected = False
        
        # 狀態追蹤
        self.start_time = None
        self.control_count = 0
        self.error_count = 0
        self.target_lost_count = 0
        self.last_control_time = 0
        
        # 目標追蹤歷史（用於濾波和預測）
        self.target_history = []
        self.max_history_length = 10
        
        # 性能監控
        self.control_latency_samples = []
        self.max_latency_samples = 100

    def _load_config(self, config):
        """載入配置參數"""
        gconf = config.get('gimbal_control', {})
        yaw_pid_conf = gconf.get('pid_yaw', {})
        pitch_pid_conf = gconf.get('pid_pitch', {})
        loop_conf = gconf.get('control_loop', {})
        
        # 從 spray_strategy.KIM_position 獲取相機參數
        kim_config = config.get('spray_strategy', {}).get('KIM_position', {})
        camera_width = kim_config.get('image_width', 1920)
        camera_height = kim_config.get('image_height', 1080)

        # 相機參數
        self.center_x = camera_width / 2
        self.center_y = camera_height / 2
        self.image_width = camera_width
        self.image_height = camera_height
        
        # 控制參數
        self.jump_filter_thresh = loop_conf.get('jump_threshold_px', 500)
        self.max_speed = loop_conf.get('max_speed_dps', 15)
        self.control_frequency = loop_conf.get('frequency_hz', 20)
        self.target_timeout = loop_conf.get('target_timeout_s', 1.0)
        
        # 新增：濾波和穩定性參數
        self.min_confidence = loop_conf.get('min_confidence', 0.3)
        self.stabilization_threshold = loop_conf.get('stabilization_threshold_px', 10)
        self.prediction_enable = loop_conf.get('enable_prediction', True)
        
        # PID 參數
        self.yaw_pid_params = {
            'kp': yaw_pid_conf.get('kp', 0.05),
            'ki': yaw_pid_conf.get('ki', 0.001),
            'kd': yaw_pid_conf.get('kd', 0.01)
        }
        self.pitch_pid_params = {
            'kp': pitch_pid_conf.get('kp', -0.05),
            'ki': pitch_pid_conf.get('ki', -0.001),
            'kd': pitch_pid_conf.get('kd', -0.01)
        }

        # MQTT 配置
        self.mqtt_config = config.get("mqtt", {})
        self.broker = self.mqtt_config.get("broker", "192.168.144.100")
        self.port = self.mqtt_config.get("port", 1883)
        
        # Topic 配置
        self.ai_topic = self.mqtt_config.get("topics", {}).get("read1", "ai_fire/target")

        self.logger.log(f"[Tracking] 配置載入完成 - 相機中心: ({self.center_x}, {self.center_y}), "
                       f"最大速度: {self.max_speed}°/s, 控制頻率: {self.control_frequency}Hz", 
                       step="track", level="info")

    def _init_pid_controllers(self):
        """初始化 PID 控制器"""
        self.yaw_pid = PIDController(**self.yaw_pid_params)
        self.pitch_pid = PIDController(**self.pitch_pid_params)
        
        self.logger.log(f"[Tracking] PID 控制器已初始化 - "
                       f"Yaw PID: {self.yaw_pid_params}, Pitch PID: {self.pitch_pid_params}", 
                       step="track", level="info")

    def _init_local_mqtt(self):
        """建立獨立 MQTT 用戶端連線 - 修復 callback API 版本問題"""
        try:
            # 修復：使用兼容的 MQTT 客戶端創建方式
            client_id = f"gimbal_tracker_{int(time.time() * 1000)}_{threading.get_ident()}"
            
            # 嘗試使用較新的 API
            try:
                # paho-mqtt >= 2.0
                from paho.mqtt.client import CallbackAPIVersion
                self.local_mqtt = mqtt.Client(
                    client_id=client_id, 
                    callback_api_version=CallbackAPIVersion.VERSION1,
                    clean_session=True
                )
            except (ImportError, AttributeError):
                # paho-mqtt < 2.0 (舊版本)
                self.local_mqtt = mqtt.Client(client_id=client_id, clean_session=True)
            
            # 設置連接參數
            self.local_mqtt._keepalive = 60
            self.local_mqtt.max_inflight_messages_set(20)
            self.local_mqtt.max_queued_messages_set(0)
            
            # 設置回調函數
            self.local_mqtt.on_connect = self._on_mqtt_connect
            self.local_mqtt.on_message = self._on_mqtt_message
            self.local_mqtt.on_disconnect = self._on_mqtt_disconnect
            
            # 連接到 broker
            self.logger.log(f"[Tracking] 連接到 MQTT broker: {self.broker}:{self.port}", step="track")
            self.local_mqtt.connect(self.broker, self.port, 60)
            self.local_mqtt.loop_start()
            
            return True
            
        except Exception as e:
            self.logger.log(f"[Tracking] 初始化獨立 MQTT 客戶端失敗: {e}", step="track", level="error")
            return False

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """MQTT 連接回調"""
        if rc == 0:
            self.local_mqtt_connected = True
            self.logger.log(f"[Tracking] 獨立 MQTT 已連線，訂閱 {self.ai_topic}", step="track")
            try:
                client.subscribe(self.ai_topic, qos=1)
            except Exception as e:
                self.logger.log(f"[Tracking] MQTT 訂閱失敗: {e}", step="track", level="error")
        else:
            self.logger.log(f"[Tracking] 獨立 MQTT 連線失敗 (rc={rc})", step="track", level="error")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """MQTT 斷線回調"""
        self.local_mqtt_connected = False
        if rc != 0:
            self.logger.log(f"[Tracking] 獨立 MQTT 意外斷線 (rc={rc})", step="track", level="warning")

    def _on_mqtt_message(self, client, userdata, msg):
        """MQTT 訊息回調"""
        try:
            if msg.topic == self.ai_topic:
                payload = json.loads(msg.payload.decode("utf-8"))
                self._on_target(msg.topic, payload)
        except Exception as e:
            self.logger.log(f"[Tracking] 處理 MQTT 訊息錯誤: {e}", step="track", level="error")

    def _on_target(self, topic, payload):
        """處理目標資訊 - 線程安全，專用於即時追蹤"""
        try:
            # 驗證資料格式
            if not isinstance(payload, dict):
                self.logger.log(f"[Tracking] 目標資料格式錯誤: {type(payload)}", 
                               step="track", level="warning")
                return

            # 提取目標資訊
            coordinates = payload.get('coordinates', {})
            x = coordinates.get('X') if coordinates else payload.get('x')
            y = coordinates.get('Y') if coordinates else payload.get('y')
            confidence = payload.get('confidence', 1.0)
            status = payload.get('status', True)
            
            # 驗證座標有效性
            if x is None or y is None or not status:
                with self.data_lock:
                    self.target_lost_count += 1
                self.logger.log(f"[Tracking] 目標遺失或座標無效: x={x}, y={y}, status={status}", 
                               step="track", level="debug")
                return

            # 驗證座標範圍
            if not (0 <= x <= self.image_width and 0 <= y <= self.image_height):
                self.logger.log(f"[Tracking] 目標座標超出範圍: x={x}, y={y}", 
                               step="track", level="warning")
                return

            # 驗證置信度
            if confidence < self.min_confidence:
                self.logger.log(f"[Tracking] 目標置信度過低: {confidence} < {self.min_confidence}", 
                               step="track", level="debug")
                return

            # 線程安全更新
            current_time = time.time()
            with self.data_lock:
                self.target['x'] = float(x)
                self.target['y'] = float(y)
                self.target['confidence'] = float(confidence)
                self.target['timestamp'] = current_time
                
                # 更新目標歷史
                self.target_history.append({
                    'x': float(x),
                    'y': float(y),
                    'timestamp': current_time,
                    'confidence': float(confidence)
                })
                
                # 限制歷史長度
                if len(self.target_history) > self.max_history_length:
                    self.target_history.pop(0)
                
            self.logger.log(f"[Tracking] 收到目標位置: x={x:.1f}, y={y:.1f}, conf={confidence:.2f}", 
                           step="track", level="debug")
            
        except Exception as e:
            self.error_count += 1
            self.logger.log(f"[Tracking] 解析目標資訊失敗: {e}", step="track", level="error")

    def _on_gimbal_info(self, topic, payload):
        """處理雲台資訊 - 線程安全，用於狀態監控"""
        try:
            # 驗證資料格式
            if not isinstance(payload, dict):
                self.logger.log(f"[Tracking] 雲台資料格式錯誤: {type(payload)}", 
                               step="track", level="warning")
                return

            # 線程安全更新
            with self.data_lock:
                self.gimbal_info['yaw'] = payload.get('yaw', 0)
                self.gimbal_info['pitch'] = payload.get('pitch', 0)
                self.gimbal_info['roll'] = payload.get('roll', 0)
                self.gimbal_info['timestamp'] = time.time()
                
            self.logger.log(f"[Tracking] 收到雲台資訊: yaw={self.gimbal_info['yaw']:.2f}°, "
                           f"pitch={self.gimbal_info['pitch']:.2f}°", 
                           step="track", level="debug")
            
        except Exception as e:
            self.error_count += 1
            self.logger.log(f"[Tracking] 解析雲台資訊失敗: {e}", step="track", level="error")

    def _filter_target_position(self, x, y):
        """濾除突然跳動和噪聲"""
        # 1. 跳動濾除
        if self.last_x is not None and self.last_y is not None:
            dist = np.sqrt((x - self.last_x)**2 + (y - self.last_y)**2)
            if dist > self.jump_filter_thresh:
                self.logger.log(f"[Tracking] 濾除跳動點: 距離={dist:.2f}px > {self.jump_filter_thresh}px", 
                               step="track", level="debug")
                return None, None

        # 2. 移動平均濾波（基於歷史）
        if len(self.target_history) >= 3:
            recent_targets = self.target_history[-3:]
            avg_x = sum(t['x'] for t in recent_targets) / len(recent_targets)
            avg_y = sum(t['y'] for t in recent_targets) / len(recent_targets)
            
            # 加權平均（最新的權重更大）
            weights = [0.2, 0.3, 0.5]
            weighted_x = sum(w * t['x'] for w, t in zip(weights, recent_targets))
            weighted_y = sum(w * t['y'] for w, t in zip(weights, recent_targets))
            
            return weighted_x, weighted_y
        
        return x, y

    def _predict_target_position(self, x, y):
        """基於歷史軌跡預測目標位置"""
        if not self.prediction_enable or len(self.target_history) < 3:
            return x, y
        
        try:
            # 使用最近3個點計算速度
            recent = self.target_history[-3:]
            dt1 = recent[-1]['timestamp'] - recent[-2]['timestamp']
            dt2 = recent[-2]['timestamp'] - recent[-3]['timestamp']
            
            if dt1 <= 0 or dt2 <= 0:
                return x, y
            
            # 計算速度
            vx1 = (recent[-1]['x'] - recent[-2]['x']) / dt1
            vy1 = (recent[-1]['y'] - recent[-2]['y']) / dt1
            vx2 = (recent[-2]['x'] - recent[-3]['x']) / dt2
            vy2 = (recent[-2]['y'] - recent[-3]['y']) / dt2
            
            # 平均速度
            avg_vx = (vx1 + vx2) / 2
            avg_vy = (vy1 + vy2) / 2
            
            # 預測未來位置（假設控制延遲為一個控制週期）
            predict_dt = 1.0 / self.control_frequency
            predicted_x = x + avg_vx * predict_dt
            predicted_y = y + avg_vy * predict_dt
            
            # 限制預測範圍
            predicted_x = np.clip(predicted_x, 0, self.image_width)
            predicted_y = np.clip(predicted_y, 0, self.image_height)
            
            return predicted_x, predicted_y
            
        except Exception as e:
            self.logger.log(f"[Tracking] 目標預測錯誤: {e}", step="track", level="warning")
            return x, y

    def start(self):
        """啟動追蹤器"""
        if self.running:
            self.logger.log("[Tracking] 追蹤器已在運行", step="track", level="warning")
            return False

        try:
            self.logger.log("[Tracking] 正在啟動追蹤器...", step="track", level="info")
            
            # 初始化雲台控制器
            if self.controller is None:
                self.logger.log("[Tracking] 初始化雲台控制器...", step="track", level="info")
                
                gimbal_config = self.config.get("gimbal_tcp", {})
                gimbal_ip = gimbal_config.get("ip", "192.168.144.200")
                gimbal_port = gimbal_config.get("port", 2000)
                
                self.controller = KTGGimbalController(ip=gimbal_ip, port=gimbal_port)
                
                # 連接雲台
                self.logger.log("[Tracking] 連接雲台...", step="track", level="info")
                if self.controller.connect():
                    self.controller.start_listening()
                else:
                    self.logger.log("[Tracking] 雲台連接失敗", step="track", level="error")
                    return False
                
            # 初始化獨立 MQTT 客戶端
            if not self._init_local_mqtt():
                self.logger.log("[Tracking] 獨立 MQTT 初始化失敗", step="track", level="error")
                return False
                
            # 註冊共享 MQTT 處理器（用於雲台資訊）
            if self.shared_mqtt_service:
                gimbal_topic = "gimbal/info"
                handler_id = self.shared_mqtt_service.add_handler(gimbal_topic, self._on_gimbal_info)
                if handler_id:
                    self.handler_ids.append(handler_id)
                    self.logger.log(f"[Tracking] 共享 MQTT 處理器註冊成功 - Topic: {gimbal_topic}", 
                                    step="track")
                
            # 重置 PID 控制器
            self.yaw_pid.reset()
            self.pitch_pid.reset()
            
            # 重置狀態
            self.start_time = time.time()
            self.control_count = 0
            self.error_count = 0
            self.target_lost_count = 0
            self.last_x = None
            self.last_y = None
            self.target_history.clear()
            
            # 啟動追蹤線程
            self.running = True
            self.thread = threading.Thread(target=self._tracking_loop, daemon=True, name="GimbalTracker")
            self.thread.start()
            
            self.logger.log("[Tracking] 追蹤器啟動成功", step="track", level="info")
            return True
            
        except Exception as e:
            self.logger.log(f"[Tracking] 啟動失敗: {e}", step="track", level="error")
            self.stop()
            return False

    def stop(self):
        """停止追蹤器"""
        if not self.running:
            return
            
        self.logger.log("[Tracking] 正在停止追蹤器...", step="track", level="info")
        
        # 停止追蹤線程
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                self.logger.log("[Tracking] 追蹤線程停止超時", step="track", level="warning")

        # 停止獨立 MQTT 客戶端
        if self.local_mqtt:
            try:
                self.local_mqtt.loop_stop()
                self.local_mqtt.disconnect()
                self.logger.log("[Tracking] 獨立 MQTT 已斷開", step="track")
            except Exception as e:
                self.logger.log(f"[Tracking] 停止獨立 MQTT 錯誤: {e}", step="track", level="error")
            
        # 移除共享 MQTT 處理器
        if self.shared_mqtt_service and self.handler_ids:
            for i, handler_id in enumerate(self.handler_ids):
                try:
                    if self.shared_mqtt_service.remove_handler(handler_id):
                        self.logger.log(f"[Tracking] 共享 MQTT 處理器 {i+1} 已移除", step="track", level="debug")
                    else:
                        self.logger.log(f"[Tracking] 共享 MQTT 處理器 {i+1} 移除失敗", step="track", level="warning")
                except Exception as e:
                    self.logger.log(f"[Tracking] 移除共享 MQTT 處理器錯誤: {e}", step="track", level="error")
            
            self.handler_ids = []
            self.logger.log("[Tracking] 所有共享 MQTT 處理器已移除", step="track")
        
        # 停止雲台控制器
        if self.controller:
            try:
                # 停止雲台運動
                self.controller.eo_control_gimbal(0, 0)  # 停止所有運動
                time.sleep(0.1)
                
                self.controller.stop_listening()
                self.controller.disconnect()
                self.logger.log("[Tracking] 雲台控制器已斷開", step="track", level="info")
            except Exception as e:
                self.logger.log(f"[Tracking] 停止雲台控制器錯誤: {e}", step="track", level="error")
        
        # 輸出統計資訊
        if self.start_time:
            runtime = time.time() - self.start_time
            avg_latency = (sum(self.control_latency_samples) / len(self.control_latency_samples) 
                          if self.control_latency_samples else 0)
            
            self.logger.log(f"[Tracking] 運行統計 - 時間: {runtime:.1f}s, "
                           f"控制次數: {self.control_count}, 錯誤次數: {self.error_count}, "
                           f"目標遺失次數: {self.target_lost_count}, 平均延遲: {avg_latency*1000:.1f}ms", 
                           step="track", level="info")
        
        self.logger.log("[Tracking] 追蹤器已完全停止", step="track", level="info")

    def _tracking_loop(self):
        """追蹤主循環 - 專注於即時控制，不影響其他 MQTT 操作"""
        self.logger.log("[Tracking] 追蹤循環已啟動", step="track", level="info")
        
        loop_interval = 1.0 / self.control_frequency
        
        while self.running:
            loop_start_time = time.time()
            
            try:
                # 線程安全地獲取目標位置
                with self.data_lock:
                    x = self.target['x']
                    y = self.target['y']
                    confidence = self.target['confidence']
                    target_age = time.time() - self.target['timestamp']

                # 檢查目標有效性
                if x is None or y is None:
                    time.sleep(loop_interval)
                    continue

                # 檢查目標是否過舊
                if target_age > self.target_timeout:
                    self.logger.log(f"[Tracking] 目標資訊過舊: {target_age:.2f}s", 
                                   step="track", level="debug")
                    time.sleep(loop_interval)
                    continue

                # 濾波處理
                filtered_x, filtered_y = self._filter_target_position(x, y)
                if filtered_x is None or filtered_y is None:
                    time.sleep(loop_interval)
                    continue

                # 預測處理
                predicted_x, predicted_y = self._predict_target_position(filtered_x, filtered_y)

                # 更新最後位置
                self.last_x = predicted_x
                self.last_y = predicted_y

                # 計算誤差
                error_x = predicted_x - self.center_x
                error_y = self.center_y - predicted_y  # pitch 通常方向相反

                # 穩定性檢查 - 如果誤差很小，減少控制輸出
                error_magnitude = np.sqrt(error_x**2 + error_y**2)
                if error_magnitude < self.stabilization_threshold:
                    stabilization_factor = error_magnitude / self.stabilization_threshold
                else:
                    stabilization_factor = 1.0

                # PID 控制計算
                yaw_speed = self.yaw_pid.compute(error_x) * stabilization_factor
                pitch_speed = self.pitch_pid.compute(error_y) * stabilization_factor

                # 限制速度
                yaw_speed = np.clip(yaw_speed, -self.max_speed, self.max_speed)
                pitch_speed = np.clip(pitch_speed, -self.max_speed, self.max_speed)

                # 控制雲台
                if self.controller and self.controller.connected:
                    control_start_time = time.time()
                    self.controller.eo_control_gimbal(yaw_speed, pitch_speed)
                    control_latency = time.time() - control_start_time
                    
                    # 記錄控制延遲
                    self.control_latency_samples.append(control_latency)
                    if len(self.control_latency_samples) > self.max_latency_samples:
                        self.control_latency_samples.pop(0)
                    
                    self.control_count += 1
                    self.last_control_time = time.time()

                # 詳細日誌記錄
                if self.control_count % (self.control_frequency * 2) == 0:  # 每2秒記錄一次
                    avg_latency = (sum(self.control_latency_samples[-10:]) / 
                                  min(10, len(self.control_latency_samples)))
                    
                    self.logger.log(f"[Tracking] 控制狀態: 目標=({predicted_x:.1f},{predicted_y:.1f}), "
                                   f"誤差=({error_x:.1f},{error_y:.1f}), "
                                   f"速度=({yaw_speed:.2f}°/s,{pitch_speed:.2f}°/s), "
                                   f"置信度={confidence:.2f}, 延遲={avg_latency*1000:.1f}ms", 
                                   step="track", level="debug")

            except Exception as e:
                self.error_count += 1
                self.logger.log(f"[Tracking] 追蹤循環錯誤: {e}", step="track", level="error")
            
            # 控制循環頻率
            loop_time = time.time() - loop_start_time
            sleep_time = max(0, loop_interval - loop_time)
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif loop_time > loop_interval * 2:
                self.logger.log(f"[Tracking] 控制循環過慢: {loop_time*1000:.1f}ms", 
                               step="track", level="warning")

        self.logger.log("[Tracking] 追蹤循環已退出", step="track", level="info")

    def get_status(self):
        """獲取追蹤器狀態"""
        with self.data_lock:
            runtime = time.time() - self.start_time if self.start_time else 0
            avg_latency = (sum(self.control_latency_samples) / len(self.control_latency_samples) 
                          if self.control_latency_samples else 0)
            
            return {
                "running": self.running,
                "runtime_seconds": runtime,
                "control_count": self.control_count,
                "error_count": self.error_count,
                "target_lost_count": self.target_lost_count,
                "target": self.target.copy(),
                "gimbal_info": self.gimbal_info.copy(),
                "last_position": (self.last_x, self.last_y),
                "handler_count": len(self.handler_ids),
                "control_frequency": self.control_frequency,
                "max_speed": self.max_speed,
                "target_history_length": len(self.target_history),
                "average_control_latency_ms": avg_latency * 1000,
                "is_tracking_active": self.is_tracking_active(),
                "local_mqtt_connected": self.local_mqtt_connected,
                "gimbal_connected": self.controller.connected if self.controller else False,
                "pid_states": {
                    "yaw": self.yaw_pid.get_state(),
                    "pitch": self.pitch_pid.get_state()
                }
            }

    def update_pid_parameters(self, yaw_params=None, pitch_params=None):
        """動態更新 PID 參數"""
        try:
            if yaw_params:
                self.yaw_pid.update_parameters(**yaw_params)
                self.yaw_pid_params.update(yaw_params)
                self.logger.log(f"[Tracking] Yaw PID 參數已更新: {yaw_params}", 
                               step="track", level="info")
            
            if pitch_params:
                self.pitch_pid.update_parameters(**pitch_params)
                self.pitch_pid_params.update(pitch_params)
                self.logger.log(f"[Tracking] Pitch PID 參數已更新: {pitch_params}", 
                               step="track", level="info")
            
            return True
        except Exception as e:
            self.logger.log(f"[Tracking] 更新 PID 參數失敗: {e}", step="track", level="error")
            return False

    def reset_tracking(self):
        """重置追蹤狀態"""
        try:
            with self.data_lock:
                self.target = {'x': None, 'y': None, 'timestamp': 0, 'confidence': 0}
                self.last_x = None
                self.last_y = None
                self.target_history.clear()
            
            self.yaw_pid.reset()
            self.pitch_pid.reset()
            
            self.logger.log("[Tracking] 追蹤狀態已重置", step="track", level="info")
            return True
        except Exception as e:
            self.logger.log(f"[Tracking] 重置追蹤狀態失敗: {e}", step="track", level="error")
            return False

    def is_tracking_active(self):
        """檢查是否正在積極追蹤目標"""
        with self.data_lock:
            if self.target['timestamp'] == 0:
                return False
            
            target_age = time.time() - self.target['timestamp']
            return (target_age <= self.target_timeout and 
                   self.target['x'] is not None and
                   self.target['confidence'] >= self.min_confidence)

    def get_tracking_performance(self):
        """獲取追蹤性能指標"""
        runtime = time.time() - self.start_time if self.start_time else 0
        if runtime > 0:
            control_rate = self.control_count / runtime
            error_rate = self.error_count / runtime
            target_lost_rate = self.target_lost_count / runtime
        else:
            control_rate = 0
            error_rate = 0
            target_lost_rate = 0
        
        avg_latency = (sum(self.control_latency_samples) / len(self.control_latency_samples) 
                      if self.control_latency_samples else 0)
        
        return {
            "control_rate_hz": control_rate,
            "error_rate_per_second": error_rate,
            "target_lost_rate_per_second": target_lost_rate,
            "total_runtime": runtime,
            "is_tracking": self.is_tracking_active(),
            "target_age": time.time() - self.target['timestamp'] if self.target['timestamp'] > 0 else None,
            "average_control_latency_ms": avg_latency * 1000,
            "control_frequency_actual": control_rate,
            "target_history_length": len(self.target_history)
        }

    def emergency_stop(self):
        """緊急停止雲台運動"""
        try:
            if self.controller and self.controller.connected:
                self.controller.eo_control_gimbal(0, 0)
                self.logger.log("[Tracking] 緊急停止雲台運動", step="track", level="warning")
                return True
        except Exception as e:
            self.logger.log(f"[Tracking] 緊急停止失敗: {e}", step="track", level="error")
        return False

    def set_control_parameters(self, max_speed=None, frequency=None, jump_threshold=None):
        """動態設置控制參數"""
        try:
            if max_speed is not None:
                self.max_speed = max_speed
                self.logger.log(f"[Tracking] 最大速度已更新: {max_speed}°/s", step="track")
            
            if frequency is not None:
                self.control_frequency = frequency
                self.logger.log(f"[Tracking] 控制頻率已更新: {frequency}Hz", step="track")
            
            if jump_threshold is not None:
                self.jump_filter_thresh = jump_threshold
                self.logger.log(f"[Tracking] 跳動閾值已更新: {jump_threshold}px", step="track")
            
            return True
        except Exception as e:
            self.logger.log(f"[Tracking] 設置控制參數失敗: {e}", step="track", level="error")
            return False