# -*- coding: utf-8 -*-
"""API-04 受控意图解析契约（docs/24 §8 API-04）。

服务端校验意图枚举、置信度、地点 ID 与地图版本；外部模型原始响应不直接返回。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from pathai_backend.schemas.common import IntentType, PlaceCandidate, StrictModel


class IntentLocation(StrictModel):
    """语音请求随带的当前定位（docs/29 §2）。

    仅有坐标与置信度时可用于按距离挑选同层的类别实例；
    headingDeg 用于生成时钟方向，缺失时按 0 度处理（正北），
    因此位置直答建议客户端携带朝向。
    """

    x: float
    y: float
    floorId: str | None = Field(default=None, min_length=1)
    confidence: float = Field(ge=0, le=1)
    headingDeg: float | None = Field(default=None, ge=0, lt=360)


class IntentParseRequest(StrictModel):
    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    sessionId: str | None = Field(default=None, min_length=1)
    locale: str = "zh-CN"
    location: IntentLocation | None = None


class IntentParseResponse(StrictModel):
    intentId: str = Field(min_length=1)
    intent: IntentType
    confidence: float = Field(ge=0, le=1)
    needsClarification: bool
    destination: PlaceCandidate | None = None
    candidates: list[PlaceCandidate] = Field(default_factory=list)
    responseText: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_clarification(self) -> "IntentParseResponse":
        if self.needsClarification and self.destination is not None:
            raise ValueError("needsClarification=true 时不得同时给出唯一 destination")
        return self
