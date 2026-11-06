"""Import openly licensed traffic media from Wikimedia Commons to Supabase."""

import argparse
from email.utils import parsedate_to_datetime
import html
import json
import logging
import mimetypes
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "AI-Traffic-Violation educational media importer/1.0"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogv", ".ogg", ".mov"}
SEARCH_TERMS = {
    "image": ("traffic intersection", "road traffic", "traffic light"),
    "video": ("road traffic webm", "traffic in city webm", "traffic jam video"),
}
IMAGE_RELEVANCE_TERMS = (
    "traffic", "road", "street", "intersection", "vehicle", "car", "roundabout",
    "motorway", "highway", "crossing", "pedestrian", "motorcycle", "bicycle",
    "bus", "truck",
)
VIDEO_EXCLUDE_TERMS = ("simulation", "traffic model", "air traffic", "tornado")
ALLOWED_MEDIA_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
logger = logging.getLogger("wikimedia_import")
API_REQUEST_INTERVAL_SECONDS = 1.0
API_MAX_ATTEMPTS = 5
MEDIA_DOWNLOAD_INTERVAL_SECONDS = 5.0
_last_api_request_at = 0.0
_last_media_download_at = 0.0


class WikimediaDownloadRateLimit(RuntimeError):
    """Raised when Wikimedia's media CDN rate-limits a download."""


class VideoRecordCreateError(RuntimeError):
    """Raised when a video upload succeeds but its database record fails."""


def _wait_for_api_slot():
    global _last_api_request_at
    elapsed = time.monotonic() - _last_api_request_at
    wait_seconds = API_REQUEST_INTERVAL_SECONDS - elapsed
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_api_request_at = time.monotonic()


def _retry_after_seconds(error):
    value = error.headers.get("Retry-After") if error.headers else None
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _api_get(params):
    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(API_MAX_ATTEMPTS):
        _wait_for_api_slot()
        try:
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            api_error = data.get("error")
            api_error_code = api_error.get("code") if isinstance(api_error, dict) else None
            if (
                api_error_code in {"cirrussearch-too-busy-error", "maxlag"}
                and attempt < API_MAX_ATTEMPTS - 1
            ):
                delay = min(5 * (2 ** attempt), 120)
                logger.warning(
                    "Wikimedia Commons API đang bận (%s); thử lại sau %.0f giây (%s/%s).",
                    api_error_code,
                    delay,
                    attempt + 1,
                    API_MAX_ATTEMPTS - 1,
                )
                time.sleep(delay)
                continue
            break
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt == API_MAX_ATTEMPTS - 1:
                if exc.code == 429:
                    raise RuntimeError(
                        "Wikimedia Commons vẫn giới hạn yêu cầu (HTTP 429) sau "
                        f"{API_MAX_ATTEMPTS} lần thử. Hãy chờ một lúc rồi chạy lại."
                    ) from exc
                raise RuntimeError(
                    f"Wikimedia Commons API trả HTTP {exc.code}: {exc.reason}"
                ) from exc
            backoff = min(5 * (2 ** attempt), 120)
            delay = max(backoff, _retry_after_seconds(exc))
            logger.warning(
                "Wikimedia Commons trả HTTP %s; thử lại sau %.0f giây (%s/%s).",
                exc.code,
                delay,
                attempt + 1,
                API_MAX_ATTEMPTS - 1,
            )
            time.sleep(delay)
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f"Không truy cập được Wikimedia Commons API: {exc}") from exc
    if "error" in data:
        error = data["error"]
        if isinstance(error, dict) and error.get("code") in {
            "cirrussearch-too-busy-error",
            "maxlag",
        }:
            raise RuntimeError(
                "Wikimedia Commons API vẫn đang bận sau nhiều lần thử; "
                "hãy chờ vài phút rồi chạy lại."
            )
        raise RuntimeError(f"Wikimedia Commons API trả lỗi: {data['error']}")
    return data


def _extension(title):
    return os.path.splitext(title.rsplit(":", 1)[-1])[1].lower()


def _search_titles(media_type, limit):
    extensions = IMAGE_EXTENSIONS if media_type == "image" else VIDEO_EXTENSIONS
    seen = set()
    titles = []
    for term in SEARCH_TERMS[media_type]:
        continuation = {}
        while len(titles) < limit:
            params = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "list": "search",
                "srnamespace": 6,
                "srlimit": 50,
                "srsearch": term,
                **continuation,
            }
            data = _api_get(params)
            for item in data.get("query", {}).get("search", []):
                title = item["title"]
                if title not in seen and _extension(title) in extensions:
                    seen.add(title)
                    titles.append(title)
                    if len(titles) >= limit:
                        break
            continuation = data.get("continue", {})
            if not continuation:
                break
        if len(titles) >= limit:
            break
    return titles


def _plain_text(value):
    if isinstance(value, dict):
        value = value.get("value", "")
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()


def _get_media_info(titles):
    media = []
    for offset in range(0, len(titles), 50):
        data = _api_get({
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1280,
            "titles": "|".join(titles[offset:offset + 50]),
        })
        for page in data.get("query", {}).get("pages", []):
            imageinfo = (page.get("imageinfo") or [{}])[0]
            metadata = imageinfo.get("extmetadata", {})
            license_name = _plain_text(metadata.get("LicenseShortName"))
            license_url = _plain_text(metadata.get("LicenseUrl"))
            description = _plain_text(metadata.get("ImageDescription"))
            searchable_text = f"{page['title']} {description}".lower()
            extension = _extension(page["title"])
            if extension in IMAGE_EXTENSIONS and not any(
                term in searchable_text for term in IMAGE_RELEVANCE_TERMS
            ):
                logger.info("Bỏ qua ảnh không liên quan rõ ràng đến giao thông: %s", page["title"])
                continue
            if extension in VIDEO_EXTENSIONS:
                if any(term in searchable_text for term in VIDEO_EXCLUDE_TERMS):
                    logger.info("Bỏ qua video không phải cảnh giao thông đường bộ: %s", page["title"])
                    continue
                if not any(term in searchable_text for term in ("traffic", "road", "street", "vehicle", "car")):
                    continue
            if not imageinfo.get("url") or not license_name:
                logger.warning("Bỏ qua %s vì thiếu URL hoặc thông tin giấy phép.", page["title"])
                continue
            is_image = extension in IMAGE_EXTENSIONS
            download_url = imageinfo.get("thumburl") if is_image else imageinfo.get("url")
            if not download_url:
                logger.warning(
                    "Bỏ qua %s vì Wikimedia không cung cấp URL thumbnail.", page["title"]
                )
                continue
            media.append({
                "title": page["title"],
                "page_id": page.get("pageid"),
                "url": download_url,
                "size": int(
                    (imageinfo.get("thumbsize") if is_image else None)
                    or imageinfo.get("size")
                    or 0
                ),
                "mime": imageinfo.get("mime") or "application/octet-stream",
                "is_thumbnail": is_image,
                "license": license_name,
                "license_url": license_url,
                "author": _plain_text(metadata.get("Artist")),
                "credit": _plain_text(metadata.get("Credit")),
                "description": description,
                "source_page": (
                    "https://commons.wikimedia.org/wiki/"
                    + quote(page["title"].replace(" ", "_"), safe=":_()")
                ),
            })
    return media


def _safe_filename(title):
    original = title.rsplit(":", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "_", original).strip("._") or "media"


def _download(media, max_bytes):
    global _last_media_download_at
    if media["size"] > max_bytes:
        raise ValueError(f"Tệp vượt giới hạn kích thước {max_bytes // (1024 * 1024)} MB.")
    parsed_url = urlsplit(media["url"])
    if parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_MEDIA_HOSTS:
        raise ValueError("URL tải xuống không thuộc máy chủ media chính thức của Wikimedia.")
    elapsed = time.monotonic() - _last_media_download_at
    wait_seconds = MEDIA_DOWNLOAD_INTERVAL_SECONDS - elapsed
    if wait_seconds > 0:
        logger.info("Chờ %.0f giây giữa các lần tải từ Wikimedia...", wait_seconds)
        time.sleep(wait_seconds)
    _last_media_download_at = time.monotonic()
    request = Request(media["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            chunks = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Tệp tải về vượt giới hạn kích thước đã đặt.")
                chunks.append(chunk)
    except HTTPError as exc:
        if exc.code == 429:
            raise WikimediaDownloadRateLimit(
                "Wikimedia CDN từ chối tải (HTTP 429). Đã dừng các lượt tải tiếp theo "
                "để tránh gửi thêm yêu cầu; hãy chờ ít nhất 15 phút trước khi thử lại. "
                "Ảnh dùng thumbnail 1280 px, video vẫn cần tải tệp media."
            ) from exc
        raise RuntimeError(f"Không tải được {media['title']}: HTTP {exc.code} {exc.reason}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Không tải được {media['title']}: {exc}") from exc
    return b"".join(chunks)


def _content_type(media):
    if media.get("is_thumbnail"):
        return mimetypes.guess_type(media["url"])[0] or "image/jpeg"
    return media["mime"] or mimetypes.guess_type(media["title"])[0] or "application/octet-stream"


def _manifest_entry(media, kind, bucket, storage_path):
    return {
        "media_type": kind,
        "title": media["title"],
        "source_page": media["source_page"],
        "author": media["author"],
        "credit": media["credit"],
        "license": media["license"],
        "license_url": media["license_url"],
        "storage_bucket": bucket,
        "storage_path": storage_path,
    }


def _upload(client, media, kind, max_bytes):
    filename = _safe_filename(media["title"])
    if kind == "image":
        bucket = "traffic-dataset"
        storage_path = f"images/wikimedia/{media['page_id']}-{filename}"
    else:
        bucket = "videos"
        storage_path = f"dataset/wikimedia/{media['page_id']}-{filename}"
        existing = (client.table("videos").select("id")
                    .eq("storage_path", storage_path).limit(1).execute())
        if existing.data:
            logger.info("Video đã được nhập trước đó, bỏ qua: %s", media["title"])
            return None

    content = _download(media, max_bytes)
    client.storage.from_(bucket).upload(
        path=storage_path,
        file=content,
        file_options={"content-type": _content_type(media), "upsert": "true"},
    )

    if kind == "video":
        try:
            record = client.table("videos").insert({
                "file_name": filename,
                "file_url": storage_path,
                "storage_path": storage_path,
                "status": "pending",
                "processing_options": {
                    "frame_skip": 3,
                    "enable_stop_line": False,
                    "camera_id": "wikimedia-commons",
                    "source": "Wikimedia Commons",
                    "source_page": media["source_page"],
                    "author": media["author"],
                    "license": media["license"],
                    "license_url": media["license_url"],
                },
            }).execute()
        except Exception as exc:
            raise VideoRecordCreateError(
                f"Video đã upload vào {bucket}/{storage_path} nhưng tạo bản ghi "
                "trong bảng videos thất bại."
            ) from exc
        if not record.data:
            raise VideoRecordCreateError(
                f"Video đã upload vào {bucket}/{storage_path} nhưng Supabase không "
                "trả bản ghi mới."
            )

    return _manifest_entry(media, kind, bucket, storage_path)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Tải ảnh/video giao thông từ Wikimedia Commons lên Supabase."
    )
    parser.add_argument("--images", type=int, default=20, help="Số ảnh tối đa (mặc định: 20).")
    parser.add_argument("--videos", type=int, default=3, help="Số video tối đa (mặc định: 3).")
    parser.add_argument("--max-image-mb", type=int, default=20, help="Giới hạn MB mỗi ảnh.")
    parser.add_argument("--max-video-mb", type=int, default=100, help="Giới hạn MB mỗi video.")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ liệt kê, không tải lên Supabase.")
    args = parser.parse_args()
    if min(args.images, args.videos) < 0 or min(args.max_image_mb, args.max_video_mb) < 1:
        parser.error("Số lượng không được âm và giới hạn dung lượng phải từ 1 MB.")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    project_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not args.dry_run:
        if not project_url or not service_key:
            parser.error("Cần đặt SUPABASE_URL và SUPABASE_SERVICE_ROLE_KEY trong terminal.")
        parsed_url = urlsplit(project_url)
        if (
            parsed_url.scheme != "https"
            or not parsed_url.netloc
            or "<" in project_url
            or ">" in project_url
            or "project-ref" in project_url.lower()
        ):
            parser.error(
                "SUPABASE_URL chưa hợp lệ. Hãy thay https://<project-ref>.supabase.co "
                "bằng Project URL thật trong Supabase Dashboard."
            )

    selected = []
    for kind, count in (("image", args.images), ("video", args.videos)):
        if not count:
            continue
        logger.info("Đang tìm tối đa %s %s trên Wikimedia Commons...", count, kind)
        titles = _search_titles(kind, count * 5)
        candidates = _get_media_info(titles)
        size_mb = args.max_image_mb if kind == "image" else args.max_video_mb
        eligible = [
            item for item in candidates
            if item["size"] <= size_mb * 1024 * 1024
        ]
        selected.extend((kind, item) for item in eligible[:count])
        logger.info("Đã tìm thấy %s %s đủ điều kiện.", min(count, len(eligible)), kind)

    if args.dry_run:
        for kind, media in selected:
            print(f"{kind}: {media['title']} | {media['license']} | {media['source_page']}")
        print(f"Chế độ xem trước: tìm thấy {len(selected)} tệp có thông tin giấy phép.")
        return

    if not selected:
        print("Không tìm thấy tệp phù hợp; chưa kết nối hoặc upload lên Supabase.")
        return

    logger.info("Đang nạp thư viện Supabase và kết nối project...")
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError(
            "Không nạp được thư viện supabase trong môi trường Python hiện tại. "
            "Hãy kiểm tra môi trường ảo của project."
        ) from exc
    client = create_client(project_url, service_key)

    manifest = []
    failures = 0
    queued_videos = 0
    skipped_videos = 0
    stopped_by_rate_limit = False
    possible_orphan_videos = []
    for index, (kind, media) in enumerate(selected, start=1):
        limit_mb = args.max_image_mb if kind == "image" else args.max_video_mb
        try:
            logger.info("[%s/%s] Đang upload %s: %s", index, len(selected), kind, media["title"])
            entry = _upload(client, media, kind, limit_mb * 1024 * 1024)
            if entry:
                manifest.append(entry)
                if kind == "video":
                    queued_videos += 1
                logger.info("Đã upload %s: %s", kind, media["title"])
            elif kind == "video":
                skipped_videos += 1
        except WikimediaDownloadRateLimit as exc:
            failures += 1
            stopped_by_rate_limit = True
            logger.error("%s", exc)
            break
        except VideoRecordCreateError as exc:
            failures += 1
            possible_orphan_videos.append(str(exc))
            logger.exception("Video đã vào Storage nhưng chưa vào hàng đợi: %s", media["title"])
        except Exception:
            failures += 1
            logger.exception("Không thể nhập %s", media["title"])

    if manifest:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        manifest_path = f"manifests/wikimedia-{timestamp}.json"
        client.storage.from_("traffic-dataset").upload(
            path=manifest_path,
            file=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "false"},
        )
        print(f"Đã lưu danh sách giấy phép/ghi công tại traffic-dataset/{manifest_path}")
    print(
        f"Hoàn tất: {len(manifest)} mục nhập đầy đủ; "
        f"{queued_videos} video đã vào hàng đợi; "
        f"{skipped_videos} video đã tồn tại; {failures} lỗi."
    )
    if queued_videos:
        print("Cần worker đang chạy để phân tích các video trong hàng đợi.")
    if stopped_by_rate_limit:
        print("Đã dừng import sớm do Wikimedia CDN giới hạn tải; các tệp còn lại chưa được thử.")
    for orphan in possible_orphan_videos:
        print(orphan)
    print("Ảnh chỉ được lưu vào Storage, không tự tạo lịch sử phân tích hay kết luận vi phạm.")


if __name__ == "__main__":
    main()
