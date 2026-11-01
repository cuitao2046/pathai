import json
import socket
import threading
import urllib.request

import pytest

from pathai_backend.local_discovery_probe import (
    MAX_PACKET_BYTES,
    PROTOCOL,
    SERVICE_NAME,
    parse_discover,
    private_ipv4,
    serve_probe,
)


def test_valid_p0_discover():
    request_id = "0123456789abcdef0123456789abcdef"
    payload = json.dumps(
        {
            "protocol": PROTOCOL,
            "version": 1,
            "type": "DISCOVER",
            "serviceName": SERVICE_NAME,
            "requestId": request_id,
        }
    ).encode("utf-8")
    assert parse_discover(payload) == request_id


def test_invalid_discover_is_ignored():
    request = {
        "protocol": PROTOCOL,
        "version": 1,
        "type": "DISCOVER",
        "serviceName": SERVICE_NAME,
        "requestId": "0123456789abcdef0123456789abcdef",
    }
    for field, value in (("version", True), ("type", "OFFER"), ("requestId", "a" * 32 + "b")):
        assert parse_discover(json.dumps({**request, field: value}).encode("utf-8")) is None
    assert parse_discover(b"{" * (MAX_PACKET_BYTES + 1)) is None
    assert parse_discover(b"\xff") is None
    assert parse_discover(b'{"requestId":"a","requestId":"b"}') is None


def test_only_rfc1918_ipv4_sources():
    for address in ("10.1.2.3", "172.16.1.2", "172.31.2.3", "192.168.1.2"):
        assert private_ipv4(address)
    for address in ("127.0.0.1", "172.32.0.1", "8.8.8.8", "255.255.255.255", "::1"):
        assert not private_ipv4(address)


def test_probe_server_stops_cleanly():
    stop = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp, socket.socket(
        socket.AF_INET, socket.SOCK_STREAM
    ) as http:
        udp.bind(("127.0.0.1", 0))
        http.bind(("127.0.0.1", 0))
        udp_port = udp.getsockname()[1]
        http_port = http.getsockname()[1]
    server = threading.Thread(target=serve_probe, args=(udp_port, http_port, stop), daemon=True)
    server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/local/p0/probe", timeout=2) as response:
            assert response.status == 200
            assert json.load(response) == {"protocol": PROTOCOL, "ready": True}
    finally:
        stop.set()
        server.join(timeout=3)
    assert not server.is_alive()


def test_probe_rejects_invalid_ports():
    with pytest.raises(ValueError):
        serve_probe(0, 39002)
    with pytest.raises(ValueError):
        serve_probe(39001, 65536)
