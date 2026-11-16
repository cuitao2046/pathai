# -*- coding: utf-8 -*-
"""API-04：POST /api/v1/intents/parse（docs/24 §8）。"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends

from pathai_backend import auth, semantic_intent
from pathai_backend.config import Settings
from pathai_backend.errors import ApiError
from pathai_backend.intent_parser import build_response_text, parse_intent
from pathai_backend.map_repository import MapRepository
from pathai_backend.routers.map import get_map_repository
from pathai_backend.schemas.common import IntentType
from pathai_backend.schemas.intent import (
    IntentAction,
    IntentParseRequest,
    IntentParseResponse,
)

router = APIRouter(prefix="/api/v1/intents", tags=["intent"])

logger = logging.getLogger(__name__)


@router.post("/parse", response_model=IntentParseResponse)
def parse(
    body: IntentParseRequest,
    _: object = Depends(auth.get_current_user),
    repo: MapRepository = Depends(get_map_repository),
    settings: Settings = Depends(auth.get_settings),
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
    logger.info(
        "意图规则解析 text=%r intent=%s needs_clarification=%s action=%s",
        body.text,
        parsed.intent.value,
        parsed.needs_clarification,
        parsed.action,
    )

    # 模糊命中已有候选：直接返回候选卡，不走大模型（避免额外延迟）
    if parsed.needs_clarification and parsed.candidates:
        logger.info(
            "规则模糊命中 %d 项，直接返回候选卡（不走大模型）text=%r",
            len(parsed.candidates),
            body.text,
        )

    # 第一阶段语义兜底（docs/29）：仅零命中/位置直答时调用 LLM；
    # 未配置、词典过期或调用失败都返回 None，继续沿用下面的规则结果。
    zero_hit = parsed.needs_clarification and not parsed.candidates
    if parsed.intent is IntentType.QUERY_LOCATION or zero_hit:
        service = semantic_intent.get_semantic_service(settings)
        if service is None:
            logger.warning("规则未命中但大模型未配置，沿用规则结果：text=%r", body.text)
        else:
            logger.info("零命中/需澄清，转大模型：text=%r", body.text)
            answer = service.parse(body.text, published, body.location)
            if answer is not None:
                logger.info(
                    "大模型返回动作 text=%r intent=%s action=%s needs_clarification=%s",
                    body.text,
                    answer.intent.value,
                    answer.action,
                    answer.needs_clarification,
                )
                return IntentParseResponse(
                    intentId=f"intent_{uuid.uuid4().hex}",
                    intent=answer.intent,
                    confidence=answer.confidence,
                    needsClarification=answer.needs_clarification,
                    destination=answer.destination,
                    candidates=answer.candidates,
                    responseText=answer.response_text,
                    action=(
                        IntentAction(name=answer.action, confirmationRequired=False)
                        if answer.action is not None
                        else None
                    ),
                )
            logger.warning("大模型未接管，沿用规则结果：text=%r", body.text)

    return IntentParseResponse(
        intentId=f"intent_{uuid.uuid4().hex}",
        intent=parsed.intent,
        confidence=parsed.confidence,
        needsClarification=parsed.needs_clarification,
        destination=parsed.destination,
        candidates=list(parsed.candidates),
        responseText=build_response_text(parsed),
        action=(
            IntentAction(name=parsed.action, confirmationRequired=False)
            if parsed.action is not None
            else None
        ),
    )
