# -*- coding: utf-8 -*-
"""API-01 微信登录换取访问令牌契约（docs/24 §8 API-01）。

匿名主体规则：userId = anon_<sha256(openid+salt) 前 16 位 hex>；
令牌为 opaque 随机值（非自包含 JWT），默认有效期 7200 秒。
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from pathai_backend.schemas.common import StrictModel


class WechatLoginRequest(StrictModel):
    code: str = Field(min_length=1, max_length=256)


class AuthResponse(StrictModel):
    accessToken: str = Field(min_length=1)
    tokenType: Literal["Bearer"] = "Bearer"
    expiresIn: int = Field(default=7200, gt=0)
    userId: str = Field(pattern=r"^anon_[0-9a-f]{16}$")
