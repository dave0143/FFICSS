"""
流程控制器
提供步驟管理、錯誤處理、資源管理和步驟堆疊功能
支援debug模式
"""

import os
import json
import time
import importlib
import traceback
import threading
from typing import Dict, Any, Optional, Callable
from threading import Lock, Event
from enum import Enum

class FlowState(Enum):
    """流程狀態枚舉"""
    STANDBY = "standby"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    STOPPING = "stopping"

class StepStatus(Enum):
    """步驟狀態枚舉"""
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    STOPPING = "stopping"

class FlowStepController:
    """流程步驟控制器 - 支援debug模式"""
    
    def __init__(self, config: Dict[str, Any], logger, debug_mode=False): 
        # 基本配置
        self.step_file = "flow_step.json"
        self.config = config
        self.logger = logger
        self.debug_mode = debug_mode
        self.debug_config = config.get("debug_config", {})

        # 初始化 step 屬性
        self.step = "standby"  # 先設置默認值
        
        # 狀態管理
        self.flow_state = FlowState.STANDBY
        self.step_status = StepStatus.IDLE
        self.step_start_time = time.time()
        self.state_lock = Lock()
        self.stop_event = Event()
        
        # 載入步驟（重要：在其他初始化之前）
        loaded_step = self.load_step()
        
        # 強制重置到 standby 如果不是有效狀態
        if loaded_step not in ["standby", "000"]:
            # 使用 print 代替 log，因為此時 log 方法可能不可用
            print(f"[Flow] 系統初始化時步驟為 {loaded_step}，強制重置為 standby")
            self.step = "standby"
            self.save_step()
        else:
            self.step = loaded_step

        # 數據儲存
        self.last_topic = None
        self.last_payload = None
        self.gimbal_info = None
        self.uav_info = None
        self.scout_result = None
        self.return_data = {}  # 新增返回數據儲存
        self.mission_complete = False  # 新增任務完成標記
        
        # 服務和控制器引用
        self.device = None
        self.mqtt = None
        self.gimbal_controller = None
        
        # 當前步驟管理
        self.current_step_module = None
        self.current_step_stop_func = None
        self.current_step_data = {}
        
        # 步驟處理器映射
        self.step_handlers = self._initialize_step_handlers()
        self.step_modules = self._initialize_step_modules()
        
        # 錯誤狀態配置
        self.critical_statuses = ["HWErr", "EMO", "RC", "Battery_Low", "GPS_Lost"]
        
        # 模組快取
        self.loaded_modules = {}
        
        # 統計資訊
        self.step_execution_count = {}
        self.step_error_count = {}

        # 現在 log 方法已完全初始化，可以安全調用
        self.log(f"流程控制器初始化完成，當前步驟: {self.step}")
        if self.debug_mode:
            self.log("全域DEBUG模式已啟用", "debug")

    def _initialize_step_handlers(self) -> Dict[str, Callable]:
        """初始化步驟處理器映射"""
        return {
            "standby": self._handle_step_standby,
            "000": self._handle_step_000,
            "001": self._handle_step_001,
            "002": self._handle_step_002,
            "003": self._handle_step_003,
            "004": self._handle_step_004,
            "005": self._handle_step_005,
            "006": self._handle_step_006,
            "007": self._handle_step_007,
        }
    
    def _initialize_step_modules(self) -> Dict[str, str]:
        """初始化步驟模組映射"""
        return {
            "000": "step_000_standby_watch",
            "001": "step_001_mission_start",
            "002": "step_002_scout_mode",
            "003": "step_003_descent_search",
            "004": "step_004_target_lock",
            "005": "step_005_DeltaD",
            "006": "step_006_spray_strategy",
            "007": "step_007_return_and_reset"
        }
    
    # ==========================================================================
    # 公共接口方法
    # ==========================================================================
    def set_device_controller(self, device):
        """設置設備控制器"""
        self.device = device
        self.log("設備控制器已設置")

    def set_mqtt_service(self, mqtt_service):
        """設置MQTT服務"""
        self.mqtt = mqtt_service
        self.log("MQTT服務已設置")
    
    def set_gimbal_controller(self, gimbal_controller):
        """設置雲台控制器"""
        self.gimbal_controller = gimbal_controller
        self.log("雲台控制器已設置")
    
    def get_step(self) -> str:
        """獲取當前步驟"""
        return self.step
    
    def get_flow_state(self) -> FlowState:
        """獲取流程狀態"""
        return self.flow_state
    
    def get_step_status(self) -> StepStatus:
        """獲取步驟狀態"""
        return self.step_status
    
    def get_statistics(self) -> Dict[str, Any]:
        """獲取統計資訊"""
        return {
            "current_step": self.step,
            "flow_state": self.flow_state.value,
            "step_status": self.step_status.value,
            "step_execution_count": self.step_execution_count.copy(),
            "step_error_count": self.step_error_count.copy(),
            "loaded_modules": list(self.loaded_modules.keys()),
            "step_duration": time.time() - self.step_start_time,
        }
    
    # ==========================================================================
    # 步驟管理方法
    # ==========================================================================
    
    def load_step(self) -> str:
        """載入步驟配置"""
        try:
            if os.path.exists(self.step_file):
                with open(self.step_file, "r", encoding='utf-8') as f:
                    data = json.load(f)
                    step = data.get("step", "standby")
                    
                    # 安全檢查：確保步驟是有效的
                    valid_steps = ["standby", "000", "001", "002", "003", "004", "005", "006", "007"]
                    if step not in valid_steps:
                        if hasattr(self, 'logger') and self.logger:
                            self.log(f"無效的步驟 {step}，重置為 standby", "warning")
                        else:
                            print(f"[Flow] 無效的步驟 {step}，重置為 standby")
                        step = "standby"
                    
                    if hasattr(self, 'logger') and self.logger:
                        self.log(f"從檔案載入步驟: {step}")
                    else:
                        print(f"[Flow] 從檔案載入步驟: {step}")
                    return step
            else:
                if hasattr(self, 'logger') and self.logger:
                    self.log("步驟檔案不存在，使用預設步驟: standby")
                else:
                    print("[Flow] 步驟檔案不存在，使用預設步驟: standby")
                return "standby"
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.log(f"載入步驟檔案錯誤: {e}，使用預設步驟: standby", "error")
            else:
                print(f"[Flow] 載入步驟檔案錯誤: {e}，使用預設步驟: standby")
            return "standby"
    
    def save_step(self) -> bool:
        """儲存步驟配置"""
        try:
            step_data = {
                "step": self.step,
                "timestamp": time.time(),
                "flow_state": self.flow_state.value,
                "step_status": self.step_status.value,
            }
            
            with open(self.step_file, "w", encoding='utf-8') as f:
                json.dump(step_data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.log(f"儲存步驟檔案錯誤: {e}", "error")
            return False

    def reset_step_file(self, force_standby: bool = False):
        """重置步驟檔案到初始狀態"""
        try:
            # 刪除現有檔案
            if os.path.exists(self.step_file):
                os.remove(self.step_file)
                self.log(f"已刪除現有步驟檔案: {self.step_file}")
            
            # 創建新的步驟檔案
            initial_step_data = {
                "step": "standby",
                "timestamp": time.time(),
                "flow_state": "standby",
                "step_status": "idle"
            }
            
            with open(self.step_file, "w", encoding='utf-8') as f:
                json.dump(initial_step_data, f, indent=2, ensure_ascii=False)
            
            if force_standby:
                # 強制重置當前狀態
                self.step = "standby"
                self.flow_state = FlowState.STANDBY
                self.step_status = StepStatus.IDLE
                self.step_start_time = time.time()
                
                # 停止當前步驟
                self._stop_current_step()
            
            self.log("步驟檔案已重置到 standby 狀態")
            return True
            
        except Exception as e:
            self.log(f"重置步驟檔案時出錯: {e}", "error")
            return False

    def force_reset_to_standby(self):
        """強制重置流程到待機狀態"""
        with self.state_lock:
            self.log("執行強制重置到待機狀態", "warning")
            
            # 停止當前步驟
            self._stop_current_step()
            
            # 重置狀態
            self.step = "standby"
            self.flow_state = FlowState.STANDBY
            self.step_status = StepStatus.IDLE
            self.step_start_time = time.time()
            
            # 清理數據
            self.current_step_data = {}
            self.mission_complete = False
            
            # 重置步驟檔案
            self.reset_step_file(force_standby=True)
            
            # 清理模組快取
            self.loaded_modules.clear()
            
            self.log("強制重置完成，當前步驟: standby")

    def check_and_repair_step_state(self):
        """檢查並修復步驟狀態"""
        valid_steps = ["standby", "000", "001", "002", "003", "004", "005", "006", "007"]
        
        if self.step not in valid_steps:
            self.log(f"檢測到無效步驟 {self.step}，執行修復", "warning")
            self.force_reset_to_standby()
            return False
        
        # 檢查步驟檔案是否與當前狀態一致
        try:
            if os.path.exists(self.step_file):
                with open(self.step_file, "r", encoding='utf-8') as f:
                    data = json.load(f)
                    file_step = data.get("step", "standby")
                    
                    if file_step != self.step:
                        self.log(f"步驟檔案({file_step})與當前狀態({self.step})不一致，執行修復", "warning")
                        self.save_step()
        except Exception as e:
            self.log(f"檢查步驟檔案時出錯: {e}", "error")
        
        return True
    
    def _stop_current_step(self):
        """停止當前步驟"""
        self.log(f"開始停止當前步驟: {self.step}", level="info")
        
        if self.current_step_stop_func:
            try:
                self.log(f"====== 停止步驟 {self.step} 的任務... ======", level="info")
                self.step_status = StepStatus.STOPPING
                
                # 記錄停止函數信息
                self.log(f"當前停止函數: {self.current_step_stop_func}", level="debug")
                
                # 調用停止函數
                start_time = time.time()
                result = self.current_step_stop_func()
                end_time = time.time()
                
                self.log(f"停止函數執行完成，耗時: {end_time - start_time:.2f}秒，結果: {result}", level="info")
                
            except Exception as e:
                self.log(f"停止步驟任務時出錯: {e}", "error")
                self.log(f"停止步驟錯誤詳情: {traceback.format_exc()}", "error")
            finally:
                self.current_step_stop_func = None
                self.current_step_module = None
                self.log(f"====== 步驟 {self.step} 清理完成 ======", level="info")
        else:
            self.log(f"步驟 {self.step} 沒有停止函數，直接清理", level="info")

    def set_step(self, new_step: str, force: bool = False) -> bool:
        """設置新步驟"""
        self.log(f"[DEBUG] 準備設置新步驟: {new_step}, 當前步驟: {self.step}, 強制: {force}")
        
        try:
            # 使用非阻塞方式獲取鎖
            lock_acquired = self.state_lock.acquire(blocking=False)
            if not lock_acquired:
                self.log(f"[WARNING] 無法獲取狀態鎖，延遲執行步驟切換: {new_step}", "warning")
                
                # 使用定時器延遲執行
                def delayed_set_step():
                    time.sleep(0.5)  # 等待500ms
                    self.log(f"[DEBUG] 重試設置步驟: {new_step}")
                    return self.set_step(new_step, force)
                
                timer_thread = threading.Thread(target=delayed_set_step, daemon=True)
                timer_thread.start()
                return True
            
            try:
                self.log(f"[DEBUG] 已獲取狀態鎖")
                
                if self.step == new_step and not force:
                    self.log(f"步驟相同且非強制，跳過切換: {new_step}", level="debug")
                    return True
                
                try:
                    self.log(f"開始步驟切換程序: {self.step} -> {new_step}", level="debug")
                    
                    # 停止當前步驟
                    self._stop_current_step()
                    
                    # 記錄步驟切換
                    old_step = self.step
                    self.step = new_step
                    self.step_start_time = time.time()
                    self.step_status = StepStatus.IDLE
                    self.current_step_data = {}
                    self.mission_complete = False  # 重置任務完成標記
                    
                    self.log(f"步驟狀態已更新: {old_step} -> {new_step}", level="debug")
                    
                    # 儲存步驟
                    if not self.save_step():
                        self.log("儲存步驟失敗，但繼續執行", "warning")
                    else:
                        self.log("步驟已儲存到檔案", level="debug")
                    
                    # 更新統計
                    self.step_execution_count[new_step] = self.step_execution_count.get(new_step, 0) + 1
                    
                    self.log(f"步驟切換完成: {old_step} -> {new_step}")
                    
                    # 如果切換到待機模式，重置流程狀態
                    if new_step == "standby":
                        self.flow_state = FlowState.STANDBY
                        self.log("流程狀態重置為 STANDBY", level="debug")
                    else:
                        self.flow_state = FlowState.RUNNING
                        self.log("流程狀態設置為 RUNNING", level="debug")
                    
                    return True
                    
                except Exception as e:
                    self.log(f"設置步驟錯誤: {e}", "error")
                    self.log(f"設置步驟錯誤詳情: {traceback.format_exc()}", "error")
                    return False
            
            finally:
                self.state_lock.release()
                self.log(f"[DEBUG] 已釋放狀態鎖")
                
        except Exception as e:
            self.log(f"[ERROR] set_step 發生未捕獲的異常: {e}", "error")
            self.log(f"[ERROR] 異常詳情: {traceback.format_exc()}", "error")
            return False
    
    # ==========================================================================
    # 超時和狀態檢查
    # ==========================================================================
    def check_timeout(self) -> bool:
        """檢查步驟超時"""
        try:
            # 當步驟為standby時不檢查超時
            if self.step == "standby":
                return False
            
            # 如果正在停止，不檢查超時
            if self.step_status == StepStatus.STOPPING:
                return False
            
            # 如果已經完成，不檢查超時
            if self.step_status == StepStatus.COMPLETED:
                return False
                
            timeout_config = self.config.get("flow_timeout", {})
            step_key = self.step if len(self.step) == 3 else self.step.zfill(3)
            timeout = timeout_config.get(step_key, 30)
            
            # 如果超時設置為0或負數，跳過檢查
            if timeout <= 0:
                return False
                
            elapsed = time.time() - self.step_start_time
            
            if elapsed > timeout:
                self.log(f"步驟 {self.step} 超時! 已執行 {elapsed:.1f}s, 限制 {timeout}s", "warning")
                
                # 超時時嘗試強制停止當前步驟
                self.log(f"嘗試強制停止超時的步驟 {self.step}", "warning")
                self._stop_current_step()
                
                self.step_status = StepStatus.TIMEOUT
                # 超時後切換到待機狀態
                self.set_step("007")
                return True
                
            return False
        except Exception as e:
            self.log(f"檢查超時錯誤: {e}", "error")
            return False
    
    def check_uav_status(self) -> bool:
        """檢查無人機狀態"""
        try:
            if not self.uav_info or "status" not in self.uav_info:
                return True
            
            status = self.uav_info["status"]
            Lidar_status = self.uav_info["mission_status"] #Lidar WP_V2_STATUS_OBSTACLE_DETECTED

            if status in self.critical_statuses or Lidar_status == 9:
                self.log(f"檢測到無人機關鍵狀態: {status}, 進入待機模式", "error")
                self.flow_state = FlowState.ERROR
                self.set_step("standby")
                return False
            
            return True
        except Exception as e:
            self.log(f"檢查無人機狀態錯誤: {e}", "error")
            return True
    
    # ==========================================================================
    # 數據處理方法
    # ==========================================================================
    
    def on_tcp_data(self, info: Dict[str, Any]):
        """處理TCP數據（雲台資訊）"""
        try:
            self.gimbal_info = info
            # self.log(f"收到雲台資訊: {info}")
            
            # 傳遞給當前步驟模組
            if self.current_step_module and hasattr(self.current_step_module, 'on_gimbal_data'):
                self.current_step_module.on_gimbal_data(info)
            
            # 特殊步驟處理
            if self.step == "004" and self._check_target_lock_condition(info):
                self.set_step("005")
                
        except Exception as e:
            self.log(f"處理TCP數據錯誤: {e}", "error")
    
    def handle_mqtt_data(self, topic: str, payload: Dict[str, Any]):
        """處理MQTT數據"""
        try:
            self.last_topic = topic
            self.last_payload = payload
            self.log(f"收到MQTT資料: topic={topic}, payload={payload}")
            
            # 檢查無人機狀態
            if not self.check_uav_status():
                return
            
            # 更新UAV資訊
            if topic == "uav/info":
                self.uav_info = payload
            
            # 傳遞給當前步驟模組
            if self.current_step_module and hasattr(self.current_step_module, 'on_mqtt_data'):
                self.current_step_module.on_mqtt_data(topic, payload)
            
            # 只在步驟狀態為 IDLE 時執行步驟（避免重複執行）
            if self.step_status == StepStatus.IDLE:
                self.execute_current_step()
            
        except Exception as e:
            self.log(f"處理MQTT資料錯誤: {e}", "error")
    
    def _check_target_lock_condition(self, info: Dict[str, Any]) -> bool:
        """檢查目標鎖定條件"""
        try:
            return (info.get("success") and 
                   info.get("range_finding", {}).get("success") and 
                   1 < info.get("range_finding", {}).get("distance", 0) < 300)
        except Exception:
            return False
    
    # ==========================================================================
    # 步驟執行方法
    # ==========================================================================
    def execute_current_step(self):
        """執行當前步驟的處理邏輯"""
        try:
            if self.flow_state == FlowState.PAUSED:
                return
            
            # 如果步驟已經在運行中，不重複執行
            if self.step_status == StepStatus.RUNNING:
                self.log(f"步驟 {self.step} 已在運行中，跳過重複執行", level="debug")
                return
                
            # 如果步驟已經完成，不重複執行
            if self.step_status == StepStatus.COMPLETED:
                self.log(f"步驟 {self.step} 已完成，跳過重複執行", level="debug")
                return
                
            handler = self.step_handlers.get(self.step)
            if handler:
                self.log(f"執行步驟處理器: {self.step}", level="debug")
                handler()
            else:
                self.log(f"未找到步驟 {self.step} 的處理器", "error")
                self.step_status = StepStatus.FAILED
                
        except Exception as e:
            self.log(f"執行步驟 {self.step} 錯誤: {e}", "error")
            self.log(f"錯誤詳情: {traceback.format_exc()}", "error")
            
            # 更新錯誤統計
            self.step_error_count[self.step] = self.step_error_count.get(self.step, 0) + 1
            self.step_status = StepStatus.FAILED
            
            # 如果錯誤次數過多，進入待機模式
            if self.step_error_count[self.step] >= 3:
                self.log(f"步驟 {self.step} 錯誤次數過多，進入待機模式", "error")
                self.set_step("standby")
    
    def load_step_module(self, step_key: str):
        """載入步驟模組"""
        try:
            if step_key in self.loaded_modules:
                return self.loaded_modules[step_key]
            
            module_name = self.step_modules.get(step_key)
            if not module_name:
                raise ValueError(f"未找到步驟 {step_key} 的模組映射")
            
            # 動態載入模組
            full_module_name = f"mission_steps.{module_name}"
            module = importlib.import_module(full_module_name)
            
            # 重新載入模組以獲取最新版本
            importlib.reload(module)
            
            # 快取模組
            self.loaded_modules[step_key] = module
            self.log(f"成功載入模組: {full_module_name}")
            
            return module
            
        except Exception as e:
            self.log(f"載入步驟模組 {step_key} 錯誤: {e}", "error")
            return None
    
    def run_step_module(self, step_key: str) -> Optional[Dict[str, Any]]:
        """執行步驟模組 - 支援debug模式"""
        try:
            # 載入模組
            module = self.load_step_module(step_key)
            if not module:
                return None
            
            # 準備數據源
            data_sources = self._prepare_data_sources()
            
            # 準備完成回調
            notify_complete = self._create_notify_complete_callback()
            
            # 更新狀態
            self.step_status = StepStatus.RUNNING
            self.current_step_module = module
            
            # 檢查是否強制啟用DEBUG模式
            force_debug = self.config.get("debug_config", {}).get(f"step_{step_key}", False)
            
            # DEBUG模式邏輯
            if force_debug and hasattr(module, 'debug_test'):
                self.log(f"步驟 {step_key} 強制啟用DEBUG模式", level="debug")
                
                # 在背景執行緒中執行debug測試
                def debug_wrapper():
                    try:
                        # 調用模組的debug_test方法
                        result = module.debug_test(data_sources, self.config, self.logger, notify_complete)
                        
                        # 處理debug結果
                        if result and callable(result.get("stop")):
                            self.current_step_stop_func = result["stop"]
                        
                        self.log(f"步驟 {step_key} debug測試完成", level="debug")
                    except Exception as e:
                        self.log(f"步驟 {step_key} debug測試錯誤: {e}", "error")
                        # debug模式錯誤時，通知失敗
                        try:
                            notify_complete(f"{step_key}_completed", 99)
                        except:
                            pass
                
                debug_thread = threading.Thread(target=debug_wrapper, daemon=True)
                debug_thread.start()
                return {"debug_mode": True, "step": step_key}

            # 正常模式執行
            elif hasattr(module, 'run_step'):
                result = module.run_step(data_sources, self.config, self.logger, notify_complete)
                
                # 處理執行結果
                if result:
                    if callable(result.get("stop")):
                        self.current_step_stop_func = result["stop"]
                    
                    # 儲存步驟數據
                    if "data" in result:
                        self.current_step_data.update(result["data"])
                
                return result
            else:
                self.log(f"步驟模組 {step_key} 缺少 run_step 函數", "error")
                return None
            
        except Exception as e:
            self.log(f"執行步驟模組 {step_key} 錯誤: {e}", "error")
            self.log(f"錯誤詳情: {traceback.format_exc()}", "error")
            self.step_status = StepStatus.FAILED
            return None
    
    def _prepare_data_sources(self) -> Dict[str, Any]:
        """準備數據源"""
        return {
            "last_topic": self.last_topic,
            "last_payload": self.last_payload,
            "gimbal_info": self.gimbal_info,
            "uav_info": self.uav_info,
            "scout_result": self.scout_result,
            "mqtt_service": self.mqtt,
            "gimbal_controller": self.gimbal_controller,
            "device_controller": self.device,
            "current_step_data": self.current_step_data.copy(),
            "flow_controller": self,  # 提供流程控制器引用
            "return_data": self.return_data.copy(),  # 新增返回數據
            "mission_complete": self.mission_complete  # 新增任務完成標記
        }
    
    def _create_notify_complete_callback(self) -> Callable:
        """創建完成通知回調"""
        def notify_complete(next_step: str, result: Any = None):
            try:
                self.log(f"收到完成通知: {next_step}, 結果: {result}")
                
                # 更新步驟狀態為完成
                self.step_status = StepStatus.COMPLETED
                next_step = next_step.lower()

                if next_step == "000_complete":
                    self.log("處理000完成通知，切換到001", level="info")
                    self.set_step("001")
                    
                elif next_step == "001_complete":
                    self.log("處理001完成通知，切換到002", level="info")
                    self.set_step("002")

                elif next_step == "002_complete":
                    self.log(f"處理002完成通知，結果: {result}", level="info")
                    if result == 0:  # 搜索完成
                        self.log("處理002完成通知，切換到004", level="info")
                        self.set_step("004")
                    elif result == 1:  # 搜索失敗
                        self.log("002搜索失敗，切換到003", level="info")
                        self.set_step("003")    
                    elif result == 99:  # 緊急停止
                        self.log("002緊急停止，切換到standby", level="info")
                        self.set_step("standby")
                    else:
                        self.log(f"步驟002未知結果: {result}，切換到standby", "warning")
                        self.set_step("standby")

                elif next_step == "003_complete":
                    self.log(f"處理003完成通知，結果: {result}", level="info")
                    if result == 0:  # 下降完成
                        self.log("003下降完成，重新搜索，切換到002", level="info")
                        self.set_step("002")  # 重新開始搜索
                    elif result == 99:  # 緊急停止
                        self.log("003緊急停止，切換到standby", level="info")
                        self.set_step("standby")
                    elif result == 1:  # 高度過低
                        self.log("003高度過低，切換到007返航", level="info")
                        self.set_step("007")  # 直接進入返航
                    else:
                        self.log(f"步驟003未知結果: {result}，切換到standby", "warning")
                        self.set_step("standby")
                
                elif next_step == "004_complete":
                    # 目標鎖定完成後轉到DeltaD步驟
                    if result == 99:  # 錯誤狀態
                        self.set_step("standby")
                    else:
                        self.set_step("005")
                
                elif next_step == "005_complete":
                    # DeltaD完成後轉到噴灑策略
                    if result == 99:  # 錯誤狀態
                        self.set_step("standby")
                    else:
                        self.set_step("006")
                    
                elif next_step == "006_complete":
                    # 噴灑策略完成後轉到返航
                    if result == 99:  # 錯誤狀態
                        self.set_step("standby")
                    else:
                        self.set_step("007")
                    
                elif next_step == "007_complete":
                    # 返航完成後回到待機
                    self.set_step("standby")
                
                else:
                    self.log(f"未知的完成通知: {next_step}", "warning")
                    
            except Exception as e:
                self.log(f"處理完成通知錯誤: {e}", "error")
                self.log(f"錯誤詳情: {traceback.format_exc()}", "error")
        
        return notify_complete
    
    # ==========================================================================
    # 步驟處理器方法
    # ==========================================================================
    def _handle_step_standby(self):
        """處理待機模式（步驟000）"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟000模組", level="debug")
            self.run_step_module("000")

    def _handle_step_000(self):
        """處理步驟000"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟000模組", level="debug")
            self.run_step_module("000")

    def _handle_step_001(self):
        """處理步驟001"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟001模組", level="debug")
            self.run_step_module("001")

    def _handle_step_002(self):
        """處理步驟002"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟002模組", level="debug")
            self.run_step_module("002")

    def _handle_step_003(self):
        """處理步驟003"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟003模組", level="debug")
            self.run_step_module("003")

    def _handle_step_004(self):
        """處理步驟004"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟004模組", level="debug")
            self.run_step_module("004")

    def _handle_step_005(self):
        """處理步驟005"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟005模組", level="debug")
            self.run_step_module("005")

    def _handle_step_006(self):
        """處理步驟006"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟006模組", level="debug")
            self.run_step_module("006")

    def _handle_step_007(self):
        """處理步驟007"""
        if self.step_status == StepStatus.IDLE:
            self.log("啟動步驟007模組", level="debug")
            self.run_step_module("007")
    
    # ==========================================================================
    # 控制方法
    # ==========================================================================
    
    def pause_flow(self):
        """暫停流程"""
        with self.state_lock:
            if self.flow_state == FlowState.RUNNING:
                self.flow_state = FlowState.PAUSED
                self.log("流程已暫停")
    
    def resume_flow(self):
        """恢復流程"""
        with self.state_lock:
            if self.flow_state == FlowState.PAUSED:
                self.flow_state = FlowState.RUNNING
                self.log("流程已恢復")
    
    def emergency_stop(self):
        """緊急停止"""
        with self.state_lock:
            self.log("執行緊急停止", "warning")
            self.flow_state = FlowState.ERROR
            
            # 停止當前步驟
            self._stop_current_step()
            
            # 停止設備
            if self.device:
                try:
                    self.device.stop()
                except Exception as e:
                    self.log(f"停止設備時出錯: {e}", "error")
            
            # 強制重置到待機狀態
            self.force_reset_to_standby()
    
    def reset_statistics(self):
        """重置統計資訊"""
        self.step_execution_count.clear()
        self.step_error_count.clear()
        self.log("統計資訊已重置")

    def get_debug_info(self) -> Dict[str, Any]:
        """獲取調試資訊"""
        return {
            "current_step": self.step,
            "flow_state": self.flow_state.value,
            "step_status": self.step_status.value,
            "step_start_time": self.step_start_time,
            "step_duration": time.time() - self.step_start_time,
            "has_stop_func": self.current_step_stop_func is not None,
            "has_module": self.current_step_module is not None,
            "loaded_modules": list(self.loaded_modules.keys()),
            "step_file_exists": os.path.exists(self.step_file),
            "uav_info_available": self.uav_info is not None,
            "last_topic": self.last_topic,
            "debug_mode": self.debug_mode,
            "debug_config": self.debug_config.copy()
        }

    def print_debug_info(self):
        """打印調試資訊"""
        debug_info = self.get_debug_info()
        self.log("=== 調試資訊 ===")
        for key, value in debug_info.items():
            self.log(f"{key}: {value}")
        self.log("=== 調試資訊結束 ===")

    # 添加公共方法供外部調用
    def manual_reset(self):
        """手動重置（可供外部調用）"""
        self.log("執行手動重置", "info")
        self.force_reset_to_standby()

    def repair_if_needed(self):
        """如果需要則修復（可供外部調用）"""
        return self.check_and_repair_step_state()
    
    # ==========================================================================
    # Debug相關方法
    # ==========================================================================
    
    def is_debug_mode_enabled(self, step_key: str = None) -> bool:
        """檢查是否啟用debug模式"""
        if step_key is None:
            step_key = self.step
        
        if step_key == "standby":
            step_key = "000"
        
        return self.debug_config.get(f"step_{step_key}", False)
    
    def get_all_debug_status(self) -> Dict[str, Any]:
        """獲取所有步驟的debug狀態"""
        return {
            "global_debug": self.debug_mode,
            "step_debug": {
                step: self.debug_config.get(f"step_{step}", False)
                for step in ["000", "001", "002", "003", "004", "005", "006", "007"]
            }
        }
    
    def update_debug_config(self, step: str, enabled: bool) -> bool:
        """動態更新debug配置"""
        try:
            self.debug_config[f"step_{step}"] = enabled
            self.log(f"步驟 {step} debug模式已{'啟用' if enabled else '停用'}")
            return True
        except Exception as e:
            self.log(f"更新debug配置失敗: {e}", "error")
            return False
    
    def toggle_debug_mode(self, step: str = None) -> bool:
        """切換debug模式"""
        if step:
            current = self.debug_config.get(f"step_{step}", False)
            return self.update_debug_config(step, not current)
        else:
            self.debug_mode = not self.debug_mode
            self.log(f"全域debug模式已{'啟用' if self.debug_mode else '停用'}")
            return True
    
    # ==========================================================================
    # 工具方法
    # ==========================================================================
    
    def log(self, msg: str, level: str = "info", step: str = None, source: str = None):
        """記錄日誌"""
        if self.logger:
            step = step if step else self.step
            self.logger.log(f"[Flow] {msg}", step=step, source=source, level=level)
        else:
            print(f"[Flow] {msg}")
    
    def cleanup(self):
        """清理資源"""
        try:
            self.log("開始清理流程控制器資源")
            
            # 停止當前步驟
            self._stop_current_step()
            
            # 設置停止事件
            self.stop_event.set()
            
            # 清理模組快取
            self.loaded_modules.clear()
            
            # 重置狀態
            self.flow_state = FlowState.STANDBY
            self.step_status = StepStatus.IDLE
            
            self.log("流程控制器資源清理完成")
            
        except Exception as e:
            self.log(f"清理資源時出錯: {e}", "error")


# ==========================================================================
# 工具函數和輔助方法
# ==========================================================================

def create_flow_controller(config: Dict[str, Any], logger, debug_mode: bool = False) -> FlowStepController:
    """創建流程控制器實例的工廠函數"""
    try:
        controller = FlowStepController(config, logger, debug_mode)
        logger.log("流程控制器創建成功", step="factory", level="info")
        return controller
    except Exception as e:
        logger.log(f"創建流程控制器失敗: {e}", step="factory", level="error")
        raise

def validate_step_config(config: Dict[str, Any]) -> bool:
    """驗證步驟配置的有效性"""
    required_sections = ["mqtt", "device", "log", "flow_timeout"]
    
    for section in required_sections:
        if section not in config:
            print(f"[Flow] 警告: 配置中缺少必要區段 '{section}'")
            return False
    
    # 驗證步驟超時配置
    timeout_config = config.get("flow_timeout", {})
    required_steps = ["000", "001", "002", "003", "004", "005", "006", "007"]
    
    for step in required_steps:
        if step not in timeout_config:
            print(f"[Flow] 警告: 缺少步驟 {step} 的超時配置")
    
    # 驗證debug配置
    debug_config = config.get("debug_config", {})
    for step in required_steps:
        debug_key = f"step_{step}"
        if debug_key not in debug_config:
            # 設置默認值
            debug_config[debug_key] = False
    
    return True

def get_step_description(step: str) -> str:
    """獲取步驟描述"""
    descriptions = {
        "standby": "待機模式 - 等待任務開始",
        "000": "待機監控 - 監控系統狀態並等待啟動信號",
        "001": "任務開始 - 初始化任務並準備執行",
        "002": "斥候模式 - 雲台掃描搜索目標",
        "003": "下降搜索 - 降低高度重新搜索目標",
        "004": "目標鎖定 - 精確鎖定目標並計算路徑",
        "005": "位置調整 - DeltaD計算與位置微調",
        "006": "噴灑策略 - 執行噴灑作業",
        "007": "返航重置 - 任務完成返航並重置系統"
    }
    return descriptions.get(step, f"未知步驟: {step}")

def format_step_status_report(controller: FlowStepController) -> str:
    """格式化步驟狀態報告"""
    report_lines = [
        "=" * 50,
        "流程狀態報告",
        "=" * 50,
        f"當前步驟: {controller.step} ({get_step_description(controller.step)})",
        f"流程狀態: {controller.flow_state.value}",
        f"步驟狀態: {controller.step_status.value}",
        f"執行時間: {time.time() - controller.step_start_time:.1f}秒",
        f"全域Debug模式: {'啟用' if controller.debug_mode else '停用'}",
        ""
    ]
    
    # 添加統計資訊
    stats = controller.get_statistics()
    report_lines.extend([
        "執行統計:",
        f"  已載入模組: {len(stats['loaded_modules'])}",
        f"  總執行次數: {sum(controller.step_execution_count.values())}",
        f"  總錯誤次數: {sum(controller.step_error_count.values())}",
        ""
    ])
    
    # 添加各步驟狀態
    report_lines.append("各步驟狀態:")
    for step in ["000", "001", "002", "003", "004", "005", "006", "007"]:
        exec_count = controller.step_execution_count.get(step, 0)
        error_count = controller.step_error_count.get(step, 0)
        debug_enabled = controller.debug_config.get(f"step_{step}", False)
        debug_status = " [DEBUG]" if debug_enabled else ""
        report_lines.append(f"  {step}: {exec_count}次執行, {error_count}次錯誤{debug_status}")
    
    report_lines.extend([
        "",
        "=" * 50
    ])
    
    return "\n".join(report_lines)

# ==========================================================================
# 異常類別定義
# ==========================================================================

class FlowStepError(Exception):
    """流程步驟基礎異常"""
    pass

class StepTransitionError(FlowStepError):
    """步驟轉換異常"""
    def __init__(self, from_step: str, to_step: str, reason: str = ""):
        self.from_step = from_step
        self.to_step = to_step
        self.reason = reason
        super().__init__(f"無效的步驟轉換: {from_step} -> {to_step}. {reason}")

class StepTimeoutError(FlowStepError):
    """步驟超時異常"""
    def __init__(self, step: str, timeout: int, elapsed: float):
        self.step = step
        self.timeout = timeout
        self.elapsed = elapsed
        super().__init__(f"步驟 {step} 超時: {elapsed:.1f}s > {timeout}s")

class StepModuleError(FlowStepError):
    """步驟模組異常"""
    def __init__(self, step: str, module_name: str, error: str):
        self.step = step
        self.module_name = module_name
        self.error = error
        super().__init__(f"步驟 {step} 模組 {module_name} 錯誤: {error}")

# ==========================================================================
# 常數定義
# ==========================================================================

# 步驟常數
VALID_STEPS = ["standby", "000", "001", "002", "003", "004", "005", "006", "007"]
CRITICAL_STEPS = ["002", "004", "006"]
DEFAULT_TIMEOUT = 30

# 狀態常數
DEFAULT_FLOW_STATE = FlowState.STANDBY
DEFAULT_STEP_STATUS = StepStatus.IDLE

# 配置常數
DEFAULT_CONFIG_FILE = "config.json"
DEFAULT_STEP_FILE = "flow_step.json"
DEFAULT_LOG_LEVEL = "info"

# Debug常數
DEBUG_PREFIX = "[DEBUG]"
DEBUG_SLEEP_TIME = 2  # debug模式下每個步驟的模擬時間


# ==========================================================================
# 主程式入口（如果直接執行此檔案）
# ==========================================================================

if __name__ == "__main__":
    import sys
    import argparse
    
    def main():
        """主函數 - 用於測試和調試"""
        parser = argparse.ArgumentParser(description='Flow Step Controller 測試工具')
        parser.add_argument('--config', default='config.json', help='配置檔案路徑')
        parser.add_argument('--debug', action='store_true', help='啟用全域debug模式')
        parser.add_argument('--step', help='設置初始步驟')
        parser.add_argument('--validate', action='store_true', help='驗證配置檔案')
        parser.add_argument('--report', action='store_true', help='生成狀態報告')
        
        args = parser.parse_args()
        
        # 載入配置
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            print(f"無法載入配置檔案: {e}")
            return 1
        
        # 驗證配置
        if args.validate:
            if validate_step_config(config):
                print("配置檔案驗證通過")
                return 0
            else:
                print("配置檔案驗證失敗")
                return 1
        
        # 創建簡單的日誌器
        class SimpleLogger:
            def log(self, msg, step=None, source=None, level="info"):
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{timestamp}] [{level.upper()}] {msg}")
        
        logger = SimpleLogger()
        
        # 創建流程控制器
        try:
            controller = create_flow_controller(config, logger, args.debug)
        except Exception as e:
            print(f"創建流程控制器失敗: {e}")
            return 1
        
        # 設置初始步驟
        if args.step:
            if args.step in VALID_STEPS:
                controller.set_step(args.step, force=True)
                print(f"已設置步驟為: {args.step}")
            else:
                print(f"無效的步驟: {args.step}")
                print(f"有效步驟: {', '.join(VALID_STEPS)}")
                return 1
        
        # 生成報告
        if args.report:
            report = format_step_status_report(controller)
            print(report)
        
        # 如果沒有其他操作，顯示基本資訊
        if not any([args.report, args.step]):
            print(f"Flow Step Controller")
            print(f"當前步驟: {controller.step}")
            print(f"流程狀態: {controller.flow_state.value}")
            print(f"全域Debug模式: {'啟用' if controller.debug_mode else '停用'}")
            
            # 顯示各步驟debug狀態
            debug_status = controller.get_all_debug_status()
            print("各步驟Debug狀態:")
            for step, enabled in debug_status["step_debug"].items():
                status = "啟用" if enabled else "停用"
                print(f"  步驟{step}: {status}")
        
        return 0
    
    # 執行主函數
    sys.exit(main())