"""
提供豐富的日誌功能，包括多級別日誌、性能監控、自動清理和導出功能
"""

import sqlite3
import json
import time
import os
import threading
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union
from enum import Enum
import gzip
import csv
from contextlib import contextmanager

class LogLevel(Enum):
    """日誌等級枚舉"""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

class LoggerConfig:
    """日誌配置類"""
    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.load_config()
    
    def load_config(self):
        """載入配置文件"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            
            log_config = config.get("log", {})
            
            # 基本配置
            self.db_path = log_config.get("sqlite_db", "system_log.db")
            self.retention_days = log_config.get("retention_days", 10)
            self.max_file_size = log_config.get("max_file_size_mb", 100) * 1024 * 1024
            self.enable_compression = log_config.get("enable_compression", True)
            self.auto_vacuum = log_config.get("auto_vacuum", True)
            
            # 性能配置
            self.batch_size = log_config.get("batch_size", 100)
            self.flush_interval = log_config.get("flush_interval", 5)
            self.connection_pool_size = log_config.get("connection_pool_size", 3)
            
            # 日誌等級配置
            self.min_level = LogLevel[log_config.get("min_level", "INFO").upper()]
            self.console_output = log_config.get("console_output", True)
            self.file_output = log_config.get("file_output", True)
            
            # 輪轉配置
            self.enable_rotation = log_config.get("enable_rotation", True)
            self.rotation_size = log_config.get("rotation_size_mb", 50) * 1024 * 1024
            
        except Exception as e:
            print(f"[Logger] 載入配置錯誤: {e}，使用預設配置")
            self._set_default_config()
    
    def _set_default_config(self):
        """設置預設配置"""
        self.db_path = "system_log.db"
        self.retention_days = 10
        self.max_file_size = 100 * 1024 * 1024
        self.enable_compression = True
        self.auto_vacuum = True
        self.batch_size = 100
        self.flush_interval = 5
        self.connection_pool_size = 3
        self.min_level = LogLevel.INFO
        self.console_output = True
        self.file_output = True
        self.enable_rotation = True
        self.rotation_size = 50 * 1024 * 1024

class ConnectionPool:
    """數據庫連接池"""
    def __init__(self, db_path: str, pool_size: int = 3):
        self.db_path = db_path
        self.pool_size = pool_size
        self.connections = []
        self.lock = threading.RLock()
        self._init_pool()
    
    def _init_pool(self):
        """初始化連接池"""
        for _ in range(self.pool_size):
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self.connections.append(conn)
    
    @contextmanager
    def get_connection(self):
        """獲取數據庫連接"""
        with self.lock:
            if self.connections:
                conn = self.connections.pop()
            else:
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
        
        try:
            yield conn
        finally:
            with self.lock:
                if len(self.connections) < self.pool_size:
                    self.connections.append(conn)
                else:
                    conn.close()
    
    def close_all(self):
        """關閉所有連接"""
        with self.lock:
            for conn in self.connections:
                conn.close()
            self.connections.clear()

class LogBuffer:
    """日誌緩衝區"""
    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.buffer = []
        self.lock = threading.RLock()
    
    def add(self, log_entry: Dict[str, Any]):
        """添加日誌條目到緩衝區"""
        with self.lock:
            self.buffer.append(log_entry)
            return len(self.buffer) >= self.max_size
    
    def flush(self) -> List[Dict[str, Any]]:
        """清空緩衝區並返回所有條目"""
        with self.lock:
            buffer_copy = self.buffer.copy()
            self.buffer.clear()
            return buffer_copy
    
    def size(self) -> int:
        """獲取緩衝區大小"""
        with self.lock:
            return len(self.buffer)

class PerformanceMonitor:
    """性能監控器"""
    def __init__(self):
        self.metrics = {
            "total_logs": 0,
            "logs_per_level": {level.name: 0 for level in LogLevel},
            "avg_write_time": 0.0,
            "max_write_time": 0.0,
            "db_size": 0,
            "last_cleanup": None,
            "errors": 0
        }
        self.lock = threading.RLock()
        self.write_times = []
    
    def record_log(self, level: LogLevel, write_time: float):
        """記錄日誌統計"""
        with self.lock:
            self.metrics["total_logs"] += 1
            self.metrics["logs_per_level"][level.name] += 1
            
            self.write_times.append(write_time)
            if len(self.write_times) > 100:  # 只保留最近100次的寫入時間
                self.write_times.pop(0)
            
            self.metrics["avg_write_time"] = sum(self.write_times) / len(self.write_times)
            self.metrics["max_write_time"] = max(self.metrics["max_write_time"], write_time)
    
    def record_error(self):
        """記錄錯誤"""
        with self.lock:
            self.metrics["errors"] += 1
    
    def update_db_size(self, size: int):
        """更新數據庫大小"""
        with self.lock:
            self.metrics["db_size"] = size
    
    def get_metrics(self) -> Dict[str, Any]:
        """獲取性能指標"""
        with self.lock:
            return self.metrics.copy()

class EnhancedLogger:
    """增強的日誌記錄器"""
    
    def __init__(self, config_path: str = "config.json"):
        """初始化日誌記錄器"""
        self.config = LoggerConfig(config_path)
        self.connection_pool = ConnectionPool(self.config.db_path, self.config.connection_pool_size)
        self.buffer = LogBuffer(self.config.batch_size)
        self.performance_monitor = PerformanceMonitor()
        
        # 線程控制
        self.flush_thread = None
        self.cleanup_thread = None
        self.running = True
        self.lock = threading.RLock()
        
        # 初始化
        self._init_db()
        self._start_background_tasks()
        
        # 設置Python內建日誌
        self._setup_python_logging()
    
    def _init_db(self):
        """初始化資料庫表格"""
        try:
            with self.connection_pool.get_connection() as conn:
                # 主日誌表
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL,
                        datetime TEXT,
                        level INTEGER,
                        level_name TEXT,
                        message TEXT,
                        step TEXT,
                        source TEXT,
                        thread_id INTEGER,
                        extra_data TEXT,
                        created_at REAL DEFAULT (julianday('now'))
                    )
                """)
                
                # 性能指標表
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS performance_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL,
                        metric_name TEXT,
                        metric_value REAL,
                        details TEXT
                    )
                """)
                
                # 創建索引
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_step ON logs(step)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_source ON logs(source)")
                
                # 啟用自動清理
                if self.config.auto_vacuum:
                    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
                
                conn.commit()
                
        except Exception as e:
            print(f"[Logger] 初始化資料庫錯誤: {e}")
            self.performance_monitor.record_error()
    
    def _setup_python_logging(self):
        """設置Python內建日誌系統"""
        class CustomHandler(logging.Handler):
            def __init__(self, logger_instance):
                super().__init__()
                self.logger_instance = logger_instance
            
            def emit(self, record):
                try:
                    level_map = {
                        logging.DEBUG: LogLevel.DEBUG,
                        logging.INFO: LogLevel.INFO,
                        logging.WARNING: LogLevel.WARNING,
                        logging.ERROR: LogLevel.ERROR,
                        logging.CRITICAL: LogLevel.CRITICAL
                    }
                    
                    level = level_map.get(record.levelno, LogLevel.INFO)
                    self.logger_instance.log(
                        message=record.getMessage(),
                        level=level,
                        source=record.name,
                        step="python_logging"
                    )
                except:
                    pass
        
        handler = CustomHandler(self)
        logging.getLogger().addHandler(handler)
    
    def _start_background_tasks(self):
        """啟動背景任務"""
        # 啟動自動刷新線程
        self.flush_thread = threading.Thread(target=self._auto_flush_loop, daemon=True)
        self.flush_thread.start()
        
        # 啟動清理線程
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
    
    def _auto_flush_loop(self):
        """自動刷新循環"""
        while self.running:
            try:
                time.sleep(self.config.flush_interval)
                if self.buffer.size() > 0:
                    self._flush_buffer()
            except Exception as e:
                print(f"[Logger] 自動刷新錯誤: {e}")
                self.performance_monitor.record_error()
    
    def _cleanup_loop(self):
        """清理循環"""
        while self.running:
            try:
                # 每1小時執行一次清理
                time.sleep(3600)
                self._cleanup_old_logs()
                self._update_performance_metrics()
            except Exception as e:
                print(f"[Logger] 清理循環錯誤: {e}")
                self.performance_monitor.record_error()
    
    def log(self, 
           message: str, 
           level: Union[LogLevel, str] = LogLevel.INFO,
           step: str = "unknown",
           source: str = "general",
           extra_data: Optional[Dict[str, Any]] = None):
        """
        記錄日誌
        
        Args:
            message: 日誌訊息
            level: 日誌等級
            step: 當前步驟
            source: 日誌來源
            extra_data: 額外數據
        """
        # 處理等級參數
        if isinstance(level, str):
            try:
                level = LogLevel[level.upper()]
            except KeyError:
                level = LogLevel.INFO
        
        # 檢查最小等級
        if level.value < self.config.min_level.value:
            return
        
        timestamp = time.time()
        datetime_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        
        log_entry = {
            "timestamp": timestamp,
            "datetime": datetime_str,
            "level": level.value,
            "level_name": level.name,
            "message": str(message),
            "step": str(step),
            "source": str(source),
            "thread_id": threading.get_ident(),
            "extra_data": json.dumps(extra_data) if extra_data else None
        }
        
        # 控制台輸出
        if self.config.console_output:
            self._console_output(log_entry)
        
        # 添加到緩衝區
        start_time = time.time()
        should_flush = self.buffer.add(log_entry)
        
        # 如果緩衝區滿了，立即刷新
        if should_flush:
            self._flush_buffer()
        
        write_time = time.time() - start_time
        self.performance_monitor.record_log(level, write_time)
    
    def _console_output(self, log_entry: Dict[str, Any]):
        """控制台輸出"""
        level_colors = {
            "DEBUG": "\033[36m",    # 青色
            "INFO": "\033[32m",     # 綠色
            "WARNING": "\033[33m",  # 黃色
            "ERROR": "\033[31m",    # 紅色
            "CRITICAL": "\033[35m"  # 紫色
        }
        
        reset_color = "\033[0m"
        level_name = log_entry["level_name"]
        color = level_colors.get(level_name, "")
        
        print(f"{color}[{log_entry['datetime']}] {level_name:8} [{log_entry['step']}:{log_entry['source']}] {log_entry['message']}{reset_color}")
    
    def _flush_buffer(self):
        """刷新緩衝區到數據庫"""
        buffer_data = self.buffer.flush()
        if not buffer_data:
            return
        
        try:
            with self.connection_pool.get_connection() as conn:
                conn.executemany("""
                    INSERT INTO logs (timestamp, datetime, level, level_name, message, step, source, thread_id, extra_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    (entry["timestamp"], entry["datetime"], entry["level"], entry["level_name"],
                     entry["message"], entry["step"], entry["source"], entry["thread_id"], entry["extra_data"])
                    for entry in buffer_data
                ])
                conn.commit()
                
        except Exception as e:
            print(f"[Logger] 刷新緩衝區錯誤: {e}")
            self.performance_monitor.record_error()
            # 重新添加到緩衝區
            for entry in buffer_data:
                self.buffer.add(entry)
    
    def _cleanup_old_logs(self):
        """清理舊日誌"""
        try:
            cutoff_time = time.time() - (self.config.retention_days * 24 * 3600)
            
            with self.connection_pool.get_connection() as conn:
                # 刪除舊日誌
                cursor = conn.execute("DELETE FROM logs WHERE timestamp < ?", (cutoff_time,))
                deleted_count = cursor.rowcount
                
                # 刪除舊性能指標
                conn.execute("DELETE FROM performance_metrics WHERE timestamp < ?", (cutoff_time,))
                
                # 執行VACUUM以釋放空間
                if self.config.auto_vacuum:
                    conn.execute("PRAGMA incremental_vacuum")
                
                conn.commit()
                
                if deleted_count > 0:
                    self.log(f"已清理 {deleted_count} 條超過 {self.config.retention_days} 天的舊日誌", 
                           level=LogLevel.INFO, source="cleanup")
                
                self.performance_monitor.metrics["last_cleanup"] = datetime.now().isoformat()
                
        except Exception as e:
            self.log(f"清理舊日誌錯誤: {e}", level=LogLevel.ERROR, source="cleanup")
            self.performance_monitor.record_error()
    
    def _update_performance_metrics(self):
        """更新性能指標"""
        try:
            # 更新數據庫大小
            db_size = os.path.getsize(self.config.db_path) if os.path.exists(self.config.db_path) else 0
            self.performance_monitor.update_db_size(db_size)
            
            # 記錄性能指標到數據庫
            metrics = self.performance_monitor.get_metrics()
            timestamp = time.time()
            
            with self.connection_pool.get_connection() as conn:
                for metric_name, metric_value in metrics.items():
                    if isinstance(metric_value, (int, float)):
                        conn.execute("""
                            INSERT INTO performance_metrics (timestamp, metric_name, metric_value)
                            VALUES (?, ?, ?)
                        """, (timestamp, metric_name, metric_value))
                conn.commit()
                
        except Exception as e:
            print(f"[Logger] 更新性能指標錯誤: {e}")
    
    def get_recent_logs(self, 
                       limit: int = 100,
                       level_filter: Optional[LogLevel] = None,
                       step_filter: Optional[str] = None,
                       source_filter: Optional[str] = None,
                       start_time: Optional[float] = None,
                       end_time: Optional[float] = None) -> List[Dict[str, Any]]:
        """
        獲取最近的日誌記錄
        
        Args:
            limit: 限制數量
            level_filter: 等級過濾
            step_filter: 步驟過濾
            source_filter: 來源過濾
            start_time: 開始時間
            end_time: 結束時間
        """
        try:
            # 先刷新緩衝區
            self._flush_buffer()
            
            # 構建查詢
            query = "SELECT * FROM logs WHERE 1=1"
            params = []
            
            if level_filter:
                query += " AND level >= ?"
                params.append(level_filter.value)
            
            if step_filter:
                query += " AND step = ?"
                params.append(step_filter)
            
            if source_filter:
                query += " AND source = ?"
                params.append(source_filter)
            
            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time)
            
            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            with self.connection_pool.get_connection() as conn:
                cursor = conn.execute(query, params)
                columns = [description[0] for description in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
                
        except Exception as e:
            self.log(f"獲取日誌錯誤: {e}", level=LogLevel.ERROR, source="query")
            return []
    
    def export_logs(self, 
                   output_file: str = None,
                   format_type: str = "csv",
                   compress: bool = True,
                   **filters) -> str:
        """
        導出日誌到文件
        
        Args:
            output_file: 輸出文件名
            format_type: 格式類型 (csv, json)
            compress: 是否壓縮
            **filters: 過濾條件
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"logs_export_{timestamp}.{format_type}"
        
        try:
            logs = self.get_recent_logs(limit=0, **filters)  # 0 = 所有記錄
            
            if format_type.lower() == "csv":
                self._export_to_csv(logs, output_file, compress)
            elif format_type.lower() == "json":
                self._export_to_json(logs, output_file, compress)
            else:
                raise ValueError(f"不支援的格式: {format_type}")
            
            self.log(f"已導出 {len(logs)} 條日誌到 {output_file}", 
                   level=LogLevel.INFO, source="export")
            return output_file
            
        except Exception as e:
            self.log(f"導出日誌錯誤: {e}", level=LogLevel.ERROR, source="export")
            raise
    
    def _export_to_csv(self, logs: List[Dict], output_file: str, compress: bool):
        """導出到CSV格式"""
        if compress:
            output_file += ".gz"
            file_opener = gzip.open
        else:
            file_opener = open
        
        with file_opener(output_file, "wt", encoding="utf-8", newline="") as f:
            if logs:
                writer = csv.DictWriter(f, fieldnames=logs[0].keys())
                writer.writeheader()
                writer.writerows(logs)
    
    def _export_to_json(self, logs: List[Dict], output_file: str, compress: bool):
        """導出到JSON格式"""
        if compress:
            output_file += ".gz"
            file_opener = gzip.open
        else:
            file_opener = open
        
        with file_opener(output_file, "wt", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2, default=str)
    
    def get_statistics(self) -> Dict[str, Any]:
        """獲取日誌統計信息"""
        try:
            self._flush_buffer()
            
            with self.connection_pool.get_connection() as conn:
                # 基本統計
                cursor = conn.execute("SELECT COUNT(*) FROM logs")
                total_logs = cursor.fetchone()[0]
                
                # 各等級統計
                cursor = conn.execute("""
                    SELECT level_name, COUNT(*) 
                    FROM logs 
                    GROUP BY level_name
                    ORDER BY level DESC
                """)
                level_stats = dict(cursor.fetchall())
                
                # 各步驟統計
                cursor = conn.execute("""
                    SELECT step, COUNT(*) 
                    FROM logs 
                    GROUP BY step 
                    ORDER BY COUNT(*) DESC 
                    LIMIT 10
                """)
                step_stats = dict(cursor.fetchall())
                
                # 各來源統計
                cursor = conn.execute("""
                    SELECT source, COUNT(*) 
                    FROM logs 
                    GROUP BY source 
                    ORDER BY COUNT(*) DESC 
                    LIMIT 10
                """)
                source_stats = dict(cursor.fetchall())
                
                # 時間範圍
                cursor = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM logs")
                time_range = cursor.fetchone()
                
                return {
                    "total_logs": total_logs,
                    "level_distribution": level_stats,
                    "top_steps": step_stats,
                    "top_sources": source_stats,
                    "time_range": {
                        "start": datetime.fromtimestamp(time_range[0]).isoformat() if time_range[0] else None,
                        "end": datetime.fromtimestamp(time_range[1]).isoformat() if time_range[1] else None
                    },
                    "performance_metrics": self.performance_monitor.get_metrics(),
                    "db_size_mb": round(os.path.getsize(self.config.db_path) / 1024 / 1024, 2) if os.path.exists(self.config.db_path) else 0
                }
                
        except Exception as e:
            self.log(f"獲取統計信息錯誤: {e}", level=LogLevel.ERROR, source="stats")
            return {}
    
    def debug(self, message: str, **kwargs):
        """記錄DEBUG級別日誌"""
        self.log(message, LogLevel.DEBUG, **kwargs)
    
    def info(self, message: str, **kwargs):
        """記錄INFO級別日誌"""
        self.log(message, LogLevel.INFO, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """記錄WARNING級別日誌"""
        self.log(message, LogLevel.WARNING, **kwargs)
    
    def error(self, message: str, **kwargs):
        """記錄ERROR級別日誌"""
        self.log(message, LogLevel.ERROR, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """記錄CRITICAL級別日誌"""
        self.log(message, LogLevel.CRITICAL, **kwargs)
    
    def shutdown(self):
        """關閉日誌系統"""
        self.log("日誌系統正在關閉...", level=LogLevel.INFO, source="shutdown")
        
        # 停止背景線程
        self.running = False
        
        # 刷新剩餘的日誌
        self._flush_buffer()
        
        # 等待線程結束
        if self.flush_thread and self.flush_thread.is_alive():
            self.flush_thread.join(timeout=5)
        
        if self.cleanup_thread and self.cleanup_thread.is_alive():
            self.cleanup_thread.join(timeout=5)
        
        # 關閉連接池
        self.connection_pool.close_all()
        
        print("[Logger] 日誌系統已關閉")

# 向後兼容的Logger類
class Logger(EnhancedLogger):
    """向後兼容的Logger類"""
    
    def __init__(self, db_path: str):
        # 創建臨時配置
        config = {
            "log": {
                "sqlite_db": db_path,
                "retention_days": 10
            }
        }
        
        # 保存配置到臨時文件
        temp_config_path = "temp_logger_config.json"
        with open(temp_config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)
        
        # 初始化增強版日誌器
        super().__init__(temp_config_path)
        
        # 清理臨時文件
        try:
            os.remove(temp_config_path)
        except:
            pass
    
    def log(self, message, step="未知", source="一般", level="資訊"):
        """向後兼容的log方法"""
        # 轉換中文等級到英文
        level_map = {
            "偵錯": LogLevel.DEBUG,
            "資訊": LogLevel.INFO,
            "警告": LogLevel.WARNING,
            "錯誤": LogLevel.ERROR,
            "嚴重": LogLevel.CRITICAL,
            "debug": LogLevel.DEBUG,
            "info": LogLevel.INFO,
            "warning": LogLevel.WARNING,
            "error": LogLevel.ERROR,
            "critical": LogLevel.CRITICAL
        }
        
        log_level = level_map.get(level.lower(), LogLevel.INFO)
        super().log(message, log_level, step, source)


# 使用示例
if __name__ == "__main__":
    # 測試增強版日誌器
    logger = EnhancedLogger()
    
    # 測試各種日誌等級
    logger.debug("這是一個調試訊息", step="test", source="demo")
    logger.info("系統啟動完成", step="startup", source="main")
    logger.warning("記憶體使用率較高", step="monitor", source="health")
    logger.error("連接數據庫失敗", step="init", source="db")
    logger.critical("系統即將崩潰", step="crash", source="system")
    
    # 測試額外數據
    logger.info("用戶登入", step="auth", source="login", 
               extra_data={"user_id": 12345, "ip": "192.168.1.100"})
    
    # 等待一下讓日誌寫入
    time.sleep(2)
    
    # 獲取統計信息
    stats = logger.get_statistics()
    print("\n日誌統計信息:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    
    # 導出日誌
    export_file = logger.export_logs()
    print(f"\n日誌已導出到: {export_file}")