# -*- coding: utf-8 -*-
"""讯飞开放平台 WebSocket URL 签名（HMAC-SHA256）。

鉴权规则（语音听写大模型 V2.0 / 超拟人合成「接口鉴权」一致）：
- URL 携带 query 参数 host / date / authorization；
- date 为 RFC1123 GMT（服务端校验与自身时间偏差 ±300s）；
- signature_origin = "host: {host}\\ndate: {date}\\nGET {path} HTTP/1.1"；
- signature = base64(HMAC-SHA256(api_secret, signature_origin))；
- authorization_origin =
    api_key="{key}", algorithm="hmac-sha256",
    headers="host date request-line", signature="{signature}"；
- authorization = base64(authorization_origin)，最终 urlencode 拼入 URL。

密钥只从 Settings 注入（PATHAI_XFYUN_* 环境变量），不落日志、不入仓库。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlencode, urlsplit

from pathai_backend.config import Settings
from pathai_backend.errors import ApiError

#: 语音听写大模型 V2.0 固定接入地址。
IAT_WSS_HOST = "iat.cn-huabei-1.xf-yun.com"
IAT_WSS_PATH = "/v1"

#: 签发 URL 的客户端缓存上限（讯飞校验 date ±300s，留 60s 余量）。
URL_EXPIRES_SECONDS = 240


def sign_wss_url(
    *,
    scheme: str,
    host: str,
    path: str,
    api_key: str,
    api_secret: str,
    now: datetime | None = None,
) -> str:
    """按讯飞规则生成带签名的 wss URL；now 可注入以便测试固定 date。"""
    current = now if now is not None else datetime.now(timezone.utc)
    date = format_datetime(current.astimezone(timezone.utc), usegmt=True)

    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    digest = hmac.new(
        api_secret.encode("utf-8"), signature_origin.encode("utf-8"), hashlib.sha256
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")

    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")

    query = urlencode({"host": host, "date": date, "authorization": authorization})
    return f"{scheme}://{host}{path}?{query}"


def build_iat_url(settings: Settings, now: datetime | None = None) -> str:
    """语音听写（ASR）签名 URL；缺密钥 → 503 XFYUN_NOT_CONFIGURED。"""
    if not (settings.xfyun_appid and settings.xfyun_iat_api_key and settings.xfyun_iat_api_secret):
        raise ApiError(
            503,
            "XFYUN_NOT_CONFIGURED",
            "讯飞语音听写未配置密钥（PATHAI_XFYUN_APPID / PATHAI_XFYUN_IAT_API_KEY / "
            "PATHAI_XFYUN_IAT_API_SECRET）",
            retryable=False,
        )
    return sign_wss_url(
        scheme="wss",
        host=IAT_WSS_HOST,
        path=IAT_WSS_PATH,
        api_key=settings.xfyun_iat_api_key,
        api_secret=settings.xfyun_iat_api_secret,
        now=now,
    )


def build_tts_url(settings: Settings, now: datetime | None = None) -> str:
    """超拟人合成（TTS）签名 URL：host/path 取自控制台地址，签名重新生成。"""
    if not (
        settings.xfyun_appid
        and settings.xfyun_tts_api_key
        and settings.xfyun_tts_api_secret
        and settings.xfyun_tts_url
    ):
        raise ApiError(
            503,
            "XFYUN_NOT_CONFIGURED",
            "讯飞语音合成未配置（PATHAI_XFYUN_APPID / PATHAI_XFYUN_TTS_API_KEY / "
            "PATHAI_XFYUN_TTS_API_SECRET / PATHAI_XFYUN_TTS_URL）",
            retryable=False,
        )
    parts = urlsplit(settings.xfyun_tts_url)
    if parts.scheme not in ("wss", "ws") or not parts.netloc or not parts.path:
        raise ApiError(
            503,
            "XFYUN_NOT_CONFIGURED",
            "PATHAI_XFYUN_TTS_URL 格式不合法（应形如 wss://host/v1/private/xxx）",
            retryable=False,
        )
    # 丢弃配置里可能带的旧 query，签名参数全部重新生成
    return sign_wss_url(
        scheme=parts.scheme,
        host=parts.netloc,
        path=parts.path,
        api_key=settings.xfyun_tts_api_key,
        api_secret=settings.xfyun_tts_api_secret,
        now=now,
    )
