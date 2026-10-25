# -*- coding: utf-8 -*-
"""API-04 受控意图解析契约（docs/24 §8 API-04）。

服务端校验意图枚举、置信度、地点 ID 与地图版本；外部模型原始响应不直接返回。
"""
from __future__ import annotations

from pydantic import Field, model_validator

from pathai_backend.schemas.common import IntentType, PlaceCandidate, StrictModel


class IntentParseRequest(StrictModel):
    siteId: str = Field(min_length=1)
    mapVersion: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=500)
    sessionId: str | None = Field(default=None, min_length=1)
    locale: str = "zh-CN"


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
