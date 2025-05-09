import socket
import sys
import time
import threading
import binascii
import cv2
import numpy as np
import json
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

class RTSPStream:
    """RTSP Stream Class"""
    def __init__(self, rtsp_url):
        """
        初始化RTSP串流
        :param rtsp_url: RTSP串流URL
        """
        self.rtsp_url = rtsp_url
        self.cap = None
        self.running = False
        self.stream_thread = None
        self.frame = None
        self.frame_lock = threading.Lock()
        self.last_frame_time = None
    
    def start_stream(self):
        """啟動串流"""
        if self.running:
            print("RTSP串流已在運行中")
            return False
        
        try:
            self.cap = cv2.VideoCapture(self.rtsp_url)
            if not self.cap.isOpened():
                print(f"無法打開RTSP串流: {self.rtsp_url}")
                return False
            
            self.running = True
            self.stream_thread = threading.Thread(target=self._stream_loop)
            self.stream_thread.daemon = True
            self.stream_thread.start()
            print("RTSP串流執行緒已啟動")
            return True
            
        except Exception as e:
            print(f"啟動RTSP串流時發生錯誤: {e}")
            return False
    
    def _stream_loop(self):
        """串流循環"""
        while self.running and self.cap:
            try:
                ret, frame = self.cap.read()
                if ret:
                    with self.frame_lock:
                        self.frame = frame
                        self.last_frame_time = datetime.now()
                else:
                    print("無法讀取RTSP幀")
                    time.sleep(0.01)  # 減少等待時間從0.1秒到0.01秒
                
            except Exception as e:
                print(f"讀取RTSP幀時發生錯誤: {e}")
                time.sleep(0.01)  # 減少等待時間從0.1秒到0.01秒
    
    def get_frame(self):
        """獲取最新幀"""
        with self.frame_lock:
            if self.frame is not None:
                return self.frame.copy()
            return None
    
    def stop_stream(self):
        """停止串流"""
        self.running = False
        if self.cap:
            self.cap.release()
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=3.0)
        print("RTSP串流已停止")

class TargetDataDisplay:
    """Target Data Display Class"""
    def __init__(self):
        self.distance = 0
        self.height = 0
        self.longitude = 0
        self.latitude = 0
        self.z_angle = 0
        self.pitch_angle = 0
        self.roll_angle = 0
        self.yaw_angle = 0
        self.range_enabled = False
        self.running = False
        self.display_thread = None
        self.data_lock = threading.Lock()
        self.last_update_time = datetime.now()
        self.rtsp_stream = None
        self.on_esc_pressed = None  # 添加ESC按下的回調函數
        self._window_closed = False  # 添加視窗關閉標誌
    
    def set_rtsp_stream(self, rtsp_stream):
        """設置RTSP串流"""
        self.rtsp_stream = rtsp_stream
    
    def set_esc_callback(self, callback):
        """設置ESC按下的回調函數"""
        self.on_esc_pressed = callback
    
    def put_text(self, img, text, position, text_color=(255, 255, 255)):
        """
        Add text to image
        :param img: OpenCV image
        :param text: text to display
        :param position: text position (x, y)
        :param text_color: text color
        """
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 1)  # 調整字體大小和粗細
        return img
    
    def update_data(self, z_angle, pitch_angle, roll_angle, yaw_angle, range_enabled, 
                   distance, height, longitude, latitude):
        """Update target data"""
        with self.data_lock:
            self.z_angle = z_angle
            self.pitch_angle = pitch_angle
            self.roll_angle = roll_angle
            self.yaw_angle = yaw_angle
            self.range_enabled = range_enabled
            self.distance = distance
            self.height = height
            self.longitude = longitude
            self.latitude = latitude
            self.last_update_time = datetime.now()
    
    def start_display(self):
        """Start display thread"""
        if self.running:
            print("Target data display is already running")
            return False
        
        self.running = True
        self.display_thread = threading.Thread(target=self._display_loop)
        self.display_thread.daemon = True
        self.display_thread.start()
        print("Target data display thread started")
        return True
    
    def _display_loop(self):
        """Display loop"""
        # Create display window
        window_name = "Target Data"
        window_size = (1200, 800)  # 增加視窗高度以容納影像
        
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, *window_size)
        cv2.moveWindow(window_name, 50, 50)
        
        # Create display background
        background = np.zeros((window_size[1], window_size[0], 3), dtype=np.uint8)
        background[:] = (30, 30, 30)  # Dark gray background
        
        # Main display loop
        while self.running:
            # Get latest data
            with self.data_lock:
                z_angle = self.z_angle
                pitch_angle = self.pitch_angle
                roll_angle = self.roll_angle
                yaw_angle = self.yaw_angle
                range_enabled = self.range_enabled
                distance = self.distance
                height = self.height
                longitude = self.longitude
                latitude = self.latitude
                update_time = self.last_update_time
            
            # Create new image
            img = background.copy()
            
            # Add title
            img = self.put_text(img, "Target Data Monitor", (20, 30), (255, 255, 255))
            
            # Add update time
            time_str = update_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            img = self.put_text(img, f"Update Time: {time_str}", (window_size[0]-700, 30), (150, 150, 150))
            
            # Draw separator line
            cv2.line(img, (20, 50), (window_size[0]-20, 50), (100, 100, 100), 2)
            
            # Define data display area
            data_y_start = 80
            row_height = 45  # 減少行高
            col_width = window_size[0] // 2
            
            # Draw angle data
            y_pos = data_y_start
            img = self.put_text(img, "Z-Axis Motor Angle:", (30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{z_angle*0.01:.2f}°", (30, y_pos+30), (0, 255, 0))
            
            img = self.put_text(img, "Pitch Angle:", (col_width+30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{pitch_angle*0.01:.2f}°", (col_width+30, y_pos+30), (0, 255, 0))
            
            y_pos += row_height
            img = self.put_text(img, "Roll Angle:", (30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{roll_angle*0.01:.2f}°", (30, y_pos+30), (0, 255, 0))
            
            img = self.put_text(img, "Yaw Angle:", (col_width+30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{yaw_angle*0.01:.2f}°", (col_width+30, y_pos+30), (0, 255, 0))
            
            y_pos += row_height
            img = self.put_text(img, "Range Status:", (30, y_pos), (150, 150, 255))
            img = self.put_text(img, "Enabled" if range_enabled else "Disabled", (30, y_pos+30), 
                              (0, 255, 0) if range_enabled else (255, 0, 0))
            
            # Draw distance and height
            y_pos += row_height
            img = self.put_text(img, "Distance:", (30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{distance*0.1:.2f} m", (30, y_pos+30), (0, 255, 0))
            
            img = self.put_text(img, "Height:", (col_width+30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{height:.2f} m", (col_width+30, y_pos+30), (0, 255, 0))
            
            # Draw coordinates
            y_pos += row_height
            img = self.put_text(img, "Longitude:", (30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{longitude:.6f}°", (30, y_pos+30), (0, 255, 0))
            
            img = self.put_text(img, "Latitude:", (col_width+30, y_pos), (150, 150, 255))
            img = self.put_text(img, f"{latitude:.6f}°", (col_width+30, y_pos+30), (0, 255, 0))
            
            # Draw separator line before video
            y_pos += row_height + 10
            cv2.line(img, (20, y_pos), (window_size[0]-20, y_pos), (100, 100, 100), 2)
            
            # Draw RTSP stream if available
            if self.rtsp_stream:
                rtsp_frame = self.rtsp_stream.get_frame()
                if rtsp_frame is not None:
                    # Calculate video display area
                    video_y = y_pos + 10
                    video_height = window_size[1] - video_y - 20
                    video_width = window_size[0] - 20
                    
                    # Resize RTSP frame to fit in the display area
                    rtsp_height = video_height
                    rtsp_width = int(rtsp_height * (rtsp_frame.shape[1] / rtsp_frame.shape[0]))
                    
                    # Center the video horizontally
                    rtsp_x = (window_size[0] - rtsp_width) // 2
                    
                    # Resize frame
                    rtsp_frame = cv2.resize(rtsp_frame, (rtsp_width, rtsp_height))
                    
                    # Create a mask for the RTSP frame
                    mask = rtsp_frame[:, :, 3] if rtsp_frame.shape[2] == 4 else None
                    
                    # Place RTSP frame in the image
                    if mask is not None:
                        # If frame has alpha channel
                        img[video_y:video_y+rtsp_height, rtsp_x:rtsp_x+rtsp_width] = \
                            img[video_y:video_y+rtsp_height, rtsp_x:rtsp_x+rtsp_width] * (1 - mask[:, :, None]) + \
                            rtsp_frame[:, :, :3] * mask[:, :, None]
                    else:
                        # If frame doesn't have alpha channel
                        img[video_y:video_y+rtsp_height, rtsp_x:rtsp_x+rtsp_width] = rtsp_frame
            
            # Draw bottom instructions
            footer_y = window_size[1] - 20
            img = self.put_text(img, "Press 'q' or 'ESC' to close", (20, footer_y), (150, 150, 150))
            
            # Show window
            cv2.imshow(window_name, img)
            
            # Check for key press
            key = cv2.waitKey(1)  # 減少等待時間從100ms到1ms
            if key == ord('q') or key == 27:  # 'q' or ESC to exit
                self.running = False
                if self.on_esc_pressed:  # 調用ESC回調函數
                    self.on_esc_pressed()
                break
        
        # Clean up resources
        if not self._window_closed:  # 只在視窗未關閉時執行清理
            cv2.destroyWindow(window_name)
            self._window_closed = True
    
    def stop_display(self):
        """Stop display"""
        self.running = False
        if self.display_thread and self.display_thread.is_alive():
            # 等待顯示執行緒結束，但不使用join
            while self.display_thread.is_alive():
                time.sleep(0.1)
        if not self._window_closed:
            cv2.destroyWindow("Target Data")
            self._window_closed = True
        print("Target data display closed")

class TCPClient:
    def __init__(self, config):
        """
        初始化TCP客戶端
        :param config: 配置字典，包含主機、埠口等設定
        """
        self.host = config.get("tcp_host", "192.168.144.200")
        self.port = config.get("tcp_port", 2000)
        self.client_socket = None
        self.running = False
        self.receiver_thread = None
        self.target_display = TargetDataDisplay()
        self.rtsp_stream = None
        self._closing = False  # 添加關閉標誌
        
        # 初始化RTSP串流
        auto_rtsp = config.get("auto_rtsp")
        if auto_rtsp:
            self.rtsp_stream = RTSPStream(auto_rtsp)
            self.target_display.set_rtsp_stream(self.rtsp_stream)
        
        # 設置ESC回調函數
        self.target_display.set_esc_callback(self.close)
    
    def connect(self):
        """連接到伺服器"""
        try:
            self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client_socket.settimeout(10)  # 僅用於連接階段
            
            print(f"正在連接到 {self.host}:{self.port}...")
            self.client_socket.connect((self.host, self.port))
            # 連接後將socket設為非阻塞
            self.client_socket.settimeout(None)
            print(f"已成功連接到 {self.host}:{self.port}")
            return True
        
        except ConnectionRefusedError:
            print(f"連接被拒絕，請確認伺服器 {self.host}:{self.port} 是否運行")
        except socket.timeout:
            print("連接超時")
        except Exception as e:
            print(f"連接錯誤: {e}")
        
        return False
    
    def start_receiver(self):
        """啟動接收執行緒"""
        self.running = True
        self.receiver_thread = threading.Thread(target=self._receive_loop)
        self.receiver_thread.daemon = True
        self.receiver_thread.start()
        print("已啟動數據接收執行緒，正在等待伺服器訊息...")
        
        # 啟動目標數據顯示
        self.target_display.start_display()
        
        # 啟動RTSP串流
        if self.rtsp_stream:
            self.rtsp_stream.start_stream()
    
    def parse_target_data(self, data):
        """
        解析封包中的目標數據
        :param data: 接收到的原始封包數據
        """
        try:
            # 確保數據長度足夠
            if len(data) < 24:
                print(f"封包長度不足，無法解析目標數據 (長度: {len(data)})")
                return
            
            # 解析角度數據
            # 3~4碼是z軸電機角
            z_angle = int.from_bytes(data[3:5], byteorder='little', signed=True)
            
            # 5~6碼是俯仰角
            pitch_angle = int.from_bytes(data[5:7], byteorder='little', signed=True)
            
            # 7~8碼是滾轉角
            roll_angle = int.from_bytes(data[7:9], byteorder='little', signed=True)
            
            # 9~10碼是航向角
            yaw_angle = int.from_bytes(data[9:11], byteorder='little', signed=True)
            
            # 11碼是測距是否開啟
            range_enabled = bool(data[11])
            
            # 12~13碼是目標距離
            distance = int.from_bytes(data[12:14], byteorder='little', signed=True)
            
            # 14~15碼是目標高度
            height = int.from_bytes(data[14:16], byteorder='little', signed=True)
            
            # 16~19碼是目標經度
            longitude_bytes = data[16:20]
            longitude = np.frombuffer(longitude_bytes, dtype=np.float32)[0]
            
            # 20~23碼是目標緯度
            latitude_bytes = data[20:24]
            latitude = np.frombuffer(latitude_bytes, dtype=np.float32)[0]
            
            # 更新顯示數據
            self.target_display.update_data(z_angle, pitch_angle, roll_angle, yaw_angle, range_enabled, 
                                           distance, height, longitude, latitude)
            
            # 顯示解析結果
            # print(f"\nTarget Data:")
            # print(f"- Z軸電機角: {z_angle*0.01}°")
            # print(f"- 俯仰角: {pitch_angle*0.01}°")
            # print(f"- 滾轉角: {roll_angle*0.01}°")
            # print(f"- 航向角: {yaw_angle*0.01}°")
            # print(f"- 測距狀態: {'開啟' if range_enabled else '關閉'}")
            # print(f"- 距離: {distance} m")
            # print(f"- 高度: {height} m")
            # print(f"- 經度: {longitude:.6f}°")
            # print(f"- 緯度: {latitude:.6f}°")
            
        except Exception as e:
            print(f"解析目標數據時發生錯誤: {e}")
    
    def _receive_loop(self):
        """接收訊息的循環"""
        while self.running and self.client_socket:
            try:
                # 嘗試接收數據
                data = self.client_socket.recv(4096)
                
                if not data:
                    print("伺服器已關閉連接")
                    self.running = False
                    break
                
                # 顯示接收到的數據
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                hex_data = data.hex()
                # print(f"\n[{timestamp}] 接收到數據 (HEX): {hex_data}")
                
                # 解析目標數據
                self.parse_target_data(data)
                
                # 打印分隔符以提高可讀性
                # print("------------------------------------------")
                
                # 重新顯示輸入提示
                # print("請輸入要發送的十六進制數據: ", end='', flush=True)
            
            except ConnectionResetError:
                print("\n連接被伺服器重置")
                self.running = False
                break
            except ConnectionAbortedError:
                print("\n連接被中止")
                self.running = False
                break
            except Exception as e:
                if self.running:  # 只在非主動關閉時顯示錯誤
                    print(f"\n接收數據時發生錯誤: {e}")
                    self.running = False
                break
    
    def send_message(self, message):
        """
        發送訊息到伺服器
        :param message: 要發送的訊息
        :return: 是否發送成功
        """
        if not self.client_socket or not self.running:
            print("未連接到伺服器")
            return False
        
        try:
            # 移除空格以處理類似"48 65 6C 6C 6F"的輸入
            hex_str = message.replace(" ", "")
            send_data = bytes.fromhex(hex_str)
            print(f"發送十六進制數據: {send_data.hex()}")
            
            self.client_socket.send(send_data)
            return True
        
        except ValueError as ve:
            print(f"數據格式錯誤: {ve}")
        except Exception as e:
            print(f"發送訊息時發生錯誤: {e}")
            self.running = False
        
        return False
    
    def close(self):
        """關閉連接"""
        if self._closing:  # 防止重複關閉
            return
        
        self._closing = True
        self.running = False
        
        # 關閉目標數據顯示
        if self.target_display:
            self.target_display.stop_display()
        
        # 關閉RTSP串流
        if self.rtsp_stream:
            self.rtsp_stream.stop_stream()
        
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None
        
        if self.receiver_thread and self.receiver_thread.is_alive():
            self.receiver_thread.join(timeout=1.0)
        
        print("所有連接已關閉")
    
    def run_interactive(self):
        """運行互動模式"""
        if not self.connect():
            return
        
        # 啟動接收執行緒
        self.start_receiver()
        
        try:
            while self.running:
                try:
                    message = input("請輸入要發送的十六進制數據 (例如: 48656C6C6F): ")
                    
                    if message.lower() in ['exit', 'quit']:
                        break
                    
                    if message:  # 只有當有內容時才發送
                        if not self.send_message(message):
                            break
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\n接收到中斷信號，正在退出...")
                    break
        
        except Exception as e:
            print(f"發生錯誤: {e}")
        finally:
            self.close()

def parse_packet(data, verbose=True):
    """
    解析TCP封包內容（示例函數，可根據實際協議擴展）
    :param data: 二進制數據
    :param verbose: 是否顯示詳細信息
    :return: 解析結果
    """
    result = {}
    
    # 這裡只是一個簡單示例
    # 在實際應用中，您需要根據您的協議格式進行定制
    if len(data) < 4:
        return {"error": "數據太短，無法解析"}
    
    # 假設前4字節是某種頭部信息
    header = int.from_bytes(data[:4], byteorder='big')
    result["header"] = header
    
    # 假設接下來的數據是訊息內容
    payload = data[4:]
    result["payload_hex"] = payload.hex()
    
    # 列印詳細解析結果
    if verbose:
        print("\n數據封包解析結果:")
        print(f"- 封包長度: {len(data)}字節")
        print(f"- 標頭值: 0x{header:08X}")
        print(f"- 負載(HEX): {result['payload_hex']}")
    
    return result

def check_dependencies():
    """檢查必要的依賴庫是否已安裝"""
    try:
        import cv2
        print("OpenCV已安裝，版本:", cv2.__version__)
        return True
    except ImportError:
        print("錯誤: 未找到OpenCV庫")
        print("請使用以下命令安裝OpenCV:")
        print("pip install opencv-python")
        return False

def create_default_config():
    """創建預設配置文件"""
    default_config = {
        "tcp_host": "127.0.0.1",
        "tcp_port": 9999,
        "auto_rtsp": "rtsp://admin:admin@192.168.1.1:554/stream1",  # 添加RTSP URL配置
        "auto_connect": True,
        "buffer_size": 4096,
        "reconnect_attempts": 3,
        "reconnect_delay": 5
    }
    
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(default_config, f, indent=4, ensure_ascii=False)
    
    print("已創建預設配置文件: config.json")
    return default_config

def load_config(config_path="config.json"):
    """
    加載配置文件
    :param config_path: 配置文件路徑
    :return: 配置字典
    """
    if not os.path.exists(config_path):
        print(f"配置文件 {config_path} 不存在，將創建預設配置文件")
        return create_default_config()
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        print(f"已成功載入配置文件: {config_path}")
        return config
    except Exception as e:
        print(f"載入配置文件時發生錯誤: {e}")
        print("將使用預設配置")
        return create_default_config()

def main():
    # 檢查依賴
    if not check_dependencies():
        return
    
    # 解析命令行參數，檢查是否指定配置文件
    config_path = "config.json"
    if len(sys.argv) >= 2:
        config_path = sys.argv[1]
    
    # 載入配置
    config = load_config(config_path)
    
    # 顯示已載入的配置
    print("當前配置:")
    for key, value in config.items():
        print(f"- {key}: {value}")
    
    # 創建並運行客戶端
    client = TCPClient(config)
    
    # 檢查是否設定自動連接
    if config.get("auto_connect", True):
        client.run_interactive()
    else:
        print("自動連接已禁用，程式退出")

if __name__ == "__main__":
    main()