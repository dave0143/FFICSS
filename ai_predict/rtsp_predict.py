import argparse
import os
import configparser
from ultralytics import YOLO
import cv2
import json
import paho.mqtt.client as mqtt

# 載入設定檔
config = configparser.ConfigParser()
config.read('ai_predict.conf')

# argparse 優先
parser = argparse.ArgumentParser()
parser.add_argument('--weights', default=None)
parser.add_argument('--rtsp', default=None)
parser.add_argument('--mqtt-host', default=None)
parser.add_argument('--mqtt-port', type=int, default=None)
parser.add_argument('--frame-skip', type=int, default=None)
args = parser.parse_args()

# ✅ 優先順序：CLI > .conf > ENV > 預設值
def get_config(key, section, default=None, cast=str):
    val = getattr(args, key)
    if val is not None:
        return val
    if section in config and key in config[section]:
        return cast(config[section][key])
    return cast(os.environ.get(key.upper().replace('-', '_'), default))

weights = get_config('weights', 'ai', default='best.pt')
rtsp_url = get_config('rtsp', 'ai', default='rtsp://localhost:8554/mystream')
mqtt_host = get_config('mqtt_host', 'mqtt', default='localhost')
mqtt_port = get_config('mqtt_port', 'mqtt', default=1883, cast=int)
frame_skip = get_config('frame_skip', 'ai', default=3, cast=int)

# MQTT
mqtt_client = mqtt.Client()
mqtt_client.connect(mqtt_host, mqtt_port)
last_sent = None

# 模型
print(f"✅ 模型：{weights}")
model = YOLO(weights)

# RTSP
print(f"📡 RTSP：{rtsp_url}")
cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

if not cap.isOpened():
    print("❌ 無法開啟 RTSP")
    exit(1)

frame_count = 0
max_size = 640
print("🚀 開始分析... Q 結束")

while cap.isOpened():
    cap.grab()
    ret, frame = cap.retrieve()
    if not ret:
        print("⚠️ 無法讀取影像")
        break

    frame_count += 1

    if frame_count % frame_skip == 0:
        results = model.predict(source=frame, conf=0.25, save=False, imgsz=max_size)
        r = results[0]
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            x_center = int((x1 + x2) / 2)
            y_center = int((y1 + y2) / 2)
            print(f"🔥 ({x_center},{y_center}) 信心度: {conf:.2f}")

            annotated = r.plot()
            cv2.imshow("YOLOv8 RTSP Fire Detection", annotated)

            payload = {"x": x_center, "y": y_center, "tag": cls, "conf": float(conf)}
            try:
                if (x_center, y_center) != last_sent:
                    mqtt_client.publish("fire/XY_target", json.dumps(payload))
                    last_sent = (x_center, y_center)
            except Exception as e:
                print(f"❌ MQTT 發佈失敗: {e}")
    else:
        cv2.imshow("YOLOv8 RTSP Fire Detection", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        print("🛑 手動中止")
        break

cap.release()
cv2.destroyAllWindows()

