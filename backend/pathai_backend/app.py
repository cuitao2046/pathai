# -*- coding: utf-8 -*-
"""FastAPI 应用工厂。"""
from __future__ import annotations

import asyncio
import logging
import platform
import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from pathai_backend.admin import service as admin_service
from pathai_backend.body_limit import BodySizeLimitMiddleware
from pathai_backend.config import LOCAL_ENV, Settings
from pathai_backend.database import dispose_engine, init_engine, session_scope
from pathai_backend.errors import install_error_handlers
from pathai_backend.hazard_layer import HazardRepository
from pathai_backend.local_discovery_v1 import LocalDiscoveryServer
from pathai_backend.local_identity_protocol import SERVICE_NAME
from pathai_backend.local_pairing import LocalPairingService
from pathai_backend.maintenance import PURGE_INTERVAL_S, run_purge
from pathai_backend.map_repository import MapRepository
from pathai_backend.request_context import RequestContextMiddleware
from pathai_backend.runtime_params import RuntimeParams
from pathai_backend.routers import auth as auth_router
from pathai_backend.routers import admin_auth as admin_auth_router
from pathai_backend.routers import admin_maps as admin_maps_router
from pathai_backend.routers import admin_users as admin_users_router
from pathai_backend.routers import beacons as beacons_router
from pathai_backend.routers import calibration as calibration_router
from pathai_backend.routers import config as config_router
from pathai_backend.routers import fingerprint_db as fingerprint_db_router
from pathai_backend.routers import intent as intent_router
from pathai_backend.routers import location as location_router
from pathai_backend.routers import local_pairing as local_pairing_router
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


logger = logging.getLogger(__name__)

#: 业务日志命名空间：uvicorn 只配置自身 logger，不接管这些前缀，INFO 默认被丢弃。
_LOG_NAMESPACES = ("pathai", "pathai_backend", "src.voice_agent")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _configure_logging() -> None:
    """为业务命名空间挂一次 StreamHandler 并置 INFO（重复 create_app 不重复挂）。"""
    for name in _LOG_NAMESPACES:
        target = logging.getLogger(name)
        if not any(getattr(handler, "_pathai_handler", False) for handler in target.handlers):
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(_LOG_FORMAT))
            handler._pathai_handler = True  # type: ignore[attr-defined]
            target.addHandler(handler)
        target.setLevel(logging.INFO)

# 客户端发现与能力查询的稳定身份口径（/healthz 与 /api/v1/device/info 共用）
API_VERSION = "v1"
SERVICE_CAPABILITIES = (
    "indoor-navigation",
    "map-service",
    "fingerprint-location",
    "voice-intent",
)


async def _purge_loop() -> None:
    """周期清理过期行；后台任务异常只记日志，不拖垮服务。"""
    while True:
        await asyncio.sleep(PURGE_INTERVAL_S)
        try:
            await run_in_threadpool(run_purge)
        except Exception:  # noqa: BLE001
            logger.exception("过期行清理任务失败")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    _configure_logging()
    if settings.local_discovery_port and (
        not 1 <= settings.local_discovery_port <= 65535 or
        not 1 <= settings.local_business_port <= 65535
    ):
        raise RuntimeError("局域网发现需配置合法的 UDP/业务端口")
    if settings.local_business_port and not settings.local_discovery_port:
        raise RuntimeError("业务端口不得在关闭发现服务时单独启用")
    init_engine(settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        discovery = None
        purge_task: asyncio.Task | None = None
        try:
            # 管理后台固定账户初始化：数据库表须已由 Alembic/create_all 建立
            admin_service.ensure_admin_account()
            # 设备身份持久化在数据库，同一台机器重启后保持不变；仅用于局域网发现。
            with session_scope() as session:
                app.state.device_id = LocalPairingService.device_id(session)
            if settings.local_discovery_port:
                discovery = LocalDiscoveryServer(
                    app.state.local_pairing, settings.local_business_port, settings.local_discovery_port,
                    unpaired=True,
                )
                discovery.start()
            purge_task = asyncio.create_task(_purge_loop())
            yield
        finally:
            app.state.local_pairing_ready = False
            if purge_task is not None:
                purge_task.cancel()
            if discovery is not None:
                discovery.close()
            dispose_engine()

    app = FastAPI(
        title="PathAI API", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.runtime_params = RuntimeParams(lambda: app.state.settings)
    app.state.local_pairing = LocalPairingService(f"boot-{secrets.token_hex(16)}")
    app.state.local_pairing_ready = False
    # 未进入 lifespan 时为空的设备身份；正常启动会在 lifespan 内补全
    app.state.device_id = ""
    app.state.device_name = socket.gethostname() or "PathAI Gateway"
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
    # 危险层独立加载：文件缺失/损坏/版本不匹配只降级为「无危险层」，不阻塞启动
    app.state.hazard_repository = HazardRepository.load(settings.hazard_data_dir)
    # 顺序：add_middleware 后加的在外层。BodySizeLimit 须在 RequestContext 内层，
    # 拒绝 413 时才能读到 scope.state.request_id 写入错误信封。
    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_body_bytes)
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
    app.include_router(admin_users_router.router)
    app.include_router(beacons_router.router)
    app.include_router(calibration_router.router)
    app.include_router(config_router.router)
    app.include_router(fingerprint_db_router.router)
    app.include_router(map_router.router)
    app.include_router(intent_router.router)
    app.include_router(location_router.router)
    app.include_router(local_pairing_router.router)
    app.include_router(nav_router.router)
    # 讯飞 WebSocket 签名 URL 签发（live 语音 ASR/TTS）
    app.include_router(voice_router.router)

    # 管理后台静态资源：仅在 /admin 前缀下挂载，/api/* 路由在此之前注册，不会被静态资源捕获
    if _ADMIN_DIST.is_dir():
        app.mount("/admin", StaticFiles(directory=_ADMIN_DIST, html=True), name="admin-ui")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """存活检查 + 设备身份：客户端据此排除同网段的其它服务。"""
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "device_id": app.state.device_id,
            "version": app.version,
            "api_version": API_VERSION,
        }

    @app.get("/api/v1/device/info")
    def device_info() -> dict[str, object]:
        """局域网发现后的能力查询；只暴露非敏感元数据，不要求鉴权。"""
        return {
            "service": SERVICE_NAME,
            "device_id": app.state.device_id,
            "device_name": app.state.device_name,
            "os": platform.platform(),
            "version": app.version,
            "api_version": API_VERSION,
            "services": {"http": settings.local_business_port or 8000},
            "capabilities": list(SERVICE_CAPABILITIES),
        }

    return app
