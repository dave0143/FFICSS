import json
import os

class ConfigLoader:
    DEFAULT_CONFIG = {
        "log": {
            "sqlite_db": "default_system_log.db",
            "retention_days": 10
        },
        "mqtt": {
            "broker": "localhost",
            "port": 1883,
            "keepalive": 60,
            "qos": 1,
            "topics": {}
        },
        "device": {
            # "gpio_pin": 21,
            "active_duration": 10
        }
    }
    
    def __init__(self, config_file):
        self.config_file = config_file
        self.config = self._load_config()
    
    def _load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置和加载的配置
                    return {**self.DEFAULT_CONFIG, **config}
            else:
                print(f"配置文件 {self.config_file} 不存在，使用默认配置")
                return self.DEFAULT_CONFIG
        except Exception as e:
            print(f"載入設定檔錯誤: {e}")
            return self.DEFAULT_CONFIG
    
    def get(self, key, default=None):
        """取得設定值，支援巢狀鍵值"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value