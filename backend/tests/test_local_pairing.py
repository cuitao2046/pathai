from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from pathai_backend.local_discovery_v1 import offer_for_query, parse_discover
from pathai_backend.local_identity_protocol import authenticated, decode_key, verify


def _local(client):
    client.app.state.settings = replace(client.app.state.settings, local_business_port=8000,
                                        local_pairing_admin_token="T" * 32)
    client.app.state.local_pairing_ready = True
    return TestClient(client.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 42000),
                      raise_server_exceptions=False,
                      headers={"Origin": "http://127.0.0.1:8000", "X-Local-Pairing-Admin": "T" * 32})


def _open(client):
    response = client.post("/local/v1/admin/pair/open", json={"ipv4": "192.168.1.28"})
    assert response.status_code == 200
    return response.json()


def _activate(client, pairing):
    key = decode_key(pairing["pairingKey"])
    payload = {
        "protocol": "local-service-discovery", "version": 1, "type": "ACTIVATE",
        "serviceName": "my-backend", "deviceId": pairing["deviceId"],
        "pairId": pairing["pairId"], "nonce": "0123456789abcdef0123456789abcdef",
    }
    return client.post("/local/v1/pair/activate", json=authenticated("ACTIVATE", payload, key))


def test_pair_activation_probe_and_revocation(client, monkeypatch):
    client = _local(client)
    monkeypatch.setattr("pathai_backend.routers.local_pairing._check_local_endpoint", lambda *_args: None)
    pairing = _open(client)
    assert len(decode_key(pairing["pairingKey"])) == 32
    challenge = "abcdef0123456789abcdef0123456789"
    url = "/local/v1/probe"
    args = {"pairId": pairing["pairId"], "challenge": challenge}
    assert client.get(url, params=args).status_code == 403
    activated = _activate(client, pairing)
    assert activated.status_code == 200
    assert verify("ACTIVATE", activated.json(), decode_key(pairing["pairingKey"]))
    assert _activate(client, pairing).json() == activated.json()
    response = client.get(url, params=args)
    assert response.status_code == 200
    assert response.json()["challenge"] == challenge
    assert verify("PROBE", response.json(), decode_key(pairing["pairingKey"]))
    query = {"protocol": "local-service-discovery", "version": 1, "type": "DISCOVER",
             "requestId": challenge, "serviceName": "my-backend", "deviceId": pairing["deviceId"],
             "pairId": pairing["pairId"]}
    parsed = parse_discover(json.dumps(query).encode())
    assert parsed == query
    offer = offer_for_query(client.app.state.local_pairing, parsed, 8000)
    decoded = json.loads(offer)
    assert decoded["port"] == 8000
    assert verify("OFFER", decoded, decode_key(pairing["pairingKey"]))
    assert offer_for_query(client.app.state.local_pairing, {**parsed, "deviceId": "srv-ffffffffffffffff"}, 8000) is None
    assert client.post("/local/v1/admin/pair/revoke", json={"pairId": pairing["pairId"]}).json() == {"revoked": True}
    assert offer_for_query(client.app.state.local_pairing, parsed, 8000) is None
    assert client.get(url, params=args).status_code == 403
    assert _activate(client, pairing).status_code == 403


def test_pair_window_expiry_and_replaced_window(client, monkeypatch):
    client = _local(client)
    monkeypatch.setattr("pathai_backend.routers.local_pairing._check_local_endpoint", lambda *_args: None)
    from pathai_backend import local_pairing
    clock = [1000]
    monkeypatch.setattr(local_pairing.time, "time", lambda: clock[0])
    first = _open(client)
    assert client.post("/local/v1/admin/pair/open", json={"ipv4": "192.168.1.28"}).status_code == 409
    assert _activate(client, first).status_code == 200
    clock[0] += 121
    second = _open(client)
    assert first["pairId"] != second["pairId"]
    clock[0] += 121
    assert _activate(client, second).status_code == 403
    assert _activate(client, second).status_code == 403


def test_invalid_pairs_and_no_lan_admin(client, monkeypatch):
    assert client.post("/local/v1/admin/pair/open", json={"ipv4": "192.168.1.28"}).status_code == 403
    client = _local(client)
    monkeypatch.setattr("pathai_backend.routers.local_pairing._check_local_endpoint", lambda *_args: None)
    pairing = _open(client)
    assert client.post("/local/v1/pair/activate", content=b'{"pairId":"a","pairId":"b"}',
                       headers={"Content-Type": "application/json"}).status_code == 403
    bad = client.post("/local/v1/admin/pair/open", json={"ipv4": "8.8.8.8"})
    assert bad.status_code == 400
    assert client.post("/local/v1/admin/pair/open", json={"ipv4": "192.168.1.28"},
                       headers={"Origin": "http://evil.example"}).status_code == 403
    client.app.state.local_pairing.boot_id = "boot-0123456789abcdef"
    assert _activate(client, pairing).status_code == 200
    address = client.get("/local/v1/admin/address", params={"ipv4": "192.168.1.28"})
    assert address.status_code == 200
    assert address.json()["deviceId"] == pairing["deviceId"]
    assert "pairingKey" not in address.text
    assert client.post("/local/v1/pair/activate", content=b"{" + b"a" * 2048,
                       headers={"Content-Type": "application/json"}).status_code == 400

