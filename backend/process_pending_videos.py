"""Durable Supabase video worker.

Run one worker per deployment:
    python process_pending_videos.py --loop 5
"""

import argparse
import ast
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from ai_detector import get_detector
from supabase_client import get_admin_client, supabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RESULT_FOLDER = Path(__file__).parent / "results"
UPLOAD_FOLDER = Path(__file__).parent / "uploads"
RESULT_FOLDER.mkdir(exist_ok=True)
UPLOAD_FOLDER.mkdir(exist_ok=True)
MAX_ATTEMPTS = 3
MAX_ANNOTATED_UPLOAD_BYTES = 45_000_000


def ffmpeg_executable():
    configured_path = os.environ.get("FFMPEG_PATH")
    if configured_path:
        executable = Path(configured_path).expanduser()
        if executable.is_file():
            return str(executable)
        raise FileNotFoundError(f"Không tìm thấy FFmpeg tại FFMPEG_PATH: {executable}")

    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates = glob.glob(
            str(Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
                / "Gyan.FFmpeg.Shared_*" / "ffmpeg-*" / "bin" / "ffmpeg.exe")
        )
        if candidates:
            return max(candidates, key=lambda path: Path(path).stat().st_mtime)

    raise FileNotFoundError(
        "Không tìm thấy FFmpeg. Cài FFmpeg và thêm vào PATH, hoặc đặt biến FFMPEG_PATH."
    )


def transcode_browser_video(local_path):
    local_path = Path(local_path)
    executable = ffmpeg_executable()
    temporary_path = local_path.with_name(f"{local_path.stem}.h264.tmp.mp4")
    try:
        for quality in (28, 32, 36):
            completed = subprocess.run(
                [
                    executable, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(local_path), "-an",
                    "-c:v", "libx264", "-preset", "fast",
                    "-crf", str(quality), "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", str(temporary_path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                details = (completed.stderr or "").strip()[-1000:]
                raise RuntimeError(
                    f"FFmpeg không thể mã hóa video đầu ra thành H.264."
                    f"{' ' + details if details else ''}"
                )

            if temporary_path.stat().st_size <= MAX_ANNOTATED_UPLOAD_BYTES:
                os.replace(temporary_path, local_path)
                logger.info(
                    "H.264 browser-compatible result ready: %s (%.1f MB, CRF %s)",
                    local_path.name,
                    local_path.stat().st_size / 1_000_000,
                    quality,
                )
                return local_path

            temporary_path.unlink()

        raise ValueError(
            "Video H.264 kết quả vẫn vượt giới hạn upload an toàn 45 MB "
            "sau khi nén; hãy giảm độ phân giải/FPS hoặc tăng giới hạn bucket."
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def _box_iou(first, second):
    if not isinstance(first, (list, tuple)) or not isinstance(second, (list, tuple)):
        return 0.0
    if len(first) != 4 or len(second) != 4:
        return 0.0
    ax1, ay1, ax2, ay2 = (float(value) for value in first)
    bx1, by1, bx2, by2 = (float(value) for value in second)
    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union else 0.0


def _license_plate(violation):
    plate = violation.get("license_plate")
    if not plate or str(plate).strip().lower() == "chưa rõ":
        extra = violation.get("extra_data") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except json.JSONDecodeError:
                try:
                    extra = ast.literal_eval(extra)
                except (ValueError, SyntaxError):
                    extra = {}
        plate = extra.get("license_plate", "") if isinstance(extra, dict) else ""
    if not plate or str(plate).strip().lower() == "chưa rõ":
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(plate).upper())


def deduplicate_video_events(violations, fps, frame_skip=1, max_gap_seconds=3):
    """Merge consecutive same-type detections for one spatially consistent vehicle."""
    max_gap_frames = max(
        1,
        round(max(1.0, fps) * max_gap_seconds),
        int(frame_skip) * 3,
    )
    events = []
    for violation in sorted(violations, key=lambda item: item.get("frame", 0)):
        frame = int(violation.get("frame", 0))
        plate = _license_plate(violation)
        match = None
        for event in reversed(events):
            if frame - event["last_frame"] > max_gap_frames:
                continue
            if event["type"] != violation.get("type"):
                continue
            same_plate = plate and plate == event.get("plate")
            if same_plate or _box_iou(violation.get("box"), event.get("box")) >= 0.2:
                match = event
                break

        if match is None:
            events.append({
                **violation,
                "first_frame": frame,
                "last_frame": frame,
                "occurrence_count": 1,
                "plate": plate,
            })
            continue

        match["last_frame"] = frame
        match["occurrence_count"] += 1
        if plate and not match.get("plate"):
            match["plate"] = plate
        if float(violation.get("conf", 0)) > float(match.get("conf", 0)):
            first_frame = match["first_frame"]
            last_frame = match["last_frame"]
            occurrence_count = match["occurrence_count"]
            match.update(violation)
            match["first_frame"] = first_frame
            match["last_frame"] = last_frame
            match["occurrence_count"] = occurrence_count
            match["plate"] = plate or match.get("plate", "")

    for event in events:
        event.pop("plate", None)
    return events


def summarize_events(events):
    count_for = lambda violation_type: sum(
        item.get("type") == violation_type for item in events
    )
    plates = sorted({
        str(item.get("license_plate", "")).strip()
        for item in events
        if item.get("license_plate") and item.get("license_plate") != "Chưa rõ"
    })
    return {
        "has_violations": bool(events),
        "total_violations": len(events),
        "no_helmet_count": count_for("no_helmet"),
        "overloaded_motorcycle_count": count_for("overloaded_motorcycle"),
        "red_light_count": count_for("red_light"),
        "detected_plates": plates,
    }


def database():
    client = get_admin_client()
    if client is None:
        raise RuntimeError("Set SUPABASE_SERVICE_ROLE_KEY before starting the video worker.")
    return client


def record_heartbeat(client):
    client.table("worker_heartbeats").upsert({
        "worker_name": "video",
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "details": {"max_attempts": MAX_ATTEMPTS},
    }, on_conflict="worker_name").execute()


def heartbeat_loop(client, stop_event, interval_seconds=10):
    while not stop_event.wait(interval_seconds):
        try:
            record_heartbeat(client)
        except Exception:
            logger.exception("Could not update video worker heartbeat")


def video_storage_path(record):
    storage_path = record.get("storage_path")
    if storage_path:
        return storage_path

    file_url = record.get("file_url")
    if not isinstance(file_url, str) or not file_url.strip():
        return None
    if not file_url.startswith(("http://", "https://")):
        return file_url

    url_path = urlparse(file_url).path
    for access_type in ("public", "sign", "authenticated"):
        marker = f"/object/{access_type}/videos/"
        if marker in url_path:
            return unquote(url_path.split(marker, 1)[1])
    return None


def download_video(record, client):
    stored_path = record.get("local_path")
    if stored_path and Path(stored_path).is_file():
        return Path(stored_path)

    storage_path = video_storage_path(record)
    if not storage_path:
        raise FileNotFoundError(
            "Video has no local file, storage_path, or usable file_url."
        )

    destination = UPLOAD_FOLDER / Path(storage_path).name
    payload = client.storage.from_("videos").download(storage_path)
    destination.write_bytes(payload)
    return destination


def upload_result(client, local_path, storage_path):
    local_path = Path(local_path)
    file_size = local_path.stat().st_size
    if file_size > MAX_ANNOTATED_UPLOAD_BYTES:
        raise ValueError(
            f"Video kết quả quá lớn ({file_size / 1_000_000:.1f} MB); "
            f"giới hạn upload an toàn là {MAX_ANNOTATED_UPLOAD_BYTES / 1_000_000:.0f} MB. "
            "Hãy giảm độ phân giải/FPS hoặc tăng giới hạn bucket Storage."
        )
    logger.info(
        "Uploading annotated result %s (%.1f MB)",
        local_path.name, file_size / 1_000_000,
    )
    payload = local_path.read_bytes()
    client.storage.from_("violations").upload(
        path=storage_path,
        file=payload,
        file_options={"content-type": "video/mp4", "upsert": "true"},
    )
    return storage_path


def process_one(record):
    client = database()
    video_id = record["id"]
    attempts = record.get("processing_attempts", 1)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        args=(client, heartbeat_stop),
        name="video-worker-heartbeat",
        daemon=True,
    )
    try:
        heartbeat_thread.start()
        source = download_video(record, client)
        options = record.get("processing_options") or {}
        frame_skip = max(1, min(10, int(options.get("frame_skip", 3))))
        stop_ratio = float(options.get("stop_line_ratio", 0.72))
        stop_angle = float(options.get("stop_line_angle", 0))
        enable_stop_line = bool(options.get("enable_stop_line", True))
        output_path = RESULT_FOLDER / f"annotated_{video_id}_{source.stem}.mp4"
        last_progress = -10

        def report_progress(current, total):
            nonlocal last_progress
            if not total:
                return
            progress = min(99, int(current * 100 / total))
            if progress - last_progress >= 5:
                client.table("videos").update({
                    "processing_progress": progress,
                }).eq("id", video_id).eq("status", "processing").execute()
                last_progress = progress

        logger.info("[video=%s] processing started attempt=%s", video_id, attempts)
        detector = get_detector()
        result = detector.detect_video(
            str(source),
            output_path=str(output_path),
            frame_skip=frame_skip,
            stop_line_ratio=stop_ratio,
            enable_stop_line=enable_stop_line,
            progress_cb=report_progress,
            stop_line_angle=stop_angle,
        )

        deduplicated = deduplicate_video_events(
            result["violations"], result.get("fps", 30), frame_skip
        )
        result["violations"] = deduplicated
        result["summary"] = summarize_events(deduplicated)

        client.table("videos").update({
            "processing_progress": 95,
        }).eq("id", video_id).eq("status", "processing").execute()
        transcode_browser_video(output_path)
        client.table("videos").update({
            "processing_progress": 96,
        }).eq("id", video_id).eq("status", "processing").execute()
        annotated_key = f"annotated/{output_path.name}"
        annotated_url = upload_result(client, output_path, annotated_key)
        client.table("videos").update({
            "processing_progress": 98,
        }).eq("id", video_id).eq("status", "processing").execute()
        client.table("violations").delete().eq("video_id", video_id).execute()
        for violation in result["violations"]:
            extra = {
                key: value for key, value in violation.items()
                if key not in ("type", "label", "conf", "frame", "box")
            }
            client.table("violations").insert({
                "video_id": video_id,
                "violation_type": violation["type"],
                "label": violation["label"],
                "confidence": violation.get("conf", 0),
                "frame_number": violation.get("first_frame", violation.get("frame")),
                "first_frame": violation.get("first_frame", violation.get("frame")),
                "last_frame": violation.get("last_frame", violation.get("frame")),
                "occurrence_count": violation.get("occurrence_count", 1),
                "box": str(violation.get("box")),
                "extra_data": str(extra),
            }).execute()

        client.table("videos").update({
            "status": "processed",
            "processing_progress": 100,
            "processing_error": None,
            "annotated_url": annotated_url,
            "violation_count": result["summary"]["total_violations"],
        }).eq("id", video_id).eq("status", "processing").execute()
        try:
            client.table("audit_logs").insert({
                "actor_id": record.get("upload_by"),
                "action": "video_processed",
                "resource_type": "video",
                "resource_id": str(video_id),
                "details": {"violations": result["summary"]["total_violations"]},
            }).execute()
        except Exception:
            logger.exception("[video=%s] processed but audit log write failed", video_id)
        logger.info("[video=%s] processing complete violations=%s",
                    video_id, result["summary"]["total_violations"])
        return True
    except Exception as exc:
        logger.exception("[video=%s] processing failed attempt=%s", video_id, attempts)
        new_status = "error" if attempts >= MAX_ATTEMPTS else "pending"
        try:
            client.table("videos").update({
                "status": new_status,
                "processing_error": str(exc)[:1000],
            }).eq("id", video_id).eq("status", "processing").execute()
            client.table("audit_logs").insert({
                "actor_id": record.get("upload_by"),
                "action": "video_processing_failed",
                "resource_type": "video",
                "resource_id": str(video_id),
                "details": {"attempt": attempts, "error": str(exc)[:500]},
            }).execute()
        except Exception:
            logger.exception("[video=%s] could not persist failure state", video_id)
        return False
    finally:
        heartbeat_stop.set()
        if heartbeat_thread.is_alive():
            heartbeat_thread.join(timeout=2)


def run_once():
    client = database()
    record_heartbeat(client)
    response = client.rpc("claim_next_video_job", {}).execute()
    if not response.data:
        return 0
    record = response.data[0] if isinstance(response.data, list) else response.data
    process_one(record)
    return 1


def run_loop(interval_seconds):
    logger.info("Video worker started poll_interval=%ss max_attempts=%s", interval_seconds, MAX_ATTEMPTS)
    while True:
        try:
            claimed = run_once()
            if not claimed:
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Video worker stopped.")
            return
        except Exception:
            logger.exception("Video worker poll failed")
            time.sleep(interval_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supabase video processing worker")
    parser.add_argument("--loop", type=int, default=5, metavar="SECONDS")
    args = parser.parse_args()
    if args.loop < 1:
        parser.error("--loop must be at least one second")
    run_loop(args.loop)
