# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pathai_backend.admin import service as admin_service
from pathai_backend.config import LOCAL_ENV, Settings
from pathai_backend.database import dispose_engine, init_engine
from pathai_backend.errors import install_error_handlers
from pathai_backend.map_repository import MapRepository
from pathai_backend.request_context import RequestContextMiddleware
from pathai_backend.routers import auth as auth_router
from pathai_backend.routers import admin_auth as admin_auth_router
from pathai_backend.routers import admin_maps as admin_maps_router
from pathai_backend.routers import intent as intent_router
from pathai_backend.routers import location as location_router
from pathai_backend.routers import map as map_router
from pathai_backend.routers import nav as nav_router
from pathai_backend.routers import voice as voice_router
from pathai_backend.wechat import FakeWechatAuthClient, HttpWechatAuthClient

# 管理后台前端同源托管：构建产物不存在时跳过挂载，不影响纯 API 部署与测试
_ADMIN_DIST = Path(__file__).resolve().parents[2] / "admin-web" / "dist"
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    init_engine(settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            # 管理后台固定账户初始化：数据库表须已由 Alembic/create_all 建立
            admin_service.ensure_admin_account()
            yield
        finally:
            dispose_engine()

    app = FastAPI(
        title="PathAI API", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    if settings.wechat_fake:
        if settings.env != LOCAL_ENV:
            raise RuntimeError("PATHAI_WECHAT_FAKE 仅允许在 PATHAI_ENV=local 下开启")
        app.state.wechat_client = FakeWechatAuthClient()
    else:
        app.state.wechat_client = HttpWechatAuthClient(
            appid=settings.wechat_appid, secret=settings.wechat_secret
        )
    app.state.map_repository = MapRepository.load(
        map_data_dir=settings.map_data_dir, public_base_url=settings.public_base_url
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    app.include_router(auth_router.router)
    app.include_router(admin_auth_router.router)
    app.include_router(admin_maps_router.router)
    app.include_router(map_router.router)
    app.include_router(intent_router.router)
    app.include_router(location_router.router)
    app.include_router(nav_router.router)
    # 讯飞 WebSocket 签名 URL 签发（live 语音 ASR/TTS）
    app.include_router(voice_router.router)

    # 管理后台静态资源：仅在 /admin 前缀下挂载，/api/* 路由在此之前注册，不会被静态资源捕获
    if _ADMIN_DIST.is_dir():
        app.mount("/admin", StaticFiles(directory=_ADMIN_DIST, html=True), name="admin-ui")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
