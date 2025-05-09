import sys
import os
import json
import cv2
import numpy as np
import time
import threading
from ktgGimbalControl import KTGGimbalController, ControlUnit, EOCommand
from display import TargetDataDisplay
from video import RTSPStream
from mqtt_client import MQTTClient

def calculate_speeds(x, y, frame_width, frame_height):
    """
    根據目標位置計算雲台速度
    :param x: 目標X座標
    :param y: 目標Y座標
    :param frame_width: 視窗寬度
    :param frame_height: 視窗高度
    :return: (yaw_speed, pitch_speed)
    """
    center_x = frame_width // 2
    center_y = frame_height // 2
    dx = x - center_x
    dy = y - center_y
    distance = np.sqrt(dx**2 + dy**2)
    dead_zone = 30
    if distance < dead_zone:
        return 0, 0
    max_speed = 100 
    speed_ratio = min(1.0, distance / (frame_width // 2))
    if distance > 300:
    	speed = max_speed * np.sqrt(speed_ratio)
    else:
    	speed = max_speed * speed_ratio
    angle = np.arctan2(dy, dx)
    yaw_speed = speed * np.cos(angle)
    pitch_speed = -speed * np.sin(angle)
    return yaw_speed, pitch_speed

def on_mqtt_message(topic: str, payload: dict):
    """
    MQTT 消息處理回調函數
    """
    global controller, current_frame
    try:
        print(f"收到MQTT消息: {topic} - {payload}")  # 添加詳細日誌
        if isinstance(payload, dict) and "x" in payload and "y" in payload:
            try:
                # 確保轉換為數字類型
                x = int(float(payload["x"]))
                y = int(float(payload["y"]))
                
                if current_frame is not None:
                    yaw_speed, pitch_speed = calculate_speeds(x, y, current_frame.shape[1], current_frame.shape[0])
                    print(f"MQTT: 追蹤目標座標 - X: {x}, Y: {y}")
                    print(f"MQTT: 雲台控制命令 - Yaw: {yaw_speed:.1f}, Pitch: {pitch_speed:.1f}")
                    
                    # 確認控制器存在且連接狀態
                    if controller:
                        controller.eo_control_gimbal(yaw_speed, pitch_speed)
                    else:
                        print("MQTT: 雲台控制器未初始化")
                else:
                    print("MQTT: current_frame 未初始化，無法計算速度")
            except (ValueError, TypeError) as e:
                print(f"MQTT: 座標格式錯誤: {e}")
        else:
            print(f"MQTT: 消息中缺少必要的 x 或 y 資料，或格式不正確: {payload}")
    except Exception as e:
        print(f"MQTT: 處理消息時發生錯誤: {e}", file=sys.stderr)

def main():
    global controller, current_frame, mouse_x, mouse_y, is_tracking
    
    config_path = "config.json"
    if len(sys.argv) >= 2:
        config_path = sys.argv[1]

    with open(config_path, 'r') as f:
        config = json.load(f)

    print("Current configuration:")
    for key, value in config.items():
        print(f"- {key}: {value}")

    controller = KTGGimbalController(
        ip=config.get("tcp_host", "192.168.144.200"),
        port=config.get("tcp_port", 2000)
    )

    target_display = TargetDataDisplay()
    mqtt_client = MQTTClient(config, on_mqtt_message)

    rtsp_stream = None
    if config.get("auto_rtsp"):
        rtsp_config = config.get("rtsp", {})
        rtsp_stream = RTSPStream(
            config["auto_rtsp"],
            target_width=rtsp_config.get("width", 320),
            target_height=rtsp_config.get("height", 240),
            target_fps=rtsp_config.get("fps", 15)
        )
        target_display.set_rtsp_stream(rtsp_stream)

    current_frame = None
    mouse_x, mouse_y = 0, 0
    is_tracking = False

    def on_mouse(event, x, y, flags, param):
        global mouse_x, mouse_y, is_tracking, current_frame
        if event == cv2.EVENT_LBUTTONDOWN:
            is_tracking = True
        elif event == cv2.EVENT_LBUTTONUP:
            is_tracking = False
            controller.eo_control_gimbal(0, 0)
            print("停止雲台移動")
        if is_tracking and current_frame is not None:
            mouse_x, mouse_y = x, y
            yaw_speed, pitch_speed = calculate_speeds(x, y, current_frame.shape[1], current_frame.shape[0])
            controller.eo_control_gimbal(yaw_speed, pitch_speed)
            print(f"雲台控制命令 - Yaw: {yaw_speed:.1f}, Pitch: {pitch_speed:.1f}")

    control_mode = config.get("control_mode", "mqtt").lower()
    if control_mode not in ["mqtt", "mouse"]:
        print(f"未知的控制模式: {control_mode}, 使用預設模式 'mqtt'")
        control_mode = "mqtt"

    if config.get("auto_connect", True):
        try:
            if controller.connect():
                print("Connected to gimbal")
                
                if control_mode == "mqtt":
                    print("使用 MQTT 模式控制 gimbal")
                    if mqtt_client.enabled:
                        mqtt_client.connect()
                        
                        # 啟動RTSP串流顯示，用於MQTT模式下的視覺監控
                        if rtsp_stream:
                            rtsp_stream.start_stream()
                            cv2.namedWindow("RTSP Stream (MQTT Control)")
                            
                            try:
                                print("程式運行中，按 'q' 鍵終止...")
                                running = True
                                while running:
                                    frame = rtsp_stream.get_frame()
                                    if frame is not None:
                                        current_frame = frame
                                        cv2.imshow("RTSP Stream (MQTT Control)", frame)
                                    
                                    key = cv2.waitKey(1) & 0xFF
                                    if key == ord('q'):
                                        running = False
                            except KeyboardInterrupt:
                                print("接收到終止信號，準備結束程式...")
                        else:
                            # 如果沒有RTSP串流，仍然保持程式運行
                            try:
                                print("MQTT模式運行中，按Ctrl+C終止...")
                                while True:
                                    time.sleep(1)
                            except KeyboardInterrupt:
                                print("接收到終止信號，準備結束程式...")
                        
                elif control_mode == "mouse":
                    print("使用滑鼠位置模式控制 gimbal")
                    if rtsp_stream:
                        rtsp_stream.start_stream()
                        cv2.namedWindow("RTSP Stream")
                        cv2.setMouseCallback("RTSP Stream", on_mouse)
                        running = True
                        while running:
                            try:
                                frame = rtsp_stream.get_frame()
                                if frame is not None:
                                    current_frame = frame
                                    if is_tracking:
                                        cv2.circle(frame, (mouse_x, mouse_y), 5, (0, 255, 0), -1)
                                        cv2.arrowedLine(frame, (frame.shape[1] // 2, frame.shape[0] // 2),
                                                        (mouse_x, mouse_y), (0, 255, 0), 2)
                                    cv2.imshow("RTSP Stream", frame)
                                key = cv2.waitKey(1) & 0xFF
                                if key == ord('q'):
                                    running = False
                            except Exception as e:
                                print(f"滑鼠控制錯誤: {e}")
                                running = False
                        cv2.destroyAllWindows()

        except Exception as e:
            print(f"Error: {e}")
        finally:
            if mqtt_client:
                try:
                    mqtt_client.disconnect()
                except:
                    pass
            if rtsp_stream:
                try:
                    rtsp_stream.stop_stream()
                except:
                    pass
            try:
                controller.disconnect()
            except:
                pass
            cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
