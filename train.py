from multiprocessing import freeze_support
from ultralytics import YOLO

def main():
    model = YOLO("yolov8n.pt")

    model.train(
        data="datasets/helmet/data.yaml",
        epochs=50,
        imgsz=640,
        batch=8,
        device=0,
        workers=0
    )

if __name__ == "__main__":
    freeze_support()
    main()