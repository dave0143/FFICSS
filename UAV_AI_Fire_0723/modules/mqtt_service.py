"""
MQTT 通訊服務 - 修正版
整合了基礎功能和增強功能，解決循環導入和類衝突問題
"""

import paho.mqtt.client as mqtt
import threading
import json
import time
import logging
import queue
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Callable, Optional, List, Union
import uuid

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"

class MessagePriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4

class QueuedMessage:
    def __init__(self, topic: str, payload: Any, qos: int = 1, 
                 retain: bool = False, priority: MessagePriority = MessagePriority.NORMAL):
        self.id = str(uuid.uuid4())
        self.topic = topic
        self.payload = payload
        self.qos = qos
        self.retain = retain
        self.priority = priority
        self.timestamp = time.time()
        self.retry_count = 0
        self.max_retries = 3

    def __lt__(self, other):
        """優先級比較，用於優先隊列排序"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.timestamp < other.timestamp

class MQTTStatistics:
    def __init__(self):
        self.reset()
    
    def reset(self):
        self.messages_sent = 0
        self.messages_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
        self.connection_count = 0
        self.last_message_time = None
        self.publish_failures = 0
        self.queue_overflow_count = 0
        self.start_time = time.time()
    
    def get_stats(self) -> Dict[str, Any]:
        uptime = time.time() - self.start_time
        return {
            "uptime_seconds": uptime,
            "messages_sent": self.messages_sent,
            "messages_received": self.messages_received,
            "bytes_sent": self.bytes_sent,
            "bytes_received": self.bytes_received,
            "connection_count": self.connection_count,
            "publish_failures": self.publish_failures,
            "queue_overflow_count": self.queue_overflow_count,
            "last_message_time": self.last_message_time,
            "avg_messages_per_minute": (self.messages_received / (uptime / 60)) if uptime > 0 else 0
        }

class MQTTService(threading.Thread):
    """
    統一的 MQTT 服務類
    包含基礎功能和增強功能
    """
    
    def __init__(self, config: Dict[str, Any], on_message_callback: Callable):
        super().__init__(name="MQTTService")
        self.config = config
        self.on_message_callback = on_message_callback
        
        # 基本設定
        self.broker = config["broker"]
        self.port = config.get("port", 1883)
        self.keepalive = config.get("keepalive", 60)
        self.qos = config.get("qos", 1)
        self.topics = config.get("topics", {})
        
        # 連接設定
        self.username = config.get("username")
        self.password = config.get("password")
        self.client_id = config.get("client_id", f"mqtt_client_{uuid.uuid4().hex[:8]}")
        
        # 重連設定
        self.max_reconnect_attempts = config.get("max_reconnect_attempts", 5)
        self.initial_reconnect_delay = config.get("reconnect_delay", 1)
        self.max_reconnect_delay = config.get("max_reconnect_delay", 60)
        self.exponential_backoff = config.get("exponential_backoff", True)
        
        # 訊息隊列設定（可選功能）
        self.enable_queue = config.get("enable_message_queue", False)
        self.max_queue_size = config.get("max_queue_size", 1000)
        self.queue_timeout = config.get("queue_timeout", 5)
        
        # 狀態管理
        self.connection_state = ConnectionState.DISCONNECTED
        self.state_lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self.connected = False  # 向後兼容
        
        # MQTT 客戶端
        self.client = mqtt.Client(client_id=self.client_id, clean_session=True)
        self.setup_client()
        
        # 訊息隊列（如果啟用）
        if self.enable_queue:
            self.publish_queue = queue.PriorityQueue(maxsize=self.max_queue_size)
            self.publish_thread = None
        
        # 統計和監控
        self.stats = MQTTStatistics()
        self.logger = None
        
        # 重連管理
        self.reconnect_attempts = 0
        self.last_reconnect_time = 0
        
        # 訂閱管理
        self.subscribed_topics = set()
        self.pending_subscriptions = set()
        
        # 處理器管理
        self.message_handlers = {}
        self.handler_lock = threading.RLock()
        
        self.daemon = True

    def setup_client(self):
        """設置MQTT客戶端"""
        # 設置回調函數
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_publish = self._on_publish
        self.client.on_subscribe = self._on_subscribe
        self.client.on_unsubscribe = self._on_unsubscribe
        self.client.on_log = self._on_log
        
        # 設置認證
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)
        
        # 設置遺囑消息
        will_topic = self.config.get("will_topic")
        if will_topic:
            will_payload = self.config.get("will_payload", {"status": "offline", "timestamp": int(time.time())})
            self.client.will_set(will_topic, json.dumps(will_payload), qos=1, retain=True)

    def set_logger(self, logger):
        """設置日誌記錄器"""
        self.logger = logger

    def log(self, msg: str, level: str = "info", extra_data: Dict = None):
        """記錄日誌"""
        log_entry = f"[MQTT] {msg}"
        if extra_data:
            log_entry += f" | {extra_data}"
            
        if self.logger:
            self.logger.log(log_entry, level=level, source="mqtt")
        else:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] [{level.upper()}] {log_entry}")

    def run(self):
        """主執行緒"""
        self.log("啟動 MQTT 服務", "info", {"client_id": self.client_id})
        
        # 啟動發佈隊列處理執行緒（如果啟用）
        if self.enable_queue:
            self.publish_thread = threading.Thread(target=self._publish_worker, daemon=True)
            self.publish_thread.start()
        
        # 主連接循環
        while not self.shutdown_event.is_set():
            try:
                if self.connection_state == ConnectionState.DISCONNECTED:
                    self._attempt_connection()
                
                # 監控連接狀態
                if self.shutdown_event.wait(timeout=1):
                    break
                
            except Exception as e:
                self.log(f"主循環錯誤: {str(e)}", "error")
                time.sleep(5)
        
        self.log("MQTT 服務已停止")

    def _attempt_connection(self):
        """嘗試連接到MQTT代理"""
        if self.max_reconnect_attempts > 0 and self.reconnect_attempts >= self.max_reconnect_attempts:
            self.log(f"已達到最大重連次數 ({self.max_reconnect_attempts})", "error")
            return False
        
        current_time = time.time()
        delay = self._calculate_reconnect_delay()
        
        if current_time - self.last_reconnect_time < delay:
            return False
        
        with self.state_lock:
            self.connection_state = ConnectionState.CONNECTING
        
        try:
            self.log(f"嘗試連接到 MQTT Broker: {self.broker}:{self.port} (嘗試 #{self.reconnect_attempts + 1})")
            
            # 連接到代理
            self.client.connect(self.broker, self.port, self.keepalive)
            self.client.loop_start()
            
            self.last_reconnect_time = current_time
            self.reconnect_attempts += 1
            
            # 等待連接結果
            timeout = 10
            start_time = time.time()
            while (time.time() - start_time < timeout and 
                   self.connection_state in [ConnectionState.CONNECTING, ConnectionState.RECONNECTING]):
                time.sleep(0.1)
            
            return self.connection_state == ConnectionState.CONNECTED
            
        except Exception as e:
            self.log(f"連接失敗: {str(e)}", "error")
            with self.state_lock:
                self.connection_state = ConnectionState.DISCONNECTED
                self.connected = False
            return False

    def _calculate_reconnect_delay(self) -> float:
        """計算重連延遲時間"""
        if not self.exponential_backoff:
            return self.initial_reconnect_delay
        
        delay = self.initial_reconnect_delay * (2 ** min(self.reconnect_attempts, 6))
        return min(delay, self.max_reconnect_delay)

    def _on_connect(self, client, userdata, flags, rc):
        """連接回調"""
        if rc == 0:
            with self.state_lock:
                self.connection_state = ConnectionState.CONNECTED
                self.connected = True  # 向後兼容
            
            self.stats.connection_count += 1
            self.reconnect_attempts = 0
            
            self.log("MQTT 連接成功", "info", {"flags": flags, "client_id": self.client_id})
            
            # 訂閱主題
            self._subscribe_topics()
            
            # 發佈上線消息
            self._publish_online_status()
            
        else:
            with self.state_lock:
                self.connection_state = ConnectionState.DISCONNECTED
                self.connected = False
            
            error_messages = {
                1: "協議版本不正確",
                2: "客戶端標識符無效", 
                3: "服務器不可用",
                4: "用戶名或密碼錯誤",
                5: "未授權"
            }
            
            error_msg = error_messages.get(rc, f"未知錯誤 (代碼: {rc})")
            self.log(f"MQTT 連接失敗: {error_msg}", "error")

    def _on_disconnect(self, client, userdata, rc):
        """斷線回調"""
        with self.state_lock:
            self.connected = False
            if self.connection_state != ConnectionState.STOPPING:
                self.connection_state = ConnectionState.RECONNECTING
        
        if rc != 0:
            self.log(f"意外斷線 (代碼: {rc})", "warning")
        else:
            self.log("正常斷線", "info")

    def _on_message(self, client, userdata, msg):
        """訊息接收回調"""
        try:
            # 統計
            self.stats.messages_received += 1
            self.stats.bytes_received += len(msg.payload)
            self.stats.last_message_time = time.time()
            
            # 解析載荷
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = msg.payload.decode("utf-8", errors='ignore')
            
            self.log(f"收到訊息: topic={msg.topic}, payload_size={len(msg.payload)}")
            
            # 回調用戶處理函數
            if self.on_message_callback:
                try:
                    self.on_message_callback(msg.topic, payload)
                except Exception as e:
                    self.log(f"用戶回調函數錯誤: {str(e)}", "error")
            
            # 調用特定主題的處理器
            with self.handler_lock:
                if msg.topic in self.message_handlers:
                    for handler in self.message_handlers[msg.topic]:
                        try:
                            handler(msg.topic, payload)
                        except Exception as e:
                            self.log(f"主題處理器錯誤: {str(e)}", "error", {"topic": msg.topic})
        except Exception as e:
            self.log(f"處理接收訊息錯誤: {str(e)}", "error")

    def _on_publish(self, client, userdata, mid):
        """發佈確認回調"""
        pass  # 可以在此處添加發佈確認邏輯

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """訂閱確認回調"""
        self.log(f"訂閱確認: message_id={mid}, granted_qos={granted_qos}")

    def _on_unsubscribe(self, client, userdata, mid):
        """取消訂閱確認回調"""
        self.log(f"取消訂閱確認: message_id={mid}")

    def _on_log(self, client, userdata, level, buf):
        """MQTT日誌回調"""
        if level <= mqtt.MQTT_LOG_WARNING:  # 只記錄警告和錯誤
            log_level = "warning" if level == mqtt.MQTT_LOG_WARNING else "error"
            self.log(f"MQTT客戶端: {buf}", log_level)

    def _subscribe_topics(self):
        """訂閱主題"""
        try:
            for name, topic in self.topics.items():
                if name.startswith("read"):
                    result, _ = self.client.subscribe(topic, qos=self.qos)
                    if result == mqtt.MQTT_ERR_SUCCESS:
                        self.subscribed_topics.add(topic)
                        self.log(f"成功訂閱主題: {name} -> {topic}")
                    else:
                        self.log(f"訂閱失敗: {name} -> {topic}", "error")
        except Exception as e:
            self.log(f"批量訂閱錯誤: {str(e)}", "error")

    def _publish_online_status(self):
        """發佈上線狀態"""
        status_topic = self.config.get("status_topic")
        if status_topic:
            status_msg = {
                "status": "online",
                "timestamp": int(time.time()),
                "client_id": self.client_id,
                "version": "1.0"
            }
            self.publish(status_topic, status_msg, retain=True)

    def _publish_worker(self):
        """發佈隊列工作執行緒"""
        if not self.enable_queue:
            return
            
        self.log("發佈隊列工作執行緒啟動")
        
        while not self.shutdown_event.is_set():
            try:
                # 等待隊列中的訊息
                queued_msg = self.publish_queue.get(timeout=self.queue_timeout)
                
                if queued_msg is None:  # 停止信號
                    break
                
                # 檢查連接狀態
                if self.connection_state != ConnectionState.CONNECTED:
                    # 重新放回隊列等待連接
                    if queued_msg.retry_count < queued_msg.max_retries:
                        queued_msg.retry_count += 1
                        self.publish_queue.put(queued_msg)
                    else:
                        self.log(f"訊息重試次數超限，丟棄訊息", "warning", {"topic": queued_msg.topic})
                        self.stats.publish_failures += 1
                    continue
                
                # 發佈訊息
                success = self._do_publish(queued_msg)
                if not success and queued_msg.retry_count < queued_msg.max_retries:
                    queued_msg.retry_count += 1
                    self.publish_queue.put(queued_msg)
                elif not success:
                    self.stats.publish_failures += 1
                
                self.publish_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                self.log(f"發佈工作執行緒錯誤: {str(e)}", "error")

    def _do_publish(self, queued_msg: QueuedMessage) -> bool:
        """執行實際的發佈操作"""
        try:
            # 準備載荷
            if isinstance(queued_msg.payload, dict):
                payload_str = json.dumps(queued_msg.payload, ensure_ascii=False)
            else:
                payload_str = str(queued_msg.payload)
            
            # 發佈訊息
            result = self.client.publish(
                queued_msg.topic, 
                payload_str, 
                qos=queued_msg.qos, 
                retain=queued_msg.retain
            )
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats.messages_sent += 1
                self.stats.bytes_sent += len(payload_str.encode('utf-8'))
                return True
            else:
                self.log(f"發佈失敗: topic={queued_msg.topic}, error_code={result.rc}", "warning")
                return False
                
        except Exception as e:
            self.log(f"發佈訊息錯誤: {str(e)}", "error", {"topic": queued_msg.topic})
            return False

    # 公共方法

    def add_handler(self, topic: str, handler: Callable):
        """添加主題處理器"""
        with self.handler_lock:
            if topic not in self.message_handlers:
                self.message_handlers[topic] = []
            self.message_handlers[topic].append(handler)
            self.log(f"添加處理器到主題: {topic}", "debug")
    
    def remove_handler(self, topic: str, handler: Callable):
        """移除主題處理器"""
        with self.handler_lock:
            if topic in self.message_handlers:
                if handler in self.message_handlers[topic]:
                    self.message_handlers[topic].remove(handler)
                    self.log(f"從主題 {topic} 移除處理器", "debug")

    def publish(self, topic: str, payload: Any, qos: int = None, 
                retain: bool = False, priority: MessagePriority = MessagePriority.NORMAL) -> bool:
        """發佈訊息"""
        if self.shutdown_event.is_set():
            return False
        
        if qos is None:
            qos = self.qos
        
        # 如果啟用隊列模式
        if self.enable_queue:
            try:
                queued_msg = QueuedMessage(topic, payload, qos, retain, priority)
                self.publish_queue.put_nowait(queued_msg)
                return True
            except queue.Full:
                self.stats.queue_overflow_count += 1
                self.log(f"發佈隊列已滿，丟棄訊息: {topic}", "warning")
                return False
            except Exception as e:
                self.log(f"加入發佈隊列錯誤: {str(e)}", "error")
                return False
        
        # 直接模式
        try:
            if isinstance(payload, dict):
                payload_str = json.dumps(payload, ensure_ascii=False)
            else:
                payload_str = str(payload)
            
            result = self.client.publish(topic, payload_str, qos=qos, retain=retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.stats.messages_sent += 1
                self.stats.bytes_sent += len(payload_str.encode('utf-8'))
                self.log(f"發佈成功: topic={topic}")
                return True
            else:
                self.log(f"發佈失敗 (錯誤代碼 {result.rc}): topic={topic}", "error")
                return False
                
        except Exception as e:
            self.log(f"發佈訊息錯誤: {str(e)}", "error")
            return False

    def subscribe(self, topic: str, qos: int = None) -> bool:
        """訂閱主題"""
        if qos is None:
            qos = self.qos
        
        try:
            if self.connection_state == ConnectionState.CONNECTED:
                result, _ = self.client.subscribe(topic, qos=qos)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self.subscribed_topics.add(topic)
                    self.log(f"成功訂閱主題: {topic}", "info", {"qos": qos})
                    return True
                else:
                    self.log(f"訂閱失敗: {topic}", "error", {"error_code": result})
                    return False
            else:
                # 連接未建立時，加入待訂閱列表
                self.pending_subscriptions.add((topic, qos))
                self.log(f"連接未建立，主題加入待訂閱列表: {topic}", "info")
                return True
                
        except Exception as e:
            self.log(f"訂閱主題錯誤: {str(e)}", "error", {"topic": topic})
            return False

    def unsubscribe(self, topic: str) -> bool:
        """取消訂閱主題"""
        try:
            if self.connection_state == ConnectionState.CONNECTED:
                result, _ = self.client.unsubscribe(topic)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self.subscribed_topics.discard(topic)
                    self.log(f"成功取消訂閱主題: {topic}", "info")
                    return True
                else:
                    self.log(f"取消訂閱失敗: {topic}", "error", {"error_code": result})
                    return False
            else:
                self.log("連接未建立，無法取消訂閱", "warning", {"topic": topic})
                return False
                
        except Exception as e:
            self.log(f"取消訂閱主題錯誤: {str(e)}", "error", {"topic": topic})
            return False

    def is_connected(self) -> bool:
        """檢查連接狀態"""
        return self.connection_state == ConnectionState.CONNECTED

    def get_connection_state(self) -> ConnectionState:
        """獲取詳細連接狀態"""
        return self.connection_state

    def get_statistics(self) -> Dict[str, Any]:
        """獲取統計資訊"""
        stats = self.stats.get_stats()
        stats.update({
            "connection_state": self.connection_state.value,
            "reconnect_attempts": self.reconnect_attempts,
            "subscribed_topics": list(self.subscribed_topics),
            "client_id": self.client_id
        })
        
        if self.enable_queue:
            stats["queue_size"] = self.publish_queue.qsize()
            
        return stats

    def force_reconnect(self):
        """強制重新連接"""
        self.log("強制重新連接", "info")
        try:
            with self.state_lock:
                if self.connection_state == ConnectionState.CONNECTED:
                    self.client.disconnect()
                self.connection_state = ConnectionState.DISCONNECTED
                self.connected = False
                self.reconnect_attempts = 0
        except Exception as e:
            self.log(f"強制重連錯誤: {str(e)}", "error")

    def stop(self):
        """停止服務"""
        self.log("開始停止 MQTT 服務", "info")
        
        with self.state_lock:
            self.connection_state = ConnectionState.STOPPING
            self.connected = False
        
        self.shutdown_event.set()
        
        try:
            # 發佈離線狀態
            status_topic = self.config.get("status_topic")
            if status_topic and self.is_connected():
                offline_msg = {
                    "status": "offline",
                    "timestamp": int(time.time()),
                    "client_id": self.client_id
                }
                # 直接發佈，不通過隊列
                payload_str = json.dumps(offline_msg)
                self.client.publish(status_topic, payload_str, qos=1, retain=True)
                time.sleep(0.5)  # 等待發佈完成
            
            # 停止發佈隊列
            if self.enable_queue and self.publish_thread and self.publish_thread.is_alive():
                self.publish_queue.put(None)  # 停止信號
                self.publish_thread.join(timeout=5)
            
            # 停止MQTT客戶端
            self.client.loop_stop()
            self.client.disconnect()
            
            self.log("MQTT 服務已完全停止", "info")
            
        except Exception as e:
            self.log(f"停止服務時出錯: {str(e)}", "error")

# 別名供不同使用場景
EnhancedMQTTService = MQTTService

# 使用範例
if __name__ == "__main__":
    def message_handler(topic, payload):
        print(f"收到訊息: {topic} -> {payload}")
    
    # 基礎配置
    basic_config = {
        "broker": "localhost",
        "port": 1883,
        "keepalive": 60,
        "qos": 1,
        "topics": {
            "read1": "test/topic1",
            "read2": "test/topic2",
            "write": "test/output"
        }
    }
    
    # 增強配置
    enhanced_config = {
        "broker": "localhost",
        "port": 1883,
        "keepalive": 60,
        "qos": 1,
        "topics": {
            "read1": "test/topic1", 
            "read2": "test/topic2",
            "write": "test/output"
        },
        "status_topic": "test/status",
        "enable_message_queue": True,
        "max_queue_size": 500,
        "reconnect_delay": 2,
        "max_reconnect_delay": 30,
        "exponential_backoff": True
    }
    
    mqtt_service = MQTTService(enhanced_config, message_handler)
    mqtt_service.start()
    
    try:
        # 測試發佈
        time.sleep(2)
        for i in range(5):
            mqtt_service.publish("test/output", {"test": i, "timestamp": time.time()})
            time.sleep(1)
        
        # 顯示統計
        print("統計資訊:", mqtt_service.get_statistics())
        
    except KeyboardInterrupt:
        print("停止服務...")
    finally:
        mqtt_service.stop()
        mqtt_service.join(timeout=5)