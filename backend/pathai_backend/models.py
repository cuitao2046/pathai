# -*- coding: utf-8 -*-
"""ORM 模型基类。

具体表模型按里程碑随迁移一起加入（M4 起：匿名主体/令牌映射等）。
数据库仅持久化用户主体映射、导航会话、幂等记录及最小业务元数据；
地图几何等大文件不入库（spec §4）。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AnonymousSubject(Base):
    """匿名主体：仅存加盐 openid 哈希（docs/24 §8 API-01），不存明文身份。"""

    __tablename__ = "anonymous_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    openid_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(21), nullable=False, unique=True)
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class AccessToken(Base):
    """不透明访问令牌：服务端仅存 token 哈希（sha256 hex 作主键），便于吊销。

    多设备并发登录各自持有独立令牌，互不踢除；过期自然失效。
    """

    __tablename__ = "access_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), nullable=False
    )
    created_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


Index("ix_access_tokens_subject", AccessToken.subject_id)


class LocationSequence(Base):
    """定位批次序号：按（主体, 场所）记录已处理最大 sequence，拒绝过期批次。

    sequence 严格小于已处理值 → 409 STALE_LOCATION_BATCH（docs/24 API-05）。
    """

    __tablename__ = "location_sequences"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at_epoch: Mapped[int] = mapped_column(Integer, nullable=False)


class NavSession(Base):
    """导航会话：状态机 active↔recovering→arrived/ended（终态不可逆，docs/24 §4.3）。"""

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
    """会话创建幂等记录：Idempotency-Key → 已创建会话及其响应（docs/24 API-06）。"""

    __tablename__ = "nav_idempotency_keys"

    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("anonymous_subjects.id"), primary_key=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(40), nullable=False)
    response_json: Mapped[str] = mapped_column(Text, nullable=False)
