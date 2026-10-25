# -*- coding: utf-8 -*-
"""数据库引擎与会话（同步 SQLAlchemy 2）。

第一阶段请求处理使用同步 Session/同步路由（spec §4），避免为少量并发
引入异步数据库复杂度；CPU 密集的定位/路由计算不得在事件循环中执行。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_engine(database_url: str) -> Engine:
    """创建全局引擎；SQLite 需要允许同线程内多连接复用文件库。"""
    global _engine, _session_factory
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("数据库引擎未初始化，请先调用 init_engine()")
    return _engine


def session_scope() -> Session:
    if _session_factory is None:
        raise RuntimeError("数据库引擎未初始化，请先调用 init_engine()")
    return _session_factory()


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
