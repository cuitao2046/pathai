# -*- coding: utf-8 -*-
"""应用配置。

规则（specs/phase-1-indoor-navigation/spec.md §4）：
- 本地开发默认 SQLite 文件数据库（backend/.local/pathai.db，不入库）；
- Demo 必须显式配置 PATHAI_DATABASE_URL（托管 PostgreSQL），缺失时启动失败，
  不得静默回退 SQLite；
- 密钥（微信 AppSecret、openid 盐）由环境变量注入，禁止提交仓库或写日志。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LOCAL_ENV = "local"
DEMO_ENV = "demo"

# 本地默认数据库文件位置：backend/.local/pathai.db
_LOCAL_DB_FILE = Path(__file__).resolve().parent.parent / ".local" / "pathai.db"

_SQLITE_SCHEMES = ("sqlite",)
_POSTGRES_SCHEMES = ("postgresql", "postgresql+psycopg")


@dataclass(frozen=True)
class Settings:
    env: str = LOCAL_ENV
    database_url: str = ""
    wechat_appid: str = field(default="", repr=False)
    wechat_secret: str = field(default="", repr=False)
    openid_salt: str = field(default="", repr=False)
    token_ttl_seconds: int = 7200
    # 本地联调假微信客户端开关：仅 env=local 允许开启（code 以 fake-<openid> 形式直换）
    wechat_fake: bool = False
    # 地图包目录：约定 backend/maps_data/<siteId>/<mapVersion>.json
    map_data_dir: Path = Path(__file__).resolve().parent.parent / "maps_data"
    # 对外可访问基地址：拼接 API-02 dataUrl；必须 HTTPS（契约校验强制）
    public_base_url: str = "https://localhost"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Settings":
        env = environ if environ is not None else dict(os.environ)
        map_data_dir = env.get("PATHAI_MAP_DATA_DIR")
        return cls(
            env=env.get("PATHAI_ENV", LOCAL_ENV),
            database_url=env.get("PATHAI_DATABASE_URL", ""),
            wechat_appid=env.get("PATHAI_WECHAT_APPID", ""),
            wechat_secret=env.get("PATHAI_WECHAT_SECRET", ""),
            openid_salt=env.get("PATHAI_OPENID_SALT", ""),
            token_ttl_seconds=int(env.get("PATHAI_TOKEN_TTL", "7200")),
            wechat_fake=env.get("PATHAI_WECHAT_FAKE", "").lower() in ("1", "true", "yes"),
            map_data_dir=Path(map_data_dir) if map_data_dir else cls._default_map_data_dir(),
            public_base_url=env.get("PATHAI_PUBLIC_BASE_URL", "https://localhost"),
        )

    @staticmethod
    def _default_map_data_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "maps_data"

    @property
    def resolved_database_url(self) -> str:
        """按环境规则解析数据库连接串；Demo 缺显式配置时启动失败。"""
        return resolve_database_url(env=self.env, explicit_url=self.database_url)


def resolve_database_url(env: str, explicit_url: str) -> str:
    if explicit_url:
        _validate_scheme(explicit_url, env)
        return explicit_url
    if env == DEMO_ENV:
        raise RuntimeError(
            "PATHAI_ENV=demo 需要显式配置 PATHAI_DATABASE_URL（托管 PostgreSQL），"
            "禁止静默回退 SQLite"
        )
    _LOCAL_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{_LOCAL_DB_FILE.as_posix()}"


def _validate_scheme(url: str, env: str) -> None:
    scheme = url.split(":", 1)[0].lower()
    if scheme in _SQLITE_SCHEMES:
        return  # 本地/测试显式使用 SQLite 是允许的
    if scheme.startswith(_POSTGRES_SCHEMES):
        return
    raise RuntimeError(f"不支持的数据库 URL scheme：{scheme}（env={env}）")
