from ultralytics import YOLO
import cv2
import time
import sys
import json
import paho.mqtt.client as mqtt
# -------------------------------
# 使用方式：
# python predict_rtsp.py <模型路徑> <RTSP網址> [幀率N]
# -------------------------------
# python .\rtsp_predict.py .\20250324_yx\best.pt rtsp://localhost:8554/mystream 3 
if len(sys.argv) < 3:
    print("用法：python rtsp_predict.py <模型路徑> <RTSP網址> [每N幀推論一次]")
    print("範例：python rtsp_predict.py 20250324_yx/best.pt rtsp://localhost:8554/mystream 3")
    sys.exit(1)

model_path = sys.argv[1]
rtsp_url = sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3  # 每N幀推論一次，預設3

mqtt_client = mqtt.Client()
mqtt_client.connect("192.168.5.78", 1883)  # ⚠️ 改成你 Orin NX 的 IP 或 MQTT Broker IP
last_sent = None

# 載入模型
print(f"✅ 載入模型：{model_path}")
model = YOLO(model_path)

# 連接 RTSP 串流
print(f"📡 嘗試連接 RTSP：{rtsp_url}")
if rtsp_url.startswith("rtspsrc"):
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_GSTREAMER)
else:
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

if not cap.isOpened():
    print("❌ 無法開啟 RTSP 串流")
    sys.exit(1)

frame_count = 0
max_size = 640  # 你可改成適合你模型的輸入大小

print("🚀 開始串流分析... 按 Q 可關閉")

while cap.isOpened():
    cap.grab()
    ret, frame = cap.retrieve()
    if not ret:
        print("⚠️ 無法讀取影像，結束")
        break
    
    frame_count += 1


    if frame_count % N == 0:
        # 偵測時輸入尺寸（例如模型 imgsz 設定的）
        orig_width = 1920
        orig_height = 1080

        # 目標影像尺寸（實際串流畫面用來控制機構等的大小）
        target_width = 320
        target_height = 240
        
        # ✅ 檢查並轉換成三通道（RGB / BGR）
        if frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        print("原始畫面尺寸：", frame.shape)  # (高, 寬, 通道)
        
        # 執行推論
        results = model.predict(source=frame, conf=0.25, save=False, imgsz=max_size)
        r = results[0]
        print("YOLO 模型輸入大小：", r.orig_shape)  # (H, W)
        # 印出資訊 + 框火焰
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            x_center = int((x1 + x2) / 2)
            y_center = int((y1 + y2) / 2)

            print(f"🔥 火焰偵測 - 中心點座標: ({x_center}, {y_center}) 信心度: {conf:.2f} 類別: {cls}")
                 
            # 📐 比例轉換到目標尺寸
            scale_x = target_width / orig_width
            scale_y = target_height / orig_height
            converted_x = int(x_center * scale_x)
            converted_y = int(y_center * scale_y)

            print(f"👉 換算成 {target_width}x{target_height} 畫面後座標: ({converted_x}, {converted_y})")

            # 顯示結果畫面
            #annotated = r.plot()
            #cv2.imshow("YOLOv8 RTSP Fire Detection", annotated)

            # ✅ 顯示畫面（縮小顯示）
            annotated = r.plot()
            display = cv2.resize(annotated, (640, 360))  # 可改成你想要的顯示大小
            cv2.imshow("🔥 Fire Detection Preview", display)

            payload = {
                "x": converted_x,
                "y": converted_y,
                "tag": cls,
                "conf": float(conf)
            }
            try:
                if (x_center, y_center) != last_sent:
                    mqtt_client.publish("fire/XY_target", json.dumps(payload))
                    last_sent = (x_center, y_center)
            except Exception as e:
                print(f"❌ MQTT 發佈失敗: {e}")
    else:
        # 顯示原始畫面（無推論）
        print("NO Fire!")
        resized = cv2.resize(frame, (640, 360))
        cv2.imshow("YOLOv8 RTSP Fire Detection", resized)
        #cv2.imshow("YOLOv8 RTSP Fire Detection", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        print("🛑 手動中止")
        break

cap.release()
cv2.destroyAllWindows()
