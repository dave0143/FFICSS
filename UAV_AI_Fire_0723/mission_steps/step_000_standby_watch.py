import time
import threading
import datetime
import subprocess
import os

class TimeSyncManager:
    """時間同步管理器"""
    
    def __init__(self, logger, config):
        self.logger = logger
        self.config = config
        self.last_sync_date = None
        self.latest_uav_timestamp = None
        
        # 從 system.timesync 讀取時間同步設定
        system_config = config.get("system", {})
        self.sync_enabled = system_config.get("timesync", False)  # 預設為 False
        
        # 其他時間同步參數（可選配置）
        time_sync_config = config.get("time_sync", {})
        self.time_tolerance = time_sync_config.get("tolerance_seconds", 5)  # 時間容忍度（秒）
        self.sync_hour = time_sync_config.get("sync_hour", 0)  # 同步小時（預設 00:00）
        self.sync_minute = time_sync_config.get("sync_minute", 0)  # 同步分鐘
        
        # 記錄時間同步配置狀態
        if self.sync_enabled:
            self.log(f"時間同步功能已啟用，同步時間: {self.sync_hour:02d}:{self.sync_minute:02d}:00")
        else:
            self.log("時間同步功能已停用（system.timesync = false）")
        
    def log(self, msg, level="info"):
        """記錄日誌"""
        self.logger.log(f"[TimeSync] {msg}", step="000", level=level)
    
    def update_uav_timestamp(self, uav_info):
        """更新 UAV 時間戳"""
        try:
            # 如果時間同步功能未啟用，直接返回
            if not self.sync_enabled:
                return
                
            # 從 uav_info 中提取時間戳
            timestamp = uav_info.get("timestamp")
            if timestamp:
                self.latest_uav_timestamp = timestamp
                self.log(f"更新 UAV 時間戳: {timestamp}", level="debug")
                
                # 檢查是否需要進行時間同步
                self.check_and_sync_time()
                
        except Exception as e:
            self.log(f"更新 UAV 時間戳錯誤: {e}", level="error")
    
    def check_and_sync_time(self):
        """檢查並執行時間同步"""
        if not self.sync_enabled or not self.latest_uav_timestamp:
            return
        
        try:
            # 獲取當前時間
            now = datetime.datetime.now()
            current_date = now.date()
            
            # 檢查是否到達同步時間點（預設 00:00:00）
            if (now.hour == self.sync_hour and 
                now.minute == self.sync_minute and 
                now.second == 0 and 
                self.last_sync_date != current_date):
                
                self.log("到達時間同步點，開始執行時間校正")
                success = self.sync_system_time()
                
                if success:
                    self.last_sync_date = current_date
                    self.log(f"時間同步完成，下次同步日期: {current_date + datetime.timedelta(days=1)}")
                
        except Exception as e:
            self.log(f"檢查時間同步錯誤: {e}", level="error")
    
    def sync_system_time(self):
        """同步系統時間"""
        try:
            if not self.latest_uav_timestamp:
                self.log("沒有可用的 UAV 時間戳，跳過同步", level="warning")
                return False
            
            # 轉換 UAV 時間戳為 datetime 對象
            uav_time = datetime.datetime.fromtimestamp(self.latest_uav_timestamp)
            current_time = datetime.datetime.now()
            
            # 計算時間差
            time_diff = abs((uav_time - current_time).total_seconds())
            
            self.log(f"當前系統時間: {current_time}")
            self.log(f"UAV 時間: {uav_time}")
            self.log(f"時間差: {time_diff:.2f} 秒")
            
            # 如果時間差在容忍範圍內，不進行同步
            if time_diff <= self.time_tolerance:
                self.log(f"時間差在容忍範圍內({self.time_tolerance}秒)，不需要同步")
                return True
            
            # 格式化時間為系統命令格式 (YYYY-MM-DD HH:MM:SS)
            time_str = uav_time.strftime("%Y-%m-%d %H:%M:%S")
            
            self.log(f"開始設置系統時間為: {time_str}")
            
            # 嘗試多種時間設置方法
            success = False
            
            # 方法1：使用 date 命令
            try:
                cmd = f'sudo date -s "{time_str}"'
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    self.log("使用 date 命令成功設置系統時間")
                    success = True
                else:
                    self.log(f"date 命令失敗: {result.stderr}", level="warning")
            except Exception as e:
                self.log(f"date 命令執行錯誤: {e}", level="warning")
            
            # 方法2：使用 timedatectl（如果 date 命令失敗）
            if not success:
                try:
                    cmd = f'sudo timedatectl set-time "{time_str}"'
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        self.log("使用 timedatectl 命令成功設置系統時間")
                        success = True
                    else:
                        self.log(f"timedatectl 命令失敗: {result.stderr}", level="warning")
                except Exception as e:
                    self.log(f"timedatectl 命令執行錯誤: {e}", level="warning")
            
            # 方法3：直接寫入硬體時鐘
            if success:
                try:
                    # 同步到硬體時鐘
                    cmd = "sudo hwclock --systohc"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        self.log("成功同步到硬體時鐘")
                    else:
                        self.log(f"同步硬體時鐘失敗: {result.stderr}", level="warning")
                except Exception as e:
                    self.log(f"同步硬體時鐘錯誤: {e}", level="warning")
            
            if success:
                # 驗證時間設置結果
                new_time = datetime.datetime.now()
                final_diff = abs((uav_time - new_time).total_seconds())
                self.log(f"時間同步完成，最終時間差: {final_diff:.2f} 秒")
                
                if final_diff <= self.time_tolerance * 2:  # 放寬驗證標準
                    self.log("時間同步驗證成功")
                    return True
                else:
                    self.log(f"時間同步驗證失敗，差異仍然過大: {final_diff:.2f} 秒", level="warning")
                    return False
            else:
                self.log("所有時間設置方法都失敗", level="error")
                return False
            
        except Exception as e:
            self.log(f"同步系統時間錯誤: {e}", level="error")
            return False
    
    def get_sync_status(self):
        """獲取同步狀態"""
        return {
            "sync_enabled": self.sync_enabled,
            "last_sync_date": self.last_sync_date.isoformat() if self.last_sync_date else None,
            "latest_uav_timestamp": self.latest_uav_timestamp,
            "time_tolerance": self.time_tolerance,
            "sync_time": f"{self.sync_hour:02d}:{self.sync_minute:02d}:00"
        }


def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_000", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="000", level=level)
    
    log("step_000_standby_watch 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    mqtt = data_sources.get("mqtt_service")
    flow = data_sources.get("flow_controller")
    completed = False
    stopped = False

    # 初始化時間同步管理器
    time_sync_manager = TimeSyncManager(logger, config)
    
    # 根據配置決定是否記錄時間同步狀態
    system_config = config.get("system", {})
    if system_config.get("timesync", False):
        log("時間同步管理器已初始化並啟用")
    else:
        log("時間同步管理器已初始化但停用（system.timesync = false）")

    # 檢查是否在 standby 狀態
    if flow and flow.step != "standby":
        log("流程非 standby，略過本步驟", level="warn")
        return None

    def on_message(topic, payload):
        nonlocal completed, stopped
        if completed or stopped:
            return
            
        try:
            # 雙重檢查當前步驟狀態
            if flow and flow.step != "standby":
                log("流程已離開standby狀態，忽略訊息", level="debug")
                return
            
            # 更新時間同步管理器的 UAV 時間戳
            time_sync_manager.update_uav_timestamp(payload)
                
            status = payload.get("status", "").lower()
            navigation = payload.get("navigation", {})
            task = navigation.get("flight_mode", "").lower()

            log(f"step_000收到uav/info MQTT資料: status={status}, task={task}", level="debug")

            if status == "ongoing" and task == "auto_start":
                log("條件成立，通知流程完成 000")
                notify_complete("000_Complete", 0)  # 0=成功, 1=失敗, 99=錯誤
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
        log("步驟000停止")

        # 記錄時間同步狀態
        sync_status = time_sync_manager.get_sync_status()
        log(f"時間同步狀態: {sync_status}")
    
    return {
        "stop": stop,
        "time_sync_manager": time_sync_manager  # 提供時間同步管理器供外部查詢
    }


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="000", level=level)
    
    log("執行 step_000 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    # 在DEBUG模式下也初始化時間同步管理器
    time_sync_manager = TimeSyncManager(logger, config)
    
    # 根據配置決定是否記錄時間同步狀態
    system_config = config.get("system", {})
    if system_config.get("timesync", False):
        log("DEBUG: 時間同步管理器已初始化並啟用")
    else:
        log("DEBUG: 時間同步管理器已初始化但停用（system.timesync = false）")
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬任務啟動"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬任務啟動過程", level="debug")
        log("DEBUG: 等待2秒後將自動完成步驟", level="debug")
        
        # 模擬 UAV 數據，包含時間戳
        mock_uav_info = {
            "timestamp": time.time(),
            "status": "ongoing",
            "navigation": {
                "flight_mode": "auto_start"
            }
        }
        
        # 只有當時間同步啟用時才測試時間同步功能
        system_config = config.get("system", {})
        if system_config.get("timesync", False):
            log("DEBUG: 測試時間同步功能", level="debug")
            time_sync_manager.update_uav_timestamp(mock_uav_info)
        else:
            log("DEBUG: 時間同步功能已停用，跳過測試", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬步驟完成，通知流程完成 000", level="debug")
            # 模擬結果 (0=成功, 1=失敗, 99=錯誤)
            result = 0  # 預設模擬成功
            notify_complete("000_Complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬任務啟動計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬任務啟動計時器", level="debug")
        
        # 記錄時間同步狀態
        sync_status = time_sync_manager.get_sync_status()
        log(f"DEBUG: 時間同步狀態: {sync_status}")
        
        log("DEBUG: 步驟000停止，資源已釋放")
    
    return {
        "stop": stop,
        "time_sync_manager": time_sync_manager
    }