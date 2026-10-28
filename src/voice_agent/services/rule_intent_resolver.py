# -*- coding: utf-8 -*-
"""规则版语义解析（spec REQ-VOICE-03）。

LLM 未接入期的解析器，也是 LLM 失败/无密钥时的降级通道；输出与 LLM
相同的 `IntentParseResult`，下游决策矩阵无需区分来源。

匹配优先级：最长别名命中 > 类别别名 > 弱相关推荐。
"""
from __future__ import annotations

import re
from typing import Iterable, Sequence

from ..schemas.llm_intent import DestinationCandidate, IntentParseResult
from .dictionary_store import PoiDictionary

FLOOR_LABEL = {"1": "一楼", "2": "二楼", "3": "三楼", "4": "四楼"}

_CONF_EXACT = 0.95
_CONF_CATEGORY = 0.60
_CONF_RELATED = 0.40
_CONF_CONTEXTUAL = 1.0

_REPEAT_WORDS = ("再说一遍", "再说一次", "重复", "没听清")
_CONTROL_WORDS = ("取消导航", "取消", "不去了", "结束导航", "我到了", "结束")
_WHERE_WORDS = ("我在哪", "在哪儿", "在哪", "什么位置", "我的位置", "当前位置", "周围有什么")
_HAZARD_WORDS = ("门开着", "门关着", "好走吗", "前面有", "前方有", "有障碍", "挡路", "坡道", "台阶")
_NAV_VERBS = ("带我去", "导航到", "我要去", "我要到", "怎么走", "哪里", "在哪", "最近", "去", "到", "找")
#: 提取目的地原词时剥离的口语前缀/后缀（长词优先）
_MENTION_PREFIXES = (
    "带我去", "导航到", "我要到", "我要去", "我想去", "我想找", "帮忙找", "找一下", "我在找", "我要", "去", "到", "找",
)
_MENTION_SUFFIXES = ("怎么走", "在哪里", "在哪儿", "在哪", "的位置", "位置")

_PUNCT = re.compile(r"[\s，。！？、；：,.!?;:~～\"'“”‘’]+")
_ORDINAL = {"一": 0, "二": 1, "三": 2, "1": 0, "2": 1, "3": 2}


def _normalize(text: str) -> str:
    return _PUNCT.sub("", text or "")


def _mention(text: str) -> str:
    """从口语文本中剥离导航动词与疑问后缀，得到目的地原词。"""
    value = text
    for prefix in _MENTION_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    for suffix in _MENTION_SUFFIXES:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return value or text


def _has(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def _candidate(
    standard_name: str,
    match_type: str,
    confidence: float,
    reasoning: str,
    floor: str | None = None,
    node_id: str | None = None,
) -> DestinationCandidate:
    return DestinationCandidate(
        standard_name=standard_name,
        floor=floor,
        match_type=match_type,  # type: ignore[arg-type]
        confidence=confidence,
        reasoning=reasoning,
        node_id=node_id,
    )


def _intent(intent_type: str, **kwargs) -> IntentParseResult:
    return IntentParseResult(intent_type=intent_type, **kwargs)  # type: ignore[arg-type]


def _index_aliases(dictionary: PoiDictionary) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for entry in dictionary.pois.values():
        for name in (entry.standard_name, *entry.aliases):
            if name:
                index.setdefault(_normalize(name) or name, set()).add(entry.standard_name)
    return index


def _longest_match(text: str, index: dict[str, Sequence[str] | set[str]]) -> tuple[int, list[str]]:
    best_len = 0
    names: list[str] = []
    for word, targets in index.items():
        if not word or word not in text:
            continue
        if len(word) > best_len:
            best_len = len(word)
            names = list(targets)
        elif len(word) == best_len:
            names.extend(t for t in targets if t not in names)
    return best_len, names


def _clarify_multiple(names: Sequence[str], dictionary: PoiDictionary) -> str:
    entries = [dictionary.get(name) for name in names]
    entries = [e for e in entries if e is not None]
    floors = {e.instances[0].floor_id for e in entries if e.instances}
    prefix = FLOOR_LABEL.get(next(iter(floors)), "楼内") if len(floors) == 1 else "楼内"
    listed = "、".join(e.standard_name for e in entries)
    return f"{prefix}有{listed}，请问您要去哪一个？"


def _contextual_candidate(
    item: dict, dictionary: PoiDictionary, reasoning: str
) -> IntentParseResult:
    entry = dictionary.get(item.get("standard_name", ""))
    if entry is None:
        return _intent("out_of_scope")
    return IntentParseResult(
        intent_type="navigate_to_destination",
        raw_target_mention=entry.standard_name,
        resolution_status="resolved_unique_concept",
        top_candidates=[
            _candidate(
                entry.standard_name,
                "contextual_selection",
                _CONF_CONTEXTUAL,
                reasoning,
                floor=item.get("floor"),
                node_id=item.get("node_id"),
            )
        ],
    )


def _match_pending(
    text: str, pending: Sequence[dict], dictionary: PoiDictionary
) -> IntentParseResult | None:
    ordinal = re.search(r"第([一二三123])", text)
    if ordinal is not None:
        index = _ORDINAL.get(ordinal.group(1))
        if index is not None and index < len(pending):
            return _contextual_candidate(pending[index], dictionary, "按用户选择的序号消歧")

    if _has(text, ("最近的", "离我最近", "最近那个")):
        names = {item.get("standard_name") for item in pending}
        if len(names) == 1:
            return _contextual_candidate(
                {"standard_name": names.pop(), "floor": None, "node_id": None},
                dictionary,
                "用户选择离自己最近的一间",
            )

    best_len = 0
    best_item: dict | None = None
    for item in pending:
        entry = dictionary.get(item.get("standard_name", ""))
        keys = [item.get("standard_name") or "", item.get("zone_hint") or ""]
        if entry is not None:
            keys.extend(entry.aliases)
        for key in keys:
            key_norm = _normalize(key)
            if key_norm and key_norm in text and len(key_norm) > best_len:
                best_len = len(key_norm)
                best_item = item
    if best_item is not None:
        return _contextual_candidate(best_item, dictionary, "命中上一轮候选的名称或方位描述")
    return None


def resolve(
    text: str,
    dictionary: PoiDictionary,
    pending: Sequence[dict] = (),
) -> IntentParseResult:
    """把用户文本解析为结构化意图与 Top-3 候选（无 LLM）。"""
    norm = _normalize(text)

    if _has(norm, _REPEAT_WORDS):
        return _intent("navigation_control", direct_response_speech="好的，将重复当前指令。")
    if _has(norm, _CONTROL_WORDS):
        return _intent("navigation_control", direct_response_speech="好的，已收到。")

    if pending:
        contextual = _match_pending(norm, pending, dictionary)
        if contextual is not None:
            return contextual

    alias_len, alias_names = _longest_match(norm, _index_aliases(dictionary))
    category_len, category_names = _longest_match(norm, dictionary.category_aliases)
    matched_len = max(alias_len, category_len)
    # 最长命中优先：仅当类别别名更长或等长时才纳入类别候选
    if alias_len > category_len:
        names = list(alias_names)
        from_category = False
    elif category_len > alias_len:
        names = list(category_names)
        from_category = True
    else:
        names = [*alias_names, *category_names]
        from_category = bool(category_names)
    names = [name for name in dict.fromkeys(names) if dictionary.get(name) is not None][:3]
    mention = _mention(norm)

    if matched_len and len(names) == 1:
        entry = dictionary.get(names[0])
        assert entry is not None
        return IntentParseResult(
            intent_type="navigate_to_destination",
            raw_target_mention=mention,
            resolution_status="resolved_unique_concept",
            top_candidates=[
                _candidate(
                    entry.standard_name,
                    "category_submatch" if from_category else "exact_or_synonym",
                    _CONF_CATEGORY if from_category else _CONF_EXACT,
                    "类别别名命中" if from_category else "标准名或口语别名命中",
                    floor=None,
                )
            ],
        )

    if matched_len and len(names) > 1:
        return IntentParseResult(
            intent_type="navigate_to_destination",
            raw_target_mention=mention,
            resolution_status="ambiguous_concepts",
            top_candidates=[
                _candidate(name, "category_submatch", _CONF_CATEGORY, "多个标准名同时命中")
                for name in names
            ],
            clarification_question=_clarify_multiple(names, dictionary),
        )

    # 位置/环境问答：无目的地命中时才判定，避免「厕所在哪」被误判为位置查询
    if _has(norm, _WHERE_WORDS):
        return _intent("query_where_am_i")
    if _has(norm, _HAZARD_WORDS):
        return _intent("check_hazard_or_door")

    # 弱相关推荐：楼内没有，但存在人工配置的近似设施
    fallback_len = 0
    fallback_target: str | None = None
    for word, target in dictionary.related_fallbacks.items():
        word_norm = _normalize(word)
        if word_norm and word_norm in norm and len(word_norm) > fallback_len:
            fallback_len = len(word_norm)
            fallback_target = target
    if fallback_target is not None and dictionary.get(fallback_target) is not None:
        return IntentParseResult(
            intent_type="navigate_to_destination",
            raw_target_mention=mention,
            resolution_status="not_found_in_building",
            top_candidates=[
                _candidate(
                    fallback_target,
                    "related_fallback",
                    _CONF_RELATED,
                    "楼内无该设施，按人工配置给出近似设施",
                )
            ],
            clarification_question=f"楼内没有找到{mention}，您是指{fallback_target}吗？",
        )

    if _has(norm, _NAV_VERBS):
        return IntentParseResult(
            intent_type="navigate_to_destination",
            raw_target_mention=mention,
            resolution_status="not_found_in_building",
        )
    return _intent("out_of_scope")
