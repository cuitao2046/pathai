# -*- coding: utf-8 -*-
"""ORM 模型基类。"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AnonymousSubject(Base):
    """匿名主体：仅存加盐 openid 哈希，不存明文身份。"""

    __tablename__ = "anonymous_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    openid_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(21), nullable=False, unique=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AccessToken(Base):
    """不透明访问令牌：服务端仅存 token 哈希。"""

    __tablename__ = "access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), nullable=False
    )
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_access_tokens_subject", AccessToken.subject_id)


class LocationSequence(Base):
    """定位批次序号。"""

    __tablename__ = "location_sequences"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class NavSession(Base):
    """导航会话。"""

    __tablename__ = "nav_sessions"

    session_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), nullable=False
    )
    site_id: Mapped[str] = mapped_column(String(64), nullable=False)
    map_version: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_place_id: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_name: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_floor_id: Mapped[str] = mapped_column(String(16), nullable=False)
    user_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    route_json: Mapped[str] = mapped_column(Text, nullable=False)
    current_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    route_rules_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    ended_at_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


Index("ix_nav_sessions_subject", NavSession.subject_id)


class NavIdempotency(Base):
    """导航创建幂等记录。"""

    __tablename__ = "nav_idempotency_keys"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(40), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)


class AdminAccount(Base):
    """管理后台固定账户：仅存带随机盐的密码哈希，不存明文。

    must_change_password：首版初始化 admin/admin 后为真，成功改密后置假。
    """

    __tablename__ = "admin_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    password_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_params: Mapped[str] = mapped_column(String(64), nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AdminSession(Base):
    """管理员会话：服务端只存 token/CSRF 的 SHA-256 摘要，cookie 携带原文。"""

    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class MapDraft(Base):
    """每场所唯一活动草稿：只保存白名单编辑差异，不复制整份地图包。"""

    __tablename__ = "map_drafts"

    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_version: Mapped[str] = mapped_column(String(32), nullable=False)
    patch_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AdminAuditEvent(Base):
    """关键管理操作审计：登录成功/失败、改密、退出、草稿重置、发布、回滚。

    不记录密码、token 或字段级编辑差异（map-admin-spec §6 审计）。
    """

    __tablename__ = "admin_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)

