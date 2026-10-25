# -*- coding: utf-8 -*-
"""第一阶段受控意图解析（docs/24 §8 API-04）。

规则引擎：关键词触发意图 + 地点目录最长匹配消歧，不依赖外部模型；
解析结果封装为契约响应，低置信/多义/未命中一律 needsClarification=true，
不得据此创建导航会话。置信度为规则固定值（工程初始值，评审后冻结）。
"""
from __future__ import annotations

from dataclasses import dataclass

from pathai_backend.map_package.schema import PlaceEntry
from pathai_backend.schemas.common import IntentType, PlaceCandidate

#: 有序触发词表：靠前的意图优先（控制类短命令优先于带目的地的导航请求）。
_INTENT_RULES: tuple[tuple[IntentType, tuple[str, ...]], ...] = (
    (IntentType.REPEAT_INSTRUCTION, ("再说一遍", "再说一次", "重复", "没听清")),
    (IntentType.END_NAVIGATION, ("结束导航", "我到了", "已到达", "结束")),
    (IntentType.CANCEL, ("取消导航", "取消", "不去了")),
    (IntentType.QUERY_LOCATION, ("我在哪里", "我在哪", "我的位置", "当前位置")),
    (IntentType.SEARCH_FACILITY, ("最近的", "附近", "哪里有")),
    (IntentType.START_NAVIGATION, ("带我去", "导航到", "我要去", "我要到", "去", "到")),
)

#: 规则置信度（工程初始值）。
_CONFIDENCE_COMMAND = 0.95
_CONFIDENCE_UNIQUE_PLACE = 0.9
_CONFIDENCE_AMBIGUOUS = 0.6
_CONFIDENCE_NO_PLACE = 0.3
_CONFIDENCE_UNKNOWN = 0.1

#: 澄清候选数量上限。
_MAX_CANDIDATES = 5


@dataclass(frozen=True)
class ParsedIntent:
    intent: IntentType
    confidence: float
    needs_clarification: bool
    destination: PlaceCandidate | None
    candidates: tuple[PlaceCandidate, ...]


def _match_places(text: str, places: list[PlaceEntry]) -> list[PlaceEntry]:
    """地点目录最长匹配：只保留命中别名最长（并列）的地点，用于消歧。"""
    best_len = 0
    matched: list[PlaceEntry] = []
    for place in places:
        aliases = [place.name, place.spokenName, *place.aliases]
        hit_len = max((len(a) for a in aliases if a and a in text), default=0)
        if hit_len == 0:
            continue
        if hit_len > best_len:
            best_len = hit_len
            matched = [place]
        elif hit_len == best_len:
            matched.append(place)
    matched.sort(key=lambda p: p.placeId)
    return matched[:_MAX_CANDIDATES]


def _candidate(place: PlaceEntry) -> PlaceCandidate:
    return PlaceCandidate(placeId=place.placeId, name=place.name, floorId=place.floorId)


def parse_intent(text: str, places: list[PlaceEntry]) -> ParsedIntent:
    """规则解析：返回意图与地点候选（docs/24 API-04 规则）。"""
    matched_intent = IntentType.UNKNOWN
    for intent, keywords in _INTENT_RULES:
        if any(k in text for k in keywords):
            matched_intent = intent
            break

    if matched_intent is IntentType.UNKNOWN:
        return ParsedIntent(
            intent=IntentType.UNKNOWN,
            confidence=_CONFIDENCE_UNKNOWN,
            needs_clarification=True,
            destination=None,
            candidates=(),
        )

    # 控制类/查询类意图不解析目的地
    if matched_intent in (
        IntentType.REPEAT_INSTRUCTION,
        IntentType.END_NAVIGATION,
        IntentType.CANCEL,
        IntentType.QUERY_LOCATION,
        IntentType.SEARCH_FACILITY,
    ):
        return ParsedIntent(
            intent=matched_intent,
            confidence=_CONFIDENCE_COMMAND,
            needs_clarification=False,
            destination=None,
            candidates=(),
        )

    matched = _match_places(text, places)
    if len(matched) == 1:
        return ParsedIntent(
            intent=matched_intent,
            confidence=_CONFIDENCE_UNIQUE_PLACE,
            needs_clarification=False,
            destination=_candidate(matched[0]),
            candidates=(),
        )
    return ParsedIntent(
        intent=matched_intent,
        confidence=(
            _CONFIDENCE_AMBIGUOUS if matched else _CONFIDENCE_NO_PLACE
        ),
        needs_clarification=True,
        destination=None,
        candidates=tuple(_candidate(p) for p in matched),
    )


def build_response_text(parsed: ParsedIntent) -> str:
    """澄清/确认话术（只用于 TTS 播报确认，不作为路线指令来源）。"""
    intent = parsed.intent
    if intent is IntentType.UNKNOWN:
        return "抱歉，我没有听懂，请重新描述。"
    if intent is IntentType.CANCEL:
        return "好的，已收到取消请求。"
    if intent is IntentType.END_NAVIGATION:
        return "好的，将结束本次导航。"
    if intent is IntentType.REPEAT_INSTRUCTION:
        return "好的，将重复当前指令。"
    if intent is IntentType.QUERY_LOCATION:
        return "请稍等，正在查询您的当前位置。"
    if intent is IntentType.SEARCH_FACILITY:
        return "请稍等，正在为您查找附近设施。"
    if parsed.destination is not None:
        return f"准备导航到{parsed.destination.name}，请确认。"
    if parsed.candidates:
        names = "、".join(c.name for c in parsed.candidates)
        return f"找到{len(parsed.candidates)}个匹配地点：{names}，请说明楼层或完整名称。"
    return "没有找到该地点，请换个说法。"
