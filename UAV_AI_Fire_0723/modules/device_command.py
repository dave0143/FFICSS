"""
設備控制
"""
try:
    import Jetson.GPIO as GPIO
except ImportError:
    class MockGPIO:
        BCM = "BCM"
        OUT = "OUT"
        HIGH = "HIGH"
        LOW = "LOW"
        def setmode(self, m): print(f"[MockGPIO] setmode {m}")
        def setup(self, pin, mode, initial=None):
            print(f"[MockGPIO] setup pin={pin}, mode={mode}, initial={initial}") 
            print(f"[MockGPIO] setup pin={pin}, mode={mode}")
        def output(self, pin, value): print(f"[MockGPIO] output pin={pin}, value={value}")
        def cleanup(self): print("[MockGPIO] cleanup")
    GPIO = MockGPIO()

import time
import threading

class DeviceCommand:
    def __init__(self, gpio_pin):
        self.pin = gpio_pin
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.OUT, initial=GPIO.LOW)
    
    def start_spray(self):
        """直接啟動噴灑"""
        GPIO.output(self.pin, GPIO.HIGH)
        print("啟動噴灑")
    
    def stop_spray(self):
        """直接停止噴灑"""
        GPIO.output(self.pin, GPIO.LOW)
        print("停止噴灑")
    
    def trigger_thread(self, duration):
        """内部线程实现"""
        try:
            self.start_spray()
            time.sleep(duration)
        finally:
            self.stop_spray()