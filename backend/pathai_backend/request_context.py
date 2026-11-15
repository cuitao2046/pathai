# -*- coding: utf-8 -*-
"""请求上下文中间件：为每个请求生成 requestId 并回写到响应头。

spec §8：每个响应含 requestId；日志使用脱敏结构化字段。
"""
from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("pathai.access")

#: 高频探活路径不记访问日志，避免淹没有效流量。
_ACCESS_LOG_SKIP_PATHS = {"/healthz"}


def generate_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = generate_request_id()
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        if request.url.path not in _ACCESS_LOG_SKIP_PATHS:
            logger.info(
                "%s %s -> %s %.1fms",
                request.method, request.url.path, response.status_code, duration_ms,
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )
        response.headers["X-Request-Id"] = request.state.request_id
        return response
