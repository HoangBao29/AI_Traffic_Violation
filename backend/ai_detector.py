"""
AI Traffic Violation Detector:
1. Phát hiện không đội mũ bảo hiểm (dùng model custom train-3)
2. Cải tiến logic ghép người - xe máy (Spatial Geometric Bipartite Matching) -> Phát hiện xe chở quá số người quy định
3. Phát hiện phương tiện vượt đèn đỏ (Traffic Light State Analysis + Intersection Stop Line ROI)
4. Nhận dạng biển số xe (License Plate Recognition - ALPR)
"""

import os
import math
import cv2
import numpy as np
from ultralytics import YOLO
from pathlib import Path
import logging
from license_plate import get_lp_recognizer

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent

# Models
HELMET_MODEL_PATH = Path(os.environ.get(
    "HELMET_MODEL_PATH",
    str(BASE_DIR / "runs" / "detect" / "train-3" / "weights" / "best.pt"),
)).expanduser().resolve()
COCO_MODEL_PATH   = BASE_DIR / "yolov8n.pt"

# Màu Bounding Box (BGR)
COLOR_OK       = (0, 200, 0)      # Xanh lá  – hợp lệ
COLOR_WARN     = (0, 165, 255)    # Cam      – cảnh báo
COLOR_VIOL     = (0, 0, 220)      # Đỏ       – vi phạm
COLOR_RED_VIOL = (20, 20, 255)    # Đỏ tươi  – vượt đèn đỏ
COLOR_LIGHT_RED= (0, 0, 255)
COLOR_LIGHT_GRN= (0, 255, 0)

HELMET_CLASS_WITH    = "With Helmet"
HELMET_CLASS_WITHOUT = "Without Helmet"

MAX_VIDEO_OUTPUT_DIMENSION = 1120
MAX_VIDEO_OUTPUT_FPS = 12


def video_output_profile(width: int, height: int, fps: float) -> tuple[int, int, float, int]:
    scale = min(1.0, MAX_VIDEO_OUTPUT_DIMENSION / max(width, height))
    output_width = max(2, int(round(width * scale / 2)) * 2)
    output_height = max(2, int(round(height * scale / 2)) * 2)
    output_stride = max(1, math.ceil(fps / MAX_VIDEO_OUTPUT_FPS))
    return output_width, output_height, fps / output_stride, output_stride


def stop_line_y_at_x(x: float, width: int, height: int,
                     ratio: float, angle: float) -> float:
    center_y = height * ratio
    slope = math.tan(math.radians(angle))
    return center_y + (x - width / 2) * slope


def vehicle_crossed_stop_line(box, width: int, height: int,
                              ratio: float, angle: float) -> bool:
    center_x = (float(box[0]) + float(box[2])) / 2
    bottom_y = float(box[3])
    return bottom_y > stop_line_y_at_x(center_x, width, height, ratio, angle)


class TrafficViolationDetector:
    def __init__(self, conf_helmet: float = 0.4, conf_coco: float = 0.45):
        logger.info("Đang tải model YOLO...")
        if not HELMET_MODEL_PATH.exists():
            raise FileNotFoundError(f"Không tìm thấy model mũ bảo hiểm: {HELMET_MODEL_PATH}")
        self.helmet_model = YOLO(str(HELMET_MODEL_PATH))
        self.coco_model   = YOLO(str(COCO_MODEL_PATH))

        self.conf_helmet = conf_helmet
        self.conf_coco   = conf_coco

        self.helmet_names = self.helmet_model.names
        coco_names = self.coco_model.names

        # ID các class trong COCO
        self.coco_person_id       = next((k for k, v in coco_names.items() if v == "person"), 0)
        self.coco_motorcycle_id   = next((k for k, v in coco_names.items() if v == "motorcycle"), 3)
        self.coco_car_id          = next((k for k, v in coco_names.items() if v == "car"), 2)
        self.coco_bus_id          = next((k for k, v in coco_names.items() if v == "bus"), 5)
        self.coco_truck_id        = next((k for k, v in coco_names.items() if v == "truck"), 7)
        self.coco_traffic_light_id= next((k for k, v in coco_names.items() if v == "traffic light"), 9)

        self.vehicle_class_ids = [
            self.coco_motorcycle_id,
            self.coco_car_id,
            self.coco_bus_id,
            self.coco_truck_id
        ]

        self.target_coco_ids = [
            self.coco_person_id,
            self.coco_traffic_light_id
        ] + self.vehicle_class_ids

        self.lp_recognizer = get_lp_recognizer()
        logger.info("AI Detector đã sẵn sàng với ALPR và Red-light Detection.")

    # ──────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────

    def detect_image(self, image_path: str, stop_line_ratio: float = 0.72,
                     enable_stop_line: bool = True, stop_line_angle: float = 0) -> dict:
        """Phân tích 1 ảnh."""
        img_path = Path(image_path)
        frame = cv2.imread(str(img_path))
        if frame is None:
            raise ValueError(f"Không đọc được ảnh: {image_path}")

        violations, annotated = self._process_frame(
            frame, stop_line_ratio=stop_line_ratio, enable_stop_line=enable_stop_line,
            stop_line_angle=stop_line_angle
        )

        out_path = img_path.parent / f"annotated_{img_path.name}"
        cv2.imwrite(str(out_path), annotated)

        return {
            "violations": violations,
            "annotated_path": str(out_path),
            "summary": self._build_summary(violations),
        }

    def detect_video(self, video_path: str, output_path: str = None,
                     frame_skip: int = 3, stop_line_ratio: float = 0.72,
                     enable_stop_line: bool = True, progress_cb=None,
                     stop_line_angle: float = 0) -> dict:
        """Phân tích video theo từng frame."""
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

        if output_path is None:
            output_path = str(video_path.parent / f"annotated_{video_path.name}")

        output_width, output_height, output_fps, output_stride = video_output_profile(
            width, height, fps
        )
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(
            output_path, fourcc, output_fps, (output_width, output_height)
        )
        if not out.isOpened():
            cap.release()
            raise RuntimeError(f"Không thể tạo video kết quả: {output_path}")

        all_violations = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % frame_skip == 0:
                # Với video, chỉ bật OCR nhận dạng biển số khi có vi phạm để giữ tốc độ xử lý nhanh
                violations, annotated = self._process_frame(
                    frame, stop_line_ratio=stop_line_ratio,
                    enable_stop_line=enable_stop_line, run_ocr_on_violations=True,
                    stop_line_angle=stop_line_angle
                )
                if violations:
                    for v in violations:
                        v["frame"] = frame_idx
                    all_violations.extend(violations)
                output_frame = annotated
            else:
                output_frame = frame

            if frame_idx % output_stride == 0:
                if (output_width, output_height) != (width, height):
                    output_frame = cv2.resize(
                        output_frame, (output_width, output_height),
                        interpolation=cv2.INTER_AREA,
                    )
                out.write(output_frame)

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

    def _process_frame(self, frame: np.ndarray, stop_line_ratio: float = 0.72,
                       enable_stop_line: bool = True,
                       run_ocr_on_violations: bool = True,
                       stop_line_angle: float = 0) -> tuple[list[dict], np.ndarray]:
        annotated = frame.copy()
        violations = []
        h_frame, w_frame = frame.shape[:2]

        # 1. Chạy COCO model
        coco_results = self.coco_model(
            frame, conf=self.conf_coco, verbose=False,
            classes=self.target_coco_ids
        )[0]

        persons        = []
        motorcycles    = []
        other_vehicles = []
        traffic_lights = []

        for box in coco_results.boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            xyxy   = box.xyxy[0].cpu().numpy().astype(int)

            if cls_id == self.coco_person_id:
                persons.append({"box": xyxy, "conf": conf, "id": len(persons)})
            elif cls_id == self.coco_motorcycle_id:
                motorcycles.append({"box": xyxy, "conf": conf, "riders": []})
            elif cls_id in self.vehicle_class_ids:
                v_type = "car" if cls_id == self.coco_car_id else ("bus" if cls_id == self.coco_bus_id else "truck")
                other_vehicles.append({"box": xyxy, "conf": conf, "type": v_type})
            elif cls_id == self.coco_traffic_light_id:
                traffic_lights.append({"box": xyxy, "conf": conf})

        # 2. Phát hiện trạng thái đèn giao thông (Đỏ / Vàng / Xanh) nếu BẬT vạch dừng
        is_red_light = False
        light_color  = "none"
        tl_box       = None
        if enable_stop_line:
            is_red_light, light_color, tl_box = self._analyze_traffic_lights(frame, traffic_lights)
            line_color = (0, 0, 255) if is_red_light else (0, 200, 0)
            line_start = (0, round(stop_line_y_at_x(
                0, w_frame, h_frame, stop_line_ratio, stop_line_angle
            )))
            line_end = (w_frame - 1, round(stop_line_y_at_x(
                w_frame - 1, w_frame, h_frame, stop_line_ratio, stop_line_angle
            )))
            cv2.line(annotated, line_start, line_end, line_color, 2, cv2.LINE_AA)
            label_y = round(stop_line_y_at_x(
                20, w_frame, h_frame, stop_line_ratio, stop_line_angle
            ))
            status_text = "VACH DUNG - DEN DO" if is_red_light else "VACH DUNG (DEN XANH/VANG)"
            cv2.putText(annotated, status_text, (20, label_y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, line_color, 2, cv2.LINE_AA)

            if tl_box is not None:
                tl_label = f"Den: {light_color.upper()}"
                tl_color = COLOR_LIGHT_RED if light_color == "red" else (COLOR_LIGHT_GRN if light_color == "green" else (0, 255, 255))
                self._draw_box(annotated, tl_box, tl_label, tl_color, thickness=2)

        # 3. Chạy model phát hiện mũ bảo hiểm
        helmet_results = self.helmet_model(frame, conf=self.conf_helmet, verbose=False)[0]
        helmets_with    = []
        helmets_without = []

        for box in helmet_results.boxes:
            cls_id = int(box.cls[0])
            xyxy   = box.xyxy[0].cpu().numpy().astype(int)
            name   = self.helmet_names.get(cls_id, "")

            if name == HELMET_CLASS_WITHOUT or "without" in name.lower():
                helmets_without.append({"box": xyxy, "conf": float(box.conf[0])})
            else:
                helmets_with.append({"box": xyxy, "conf": float(box.conf[0])})
                self._draw_box(annotated, xyxy, "Co MBH", COLOR_OK, thickness=1)

        # 4. CẢI TIẾN LOGIC GHÉP NGƯỜI - XE MÁY (Bipartite Matching với hình học không gian)
        self._associate_riders_to_motorcycles(persons, motorcycles, frame.shape)

        # Xử lý từng xe máy: kiểm tra chở quá số người quy định + kiểm tra mũ bảo hiểm của người ngồi trên xe
        for moto in motorcycles:
            m_box     = moto["box"]
            m_conf    = moto["conf"]
            riders    = moto["riders"]
            n_riders  = len(riders)

            # Đọc biển số xe máy nếu cần
            plate_info = {"plate_text": "", "plate_box": None}

            # Kiểm tra chở quá người (>= 3 người)
            if n_riders >= 3:
                if run_ocr_on_violations:
                    plate_info = self.lp_recognizer.detect_and_read(frame, m_box)

                plate_str = f" [{plate_info['plate_text']}]" if plate_info['plate_text'] else ""
                label = f"Cho {n_riders} nguoi (Vi pham){plate_str}"
                self._draw_box(annotated, m_box, label, COLOR_VIOL, thickness=2)

                violations.append({
                    "type": "overloaded_motorcycle",
                    "label": f"Xe máy chở {n_riders} người (vượt quy định){plate_str}",
                    "box": m_box.tolist(),
                    "conf": m_conf,
                    "people_count": n_riders,
                    "license_plate": plate_info["plate_text"] or "Chưa rõ",
                    "plate_box": plate_info["plate_box"]
                })
            else:
                label = f"Xe may ({n_riders} nguoi)" if n_riders > 0 else "Xe may"
                self._draw_box(annotated, m_box, label, COLOR_OK, thickness=2)

            # Kiểm tra vi phạm vượt đèn đỏ của xe máy
            if is_red_light and vehicle_crossed_stop_line(
                m_box, w_frame, h_frame, stop_line_ratio, stop_line_angle
            ):
                if not plate_info["plate_text"] and run_ocr_on_violations:
                    plate_info = self.lp_recognizer.detect_and_read(frame, m_box)
                plate_str = f" [{plate_info['plate_text']}]" if plate_info['plate_text'] else ""

                self._draw_box(annotated, m_box, f"VUOT DEN DO{plate_str}", COLOR_RED_VIOL, thickness=3)
                violations.append({
                    "type": "red_light",
                    "label": f"Xe máy vượt đèn đỏ{plate_str}",
                    "box": m_box.tolist(),
                    "conf": m_conf,
                    "license_plate": plate_info["plate_text"] or "Chưa rõ",
                    "plate_box": plate_info["plate_box"]
                })

        # 5. Kiểm tra vi phạm vượt đèn đỏ của các phương tiện khác (Ô tô, Xe buýt, Xe tải)
        if is_red_light:
            for veh in other_vehicles:
                v_box = veh["box"]
                if vehicle_crossed_stop_line(
                    v_box, w_frame, h_frame, stop_line_ratio, stop_line_angle
                ):
                    plate_info = self.lp_recognizer.detect_and_read(frame, v_box) if run_ocr_on_violations else {"plate_text": "", "plate_box": None}
                    plate_str = f" [{plate_info['plate_text']}]" if plate_info['plate_text'] else ""
                    v_name = "Ô tô" if veh["type"] == "car" else ("Xe buýt" if veh["type"] == "bus" else "Xe tải")

                    self._draw_box(annotated, v_box, f"{v_name} VUOT DEN DO{plate_str}", COLOR_RED_VIOL, thickness=3)
                    violations.append({
                        "type": "red_light",
                        "label": f"{v_name} vượt đèn đỏ{plate_str}",
                        "box": v_box.tolist(),
                        "conf": veh["conf"],
                        "license_plate": plate_info["plate_text"] or "Chưa rõ",
                        "plate_box": plate_info["plate_box"]
                    })
                else:
                    self._draw_box(annotated, v_box, veh["type"], COLOR_OK, thickness=1)
        else:
            for veh in other_vehicles:
                self._draw_box(annotated, veh["box"], veh["type"], (200, 200, 200), thickness=1)

        # 6. Gán vi phạm Không đội mũ bảo hiểm cho người & phương tiện tương ứng
        for h_viol in helmets_without:
            h_box = h_viol["box"]
            h_conf = h_viol["conf"]

            # Tìm xem mũ này thuộc người nào và xe máy nào để trích xuất biển số
            matched_moto = self._find_motorcycle_for_head(h_box, motorcycles)
            plate_info = {"plate_text": "", "plate_box": None}
            if matched_moto and run_ocr_on_violations:
                plate_info = self.lp_recognizer.detect_and_read(frame, matched_moto["box"])

            plate_str = f" [{plate_info['plate_text']}]" if plate_info['plate_text'] else ""
            label_text = f"Khong MBH{plate_str}"

            self._draw_box(annotated, h_box, label_text, COLOR_VIOL, thickness=2)

            violations.append({
                "type": "no_helmet",
                "label": f"Không đội mũ bảo hiểm{plate_str}",
                "box": h_box.tolist(),
                "conf": h_conf,
                "license_plate": plate_info["plate_text"] or "Chưa rõ",
                "plate_box": plate_info["plate_box"]
            })

        # 7. Vẽ người không đi xe máy
        for p in persons:
            if not p.get("assigned_moto"):
                self._draw_box(annotated, p["box"], "Nguoi di bo", (180, 180, 180), thickness=1)

        # 8. Overlay thống kê trực quan
        self._draw_stats(annotated, violations, len(persons), len(motorcycles), is_red_light, light_color, enable_stop_line)

        return violations, annotated

    # ──────────────────────────────────────────────────────────────────────
    # Logic cải tiến: Ghép người - Xe máy (Spatial Geometric Bipartite Matching)
    # ──────────────────────────────────────────────────────────────────────

    def _associate_riders_to_motorcycles(self, persons: list, motorcycles: list, frame_shape: tuple):
        """
        Ghép người và xe máy dựa trên đặc trưng hình học thực tế:
        - Đáy người (mông/chân) phải nằm trong thân xe máy.
        - Đầu người nhô cao hơn thân xe.
        - Tâm ngang người thẳng hàng với xe máy.
        - Đảm bảo mỗi người chỉ thuộc về nhiều nhất MỘT xe máy (không tính trùng lặp).
        """
        if not persons or not motorcycles:
            return

        candidates = []

        for p_idx, p in enumerate(persons):
            px1, py1, px2, py2 = p["box"]
            p_w = px2 - px1
            p_h = py2 - py1
            p_cx = (px1 + px2) / 2
            p_area = max(1, p_w * p_h)

            for m_idx, m in enumerate(motorcycles):
                mx1, my1, mx2, my2 = m["box"]
                m_w = mx2 - mx1
                m_h = my2 - my1
                m_cx = (mx1 + mx2) / 2

                # 1. Kiểm tra tâm ngang: người phải nằm trong khoảng chiều rộng xe (mở rộng 20% mỗi bên)
                if abs(p_cx - m_cx) > (m_w * 0.7):
                    continue

                # 2. Kiểm tra chiều dọc: Đáy người phải nằm từ 15% phía trên đến đáy xe máy
                if not (my1 - (m_h * 0.2) <= py2 <= my2 + (m_h * 0.1)):
                    continue

                # 3. Đỉnh đầu người phải cao hơn yên xe
                if py1 > my1 + (m_h * 0.45):
                    continue

                # 4. Tương quan kích thước: chiều cao người không được quá lệch so với xe
                height_ratio = p_h / max(1, m_h)
                if height_ratio < 0.4 or height_ratio > 2.5:
                    continue

                # Tính diện tích giao nhau dọc
                inter_x1 = max(px1, mx1)
                inter_x2 = min(px2, mx2)
                inter_y1 = max(py1, my1)
                inter_y2 = min(py2, my2)
                inter_w = max(0, inter_x2 - inter_x1)
                inter_h = max(0, inter_y2 - inter_y1)
                inter_area = inter_w * inter_h

                # Matching Score: ưu tiên người có tâm gần xe và độ giao nhau hợp lý
                h_dist_norm = abs(p_cx - m_cx) / max(1, m_w)
                score = (inter_area / p_area) * 0.6 + (1.0 - min(1.0, h_dist_norm)) * 0.4

                if score > 0.15:
                    candidates.append((score, p_idx, m_idx))

        # Sắp xếp các cặp theo điểm số giảm dần (Greedy Matching độc quyền)
        candidates.sort(key=lambda x: x[0], reverse=True)

        assigned_persons = set()
        for score, p_idx, m_idx in candidates:
            if p_idx not in assigned_persons:
                motorcycles[m_idx]["riders"].append(persons[p_idx])
                persons[p_idx]["assigned_moto"] = m_idx
                assigned_persons.add(p_idx)

    def _find_motorcycle_for_head(self, head_box: list, motorcycles: list) -> dict | None:
        """Tìm chiếc xe máy mà người đang không đội mũ bảo hiểm đang ngồi lên."""
        hx1, hy1, hx2, hy2 = head_box
        hcx = (hx1 + hx2) / 2
        hcy = (hy1 + hy2) / 2

        best_moto = None
        min_dist = float("inf")

        for moto in motorcycles:
            mx1, my1, mx2, my2 = moto["box"]
            # Đầu phải nằm phía trên hoặc trong vùng trên của xe máy
            if mx1 - 30 <= hcx <= mx2 + 30 and (my1 - 120 <= hcy <= my1 + 60):
                dist = abs(hcx - (mx1 + mx2) / 2)
                if dist < min_dist:
                    min_dist = dist
                    best_moto = moto

        return best_moto

    # ──────────────────────────────────────────────────────────────────────
    # Phân tích đèn giao thông (Red Light Detection)
    # ──────────────────────────────────────────────────────────────────────

    def _analyze_traffic_lights(self, frame: np.ndarray, traffic_lights: list) -> tuple[bool, str, list | None]:
        """
        Phân tích màu của đèn giao thông trong khung hình.
        Trả về: (is_red_light, light_color, tl_box)
        """
        if not traffic_lights:
            return False, "none", None

        # Chọn đèn giao thông có diện tích và độ tin cậy tốt nhất
        best_tl = max(traffic_lights, key=lambda x: (x["box"][2] - x["box"][0]) * (x["box"][3] - x["box"][1]))
        bx1, by1, bx2, by2 = best_tl["box"]
        tl_crop = frame[by1:by2, bx1:bx2]
        if tl_crop.size == 0 or (by2 - by1) < 8 or (bx2 - bx1) < 8:
            return False, "none", None

        hsv = cv2.cvtColor(tl_crop, cv2.COLOR_BGR2HSV)

        # Mask màu Đỏ
        mask_r1 = cv2.inRange(hsv, np.array([0, 70, 70]), np.array([10, 255, 255]))
        mask_r2 = cv2.inRange(hsv, np.array([160, 70, 70]), np.array([180, 255, 255]))
        red_mask = mask_r1 | mask_r2

        # Mask màu Xanh
        green_mask = cv2.inRange(hsv, np.array([35, 70, 70]), np.array([85, 255, 255]))

        # Mask màu Vàng
        yellow_mask = cv2.inRange(hsv, np.array([15, 80, 80]), np.array([34, 255, 255]))

        red_pixels   = cv2.countNonZero(red_mask)
        green_pixels = cv2.countNonZero(green_mask)
        yellow_pixels= cv2.countNonZero(yellow_mask)

        total_pixels = tl_crop.shape[0] * tl_crop.shape[1]

        # Nếu tỷ lệ màu đỏ cao nhất và chiếm > 4% diện tích đèn
        if red_pixels > green_pixels and red_pixels > yellow_pixels and (red_pixels / total_pixels) > 0.04:
            return True, "red", best_tl["box"]
        elif green_pixels > red_pixels and green_pixels > yellow_pixels and (green_pixels / total_pixels) > 0.04:
            return False, "green", best_tl["box"]
        elif yellow_pixels > 0:
            return False, "yellow", best_tl["box"]

        return False, "unknown", best_tl["box"]

    # ──────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────

    def _draw_box(self, frame, box, label: str, color: tuple, thickness: int = 2):
        x1, y1, x2, y2 = box
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - th - 8)), (x1 + tw + 6, max(th + 8, y1)), color, -1)
        cv2.putText(frame, label, (x1 + 3, max(th + 2, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)

    def _draw_stats(self, frame, violations: list, n_persons: int, n_motos: int,
                    is_red_light: bool, light_color: str, enable_stop_line: bool = True):
        no_helmet = sum(1 for v in violations if v["type"] == "no_helmet")
        overload  = sum(1 for v in violations if v["type"] == "overloaded_motorcycle")
        red_light = sum(1 for v in violations if v["type"] == "red_light")

        if enable_stop_line:
            tl_text = f"Den: {light_color.upper()}" if light_color != "none" else "Den: Khong co"
            lines = [
                f"Nguoi: {n_persons} | Xe may: {n_motos} | {tl_text}",
                f"Vi pham MBH: {no_helmet}",
                f"Cho >=3 nguoi: {overload}",
                f"Vuot den do: {red_light}"
            ]
        else:
            lines = [
                f"Nguoi: {n_persons} | Xe may: {n_motos}",
                f"Vi pham MBH: {no_helmet}",
                f"Cho >=3 nguoi: {overload}"
            ]

        x, y = 10, 10
        for i, line in enumerate(lines):
            (tw, th), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            cv2.rectangle(frame, (x - 2, y + i * 26 - 2),
                          (x + tw + 6, y + i * 26 + th + 4), (20, 20, 20), -1)

            is_viol_line = any(k in line for k in ["Vi pham", "Cho >=", "Vuot den do"]) and not line.endswith(": 0")
            color = COLOR_VIOL if is_viol_line else (220, 220, 220)
            cv2.putText(frame, line, (x, y + i * 26 + th),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)

    def _build_summary(self, violations: list) -> dict:
        no_helmet = [v for v in violations if v["type"] == "no_helmet"]
        overload  = [v for v in violations if v["type"] == "overloaded_motorcycle"]
        red_light = [v for v in violations if v["type"] == "red_light"]

        plates = [v.get("license_plate") for v in violations if v.get("license_plate") and v.get("license_plate") != "Chưa rõ"]

        return {
            "total_violations": len(violations),
            "no_helmet_count": len(no_helmet),
            "overloaded_motorcycle_count": len(overload),
            "red_light_count": len(red_light),
            "detected_plates": list(set(plates)),
            "has_violations": len(violations) > 0,
        }


# Singleton instance
_detector: TrafficViolationDetector | None = None

def get_detector() -> TrafficViolationDetector:
    global _detector
    if _detector is None:
        _detector = TrafficViolationDetector()
    return _detector
