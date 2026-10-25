# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from pathai_backend.config import LOCAL_ENV, Settings
from pathai_backend.database import dispose_engine, init_engine
from pathai_backend.errors import install_error_handlers
from pathai_backend.map_repository import MapRepository
from pathai_backend.request_context import RequestContextMiddleware
from pathai_backend.routers import auth as auth_router
from pathai_backend.routers import intent as intent_router
from pathai_backend.routers import location as location_router
from pathai_backend.routers import map as map_router
from pathai_backend.routers import nav as nav_router
from pathai_backend.wechat import FakeWechatAuthClient, HttpWechatAuthClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    init_engine(settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
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
    app.include_router(auth_router.router)
    app.include_router(map_router.router)
    app.include_router(intent_router.router)
    app.include_router(location_router.router)
    app.include_router(nav_router.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
