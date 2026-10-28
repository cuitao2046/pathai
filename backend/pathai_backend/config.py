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
    # 讯飞开放平台（live 语音 ASR/TTS）：appid 两服务共用，key/secret 按服务分列
    xfyun_appid: str = field(default="", repr=False)
    xfyun_iat_api_key: str = field(default="", repr=False)
    xfyun_iat_api_secret: str = field(default="", repr=False)
    xfyun_tts_api_key: str = field(default="", repr=False)
    xfyun_tts_api_secret: str = field(default="", repr=False)
    #: 超拟人合成控制台接入地址，形如 wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/xxx
    xfyun_tts_url: str = ""
    # LLM（OpenAI 兼容）：语音 Agent 意图解析；缺任一字段即视为未启用并降级规则解析
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    #: 实测火山方舟首 token 1.5~5.1s，8s 会误判超时；15s 为工程默认值
    llm_timeout_s: float = 15.0
    #: 结构化输出模式：json_object（DeepSeek/Qwen 通用）或 json_schema（OpenAI 系）
    llm_structured_mode: str = "json_object"
    token_ttl_seconds: int = 7200
    # 本地联调假微信客户端开关：仅 env=local 允许开启（code 以 fake-<openid> 形式直换）
    wechat_fake: bool = False
    # 地图包目录：约定 backend/maps_data/<siteId>/<mapVersion>.json
    map_data_dir: Path = Path(__file__).resolve().parent.parent / "maps_data"
    # 对外可访问基地址：拼接 API-02 dataUrl；必须 HTTPS（契约校验强制）
    public_base_url: str = "https://localhost"
    # 定位解算参数（docs/28 §5.4）：默认值均为工程占位，现场标定后冻结。
    # rssi_at_1m 是 1 m 处 measured power，不是信标广播档位 txPower。
    location_rssi_at_1m: float = -71.0
    location_path_loss_n: float = 2.0
    location_residual_reliable_m: float = 1.5
    location_residual_degraded_m: float = 4.0
    #: 参数版本号（写入 API-05 响应与导航会话，便于跨版本追溯）
    location_params_version: str = "unset"

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
            xfyun_appid=env.get("PATHAI_XFYUN_APPID", ""),
            xfyun_iat_api_key=env.get("PATHAI_XFYUN_IAT_API_KEY", ""),
            xfyun_iat_api_secret=env.get("PATHAI_XFYUN_IAT_API_SECRET", ""),
            xfyun_tts_api_key=env.get("PATHAI_XFYUN_TTS_API_KEY", ""),
            xfyun_tts_api_secret=env.get("PATHAI_XFYUN_TTS_API_SECRET", ""),
            xfyun_tts_url=env.get("PATHAI_XFYUN_TTS_URL", ""),
            llm_base_url=env.get("PATHAI_LLM_BASE_URL", ""),
            llm_api_key=env.get("PATHAI_LLM_API_KEY", ""),
            llm_model=env.get("PATHAI_LLM_MODEL", ""),
            llm_timeout_s=float(env.get("PATHAI_LLM_TIMEOUT_S", "15")),
            llm_structured_mode=env.get("PATHAI_LLM_STRUCTURED_MODE", "json_object"),
            token_ttl_seconds=int(env.get("PATHAI_TOKEN_TTL", "7200")),
            wechat_fake=env.get("PATHAI_WECHAT_FAKE", "").lower() in ("1", "true", "yes"),
            map_data_dir=Path(map_data_dir) if map_data_dir else cls._default_map_data_dir(),
            public_base_url=env.get("PATHAI_PUBLIC_BASE_URL", "https://localhost"),
            location_rssi_at_1m=float(env.get("PATHAI_LOCATION_RSSI_AT_1M", "-71")),
            location_path_loss_n=float(env.get("PATHAI_LOCATION_PATH_LOSS_N", "2.0")),
            location_residual_reliable_m=float(
                env.get("PATHAI_LOCATION_RESIDUAL_RELIABLE_M", "1.5")
            ),
            location_residual_degraded_m=float(
                env.get("PATHAI_LOCATION_RESIDUAL_DEGRADED_M", "4.0")
            ),
            location_params_version=env.get("PATHAI_LOCATION_PARAMS_VERSION", "unset"),
        )

    @staticmethod
    def _default_map_data_dir() -> Path:
        return Path(__file__).resolve().parent.parent / "maps_data"

    @property
    def resolved_database_url(self) -> str:
        """按环境规则解析数据库连接串；Demo 缺显式配置时启动失败。"""
        return resolve_database_url(env=self.env, explicit_url=self.database_url)

    @property
    def llm_configured(self) -> bool:
        """LLM 三要素齐备才启用；缺一即降级为规则解析（不阻启动）。"""
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


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
