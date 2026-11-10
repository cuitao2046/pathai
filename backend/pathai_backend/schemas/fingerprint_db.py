# -*- coding: utf-8 -*-
"""指纹库请求契约（无鉴权；沿用私网 + 限流）。"""
from __future__ import annotations

from pydantic import Field

from pathai_backend.schemas.common import StrictModel

#: 与位置估计一致的 RSSI 工程范围。
RSSI_MIN_DBM = -100
RSSI_MAX_DBM = -20


class FingerprintIngestRequest(StrictModel):
    fileName: str = Field(min_length=1, max_length=160)
    #: 导出文件的原始 JSON 文本（服务端负责 schema/语义校验）
    content: str = Field(min_length=2)
    operator: str | None = Field(default=None, max_length=64)


class FingerprintRollbackRequest(StrictModel):
    """回滚到保留窗口内的既有版本；已裁剪明细的版本返回 VERSION_EXPIRED。"""

    version: str = Field(min_length=1, max_length=40)
    operator: str | None = Field(default=None, max_length=64)


class FingerprintQueryBeacon(StrictModel):
    beaconId: str = Field(min_length=1, max_length=64)
    rssi: int = Field(ge=RSSI_MIN_DBM, le=RSSI_MAX_DBM)


class FingerprintQueryRequest(StrictModel):
    siteId: str = Field(min_length=1, max_length=64)
    mapVersion: str = Field(default="latest", min_length=1, max_length=40)
    floorHint: str | None = Field(default=None, min_length=1, max_length=16)
    beacons: list[FingerprintQueryBeacon] = Field(min_length=1, max_length=200)


class FingerprintMatchResponse(StrictModel):
    matched: bool
    reason: str | None = None
    libraryVersion: str | None = None
    fpId: str | None = None
    floorId: str | None = None
    position: dict | None = None
    confidence: float | None = None
    rmseDb: float | None = None
    overlap: int | None = None
