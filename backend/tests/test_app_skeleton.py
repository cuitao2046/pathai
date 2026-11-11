# -*- coding: utf-8 -*-
"""M1 骨架基线测试：requestId、统一错误封装、数据库 URL 解析规则。

对应规格：specs/phase-1-indoor-navigation/spec.md §4（技术栈与数据库规则）、
§8（每个响应含 requestId、统一错误结构）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pathai_backend.app import create_app
from pathai_backend.config import DEMO_ENV, Settings, resolve_database_url
from pathai_backend.errors import ApiError

ADMIN_DIST = Path(__file__).resolve().parents[2] / "admin-web" / "dist"


def _assert_error_shape(body: dict, expected_code: str) -> None:
    assert set(body.keys()) == {"error", "requestId"}
    assert body["requestId"].startswith("req_")
    error = body["error"]
    assert set(error.keys()) == {"code", "message", "retryable", "details"}
    assert error["code"] == expected_code


class TestRequestContext:
    def test_healthz_returns_ok_with_request_id(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert resp.headers["X-Request-Id"].startswith("req_")

    def test_request_id_unique_per_request(self, client: TestClient):
        first = client.get("/healthz").headers["X-Request-Id"]
        second = client.get("/healthz").headers["X-Request-Id"]
        assert first != second


class TestSecurityHeaders:
    def test_csp_limits_to_self(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.headers["Content-Security-Policy"].startswith("default-src 'self'")
        assert "connect-src 'self'" in resp.headers["Content-Security-Policy"]
        assert resp.headers["X-Content-Type-Options"] == "nosniff"


class TestAdminStaticHosting:
    def test_admin_ui_served_when_built(self, client: TestClient):
        if not (ADMIN_DIST / "index.html").exists():
            pytest.skip("admin-web/dist 未构建")
        resp = client.get("/admin/")
        assert resp.status_code == 200
        assert "PathAI 地图管理后台" in resp.text

    def test_api_route_not_captured_by_static_mount(self, client: TestClient):
        resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        _assert_error_shape(resp.json(), "NOT_FOUND")


class TestErrorEnvelope:
    def test_unknown_route_returns_not_found_envelope(self, client: TestClient):
        resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        _assert_error_shape(resp.json(), "NOT_FOUND")

    def test_validation_error_returns_400_invalid_request(self, app, client: TestClient):
        class _Body(BaseModel):
            sequence: int

        @app.post("/api/v1/_test/validated")
        def _route(body: _Body) -> dict:
            return {"sequence": body.sequence}

        resp = client.post("/api/v1/_test/validated", json={"sequence": "not-an-int"})
        assert resp.status_code == 400
        _assert_error_shape(resp.json(), "INVALID_REQUEST")

    def test_api_error_uses_stable_code_and_retryable(self, app, client: TestClient):
        @app.get("/api/v1/_test/fail")
        def _route() -> dict:
            raise ApiError(422, "LOCATION_UNRELIABLE", "暂时无法可靠确定当前位置", retryable=True)

        resp = client.get("/api/v1/_test/fail")
        assert resp.status_code == 422
        _assert_error_shape(resp.json(), "LOCATION_UNRELIABLE")
        assert resp.json()["error"]["retryable"] is True

    def test_unhandled_exception_returns_internal_error_without_leaking_details(
        self, app, client: TestClient
    ):
        @app.get("/api/v1/_test/boom")
        def _route() -> dict:
            raise RuntimeError("secret-password-in-internal-message")

        resp = client.get("/api/v1/_test/boom")
        assert resp.status_code == 500
        _assert_error_shape(resp.json(), "INTERNAL_ERROR")
        # 内部细节不得外泄
        assert "secret-password" not in resp.text

    def test_unhandled_exception_is_logged_with_request_id(self, app, client, caplog):
        @app.get("/api/v1/_test/boom-logged")
        def _route() -> dict:
            raise RuntimeError("boom-for-log")

        with caplog.at_level(logging.ERROR, logger="pathai.errors"):
            resp = client.get("/api/v1/_test/boom-logged")
        assert resp.status_code == 500
        records = [r for r in caplog.records if r.name == "pathai.errors"]
        assert records, "未处理异常必须留下 ERROR 日志"
        record = records[-1]
        assert "boom-logged" in record.getMessage()
        assert getattr(record, "request_id", "").startswith("req_")


class TestAccessLog:
    def test_api_request_logged_with_structured_fields(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="pathai.access"):
            resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        records = [r for r in caplog.records if r.name == "pathai.access"]
        assert records, "API 请求必须留访问日志"
        record = records[-1]
        assert getattr(record, "method") == "GET"
        assert getattr(record, "path") == "/api/v1/does-not-exist"
        assert getattr(record, "status") == 404
        assert isinstance(getattr(record, "duration_ms"), float)
        assert getattr(record, "request_id") == resp.headers["X-Request-Id"]

    def test_healthz_not_logged(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="pathai.access"):
            client.get("/healthz")
        assert not [r for r in caplog.records if r.name == "pathai.access"]


class TestBodySizeLimit:
    """直连部署无反代兜底，服务端自行拒绝超大 body（413 PAYLOAD_TOO_LARGE）。"""

    @pytest.fixture()
    def small_limit_app(self):
        return create_app(Settings.from_env({
            "PATHAI_MAX_BODY_BYTES": "1024",
            "PATHAI_DATABASE_URL": "sqlite:///:memory:",
        }))

    def _register_echo(self, app) -> None:
        @app.post("/api/v1/_test/echo")
        def _route(body: dict) -> dict:
            return body

    def test_oversized_content_length_rejected_without_reading(self, small_limit_app):
        self._register_echo(small_limit_app)
        client = TestClient(small_limit_app)
        resp = client.post("/api/v1/_test/echo", content=b"x" * 2048,
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 413
        body = resp.json()
        assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"
        assert body["error"]["details"]["maxBodyBytes"] == 1024
        assert body["requestId"].startswith("req_")
        assert resp.headers["X-Request-Id"] == body["requestId"]

    def test_oversized_chunked_body_rejected(self, small_limit_app):
        self._register_echo(small_limit_app)
        client = TestClient(small_limit_app)

        def _gen():
            yield b"x" * 800
            yield b"x" * 800  # 累计 1600 > 1024，无 Content-Length

        resp = client.post("/api/v1/_test/echo", content=_gen())
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

    def test_body_within_limit_passes_through(self, small_limit_app):
        self._register_echo(small_limit_app)
        client = TestClient(small_limit_app)
        payload = {"data": "y" * 500}
        resp = client.post("/api/v1/_test/echo", json=payload)
        assert resp.status_code == 200
        assert resp.json() == payload

    def test_get_requests_unaffected(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200


class TestDatabaseUrlRules:
    """spec §4：本地默认 SQLite；Demo 必须显式配置 PostgreSQL，禁止静默回退。"""

    def test_local_without_url_uses_local_sqlite_file(self):
        url = resolve_database_url(env="local", explicit_url="")
        assert url.startswith("sqlite:///")

    def test_explicit_url_is_honored(self):
        url = "sqlite:///:memory:"
        assert resolve_database_url(env="local", explicit_url=url) == url

    def test_demo_requires_explicit_url(self):
        with pytest.raises(RuntimeError, match="PATHAI_DATABASE_URL"):
            resolve_database_url(env=DEMO_ENV, explicit_url="")

    def test_demo_with_postgres_url_is_accepted(self):
        url = "postgresql+psycopg://user:pw@db.example:5432/pathai"
        assert resolve_database_url(env=DEMO_ENV, explicit_url=url) == url

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(RuntimeError, match="scheme"):
            resolve_database_url(env="local", explicit_url="mysql://host/db")

    def test_settings_default_env_is_local(self):
        assert Settings.from_env({}).env == "local"
        assert Settings.from_env({"PATHAI_ENV": "demo"}).env == "demo"

    def test_location_params_defaults_and_env_override(self):
        defaults = Settings.from_env({})
        assert defaults.location_rssi_at_1m == -71.0
        assert defaults.location_path_loss_n == 2.0
        assert defaults.location_residual_reliable_m == 1.5
        assert defaults.location_residual_degraded_m == 4.0
        assert defaults.location_params_version == "unset"

        overridden = Settings.from_env(
            {
                "PATHAI_LOCATION_RSSI_AT_1M": "-69.2",
                "PATHAI_LOCATION_PATH_LOSS_N": "2.31",
                "PATHAI_LOCATION_RESIDUAL_RELIABLE_M": "1.2",
                "PATHAI_LOCATION_RESIDUAL_DEGRADED_M": "3.5",
                "PATHAI_LOCATION_PARAMS_VERSION": "loc-2026-11-01.1",
            }
        )
        assert overridden.location_rssi_at_1m == -69.2
        assert overridden.location_path_loss_n == 2.31
        assert overridden.location_residual_reliable_m == 1.2
        assert overridden.location_residual_degraded_m == 3.5
        assert overridden.location_params_version == "loc-2026-11-01.1"

    def test_llm_config_from_env(self):
        settings = Settings.from_env(
            {
                "PATHAI_LLM_BASE_URL": "https://api.example/v1",
                "PATHAI_LLM_API_KEY": "k-123",
                "PATHAI_LLM_MODEL": "deepseek-chat",
                "PATHAI_LLM_TIMEOUT_S": "5.5",
                "PATHAI_LLM_STRUCTURED_MODE": "json_schema",
            }
        )
        assert settings.llm_configured
        assert settings.llm_timeout_s == 5.5
        assert settings.llm_structured_mode == "json_schema"
        assert "k-123" not in repr(settings)

    def test_llm_not_configured_without_full_triplet(self):
        assert not Settings.from_env({}).llm_configured
        partial = Settings.from_env(
            {"PATHAI_LLM_BASE_URL": "https://api.example/v1", "PATHAI_LLM_MODEL": "m"}
        )
        assert not partial.llm_configured
