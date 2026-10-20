"""
AI Traffic Violation Detector
- Phát hiện không đội mũ bảo hiểm (dùng model đã train)
- Phát hiện xe máy chở 3 người trở lên (dùng YOLOv8 pretrained COCO)
"""

import os
import cv2
import numpy as np
from ultralytics import YOLO
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent

# Model 1: detect mũ bảo hiểm (đã train)
HELMET_MODEL_PATH = BASE_DIR / "runs" / "detect" / "train-3" / "weights" / "best.pt"

# Model 2: detect người + xe máy (COCO pretrained)
COCO_MODEL_PATH = BASE_DIR / "yolov8n.pt"

# ── Màu bounding box ───────────────────────────────────────────────────────
COLOR_OK       = (0, 200, 0)      # Xanh lá  – hợp lệ
COLOR_WARN     = (0, 165, 255)    # Cam      – cảnh báo
COLOR_VIOL     = (0, 0, 220)      # Đỏ       – vi phạm

# ── Tên class model helmet ─────────────────────────────────────────────────
HELMET_CLASS_WITH    = "With Helmet"
HELMET_CLASS_WITHOUT = "Without Helmet"


class TrafficViolationDetector:
    """
    Phát hiện vi phạm giao thông:
      1. Không đội mũ bảo hiểm
      2. Xe máy chở từ 3 người trở lên
    """

    def __init__(self, conf_helmet: float = 0.4, conf_coco: float = 0.45):
        logger.info("Đang tải model YOLO...")

        # Model mũ bảo hiểm
        if not HELMET_MODEL_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy model: {HELMET_MODEL_PATH}")
        self.helmet_model = YOLO(str(HELMET_MODEL_PATH))

        # Model COCO (person + motorcycle)
        self.coco_model = YOLO(str(COCO_MODEL_PATH))

        self.conf_helmet = conf_helmet
        self.conf_coco   = conf_coco

        # Lấy tên class của helmet model
        self.helmet_names = self.helmet_model.names   # {0: 'With Helmet', 1: 'Without Helmet'}
        logger.info(f"Helmet classes: {self.helmet_names}")

        # Lấy class IDs động từ COCO model
        coco_names = self.coco_model.names  # {0: 'person', 1: 'bicycle', ...}
        logger.info(f"COCO classes (first 10): { {k: v for k, v in coco_names.items() if k < 10} }")
        self.coco_person_id     = next((k for k, v in coco_names.items() if v == "person"), 0)
        self.coco_motorcycle_id = next((k for k, v in coco_names.items() if v == "motorcycle"), 3)
        logger.info(f"person id={self.coco_person_id}, motorcycle id={self.coco_motorcycle_id}")

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def detect_image(self, image_path: str) -> dict:
        """Phân tích 1 ảnh, trả về dict kết quả + đường dẫn ảnh annotated."""
        img_path = Path(image_path)
        frame = cv2.imread(str(img_path))
        if frame is None:
            raise ValueError(f"Không đọc được ảnh: {image_path}")

        violations, annotated = self._process_frame(frame)

        # Lưu ảnh đã annotate
        out_path = img_path.parent / f"annotated_{img_path.name}"
        cv2.imwrite(str(out_path), annotated)

        return {
            "violations": violations,
            "annotated_path": str(out_path),
            "summary": self._build_summary(violations),
        }

    def detect_video(self, video_path: str, output_path: str = None,
                     frame_skip: int = 3, progress_cb=None) -> dict:
        """
        Phân tích video theo từng frame.
        frame_skip: xử lý 1 trong mỗi N frame (tăng tốc).
        progress_cb: callback(current_frame, total_frames)
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Không tìm thấy video: {video_path}")

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError(f"Không mở được video: {video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps          = cap.get(cv2.CAP_PROP_FPS) or 30
        width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Output video
        if output_path is None:
            output_path = str(video_path.parent / f"annotated_{video_path.name}")

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out    = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        all_violations = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                violations, annotated = self._process_frame(frame)
                if violations:
                    for v in violations:
                        v["frame"] = frame_idx
                    all_violations.extend(violations)
                out.write(annotated)
            else:
                out.write(frame)

            frame_idx += 1
            if progress_cb:
                progress_cb(frame_idx, total_frames)

        cap.release()
        out.release()

        return {
            "violations": all_violations,
            "annotated_path": output_path,
            "total_frames": total_frames,
            "fps": fps,
            "summary": self._build_summary(all_violations),
        }

    # ──────────────────────────────────────────────────────────────────────
    # Core detection logic
    # ──────────────────────────────────────────────────────────────────────

    def _process_frame(self, frame: np.ndarray) -> tuple[list[dict], np.ndarray]:
        """
        Chạy cả 2 model trên 1 frame.
        Trả về: (danh_sách_vi_phạm, frame_đã_annotate)
        """
        annotated = frame.copy()
        violations = []

        # 1) Detect người + xe máy (COCO model)
        coco_results = self.coco_model(
            frame, conf=self.conf_coco, verbose=False,
            classes=[self.coco_person_id, self.coco_motorcycle_id]
        )[0]

        persons     = []   # [{box, conf}]
        motorcycles = []   # [{box, conf}]

        for box in coco_results.boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            xyxy   = box.xyxy[0].cpu().numpy().astype(int)

            if cls_id == self.coco_person_id:
                persons.append({"box": xyxy, "conf": conf})
            elif cls_id == self.coco_motorcycle_id:
                motorcycles.append({"box": xyxy, "conf": conf})

        # 2) Detect mũ bảo hiểm
        helmet_results = self.helmet_model(frame, conf=self.conf_helmet, verbose=False)[0]

        helmets_with    = []  # box
        helmets_without = []  # box

        for box in helmet_results.boxes:
            cls_id = int(box.cls[0])
            xyxy   = box.xyxy[0].cpu().numpy().astype(int)
            name   = self.helmet_names.get(cls_id, "")

            if name == HELMET_CLASS_WITHOUT or "without" in name.lower():
                helmets_without.append(xyxy)
                self._draw_box(annotated, xyxy, "Không MBH", COLOR_VIOL, thickness=2)
                violations.append({
                    "type": "no_helmet",
                    "label": "Không đội mũ bảo hiểm",
                    "box": xyxy.tolist(),
                    "conf": float(box.conf[0]),
                })
            else:
                helmets_with.append(xyxy)
                self._draw_box(annotated, xyxy, "Có MBH", COLOR_OK, thickness=1)

        # 3) Phát hiện xe chở 3+ người
        for moto in motorcycles:
            moto_box   = moto["box"]
            moto_conf  = moto["conf"]
            # Mở rộng vùng xe máy lên 1.3x chiều cao để bắt người ngồi
            expanded   = self._expand_box(moto_box, frame.shape, scale_y=1.35, scale_x=1.15)
            people_on  = self._count_persons_on_motorcycle(persons, expanded)

            color = COLOR_OK
            label = f"Xe máy ({people_on} người)"

            if people_on >= 3:
                color = COLOR_VIOL
                label = f"Chở 3! ({people_on} người)"
                violations.append({
                    "type": "overloaded_motorcycle",
                    "label": f"Xe máy chở {people_on} người (vượt quy định)",
                    "box": moto_box.tolist(),
                    "conf": moto_conf,
                    "people_count": people_on,
                })
            elif people_on == 2:
                color = COLOR_OK
            elif people_on == 0:
                color = COLOR_WARN
                label = "Xe máy"

            self._draw_box(annotated, moto_box, label, color, thickness=2)

        # 4) Vẽ người không gắn với xe máy nào
        for p in persons:
            self._draw_box(annotated, p["box"], "Người", (200, 200, 200), thickness=1)

        # 5) Thêm overlay thống kê
        self._draw_stats(annotated, violations, len(persons), len(motorcycles))

        return violations, annotated

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _count_persons_on_motorcycle(self, persons: list, moto_expanded_box) -> int:
        """Đếm số người có center-point nằm trong vùng xe máy đã mở rộng."""
        x1m, y1m, x2m, y2m = moto_expanded_box
        count = 0
        for p in persons:
            px1, py1, px2, py2 = p["box"]
            cx = (px1 + px2) // 2
            cy = (py1 + py2) // 2
            if x1m <= cx <= x2m and y1m <= cy <= y2m:
                count += 1
        return count

    def _expand_box(self, box, img_shape, scale_y=1.3, scale_x=1.1):
        """Mở rộng bounding box theo tỉ lệ, giới hạn trong ảnh."""
        h, w = img_shape[:2]
        x1, y1, x2, y2 = box
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        bw = (x2 - x1) * scale_x / 2
        bh = (y2 - y1) * scale_y / 2
        return (
            max(0, int(cx - bw)),
            max(0, int(cy - bh)),
            min(w, int(cx + bw)),
            min(h, int(cy + bh)),
        )

    def _draw_box(self, frame, box, label: str, color: tuple, thickness: int = 2):
        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        # Background label
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
        cv2.putText(frame, label, (x1 + 3, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    def _draw_stats(self, frame, violations: list, n_persons: int, n_motos: int):
        """Vẽ bảng thống kê góc trên trái."""
        no_helmet = sum(1 for v in violations if v["type"] == "no_helmet")
        overload  = sum(1 for v in violations if v["type"] == "overloaded_motorcycle")

        lines = [
            f"Nguoi: {n_persons}  Xe may: {n_motos}",
            f"Vi pham MBH: {no_helmet}",
            f"Cho 3 nguoi: {overload}",
        ]

        x, y = 10, 10
        for i, line in enumerate(lines):
            (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(frame, (x - 2, y + i * 28 - 2),
                          (x + tw + 4, y + i * 28 + th + 4), (20, 20, 20), -1)
            color = COLOR_VIOL if ("Vi pham" in line or "Cho 3" in line) and \
                    any(c.isdigit() and c != "0" for c in line) else (220, 220, 220)
            cv2.putText(frame, line, (x, y + i * 28 + th),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    def _build_summary(self, violations: list) -> dict:
        no_helmet = [v for v in violations if v["type"] == "no_helmet"]
        overload  = [v for v in violations if v["type"] == "overloaded_motorcycle"]
        return {
            "total_violations": len(violations),
            "no_helmet_count": len(no_helmet),
            "overloaded_motorcycle_count": len(overload),
            "has_violations": len(violations) > 0,
        }


# Singleton instance
_detector: TrafficViolationDetector | None = None


def get_detector() -> TrafficViolationDetector:
    """Trả về singleton detector (lazy-load)."""
    global _detector
    if _detector is None:
        _detector = TrafficViolationDetector()
    return _detector