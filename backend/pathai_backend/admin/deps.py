# -*- coding: utf-8 -*-
"""管理 API 依赖：cookie 会话、CSRF/Origin 校验与首改密门禁。"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Request

from pathai_backend.admin import service
from pathai_backend.admin.security import constant_time_eq, sha256_hex
from pathai_backend.errors import ApiError
from pathai_backend.models import AdminAccount

COOKIE_NAME = "pathai_admin_session"
CSRF_HEADER = "X-Admin-CSRF"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class AdminContext:
    account: AdminAccount
    session_token: str
    csrf_token: str | None


def set_session_cookie(response, settings, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=service.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="strict",
        secure=settings.env != "local",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def check_same_origin(request: Request) -> None:
    """浏览器写请求 Origin 必须同源；非浏览器客户端（无 Origin）放行。"""
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    if urlparse(origin).netloc.lower() != host.lower():
        raise ApiError(403, "ADMIN_ORIGIN_INVALID", "跨源请求被拒绝")


def require_admin(request: Request) -> AdminContext:
    """所有管理 API 的会话校验；首改密门禁由 require_admin_ready 追加。"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise ApiError(401, "ADMIN_UNAUTHORIZED", "未登录或会话已过期")
    loaded = service.load_session(token)
    if loaded is None:
        raise ApiError(401, "ADMIN_UNAUTHORIZED", "未登录或会话已过期")
    account, session_row = loaded
    csrf_header = request.headers.get(CSRF_HEADER)
    if request.method in _UNSAFE_METHODS:
        if csrf_header is None or not constant_time_eq(
            sha256_hex(csrf_header), session_row.csrf_hash
        ):
            raise ApiError(403, "ADMIN_CSRF_INVALID", "缺少或错误的 CSRF token")
    return AdminContext(account=account, session_token=token, csrf_token=csrf_header)


def require_admin_ready(request: Request) -> AdminContext:
    """管理功能 API：会话有效且已完成首改密。"""
    context = require_admin(request)
    if context.account.must_change_password:
        raise ApiError(403, "ADMIN_PASSWORD_CHANGE_REQUIRED", "首次登录需先修改密码")
    return context
