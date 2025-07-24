import time
import threading
import sys
import os
from typing import Optional, Callable, TYPE_CHECKING

# 添加項目根目錄到 Python 路徑
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if TYPE_CHECKING:
    from modules.ktgGimbalControl import KTGGimbalController
else:
    try:
        from modules.ktgGimbalControl import KTGGimbalController
    except ImportError:
        print("無法導入 KTGGimbalController")


class ScoutMode:
    """
    Scout Mode Controller
    Implements smooth scanning motion of the gimbal within specified angle ranges
    """
    
    def __init__(self, gimbal_controller: KTGGimbalController, active_event: Optional[threading.Event] = None):
        """
        Initialize Scout Mode
        
        Args:
            gimbal_controller: KTG gimbal controller instance
            active_event: Thread event for controlling mode activation
        """
        self.gimbal = gimbal_controller
        self.is_running = False
        self.scan_thread: Optional[threading.Thread] = None
        self.callback: Optional[Callable] = None
        self.active_event = active_event
        
        # Scan parameters
        self.yaw_start = -60.0  # Starting yaw angle
        self.yaw_end = 60.0     # Ending yaw angle
        self.pitch_angle = -10.0  # Pitch angle
        self.step_size = 2.0    # Angle increment per step
        self.step_delay = 0.5   # Delay time per step (seconds)
        
        # Gimbal angle limits (adjust according to actual gimbal specifications)
        self.yaw_min = -90.0    # Minimum yaw angle
        self.yaw_max = 90.0     # Maximum yaw angle
        self.pitch_min = -90.0  # Minimum pitch angle
        self.pitch_max = 30.0   # Maximum pitch angle
    

    def set_scan_parameters(self, 
                           yaw_start: float = -60.0,
                           yaw_end: float = 60.0,
                           pitch_angle: float = -15.0,
                           step_size: float = 5.0,
                           step_delay: float = 0.15):
        """
        Set scan parameters
        
        Args:
            yaw_start: Starting yaw angle
            yaw_end: Ending yaw angle
            pitch_angle: Pitch angle
            step_size: Angle increment per step
            step_delay: Delay time per step (seconds)
        """
        # Check if angles are within gimbal limits
        yaw_start = max(self.yaw_min, min(self.yaw_max, yaw_start))
        yaw_end = max(self.yaw_min, min(self.yaw_max, yaw_end))
        pitch_angle = max(self.pitch_min, min(self.pitch_max, pitch_angle))
        
        self.yaw_start = yaw_start
        self.yaw_end = yaw_end
        self.pitch_angle = pitch_angle
        self.step_size = step_size
        self.step_delay = step_delay
        
    def set_callback(self, callback: Callable):
        """
        Set callback function for reporting scan progress
        
        Args:
            callback: Callback function that receives current angle information
        """
        self.callback = callback
        
    def _send_command(self, command_func, *args, **kwargs):
        """
        Send command, simplified version, no retry
        
        Args:
            command_func: Command function
            *args, **kwargs: Command parameters
            
        Returns:
            bool: Whether successful
        """
        try:
            result = command_func(*args, **kwargs)
            
            # Check response type
            if result.get("success", False):
                return True
            elif result.get("type") == "gimbal_info":
                return True
            else:
                return False
                
        except Exception as e:
            return False
        
    def _move_to_angle(self, mode: int, angle: float, description: str):
        """
        Move to specified angle, simplified version
        
        Args:
            mode: 1=yaw, 2=pitch
            angle: Target angle
            description: Description information
        """
        # Use simplified command sending method
        success = self._send_command(
            self.gimbal.eo_rotate_to_angle,
            mode=mode,
            angle=angle,
            reference=0x02  # Follow heading reference
        )
        
        if success:
            return True
        else:
            return False
        
    def _scan_worker(self):
        """
        Scan worker thread - Complete multiple rounds of scanning, pitch angle moves down 15 degrees after each scan
        
        修改重點：
        1. 將長時間睡眠拆分成短片段（0.05秒）
        2. 每次醒來時檢查中斷標誌
        3. 確保總睡眠時間不變但更頻繁檢查中斷
        """
        try:
            # Initialize pitch angle
            current_pitch = self.pitch_angle
            scan_round = 0
            
            # Start multiple rounds of scanning, pitch angle moves down 15 degrees after each scan
            while self.is_running and current_pitch >= -60.0:
                # Check for interruption signal
                if not self.is_running:
                    break
                    
                scan_round += 1
                
                # Set current pitch angle
                self._move_to_angle(2, current_pitch, "Setting pitch angle")
                    
                # Wait for pitch angle setting to complete (拆分成短睡眠)
                sleep_remaining = 1.0
                while sleep_remaining > 0 and self.is_running:
                    time.sleep(0.05)
                    sleep_remaining -= 0.05
                
                # Check for interruption again after pitch setting
                if not self.is_running:
                    break
                
                # Start yaw angle back-and-forth scanning - from start angle to end angle, then return to start angle
                current_yaw = self.yaw_start
                direction = 1  # 1: scan right, -1: scan left
                scan_count = 0
                
                # Single round scan loop
                while self.is_running:
                    # Check for interruption at the beginning of each scan step
                    if not self.is_running:
                        break
                        
                    scan_count += 1
                    
                    # Move to current yaw angle
                    self._move_to_angle(1, current_yaw, f"Moving to yaw angle {current_yaw}°")
                    
                    # Call callback function to report current position
                    if self.callback:
                        self.callback({
                            "yaw": current_yaw,
                            "pitch": current_pitch,
                            "status": "scanning",
                            "scan_count": scan_count,
                            "scan_round": scan_round,
                            "direction": "right" if direction > 0 else "left"
                        })
                    
                    # Wait for movement to complete (拆分成短睡眠)
                    sleep_remaining = self.step_delay
                    while sleep_remaining > 0 and self.is_running:
                        time.sleep(0.05)
                        sleep_remaining -= 0.05
                    
                    # Check for interruption after each step
                    if not self.is_running:
                        break
                    
                    # Calculate next position
                    current_yaw += self.step_size * direction
                    
                    # Check if reached boundary, need to change direction
                    if direction > 0 and current_yaw >= self.yaw_end:
                        # Reached right boundary, start scanning left
                        direction = -1
                        current_yaw = self.yaw_end
                    elif direction < 0 and current_yaw <= self.yaw_start:
                        # Reached left boundary, completed one round of back-and-forth scan
                        break
                
                # Check for interruption before starting next round
                if not self.is_running:
                    break
                
                # Check if continue to next round of scanning
                if current_pitch - 15.0 >= -60.0:
                    current_pitch -= 15.0
                    
                    # Wait for pitch angle adjustment to complete (拆分成短睡眠)
                    sleep_remaining = 1.0
                    while sleep_remaining > 0 and self.is_running:
                        time.sleep(0.05)
                        sleep_remaining -= 0.05
                else:
                    break
                    
        except Exception as e:
            if self.callback:
                self.callback({
                    "error": str(e),
                    "status": "error"
                })
        finally:
            self.is_running = False
            if self.active_event:
                self.active_event.clear()
            
    def start_scan(self) -> bool:
        """
        Start scout scanning - automatically stops after completing one round
        
        Returns:
            bool: Whether successfully started scanning
        """
        if self.is_running:
            return False
            
        if not self.gimbal.connected:
            return False
            
        self.is_running = True
        self.scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self.scan_thread.start()
        
        return True
        
    def stop_scan(self) -> bool:
        """
        Stop scout scanning
        
        Returns:
            bool: Whether successfully stopped scanning
        """
        if not self.is_running:
            return False
            
        self.is_running = False
        
        # Clear activation event immediately
        if self.active_event:
            self.active_event.clear()
        
        if self.scan_thread and self.scan_thread.is_alive():
            self.scan_thread.join(timeout=3.0)
            
        return True
        
    def center_gimbal(self) -> bool:
        """
        Center the gimbal
        
        Returns:
            bool: Whether successfully centered
        """
        if not self.gimbal.connected:
            return False
            
        # Stop scanning
        self.stop_scan()
        
        # Use simplified command sending method
        success = self._send_command(self.gimbal.eo_center)
        
        if success:
            return True
        else:
            return False
            
    def get_status(self) -> dict:
        """
        Get current status
        
        Returns:
            dict: Status information
        """
        return {
            "is_running": self.is_running,
            "scan_parameters": {
                "yaw_start": self.yaw_start,
                "yaw_end": self.yaw_end,
                "pitch_angle": self.pitch_angle,
                "step_size": self.step_size,
                "step_delay": self.step_delay
            },
            "gimbal_limits": {
                "yaw_min": self.yaw_min,
                "yaw_max": self.yaw_max,
                "pitch_min": self.pitch_min,
                "pitch_max": self.pitch_max
            },
            "gimbal_connected": self.gimbal.connected
        }


class Step002Controller:
    """Step 002 控制器 - 負責協調 MQTT 監聽和 Scout 模式"""
    
    def __init__(self, data_sources, config, logger, notify_complete):
        self.data_sources = data_sources
        self.config = config
        self.logger = logger
        self.notify_complete = notify_complete
        
        # 狀態管理
        self.completed = False
        self.target_detected = False
        self.scout_mode = None
        self.scout_active_event = threading.Event()
        
        # MQTT 相關
        self.mqtt = data_sources.get("mqtt_service")
        self.mqtt_handlers = []
        
        # 流程和雲台控制器
        self.flow = data_sources.get("flow_controller")
        self.gimbal_controller = data_sources.get("gimbal_controller")
        
        # 統計資訊
        self.mqtt_message_count = 0
        self.last_ai_message_time = 0
        self.last_uav_message_time = 0
        
    def log(self, msg, level="info"):
        """統一的日誌記錄"""
        self.logger.log(f"[Step002] {msg}", step="002", level=level)
    
    def on_ai_fire_target(self, topic, payload):
        """處理 AI 火源檢測訊息 - 專用處理器"""
        self.mqtt_message_count += 1
        self.last_ai_message_time = time.time()
        
        if self.completed:
            return
            
        try:
            self.log(f"收到 AI 火源檢測訊息: {payload}", level="debug")
            
            # 檢測多種可能的目標狀態格式
            status = payload.get("status")
            mode = payload.get("mode")
            coordinates = payload.get("coordinates", {})
            
            # 記錄詳細的訊息內容用於調試
            self.log(f"AI 訊息詳情 - status: {status}, mode: {mode}, coordinates: {coordinates}", level="debug")
            
            # 多種目標檢測條件
            target_found = False
            
            # 條件1: status 為 True 且有座標
            if status is True and (coordinates.get("X") is not None or coordinates.get("x") is not None):
                target_found = True
                self.log("檢測到目標 - 條件1: status=True 且有座標", level="info")
            
            # 條件2: mode 為 "EO" 且 status 為 "abnormal"
            elif mode == "EO" and status == "abnormal":
                target_found = True
                self.log("檢測到目標 - 條件2: mode=EO 且 status=abnormal", level="info")
            
            # 條件3: 直接檢查 status 字串
            elif isinstance(status, str) and status.lower() in ["abnormal", "detected", "fire", "target"]:
                target_found = True
                self.log(f"檢測到目標 - 條件3: status={status}", level="info")
            
            # 條件4: 檢查是否有 fire 或 target 相關欄位
            elif any(key in payload for key in ["fire_detected", "target_found", "anomaly_detected"]):
                if any(payload.get(key) for key in ["fire_detected", "target_found", "anomaly_detected"]):
                    target_found = True
                    self.log("檢測到目標 - 條件4: 特定欄位為 True", level="info")
            
            if target_found:
                self.log("🔥 火源目標已檢測到！停止偵察模式", step="002", level="info")
                self.target_detected = True
                self.completed = True
                
                # 立即停止 scout 模式
                if self.scout_mode:
                    self.scout_mode.stop_scan()
                    
                self.notify_complete("002_complete", 0)  # 0表示找到目標
            else:
                self.log(f"AI 訊息未觸發目標檢測條件", level="debug")
                
        except Exception as e:
            self.log(f"處理 AI 火源檢測訊息時錯誤: {e}", step="002", level="error")
    
    def on_uav_info(self, topic, payload):
        """處理 UAV 資訊訊息"""
        self.mqtt_message_count += 1
        self.last_uav_message_time = time.time()
        
        if self.completed:
            return
            
        try:
            status = payload.get("status")
            task = payload.get("task")
            
            # 檢查流程錯誤狀態
            if self.flow and hasattr(self.flow, 'flow_state') and self.flow.flow_state.value == "error":
                self.log("流程處於錯誤狀態，自動通報完成並終止", step="002", level="warning")
                self.completed = True
                if self.scout_mode:
                    self.scout_mode.stop_scan()
                self.notify_complete("002_complete", 99)
                return

            # 檢查 UAV 完成狀態
            if status == "Complete" and task == "Auto_Start":
                self.log("UAV 任務完成，自動通報完成", step="002")
                self.completed = True
                if self.scout_mode:
                    self.scout_mode.stop_scan()
                self.notify_complete("002_complete", 0)
                
        except Exception as e:
            self.log(f"處理 UAV 資訊時錯誤: {e}", step="002", level="error")
    
    def on_scout_progress(self, info):
        """處理偵察模式進度回調"""
        if self.completed:
            return
            
        try:
            if "error" in info:
                self.log(f"偵察模式錯誤: {info['error']}", step="002", level="error")
                self.completed = True
                self.notify_complete("002_complete", 99)
            else:
                # 記錄偵察進度
                scan_round = info.get('scan_round', 0)
                scan_count = info.get('scan_count', 0)
                self.log(f"偵察進度: Yaw={info['yaw']:.1f}°, Pitch={info['pitch']:.1f}° (回合 {scan_round}, 步驟 {scan_count})", 
                          step="002", level="debug")
                
                # 檢查是否完成偵察 (調整完成條件)
                if scan_round >= 3:  # 完成3個回合的偵察
                    self.log("偵察模式完成，未找到目標", step="002")
                    self.completed = True
                    self.notify_complete("002_complete", 1)  # 1表示完成但未找到目標
                    
        except Exception as e:
            self.log(f"處理偵察進度時錯誤: {e}", step="002", level="error")
    
    def setup_mqtt_handlers(self):
        """設置 MQTT 處理器"""
        if not self.mqtt:
            self.log("MQTT 服務不可用", level="error")
            return False
        
        try:
            # 使用專用的處理器來避免與 tracking 衝突
            ai_handler_id = self.mqtt.add_handler("ai_fire/target", self.on_ai_fire_target)
            uav_handler_id = self.mqtt.add_handler("uav/info", self.on_uav_info)
            
            if ai_handler_id and uav_handler_id:
                self.mqtt_handlers = [ai_handler_id, uav_handler_id]
                self.log("MQTT 處理器已註冊成功", level="info")
                return True
            else:
                self.log("MQTT 處理器註冊失敗", level="error")
                return False
                
        except Exception as e:
            self.log(f"設置 MQTT 處理器時錯誤: {e}", level="error")
            return False
    
    def cleanup_mqtt_handlers(self):
        """清理 MQTT 處理器"""
        if self.mqtt and self.mqtt_handlers:
            try:
                for handler_id in self.mqtt_handlers:
                    self.mqtt.remove_handler(handler_id)
                self.log("MQTT 處理器已移除", level="info")
            except Exception as e:
                self.log(f"移除 MQTT 處理器時錯誤: {e}", level="error")
            finally:
                self.mqtt_handlers = []
    
    def setup_scout_mode(self):
        """設置偵察模式"""
        # 檢查雲台控制器是否可用
        if not self.gimbal_controller or not self.gimbal_controller.connected:
            self.log("雲台控制器不可用，跳過偵察模式", step="002", level="warning")
            return False

        # 創建偵察模式控制器
        self.scout_mode = ScoutMode(self.gimbal_controller, self.scout_active_event)
        
        # 設置偵察參數
        self.scout_mode.set_scan_parameters(
            yaw_start=-60.0,    # Yaw範圍
            yaw_end=60.0,
            pitch_angle=-15.0,  # Pitch角度
            step_size=10.0,     # 步進大小
            step_delay=0.3      # 步進延遲
        )
        
        # 設置進度回調
        self.scout_mode.set_callback(self.on_scout_progress)
        
        return True
    
    def start_scout_scanning(self):
        """開始偵察掃描"""
        if not self.scout_mode:
            self.log("偵察模式未初始化", level="error")
            return False
            
        self.log("開始偵察模式", step="002")
        if self.scout_mode.start_scan():
            self.log("偵察模式已啟動", step="002")
            return True
        else:
            self.log("偵察模式啟動失敗", step="002", level="error")
            return False
    
    def wait_for_completion(self, timeout=50):
        """等待任務完成"""
        start_time = time.time()
        mqtt_check_interval = 2.0  # 每2秒檢查一次MQTT狀態
        last_mqtt_check = start_time
        
        while not self.completed and time.time() - start_time < timeout:
            # 定期檢查 MQTT 連接狀態和訊息統計
            current_time = time.time()
            if current_time - last_mqtt_check >= mqtt_check_interval:
                self.log(f"MQTT 狀態檢查 - 總訊息數: {self.mqtt_message_count}, "
                        f"最後 AI 訊息: {current_time - self.last_ai_message_time:.1f}s 前, "
                        f"最後 UAV 訊息: {current_time - self.last_uav_message_time:.1f}s 前", 
                        level="debug")
                last_mqtt_check = current_time
            
            time.sleep(0.5)
            
            # 檢查流程狀態
            if self.flow and hasattr(self.flow, 'flow_state') and self.flow.flow_state.value == "error":
                self.log("流程錯誤，停止偵察", step="002", level="warning")
                break
        
        # 超時處理
        if not self.completed:
            self.log("偵察模式超時", step="002", level="warning")
            self.completed = True
            if self.scout_mode:
                self.scout_mode.stop_scan()
            self.notify_complete("002_complete", 1)  # 1表示完成但未找到目標
    
    def cleanup(self):
        """清理所有資源"""
        try:
            # 停止偵察模式
            if self.scout_mode:
                self.scout_mode.stop_scan()
                self.scout_mode.center_gimbal()
            
            # 清理 MQTT 處理器
            self.cleanup_mqtt_handlers()
            
            # 輸出最終統計
            self.log(f"任務完成 - 總計處理 {self.mqtt_message_count} 條 MQTT 訊息", level="info")
            
        except Exception as e:
            self.log(f"清理資源時錯誤: {e}", level="error")
    
    def get_status(self):
        """獲取控制器狀態"""
        return {
            "completed": self.completed,
            "target_detected": self.target_detected,
            "mqtt_message_count": self.mqtt_message_count,
            "mqtt_handlers_active": len(self.mqtt_handlers),
            "scout_mode_running": self.scout_mode.is_running if self.scout_mode else False,
            "gimbal_connected": self.gimbal_controller.connected if self.gimbal_controller else False
        }


def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_002", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="002", level=level)
    
    log("step_002_scout_mode 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    logger.log("執行 step_002_scout_mode...", step="002", source="step_module")

    # 創建 Step002 控制器
    controller = Step002Controller(data_sources, config, logger, notify_complete)
    
    try:
        # 1. 設置 MQTT 處理器
        if not controller.setup_mqtt_handlers():
            log("MQTT 處理器設置失敗", level="error")
            notify_complete("002_complete", 99)
            return {"stop": controller.cleanup}
        
        # 2. 設置偵察模式
        if not controller.setup_scout_mode():
            log("偵察模式設置失敗", level="error") 
            notify_complete("002_complete", 1)  # 未找到目標
            return {"stop": controller.cleanup}
        
        # 3. 開始偵察掃描
        if not controller.start_scout_scanning():
            log("偵察掃描啟動失敗", level="error")
            notify_complete("002_complete", 99)
            return {"stop": controller.cleanup}
        
        # 4. 等待完成
        controller.wait_for_completion(timeout=50)
        
        # 5. 輸出最終狀態
        status = controller.get_status()
        log(f"Step002 執行完成 - 狀態: {status}")
        
    except Exception as e:
        log(f"Step002 執行錯誤: {e}", level="error")
        notify_complete("002_complete", 99)
    
    # 返回清理函數
    return {"stop": controller.cleanup}


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="002", level=level)
    
    log("執行 step_002 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬偵察模式過程"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬偵察模式過程", level="debug")
        log("DEBUG: 模擬雲台掃描動作", level="debug")
        
        # 模擬偵察過程
        scan_positions = [
            {"yaw": -60.0, "pitch": -15.0, "round": 1},
            {"yaw": -30.0, "pitch": -15.0, "round": 1}, 
            {"yaw": 0.0, "pitch": -15.0, "round": 1},
            {"yaw": 30.0, "pitch": -15.0, "round": 1},
            {"yaw": 60.0, "pitch": -15.0, "round": 1},
            {"yaw": 30.0, "pitch": -30.0, "round": 2},
            {"yaw": 0.0, "pitch": -30.0, "round": 2},
            {"yaw": -30.0, "pitch": -30.0, "round": 2},
            {"yaw": -60.0, "pitch": -30.0, "round": 2}
        ]
        
        for i, pos in enumerate(scan_positions):
            if stopped:
                log("DEBUG: 步驟已被停止，取消模擬偵察", level="debug")
                return
                
            log(f"DEBUG: 模擬掃描位置 Yaw={pos['yaw']:.1f}°, Pitch={pos['pitch']:.1f}° (回合 {pos['round']})", level="debug")
            time.sleep(0.5)  # 模擬掃描延遲
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 模擬偵察結果
        if not completed:
            log("DEBUG: 模擬偵察完成，未發現目標", level="debug")
            # 模擬結果 (0=找到目標, 1=完成但未找到目標, 99=錯誤)
            result = 0  # 預設模擬找到目標
            notify_complete("002_complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬偵察掃描計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬偵察掃描計時器", level="debug")
        
        log("DEBUG: 步驟002停止，資源已釋放")
    
    return {
        "stop": stop
    }