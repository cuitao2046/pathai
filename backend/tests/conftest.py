# -*- coding: utf-8 -*-
"""测试夹具：每个用例使用独立临时 SQLite 数据库（spec §4 允许单元测试用临时 SQLite）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pathai_backend.app import create_app
from pathai_backend.config import Settings


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(env="local", database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture()
def app(settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
