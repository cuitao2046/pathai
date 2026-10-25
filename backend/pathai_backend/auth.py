# -*- coding: utf-8 -*-
"""认证服务：匿名主体映射、opaque 令牌签发与校验（docs/24 §8 API-01）。

- userId = anon_<sha256(openid+salt) 前 16 位 hex>，跨会话/跨设备一致；
- 令牌：secrets 随机 ≥128bit，服务端仅存 sha256(token)，默认 7200 秒；
- 时间一律用 UTC epoch 秒整数，规避 SQLite/PostgreSQL 时区行为差异。
"""
from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pathai_backend.config import Settings
from pathai_backend.database import session_scope
from pathai_backend.errors import ApiError
from pathai_backend.models import AccessToken, AnonymousSubject
from pathai_backend.schemas.auth import AuthResponse
from pathai_backend.wechat import WechatAuthClient, WechatAuthError


@dataclass(frozen=True)
class AuthenticatedUser:
    subject_id: int
    user_id: str


def hash_openid(openid: str, salt: str) -> str:
    return hashlib.sha256((salt + openid).encode("utf-8")).hexdigest()


def user_id_from_hash(openid_hash: str) -> str:
    return f"anon_{openid_hash[:16]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def login(db: Session, client: WechatAuthClient, settings: Settings, code: str) -> AuthResponse:
    """code 换 openid → 查找或创建匿名主体 → 签发令牌。"""
    if not settings.openid_salt:
        # 配置缺失属于服务端故障，不得静默用空盐降级
        raise ApiError(500, "INTERNAL_ERROR", "服务未配置 openid 盐", retryable=False)
    try:
        openid = client.code_to_session(code)
    except WechatAuthError as exc:
        if exc.reason == "invalid_code":
            raise ApiError(401, "AUTH_CODE_INVALID", "微信登录 code 无效或已使用") from None
        raise ApiError(
            503, "AUTH_PROVIDER_UNAVAILABLE", "微信登录服务暂不可用", retryable=True
        ) from None

    openid_hash = hash_openid(openid, settings.openid_salt)
    subject = db.scalar(select(AnonymousSubject).where(AnonymousSubject.openid_hash == openid_hash))
    if subject is None:
        subject = AnonymousSubject(
            openid_hash=openid_hash,
            user_id=user_id_from_hash(openid_hash),
            created_at_epoch=int(time.time()),
        )
        db.add(subject)
        try:
            db.flush()
        except IntegrityError:
            # 并发首次登录：另一请求已创建主体，回滚后按哈希复用
            db.rollback()
            subject = db.scalar(
                select(AnonymousSubject).where(AnonymousSubject.openid_hash == openid_hash)
            )

    token = secrets.token_urlsafe(32)  # 256 bit 熵，43 字符
    now = int(time.time())
    expires_at = now + settings.token_ttl_seconds
    db.add(
        AccessToken(
            token_hash=hash_token(token),
            subject_id=subject.id,
            created_at_epoch=now,
            expires_at_epoch=expires_at,
        )
    )
    db.commit()
    return AuthResponse(
        accessToken=token,
        expiresIn=settings.token_ttl_seconds,
        userId=subject.user_id,
    )


def get_wechat_client(request: Request) -> WechatAuthClient:
    return request.app.state.wechat_client


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db_session() -> Session:
    db = session_scope()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db_session)],
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthenticatedUser:
    """Bearer 令牌校验依赖：缺失/无效 → UNAUTHENTICATED，过期 → TOKEN_EXPIRED。"""
    parts = (authorization or "").split(" ", 1)
    token = parts[1].strip() if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not token:
        raise ApiError(401, "UNAUTHENTICATED", "缺少访问令牌")
    row = db.get(AccessToken, hash_token(token))
    if row is None:
        raise ApiError(401, "UNAUTHENTICATED", "访问令牌无效")
    if row.expires_at_epoch <= int(time.time()):
        raise ApiError(401, "TOKEN_EXPIRED", "访问令牌已过期")
    subject = db.get(AnonymousSubject, row.subject_id)
    if subject is None:  # 令牌存在但主体缺失：视为数据异常，拒绝访问
        raise ApiError(401, "UNAUTHENTICATED", "访问令牌无效")
    return AuthenticatedUser(subject_id=subject.id, user_id=subject.user_id)
