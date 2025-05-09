import cv2
from ultralytics import YOLO

gst_str = (
    'rtspsrc location=rtsp://admin:53373957@192.168.144.108:554/cam/realmonitor?channel=1&subtype=2 latency=0 '
    '! rtph264depay ! h264parse ! nvv4l2decoder ! nvvidconv '
    '! video/x-raw, format=BGRx ! videoconvert ! appsink'
)

model = YOLO("model/Fire_Smoke_95556_other_yolov8n_20250322_1621_640.pt")

cap = cv2.VideoCapture(gst_str, cv2.CAP_GSTREAMER)
if not cap.isOpened():
    print("❌ GStreamer 串流打不開")
    exit(1)

while True:
    ret, frame = cap.read()
    if not ret:
        print("⚠️ 無法讀取畫面")
        break

    results = model.predict(source=frame, imgsz=640, conf=0.25)
    for box in results[0].boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        conf = float(box.conf[0])
        cls = int(box.cls[0])
        print(f"🔥 Detected: class={cls}, conf={conf:.2f}, center=({(x1+x2)/2:.0f}, {(y1+y2)/2:.0f})")

cap.release()

