"""Supabase-backed authentication and role-based access control."""

import logging
import re
import time
import uuid
from functools import wraps

from flask import jsonify, request

from supabase_client import create_auth_client, get_admin_client

logger = logging.getLogger(__name__)

ACTIVE_SESSIONS = {}
VIOLATION_REVIEWS = {}


def _user_info(user):
    metadata = user.user_metadata or {}
    app_metadata = user.app_metadata or {}
    role = app_metadata.get("role", "viewer")
    if role not in ("admin", "viewer"):
        role = "viewer"
    return {
        "id": user.id,
        "username": metadata.get("username", ""),
        "email": user.email or "",
        "role": role,
        "name": metadata.get("full_name", user.email or ""),
        "badge_id": metadata.get("badge_id", "VIEWER"),
        "unit": metadata.get("unit", "Người dùng hệ thống"),
    }


def _create_session(user_info):
    token = f"tok_{uuid.uuid4().hex}"
    ACTIVE_SESSIONS[token] = {**user_info, "created_at": time.time()}
    return token


def _registration_error_message(exc):
    error_text = str(exc).lower()
    error_code = str(getattr(exc, "code", "") or "").lower()

    if "already registered" in error_text or "user already exists" in error_text:
        return "Email này đã được đăng ký. Hãy đăng nhập hoặc dùng email khác."
    if any(marker in error_text for marker in (
        "database error saving new user",
        "user_profiles",
        "create_user_profile",
        "duplicate key",
        "violates unique constraint",
    )):
        return (
            "Supabase không tạo được hồ sơ tài khoản. Hãy chạy lại supabase_auth.sql "
            "và kiểm tra username/email đã tồn tại trong user_profiles."
        )
    if "signup is disabled" in error_text or "signups not allowed" in error_text:
        return "Supabase đang tắt đăng ký công khai. Hãy bật đăng ký email trong Authentication settings."
    if "invalid api key" in error_text or "invalid api key" in error_code:
        return "Khóa Supabase phía backend không hợp lệ. Hãy kiểm tra SUPABASE_URL và SUPABASE_ANON_KEY."
    if "rate limit" in error_text or "smtp" in error_text or "email rate limit" in error_code:
        return "Supabase không gửi được email đăng ký do giới hạn hoặc cấu hình email. Hãy kiểm tra Auth email settings."
    return "Không thể tạo tài khoản trên Supabase. Hãy xem log backend để biết lỗi Auth/Database cụ thể."


def register_user(username: str, password: str, full_name: str, email: str):
    """Create a Supabase Auth account; public registration can only create viewers."""
    uname = username.lower().strip()
    email = email.lower().strip()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", uname):
        return None, None, "Tên đăng nhập phải dài 3-32 ký tự, chỉ gồm chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới"
    if not full_name or len(full_name) > 120:
        return None, None, "Họ tên không được để trống và tối đa 120 ký tự"
    if len(password) < 6:
        return None, None, "Mật khẩu cần tối thiểu 6 ký tự"

    try:
        client = create_auth_client()
        response = client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {
                "data": {
                    "username": uname,
                    "full_name": full_name,
                    "role": "viewer",
                }
            },
        })
    except Exception as exc:
        logger.exception("Supabase signup failed")
        return None, None, _registration_error_message(exc)

    if not response.user:
        return None, None, "Supabase không trả về tài khoản mới. Hãy kiểm tra email đã đăng ký chưa."

    user_info = _user_info(response.user)
    user_info["role"] = "viewer"
    token = _create_session(user_info) if response.session else None
    return user_info, token, None


def create_admin_user(username: str, password: str, full_name: str,
                      badge_id: str = "CSGT-NEW", unit: str = "Đội CSGT",
                      email: str = None):
    """Create and promote an admin account using the server-only service key."""
    uname = username.lower().strip()
    email = (email or "").lower().strip()
    if not re.fullmatch(r"[a-z0-9_.-]{3,32}", uname):
        return None, "Tên đăng nhập phải dài 3-32 ký tự, chỉ gồm chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới"
    if len(password) < 6:
        return None, "Mật khẩu cần tối thiểu 6 ký tự"
    if not full_name or len(full_name) > 120:
        return None, "Họ tên không được để trống và tối đa 120 ký tự"

    admin = get_admin_client()
    if admin is None:
        return None, "Chưa cấu hình SUPABASE_SERVICE_ROLE_KEY trên máy chủ; không thể tạo quản trị viên."

    created_user_id = None
    try:
        existing = (admin.table("user_profiles").select("id")
                    .eq("username", uname).limit(1).execute())
        if existing.data:
            return None, "Tên đăng nhập đã tồn tại trong hệ thống"

        response = admin.auth.admin.create_user({
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": {
                "username": uname,
                "full_name": full_name,
                "badge_id": badge_id,
                "unit": unit,
            },
            "app_metadata": {"role": "admin"},
        })
        user = response.user
        if not user:
            return None, "Supabase không trả về tài khoản quản trị viên mới."
        created_user_id = user.id

        admin.table("user_profiles").update({
            "username": uname,
            "email": email,
            "full_name": full_name,
            "badge_id": badge_id,
            "unit": unit,
            "role": "admin",
        }).eq("id", user.id).execute()
        admin.table("users").update({
            "email": email,
            "full_name": full_name,
            "role": "admin",
        }).eq("id", user.id).execute()
        return _user_info(user), None
    except Exception as exc:
        if created_user_id:
            try:
                admin.auth.admin.delete_user(created_user_id)
            except Exception:
                logger.exception("Could not roll back incomplete Supabase admin account")
        logger.exception("Could not create Supabase admin account")
        return None, f"Không thể tạo quản trị viên trên Supabase: {exc}"


def authenticate(identity: str, password: str):
    """Authenticate with the registered email and return an application session."""
    email = (identity or "").strip().lower()
    if "@" not in email or not password:
        return None, None, "Email hoặc mật khẩu không chính xác."
    try:
        client = create_auth_client()
        response = client.auth.sign_in_with_password({
            "email": email,
            "password": password,
        })
    except Exception as exc:
        logger.info("Supabase login rejected: %s", exc)
        if "email not confirmed" in str(exc).lower():
            return None, None, "Email chưa được xác minh. Hãy kiểm tra hộp thư hoặc gửi lại email xác minh."
        return None, None, "Email hoặc mật khẩu không chính xác."

    if not response.user or not response.session:
        return None, None, "Email hoặc mật khẩu không chính xác."

    user_info = _user_info(response.user)
    try:
        profile = (client.table("user_profiles")
                   .select("username,email,full_name,role,badge_id,unit")
                   .eq("id", response.user.id).limit(1).execute())
        if profile.data:
            profile_info = profile.data[0]
            role = profile_info.get("role", "viewer")
            user_info.update({
                "username": profile_info.get("username", user_info["username"]),
                "email": profile_info.get("email", user_info["email"]),
                "name": profile_info.get("full_name", user_info["name"]),
                "role": role if role in ("admin", "viewer") else "viewer",
                "badge_id": profile_info.get("badge_id", user_info["badge_id"]),
                "unit": profile_info.get("unit", user_info["unit"]),
            })
    except Exception:
        logger.exception("Could not read canonical user profile during login")
        return None, None, "Không thể tải hồ sơ tài khoản. Vui lòng thử lại sau."
    token = _create_session(user_info)
    return token, user_info, None


def resend_signup_confirmation(email: str):
    try:
        create_auth_client().auth.resend({"type": "signup", "email": email.strip().lower()})
    except Exception as exc:
        logger.info("Supabase confirmation resend failed: %s", exc)
        raise RuntimeError("Không gửi được email xác minh. Hãy kiểm tra địa chỉ email hoặc thử lại sau.") from exc


def request_password_reset(email: str, redirect_to: str = None):
    options = {"redirect_to": redirect_to} if redirect_to else None
    try:
        create_auth_client().auth.reset_password_for_email(email.strip().lower(), options=options)
    except Exception as exc:
        logger.info("Supabase password reset request failed: %s", exc)
        raise RuntimeError("Không gửi được email đặt lại mật khẩu. Hãy kiểm tra địa chỉ email hoặc thử lại sau.") from exc


def update_recovery_password(access_token: str, refresh_token: str, password: str):
    if len(password) < 6:
        raise ValueError("Mật khẩu cần tối thiểu 6 ký tự.")
    try:
        client = create_auth_client()
        client.auth.set_session(access_token, refresh_token)
        client.auth.update_user({"password": password})
    except Exception as exc:
        logger.info("Supabase recovery password update failed: %s", exc)
        raise RuntimeError("Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn. Hãy yêu cầu email mới.") from exc


def get_current_user():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:].strip()
    return ACTIVE_SESSIONS.get(token)


def require_role(required_role):
    def decorator(function):
        @wraps(function)
        def decorated_function(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({
                    "error": "Yêu cầu đăng nhập để thực hiện thao tác này",
                    "code": "UNAUTHORIZED",
                }), 401
            if required_role == "admin" and user.get("role") != "admin":
                return jsonify({
                    "error": "Quyền truy cập bị từ chối: Chỉ CSGT / Admin mới được phép thao tác",
                    "code": "FORBIDDEN",
                }), 403
            return function(*args, **kwargs)
        return decorated_function
    return decorator


def list_all_users():
    admin = get_admin_client()
    if admin is None:
        raise RuntimeError("Chưa cấu hình SUPABASE_SERVICE_ROLE_KEY trên máy chủ.")
    response = (admin.table("user_profiles")
                .select("id,username,email,full_name,role,badge_id,unit,created_at")
                .order("created_at", desc=True).execute())
    return response.data
