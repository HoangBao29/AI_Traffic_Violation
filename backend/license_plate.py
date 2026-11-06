"""
License Plate Recognition (ALPR) Module for Vietnam / Standard Vehicle Plates.
Kết hợp Computer Vision định vị vùng biển số và EasyOCR nhận dạng ký tự.
"""

import re
import cv2
import numpy as np
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex biển số Việt Nam phổ biến:
# Xe máy: 29-A1 12345, 59-X2 9876, 43F1-12345, 29B1 88888
# Ô tô: 30A-123.45, 51K-999.99, 14B-01234
VN_PLATE_PATTERN = re.compile(r'([0-9]{2}[A-ZĐ][0-9A-Z]?[-.\s]?[0-9]{3,5}[.\s]?[0-9]{0,2})', re.IGNORECASE)

class LicensePlateRecognizer:
    def __init__(self, use_gpu: bool = False):
        self._reader = None
        self.use_gpu = use_gpu

    def _get_reader(self):
        """Lazy load EasyOCR để không làm chậm lúc khởi động server."""
        if self._reader is None:
            try:
                import easyocr
                import torch
                gpu_avail = torch.cuda.is_available() and self.use_gpu
                logger.info(f"Khởi tạo EasyOCR Reader (GPU={gpu_avail})...")
                self._reader = easyocr.Reader(['en'], gpu=gpu_avail, verbose=False)
                logger.info("EasyOCR Reader đã sẵn sàng.")
            except Exception as e:
                logger.warning(f"Không thể khởi tạo EasyOCR: {e}")
                self._reader = False
        return self._reader if self._reader is not False else None

    def clean_plate_text(self, text: str) -> str:
        """Chuẩn hóa ký tự biển số xe."""
        if not text:
            return ""
        # Loại bỏ các ký tự đặc biệt không mong muốn
        text = text.upper().strip()
        text = re.sub(r'[^A-Z0-9\-\.]', '', text)
        
        # Thử trích xuất theo regex chuẩn
        match = VN_PLATE_PATTERN.search(text)
        if match:
            return match.group(1).upper()
        
        # Nếu chuỗi từ 5 ký tự trở lên (VD: 29A12345)
        cleaned = re.sub(r'[^A-Z0-9]', '', text)
        if len(cleaned) >= 5:
            # Format đẹp: 2 ký tự đầu là số tỉnh, tiếp theo chữ, sau đó số
            if cleaned[:2].isdigit():
                return f"{cleaned[:3]}-{cleaned[3:]}"
            return cleaned
        return cleaned

    def detect_and_read(self, frame: np.ndarray, vehicle_box: list[int] | tuple[int, int, int, int]) -> dict:
        """
        Nhận diện biển số trong vùng xe (vehicle_box: [x1, y1, x2, y2]).
        Trả về: { "plate_text": str, "plate_box": [x1, y1, x2, y2], "confidence": float }
        """
        h_frame, w_frame = frame.shape[:2]
        vx1, vy1, vx2, vy2 = vehicle_box
        vx1, vy1 = max(0, vx1), max(0, vy1)
        vx2, vy2 = min(w_frame, vx2), min(h_frame, vy2)

        vh = vy2 - vy1
        vw = vx2 - vx1
        if vh < 30 or vw < 30:
            return {"plate_text": "", "plate_box": None, "confidence": 0.0}

        # Biển số xe thường nằm ở nửa dưới của xe (khoảng 35% phía dưới)
        plate_roi_y1 = vy1 + int(vh * 0.55)
        plate_roi_y2 = vy2
        # Căn lề ngang khoảng 10% mỗi bên
        plate_roi_x1 = vx1 + int(vw * 0.08)
        plate_roi_x2 = vx2 - int(vw * 0.08)

        roi = frame[plate_roi_y1:plate_roi_y2, plate_roi_x1:plate_roi_x2]
        if roi.size == 0:
            return {"plate_text": "", "plate_box": None, "confidence": 0.0}

        reader = self._get_reader()
        if reader is None:
            # Fallback nhẹ nếu chưa load được EasyOCR
            return {"plate_text": "", "plate_box": None, "confidence": 0.0}

        # Tiền xử lý ảnh ROI biển số để EasyOCR đọc tốt hơn
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Tăng kích thước nếu ảnh quá nhỏ
        if gray.shape[0] < 80:
            scale = 80.0 / gray.shape[0]
            gray = cv2.resize(gray, (int(gray.shape[1] * scale), 80), interpolation=cv2.INTER_CUBIC)

        # Cân bằng độ tương phản (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        try:
            results = reader.readtext(enhanced, allowlist='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ.-')
            best_text = ""
            best_conf = 0.0
            found_box = None

            full_texts = []
            for (bbox, text, prob) in results:
                c_text = self.clean_plate_text(text)
                if len(c_text) >= 4 and prob > 0.2:
                    full_texts.append(c_text)
                    if prob > best_conf:
                        best_conf = prob
                        # Map lại tọa độ bounding box lên toàn khung hình
                        pts = np.array(bbox)
                        bx1 = int(plate_roi_x1 + np.min(pts[:, 0]))
                        by1 = int(plate_roi_y1 + np.min(pts[:, 1]))
                        bx2 = int(plate_roi_x1 + np.max(pts[:, 0]))
                        by2 = int(plate_roi_y1 + np.max(pts[:, 1]))
                        found_box = [max(0, bx1), max(0, by1), min(w_frame, bx2), min(h_frame, by2)]

            if full_texts:
                # Nếu biển 2 dòng có thể nối lại
                best_text = " ".join(full_texts)
                best_text = self.clean_plate_text(best_text)

            if not found_box and best_text:
                found_box = [plate_roi_x1, plate_roi_y1, plate_roi_x2, plate_roi_y2]

            return {
                "plate_text": best_text,
                "plate_box": found_box,
                "confidence": round(float(best_conf), 2)
            }
        except Exception as e:
            logger.debug(f"Lỗi khi đọc biển số: {e}")
            return {"plate_text": "", "plate_box": None, "confidence": 0.0}


# Singleton
_lp_recognizer = None

def get_lp_recognizer() -> LicensePlateRecognizer:
    global _lp_recognizer
    if _lp_recognizer is None:
        _lp_recognizer = LicensePlateRecognizer(use_gpu=False)
    return _lp_recognizer

