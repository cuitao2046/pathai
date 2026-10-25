# -*- coding: utf-8 -*-
"""API-04：POST /api/v1/intents/parse（docs/24 §8）。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from pathai_backend import auth
from pathai_backend.errors import ApiError
from pathai_backend.intent_parser import build_response_text, parse_intent
from pathai_backend.map_repository import MapRepository
from pathai_backend.routers.map import get_map_repository
from pathai_backend.schemas.intent import IntentParseRequest, IntentParseResponse

router = APIRouter(prefix="/api/v1/intents", tags=["intent"])


@router.post("/parse", response_model=IntentParseResponse)
def parse(
    body: IntentParseRequest,
    _: object = Depends(auth.get_current_user),
    repo: MapRepository = Depends(get_map_repository),
) -> IntentParseResponse:
    # 地图版本校验：意图候选必须与已发布地图一致（docs/24 API-04 规则）
    if not repo.is_known_site(body.siteId):
        raise ApiError(404, "SITE_NOT_FOUND", f"场所不存在：{body.siteId}")
    published = (
        repo.latest(body.siteId)
        if body.mapVersion == "latest"
        else repo.get(body.siteId, body.mapVersion)
    )
    if published is None:
        raise ApiError(
            404, "MAP_NOT_PUBLISHED", f"地图版本未发布：{body.siteId}@{body.mapVersion}"
        )

    parsed = parse_intent(body.text, list(published.pkg.places))
    return IntentParseResponse(
        intentId=f"intent_{uuid.uuid4().hex}",
        intent=parsed.intent,
        confidence=parsed.confidence,
        needsClarification=parsed.needs_clarification,
        destination=parsed.destination,
        candidates=list(parsed.candidates),
        responseText=build_response_text(parsed),
    )
