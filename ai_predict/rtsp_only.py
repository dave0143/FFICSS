import cv2
import time

# 替換成你的 RTSP URL
#rtsp_url = "rtsp://192.168.5.78:8554/proxy_stream"
rtsp_url = "rtsp://admin:53373957@192.168.144.108:554/cam/realmonitor?channel=1&subtype=2"

gst_pipeline = (
    f"rtspsrc location={rtsp_url} latency=50 ! "
    "rtph264depay ! h264parse ! nvv4l2decoder ! "
    "videoconvert ! video/x-raw, width=320, height=240 ! appsink"
)

cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
#cap = cv2.VideoCapture(rtsp_url)

if not cap.isOpened():
    print("❌ 無法開啟 GStreamer 管線")
    exit()

cv2.namedWindow("RTSP with GStreamer", cv2.WINDOW_NORMAL)
cv2.resizeWindow("RTSP with GStreamer", 320, 240)

while True:
    start = time.time()
    ret, frame = cap.read()
    if not ret:
        print("⚠️ 串流讀取錯誤")
        break

    cv2.imshow("RTSP with GStreamer", frame)

    fps = 1.0 / (time.time() - start)
    print(f"⚡ GStreamer FPS: {fps:.2f}")

    if cv2.waitKey(1) == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()

