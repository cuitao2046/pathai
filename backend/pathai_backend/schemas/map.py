# -*- coding: utf-8 -*-
"""API-02 地图版本摘要 与 API-03 地点查询契约（docs/24 §8）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, HttpUrl, field_validator

from pathai_backend.schemas.common import StrictModel


class CoordinateSystem(StrictModel):
    unit: Literal["m"] = "m"
    originDescription: str = Field(min_length=1)
    headingReference: str = Field(min_length=1)


class FloorSummary(StrictModel):
    floorId: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    level: int


class MapVersionResponse(StrictModel):
    """API-02 响应：不可变发布版本 + HTTPS 数据下载地址与摘要。"""

    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    publishedAt: datetime
    coordinateSystem: CoordinateSystem
    floors: list[FloorSummary] = Field(min_length=1)
    dataUrl: HttpUrl
    dataSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("dataUrl")
    @classmethod
    def _require_https(cls, v: HttpUrl) -> HttpUrl:
        # 契约规则：dataUrl 只允许 HTTPS
        if v.scheme != "https":
            raise ValueError("dataUrl 只允许 HTTPS")
        return v


class Place(StrictModel):
    """API-03 地点条目：只返回已发布且可导航的地点。"""

    placeId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    spokenName: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    type: str = Field(min_length=1)
    floorId: str = Field(min_length=1)
    nodeId: str = Field(min_length=1)
    accessible: bool


class PlacesResponse(StrictModel):
    mapVersion: str = Field(min_length=1)
    items: list[Place]
    nextCursor: str | None = Field(default=None, min_length=1)
