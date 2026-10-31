# -*- coding: utf-8 -*-
"""导航指令文本的 LLM 改写（docs/29 §3）。

只替换 `route.segments[].text`，动作、距离、触发节点、播报时机全部不变；
改写失败（未配置/超时/非法输出/校验不过）一律返回原路线，保证导航可用。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from src.voice_agent.config.prompts import build_navigation_messages, navigation_json_schema
from src.voice_agent.services.dictionary_store import PoiDictionary
from src.voice_agent.services.llm_client import LlmClient, LlmConfig

from .config import Settings
from .map_repository import PublishedMap
from .schemas.nav import RouteInfo
from .semantic_intent import load_dictionary

logger = logging.getLogger(__name__)

#: 单条播报长度上限（行走中听不清长句）。
MAX_TEXT_CHARS = 40
_MIN_TEXT_CHARS = 4


class ChatModel(Protocol):
    def chat(self, messages: list[dict], response_schema: dict | None = None,
             schema_name: str = "voice_agent_output") -> object: ...


def _extract_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中没有 JSON 对象")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 输出 JSON 顶层不是对象")
    return data


def rewrite_route_texts(
    route: RouteInfo,
    published: PublishedMap,
    settings: Settings,
    destination_name: str,
    llm: ChatModel | None = None,
    dictionary: PoiDictionary | None = None,
) -> RouteInfo:
    dictionary = dictionary or load_dictionary()
    if dictionary is None or dictionary.map_version != published.map_version:
        return route
    if llm is None:
        if not settings.llm_configured:
            return route
        try:
            llm = LlmClient(
                LlmConfig(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    timeout_s=settings.llm_timeout_s,
                    structured_mode=settings.llm_structured_mode,
                )
            )
        except Exception as err:
            logger.warning("导航文案改写不可用：%s", err)
            return route

    payload = [
        {
            "instructionId": seg.instructionId,
            "instructionSequence": seg.instructionSequence,
            "action": seg.action.value,
            "text": seg.text,
            "distanceM": seg.distanceM,
            "priority": seg.priority.value,
            "triggerType": seg.trigger.type,
        }
        for seg in route.segments
    ]
    messages, _cache_key = build_navigation_messages(
        dictionary, payload, destination_name, route.riskNotices
    )
    try:
        raw = llm.chat(
            messages,
            response_schema=navigation_json_schema(),
            schema_name="navigation_texts",
        )
        texts = _validated_texts(_extract_json(getattr(raw, "text", "")), route)
    except Exception as err:  # 文案改写失败绝不能影响路线本身
        logger.warning("导航文案改写回退原文：%s", err)
        return route
    if texts is None:
        logger.warning("导航文案改写输出不合法，回退原文")
        return route
    segments = [seg.model_copy(update={"text": texts[seg.instructionId]}) for seg in route.segments]
    return route.model_copy(update={"segments": segments})


def _validated_texts(data: dict, route: RouteInfo) -> dict[str, str] | None:
    """逐一校验：ID 全覆盖、长度、数字不丢失。任一不过即整体回退。"""
    items = data.get("instructions")
    if not isinstance(items, list):
        return None
    by_id: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            return None
        instruction_id = item.get("instructionId")
        text = item.get("text")
        if not isinstance(instruction_id, str) or not isinstance(text, str):
            return None
        text = text.strip()
        if not (_MIN_TEXT_CHARS <= len(text) <= MAX_TEXT_CHARS):
            return None
        by_id[instruction_id] = text
    expected = {seg.instructionId for seg in route.segments}
    if set(by_id) != expected:
        return None
    for seg in route.segments:
        original_numbers = set(re.findall(r"\d+(?:\.\d+)?", seg.text))
        new_numbers = set(re.findall(r"\d+(?:\.\d+)?", by_id[seg.instructionId]))
        if not original_numbers.issubset(new_numbers):
            return None
    return by_id
