from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time

from pathai_backend.database import session_scope
from pathai_backend.local_identity_protocol import (
    MAX_PACKET_BYTES, PROTOCOL, SERVICE_NAME, authenticated, is_identifier, is_nonce,
    is_private_ipv4, parse_json,
)


def parse_discover(raw: bytes) -> dict[str, object] | None:
    try:
        value = parse_json(raw)
    except (UnicodeError, ValueError):
        return None
    if set(value) != {"protocol", "version", "type", "requestId", "serviceName", "deviceId", "pairId"}:
        return None
    if (value["protocol"] != PROTOCOL or type(value["version"]) is not int or value["version"] != 1
            or value["type"] != "DISCOVER" or value["serviceName"] != SERVICE_NAME
            or not is_nonce(value["requestId"]) or not is_identifier(value["deviceId"], "srv")
            or not is_identifier(value["pairId"], "pair")):
        return None
    return value


def offer_for_query(pairing, query: dict[str, object], port: int) -> bytes | None:
    with session_scope() as session:
        key = pairing.active_key(session, query["pairId"])
        if key is None or query["deviceId"] != pairing.device_id(session):
            return None
    message = authenticated("OFFER", {
        "protocol": PROTOCOL, "version": 1, "type": "OFFER", "requestId": query["requestId"],
        "serviceName": SERVICE_NAME, "deviceId": query["deviceId"], "pairId": query["pairId"],
        "bootId": pairing.boot_id, "port": port, "transport": "http", "apiVersion": 1,
        "ttlSec": 30,
    }, key)
    result = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return result if len(result) <= MAX_PACKET_BYTES else None


def parse_unpaired_discover(raw: bytes) -> dict[str, object] | None:
    try:
        value = parse_json(raw)
    except (UnicodeError, ValueError):
        return None
    if set(value) != {"protocol", "version", "type", "requestId", "serviceName"}:
        return None
    if (value["protocol"] != PROTOCOL or type(value["version"]) is not int or value["version"] != 1
            or value["type"] != "BUSINESS_DISCOVER" or value["serviceName"] != SERVICE_NAME
            or not is_nonce(value["requestId"])):
        return None
    return value


def unpaired_offer(query: dict[str, object], port: int) -> bytes:
    payload = {
        "protocol": PROTOCOL, "version": 1, "type": "BUSINESS_OFFER",
        "requestId": query["requestId"], "serviceName": SERVICE_NAME,
        "port": port, "authenticated": False,
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def source_is_local_subnet(source: str) -> bool:
    try:
        target = ipaddress.IPv4Address(source)
        host = socket.gethostbyname_ex(socket.gethostname())[2]
        return any(target in ipaddress.IPv4Network(f"{address}/24", strict=False)
                   for address in host if is_private_ipv4(address))
    except (OSError, ValueError):
        return False


class LocalDiscoveryServer:
    def __init__(self, pairing, port: int, udp_port: int = 39001, unpaired: bool = False):
        self.pairing = pairing
        self.port = port
        self.udp_port = udp_port
        self.unpaired = unpaired
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.sock: socket.socket | None = None

    def start(self) -> None:
        if self.thread is not None:
            raise RuntimeError("discovery already started")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", self.udp_port))
            sock.settimeout(0.2)
        except BaseException:
            sock.close()
            raise
        self.sock = sock
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.sock is not None:
            self.sock.close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _serve(self) -> None:
        assert self.sock is not None
        responses: dict[tuple[str, int, str], float] = {}
        per_source: dict[str, list[float]] = {}
        global_times: list[float] = []
        while not self.stop_event.is_set():
            try:
                raw, source = self.sock.recvfrom(MAX_PACKET_BYTES + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if not is_private_ipv4(source[0]) or not source_is_local_subnet(source[0]):
                continue
            query = parse_unpaired_discover(raw) if self.unpaired else parse_discover(raw)
            if query is None:
                continue
            now = time.monotonic()
            responses = {key: when for key, when in responses.items() if now - when < 3}
            global_times = [when for when in global_times if now - when < 1]
            source_times = [when for when in per_source.get(source[0], []) if now - when < 1]
            per_source = {key: [when for when in values if now - when < 1]
                          for key, values in per_source.items() if any(now - when < 1 for when in values)}
            key = (source[0], source[1], query["requestId"])
            if key in responses or len(responses) >= 128 or len(global_times) >= 50 or len(source_times) >= 10:
                continue
            try:
                payload = unpaired_offer(query, self.port) if self.unpaired else offer_for_query(self.pairing, query, self.port)
                if payload is None:
                    continue
                self.sock.sendto(payload, source)
            except (OSError, RuntimeError):
                continue
            responses[key] = now
            global_times.append(now)
            per_source[source[0]] = source_times + [now]
