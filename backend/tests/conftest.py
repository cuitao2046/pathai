# -*- coding: utf-8 -*-
"""测试夹具：每个用例独立临时 SQLite + 独立临时地图目录（spec §4 允许临时 SQLite）。

- FakeWechatClient：微信 code2Session 内存替身，供各测试注入/改写；
- auth_headers：已登录 Bearer 头（API-02 起，登录外接口均需认证）；
- maps_dir：拷贝合成样例地图包的临时地图目录。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pathai_backend.app import create_app
from pathai_backend.config import Settings
from pathai_backend.database import get_engine
from pathai_backend.models import Base
from pathai_backend.wechat import WechatAuthError

TEST_OPENID_SALT = "test-openid-salt"

_BACK_DIR = Path(__file__).resolve().parent.parent
SAMPLE_MAP_FILE = _BACK_DIR / "maps_data" / "school-building-01" / "2026-10-01.1.json"


class FakeWechatClient:
    """按 code 映射 openid 的内存替身；behavior 注入故障分支。"""

    def __init__(self, openid_by_code: dict[str, str] | None = None, behavior: str | None = None):
        self.openid_by_code = dict(openid_by_code or {})
        self.behavior = behavior

    def code_to_session(self, code: str) -> str:
        if self.behavior == "invalid_code":
            raise WechatAuthError("invalid_code", "fake")
        if self.behavior == "provider_error":
            raise WechatAuthError("provider_error", "fake")
        openid = self.openid_by_code.get(code)
        if openid is None:
            raise WechatAuthError("invalid_code", f"未知 code {code}")
        return openid


@pytest.fixture()
def maps_dir(tmp_path) -> Path:
    site_dir = tmp_path / "school-building-01"
    site_dir.mkdir()
    shutil.copyfile(SAMPLE_MAP_FILE, site_dir / SAMPLE_MAP_FILE.name)
    return tmp_path


@pytest.fixture()
def settings(tmp_path, maps_dir) -> Settings:
    return Settings(
        env="local",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        openid_salt=TEST_OPENID_SALT,
        map_data_dir=maps_dir,
        public_base_url="https://api.test",
    )


@pytest.fixture()
def app(settings):
    application = create_app(settings)
    Base.metadata.create_all(get_engine())
    return application


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def fake(app):
    fake_client = FakeWechatClient()
    app.state.wechat_client = fake_client
    return fake_client


@pytest.fixture()
def auth_headers(client, fake) -> dict[str, str]:
    fake.openid_by_code = {"code-ok": "openid-A"}
    resp = client.post("/api/v1/auth/wechat", json={"code": "code-ok"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}
