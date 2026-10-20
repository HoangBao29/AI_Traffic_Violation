"""
Worker xử lý tất cả video đang ở trạng thái 'pending' trong Supabase.

Cách dùng:
    python process_pending_videos.py
    python process_pending_videos.py --loop 60   # lặp mỗi 60 giây
"""

import sys
import time
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from supabase_client import supabase
from ai_detector import get_detector

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RESULT_FOLDER = Path(__file__).parent / "results"
RESULT_FOLDER.mkdir(exist_ok=True)

BUCKET_VIOLATIONS = "violations"


def upload_to_storage(local_path: str, storage_path: str) -> str | None:
    """Upload file annotated lên Supabase Storage violations bucket."""
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        supabase.storage.from_(BUCKET_VIOLATIONS).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": "video/mp4", "upsert": "true"},
        )
        url = supabase.storage.from_(BUCKET_VIOLATIONS).get_public_url(storage_path)
        return url
    except Exception as e:
        logger.warning(f"Storage upload thất bại: {e}")
        return None


def process_one(record: dict) -> bool:
    """Xử lý 1 video record. Trả về True nếu thành công."""
    video_id   = record["id"]

    # Lấy đường dẫn local (cột local_path hoặc fallback file_url local)
    local_path = record.get("local_path") or record.get("file_url", "")
    if local_path.startswith("local:"):
        local_path = local_path[6:]

    logger.info(f"[Video {video_id}] Bắt đầu xử lý: {local_path}")

    if not local_path or not Path(local_path).exists():
        logger.warning(f"[Video {video_id}] File không tồn tại: {local_path}")
        supabase.table("videos").update({"status": "error"}).eq("id", video_id).execute()
        return False

    supabase.table("videos").update({"status": "processing"}).eq("id", video_id).execute()

    try:
        out_name = f"annotated_{Path(local_path).stem}.mp4"
        out_path = str(RESULT_FOLDER / out_name)

        detector = get_detector()

        def progress_cb(current, total):
            if total > 0 and current % 150 == 0:
                pct = current * 100 // total
                logger.info(f"[Video {video_id}] {pct}% ({current}/{total} frames)")

        result = detector.detect_video(local_path, output_path=out_path,
                                       frame_skip=3, progress_cb=progress_cb)

        # Upload video annotated lên storage
        annotated_url = upload_to_storage(out_path, f"annotated/{out_name}")
        if not annotated_url:
            annotated_url = out_path   # fallback local path

        # Lưu vi phạm vào DB (đúng tên cột violations thực tế)
        for v in result["violations"]:
            supabase.table("violations").insert({
                "video_id":       video_id,
                "violation_type": v["type"],       # cột: violation_type
                "label":          v["label"],
                "confidence":     v.get("conf", 0),
                "frame_number":   v.get("frame"),  # cột: frame_number
                "box":            str(v.get("box")),
                "extra_data":     str({
                    k: val for k, val in v.items()
                    if k not in ("type", "label", "conf", "frame", "box")
                }),
            }).execute()

        # Cập nhật video
        supabase.table("videos").update({
            "status":          "processed",
            "annotated_url":   annotated_url,
            "violation_count": result["summary"]["total_violations"],
        }).eq("id", video_id).execute()

        s = result["summary"]
        logger.info(
            f"[Video {video_id}] ✓ Xong! "
            f"Vi phạm: {s['total_violations']} "
            f"(MBH: {s['no_helmet_count']}, Chở 3: {s['overloaded_motorcycle_count']})"
        )
        return True

    except Exception as e:
        logger.exception(f"[Video {video_id}] Lỗi khi xử lý")
        supabase.table("videos").update({"status": "error"}).eq("id", video_id).execute()
        return False


def run_once():
    resp = (supabase.table("videos")
            .select("*")
            .eq("status", "pending")
            .execute())

    videos = resp.data
    if not videos:
        logger.info("Không có video nào đang chờ xử lý.")
        return 0

    logger.info(f"Tìm thấy {len(videos)} video đang chờ xử lý.")
    success = sum(1 for v in videos if process_one(v))
    logger.info(f"Hoàn thành: {success}/{len(videos)} video.")
    return success


def run_loop(interval_seconds: int):
    logger.info(f"Worker chạy loop, interval={interval_seconds}s")
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Lỗi trong vòng lặp worker")
        logger.info(f"Chờ {interval_seconds}s...")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Worker xử lý video vi phạm giao thông")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                        help="Lặp mỗi N giây (mặc định: chạy 1 lần rồi thoát)")
    args = parser.parse_args()

    if args.loop > 0:
        run_loop(args.loop)
    else:
        run_once()