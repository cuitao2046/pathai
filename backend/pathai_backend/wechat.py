# -*- coding: utf-8 -*-
"""微信 code2Session 客户端抽象。

生产实现走微信 HTTPS 接口（同步，超时受控）；测试注入 fake 替身。
错误二分（docs/24 §8 API-01）：
- code 无效/已使用 → 401 AUTH_CODE_INVALID（不可重试）；
- 微信侧故障/网络异常/响应异常 → 5xx AUTH_PROVIDER_UNAVAILABLE（可重试）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib import request as _urlrequest
from urllib.error import HTTPError, URLError

_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
_TIMEOUT_SECONDS = 5.0

# 微信侧判定 code 本身有误的错误码（其余错误码一律视为提供方故障）
_INVALID_CODE_ERRCODES = {40029, 40163, 45011}


class WechatAuthError(Exception):
    """code2Session 失败。reason ∈ {"invalid_code", "provider_error"}。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason


class WechatAuthClient(Protocol):
    """换取 openid 的最小接口；测试提供内存替身。"""

    def code_to_session(self, code: str) -> str:
        """成功返回 openid；失败抛 WechatAuthError。"""
        ...  # pragma: no cover


@dataclass(frozen=True)
class HttpWechatAuthClient:
    """code2Session 的 HTTP 实现（第一阶段同步阻塞即可）。"""

    appid: str
    secret: str

    def code_to_session(self, code: str) -> str:
        url = (
            f"{_CODE2SESSION_URL}?appid={self.appid}&secret={self.secret}"
            f"&js_code={code}&grant_type=authorization_code"
        )
        try:
            with _urlrequest.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            raise WechatAuthError("provider_error", "code2session 请求失败") from None
        openid = payload.get("openid")
        errcode = payload.get("errcode", 0)
        if not errcode and isinstance(openid, str) and openid:
            return openid
        if errcode in _INVALID_CODE_ERRCODES:
            raise WechatAuthError("invalid_code", f"微信返回 errcode={errcode}")
        raise WechatAuthError("provider_error", f"code2session 异常响应 errcode={errcode}")
