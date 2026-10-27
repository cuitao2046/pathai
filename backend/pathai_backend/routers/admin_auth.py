# -*- coding: utf-8 -*-
"""管理员认证 API：登录、会话引导、改密与退出（map-admin 技术设计 §3/§5）。

管理员会话与普通微信 Bearer token 完全隔离；cookie 为 HttpOnly + SameSite=Strict。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from pathai_backend.admin import deps, service
from pathai_backend.admin.schemas import LoginRequest, PasswordChangeRequest
from pathai_backend.admin.security import password_too_short
from pathai_backend.errors import ApiError

router = APIRouter(prefix="/api/v1/admin/auth", tags=["admin-auth"])


def _me_payload(username: str, must_change: bool, csrf_token: str) -> dict:
    return {
        "username": username,
        "mustChangePassword": must_change,
        "csrfToken": csrf_token,
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    deps.check_same_origin(request)
    service.ensure_admin_account()
    account = service.authenticate(body.username, body.password)
    if account is None:
        # 泛化错误：不区分用户名不存在与密码错误
        service.record_audit("login", False, {"username": body.username})
        raise ApiError(401, "ADMIN_INVALID_CREDENTIALS", "用户名或密码错误")
    token, csrf_token = service.create_session(account)
    deps.set_session_cookie(response, request.app.state.settings, token)
    service.record_audit("login", True, {"username": account.username})
    return _me_payload(account.username, account.must_change_password, csrf_token)


@router.get("/me")
def me(request: Request) -> dict:
    context = deps.require_admin(request)
    csrf_token = service.rotate_csrf(context.session_token)
    return _me_payload(context.account.username, context.account.must_change_password, csrf_token)


@router.post("/password")
def change_password(body: PasswordChangeRequest, request: Request, response: Response) -> dict:
    deps.check_same_origin(request)
    context = deps.require_admin(request)
    if body.newPassword != body.confirmPassword:
        raise ApiError(422, "PASSWORD_MISMATCH", "两次输入的新密码不一致")
    if password_too_short(body.newPassword):
        raise ApiError(422, "PASSWORD_TOO_SHORT", "新密码按 UTF-8 编码后至少 8 个字节")
    if not service.verify_current(context.account, body.currentPassword):
        raise ApiError(400, "CURRENT_PASSWORD_INVALID", "当前密码错误")
    service.change_password(context.account, body.newPassword)
    service.revoke_other_sessions(context.session_token)
    new_token, new_csrf = service.rotate_session(context.session_token)
    deps.set_session_cookie(response, request.app.state.settings, new_token)
    service.record_audit("password_change", True, {"username": context.account.username})
    return _me_payload(context.account.username, False, new_csrf)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    deps.check_same_origin(request)
    context = deps.require_admin(request)
    service.revoke_session(context.session_token)
    deps.clear_session_cookie(response)
    service.record_audit("logout", True, {"username": context.account.username})
    return {"ok": True}
