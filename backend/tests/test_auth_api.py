# -*- coding: utf-8 -*-
"""M4 行为测试：API-01 微信登录与 Bearer 令牌校验（docs/24 §8 API-01）。"""
from __future__ import annotations

import re
import time

import pytest
from fastapi import Depends
from sqlalchemy import inspect as sa_inspect, text

from pathai_backend.auth import get_current_user
from pathai_backend.database import get_engine

ENDPOINT = "/api/v1/auth/wechat"


@pytest.fixture()
def me_endpoint(app):
    app.add_api_route(
        "/_test/me",
        lambda user=Depends(get_current_user): {"userId": user.user_id},
        methods=["GET"],
    )
    return app


def _login(client, code: str):
    return client.post(ENDPOINT, json={"code": code})


def _envelope(body: dict) -> dict:
    assert set(body.keys()) == {"error", "requestId"}
    assert set(body["error"].keys()) == {"code", "message", "retryable", "details"}
    assert body["requestId"].startswith("req_")
    return body["error"]


class TestLoginSuccess:
    def test_returns_contract_response(self, client, fake):
        fake.openid_by_code = {"code-1": "openid-A"}
        resp = _login(client, "code-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tokenType"] == "Bearer"
        assert body["expiresIn"] == 7200
        assert len(body["accessToken"]) >= 32
        assert re.fullmatch(r"anon_[0-9a-f]{16}", body["userId"])

    def test_same_openid_reuses_subject(self, client, fake):
        fake.openid_by_code = {"c1": "openid-A", "c2": "openid-A"}
        first = _login(client, "c1").json()
        second = _login(client, "c2").json()
        assert first["userId"] == second["userId"]

    def test_different_openids_get_different_subjects(self, client, fake):
        fake.openid_by_code = {"c1": "openid-A", "c2": "openid-B"}
        first = _login(client, "c1").json()
        second = _login(client, "c2").json()
        assert first["userId"] != second["userId"]

    def test_multi_device_tokens_coexist(self, client, fake, app):
        fake.openid_by_code = {"c1": "openid-A", "c2": "openid-A"}
        app.add_api_route(
            "/_test/me",
            lambda user=Depends(get_current_user): {"userId": user.user_id},
            methods=["GET"],
        )
        t1 = _login(client, "c1").json()["accessToken"]
        t2 = _login(client, "c2").json()["accessToken"]
        assert t1 != t2
        for token in (t1, t2):
            resp = client.get("/_test/me", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200


class TestLoginFailures:
    def test_invalid_code_401(self, client, fake):
        fake.behavior = "invalid_code"
        resp = _login(client, "bad-code")
        assert resp.status_code == 401
        error = _envelope(resp.json())
        assert error["code"] == "AUTH_CODE_INVALID"
        assert error["retryable"] is False

    def test_provider_unavailable_503(self, client, fake):
        fake.behavior = "provider_error"
        resp = _login(client, "any-code")
        assert resp.status_code == 503
        error = _envelope(resp.json())
        assert error["code"] == "AUTH_PROVIDER_UNAVAILABLE"
        assert error["retryable"] is True

    def test_unknown_code_maps_to_invalid(self, client, fake):
        resp = _login(client, "never-seen")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTH_CODE_INVALID"

    @pytest.mark.parametrize("body", [{}, {"code": ""}, {"code": 123}, []])
    def test_invalid_request_body_400(self, client, fake, body):
        resp = client.post(ENDPOINT, json=body)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"


class TestPrivacy:
    def test_plaintext_openid_never_stored(self, client, fake):
        secret_openid = "oX7-PLAINTEXT-SENSITIVE"
        fake.openid_by_code = {"c1": secret_openid}
        _login(client, "c1")
        engine = get_engine()
        with engine.connect() as conn:
            names = sa_inspect(conn).get_table_names()
            for table in names:
                rows = conn.execute(text(f"SELECT * FROM {table}")).fetchall()
                for row in rows:
                    for value in row:
                        assert secret_openid != value

    def test_token_stored_as_hash_only(self, client, fake):
        fake.openid_by_code = {"c1": "openid-A"}
        token = _login(client, "c1").json()["accessToken"]
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT token_hash FROM access_tokens")).fetchall()
        assert len(rows) == 1
        assert rows[0][0] != token
        assert len(rows[0][0]) == 64


class TestBearerAuth:
    @pytest.fixture(autouse=True)
    def _setup(self, me_endpoint, fake, client):
        fake.openid_by_code = {"c1": "openid-A"}
        self.token = _login(client, "c1").json()["accessToken"]

    def _me(self, client, headers=None):
        return client.get("/_test/me", headers=headers or {})

    def test_valid_token_passes(self, client):
        resp = self._me(client, {"Authorization": f"Bearer {self.token}"})
        assert resp.status_code == 200
        assert re.fullmatch(r"anon_[0-9a-f]{16}", resp.json()["userId"])

    def test_missing_header_401_unauthenticated(self, client):
        resp = self._me(client)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_malformed_header_401_unauthenticated(self, client):
        for header in ("Basic abc", "Bearer", "bearer", "Token xyz"):
            resp = self._me(client, {"Authorization": header})
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "UNAUTHENTICATED", header

    def test_unknown_token_401_unauthenticated(self, client):
        resp = self._me(client, {"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_expired_token_401_token_expired(self, client):
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE access_tokens SET expires_at_epoch = :t"),
                {"t": int(time.time()) - 10},
            )
        resp = self._me(client, {"Authorization": f"Bearer {self.token}"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"


class TestFakeAuthSwitch:
    """C1：本地 fake 微信客户端开关（PATHAI_WECHAT_FAKE，仅 local 环境可开）。"""

    def _make_app(self, tmp_path):
        from fastapi.testclient import TestClient

        from pathai_backend.app import create_app
        from pathai_backend.config import Settings
        from pathai_backend.database import get_engine
        from pathai_backend.models import Base

        settings = Settings(
            env="local",
            database_url=f"sqlite:///{(tmp_path / 'fake-auth.db').as_posix()}",
            openid_salt="salt",
            wechat_fake=True,
        )
        application = create_app(settings)
        Base.metadata.create_all(get_engine())
        return TestClient(application, raise_server_exceptions=False)

    def test_local_fake_login_roundtrip(self, tmp_path):
        client = self._make_app(tmp_path)
        with client as c:
            resp = _login(c, "fake-user-1")
            assert resp.status_code == 200
            assert resp.json()["userId"].startswith("anon_")
            bad = _login(c, "not-fake-form")
            assert bad.status_code == 401
            assert _envelope(bad.json())["code"] == "AUTH_CODE_INVALID"

    def test_demo_env_forbidden(self):
        from pathai_backend.app import create_app
        from pathai_backend.config import Settings

        settings = Settings(env="demo", database_url="sqlite:///:memory:", wechat_fake=True)
        with pytest.raises(RuntimeError, match="PATHAI_WECHAT_FAKE"):
            create_app(settings)
