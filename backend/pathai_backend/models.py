# -*- coding: utf-8 -*-
"""ORM 模型基类。

具体表模型按里程碑随迁移一起加入（M4 起：匿名主体/令牌映射等）。
数据库仅持久化用户主体映射、导航会话、幂等记录及最小业务元数据；
地图几何等大文件不入库（spec §4）。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
