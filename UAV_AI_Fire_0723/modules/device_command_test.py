# """
# 設備控制 - 可單獨執行測試
# """
# try:
#     import Jetson.GPIO as GPIO
# except ImportError:
#     class MockGPIO:
#         BCM = "BCM"
#         OUT = "OUT"
#         HIGH = "HIGH"
#         LOW = "LOW"
#         def setmode(self, m): print(f"[MockGPIO] setmode {m}")
#         def setup(self, pin, mode, initial=None):
#             print(f"[MockGPIO] setup pin={pin}, mode={mode}, initial={initial}") 
#         def output(self, pin, value): print(f"[MockGPIO] output pin={pin}, value={value}")
#         def cleanup(self): print("[MockGPIO] cleanup")
#     GPIO = MockGPIO()

# import time
# import threading

# class DeviceCommand:
#     def __init__(self, gpio_pin):
#         self.pin = gpio_pin
#         GPIO.setmode(GPIO.BCM)
#         GPIO.setup(self.pin, GPIO.OUT, initial=GPIO.LOW)
#         print(f"已初始化設備控制，使用GPIO引腳: {gpio_pin}")
    
#     def start_spray(self):
#         """直接啟動噴灑"""
#         GPIO.output(self.pin, GPIO.HIGH)
#         print("啟動噴灑")
    
#     def stop_spray(self):
#         """直接停止噴灑"""
#         GPIO.output(self.pin, GPIO.LOW)
#         print("停止噴灑")
    
#     def trigger_thread(self, duration):
#         """内部线程实现"""
#         try:
#             self.start_spray()
#             time.sleep(duration)
#         finally:
#             self.stop_spray()
    
#     def timed_spray(self, duration):
#         """定時噴灑功能"""
#         print(f"開始定時噴灑，持續時間: {duration}秒")
#         spray_thread = threading.Thread(target=self.trigger_thread, args=(duration,))
#         spray_thread.daemon = True
#         spray_thread.start()
#         return spray_thread

# # 測試函數
# def test_device_command():
#     """測試設備控制功能"""
#     print("\n===== 設備控制測試開始 =====")
    
#     # 創建設備控制器 (使用GPIO 21)
#     device = DeviceCommand(21)
    
#     # 測試直接控制
#     print("\n測試直接控制:")
#     device.start_spray()
#     time.sleep(2)  # 噴灑2秒
#     device.stop_spray()
    
#     # 測試定時噴灑
#     print("\n測試定時噴灑:")
#     spray_thread = device.timed_spray(3)
#     print("等待噴灑完成...")
#     spray_thread.join()
    
#     print("\n===== 設備控制測試完成 =====")

# # 當直接執行此腳本時運行測試
# if __name__ == "__main__":
#     test_device_command()