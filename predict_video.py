from ultralytics import YOLO

model = YOLO(
    "runs/detect/train-3/weights/best.pt"
)

model(
    source="videos/traffic.mp4",
    save=True
)