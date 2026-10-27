# -*- coding: utf-8 -*-
"""管理后台服务层：账户初始化、会话管理与关键操作审计。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from sqlalchemy import delete, select

from pathai_backend.admin.security import (
    HASH_PARAMS,
    hash_password,
    new_csrf_token,
    new_session_token,
    sha256_hex,
    verify_password,
)
from pathai_backend.database import session_scope
from pathai_backend.errors import ApiError
from pathai_backend.models import AdminAccount, AdminAuditEvent, AdminSession

ADMIN_USERNAME = "admin"
ADMIN_INITIAL_PASSWORD = "admin"
SESSION_TTL_SECONDS = 8 * 3600


@dataclass(frozen=True)
class AdminPrincipal:
    account: AdminAccount
    token: str
    csrf_token: str


def ensure_admin_account() -> None:
    """首次启动初始化固定 admin/admin 账户；已存在时不改动。"""
    now = int(time.time())
    with session_scope() as session:
        existing = session.scalar(select(AdminAccount).where(AdminAccount.username == ADMIN_USERNAME))
        if existing is not None:
            return
        password_hash, salt = hash_password(ADMIN_INITIAL_PASSWORD)
        session.add(
            AdminAccount(
                username=ADMIN_USERNAME,
                password_hash=password_hash,
                password_salt=salt,
                hash_params=HASH_PARAMS,
                must_change_password=True,
                created_at_epoch=now,
                updated_at_epoch=now,
            )
        )
        session.commit()


def authenticate(username: str, password: str) -> AdminAccount | None:
    """验证凭据；用户不存在也执行一次哈希比较以抹平时序差异。"""
    with session_scope() as session:
        account = session.scalar(select(AdminAccount).where(AdminAccount.username == username))
        if account is None:
            verify_password(password, "00", "0" * 64)
            return None
        if not verify_password(password, account.password_salt, account.password_hash):
            return None
        session.refresh(account)
        return account


def create_session(account: AdminAccount) -> tuple[str, str]:
    """签发新会话，返回 (session_token, csrf_token)。"""
    token = new_session_token()
    csrf = new_csrf_token()
    now = int(time.time())
    with session_scope() as session:
        session.add(
                AdminSession(
                    token_hash=sha256_hex(token),
                    csrf_hash=sha256_hex(csrf),
                    created_at_epoch=now,
                    expires_at_epoch=now + SESSION_TTL_SECONDS,
                )
            )
        session.commit()
    return token, csrf


def load_session(token: str) -> tuple[AdminAccount, AdminSession] | None:
    """按 cookie token 加载未过期会话及其账户。"""
    token_hash = sha256_hex(token)
    now = int(time.time())
    with session_scope() as session:
        row = session.get(AdminSession, token_hash)
        if row is None or row.expires_at_epoch <= now:
            return None
        account = session.get(AdminAccount, _sole_account_id(session))
        if account is None:
            return None
        session.refresh(row)
        session.refresh(account)
        return account, row


def rotate_csrf(token: str) -> str:
    """bootstrap 时轮换 CSRF token 并返回新值（会话不变）。"""
    csrf = new_csrf_token()
    with session_scope() as session:
        row = session.get(AdminSession, sha256_hex(token))
        if row is None:
            raise ApiError(401, "ADMIN_UNAUTHORIZED", "未登录或会话已过期")
        row.csrf_hash = sha256_hex(csrf)
        session.commit()
        session.refresh(row)
    return csrf


def revoke_session(token: str) -> None:
    with session_scope() as session:
        session.execute(delete(AdminSession).where(AdminSession.token_hash == sha256_hex(token)))
        session.commit()


def revoke_other_sessions(keep_token: str) -> None:
    with session_scope() as session:
        keep_hash = sha256_hex(keep_token)
        rows = session.scalars(select(AdminSession)).all()
        stale = [r.token_hash for r in rows if r.token_hash != keep_hash]
        for token_hash in stale:
            session.execute(delete(AdminSession).where(AdminSession.token_hash == token_hash))
        session.commit()


def rotate_session(old_token: str) -> tuple[str, str]:
    """改密后轮换当前会话：吊销旧 token，签发新 token/CSRF。"""
    revoke_session(old_token)
    with session_scope() as session:
        account = session.get(AdminAccount, _sole_account_id(session))
        if account is None:  # pragma: no cover - 账户在改密流程中必存在
            raise RuntimeError("管理员账户不存在，无法轮换会话")
    return create_session(account)


def verify_current(account: AdminAccount, password: str) -> bool:
    """改密前校验当前密码。"""
    return verify_password(password, account.password_salt, account.password_hash)


def change_password(account: AdminAccount, new_password: str) -> None:
    password_hash, salt = hash_password(new_password)
    with session_scope() as session:
        row = session.get(AdminAccount, account.id)
        row.password_hash = password_hash
        row.password_salt = salt
        row.hash_params = HASH_PARAMS
        row.must_change_password = False
        row.updated_at_epoch = int(time.time())
        session.commit()
        session.refresh(row)


def record_audit(event_type: str, success: bool, details: dict | None = None) -> None:
    """记录关键管理事件；审计失败不阻断主流程（demo 单机 SQLite）。"""
    try:
        with session_scope() as session:
            session.add(
                AdminAuditEvent(
                    event_type=event_type,
                    success=success,
                    created_at_epoch=int(time.time()),
                    details_json=json.dumps(details or {}, ensure_ascii=False),
                )
            )
    except Exception:  # noqa: BLE001 - 审计写入失败不影响业务响应
        pass


def _sole_account_id(session) -> int:
    """demo 单管理员：账户表预期只有 admin 一行。"""
    account = session.scalar(select(AdminAccount).where(AdminAccount.username == ADMIN_USERNAME))
    if account is None:
        raise RuntimeError("管理员账户尚未初始化")
    return account.id
