# -*- coding: utf-8 -*-
"""信标配置台账与真实部署台账的请求/响应模型（docs/27 §5.2 / §6.8）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BeaconConfigItem(BaseModel):
    """单枚信标的可写配置（来源：已发布地图包 beacons）。"""

    beaconId: str
    deviceName: str
    floorId: str
    coordinates: list[float]
    uuid: str | None = None
    major: int | None = None
    minor: int | None = None
    txPower: float | None = None
    broadcastInterval: int | None = None
    locationDesc: str | None = None
    mountType: str | None = None
    semanticTag: str | None = None


class BeaconsResponse(BaseModel):
    siteId: str
    mapVersion: str
    items: list[BeaconConfigItem]


class WrittenValues(BaseModel):
    uuid: str = Field(min_length=1, max_length=64)
    major: int = Field(ge=0, le=65535)
    minor: int = Field(ge=0, le=65535)
    name: str = Field(min_length=1, max_length=16)
    txPower: float
    interval: int = Field(ge=0, le=65535)


class ObservedValues(BaseModel):
    uuid: str | None = None
    major: int | None = None
    minor: int | None = None
    name: str | None = None
    txPower: float | None = None
    interval: int | None = None


class VerifyResult(BaseModel):
    passed: bool
    observed: ObservedValues | None = None
    mode: str | None = None
    platform: str = "windows"
    at: str | None = None


class DeploymentRecordRequest(BaseModel):
    """单台写入/校验结果；source=intake 时全部等于规划，field_edit 仅允许功率/间隔差异。"""

    mapVersion: str | None = None
    deviceMac: str | None = None
    deviceName: str | None = None
    nameWrite: str | None = None
    written: WrittenValues
    verify: VerifyResult
    operator: str | None = None
    configuredAt: str | None = None
    source: Literal["intake", "field_edit"] = "intake"


class DeploymentRecordView(BaseModel):
    siteId: str
    beaconId: str
    mapVersion: str
    deviceMac: str | None = None
    deviceName: str | None = None
    nameWrite: str | None = None
    written: WrittenValues
    verify: VerifyResult
    source: str
    operator: str | None = None
    configuredAt: str | None = None
    updatedAt: str


class ProvisioningSummary(BaseModel):
    siteId: str
    mapVersion: str
    total: int
    recorded: int
    verified: int
    missing: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)


class DeploymentRecordsResponse(BaseModel):
    summary: ProvisioningSummary
    items: list[DeploymentRecordView]


class CommitRequest(BaseModel):
    mapVersion: str | None = None
    operator: str | None = None


class CommitResponse(ProvisioningSummary):
    status: str
    roundId: int
