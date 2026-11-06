"""
Flask Backend - AI Traffic Violation Detection API

Tích hợp:
1. Phân quyền người dùng (CSGT/Admin vs Viewer)
2. AI Nhận diện Không MBH, Xe chở quá số người (Logic ghép người - xe cải tiến)
3. AI Phát hiện phương tiện Vượt đèn đỏ
4. Nhận dạng biển số xe (License Plate Recognition - ALPR)
5. Duyệt vi phạm phạt nguội, chỉnh sửa và quản lý video/vi phạm
"""

import os
import json
import ast
import uuid
import shutil
import logging
import csv
import io
import re
from urllib.parse import unquote, urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from supabase_client import SUPABASE_URL, get_admin_client, supabase
from ai_detector import get_detector
from auth import (
    authenticate, get_current_user, require_role,
    register_user, create_admin_user, list_all_users,
    resend_signup_confirmation, request_password_reset,
    update_recovery_password,
    ACTIVE_SESSIONS, VIOLATION_REVIEWS
)

# ── Cấu hình ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_FOLDER = Path(__file__).parent.parent / "frontend"

app = Flask(
    __name__,
    static_folder=str(FRONTEND_FOLDER),
    static_url_path=""
)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5000,http://127.0.0.1:5000,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",")
    if origin.strip()
]
CORS(app, origins=CORS_ORIGINS)
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
    """Upload file lên Supabase Storage."""
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        storage_client = get_admin_client() or supabase
        storage_client.storage.from_(bucket).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        logger.info(f"Storage upload OK: {bucket}/{storage_path}")
        return storage_path
    except Exception as e:
        logger.warning(f"Storage upload thất bại ({bucket}/{storage_path}): {e}")
        return None


def create_signed_storage_url(bucket: str, storage_path: str, expires_in: int = 600) -> str:
    admin = get_admin_client()
    if admin is None:
        raise RuntimeError("Tệp riêng tư cần SUPABASE_SERVICE_ROLE_KEY.")
    response = admin.storage.from_(bucket).create_signed_url(storage_path, expires_in)
    signed_url = response.get("signedURL") or response.get("signedUrl")
    if not signed_url:
        raise RuntimeError(f"Supabase không tạo được URL tạm cho {bucket}/{storage_path}.")
    return signed_url


def storage_path_from_url(bucket: str, value: str) -> str | None:
    if not value.startswith(("http://", "https://")):
        return value
    path = urlparse(value).path
    for access_type in ("public", "sign"):
        marker = f"/object/{access_type}/{bucket}/"
        if marker in path:
            return unquote(path.split(marker, 1)[1])
    return None


def get_video_content_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
    }.get(ext, "video/mp4")


def image_content_type(filename: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(Path(filename).suffix.lower(), "application/octet-stream")


def upload_storage_file(bucket: str, storage_path: str, local_path: Path, content_type: str) -> None:
    admin = get_admin_client()
    if admin is None:
        raise RuntimeError("Lưu lịch sử và bằng chứng cần SUPABASE_SERVICE_ROLE_KEY.")
    admin.storage.from_(bucket).upload(
        path=storage_path,
        file=local_path.read_bytes(),
        file_options={"content-type": content_type, "upsert": "true"},
    )


def delete_storage_files(bucket: str, storage_paths: list[str]) -> None:
    if not storage_paths:
        return
    admin = get_admin_client()
    if admin is not None:
        admin.storage.from_(bucket).remove(storage_paths)


def enrich_violation(v: dict) -> dict:
    """Bổ sung trường license_plate, review_status, notes cho record vi phạm."""
    v_id = v.get("id")
    review = VIOLATION_REVIEWS.get(v_id, {})

    # Trích xuất license_plate từ extra_data nếu chưa có
    plate = review.get("license_plate")
    if not plate:
        extra_raw = v.get("extra_data") or "{}"
        try:
            if isinstance(extra_raw, str):
                try:
                    extra = json.loads(extra_raw)
                except json.JSONDecodeError:
                    extra = ast.literal_eval(extra_raw)
            else:
                extra = extra_raw
            if isinstance(extra, dict):
                plate = extra.get("license_plate", "")
        except Exception:
            plate = ""

    v["license_plate"] = plate or "Chưa rõ"
    v["review_status"] = v.get("review_status") or review.get("review_status", "pending")
    v["notes"]         = v.get("notes") or review.get("notes", "")
    v["reviewed_by"]   = v.get("reviewed_by") or review.get("reviewed_by", "")
    return v


def parse_pagination(default_limit=50, maximum_limit=200):
    try:
        limit = int(request.args.get("limit", default_limit))
        offset = int(request.args.get("offset", 0))
    except ValueError as exc:
        raise ValueError("limit và offset phải là số nguyên.") from exc
    if limit < 1 or limit > maximum_limit or offset < 0:
        raise ValueError(f"limit phải từ 1 đến {maximum_limit}, offset không được âm.")
    return limit, offset


def write_audit_log(action, resource_type, resource_id=None, details=None):
    admin = get_admin_client()
    if admin is None:
        raise RuntimeError("Audit log chưa khả dụng: cần cấu hình SUPABASE_SERVICE_ROLE_KEY.")
    actor = get_current_user() or {}
    admin.table("audit_logs").insert({
        "actor_id": actor.get("id"),
        "actor_email": actor.get("email"),
        "action": action,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "details": details or {},
    }).execute()


def database_client():
    return get_admin_client() or supabase


# ── Static / Web Routes ────────────────────────────────────────────────────

@app.route("/")
def home():
    index_file = FRONTEND_FOLDER / "index.html"
    if index_file.exists():
        return send_file(str(index_file))
    return jsonify({
        "message": "AI Traffic Violation Backend Running (Frontend index.html not found)",
        "api_endpoints": "/api"
    })


@app.route("/api")
def api_docs():
    return jsonify({
        "message": "AI Traffic Violation Backend Running",
        "endpoints": [
            "POST /api/auth/login      – Đăng nhập (CSGT/Admin, Viewer)",
            "GET  /api/auth/me         – Thông tin phiên đăng nhập",
            "POST /api/auth/logout     – Đăng xuất",
            "POST /api/auth/password-reset – Gửi liên kết đặt lại mật khẩu",
            "POST /api/auth/resend-confirmation – Gửi lại email xác minh",
            "GET  /api/cameras/<id>    – Đọc cấu hình camera",
            "PUT  /api/cameras/<id>    – Lưu cấu hình camera",
            "GET  /api/dashboard       – Số liệu dashboard",
            "GET  /api/dashboard/trends – Xu hướng vi phạm theo ngày/tuần",
            "GET  /api/audit-logs      – Nhật ký thao tác (Admin)",
            "GET  /image-analyses      – Lịch sử phân tích ảnh",
            "GET  /image-analyses/<id> – Chi tiết phân tích ảnh",
            "GET  /violations/<id>/report – Báo cáo hồ sơ và bằng chứng",
            "GET  /videos/<id>         – Trạng thái xử lý video",
            "GET  /violations/export.csv – Xuất báo cáo CSV",
            "POST /detect/image        – Phân tích ảnh trực tiếp",
            "POST /detect/video        – Phân tích video trực tiếp",
            "POST /upload-video        – Upload video → lưu Supabase (pending)",
            "POST /process-video/<id>  – Xử lý video pending theo ID",
            "GET  /videos              – Danh sách video",
            "DELETE /videos/<id>       – Xóa video (Chỉ CSGT/Admin)",
            "GET  /violations          – Danh sách vi phạm",
            "PATCH /violations/<id>    – Duyệt / Chỉnh sửa vi phạm (Chỉ CSGT/Admin)",
            "DELETE /violations/<id>   – Xóa vi phạm (Chỉ CSGT/Admin)",
            "GET  /result/<filename>   – Tải file đã annotate",
        ]
    })


@app.route("/health")
def health():
    admin = get_admin_client()
    status = {
        "api": "ok",
        "database": "unknown",
        "anon_database": "unknown",
        "video_worker": "unconfigured",
        "service_role_configured": admin is not None,
        "service_role": "unknown" if admin is not None else "missing",
    }

    try:
        supabase.table("videos").select("id").limit(1).execute()
        status["anon_database"] = "ok"
    except Exception as exc:
        status["anon_database"] = "error"
        if _is_invalid_supabase_key(exc):
            status["anon_database"] = "invalid_api_key"
        logger.warning("Supabase anon-key health check failed: %s", exc)

    if admin is not None:
        try:
            admin.table("videos").select("id").limit(1).execute()
            status["database"] = "ok"
            status["service_role"] = "ok"
        except Exception as exc:
            status["database"] = "error"
            status["service_role"] = (
                "invalid_api_key" if _is_invalid_supabase_key(exc) else "error"
            )
            status["error_code"] = (
                "SUPABASE_INVALID_API_KEY"
                if _is_invalid_supabase_key(exc)
                else "SUPABASE_DATABASE_UNAVAILABLE"
            )
            logger.warning("Supabase service-role health check failed: %s", exc)
            return jsonify(status), 503

        try:
            response = (admin.table("worker_heartbeats").select("last_seen")
                        .eq("worker_name", "video").limit(1).execute())
            if response.data:
                last_seen = datetime.fromisoformat(response.data[0]["last_seen"].replace("Z", "+00:00"))
                age_seconds = (datetime.now(timezone.utc) - last_seen).total_seconds()
                status["video_worker"] = "ok" if age_seconds <= 30 else "stale"
            else:
                status["video_worker"] = "not_running"
        except Exception as exc:
            if _is_missing_worker_heartbeat_table(exc):
                status["video_worker"] = "schema_missing"
                status["error_code"] = "SUPABASE_WORKER_SCHEMA_MISSING"
                logger.warning(
                    "Supabase worker_heartbeats table is missing; apply the latest "
                    "supabase_schema.sql migration."
                )
            else:
                status["video_worker"] = "error"
                logger.warning("Supabase worker heartbeat check failed: %s", exc)
        return jsonify(status)

    if status["anon_database"] == "ok":
        status["database"] = "ok"
        return jsonify(status)

    status["database"] = "error"
    status["error_code"] = (
        "SUPABASE_INVALID_API_KEY"
        if status["anon_database"] == "invalid_api_key"
        else "SUPABASE_DATABASE_UNAVAILABLE"
    )
    return jsonify(status), 503


def _is_invalid_supabase_key(exc):
    message = str(exc).lower()
    return "invalid api key" in message or "invalid api key" in str(getattr(exc, "message", "")).lower()


def _is_missing_worker_heartbeat_table(exc):
    message = str(exc).lower()
    return "pgrst205" in message and "worker_heartbeats" in message


# ── Authentication Routes ──────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    """
    Đăng ký tài khoản người dùng mới.
    Mặc định vai trò là 'viewer' và lưu vào Supabase.
    """
    data = request.get_json(silent=True) or request.form
    username  = str(data.get("username") or "").strip()
    password  = str(data.get("password") or "")
    full_name = str(data.get("full_name") or "").strip()
    email     = str(data.get("email") or "").strip()

    if not username or not password or not full_name or not email:
        return jsonify({"error": "Vui lòng nhập đầy đủ họ tên, tên đăng nhập, email và mật khẩu"}), 400

    if len(password) < 6:
        return jsonify({"error": "Mật khẩu cần tối thiểu 6 ký tự"}), 400

    user, token, err = register_user(username, password, full_name, email)
    if err:
        return jsonify({"error": err}), 400

    return jsonify({
        "message": "Đăng ký tài khoản thành công!",
        "token": token,
        "user": user,
        "requires_email_confirmation": token is None,
    })


@app.route("/api/auth/resend-confirmation", methods=["POST"])
def resend_confirmation():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "Vui lòng nhập email"}), 400
    try:
        resend_signup_confirmation(email)
        return jsonify({"message": "Nếu email cần xác minh, Supabase đã gửi hướng dẫn xác minh."})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/password-reset", methods=["POST"])
def password_reset():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "Vui lòng nhập email"}), 400
    redirect_to = os.environ.get("SUPABASE_AUTH_REDIRECT_URL")
    try:
        request_password_reset(email, redirect_to)
        return jsonify({"message": "Nếu email đã đăng ký, hướng dẫn đặt lại mật khẩu sẽ được gửi tới hộp thư."})
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/password-reset/complete", methods=["POST"])
def complete_password_reset():
    data = request.get_json(silent=True) or {}
    access_token = str(data.get("access_token") or "")
    refresh_token = str(data.get("refresh_token") or "")
    password = str(data.get("password") or "")
    if not access_token or not refresh_token:
        return jsonify({"error": "Liên kết đặt lại mật khẩu thiếu token hoặc đã hết hạn."}), 400
    try:
        update_recovery_password(access_token, refresh_token, password)
        return jsonify({"message": "Đổi mật khẩu thành công. Bạn có thể đăng nhập bằng mật khẩu mới."})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/auth/create-admin", methods=["POST"])
@require_role("admin")
def create_admin():
    """
    Quản trị viên / CSGT thêm một Quản trị viên mới.
    Chỉ tài khoản admin mới có quyền gọi.
    """
    data = request.get_json(silent=True) or request.form
    username  = str(data.get("username") or "").strip()
    password  = str(data.get("password") or "")
    full_name = str(data.get("full_name") or "").strip()
    badge_id  = str(data.get("badge_id") or "CSGT-NEW").strip()
    unit      = str(data.get("unit") or "Đội CSGT").strip()
    email     = str(data.get("email") or "").strip()

    if not username or not password or not full_name or not email:
        return jsonify({"error": "Vui lòng nhập đầy đủ thông tin quản trị viên mới"}), 400

    new_admin, err = create_admin_user(username, password, full_name, badge_id, unit, email)
    if err:
        return jsonify({"error": err}), 400

    try:
        write_audit_log("admin_created", "user", new_admin["id"], {
            "email": email,
            "username": username,
        })
    except Exception:
        logger.exception("Không thể ghi audit log tạo admin")
        return jsonify({"error": "Tài khoản đã được tạo nhưng ghi nhật ký thất bại; hãy kiểm tra audit_logs."}), 503

    return jsonify({
        "message": f"Đã tạo Quản trị viên mới: {full_name} ({username})",
        "user": new_admin
    })


@app.route("/api/auth/users", methods=["GET"])
@require_role("admin")
def get_users_list():
    """Lấy danh sách người dùng trong hệ thống (Chỉ Admin)."""
    try:
        return jsonify(list_all_users())
    except Exception as exc:
        logger.exception("Không thể tải danh sách người dùng Supabase")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/auth/login", methods=["POST"])
def login():
    """Đăng nhập bằng email Supabase và phân quyền theo app metadata."""
    data = request.get_json(silent=True) or request.form
    identity = str(data.get("email") or data.get("username") or "")
    password = str(data.get("password") or "")

    token, user, error = authenticate(identity, password)
    if not token:
        return jsonify({"error": error}), 401

    return jsonify({
        "message": f"Đăng nhập thành công với vai trò {user['role'].upper()}",
        "token": token,
        "user": user
    })


@app.route("/api/auth/me")
def get_me():
    """Lấy thông tin người dùng hiện tại."""
    user = get_current_user()
    if not user:
        return jsonify({"authenticated": False, "role": "guest"}), 200
    return jsonify({"authenticated": True, "user": user})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    """Đăng xuất."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        ACTIVE_SESSIONS.pop(token, None)
    return jsonify({"message": "Đã đăng xuất thành công"})


@app.route("/api/cameras/<camera_id>", methods=["GET"])
@require_role("viewer")
def get_camera_config(camera_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", camera_id):
        return jsonify({"error": "Mã camera chỉ được gồm chữ, số, dấu chấm, gạch ngang và gạch dưới."}), 400
    user = get_current_user()
    admin = get_admin_client()
    if admin is None:
        return jsonify({"error": "Camera config chưa khả dụng: cần SUPABASE_SERVICE_ROLE_KEY."}), 503
    result = (admin.table("camera_configs").select(
        "camera_id,enabled,stop_line_ratio,stop_line_angle,updated_at"
    )
              .eq("owner_id", user["id"]).eq("camera_id", camera_id).limit(1).execute())
    if result.data:
        return jsonify(result.data[0])
    return jsonify({
        "camera_id": camera_id,
        "enabled": True,
        "stop_line_ratio": 0.72,
        "stop_line_angle": 0,
        "updated_at": None,
    })


@app.route("/api/cameras/<camera_id>", methods=["PUT"])
@require_role("viewer")
def save_camera_config(camera_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", camera_id):
        return jsonify({"error": "Mã camera chỉ được gồm chữ, số, dấu chấm, gạch ngang và gạch dưới."}), 400
    data = request.get_json(silent=True) or {}
    try:
        ratio = float(data.get("stop_line_ratio", 0.72))
    except (TypeError, ValueError):
        return jsonify({"error": "Vị trí vạch dừng phải là số từ 0.30 đến 0.95."}), 400
    if not 0.30 <= ratio <= 0.95:
        return jsonify({"error": "Vị trí vạch dừng phải từ 30% đến 95%."}), 400
    try:
        angle = float(data.get("stop_line_angle", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Góc vạch dừng phải là số từ -45° đến 45°."}), 400
    if not -45 <= angle <= 45:
        return jsonify({"error": "Góc vạch dừng phải từ -45° đến 45°."}), 400
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "Trường enabled phải là true hoặc false."}), 400

    user = get_current_user()
    admin = get_admin_client()
    if admin is None:
        return jsonify({"error": "Camera config chưa khả dụng: cần SUPABASE_SERVICE_ROLE_KEY."}), 503
    try:
        result = admin.table("camera_configs").upsert({
            "owner_id": user["id"],
            "camera_id": camera_id.strip(),
            "enabled": enabled,
            "stop_line_ratio": ratio,
            "stop_line_angle": angle,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="owner_id,camera_id").execute()
        write_audit_log("camera_config_updated", "camera", camera_id.strip(), {
            "enabled": enabled,
            "stop_line_ratio": ratio,
            "stop_line_angle": angle,
        })
        return jsonify(result.data[0] if result.data else {
            "camera_id": camera_id.strip(),
            "enabled": enabled,
            "stop_line_ratio": ratio,
            "stop_line_angle": angle,
        })
    except Exception as exc:
        logger.exception("Không thể lưu cấu hình camera")
        return jsonify({"error": str(exc)}), 500


# ── Detect ảnh trực tiếp ───────────────────────────────────────────────────

@app.route("/detect/image", methods=["POST"])
@require_role("viewer")
def detect_image():
    """
    Upload ảnh → AI phân tích (MBH, Chở 3, Vượt đèn đỏ, Nhận dạng biển số).
    Form-data: file = <image>, stop_line_ratio = <float>, enable_stop_line = <bool>
    """
    if "file" not in request.files:
        return jsonify({"error": "Thiếu trường 'file'"}), 400

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_IMAGE):
        return jsonify({"error": "Chỉ chấp nhận ảnh: jpg, jpeg, png, bmp, webp"}), 400

    try:
        stop_line_ratio = float(request.form.get("stop_line_ratio", 0.72))
    except ValueError:
        return jsonify({"error": "Vị trí vạch dừng phải là số từ 0.30 đến 0.95."}), 400
    if not 0.30 <= stop_line_ratio <= 0.95:
        return jsonify({"error": "Vị trí vạch dừng phải từ 30% đến 95%."}), 400
    try:
        stop_line_angle = float(request.form.get("stop_line_angle", 0))
    except ValueError:
        return jsonify({"error": "Góc vạch dừng phải là số từ -45° đến 45°."}), 400
    if not -45 <= stop_line_angle <= 45:
        return jsonify({"error": "Góc vạch dừng phải từ -45° đến 45°."}), 400
    enable_stop_line = request.form.get("enable_stop_line", "true").lower() in ("true", "1", "yes")
    if get_admin_client() is None:
        return jsonify({"error": "Lưu lịch sử phân tích ảnh cần cấu hình SUPABASE_SERVICE_ROLE_KEY."}), 503

    fname     = unique_filename(file.filename)
    save_path = UPLOAD_FOLDER / fname
    file.save(str(save_path))
    logger.info(f"Ảnh upload local: {save_path} (Stop Line={enable_stop_line})")
    storage_paths = []
    annotated_dest = None
    analysis_id = None

    try:
        detector = get_detector()
        result   = detector.detect_image(
            str(save_path), stop_line_ratio=stop_line_ratio,
            enable_stop_line=enable_stop_line, stop_line_angle=stop_line_angle
        )

        # Di chuyển ảnh annotated vào results/
        annotated_src  = Path(result["annotated_path"])
        annotated_dest = RESULT_FOLDER / annotated_src.name
        if annotated_src.exists():
            if annotated_dest.exists():
                annotated_dest.unlink(missing_ok=True)
            shutil.move(str(annotated_src), str(annotated_dest))

        analysis_storage_prefix = f"image-analyses/{uuid.uuid4().hex}"
        original_storage_path = f"{analysis_storage_prefix}/original{save_path.suffix.lower()}"
        annotated_storage_path = f"{analysis_storage_prefix}/annotated{annotated_dest.suffix.lower()}"
        storage_paths.append(original_storage_path)
        upload_storage_file(
            BUCKET_VIOLATIONS, original_storage_path, save_path, image_content_type(save_path.name)
        )
        storage_paths.append(annotated_storage_path)
        upload_storage_file(
            BUCKET_VIOLATIONS, annotated_storage_path, annotated_dest,
            image_content_type(annotated_dest.name),
        )

        user = get_current_user()
        database = get_admin_client()
        analysis_response = database.table("image_analyses").insert({
            "owner_id": user["id"],
            "file_name": file.filename,
            "original_storage_path": original_storage_path,
            "annotated_storage_path": annotated_storage_path,
            "stop_line_enabled": enable_stop_line,
            "stop_line_ratio": stop_line_ratio,
            "stop_line_angle": stop_line_angle,
            "stop_line_angle": stop_line_angle,
            "summary": result["summary"],
        }).execute()
        if not analysis_response.data:
            raise RuntimeError("Supabase không lưu được lịch sử phân tích ảnh.")
        analysis_record = analysis_response.data[0]
        analysis_id = analysis_record["id"]

        persisted_violations = []
        for violation in result.get("violations", []):
            extra = {
                key: value for key, value in violation.items()
                if key not in ("type", "label", "conf", "frame", "box")
            }
            persisted_violations.append({
                "image_analysis_id": analysis_id,
                "evidence_storage_path": annotated_storage_path,
                "violation_type": violation["type"],
                "label": violation["label"],
                "confidence": violation.get("conf", 0),
                "frame_number": None,
                "box": json.dumps(violation.get("box")),
                "extra_data": json.dumps(extra, ensure_ascii=False),
            })
        if persisted_violations:
            database.table("violations").insert(persisted_violations).execute()

        try:
            write_audit_log("image_analysis_completed", "image_analysis", analysis_id, {
                "violations": len(persisted_violations),
                "file_name": file.filename,
            })
        except Exception:
            logger.exception("Đã lưu lịch sử ảnh nhưng không ghi được audit log")

        result["annotated_path"] = str(annotated_dest)
        result["annotated_url"] = create_signed_storage_url(
            BUCKET_VIOLATIONS, annotated_storage_path
        )
        result["analysis_id"] = analysis_id
        result["stop_line_angle"] = stop_line_angle
        result["created_at"] = analysis_record.get("created_at")
        result["original_url"] = create_signed_storage_url(
            BUCKET_VIOLATIONS, original_storage_path
        )

        logger.info(f"Kết quả ảnh: {result['summary']}")
        return jsonify(result)

    except Exception as e:
        logger.exception("Lỗi detect ảnh")
        if analysis_id is not None:
            try:
                get_admin_client().table("image_analyses").delete().eq("id", analysis_id).execute()
            except Exception:
                logger.exception("Không thể rollback bản ghi lịch sử ảnh bị lỗi")
        try:
            delete_storage_files(BUCKET_VIOLATIONS, storage_paths)
        except Exception:
            logger.exception("Không thể dọn tệp Storage của lần phân tích ảnh thất bại")
        return jsonify({"error": str(e)}), 500
    finally:
        save_path.unlink(missing_ok=True)
        if annotated_dest and annotated_dest.exists():
            annotated_dest.unlink(missing_ok=True)


# ── Detect video trực tiếp ─────────────────────────────────────────────────

@app.route("/detect/video", methods=["POST"])
@require_role("viewer")
def detect_video():
    """
    Đưa video vào hàng đợi bền vững; worker xử lý nền.
    Form-data: file = <video>, frame_skip = <int>, stop_line_ratio = <float>, enable_stop_line = <bool>
    """
    if "file" not in request.files:
        return jsonify({"error": "Thiếu trường 'file'"}), 400
    if get_admin_client() is None:
        return jsonify({"error": "Hàng đợi xử lý video cần cấu hình SUPABASE_SERVICE_ROLE_KEY."}), 503

    file = request.files["file"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_VIDEO):
        return jsonify({"error": "Chỉ chấp nhận video: mp4, avi, mov, mkv"}), 400

    try:
        frame_skip = max(1, min(10, int(request.form.get("frame_skip", 3))))
        stop_line_ratio = float(request.form.get("stop_line_ratio", 0.72))
        stop_line_angle = float(request.form.get("stop_line_angle", 0))
    except ValueError:
        return jsonify({"error": "Tham số video hoặc vạch dừng không hợp lệ."}), 400
    if not 0.30 <= stop_line_ratio <= 0.95:
        return jsonify({"error": "Vị trí vạch dừng phải từ 30% đến 95%."}), 400
    if not -45 <= stop_line_angle <= 45:
        return jsonify({"error": "Góc vạch dừng phải từ -45° đến 45°."}), 400
    enable_stop_line = request.form.get("enable_stop_line", "true").lower() in ("true", "1", "yes")
    camera_id = str(request.form.get("camera_id") or "default").strip()[:120] or "default"

    fname     = unique_filename(file.filename)
    save_path = UPLOAD_FOLDER / fname
    file.save(str(save_path))
    logger.info(f"Video upload local: {save_path} (Stop Line={enable_stop_line})")

    try:
        storage_key = f"uploads/{fname}"
        storage_path = upload_to_storage(
            BUCKET_VIDEOS, storage_key, str(save_path), get_video_content_type(file.filename)
        )
        if not storage_path:
            save_path.unlink(missing_ok=True)
            return jsonify({"error": "Không thể lưu video vào Supabase Storage; chưa đưa video vào hàng đợi."}), 503

        user = get_current_user()
        result = database_client().table("videos").insert({
            "file_name": file.filename,
            "file_url": storage_path,
            "storage_path": storage_key,
            "upload_by": user["id"],
            "status": "pending",
            "processing_options": {
                "frame_skip": frame_skip,
                "stop_line_ratio": stop_line_ratio,
                "stop_line_angle": stop_line_angle,
                "enable_stop_line": enable_stop_line,
                "camera_id": camera_id,
            },
        }).execute()
        if not result.data:
            raise RuntimeError("Supabase không tạo được bản ghi video.")
        record = result.data[0]
        write_audit_log("video_queued", "video", record["id"], {
            "camera_id": camera_id,
            "enable_stop_line": enable_stop_line,
        })
        return jsonify({
            "message": "Video đã được đưa vào hàng đợi xử lý.",
            "video_id": record["id"],
            "status": "pending",
        }), 202
    except Exception as e:
        logger.exception("Không thể đưa video vào hàng đợi")
        save_path.unlink(missing_ok=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/videos/upload-url", methods=["POST"])
@require_role("viewer")
def create_video_upload_url():
    """Create a short-lived, path-scoped token for resumable browser-to-Storage upload."""
    data = request.get_json(silent=True) or {}
    file_name = str(data.get("file_name") or "").strip()
    if not file_name or Path(file_name).name != file_name or not allowed_file(file_name, ALLOWED_VIDEO):
        return jsonify({"error": "Tên video hoặc định dạng tệp không hợp lệ."}), 400
    try:
        file_size = int(data.get("file_size", 0))
        frame_skip = max(1, min(10, int(data.get("frame_skip", 3))))
        stop_line_ratio = float(data.get("stop_line_ratio", 0.72))
        stop_line_angle = float(data.get("stop_line_angle", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Thông tin video hoặc cấu hình vạch dừng không hợp lệ."}), 400
    if not 0 < file_size <= app.config["MAX_CONTENT_LENGTH"]:
        return jsonify({"error": "Dung lượng video phải lớn hơn 0 và không vượt quá 500 MB."}), 400
    if not 0.30 <= stop_line_ratio <= 0.95:
        return jsonify({"error": "Vị trí vạch dừng phải từ 30% đến 95%."}), 400
    if not -45 <= stop_line_angle <= 45:
        return jsonify({"error": "Góc vạch dừng phải từ -45° đến 45°."}), 400
    enable_stop_line = data.get("enable_stop_line", True)
    if not isinstance(enable_stop_line, bool):
        return jsonify({"error": "Trường enable_stop_line phải là true hoặc false."}), 400
    camera_id = str(data.get("camera_id") or "default").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", camera_id):
        return jsonify({"error": "Mã camera không hợp lệ."}), 400

    user = get_current_user()
    admin = get_admin_client()
    if admin is None:
        return jsonify({"error": "Upload video cần cấu hình SUPABASE_SERVICE_ROLE_KEY."}), 503

    extension = Path(file_name).suffix.lower()
    storage_path = f"uploads/{user['id']}/{uuid.uuid4().hex}{extension}"
    try:
        signed = admin.storage.from_(BUCKET_VIDEOS).create_signed_upload_url(storage_path)
        token = signed.get("token")
        if not token:
            raise RuntimeError("Supabase không trả về token upload có thời hạn.")

        parsed_url = urlparse(SUPABASE_URL)
        hostname = parsed_url.hostname
        if not hostname:
            raise RuntimeError("SUPABASE_URL không hợp lệ.")
        if hostname.endswith(".supabase.co"):
            hostname = f"{hostname[:-len('.supabase.co')]}.storage.supabase.co"
        netloc = hostname if parsed_url.port is None else f"{hostname}:{parsed_url.port}"
        tus_endpoint = parsed_url._replace(
            netloc=netloc,
            path="/storage/v1/upload/resumable/sign",
            params="",
            query="",
            fragment="",
        ).geturl()
        return jsonify({
            "storage_path": storage_path,
            "upload_token": token,
            "tus_endpoint": tus_endpoint,
            "content_type": get_video_content_type(file_name),
        })
    except Exception as exc:
        logger.exception("Không tạo được token upload resumable cho video")
        return jsonify({"error": f"Không thể khởi tạo upload video: {exc}"}), 503


@app.route("/api/videos/queue", methods=["POST"])
@require_role("viewer")
def queue_uploaded_video():
    """Verify a completed Storage upload and create its processing job."""
    data = request.get_json(silent=True) or {}
    file_name = str(data.get("file_name") or "").strip()
    storage_path = str(data.get("storage_path") or "").strip()
    user = get_current_user()
    valid_path = re.fullmatch(
        rf"uploads/{re.escape(str(user['id']))}/[0-9a-f]{{32}}"
        rf"{re.escape(Path(file_name).suffix.lower())}",
        storage_path,
    )
    if (
        not file_name
        or Path(file_name).name != file_name
        or not allowed_file(file_name, ALLOWED_VIDEO)
        or not valid_path
    ):
        return jsonify({"error": "Tệp video tải lên không hợp lệ hoặc không thuộc tài khoản này."}), 400
    try:
        frame_skip = max(1, min(10, int(data.get("frame_skip", 3))))
        stop_line_ratio = float(data.get("stop_line_ratio", 0.72))
        stop_line_angle = float(data.get("stop_line_angle", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Cấu hình xử lý video không hợp lệ."}), 400
    if not 0.30 <= stop_line_ratio <= 0.95 or not -45 <= stop_line_angle <= 45:
        return jsonify({"error": "Cấu hình vạch dừng nằm ngoài giới hạn cho phép."}), 400
    camera_id = str(data.get("camera_id") or "default").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", camera_id):
        return jsonify({"error": "Mã camera không hợp lệ."}), 400

    admin = get_admin_client()
    if admin is None:
        return jsonify({"error": "Hàng đợi xử lý video cần cấu hình SUPABASE_SERVICE_ROLE_KEY."}), 503
    enable_stop_line = data.get("enable_stop_line", True)
    if not isinstance(enable_stop_line, bool):
        return jsonify({"error": "Trường enable_stop_line phải là true hoặc false."}), 400
    try:
        admin.storage.from_(BUCKET_VIDEOS).info(storage_path)
        result = admin.table("videos").insert({
            "file_name": file_name,
            "file_url": storage_path,
            "storage_path": storage_path,
            "upload_by": user["id"],
            "status": "pending",
            "processing_options": {
                "frame_skip": frame_skip,
                "stop_line_ratio": stop_line_ratio,
                "stop_line_angle": stop_line_angle,
                "enable_stop_line": enable_stop_line,
                "camera_id": camera_id,
            },
        }).execute()
        if not result.data:
            raise RuntimeError("Supabase không tạo được bản ghi video.")
        record = result.data[0]
        write_audit_log("video_queued", "video", record["id"], {
            "camera_id": camera_id,
            "enable_stop_line": enable_stop_line,
        })
        return jsonify({
            "message": "Video đã tải lên và được đưa vào hàng đợi xử lý.",
            "video_id": record["id"],
            "status": "pending",
        }), 202
    except Exception as exc:
        logger.exception("Không thể xác minh upload hoặc đưa video vào hàng đợi")
        return jsonify({"error": f"Chưa thể đưa video vào hàng đợi: {exc}"}), 503


# ── Upload video → Supabase Storage + DB ──────────────────────────────────

@app.route("/upload-video", methods=["POST"])
@require_role("viewer")
def upload_video():
    """Upload video lên Supabase Storage + tạo record DB."""
    if "video" not in request.files:
        return jsonify({"error": "Thiếu trường 'video'"}), 400
    if get_admin_client() is None:
        return jsonify({"error": "Hàng đợi xử lý video cần cấu hình SUPABASE_SERVICE_ROLE_KEY."}), 503

    file = request.files["video"]
    if not file.filename or not allowed_file(file.filename, ALLOWED_VIDEO):
        return jsonify({"error": "Chỉ chấp nhận video: mp4, avi, mov, mkv"}), 400

    fname      = unique_filename(file.filename)
    local_path = UPLOAD_FOLDER / fname
    file.save(str(local_path))
    logger.info(f"Video lưu local: {local_path}")

    try:
        storage_key  = f"uploads/{fname}"
        content_type = get_video_content_type(file.filename)
        file_url     = upload_to_storage(
            BUCKET_VIDEOS, storage_key, str(local_path), content_type
        )

        if not file_url:
            local_path.unlink(missing_ok=True)
            return jsonify({"error": "Không thể lưu video vào Supabase Storage."}), 503

        insert_data = {
            "file_name":  file.filename,
            "file_url":   file_url,
            "storage_path": storage_key,
            "local_path": str(local_path),
            "upload_by": get_current_user()["id"],
            "status":     "pending",
            "processing_options": {
                "frame_skip": 3,
                "stop_line_ratio": 0.72,
                "stop_line_angle": 0,
                "enable_stop_line": True,
                "camera_id": "default",
            },
        }

        db_resp = database_client().table("videos").insert(insert_data).execute()
        video_record = db_resp.data[0] if db_resp.data else {}
        write_audit_log("video_queued", "video", video_record.get("id"), {
            "camera_id": "default",
            "enable_stop_line": True,
        })

        return jsonify({
            "message":  "Upload thành công, đang chờ xử lý",
            "video_id": video_record.get("id"),
            "file_name": file.filename,
            "file_url": create_signed_storage_url(BUCKET_VIDEOS, storage_key),
            "status":   "pending",
        }), 202

    except Exception as e:
        logger.exception("Lỗi khi upload video lên Supabase")
        return jsonify({"error": str(e)}), 500


# ── Process 1 video từ DB ──────────────────────────────────────────────────

@app.route("/process-video/<int:video_id>", methods=["POST"])
@require_role("viewer")
def process_video(video_id: int):
    """Retry a failed video by returning it to the durable worker queue."""
    try:
        resp = database_client().table("videos").select("*").eq("id", video_id).limit(1).execute()
        if not resp.data:
            return jsonify({"error": "Không tìm thấy video."}), 404
        record = resp.data[0]
        if record.get("status") == "processed":
            return jsonify({"message": "Video đã xử lý xong.", "video_id": video_id, "status": "processed"})
        if record.get("status") == "processing":
            return jsonify({"message": "Video đang được worker xử lý.", "video_id": video_id, "status": "processing"}), 202
        if record.get("status") not in ("pending", "error"):
            return jsonify({"error": "Trạng thái video không hỗ trợ xử lý lại."}), 409
        admin = get_admin_client()
        if admin is None:
            return jsonify({"error": "Hàng đợi cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        admin.table("videos").update({
            "status": "pending",
            "processing_progress": 0,
            "processing_error": None,
            "processing_attempts": 0,
        }).eq("id", video_id).execute()
        write_audit_log("video_requeued", "video", video_id)
        return jsonify({
            "message": "Video đã được đưa lại vào hàng đợi.",
            "video_id": video_id,
            "status": "pending",
        }), 202
    except Exception as e:
        logger.exception("Không thể đưa video trở lại hàng đợi")
        return jsonify({"error": str(e)}), 500


# ── Serve file kết quả local ───────────────────────────────────────────────

@app.route("/result/<filename>")
@require_role("viewer")
def get_result_file(filename: str):
    file_path = RESULT_FOLDER / filename
    if not file_path.exists():
        return jsonify({"error": "File không tồn tại"}), 404
    return send_file(str(file_path))


# ── DB Queries & RBAC Management ──────────────────────────────────────────

@app.route("/videos")
@require_role("viewer")
def get_videos():
    """Xem danh sách video (Cho phép cả CSGT và Viewer)."""
    try:
        limit, offset = parse_pagination()
        query = database_client().table("videos").select("*", count="exact")
        status = request.args.get("status", "").strip()
        search = request.args.get("search", "").strip()
        if status:
            query = query.eq("status", status)
        if search:
            query = query.ilike("file_name", f"%{search}%")
        resp = query.order("id", desc=True).range(offset, offset + limit - 1).execute()
        videos = []
        for record in resp.data:
            video = dict(record)
            video["has_annotated_video"] = bool(video.get("annotated_url"))
            video["annotated_url"] = None
            videos.append(video)
        return jsonify({"items": videos, "total": resp.count or 0, "limit": limit, "offset": offset})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Không thể tải video")
        return jsonify({"error": str(exc)}), 503


@app.route("/videos/<int:video_id>", methods=["GET"])
@require_role("viewer")
def get_video_status(video_id: int):
    try:
        result = database_client().table("videos").select("*").eq("id", video_id).limit(1).execute()
        if not result.data:
            return jsonify({"error": "Không tìm thấy video."}), 404
        video = result.data[0]
        annotated_value = video.get("annotated_url")
        if annotated_value:
            storage_path = storage_path_from_url(BUCKET_VIOLATIONS, annotated_value)
            if storage_path:
                video["annotated_url"] = create_signed_storage_url(BUCKET_VIOLATIONS, storage_path)
        if video.get("status") == "processed":
            violations = (database_client().table("violations").select("*")
                          .eq("video_id", video_id).order("id").execute())
            video["violations"] = [enrich_violation(item) for item in violations.data]
        return jsonify(video)
    except Exception as exc:
        logger.exception("Không thể tải trạng thái video")
        return jsonify({"error": str(exc)}), 503


@app.route("/videos/<int:video_id>/annotated-url")
@require_role("viewer")
def get_annotated_video_url(video_id: int):
    try:
        result = (database_client().table("videos").select("annotated_url")
                  .eq("id", video_id).limit(1).execute())
        if not result.data:
            return jsonify({"error": "Không tìm thấy video."}), 404
        annotated_path = result.data[0].get("annotated_url")
        if not annotated_path:
            return jsonify({"error": "Video chưa có kết quả đã xử lý."}), 404
        storage_path = storage_path_from_url(BUCKET_VIOLATIONS, annotated_path)
        if not storage_path:
            if annotated_path.startswith(("http://", "https://")):
                return jsonify({"url": annotated_path})
            return jsonify({"error": "Đường dẫn video kết quả không hợp lệ."}), 500
        return jsonify({
            "url": create_signed_storage_url(BUCKET_VIOLATIONS, storage_path)
        })
    except Exception as exc:
        logger.exception("Không thể tạo liên kết tạm cho video")
        return jsonify({"error": str(exc)}), 503


@app.route("/image-analyses")
@require_role("viewer")
def get_image_analyses():
    try:
        limit, offset = parse_pagination()
        admin = get_admin_client()
        if admin is None:
            return jsonify({"error": "Lịch sử ảnh cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        query = (admin.table("image_analyses")
                 .select("*", count="exact").order("created_at", desc=True))
        user = get_current_user()
        if user["role"] != "admin":
            query = query.eq("owner_id", user["id"])
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        try:
            start_day = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
            end_day = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        except ValueError:
            return jsonify({"error": "Ngày lọc phải theo định dạng YYYY-MM-DD."}), 400
        if start_day and end_day and end_day < start_day:
            return jsonify({"error": "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu."}), 400
        if start_day:
            query = query.gte("created_at", f"{start_day.isoformat()}T00:00:00+00:00")
        if end_day:
            end_exclusive = end_day + timedelta(days=1)
            query = query.lt("created_at", f"{end_exclusive.isoformat()}T00:00:00+00:00")
        result = query.range(offset, offset + limit - 1).execute()
        return jsonify({
            "items": result.data,
            "total": result.count or 0,
            "limit": limit,
            "offset": offset,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Không thể tải lịch sử phân tích ảnh")
        return jsonify({"error": str(exc)}), 503


@app.route("/image-analyses/<int:analysis_id>")
@require_role("viewer")
def get_image_analysis(analysis_id: int):
    try:
        database = get_admin_client()
        if database is None:
            return jsonify({"error": "Lịch sử ảnh cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        result = (database.table("image_analyses").select("*")
                  .eq("id", analysis_id).limit(1).execute())
        if not result.data:
            return jsonify({"error": "Không tìm thấy lịch sử phân tích ảnh."}), 404
        analysis = result.data[0]
        user = get_current_user()
        if user["role"] != "admin" and analysis.get("owner_id") != user["id"]:
            return jsonify({"error": "Không tìm thấy lịch sử phân tích ảnh."}), 404
        violations = (database.table("violations").select("*")
                      .eq("image_analysis_id", analysis_id).order("id").execute())
        analysis["original_url"] = create_signed_storage_url(
            BUCKET_VIOLATIONS, analysis["original_storage_path"]
        )
        analysis["annotated_url"] = create_signed_storage_url(
            BUCKET_VIOLATIONS, analysis["annotated_storage_path"]
        )
        analysis["violations"] = [enrich_violation(item) for item in violations.data]
        return jsonify(analysis)
    except Exception as exc:
        logger.exception("Không thể tải chi tiết phân tích ảnh")
        return jsonify({"error": str(exc)}), 503


@app.route("/videos/<int:video_id>", methods=["DELETE"])
@require_role("admin")
def delete_video(video_id: int):
    """Xóa video (Chỉ CSGT/Admin)."""
    try:
        database_client().table("videos").delete().eq("id", video_id).execute()
        write_audit_log("video_deleted", "video", video_id)
        return jsonify({"message": f"Đã xóa video ID {video_id}"})
    except Exception as e:
        logger.exception("Không thể xóa hoặc ghi nhật ký video")
        return jsonify({"error": str(e)}), 500


@app.route("/violations")
@require_role("viewer")
def get_violations():
    """Xem danh sách vi phạm (Cho phép cả CSGT và Viewer xem)."""
    try:
        limit, offset = parse_pagination()
        query = database_client().table("violations").select("*", count="exact")
        violation_type = request.args.get("type", "").strip()
        review_status = request.args.get("review_status", "").strip()
        search = request.args.get("search", "").strip()
        if violation_type:
            query = query.eq("violation_type", violation_type)
        if review_status:
            query = query.eq("review_status", review_status)
        if search:
            query = query.ilike("label", f"%{search}%")
        resp = query.order("id", desc=True).range(offset, offset + limit - 1).execute()
        return jsonify({
            "items": [enrich_violation(v) for v in resp.data],
            "total": resp.count or 0,
            "limit": limit,
            "offset": offset,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("Không thể tải vi phạm")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/dashboard")
@require_role("viewer")
def get_dashboard():
    try:
        video_count = database_client().table("videos").select("id", count="exact").execute().count or 0
        violations_count = database_client().table("violations").select("id", count="exact").execute().count or 0
        type_counts = {}
        for violation_type in ("no_helmet", "overloaded_motorcycle", "red_light"):
            result = (database_client().table("violations").select("id", count="exact")
                      .eq("violation_type", violation_type).execute())
            type_counts[violation_type] = result.count or 0
        latest = (database_client().table("violations").select("*")
                  .order("id", desc=True).range(0, 9).execute())
        return jsonify({
            "video_count": video_count,
            "violations_count": violations_count,
            "type_counts": type_counts,
            "recent_violations": [enrich_violation(item) for item in latest.data],
        })
    except Exception as exc:
        logger.exception("Không thể tải dashboard")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/dashboard/trends")
@require_role("viewer")
def get_violation_trends():
    try:
        start_date = datetime.strptime(request.args.get("start_date", ""), "%Y-%m-%d").date()
        end_date = datetime.strptime(request.args.get("end_date", ""), "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "start_date và end_date phải theo định dạng YYYY-MM-DD."}), 400
    bucket = request.args.get("bucket", "day").strip().lower()
    if bucket not in ("day", "week"):
        return jsonify({"error": "bucket chỉ được là day hoặc week."}), 400
    if end_date < start_date:
        return jsonify({"error": "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu."}), 400
    if (end_date - start_date).days > 366:
        return jsonify({"error": "Khoảng thời gian không được vượt quá 367 ngày."}), 400

    try:
        admin = get_admin_client()
        if admin is None:
            return jsonify({"error": "Biểu đồ cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        result = admin.rpc("violation_trends", {
            "p_start_date": start_date.isoformat(),
            "p_end_date": end_date.isoformat(),
            "p_bucket": bucket,
        }).execute()
        return jsonify({
            "items": result.data,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "bucket": bucket,
        })
    except Exception as exc:
        logger.exception("Không thể tải xu hướng vi phạm")
        return jsonify({"error": str(exc)}), 503


@app.route("/violations/<int:violation_id>/report")
@require_role("viewer")
def get_violation_report(violation_id: int):
    try:
        database = get_admin_client()
        if database is None:
            return jsonify({"error": "Báo cáo bằng chứng cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        result = (database.table("violations").select("*")
                  .eq("id", violation_id).limit(1).execute())
        if not result.data:
            return jsonify({"error": "Không tìm thấy hồ sơ vi phạm."}), 404
        violation = enrich_violation(result.data[0])
        evidence = {"image_url": None, "original_image_url": None, "video_url": None}

        evidence_path = violation.get("evidence_storage_path")
        if evidence_path:
            evidence["image_url"] = create_signed_storage_url(BUCKET_VIOLATIONS, evidence_path)
        analysis_id = violation.get("image_analysis_id")
        if analysis_id:
            analysis = (database.table("image_analyses")
                        .select("owner_id,original_storage_path,annotated_storage_path,file_name,created_at")
                        .eq("id", analysis_id).limit(1).execute())
            if analysis.data:
                user = get_current_user()
                if user["role"] != "admin" and analysis.data[0].get("owner_id") != user["id"]:
                    return jsonify({"error": "Không tìm thấy hồ sơ vi phạm."}), 404
                evidence["original_image_url"] = create_signed_storage_url(
                    BUCKET_VIOLATIONS, analysis.data[0]["original_storage_path"]
                )
                evidence["image_url"] = create_signed_storage_url(
                    BUCKET_VIOLATIONS, analysis.data[0]["annotated_storage_path"]
                )
                violation["source_file_name"] = analysis.data[0]["file_name"]

        video_id = violation.get("video_id")
        if video_id:
            video_result = (database.table("videos")
                            .select("file_name,file_url,storage_path,annotated_url,upload_time")
                            .eq("id", video_id).limit(1).execute())
            if video_result.data:
                video = video_result.data[0]
                annotated_path = storage_path_from_url(
                    BUCKET_VIOLATIONS, video.get("annotated_url") or ""
                )
                source_path = video.get("storage_path") or storage_path_from_url(
                    BUCKET_VIDEOS, video.get("file_url") or ""
                )
                if annotated_path:
                    evidence["video_url"] = create_signed_storage_url(
                        BUCKET_VIOLATIONS, annotated_path
                    )
                elif source_path:
                    evidence["video_url"] = create_signed_storage_url(
                        BUCKET_VIDEOS, source_path
                    )
                violation["source_file_name"] = video.get("file_name")

        return jsonify({"violation": violation, "evidence": evidence})
    except Exception as exc:
        logger.exception("Không thể tạo báo cáo hồ sơ vi phạm")
        return jsonify({"error": str(exc)}), 503


def safe_csv_value(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


@app.route("/violations/export.csv")
@require_role("viewer")
def export_violations_csv():
    try:
        query = database_client().table("violations").select("*")
        violation_type = request.args.get("type", "").strip()
        review_status = request.args.get("review_status", "").strip()
        if violation_type:
            query = query.eq("violation_type", violation_type)
        if review_status:
            query = query.eq("review_status", review_status)
        result = query.order("id", desc=True).range(0, 4999).execute()
        if len(result.data) == 5000:
            return jsonify({"error": "Báo cáo vượt quá 5.000 bản ghi; hãy lọc bớt dữ liệu."}), 413
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Video ID", "Loại", "Biển số", "Mô tả", "Độ tin cậy",
                         "Trạng thái duyệt", "Ghi chú", "Thời gian"])
        for item in result.data:
            enriched = enrich_violation(item)
            writer.writerow([
                safe_csv_value(enriched.get("id")),
                safe_csv_value(enriched.get("video_id")),
                safe_csv_value(enriched.get("violation_type")),
                safe_csv_value(enriched.get("license_plate")),
                safe_csv_value(enriched.get("label")),
                safe_csv_value(enriched.get("confidence")),
                safe_csv_value(enriched.get("review_status")),
                safe_csv_value(enriched.get("notes")),
                safe_csv_value(enriched.get("created_at")),
            ])
        return app.response_class(
            output.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=traffic-violations.csv"},
        )
    except Exception as exc:
        logger.exception("Không thể xuất báo cáo vi phạm")
        return jsonify({"error": str(exc)}), 503


@app.route("/api/audit-logs")
@require_role("admin")
def get_audit_logs():
    try:
        limit, offset = parse_pagination(default_limit=50, maximum_limit=100)
        admin = get_admin_client()
        if admin is None:
            return jsonify({"error": "Audit log cần SUPABASE_SERVICE_ROLE_KEY."}), 503
        query = admin.table("audit_logs").select("*", count="exact")
        search = request.args.get("search", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        if search:
            query = query.ilike("action", f"%{search}%")
        start_day = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None
        end_day = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None
        if start_day and end_day and end_day < start_day:
            return jsonify({"error": "Ngày kết thúc phải bằng hoặc sau ngày bắt đầu."}), 400
        if start_day:
            query = query.gte("created_at", f"{start_day.isoformat()}T00:00:00+00:00")
        if end_day:
            end_exclusive = end_day + timedelta(days=1)
            query = query.lt("created_at", f"{end_exclusive.isoformat()}T00:00:00+00:00")
        result = query.order("created_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()
        return jsonify({
            "items": result.data,
            "total": result.count or 0,
            "limit": limit,
            "offset": offset,
        })
    except ValueError:
        return jsonify({"error": "Ngày lọc phải theo định dạng YYYY-MM-DD."}), 400
    except Exception as exc:
        logger.exception("Không thể tải audit logs")
        return jsonify({"error": str(exc)}), 503


@app.route("/violations/<int:violation_id>", methods=["PATCH", "PUT"])
@require_role("admin")
def update_violation(violation_id: int):
    """
    Duyệt vi phạm (Phạt nguội) hoặc cập nhật biển số/ghi chú.
    Chỉ CSGT / Admin mới có quyền thực hiện.
    Body JSON: { "review_status": "approved"|"rejected"|"pending", "license_plate": "...", "notes": "..." }
    """
    current_user = get_current_user()
    data = request.get_json(silent=True) or {}
    review_status = data.get("review_status")
    if review_status is not None and review_status not in ("pending", "approved", "rejected"):
        return jsonify({"error": "Trạng thái duyệt không hợp lệ."}), 400
    notes = str(data.get("notes", "")).strip()
    plate = str(data.get("license_plate", "")).strip().upper()
    if len(notes) > 2000 or len(plate) > 32:
        return jsonify({"error": "Ghi chú tối đa 2.000 ký tự, biển số tối đa 32 ký tự."}), 400

    review_info = VIOLATION_REVIEWS.get(violation_id, {
        "review_status": "pending",
        "license_plate": "",
        "notes": "",
    })

    if "review_status" in data:
        review_info["review_status"] = data["review_status"]
    if "license_plate" in data:
        review_info["license_plate"] = plate
    if "notes" in data:
        review_info["notes"] = notes

    review_info["reviewed_by"] = current_user.get("name", "CSGT")
    VIOLATION_REVIEWS[violation_id] = review_info

    try:
        update_fields = {
            "review_status": review_info["review_status"],
            "notes": review_info["notes"],
            "reviewed_by": review_info["reviewed_by"],
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if "label" in data:
            update_fields["label"] = data["label"]
        if "license_plate" in data:
            update_fields["extra_data"] = json.dumps({"license_plate": plate}, ensure_ascii=False)
        database_client().table("violations").update(update_fields).eq("id", violation_id).execute()
        write_audit_log("violation_reviewed", "violation", violation_id, {
            "review_status": review_info["review_status"],
            "license_plate": review_info.get("license_plate"),
        })
    except Exception as exc:
        logger.exception("Không thể lưu cập nhật vi phạm hoặc audit log")
        return jsonify({"error": str(exc)}), 503

    return jsonify({
        "message": "Cập nhật trạng thái vi phạm thành công",
        "violation_id": violation_id,
        "review": review_info
    })


@app.route("/violations/<int:violation_id>", methods=["DELETE"])
@require_role("admin")
def delete_violation(violation_id: int):
    """Xóa vi phạm (Chỉ CSGT / Admin)."""
    try:
        database_client().table("violations").delete().eq("id", violation_id).execute()
        write_audit_log("violation_deleted", "violation", violation_id)
        VIOLATION_REVIEWS.pop(violation_id, None)
        return jsonify({"message": f"Đã xóa vi phạm ID {violation_id}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/violations/video/<int:video_id>")
@require_role("viewer")
def get_violations_by_video(video_id: int):
    resp = (database_client().table("violations").select("*")
            .eq("video_id", video_id).execute())
    data = [enrich_violation(v) for v in resp.data]
    return jsonify(data)


@app.route("/videos/summary")
@require_role("viewer")
def get_videos_summary():
    """Lấy tóm tắt video + số vi phạm."""
    try:
        resp = database_client().table("video_violation_summary").select("*").execute()
        return jsonify(resp.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "5000")),
        debug=debug,
    )
