from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from pathai_backend.app import create_app
from pathai_backend.config import Settings
from pathai_backend.local_discovery_v1 import LocalDiscoveryServer, parse_unpaired_discover, unpaired_offer


QUERY = {"protocol": "local-service-discovery", "version": 1, "type": "BUSINESS_DISCOVER",
         "serviceName": "my-backend", "requestId": "a" * 32}


def test_unpaired_offer_is_explicitly_untrusted_and_uses_business_port():
    assert parse_unpaired_discover(json.dumps(QUERY).encode()) == QUERY
    assert parse_unpaired_discover(json.dumps({**QUERY, "type": "DISCOVER"}).encode()) is None
    assert parse_unpaired_discover(json.dumps({**QUERY, "extra": 1}).encode()) is None
    assert parse_unpaired_discover(b'{"requestId":"a","requestId":"b"}') is None
    assert parse_unpaired_discover(b"{" + b"a" * 1200) is None
    assert parse_unpaired_discover(json.dumps({**QUERY, "requestId": "wrong"}).encode()) is None
    offer = json.loads(unpaired_offer(QUERY, 8000))
    assert offer == {"protocol": "local-service-discovery", "version": 1, "type": "BUSINESS_OFFER",
                     "serviceName": "my-backend", "requestId": "a" * 32, "port": 8000,
                     "authenticated": False}


def test_discovery_lifecycle_does_not_enable_pairing(app, settings, monkeypatch):
    settings = replace(settings, local_discovery_port=39001, local_business_port=8000)
    monkeypatch.setattr(LocalDiscoveryServer, "start", lambda self: None)
    monkeypatch.setattr(LocalDiscoveryServer, "close", lambda self: None)
    service = create_app(settings)
    with TestClient(service) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.app.state.local_pairing_ready is False
        assert client.post("/local/v1/admin/pair/open", json={"ipv4": "192.168.1.28"}).status_code == 403


def test_invalid_discovery_port_rejected(settings):
    with pytest.raises(RuntimeError):
        create_app(replace(settings, local_discovery_port=39001, local_business_port=0))


def test_local_environment_defaults_enable_business_discovery_only():
    local = Settings.from_env({"PATHAI_ENV": "local"})
    demo = Settings.from_env({"PATHAI_ENV": "demo"})
    assert (local.local_discovery_port, local.local_business_port) == (39001, 8000)
    assert (demo.local_discovery_port, demo.local_business_port) == (0, 0)
