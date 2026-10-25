# -*- coding: utf-8 -*-
"""API-05 位置估计契约（docs/24 §8 API-05）。

第一阶段仅使用 BLE 快照做三点定位解算；IMU 字段为接口预留，不参与解算。
RSSI 接受范围 -100..-20 dBm 为工程默认值，现场标定后冻结。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from pathai_backend.schemas.common import (
    GeoPosition,
    LocationQuality,
    StrictModel,
    TRUSTED_QUALITIES,
)

#: 工程初始默认值（spec REQ-LOCATION），非现场标定结果。
RSSI_MIN_DBM = -100
RSSI_MAX_DBM = -20
#: 单批信标观测上限（契约冻结时的工程默认，防单请求过大）。
MAX_BEACONS_PER_BATCH = 200


class BeaconObservation(StrictModel):
    beaconId: str = Field(min_length=1, max_length=64)
    rssi: int = Field(ge=RSSI_MIN_DBM, le=RSSI_MAX_DBM)
    observedAt: datetime


class ImuSample(StrictModel):
    timestampMs: int = Field(ge=0)
    gyroRadS: list[float] = Field(min_length=3, max_length=3)
    accelMS2: list[float] = Field(min_length=3, max_length=3)


class ImuPayload(StrictModel):
    samples: list[ImuSample] = Field(max_length=500)
    headingDeg: float | None = None  # 设备原始朝向，不做范围约束


class LocationEstimateRequest(StrictModel):
    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    collectedAt: datetime
    floorHint: str | None = Field(default=None, min_length=1)
    beacons: list[BeaconObservation] = Field(
        min_length=1, max_length=MAX_BEACONS_PER_BATCH
    )
    imu: ImuPayload | None = None


class MatchedArea(StrictModel):
    placeId: str = Field(min_length=1)
    name: str = Field(min_length=1)


class PositionEstimateResponse(StrictModel):
    estimateId: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    mapVersion: str = Field(min_length=1)
    position: GeoPosition | None = None
    headingDeg: float | None = Field(default=None, ge=0, lt=360)
    confidence: float = Field(ge=0, le=1)
    quality: LocationQuality
    matchedArea: MatchedArea | None = None
    algorithmVersion: str = Field(min_length=1)
    observedAt: datetime
    ignoredBeaconIds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_position_trust(self) -> "PositionEstimateResponse":
        if self.quality in TRUSTED_QUALITIES and self.position is None:
            raise ValueError("quality=reliable/degraded 时必须返回 position")
        return self
