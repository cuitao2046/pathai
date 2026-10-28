# -*- coding: utf-8 -*-
"""API-06..09 导航会话契约（docs/24 §8 API-06/07/08/09）。

关键不变量（spec REQ-NAV / REQ-SESSION）：
- 路线携带固定 mapVersion 与 routeRulesVersion（首期 "1.0.0"，会话内固定）；
- 公开路线默认不得是 wall_fallback；若是必须 safeForBlind=false 且带风险提示；
- 事件与状态必须一致：relocalizing→recovering、arrived→arrived；
- 终态（arrived/ended）不返回可执行旧指令；ended 必须带 endReason/endedAt。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from pathai_backend.schemas.common import (
    GeoPosition,
    InstructionAction,
    LocationQuality,
    NavigationInstruction,
    NavEvent,
    EndReason,
    PlaceCandidate,
    RouteFallbackType,
    SessionStatus,
    StrictModel,
    TERMINAL_SESSION_STATUSES,
    TRUSTED_QUALITIES,
    UserMode,
)

#: 会话/路线/指令 ID 前缀格式（docs/24 示例：nav_... / route_... / ins_...）。
_SESSION_ID_PATTERN = r"^nav_[A-Za-z0-9]+$"


class RouteInfo(StrictModel):
    """路线及按决策点组织的指令序列；首条指令即 segments[0]。"""

    routeId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    routeRulesVersion: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+$")
    distanceM: float = Field(ge=0)
    estimatedDurationSec: int | None = Field(default=None, ge=0)
    polyline: list[GeoPosition] = Field(min_length=2)
    segments: list[NavigationInstruction] = Field(min_length=1)
    fallbackType: RouteFallbackType = RouteFallbackType.NONE
    safeForBlind: bool = True
    riskNotices: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_fallback_safety(self) -> "RouteInfo":
        if self.fallbackType == RouteFallbackType.WALL_FALLBACK:
            if self.safeForBlind:
                raise ValueError("wall_fallback 路线必须标记 safeForBlind=false")
            if not self.riskNotices:
                raise ValueError("wall_fallback 路线必须给出结构化风险提示")
        return self


class OriginPosition(StrictModel):
    x: float
    y: float
    floorId: str | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0, le=1)
    observedAt: datetime


class CreateSessionRequest(StrictModel):
    """API-06 请求：仅在用户确认目的地后调用，须带 Idempotency-Key。"""

    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    origin: OriginPosition
    destinationId: str = Field(min_length=1)
    # 主用户为视障人士，缺省按最安全的 blind 模式（跨层强制电梯）
    userMode: UserMode = UserMode.BLIND
    locale: str = "zh-CN"


class CreateSessionResponse(StrictModel):
    sessionId: str = Field(pattern=_SESSION_ID_PATTERN)
    status: SessionStatus
    mapVersion: str = Field(min_length=1)
    #: 会话创建时固化的定位参数版本（docs/28 §5.4）
    paramsVersion: str = Field(min_length=1)
    destination: PlaceCandidate
    route: RouteInfo
    createdAt: datetime


class LocationUpdate(StrictModel):
    """导航更新中的位置；不可信质量允许缺坐标，可信质量必须携带。"""

    x: float | None = None
    y: float | None = None
    floorId: str | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0, le=1)
    quality: LocationQuality
    observedAt: datetime

    @model_validator(mode="after")
    def _check_coords_trust(self) -> "LocationUpdate":
        if self.quality in TRUSTED_QUALITIES and (self.x is None or self.y is None):
            raise ValueError("可信质量必须携带坐标")
        return self


class NavUpdateRequest(StrictModel):
    """API-07 请求：每次提交一个已完成位置估计，sequence 单调递增。"""

    sequence: int = Field(ge=1)
    location: LocationUpdate


class NavUpdateResponse(StrictModel):
    sessionId: str = Field(pattern=_SESSION_ID_PATTERN)
    status: SessionStatus
    event: NavEvent
    instruction: NavigationInstruction | None = None
    routeChanged: bool
    processedSequence: int = Field(ge=1)
    route: RouteInfo | None = None

    @model_validator(mode="after")
    def _check_event_consistency(self) -> "NavUpdateResponse":
        if self.instruction is None:
            raise ValueError("导航更新响应必须携带 instruction")
        if self.event == NavEvent.RELOCALIZING and self.status != SessionStatus.RECOVERING:
            raise ValueError("event=relocalizing 要求 status=recovering")
        if self.event == NavEvent.ARRIVED and self.status != SessionStatus.ARRIVED:
            raise ValueError("event=arrived 要求 status=arrived")
        if self.event == NavEvent.REROUTE and (not self.routeChanged or self.route is None):
            raise ValueError("event=reroute 必须 routeChanged=true 且携带新 route")
        return self


class CurrentInstructionSummary(StrictModel):
    """API-08 currentInstruction 的只读摘要（docs/24 示例三字段）。"""

    instructionId: str = Field(min_length=1)
    text: str = Field(min_length=1)
    action: InstructionAction


class SessionQueryResponse(StrictModel):
    sessionId: str = Field(pattern=_SESSION_ID_PATTERN)
    status: SessionStatus
    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    paramsVersion: str = Field(min_length=1)
    destination: PlaceCandidate
    currentInstruction: CurrentInstructionSummary | None = None
    lastProcessedSequence: int | None = Field(default=None, ge=1)
    updatedAt: datetime
    endedAt: datetime | None = None
    endReason: EndReason | None = None

    @model_validator(mode="after")
    def _check_terminal(self) -> "SessionQueryResponse":
        if self.status in TERMINAL_SESSION_STATUSES and self.currentInstruction is not None:
            raise ValueError("终态不得返回可执行旧指令")
        if self.status == SessionStatus.ENDED and (self.endReason is None or self.endedAt is None):
            raise ValueError("ended 状态必须返回 endReason 与 endedAt")
        return self


class EndSessionRequest(StrictModel):
    """API-09 请求；reason=arrived 仅保留枚举兼容，到达判定权在服务端。"""

    reason: EndReason


class EndSessionResponse(StrictModel):
    sessionId: str = Field(pattern=_SESSION_ID_PATTERN)
    status: Literal[SessionStatus.ENDED]
    reason: EndReason
    endedAt: datetime
