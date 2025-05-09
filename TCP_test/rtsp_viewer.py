import cv2
import time
from datetime import datetime
import os
import json

class RTSPViewer:
    def __init__(self, rtsp_url):
        """
        初始化RTSP串流查看器
        :param rtsp_url: RTSP串流URL
        """
        self.rtsp_url = rtsp_url
        self.running = False
        self.cap = None
        self.window_name = "RTSP Stream"
        
    def start(self):
        """啟動RTSP串流"""
        try:
            # 設置FFMPEG參數
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp|reorder_queue_size;0|max_delay;500000|fflags;nobuffer|flags;low_delay'
            
            # 創建視頻捕獲對象
            self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
            
            # 設置緩衝區大小
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            # 檢查連接是否成功
            if not self.cap.isOpened():
                print(f"無法連接到RTSP串流: {self.rtsp_url}")
                return False
            
            # 創建視窗
            cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.window_name, 800, 600)
            cv2.moveWindow(self.window_name, 50, 50)
            
            # 開始主循環
            self.running = True
            self._main_loop()
            
            return True
            
        except Exception as e:
            print(f"啟動RTSP串流時發生錯誤: {e}")
            return False
    
    def _main_loop(self):
        """主循環"""
        frame_count = 0
        start_time = time.time()
        fps = 0
        error_count = 0
        max_errors = 5
        
        while self.running:
            try:
                # 讀取幀
                ret, frame = self.cap.read()
                
                if not ret:
                    error_count += 1
                    print(f"讀取幀失敗 (錯誤 {error_count}/{max_errors})")
                    
                    if error_count >= max_errors:
                        print("嘗試重新連接...")
                        self.cap.release()
                        time.sleep(2)
                        self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        if not self.cap.isOpened():
                            print("重新連接失敗")
                            break
                        error_count = 0
                    continue
                
                # 重置錯誤計數
                error_count = 0
                
                # 計算FPS
                frame_count += 1
                elapsed_time = time.time() - start_time
                if elapsed_time > 1.0:
                    fps = frame_count / elapsed_time
                    frame_count = 0
                    start_time = time.time()
                
                # 添加信息到畫面
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, current_time, (10, 70), 
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # 顯示畫面
                cv2.imshow(self.window_name, frame)
                
                # 檢查按鍵
                key = cv2.waitKey(1)
                if key == ord('q') or key == 27:  # 'q' 或 ESC 退出
                    print("用戶關閉視窗")
                    break
                
            except Exception as e:
                print(f"處理幀時發生錯誤: {e}")
                break
    
    def stop(self):
        """停止RTSP串流"""
        self.running = False
        if self.cap:
            self.cap.release()
        cv2.destroyWindow(self.window_name)
        print("RTSP串流已關閉")

def load_config():
    """從config檔案載入設定"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get('auto_rtsp')
    except Exception as e:
        print(f"讀取config檔案時發生錯誤: {e}")
        return None

def main():
    # 從config檔案讀取RTSP URL
    rtsp_url = load_config()
    
    if not rtsp_url:
        print("無法從config檔案讀取auto_rtsp設定")
        return
    
    # 創建並啟動查看器
    viewer = RTSPViewer(rtsp_url)
    viewer.start()

if __name__ == "__main__":
    main() 