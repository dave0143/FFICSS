import time
import threading

def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_001", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="001", level=level)
    
    log("step_001_mission_start 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    mqtt = data_sources.get("mqtt_service")
    flow = data_sources.get("flow_controller")
    completed = False
    stopped = False

    def on_message(topic, payload):
        nonlocal completed
        if completed:
            return
            
        try:
            status = payload.get("status", "").lower()
            navigation = payload.get("navigation", {})
            task = navigation.get("flight_mode", "").lower()

            log(f"step_001收到MQTT資料: status={status}, task={task}", level="debug")

            if flow and flow.flow_state.value == "error":  # 注意這裡要用.value
                log("流程處於錯誤狀態，自動通報完成並終止", level="warn")
                notify_complete("001_Complete", 99)
                completed = True
                return

            if status == "complete" and task == "auto_start":
                log("條件達成，自動通報完成")
                notify_complete("001_Complete", 0) # 0=成功, 1=失敗, 99=錯誤
                completed = True
        except Exception as e:
            log(f"處理 MQTT 訊息時錯誤: {e}", level="error")

    # 註冊 MQTT 處理器
    mqtt.add_handler("uav/info", on_message)
    
    # 返回停止函數，讓流程控制器管理生命週期
    def stop():
        nonlocal stopped
        stopped = True
        mqtt.remove_handler("uav/info", on_message)
        log("步驟001停止")
    
    return {
        "stop": stop
    }


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="001", level=level)
    
    log("執行 step_001 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬任務完成"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬任務完成過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 001", level="debug")
            # 模擬結果 (0=成功, 1=失敗, 99=錯誤)
            result = 0  # 預設模擬成功
            notify_complete("001_Complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬任務完成計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬任務完成計時器", level="debug")
        
        log("DEBUG: 步驟001停止，資源已釋放")
    
    return {
        "stop": stop
    }