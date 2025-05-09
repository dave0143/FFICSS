import cv2
import threading
import time
from datetime import datetime
import queue

class RTSPStream:
    """RTSP Stream Class"""
    def __init__(self, rtsp_url, max_retries=3, retry_delay=5, target_width=640, target_height=480, target_fps=15):
        """
        Initialize RTSP stream
        :param rtsp_url: RTSP stream URL
        :param max_retries: Maximum number of reconnection attempts
        :param retry_delay: Delay between reconnection attempts in seconds
        :param target_width: Target frame width
        :param target_height: Target frame height
        :param target_fps: Target frame rate
        """
        self.rtsp_url = rtsp_url
        self.cap = None
        self.running = False
        self.stream_thread = None
        self.frame = None
        self.frame_lock = threading.Lock()
        self.last_frame_time = None
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.frame_queue = queue.Queue(maxsize=2)
        self.retry_count = 0
        self.last_error_time = None
        self.target_width = target_width
        self.target_height = target_height
        self.target_fps = target_fps
    
    def start_stream(self):
        """Start stream"""
        if self.running:
            print("RTSP stream is already running")
            return False
        
        try:
            self.cap = cv2.VideoCapture(self.rtsp_url)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # 只保留最新一幀
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)  # 設置目標幀率
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))  # 使用 MJPG 編碼

            if not self.cap.isOpened():
                print(f"Could not open RTSP stream: {self.rtsp_url}")
                return False
            
            self.running = True
            self.stream_thread = threading.Thread(target=self._stream_loop)
            self.stream_thread.daemon = True
            self.stream_thread.start()
            print("RTSP stream thread started")
            return True
            
        except Exception as e:
            print(f"Error starting RTSP stream: {e}")
            return False
    
    def _stream_loop(self):
        """Stream loop"""
        while self.running:
            try:
                if not self.cap or not self.cap.isOpened():
                    if self._should_reconnect():
                        self._reconnect()
                        continue
                    else:
                        break

                ret, frame = self.cap.read()
                if ret:
                    # 調整影像大小
                    frame = cv2.resize(frame, (self.target_width, self.target_height))
                    
                    with self.frame_lock:
                        self.frame = frame
                        self.last_frame_time = datetime.now()
                        # 清空隊列並放入新幀
                        while not self.frame_queue.empty():
                            try:
                                self.frame_queue.get_nowait()
                            except queue.Empty:
                                break
                        try:
                            self.frame_queue.put_nowait(frame)
                        except queue.Full:
                            pass
                    self.retry_count = 0  # 重置重試計數
                else:
                    print("Could not read RTSP frame")
                    if self._should_reconnect():
                        self._reconnect()
                    else:
                        time.sleep(0.01)
                
            except Exception as e:
                print(f"Error reading RTSP frame: {e}")
                self.last_error_time = datetime.now()
                if self._should_reconnect():
                    self._reconnect()
                else:
                    time.sleep(0.01)
    
    def _should_reconnect(self):
        """Check if we should attempt to reconnect"""
        if self.retry_count >= self.max_retries:
            return False
        
        if self.last_error_time:
            time_since_error = (datetime.now() - self.last_error_time).total_seconds()
            if time_since_error < self.retry_delay:
                return False
        
        return True
    
    def _reconnect(self):
        """Attempt to reconnect to the RTSP stream"""
        self.retry_count += 1
        print(f"Attempting to reconnect to RTSP stream (attempt {self.retry_count}/{self.max_retries})")
        
        if self.cap:
            self.cap.release()
        
        try:
            self.cap = cv2.VideoCapture(self.rtsp_url)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            
            if not self.cap.isOpened():
                raise Exception("Failed to open RTSP stream")
            
            print("Successfully reconnected to RTSP stream")
            self.last_error_time = None
            
        except Exception as e:
            print(f"Reconnection failed: {e}")
            time.sleep(self.retry_delay)
    
    def get_frame(self):
        """Get latest frame"""
        try:
            # 嘗試從隊列獲取最新幀
            frame = self.frame_queue.get_nowait()
            return frame
        except queue.Empty:
            # 如果隊列為空，返回當前幀
            with self.frame_lock:
                if self.frame is not None:
                    return self.frame.copy()
            return None
    
    def stop_stream(self):
        """Stop stream"""
        self.running = False
        if self.cap:
            self.cap.release()
        if self.stream_thread and self.stream_thread.is_alive():
            self.stream_thread.join(timeout=3.0)
        print("RTSP stream stopped")
