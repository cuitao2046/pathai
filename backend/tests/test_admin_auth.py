# -*- coding: utf-8 -*-
"""管理员认证 API 测试（Gate 1）：登录、首改密门禁、改密、CSRF/Origin、会话隔离与退出。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pathai_backend.admin.deps import COOKIE_NAME, CSRF_HEADER

NEW_PASSWORD = "safe-password-1"


def login(client: TestClient, password: str = "admin"):
    return client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": password}
    )


def change_password(client: TestClient, csrf: str, current: str = "admin", new: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/admin/auth/password",
        json={"currentPassword": current, "newPassword": new, "confirmPassword": new},
        headers={CSRF_HEADER: csrf},
    )


def ready_admin(client: TestClient) -> str:
    """登录并完成首改密，返回改密后轮换的新 CSRF token。"""
    resp = login(client)
    assert resp.status_code == 200
    resp = change_password(client, resp.json()["csrfToken"])
    assert resp.status_code == 200
    return resp.json()["csrfToken"]


@pytest.fixture()
def admin_csrf(client) -> str:
    return ready_admin(client)


class TestLogin:
    def test_default_credentials_login(self, client):
        resp = login(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "admin"
        assert body["mustChangePassword"] is True
        assert body["csrfToken"]
        assert resp.headers["set-cookie"].startswith(f"{COOKIE_NAME}=")

    def test_wrong_password_rejected(self, client):
        resp = login(client, password="wrong-pass")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "ADMIN_INVALID_CREDENTIALS"

    def test_unknown_username_same_error(self, client):
        resp = client.post(
            "/api/v1/admin/auth/login", json={"username": "nobody", "password": "x"}
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "ADMIN_INVALID_CREDENTIALS"

    def test_request_body_strict(self, client):
        resp = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "admin", "extra": 1},
        )
        # 项目约定：请求体校验失败统一 400 INVALID_REQUEST（errors.py）
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_cross_origin_login_rejected(self, client):
        resp = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "admin"},
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ADMIN_ORIGIN_INVALID"

    def test_same_origin_login_accepted(self, client):
        resp = client.post(
            "/api/v1/admin/auth/login",
            json={"username": "admin", "password": "admin"},
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200


class TestPasswordChangeGate:
    def test_ready_gate_blocks_before_change(self, client):
        login(client)
        resp = client.get("/api/v1/admin/sites")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ADMIN_PASSWORD_CHANGE_REQUIRED"

    def test_change_password_requires_csrf(self, client):
        login(client)
        resp = client.post(
            "/api/v1/admin/auth/password",
            json={
                "currentPassword": "admin",
                "newPassword": NEW_PASSWORD,
                "confirmPassword": NEW_PASSWORD,
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ADMIN_CSRF_INVALID"


class TestPasswordChange:
    def test_success_flow(self, client, admin_csrf):
        resp = client.get("/api/v1/admin/sites")
        assert resp.status_code == 200

        me = client.get("/api/v1/admin/auth/me")
        assert me.status_code == 200
        assert me.json()["mustChangePassword"] is False
        # /me 每次轮换 CSRF token
        assert me.json()["csrfToken"] != admin_csrf

    def test_new_password_required_for_relogin(self, client):
        ready_admin(client)
        assert login(client).status_code == 401
        assert login(client, password=NEW_PASSWORD).status_code == 200

    def test_confirm_mismatch(self, client):
        csrf = login(client).json()["csrfToken"]
        resp = client.post(
            "/api/v1/admin/auth/password",
            json={
                "currentPassword": "admin",
                "newPassword": NEW_PASSWORD,
                "confirmPassword": "different",
            },
            headers={CSRF_HEADER: csrf},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PASSWORD_MISMATCH"

    def test_too_short_password(self, client):
        csrf = login(client).json()["csrfToken"]
        resp = client.post(
            "/api/v1/admin/auth/password",
            json={"currentPassword": "admin", "newPassword": "a1", "confirmPassword": "a1"},
            headers={CSRF_HEADER: csrf},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PASSWORD_TOO_SHORT"

    def test_wrong_current_password(self, client):
        csrf = login(client).json()["csrfToken"]
        resp = change_password(client, csrf, current="not-current")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "CURRENT_PASSWORD_INVALID"


class TestSessionIsolation:
    def test_wechat_token_cannot_access_admin(self, client, auth_headers):
        resp = client.get("/api/v1/admin/sites", headers=auth_headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "ADMIN_UNAUTHORIZED"

    def test_admin_session_cannot_access_wechat_api(self, client, fake, admin_csrf):
        fake.openid_by_code = {"code-ok": "openid-A"}
        wechat_login = client.post("/api/v1/auth/wechat", json={"code": "code-ok"})
        assert wechat_login.status_code == 200
        # 不带 Bearer、仅管理 cookie：微信地图 API 仍拒绝
        resp = client.get("/api/v1/sites/school-building-01/map")
        assert resp.status_code == 401

    def test_password_change_revokes_other_sessions(self, client, app):
        csrf_a = login(client).json()["csrfToken"]
        with TestClient(app) as session_b:
            login(session_b)
            assert change_password(client, csrf_a).status_code == 200
            resp = session_b.get("/api/v1/admin/sites")
            assert resp.status_code == 401


class TestLogout:
    def test_logout_revokes_session(self, client, admin_csrf):
        resp = client.post("/api/v1/admin/auth/logout", headers={CSRF_HEADER: admin_csrf})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        resp = client.get("/api/v1/admin/sites")
        assert resp.status_code == 401

    def test_logout_requires_csrf(self, client, admin_csrf):
        resp = client.post("/api/v1/admin/auth/logout")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ADMIN_CSRF_INVALID"
