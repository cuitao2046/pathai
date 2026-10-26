# -*- coding: utf-8 -*-
"""讯飞 WebSocket 签名 URL 签发响应契约。"""
from __future__ import annotations

from pydantic import Field

from pathai_backend.schemas.common import StrictModel


class VoiceUrlResponse(StrictModel):
    """签发的 wss URL 与客户端缓存上限（秒）。

    expiresIn 对应讯飞 date ±300s 校验窗口减去 60s 余量；
    客户端缓存不得超过该值，过期须重新签发。
    """

    url: str = Field(min_length=1)
    expiresIn: int = Field(gt=0)
