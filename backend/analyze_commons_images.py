"""Analyze imported Wikimedia traffic images through the application's API."""

import argparse
import getpass
import logging
import mimetypes
import os
import sys
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import httpx
from supabase import create_client

logger = logging.getLogger("commons_image_batch")
BUCKET = "traffic-dataset"
IMAGE_PREFIX = "images/wikimedia"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_BATCH_SIZE = 500


def list_image_paths(storage, limit):
    """Return image object paths from the Wikimedia import folder."""
    paths = []
    offset = 0
    page_size = min(1000, limit + 1)
    while len(paths) < limit:
        entries = storage.list(
            IMAGE_PREFIX,
            options={"limit": page_size, "offset": offset},
        )
        if not entries:
            break
        for entry in entries:
            name = entry.get("name")
            if not name or PurePosixPath(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            paths.append(f"{IMAGE_PREFIX}/{name}")
            if len(paths) >= limit:
                break
        offset += len(entries)
        if len(entries) < page_size:
            break
    return paths


def _login(client, api_url, email, password):
    response = client.post(
        f"{api_url}/api/auth/login",
        json={"email": email, "password": password},
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"API đăng nhập trả phản hồi không hợp lệ (HTTP {response.status_code}).") from exc
    if response.is_error:
        raise RuntimeError(payload.get("error", "Đăng nhập API thất bại."))
    token = payload.get("token")
    user = payload.get("user") or {}
    if not token or not user.get("id"):
        raise RuntimeError("API đăng nhập không trả về phiên người dùng hợp lệ.")
    return token, user


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Phân tích hàng loạt ảnh Wikimedia đã nằm trong Supabase Storage."
    )
    parser.add_argument("--limit", type=int, default=25, help="Số ảnh tối đa, mặc định 25 (tối đa 500).")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AI_TRAFFIC_API_URL", "http://127.0.0.1:5000"),
        help="URL Flask API đang chạy (mặc định http://127.0.0.1:5000).",
    )
    parser.add_argument(
        "--stop-line",
        action="store_true",
        help="Bật kiểm tra vạch dừng; mặc định tắt cho ảnh giao thông Commons.",
    )
    parser.add_argument(
        "--reanalyze",
        action="store_true",
        help="Phân tích lại ảnh đã có trong lịch sử của tài khoản hiện tại.",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > MAX_BATCH_SIZE:
        parser.error(f"--limit phải nằm trong khoảng 1 đến {MAX_BATCH_SIZE}.")
    parsed_api_url = urlsplit(args.api_url)
    if parsed_api_url.scheme not in {"http", "https"} or not parsed_api_url.netloc:
        parser.error("--api-url cần là URL hợp lệ, ví dụ http://127.0.0.1:5000.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    project_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not project_url or not service_key:
        parser.error("Cần đặt SUPABASE_URL và SUPABASE_SERVICE_ROLE_KEY trong terminal.")
    parsed_project_url = urlsplit(project_url)
    if (
        parsed_project_url.scheme != "https"
        or not parsed_project_url.netloc
        or "<" in project_url
        or ">" in project_url
        or "project-ref" in project_url.lower()
    ):
        parser.error("SUPABASE_URL phải là Project URL thật của Supabase.")

    email = input("Email tài khoản ứng dụng: ").strip()
    password = getpass.getpass("Mật khẩu tài khoản ứng dụng: ")
    if not email or not password:
        parser.error("Email và mật khẩu không được để trống.")

    logger.info("Đang kết nối bucket %s...", BUCKET)
    admin = create_client(project_url, service_key)
    storage = admin.storage.from_(BUCKET)
    paths = list_image_paths(storage, args.limit)
    if not paths:
        print(f"Không tìm thấy ảnh trong {BUCKET}/{IMAGE_PREFIX}.")
        return
    logger.info("Tìm thấy %s ảnh; bắt đầu xử lý tuần tự.", len(paths))

    client = httpx.Client(
        base_url=args.api_url.rstrip("/"),
        timeout=httpx.Timeout(connect=10, read=300, write=300, pool=10),
    )
    try:
        token, user = _login(client, args.api_url.rstrip("/"), email, password)
        headers = {"Authorization": f"Bearer {token}"}
        completed = 0
        skipped = 0
        failed = 0
        for index, storage_path in enumerate(paths, start=1):
            filename = PurePosixPath(storage_path).name
            try:
                if not args.reanalyze:
                    prior = (admin.table("image_analyses").select("id")
                             .eq("owner_id", user["id"])
                             .eq("file_name", filename)
                             .limit(1).execute())
                    if prior.data:
                        skipped += 1
                        logger.info("[%s/%s] Bỏ qua ảnh đã phân tích: %s", index, len(paths), filename)
                        continue

                payload = storage.download(storage_path)
                mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                response = client.post(
                    "/detect/image",
                    headers=headers,
                    data={
                        "enable_stop_line": "true" if args.stop_line else "false",
                        "stop_line_ratio": "0.72",
                        "camera_id": "wikimedia-commons",
                    },
                    files={"file": (filename, payload, mime_type)},
                )
                try:
                    result = response.json()
                except ValueError:
                    raise RuntimeError(f"API phân tích trả phản hồi không hợp lệ (HTTP {response.status_code}).")
                if response.is_error:
                    raise RuntimeError(result.get("error", f"API phân tích thất bại (HTTP {response.status_code})."))
                summary = result.get("summary") or {}
                completed += 1
                logger.info(
                    "[%s/%s] Hoàn tất %s | vi phạm: %s | analysis_id: %s",
                    index,
                    len(paths),
                    filename,
                    summary.get("total_violations", 0),
                    result.get("analysis_id"),
                )
            except Exception:
                failed += 1
                logger.exception("[%s/%s] Không phân tích được %s", index, len(paths), filename)
            if index < len(paths):
                time.sleep(0.5)
        print(
            f"Hoàn tất: {completed} ảnh đã phân tích; "
            f"{skipped} ảnh bỏ qua vì đã có lịch sử; {failed} lỗi."
        )
        print("Các kết quả thành công được lưu trong Lịch sử phân tích ảnh của tài khoản.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
