# -*- coding: utf-8 -*-
"""意图解析编排（spec REQ-VOICE-06）。

优先级：精确快路径 → LLM 结构化解析 → 本地校验 → 失败降级规则解析。
`source` 标识实际生效的解析器，`degraded=True` 表示 LLM 不可用或输出非法。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from pydantic import ValidationError

from ..config.prompts import build_intent_messages, intent_json_schema
from ..schemas.llm_intent import DestinationCandidate, IntentParseResult
from ..schemas.spatial_context import NearbyElement, UserSpatialContext
from .dictionary_store import PoiDictionary
from .llm_client import LlmError, LlmResult
from .rule_intent_resolver import resolve as resolve_by_rules

logger = logging.getLogger(__name__)

#: 快路径（docs/26 §6.1）：整句精确命中，跳过 LLM
FAST_WHERE = re.compile(r"^(我在哪儿?|现在什么位置|周围有什么)$")
FAST_REPEAT = re.compile(r"^(再说一遍|再说一次|重复|没听清)$")

STATIONARY_MAX_CHARS = 65
WALKING_MAX_CHARS = 20
FRONT_CLOCK = {11, 12, 1}
FRONT_RADIUS_M = 6.0

_SENTENCE_END = "，。！？；、"
_CLOCK_PHRASE = {12: "正前方", 11: "左前方", 1: "右前方", 9: "正左方", 3: "正右方", 6: "正后方"}

ParseSource = Literal["fast_path", "llm", "rules"]


class ChatModel(Protocol):
    def chat(
        self,
        messages: list[dict],
        response_schema: dict | None = None,
        schema_name: str = "voice_agent_output",
    ) -> LlmResult: ...


@dataclass
class ParseOutcome:
    result: IntentParseResult
    source: ParseSource
    degraded: bool = False
    llm_ttft_ms: int | None = None
    cache_key: str | None = None


def _strip_edges(text: str) -> str:
    return re.sub(r"^[\s，。！？、,.!?]+|[\s，。！？、,.!?]+$", "", text or "")


def _fmt_meters(distance: float) -> str:
    return f"{distance:.0f}" if distance >= 1 else f"{distance:.1f}"


def _element_phrase(element: NearbyElement) -> str:
    direction = _CLOCK_PHRASE.get(element.clock_direction, f"{element.clock_direction}点钟方向")
    text = f"{direction}{_fmt_meters(element.distance_meters)}米"
    if element.name:
        text += f"有{element.name}"
    if element.accessibility_note:
        text += f"，{element.accessibility_note}"
    return text


def _front_element(ctx: UserSpatialContext) -> NearbyElement | None:
    front = [
        e
        for e in ctx.nearby_elements
        if e.clock_direction in FRONT_CLOCK and e.distance_meters <= FRONT_RADIUS_M
    ]
    return min(front, key=lambda e: e.distance_meters) if front else None


def _clamp_speech(text: str, motion_state: str) -> str:
    """按安全上限截断播报：静止 65 字、行走 20 字（spec D14）。"""
    limit = WALKING_MAX_CHARS if motion_state == "walking" else STATIONARY_MAX_CHARS
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) <= limit:
        return cleaned
    cut = max((cleaned.rfind(p, 0, limit) for p in _SENTENCE_END), default=-1)
    if cut >= max(8, limit // 3):
        return cleaned[: cut + 1]
    return cleaned[: limit - 1] + "。"


def fast_path_speech(ctx: UserSpatialContext) -> str:
    """快路径模板播报：区域 + 盲道状态 + 前方关键要素（不含坐标/编号）。"""
    front = _front_element(ctx)
    if ctx.motion_state == "walking":
        text = _element_phrase(front) + "。" if front else "前方没有检测到障碍。"
        return _clamp_speech(text, ctx.motion_state)
    parts = [f"你在{ctx.current_region_name}"]
    parts.append("脚下有盲道" if ctx.on_tactile_paving else "本楼未铺设盲道")
    if front is not None:
        parts.append(_element_phrase(front))
    return _clamp_speech("。".join(parts) + "。", ctx.motion_state)


def _extract_json(text: str) -> dict:
    """容忍 ```json 围栏与前后解释文字，取出第一个完整 JSON 对象。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中没有 JSON 对象")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 输出 JSON 顶层不是对象")
    return data


def _prepare_candidates(data: dict, dictionary: PoiDictionary) -> dict:
    """先剔除词典外候选，再截断 Top-3：既满足 Schema，又保留有效候选。"""
    candidates = data.get("top_candidates")
    if isinstance(candidates, list):
        valid = [
            c
            for c in candidates
            if isinstance(c, dict) and c.get("standard_name") in dictionary.pois
        ]
        data["top_candidates"] = valid[:3]
    return data


def _validate_result(
    result: IntentParseResult, dictionary: PoiDictionary
) -> IntentParseResult:
    """词典与枚举约束：非法候选必须剔除；无法解释时抛错交由降级。"""
    kept: list[DestinationCandidate] = []
    seen: set[str] = set()
    for candidate in result.top_candidates:
        if candidate.standard_name not in dictionary.pois or candidate.standard_name in seen:
            continue
        seen.add(candidate.standard_name)
        kept.append(candidate)
    kept = kept[:3]
    if result.top_candidates and not kept:
        raise ValueError("LLM 候选全部不在词典内")

    status = result.resolution_status
    if result.intent_type == "navigate_to_destination":
        if status is None:
            status = (
                "not_found_in_building"
                if not kept
                else "resolved_unique_concept"
                if len(kept) == 1
                else "ambiguous_concepts"
            )
        if status == "ambiguous_concepts" and len(kept) < 2:
            status = "resolved_unique_concept"
        if status in ("resolved_unique_concept", "ambiguous_concepts") and not kept:
            raise ValueError("LLM 未给出有效候选")
    return result.model_copy(update={"top_candidates": kept, "resolution_status": status})


class IntentParser:
    def __init__(self, dictionary: PoiDictionary, llm: ChatModel | None = None):
        self._dictionary = dictionary
        self._llm = llm

    def parse(
        self,
        text: str,
        spatial_context: UserSpatialContext | None = None,
        pending: Sequence[dict] = (),
    ) -> ParseOutcome:
        fast = self._fast_path(text, spatial_context)
        if fast is not None:
            return fast

        if self._llm is None:
            return self._degrade(text, pending, reason="LLM 未配置")

        try:
            messages, cache_key = build_intent_messages(
                self._dictionary, text, spatial_context, pending
            )
            llm_result = self._llm.chat(
                messages,
                response_schema=intent_json_schema(),
                schema_name="intent_parse_result",
            )
            parsed = IntentParseResult.model_validate(
                _prepare_candidates(_extract_json(llm_result.text), self._dictionary)
            )
            parsed = _validate_result(parsed, self._dictionary)
            parsed = self._fill_direct_speech(parsed, spatial_context)
            return ParseOutcome(
                result=parsed,
                source="llm",
                degraded=False,
                llm_ttft_ms=llm_result.ttft_ms,
                cache_key=cache_key,
            )
        except (LlmError, ValidationError, ValueError, TypeError, KeyError) as err:
            return self._degrade(text, pending, reason=str(err))

    # ------------------------------------------------------------------ #
    def _fast_path(
        self, text: str, spatial_context: UserSpatialContext | None
    ) -> ParseOutcome | None:
        command = _strip_edges(text)
        if FAST_REPEAT.match(command):
            return ParseOutcome(
                result=IntentParseResult(
                    intent_type="navigation_control",
                    direct_response_speech="好的，将重复当前指令。",
                ),
                source="fast_path",
            )
        if FAST_WHERE.match(command):
            speech = fast_path_speech(spatial_context) if spatial_context else None
            return ParseOutcome(
                result=IntentParseResult(
                    intent_type="query_where_am_i", direct_response_speech=speech
                ),
                source="fast_path",
            )
        return None

    def _fill_direct_speech(
        self, result: IntentParseResult, ctx: UserSpatialContext | None
    ) -> IntentParseResult:
        if result.intent_type in ("query_where_am_i", "check_hazard_or_door"):
            speech = result.direct_response_speech
            if not speech and ctx is not None:
                speech = fast_path_speech(ctx)
            if speech:
                motion = ctx.motion_state if ctx is not None else "stationary"
                speech = _clamp_speech(speech, motion)
            return result.model_copy(update={"direct_response_speech": speech})
        return result

    def _degrade(
        self, text: str, pending: Sequence[dict], reason: str
    ) -> ParseOutcome:
        logger.warning("意图解析降级为规则解析：%s", reason)
        return ParseOutcome(
            result=resolve_by_rules(text, self._dictionary, pending),
            source="rules",
            degraded=True,
        )
