# -*- coding: utf-8 -*-
"""局域网发现身份接口：/healthz 身份字段与 /api/v1/device/info。

对应 specs/local-service-discovery/spec.md v0.2.1：
设备身份以 device_id 为准，HTTP 单播扫描据此排除同网段的其它服务。
"""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from pathai_backend.app import API_VERSION, SERVICE_CAPABILITIES, create_app
from pathai_backend.database import get_engine
from pathai_backend.models import Base


def _start(settings) -> TestClient:
    app = create_app(settings)
    Base.metadata.create_all(get_engine())
    return TestClient(app)


def test_device_info_exposes_non_sensitive_capabilities(settings):
    with _start(settings) as client:
        health = client.get("/healthz").json()
        info = client.get("/api/v1/device/info")
        assert info.status_code == 200
        body = info.json()
        assert body["service"] == "my-backend"
        assert body["device_id"] == health["device_id"]
        assert body["device_name"]
        assert body["version"] == "0.1.0"
        assert body["api_version"] == API_VERSION
        assert body["services"] == {"http": 8000}
        assert body["capabilities"] == list(SERVICE_CAPABILITIES)


def test_device_info_does_not_require_auth(settings):
    with _start(settings) as client:
        assert client.get("/api/v1/device/info", headers={}).status_code == 200


def test_device_id_stable_across_restarts(settings):
    """同一数据库重启后 device_id 不变：IP 只作地址，设备身份以 device_id 为准。"""
    with _start(settings) as client:
        first = client.get("/healthz").json()["device_id"]
    with _start(replace(settings)) as client:
        second = client.get("/healthz").json()["device_id"]
    assert first.startswith("srv-")
    assert first == second
