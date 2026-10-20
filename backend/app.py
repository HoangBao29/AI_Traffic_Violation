"""
Flask Backend - AI Traffic Violation Detection API

Schema thực tế của Supabase:
  videos:    id, file_name, file_url, upload_by, upload_time, duration, status,
             local_path, annotated_url, violation_count
  violations: id, video_id, violation_type, confidence, frame_number,
              detected_time, created_at, label, box, extra_data
"""

import os
import uuid
import logging
from pathlib import Path
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from supabase_client import supabase
from ai_detector import get_detector

# ── Cấu hình ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)   # Cho phép frontend gọi API từ mọi origin
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024   # 500 MB

UPLOAD_FOLDER = Path(__file__).parent / "uploads"
RESULT_FOLDER = Path(__file__).parent / "results"
UPLOAD_FOLDER.mkdir(exist_ok=True)
RESULT_FOLDER.mkdir(exist_ok=True)

ALLOWED_IMAGE = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
ALLOWED_VIDEO = {".mp4", ".avi", ".mov", ".mkv"}

# Supabase Storage bucket names
BUCKET_VIDEOS     = "videos"
BUCKET_VIOLATIONS = "violations"


# ── Helpers ────────────────────────────────────────────────────────────────

def allowed_file(filename: str, allowed_set: set) -> bool:
    return Path(filename).suffix.lower() in allowed_set


def unique_filename(original_name: str) -> str:
    suffix = Path(original_name).suffix
    return f"{uuid.uuid4().hex}{suffix}"


def upload_to_storage(bucket: str, storage_path: str, local_path: str,
                      content_type: str = "application/octet-stream") -> str | None:
    """
    Upload file lên Supabase Storage.
    Trả về public URL hoặc None nếu lỗi.
    """
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        supabase.storage.from_(bucket).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        url = supabase.storage.from_(bucket).get_public_url(storage_path)
        logger.info(f"Storage upload OK: {bucket}/{storage_path}")
        return url
    except Exception as e:
        logger.warning(f"Storage upload thất bại ({bucket}/{storage_path}): {e}")
        return None


def get_video_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
    }.get(ext, "video/mp4")


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return jsonify({
        "message": "AI Traffic Violation Backend Running",
        "endpoints": [
            "POST /detect/image        – Phân tích ảnh trực tiếp",
            "POST /detect/video        – Phân tích video trực tiếp",
            "POST /upload-video        – Upload video → lưu Supabase (pending)",
            "POST /process-video/<id>  – Xử lý video pending theo ID",
            "GET  /videos              – Danh sách video",
            "GET  /violations          – Danh sách vi phạm",
            "GET  /result/<filename>   – Tải file đã annotate",
        ]
    })


# ── Detect ảnh trực tiếp ───────────────────────────────────────────────────

@app.route("/detect/image", methods=["POST"])
def detect_image():
    """
    Upload ảnh → AI phân tích → JSON kết quả + URL ảnh annotated.
    Form-data: file = <image>
    """
    if "file" not in request.files:
        return jsonify({"error": "Thiếu trường 'file'"}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_IMAGE):
        return jsonify({"error": "Chỉ chấp nhận ảnh: jpg, jpeg, png, bmp, webp"}), 400

    fname     = unique_filename(file.filename)
    save_path = UPLOAD_FOLDER / fname
    file.save(str(save_path))
    logger.info(f"Ảnh upload local: {save_path}")

    try:
        detector = get_detector()
        result   = detector.detect_image(str(save_path))

        # Di chuyển ảnh annotated vào results/
        annotated_src  = Path(result["annotated_path"])
        annotated_dest = RESULT_FOLDER / annotated_src.name
        annotated_src.rename(annotated_dest)
        result["annotated_path"] = str(annotated_dest)
        result["annotated_url"]  = f"/result/{annotated_dest.name}"

        logger.info(f"Kết quả ảnh: {result['summary']}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Lỗi detect ảnh")
        return jsonify({"error": str(e)}), 500


# ── Detect video trực tiếp ─────────────────────────────────────────────────

@app.route("/detect/video", methods=["POST"])
def detect_video():
    """
    Upload video → AI phân tích → JSON kết quả + URL video annotated.
    Form-data: file       = <video>
               frame_skip = <int, default 3>
    """
    if "file" not in request.files:
        return jsonify({"error": "Thiếu trường 'file'"}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_VIDEO):
        return jsonify({"error": "Chỉ chấp nhận video: mp4, avi, mov, mkv"}), 400

    frame_skip = int(request.form.get("frame_skip", 3))

    fname     = unique_filename(file.filename)
    save_path = UPLOAD_FOLDER / fname
    file.save(str(save_path))
    logger.info(f"Video upload local: {save_path}")

    try:
        out_name = f"annotated_{save_path.stem}.mp4"
        out_path = str(RESULT_FOLDER / out_name)

        detector = get_detector()
        result   = detector.detect_video(
            str(save_path), output_path=out_path, frame_skip=frame_skip
        )
        result["annotated_url"] = f"/result/{out_name}"

        logger.info(f"Kết quả video: {result['summary']}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Lỗi detect video")
        return jsonify({"error": str(e)}), 500


# ── Upload video → Supabase Storage + DB ──────────────────────────────────

@app.route("/upload-video", methods=["POST"])
def upload_video():
    """
    Upload video lên Supabase Storage + tạo record DB với status='pending'.
    Form-data: video     = <video file>
               upload_by = <user uuid, optional>
    """
    if "video" not in request.files:
        return jsonify({"error": "Thiếu trường 'video'"}), 400

    file = request.files["video"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_VIDEO):
        return jsonify({"error": "Chỉ chấp nhận video: mp4, avi, mov, mkv"}), 400

    upload_by = request.form.get("upload_by")   # UUID của user (nếu có)

    # 1. Lưu local tạm thời
    fname      = unique_filename(file.filename)
    local_path = UPLOAD_FOLDER / fname
    file.save(str(local_path))
    logger.info(f"Video lưu local: {local_path}")

    try:
        # 2. Upload lên Supabase Storage bucket 'videos'
        storage_key  = f"videos/{fname}"
        content_type = get_video_content_type(file.filename)
        file_url     = upload_to_storage(
            BUCKET_VIDEOS, storage_key, str(local_path), content_type
        )

        if not file_url:
            # Nếu storage lỗi, vẫn lưu local path làm fallback
            file_url = f"local:{local_path}"
            logger.warning("Dùng local path làm fallback vì storage upload thất bại")

        # 3. Lưu record vào bảng videos (đúng tên cột thực tế)
        insert_data = {
            "file_name":  file.filename,          # tên file gốc
            "file_url":   file_url,               # URL trên Supabase Storage
            "local_path": str(local_path),        # đường dẫn local (để AI xử lý)
            "status":     "pending",
        }
        if upload_by:
            insert_data["upload_by"] = upload_by

        db_resp = supabase.table("videos").insert(insert_data).execute()
        video_record = db_resp.data[0] if db_resp.data else {}

        return jsonify({
            "message":  "Upload thành công, đang chờ xử lý",
            "video_id": video_record.get("id"),
            "file_name": file.filename,
            "file_url":  file_url,
            "status":   "pending",
        })

    except Exception as e:
        logger.exception("Lỗi khi upload video lên Supabase")
        return jsonify({"error": str(e)}), 500


# ── Process 1 video từ DB ──────────────────────────────────────────────────

@app.route("/process-video/<int:video_id>", methods=["POST"])
def process_video(video_id: int):
    """
    AI xử lý video pending theo ID:
    1. Lấy local_path từ DB
    2. Chạy YOLO detect
    3. Upload video annotated lên storage 'violations'
    4. Lưu từng vi phạm vào bảng violations
    5. Cập nhật status = 'processed'
    """
    try:
        resp = supabase.table("videos").select("*").eq("id", video_id).execute()
        if not resp.data:
            return jsonify({"error": "Không tìm thấy video"}), 404

        record = resp.data[0]
        if record.get("status") == "processed":
            return jsonify({"message": "Video đã xử lý rồi", "video_id": video_id})

        # Lấy đường dẫn local
        local_path = record.get("local_path") or record.get("file_url", "")
        if local_path.startswith("local:"):
            local_path = local_path[6:]

        if not local_path or not Path(local_path).exists():
            return jsonify({"error": f"File video không tìm thấy trên server: {local_path}"}), 400

        # Đánh dấu đang xử lý
        supabase.table("videos").update({"status": "processing"}).eq("id", video_id).execute()

        out_name = f"annotated_{Path(local_path).stem}.mp4"
        out_path = str(RESULT_FOLDER / out_name)

        detector = get_detector()
        result   = detector.detect_video(local_path, output_path=out_path, frame_skip=3)

        # Upload video annotated lên storage 'violations'
        annotated_storage_key = f"annotated/{out_name}"
        annotated_url = upload_to_storage(
            BUCKET_VIOLATIONS, annotated_storage_key, out_path, "video/mp4"
        )
        if not annotated_url:
            annotated_url = f"/result/{out_name}"   # fallback: local serve

        # Lưu từng vi phạm vào DB (đúng tên cột thực tế)
        violations_saved = []
        for v in result["violations"]:
            db_v = supabase.table("violations").insert({
                "video_id":       video_id,
                "violation_type": v["type"],           # cột thực tế: violation_type
                "label":          v["label"],
                "confidence":     v.get("conf", 0),
                "frame_number":   v.get("frame"),      # cột thực tế: frame_number
                "box":            str(v.get("box")),
                "extra_data":     str({
                    k: val for k, val in v.items()
                    if k not in ("type", "label", "conf", "frame", "box")
                }),
            }).execute()
            violations_saved.extend(db_v.data)

        # Cập nhật video record
        supabase.table("videos").update({
            "status":          "processed",
            "annotated_url":   annotated_url,
            "violation_count": result["summary"]["total_violations"],
        }).eq("id", video_id).execute()

        return jsonify({
            "message":       "Xử lý thành công",
            "video_id":      video_id,
            "summary":       result["summary"],
            "annotated_url": annotated_url,
            "violations":    violations_saved,
        })

    except Exception as e:
        logger.exception(f"Lỗi khi xử lý video {video_id}")
        try:
            supabase.table("videos").update({"status": "error"}).eq("id", video_id).execute()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


# ── Serve file kết quả local ───────────────────────────────────────────────

@app.route("/result/<filename>")
def get_result_file(filename: str):
    """Tải về ảnh/video đã annotate (fallback khi storage không khả dụng)."""
    file_path = RESULT_FOLDER / filename
    if not file_path.exists():
        return jsonify({"error": "File không tồn tại"}), 404
    return send_file(str(file_path))


# ── Supabase DB queries ────────────────────────────────────────────────────

@app.route("/videos")
def get_videos():
    resp = supabase.table("videos").select("*").order("id", desc=True).execute()
    return jsonify(resp.data)


@app.route("/violations")
def get_violations():
    resp = supabase.table("violations").select("*").order("id", desc=True).execute()
    return jsonify(resp.data)


@app.route("/violations/video/<int:video_id>")
def get_violations_by_video(video_id: int):
    resp = (supabase.table("violations").select("*")
            .eq("video_id", video_id).execute())
    return jsonify(resp.data)


@app.route("/videos/summary")
def get_videos_summary():
    """Lấy tóm tắt video + số vi phạm (dùng view video_violation_summary)."""
    try:
        resp = supabase.table("video_violation_summary").select("*").execute()
        return jsonify(resp.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)