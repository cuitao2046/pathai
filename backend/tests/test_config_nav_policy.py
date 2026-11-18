# -*- coding: utf-8 -*-
"""导航判定阈值配置：默认冻结值、测试人员保存、范围校验与热生效。"""
from __future__ import annotations

import pytest

POLICY_URL = "/api/v1/config/nav-policy"
PARAMS_URL = "/api/v1/config/location-params"


def _grant_tester() -> None:
    """给首个登录主体授予测试人员权限（与 client_permissions.require_tester 对齐）。"""
    from pathai_backend.database import session_scope
    from pathai_backend.models import AnonymousSubject, ClientPermission

    with session_scope() as db:
        subject = db.query(AnonymousSubject).first()
        assert subject is not None
        db.add(
            ClientPermission(
                subject_id=subject.id, is_tester=True, can_apply_config=True
            )
        )
        db.commit()


class TestNavPolicy:
    def test_defaults_are_frozen_2m_and_3m(self, client):
        resp = client.get(POLICY_URL)
        assert resp.status_code == 200
        body = resp.json()
        assert body["arrivalRadiusM"] == 2.0
        assert body["turnPromptDistanceM"] == 3.0
        assert body["source"] == "env"

    def test_location_params_exposes_nav_defaults(self, client):
        body = client.get(PARAMS_URL).json()
        assert body["navArrivalRadiusM"] == 2.0
        assert body["navTurnPromptDistanceM"] == 3.0
        assert body["navPolicySource"] == "env"

    def test_put_requires_tester(self, client, auth_headers):
        resp = client.put(
            POLICY_URL,
            json={"arrivalRadiusM": 2.5, "turnPromptDistanceM": 3.5},
            headers=auth_headers,
        )
        assert resp.status_code == 403

    def test_tester_can_save_and_values_take_effect(self, client, auth_headers):
        _grant_tester()
        saved = client.put(
            POLICY_URL,
            json={"arrivalRadiusM": 2.5, "turnPromptDistanceM": 3.5},
            headers=auth_headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["arrivalRadiusM"] == 2.5
        assert saved.json()["turnPromptDistanceM"] == 3.5
        assert saved.json()["source"] == "db"

        # 热生效：读取路径与定位参数接口都返回新值，无需重启。
        assert client.get(POLICY_URL).json()["arrivalRadiusM"] == 2.5
        params = client.get(PARAMS_URL).json()
        assert params["navArrivalRadiusM"] == 2.5
        assert params["navTurnPromptDistanceM"] == 3.5
        assert params["navPolicySource"] == "db"

    @pytest.mark.parametrize(
        "body",
        [
            {"arrivalRadiusM": 0.4, "turnPromptDistanceM": 3.0},
            {"arrivalRadiusM": 20.1, "turnPromptDistanceM": 3.0},
            {"arrivalRadiusM": 2.0, "turnPromptDistanceM": 0.4},
            {"arrivalRadiusM": 2.0, "turnPromptDistanceM": 10.1},
        ],
    )
    def test_out_of_range_rejected(self, client, auth_headers, body):
        _grant_tester()
        resp = client.put(POLICY_URL, json=body, headers=auth_headers)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
