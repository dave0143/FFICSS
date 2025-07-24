# step_003_descent_search.py
"""
目標下降 - 簡化版
邏輯：
1. 收到uav/info即時高度
2. 檢查即時高度-descent_height是否會低於設定
3. 不會低於：發送下降waypoint到ai/target
4. 會低於：傳回complete, result=1
"""

import time
import threading
import json
from modules.utils import cal_next_position
from modules.coordinate_conversion import waypoint_format

class DescentController:
    def __init__(self, config, logger, mqtt_service, notify_complete):
        self.config = config
        self.logger = logger
        self.mqtt_service = mqtt_service
        self.notify_complete = notify_complete
        self.stopped = False
        self.task_completed = False
        self.waypoint_sent = False  # 新增：是否已發送waypoint
        self.waiting_for_completion = False  # 新增：是否等待完成
        
        # 配置參數
        self.min_height = config.get("gimbal_search", {}).get("min_height", 4)
        self.descent_height = config.get("gimbal_search", {}).get("descent_height", 2)
        
        # 註冊 MQTT 處理器
        self.mqtt_service.add_handler("uav/info", self.on_uav_info)
        
        self.log("下降控制器初始化完成")
        self.log(f"最小安全高度: {self.min_height}m")
        self.log(f"下降高度: {self.descent_height}m")
        
    def log(self, msg, level="info"):
        self.logger.log(f"[Descent] {msg}", step="003", level=level)
        
    def on_uav_info(self, topic, payload):
        """處理來自 MQTT 的 uav/info 訊息"""
        if self.stopped or self.task_completed:
            return
            
        try:
            # 獲取即時高度和狀態
            navigation = payload.get("navigation", {})
            global_pos = navigation.get("global_position", {})
            current_altitude = global_pos.get("alt", 0)
            status = payload.get("status", "").lower()
            
            if current_altitude <= 0:
                self.log("無法獲取有效的UAV高度資訊", level="warning")
                return
            
            # 情況1: 如果正在等待任務完成
            if self.waiting_for_completion:
                self.log(f"等待下降完成中 - 當前狀態: {status}, 高度: {current_altitude:.2f}m")
                
                if status == "complete":
                    self.log("收到UAV任務完成狀態")
                    self.log("下降任務成功完成")
                    self.task_completed = True
                    self.notify_complete("003_complete", 0)  # result=0 表示成功完成
                return
            
            # 情況2: 如果還沒發送waypoint，進行高度檢查
            if not self.waypoint_sent:
                self.log(f"收到UAV即時高度: {current_altitude:.2f}m")
                
                # 計算目標高度
                target_altitude = current_altitude - self.descent_height
                
                self.log(f"計算目標高度: {current_altitude:.2f} - {self.descent_height} = {target_altitude:.2f}m")
                
                # 檢查是否會低於最小安全高度
                if target_altitude < self.min_height:
                    self.log(f"目標高度 {target_altitude:.2f}m 低於最小安全高度 {self.min_height}m")
                    self.log("下降任務完成 - 高度限制")
                    self.task_completed = True
                    self.notify_complete("003_complete", 1)  # result=1 表示因高度限制完成
                    return
                
                # 安全檢查通過，發送下降waypoint
                self.send_descent_waypoint(payload, target_altitude)
            
        except Exception as e:
            self.log(f"處理 uav/info 時發生錯誤: {e}", level="error")
    
    def send_descent_waypoint(self, uav_info, target_altitude):
        """發送下降waypoint到ai/target"""
        try:
            # 獲取當前位置資訊
            navigation = uav_info.get("navigation", {})
            global_pos = navigation.get("global_position", {})
            sensors = uav_info.get("sensors", {})
            imu = sensors.get("imu", {})
            
            current_lat = global_pos.get("lat", 0)
            current_lon = global_pos.get("lon", 0)
            current_yaw = imu.get("yaw", 0)
            
            # 構建waypoint任務
            mission = {
                "timestamp": time.time(),
                "task": "process",
                "type": 1,
                "step": "003",
                "mission": [
                    {
                        "waypoint": 1,
                        "lat": current_lat,
                        "lon": current_lon,
                        "alt": target_altitude,
                        "ang": current_yaw,
                        "flight_speed": 0.5,  # 慢速下降
                        "loiter_time": 0
                    }
                ]
            }
            
            # 顯示Mission內容
            self.log("=" * 50)
            self.log("發送下降 Mission 到 ai/target:")
            self.log("=" * 50)
            self.log(f"Task Type: {mission['task']}")
            self.log(f"Step: {mission['step']}")
            self.log(f"--- Waypoint {mission['mission'][0]['waypoint']} ---")
            self.log(f"  緯度 (lat):     {mission['mission'][0]['lat']:.8f}")
            self.log(f"  經度 (lon):     {mission['mission'][0]['lon']:.8f}")
            self.log(f"  高度 (alt):     {mission['mission'][0]['alt']:.2f} m")
            self.log(f"  角度 (ang):     {mission['mission'][0]['ang']:.2f}°")
            self.log(f"  飛行速度:       {mission['mission'][0]['flight_speed']} m/s")
            self.log(f"  懸停時間:       {mission['mission'][0]['loiter_time']} s")
            
            # 顯示JSON格式
            self.log("--- Mission JSON ---")
            try:
                mission_json = json.dumps(mission, indent=2, ensure_ascii=False)
                for line in mission_json.split('\n'):
                    self.log(f"  {line}")
            except Exception as e:
                self.log(f"無法序列化 Mission 為 JSON: {e}", level="error")
            
            self.log("=" * 50)
            
            # 發送到ai/target
            self.mqtt_service.publish("ai/target", mission)
            self.log(f"✅ 下降waypoint已發送到ai/target")
            self.log(f"下降指令: 從 {global_pos.get('alt', 0):.2f}m 下降到 {target_altitude:.2f}m")
            
            # 設置狀態：已發送waypoint，等待完成
            self.waypoint_sent = True
            self.waiting_for_completion = True
            self.log("waypoint已發送，開始等待UAV完成下降任務...")
            
        except Exception as e:
            self.log(f"發送下降waypoint時發生錯誤: {e}", level="error")
            self.task_completed = True
            self.notify_complete("003_complete", 99)  # result=99 表示錯誤
    
    def stop(self):
        """停止控制器"""
        self.stopped = True
        self.task_completed = True
        self.waiting_for_completion = False
        # 移除 MQTT 處理器
        self.mqtt_service.remove_handler("uav/info", self.on_uav_info)
        self.log("下降控制器已停止，MQTT處理器已移除")


def run_step(data_sources, config, logger, notify_complete):
    # 檢查是否強制啟用DEBUG模式
    force_debug = config.get("debug_config", {}).get("step_003", False)
    
    # 內部日誌函數，統一處理debug前綴
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] " if force_debug else ""
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="003", level=level)
    
    log("step_003_descent_search 啟動")

    # 如果DEBUG模式啟用，執行debug測試
    if force_debug:
        return debug_test(data_sources, config, logger, notify_complete)

    mqtt_service = data_sources.get("mqtt_service")
    
    if not mqtt_service:
        log("MQTT服務未可用", level="error")
        notify_complete("003_complete", 99)  # 錯誤狀態
        return None
    
    logger.log("執行 step_003_descent_search...", step="003", source="step_module")
    
    # 創建下降控制器
    controller = DescentController(config, logger, mqtt_service, notify_complete)
    log("創建下降控制器，開始監聽 uav/info")
    
    # 返回停止函數
    def stop():
        if controller:
            controller.stop()
            log("步驟003停止")
    
    return {
        "stop": stop,
        "data": {"controller": controller}
    }


def debug_test(data_sources, config, logger, notify_complete):
    """DEBUG模式測試函數"""
    def log(msg, level="info"):
        debug_prefix = "[DEBUG] "
        full_msg = f"{debug_prefix}{msg}"
        logger.log(full_msg, step="003", level=level)
    
    log("執行 step_003 DEBUG測試...")
    
    # 初始化狀態變數
    completed = False
    stopped = False
    debug_timer = None
    
    def _debug_mode_scan():
        """DEBUG模式專用掃描邏輯 - 模擬下降檢查過程"""
        nonlocal completed, debug_timer, stopped
        
        log("DEBUG模式啟動 - 模擬下降高度檢查", level="debug")
        log("DEBUG: 模擬檢查高度限制，2秒後自動完成", level="debug")
        
        # 模擬延遲2秒
        time.sleep(2)
        
        # 檢查是否已被停止
        if stopped:
            log("DEBUG: 步驟已被停止，取消模擬完成", level="debug")
            return
            
        # 直接完成步驟
        if not completed:
            log("DEBUG: 模擬高度檢查完成", level="debug")
            # 模擬結果 (0=成功發送下降指令, 1=高度過低, 99=錯誤)
            result = 0  # 預設模擬成功
            notify_complete("003_complete", result)
            completed = True
        
        # 重置計時器
        debug_timer = None
    
    # 啟動模擬
    log("DEBUG: 啟動模擬高度檢查計時器", level="debug")
    debug_timer = threading.Thread(target=_debug_mode_scan, daemon=True)
    debug_timer.start()
    
    # 返回停止函數
    def stop():
        nonlocal stopped, debug_timer
        stopped = True
        
        # 取消DEBUG計時器
        if debug_timer and debug_timer.is_alive():
            log("DEBUG: 取消模擬高度檢查計時器", level="debug")
        
        log("DEBUG: 步驟003停止，資源已釋放")
    
    return {
        "stop": stop
    }