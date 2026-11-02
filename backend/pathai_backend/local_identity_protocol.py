from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re

import rfc8785

PROTOCOL = "local-service-discovery"
SERVICE_NAME = "my-backend"
MAX_PACKET_BYTES = 1200
_IDENTIFIER = re.compile(r"^(?:srv|pair|boot)-[0-9a-f]{16,32}$")
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_TAG = re.compile(r"^[A-Za-z0-9_-]{43}$")


def is_identifier(value: object, kind: str) -> bool:
    return isinstance(value, str) and value.startswith(f"{kind}-") and bool(_IDENTIFIER.fullmatch(value))


def is_nonce(value: object) -> bool:
    return isinstance(value, str) and bool(_NONCE.fullmatch(value))


def is_private_ipv4(value: str) -> bool:
    try:
        if value != str(ipaddress.IPv4Address(value)):
            return False
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return any(address in ipaddress.IPv4Network(network) for network in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw: bytes, limit: int = MAX_PACKET_BYTES) -> dict[str, object]:
    if len(raw) > limit:
        raise ValueError("oversized JSON")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def decode_key(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("invalid key encoding")
    try:
        key = base64.urlsafe_b64decode(value + "=")
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("invalid key encoding") from exc
    if len(key) != 32 or base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii") != value:
        raise ValueError("invalid key length")
    return key


def encode_key(key: bytes) -> str:
    if len(key) != 32:
        raise ValueError("invalid key length")
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")


def sign(domain: str, payload: dict[str, object], key: bytes) -> str:
    if len(key) != 32 or domain not in ("OFFER", "PROBE", "ACTIVATE"):
        raise ValueError("invalid signing input")
    data = f"LSD-{domain}-V1\n".encode("ascii") + rfc8785.dumps(payload)
    return base64.urlsafe_b64encode(hmac.new(key, data, hashlib.sha256).digest()).rstrip(b"=").decode("ascii")


def verify(domain: str, message: dict[str, object], key: bytes) -> bool:
    auth = message.get("auth")
    if not isinstance(auth, dict) or set(auth) != {"alg", "tag"} or auth["alg"] != "HS256":
        return False
    tag = auth["tag"]
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        return False
    try:
        expected = sign(domain, {name: value for name, value in message.items() if name != "auth"}, key)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, tag)


def authenticated(domain: str, payload: dict[str, object], key: bytes) -> dict[str, object]:
    return {**payload, "auth": {"alg": "HS256", "tag": sign(domain, payload, key)}}
