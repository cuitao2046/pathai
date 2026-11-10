# -*- coding: utf-8 -*-
"""定位参数运行时热切换（specs/calibration-console §6.7）。

- 生效参数以数据库 `calibration_active_pointer` 为唯一真源；
- 每个 worker 最多缓存 `calibration_params_ttl_s` 秒，apply 后秒级收敛；
- 未 apply 过时回退到环境变量冻结值（`app.state.settings`），保持现有部署语义；
- 读取失败（表缺失/数据库瞬断）不得让定位接口 500，按过期缓存或环境值降级。
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Callable

from pathai_backend.config import Settings
from pathai_backend.database import session_scope
from pathai_backend.models import CalibrationActivePointer, LocationParamVersion

#: 单场地部署（与地图包目录一致）；多站点不在本阶段范围。
DEFAULT_SITE_ID = "school-building-01"


class RuntimeParams:
    """数据库参数覆盖层。

    `base` 可以是 Settings 或返回当前 Settings 的 callable：后者让环境冻结值也能
    随 `app.state.settings` 变化（测试与热改配置场景），缓存只缓存数据库覆盖值，
    不会把旧的 base 一起缓存住。
    """

    def __init__(self, base: Settings | Callable[[], Settings]) -> None:
        self._provider: Callable[[], Settings] = base if callable(base) else (lambda: base)
        initial = self._provider()
        self._db_values: dict[str, object] | None = None
        self._param_version = initial.location_params_version
        self._grid_version: str | None = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()

    @property
    def _base(self) -> Settings:
        return self._provider()

    @property
    def ttl_s(self) -> float:
        return max(0.0, float(self._base.calibration_params_ttl_s))

    def get(self) -> Settings:
        if self.ttl_s > 0 and (time.monotonic() - self._loaded_at) < self.ttl_s:
            return self._materialize()
        with self._lock:
            if self.ttl_s > 0 and (time.monotonic() - self._loaded_at) < self.ttl_s:
                return self._materialize()
            self._reload()
            return self._materialize()

    def _materialize(self) -> Settings:
        base = self._base
        if self._db_values is None:
            return base
        return replace(base, **self._db_values, location_params_version=self._param_version)

    def invalidate(self) -> None:
        with self._lock:
            self._loaded_at = 0.0

    def snapshot(self) -> dict[str, str | None]:
        self.get()
        return {
            "paramVersion": self._param_version,
            "gridVersion": self._grid_version,
            "source": "db" if self._db_values is not None else "env",
        }

    def _reload(self) -> None:
        self._loaded_at = time.monotonic()
        session = None
        try:
            session = session_scope()
            pointer = session.get(CalibrationActivePointer, DEFAULT_SITE_ID)
            if pointer is None:
                self._db_values = None
                self._param_version = self._base.location_params_version
                self._grid_version = None
                return
            row = session.get(LocationParamVersion, pointer.param_version)
            if row is None:
                return
            self._db_values = {
                "location_rssi_at_1m": row.rssi_at_1m,
                "location_path_loss_n": row.path_loss_n,
                "location_residual_reliable_m": row.residual_reliable_m,
                "location_residual_degraded_m": row.residual_degraded_m,
            }
            self._param_version = row.version
            self._grid_version = row.grid_version
        except Exception:
            # 表未建/瞬断：保留上次缓存；从未加载成功则使用环境值
            return
        finally:
            if session is not None:
                session.close()
