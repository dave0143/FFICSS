"""
主控流程協調器 - 修正版
"""

import time
import threading
import signal
import sys
import argparse
from modules.config_loader import ConfigLoader
from modules.logger import Logger
from modules.mqtt_service import MQTTService
from modules.ktgGimbalControl import KTGGimbalController
from modules.flow_step import FlowStepController, StepStatus
from modules.device_command import DeviceCommand

class SystemController:
    def __init__(self, debug_mode=False):
        self.running = True
        self.shutdown_event = threading.Event()
        self.config = None
        self.logger = None
        self.flow = None
        self.gimbal = None
        self.device = None
        self.mqtt = None
        self.debug_mode = debug_mode

        # 系統健康檢查相關
        self.last_health_check = time.time()
        self.health_check_interval = 5  # 5秒檢查一次
        
    def initialize_system(self):
        """初始化系統所有模組"""
        try:
            # 初始化配置
            config_loader = ConfigLoader("config.json")
            self.config = config_loader.config
            
            # 初始化日誌
            log_config = self.config.get("log", {})
            db_path = log_config.get("sqlite_db", "default_system_log.db")
            self.logger = Logger(db_path)
            self.logger.log("系統初始化開始", step="system", level="info")
            
            # 初始化流程控制器
            self.flow = FlowStepController(
                config=self.config, 
                logger=self.logger,
                debug_mode=self.debug_mode
            )
            
            # 初始化雲台控制器
            gimbal_config = self.config["gimbal_tcp"]
            self.gimbal = KTGGimbalController(
                ip=gimbal_config["ip"],
                port=gimbal_config["port"]
            )
            
            # 初始化設備控制
            device_config = self.config["device"]
            self.device = DeviceCommand(gpio_pin=device_config["gpio_pin"])
            self.flow.set_device_controller(self.device)
            
            # 設置模組間的依賴關係
            self.setup_module_dependencies()
            
            self.logger.log("系統初始化完成", step="system", level="info")
            return True
            
        except Exception as e:
            error_msg = f"系統初始化失敗: {e}"
            if self.logger:
                self.logger.log(error_msg, step="system", level="error")
            else:
                print(f"[ERROR] {error_msg}")
            return False
    
    def setup_module_dependencies(self):
        """設置模組間的依賴關係"""
        # MQTT回調函數
        def mqtt_callback(topic, payload):
            try:
                self.flow.handle_mqtt_data(topic, payload)
            except Exception as e:
                self.logger.log(f"MQTT回調處理錯誤: {e}", step="mqtt", level="error")
        
        # 初始化MQTT服務
        self.mqtt = MQTTService(self.config["mqtt"], mqtt_callback)
        self.mqtt.set_logger(self.logger)
        
        # 設置雲台相關依賴
        self.gimbal.set_mqtt_service(self.mqtt)
        self.flow.set_mqtt_service(self.mqtt)
        self.flow.set_gimbal_controller(self.gimbal)
        
        # 雲台數據回調
        def gimbal_callback(info):
            try:
                self.flow.on_tcp_data(info)
            except Exception as e:
                self.logger.log(f"雲台回調處理錯誤: {e}", step="gimbal", level="error")
        
        self.gimbal.callback = gimbal_callback
    
    def start_services(self):
        """啟動所有服務"""
        try:
            # 啟動MQTT服務
            self.mqtt.start()
            self.logger.log("MQTT服務啟動", step="system", level="info")
            
            # 啟動雲台連接（非阻塞）
            threading.Thread(target=self.start_gimbal_service, daemon=True).start()
            
            # 啟動系統健康檢查
            threading.Thread(target=self.health_check_loop, daemon=True).start()
            
            self.logger.log("所有服務啟動完成", step="system", level="info")
            return True
            
        except Exception as e:
            self.logger.log(f"服務啟動失敗: {e}", step="system", level="error")
            return False
    
    def start_gimbal_service(self):
        """啟動雲台服務（在背景執行緒中運行）"""
        try:
            # 嘗試初始連接
            if self.gimbal.connect():
                self.logger.log("雲台初始連接成功", step="gimbal", level="info")
                # 啟動數據監聽
                self.gimbal.start_listening()
            else:
                self.logger.log("雲台初始連接失敗，將持續嘗試重連", step="gimbal", level="warning")
            
            # 啟動連接維護循環（這是阻塞調用，所以在背景執行緒中運行）
            self.gimbal.maintain_connection_loop()
            
        except Exception as e:
            self.logger.log(f"雲台服務錯誤: {e}", step="gimbal", level="error")
    
    def health_check_loop(self):
        """系統健康檢查循環"""
        self.logger.log("系統健康檢查服務啟動", step="health", level="info")
        
        while self.running:
            try:
                current_time = time.time()
                
                # 檢查各模組狀態
                self.check_system_health()
                
                # 主動檢查UAV狀態
                self.check_uav_status()
                
                # 檢查流程超時
                if self.flow:
                    self.flow.check_timeout()
                
                self.last_health_check = current_time
                
                # 等待下次檢查或系統關閉信號
                if self.shutdown_event.wait(timeout=self.health_check_interval):
                    break
                    
            except Exception as e:
                self.logger.log(f"健康檢查錯誤: {e}", step="health", level="error")
                time.sleep(self.health_check_interval)
    
    def check_system_health(self):
        """檢查系統各模組健康狀態"""
        try:
            health_status = {"status": "healthy", "issues": []}
            
            # 檢查MQTT連接狀態
            if self.mqtt and not self.mqtt.is_connected():
                health_status["issues"].append("MQTT連接異常")
                health_status["status"] = "warning"
            
            # 檢查雲台連接狀態
            if self.gimbal and not self.gimbal.connected:
                health_status["issues"].append("雲台連接異常")
                health_status["status"] = "warning"
            
            # 檢查流程控制器狀態
            if self.flow and self.flow.get_step_status() and self.flow.step_status == StepStatus.FAILED:
                health_status["issues"].append(f"流程步驟 {self.flow.step} 失敗")
                health_status["status"] = "error"
            
            # 檢查UAV電池狀態
            if self.flow and self.flow.uav_info:
                battery = self.flow.uav_info.get("battery", {})
                if battery.get("percentage", 100) < 20:
                    health_status["issues"].append("UAV電池低於20%")
                    health_status["status"] = "warning"
            
            # 發布健康狀態
            if self.mqtt and self.mqtt.is_connected():
                self.mqtt.publish(
                    self.config["system_monitoring"]["system_status_topic"],
                    health_status
                )
            
            return health_status
            
        except Exception as e:
            self.logger.log(f"系統健康檢查錯誤: {e}", step="health", level="error")
            return {"status": "error", "error": str(e)}
    
    def check_uav_status(self):
        """主動檢查UAV狀態"""
        try:
            if self.flow and self.flow.uav_info:
                self.flow.check_uav_status()
        except Exception as e:
            self.logger.log(f"UAV狀態檢查錯誤: {e}", step="health", level="error")
    
    def main_loop(self):
        """主控制循環"""
        self.logger.log("主控制循環啟動", step="main", level="info")
        
        while self.running:
            try:
                # 處理當前步驟
                if self.flow:
                    self.flow.execute_current_step()
                
                # 發布系統狀態
                if self.mqtt and self.mqtt.is_connected():
                    system_status = {
                        "step": self.flow.step if self.flow else "unknown",
                        "timestamp": int(time.time()),
                        "gimbal_connected": self.gimbal.connected if self.gimbal else False,
                        "mqtt_connected": self.mqtt.is_connected() if self.mqtt else False,
                        "system_health": "healthy"  # 可以根據實際健康檢查結果調整
                    }
                    self.mqtt.publish("system/status", system_status)
                
                # 檢查是否需要關閉
                if self.shutdown_event.wait(timeout=0.5):
                    break
                    
            except Exception as e:
                self.logger.log(f"主迴圈錯誤: {e}", step="main", level="error")
                time.sleep(2)  # 出錯時稍微等待
    
    def shutdown(self):
        """系統關閉程序"""
        self.logger.log("系統關閉程序啟動", step="system", level="info")
        self.running = False
        self.shutdown_event.set()
        
        try:
            # 停止當前任務
            if self.flow and self.flow.current_step_stop_func:
                self.logger.log("停止當前流程任務", step="system", level="info")
                self.flow.current_step_stop_func()
            
            # 停止設備
            if self.device:
                self.logger.log("停止設備控制", step="system", level="info")
                self.device.stop_spray()  # 確保噴灑停止
            
            # 關閉MQTT
            if self.mqtt:
                self.logger.log("關閉MQTT服務", step="system", level="info")
                self.mqtt.stop()
            
            # 關閉雲台
            if self.gimbal:
                self.logger.log("關閉雲台連接", step="system", level="info")
                self.gimbal.disconnect()
            
            self.logger.log("系統關閉完成", step="system", level="info")
            
        except Exception as e:
            error_msg = f"系統關閉時發生錯誤: {e}"
            if self.logger:
                self.logger.log(error_msg, step="system", level="error")
            else:
                print(f"[ERROR] {error_msg}")

def signal_handler(signum, frame):
    """信號處理器"""
    print(f"\n收到信號 {signum}，正在關閉系統...")
    if 'system_controller' in globals():
        system_controller.shutdown()
    sys.exit(0)
        
def main():
    """主函數"""
    global system_controller
    
    # 解析命令列參數 - 新增debug模式選項
    parser = argparse.ArgumentParser(description='UAV 系統控制器')
    parser.add_argument('--debug', action='store_true', help='啟用debug模式')
    args = parser.parse_args()
    
    # 註冊信號處理器
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # 終止信號
    
    # 創建系統控制器 - 傳遞debug模式參數
    system_controller = SystemController(debug_mode=args.debug)
    
    try:    
        # 初始化系統
        if not system_controller.initialize_system():
            print("系統初始化失敗，程式結束")
            return 1
        
        # 啟動服務
        if not system_controller.start_services():
            print("服務啟動失敗，程式結束")
            return 1
        
        # 等待一段時間讓服務穩定啟動
        time.sleep(2)
        
        # 進入主控制循環（保持永遠執行）
        while True:
            system_controller.main_loop()
            time.sleep(1)  # 防止 CPU 滿載
        
    except KeyboardInterrupt:
        print("\n收到鍵盤中斷，正在關閉系統...")
    except Exception as e:
        error_msg = f"系統運行時發生未預期錯誤: {e}"
        if system_controller.logger:
            system_controller.logger.log(error_msg, step="main", level="error")
        else:
            print(f"[ERROR] {error_msg}")
    finally:
        # 確保系統正確關閉
        system_controller.shutdown()
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)