"""
任務結束、自動返航
支援debug模式
"""
import time
import threading
import json

class ReturnController:
    def __init__(self, config, logger, mqtt_service, notify_complete, flow_controller=None):
        self.config = config
        self.logger = logger
        self.mqtt_service = mqtt_service
        self.notify_complete = notify_complete
        self.flow_controller = flow_controller
        self.return_complete = False
        self.command_sent = False
        self.start_time = time.time()
        self.uav_info = None
        self.stopped = False
        
        # 註冊 MQTT 處理器
        self.mqtt_service.add_handler("uav/info", self.on_mqtt_message)
        
    def log(self, msg, level="info"):
        self.logger.log(f"[Return] {msg}", step="007", level=level)
        
    def on_mqtt_message(self, topic, payload):
        """處理來自 MQTT 的 uav_info 訊息"""
        if self.stopped or self.return_complete:
            return
            
        try:
            self.uav_info = payload
            self.log(f"收到UAV資訊更新", level="debug")
            
            # 處理返航邏輯
            self.handle_return_logic()
            
        except Exception as e:
            self.log(f"處理 MQTT 訊息時錯誤: {e}", level="error")
    
    def handle_return_logic(self):
        """處理返航邏輯"""
        if self.return_complete or not self.uav_info:
            return
        
        # 首次執行時發送返航指令
        if not self.command_sent:
            self.send_return_command()
        
        # 檢查返航狀態
        self.check_return_status()
    
    def send_return_command(self):
        """發送返航指令"""
        try:
            # 構建返航指令
            rtl_command = {
                "timestamp": time.time(),
                "task": "auto_RTL"
            }
            
            # # ============ 顯示 Command 內容 ============
            # self.log("=" * 50)
            # self.log("準備發送返航指令:")
            # self.log("=" * 50)
            
            # # 顯示指令的基本資訊
            # self.log(f"Command: {rtl_command['command']}")
            # self.log(f"Timestamp: {rtl_command['timestamp']}")
            
            # # 顯示原始JSON格式
            # self.log("--- 返航指令 JSON 格式 ---")
            # try:
            #     command_json = json.dumps(rtl_command, indent=2, ensure_ascii=False)
            #     for line in command_json.split('\n'):
            #         self.log(f"  {line}")
            # except Exception as e:
            #     self.log(f"無法序列化指令為 JSON: {e}", "error")
            
            # # 顯示當前UAV狀態
            # if self.uav_info:
            #     navigation = self.uav_info.get("navigation", {})
            #     global_pos = navigation.get("global_position", {})
                
            #     self.log("--- 當前UAV狀態 ---")
            #     self.log(f"  當前位置: lat={global_pos.get('lat', 0):.8f}, lon={global_pos.get('lon', 0):.8f}, alt={global_pos.get('alt', 0):.2f}m")
            #     self.log(f"  飛行模式: {navigation.get('flight_mode', 'Unknown')}")
            #     self.log(f"  UAV狀態: {self.uav_info.get('status', 'Unknown')}")
                
            #     # 顯示電池狀態
            #     power = self.uav_info.get("power", {})
            #     battery = power.get("battery", {})
            #     self.log(f"  電池電量: {battery.get('percentage', 0)}%")
            #     self.log(f"  電池電壓: {battery.get('voltage', 0)}V")
            
            # self.log("=" * 50)
            
            # 發送返航指令
            self.mqtt_service.publish("ai/target", rtl_command)
            self.log("✅ 返航指令已發送至 ai/target")
            self.command_sent = True
            
        except Exception as e:
            self.log(f"發送返航指令失敗: {e}", "error")
    
    def check_return_status(self):
        """檢查返航狀態"""
        if not self.uav_info:
            return
        
        navigation = self.uav_info.get("navigation", {})
        status = self.uav_info.get("status", "").lower()
        flight_mode = navigation.get("flight_mode", "").lower()
        errors = self.uav_info.get("errors", [])
        
        self.log(f"UAV狀態檢查: flight_mode={flight_mode}, status={status}", level="debug")
        
        # 檢查是否完成返航任務
        # 當flight_mode為auto_rtl且status為complete時，表示返航完成
        if flight_mode == "auto_rtl" and status == "complete":
            self.log("UAV 完成返航")
            self.return_complete = True
            self.notify_complete("007_complete", 0)  # 成功完成
            return
        
        # 檢查錯誤狀態 - 修復：添加空值檢查
        if self.flow_controller and hasattr(self.flow_controller, 'get_flow_state'):
            try:
                if self.flow_controller.get_flow_state().value == "ERROR":  # 修復：使用.value獲取枚舉值
                    self.log("流程處於錯誤狀態，終止返航", "error")

                    if not self.stopped:
                        # 移除 MQTT 處理器
                        self.mqtt_service.remove_handler("uav/info", self.on_mqtt_message)
                        self.stopped = True
                        self.notify_complete("007_complete", 99)  # 錯誤狀態
            except Exception as e:
                self.log(f"檢查流程狀態時錯誤: {e}", level="error")
    
    def stop(self):
        """停止控制器"""
        self.stopped = True
        self.return_complete = True
        # 移除 MQTT 處理器
        try:
            self.mqtt_service.remove_handler("uav/info", self.on_mqtt_message)
            self.log("返航控制器已停止，MQTT處理器已移除")
        except Exception as e:
            self.log(f"移除MQTT處理器時錯誤: {e}", level="error")


def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_007", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="007", level=level)
    
    log("step_007_return_and_reset 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    mqtt_service = data_sources.get("mqtt_service")
    flow_controller = data_sources.get("flow_controller")
    
    if not mqtt_service:
        log("MQTT服務未可用", level="error")
        return None
    
    logger.log("執行 step_007_return_home...", step="007", source="step_module")
    
    # 創建返航控制器
    controller = ReturnController(
        config, 
        logger, 
        mqtt_service, 
        notify_complete,
        flow_controller=flow_controller,
    )
    log("創建返航控制器，開始監聽 uav/info")
    
    # 返回停止函數
    def stop():
        if controller:
            controller.stop()
            log("步驟007停止")
    
    return {
        "stop": stop,
        "data": {"controller": controller}
    }


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="007", level=level)
    
    log("執行 step_007 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬返航和重置過程"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬返航和重置過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 007", level="debug")
            # 模擬結果 (0=返航完成, 99=錯誤)
            result = 0  # 預設模擬返航完成
            notify_complete("007_complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬返航重置計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬返航重置計時器", level="debug")
        
        log("DEBUG: 步驟007停止，資源已釋放")
    
    return {
        "stop": stop
    }