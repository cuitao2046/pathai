# -*- coding: utf-8 -*-
"""冻结的 API 契约模型（docs/24 §8 API-01..09）。

各 API 实现的请求/响应模型一律从这里导入，禁止在路由层各自定义重复结构。
"""
from __future__ import annotations

from pathai_backend.schemas.auth import AuthResponse, WechatLoginRequest
from pathai_backend.schemas.common import (
    EndReason,
    GeoPosition,
    InstructionAction,
    InstructionPriority,
    IntentType,
    LandmarkRef,
    LocationQuality,
    NavEvent,
    NavigationInstruction,
    PlaceCandidate,
    RouteFallbackType,
    SessionStatus,
    StrictModel,
    Trigger,
    UserMode,
)
from pathai_backend.schemas.intent import IntentParseRequest, IntentParseResponse
from pathai_backend.schemas.location import (
    BeaconObservation,
    ImuPayload,
    ImuSample,
    LocationEstimateRequest,
    MatchedArea,
    PositionEstimateResponse,
)
from pathai_backend.schemas.map import (
    CoordinateSystem,
    FloorSummary,
    MapVersionResponse,
    Place,
    PlacesResponse,
)
from pathai_backend.schemas.nav import (
    CreateSessionRequest,
    CreateSessionResponse,
    CurrentInstructionSummary,
    EndSessionRequest,
    EndSessionResponse,
    LocationUpdate,
    NavUpdateRequest,
    NavUpdateResponse,
    OriginPosition,
    RouteInfo,
    SessionQueryResponse,
)

__all__ = [
    # 公共类型与枚举
    "StrictModel",
    "IntentType",
    "LocationQuality",
    "SessionStatus",
    "InstructionAction",
    "InstructionPriority",
    "NavEvent",
    "EndReason",
    "RouteFallbackType",
    "UserMode",
    "GeoPosition",
    "LandmarkRef",
    "Trigger",
    "NavigationInstruction",
    "PlaceCandidate",
    # API-01
    "WechatLoginRequest",
    "AuthResponse",
    # API-02/03
    "CoordinateSystem",
    "FloorSummary",
    "MapVersionResponse",
    "Place",
    "PlacesResponse",
    # API-04
    "IntentParseRequest",
    "IntentParseResponse",
    # API-05
    "BeaconObservation",
    "ImuPayload",
    "ImuSample",
    "LocationEstimateRequest",
    "MatchedArea",
    "PositionEstimateResponse",
    # API-06..09
    "OriginPosition",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "RouteInfo",
    "LocationUpdate",
    "NavUpdateRequest",
    "NavUpdateResponse",
    "CurrentInstructionSummary",
    "SessionQueryResponse",
    "EndSessionRequest",
    "EndSessionResponse",
]
