# -*- coding: utf-8 -*-
"""讯飞 WebSocket 签名 URL 签发测试（routers/voice.py + xfyun.py）。"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from pathai_backend import xfyun
from pathai_backend.errors import ApiError

FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
FIXED_DATE = "Fri, 02 Jan 2026 03:04:05 GMT"

IAT_HOST = "iat.cn-huabei-1.xf-yun.com"
TTS_URL = "wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/demo123"

XFYUN_KEYS = {
    "xfyun_appid": "appid-demo",
    "xfyun_iat_api_key": "iat-key-demo",
    "xfyun_iat_api_secret": "iat-secret-demo",
    "xfyun_tts_api_key": "tts-key-demo",
    "xfyun_tts_api_secret": "tts-secret-demo",
    "xfyun_tts_url": TTS_URL,
}


def _manual_authorization(host: str, path: str, date: str, key: str, secret: str) -> str:
    """按讯飞文档公式独立重算 authorization（与被测实现互为对照）。"""
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), signature_origin.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    authorization_origin = (
        f'api_key="{key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    return base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture()
def keyed_settings(app):
    """给运行中的 app 注入讯飞密钥（Settings 为 frozen dataclass，整体替换）。"""
    app.state.settings = dataclasses.replace(app.state.settings, **XFYUN_KEYS)
    return app.state.settings


# ---------------------------------------------------------------------------
# 签名工具（固定 now，不依赖真实时钟）
# ---------------------------------------------------------------------------


def test_sign_wss_url_matches_document_formula():
    url = xfyun.sign_wss_url(
        scheme="wss",
        host=IAT_HOST,
        path="/v1",
        api_key="key-abc",
        api_secret="secret-xyz",
        now=FIXED_NOW,
    )
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc, parts.path) == ("wss", IAT_HOST, "/v1")
    query = _query(url)
    assert set(query) == {"host", "date", "authorization"}
    assert query["host"] == IAT_HOST
    assert query["date"] == FIXED_DATE  # RFC1123 + GMT
    assert query["authorization"] == _manual_authorization(
        IAT_HOST, "/v1", FIXED_DATE, "key-abc", "secret-xyz"
    )


def test_iat_url_uses_fixed_host_and_path(settings):
    url = xfyun.build_iat_url(dataclasses.replace(settings, **XFYUN_KEYS), now=FIXED_NOW)
    parts = urlsplit(url)
    assert parts.scheme == "wss"
    assert parts.netloc == IAT_HOST
    assert parts.path == "/v1"
    query = _query(url)
    assert query["authorization"] == _manual_authorization(
        IAT_HOST, "/v1", FIXED_DATE, XFYUN_KEYS["xfyun_iat_api_key"], XFYUN_KEYS["xfyun_iat_api_secret"]
    )


def test_tts_url_signs_configured_path(settings):
    url = xfyun.build_tts_url(dataclasses.replace(settings, **XFYUN_KEYS), now=FIXED_NOW)
    parts = urlsplit(url)
    assert parts.netloc == "cbm01.cn-huabei-1.xf-yun.com"
    assert parts.path == "/v1/private/demo123"
    query = _query(url)
    assert query["authorization"] == _manual_authorization(
        parts.netloc, parts.path, FIXED_DATE,
        XFYUN_KEYS["xfyun_tts_api_key"], XFYUN_KEYS["xfyun_tts_api_secret"],
    )


def test_build_iat_url_missing_keys_raises_not_configured(settings):
    with pytest.raises(ApiError) as exc:
        xfyun.build_iat_url(settings)  # 默认 settings 无讯飞密钥
    assert exc.value.status_code == 503
    assert exc.value.code == "XFYUN_NOT_CONFIGURED"
    assert exc.value.retryable is False


def test_build_tts_url_malformed_config_raises_not_configured(settings):
    bad = dataclasses.replace(settings, **{**XFYUN_KEYS, "xfyun_tts_url": "https://example.com/v1"})
    with pytest.raises(ApiError) as exc:
        xfyun.build_tts_url(bad)
    assert exc.value.status_code == 503
    assert exc.value.code == "XFYUN_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------


def test_voice_endpoints_require_auth(client):
    for path in ("/api/v1/voice/asr-url", "/api/v1/voice/tts-url"):
        resp = client.get(path)
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"]["code"] in ("UNAUTHENTICATED", "TOKEN_EXPIRED")
        assert body["requestId"]


def test_asr_url_ok(client, app, auth_headers, keyed_settings):
    resp = client.get("/api/v1/voice/asr-url", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["expiresIn"] == xfyun.URL_EXPIRES_SECONDS
    query = _query(body["url"])
    assert urlsplit(body["url"]).netloc == IAT_HOST
    # 用响应里的 date 独立重算签名（不依赖签发时钟）
    assert query["authorization"] == _manual_authorization(
        IAT_HOST, "/v1", query["date"],
        XFYUN_KEYS["xfyun_iat_api_key"], XFYUN_KEYS["xfyun_iat_api_secret"],
    )


def test_tts_url_ok(client, app, auth_headers, keyed_settings):
    resp = client.get("/api/v1/voice/tts-url", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["expiresIn"] == xfyun.URL_EXPIRES_SECONDS
    parts = urlsplit(body["url"])
    assert parts.path == "/v1/private/demo123"
    query = _query(body["url"])
    assert query["authorization"] == _manual_authorization(
        parts.netloc, parts.path, query["date"],
        XFYUN_KEYS["xfyun_tts_api_key"], XFYUN_KEYS["xfyun_tts_api_secret"],
    )


def test_asr_url_missing_keys_returns_503_envelope(client, auth_headers):
    resp = client.get("/api/v1/voice/asr-url", headers=auth_headers)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "XFYUN_NOT_CONFIGURED"
    assert body["error"]["retryable"] is False
    assert body["requestId"]
