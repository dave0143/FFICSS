import tkinter as tk
from tkinter import ttk
import json
import time
from datetime import datetime
import threading
import queue
import cv2
import numpy as np

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
        self.on_esc_pressed = None  # Callback function for ESC key
        self._window_closed = False  # Window closed flag
        self._stop_event = threading.Event()  # 添加停止事件
    
    def set_rtsp_stream(self, rtsp_stream):
        """Set RTSP stream"""
        self.rtsp_stream = rtsp_stream
    
    def set_esc_callback(self, callback):
        """Set ESC key callback function"""
        self.on_esc_pressed = callback
    
    def put_text(self, img, text, position, text_color=(255, 255, 255)):
        """
        Add text to image
        :param img: OpenCV image
        :param text: text to display
        :param position: text position (x, y)
        :param text_color: text color
        """
        cv2.putText(img, text, position, cv2.FONT_HERSHEY_SIMPLEX, 0.7, text_color, 1)
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
        self._stop_event.clear()  # 清除停止事件
        self.display_thread = threading.Thread(target=self._display_loop)
        self.display_thread.daemon = True
        self.display_thread.start()
        print("Target data display thread started")
        return True
    
    def _display_loop(self):
        """Display loop"""
        # Create display window
        window_name = "Target Data"
        window_size = (1200, 800)  # Increased window height for the image
        
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, *window_size)
        cv2.moveWindow(window_name, 50, 50)
        
        # Create display background
        background = np.zeros((window_size[1], window_size[0], 3), dtype=np.uint8)
        background[:] = (30, 30, 30)  # Dark gray background
        
        # Main display loop
        while self.running and not self._stop_event.is_set():
            try:
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
                row_height = 45  # Reduced row height
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
                key = cv2.waitKey(1)  # Reduced wait time from 100ms to 1ms
                if key == ord('q') or key == 27:  # 'q' or ESC to exit
                    self.running = False
                    if self.on_esc_pressed:  # Call ESC callback function
                        self.on_esc_pressed()
                    break
                    
            except Exception as e:
                print(f"顯示循環錯誤: {e}")
                self.running = False
                break
        
        # Clean up resources
        if not self._window_closed:  # Only clean up if window not already closed
            cv2.destroyWindow(window_name)
            self._window_closed = True
            
    def stop_display(self):
        """Stop display"""
        self.running = False
        self._stop_event.set()  # 設置停止事件
        if self.display_thread and self.display_thread.is_alive():
            # Wait for display thread to end without using join
            while self.display_thread.is_alive():
                time.sleep(0.1)
        if not self._window_closed:
            cv2.destroyWindow("Target Data")
            self._window_closed = True
        print("Target data display closed")
