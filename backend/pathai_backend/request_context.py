# -*- coding: utf-8 -*-
"""请求上下文中间件：为每个请求生成 requestId 并回写到响应头。

spec §8：每个响应含 requestId；日志使用脱敏结构化字段。
"""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = generate_request_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response
