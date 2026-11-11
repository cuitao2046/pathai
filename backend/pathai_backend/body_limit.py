# -*- coding: utf-8 -*-
"""请求体大小限制中间件。

客户端直连部署（无反向代理 client_max_body_size 兜底），服务端必须自行
拒绝超大请求体，防止未认证攻击者向公开端点发送巨型 JSON 耗尽内存。

实现：Starlette/FastAPI 会把整个 body 读入内存后才做 Pydantic 校验，
因此在应用层前置读取 body（上限+1 字节）判超，超限直接以统一错误信封
返回 413 PAYLOAD_TOO_LARGE，不再进入路由；未超限则回放已缓冲的 body，
语义与透传一致。
"""
from __future__ import annotations

import json

from starlette.datastructures import Headers

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _envelope_bytes(code: str, message: str, request_id: str, details: dict) -> bytes:
    return json.dumps(
        {
            "error": {"code": code, "message": message, "retryable": False, "details": details},
            "requestId": request_id,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class BodySizeLimitMiddleware:
    """限制单请求 body 字节数；超限返回统一 413 错误信封。"""

    def __init__(self, app, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        # 快路径：带 Content-Length 的请求超限即拒，不读任何字节
        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self.max_body_bytes:
                await self._reject(scope, send)
                return

        # 慢路径（chunked / 无 Content-Length）：最多缓冲 max+1 字节
        buffered: list[bytes] = []
        received = 0
        exceeded = False
        while True:
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received += len(chunk)
                if received > self.max_body_bytes:
                    exceeded = True
                    break
                buffered.append(chunk)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                return

        if exceeded:
            await self._reject(scope, send)
            return

        body = b"".join(buffered)
        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    async def _reject(self, scope, send) -> None:
        request_id = (scope.get("state") or {}).get("request_id", "")
        payload = _envelope_bytes(
            "PAYLOAD_TOO_LARGE",
            f"请求体超过大小限制（上限 {self.max_body_bytes} 字节）",
            request_id,
            {"maxBodyBytes": self.max_body_bytes},
        )
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("ascii")),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("ascii")))
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
