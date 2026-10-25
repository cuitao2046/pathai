# -*- coding: utf-8 -*-
"""ORM 模型基类。

具体表模型按里程碑随迁移一起加入（M4 起：匿名主体/令牌映射等）。
数据库仅持久化用户主体映射、导航会话、幂等记录及最小业务元数据；
地图几何等大文件不入库（spec §4）。
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
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
