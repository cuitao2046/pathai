# -*- coding: utf-8 -*-
"""统一错误模型与响应封装。

公共错误结构（docs/24 §7.2）：
{"error": {"code", "message", "retryable", "details"}, "requestId"}
客户端按稳定 code 分支，不依赖 message。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """业务错误：携带稳定错误码与 HTTP 状态。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def error_envelope(request: Request, code: str, message: str, retryable: bool,
                   details: dict[str, Any]) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "retryable": retryable, "details": details},
        "requestId": request.state.request_id,
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(request, exc.code, exc.message, exc.retryable, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_envelope(request, "INVALID_REQUEST", "请求格式或字段不合法", False, {}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(request, code, str(exc.detail), False, {}),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 不向客户端泄露内部细节；服务端记录结构化日志（脱敏在日志层保证）
        return JSONResponse(
            status_code=500,
            content=error_envelope(request, "INTERNAL_ERROR", "服务内部错误", True, {}),
        )
