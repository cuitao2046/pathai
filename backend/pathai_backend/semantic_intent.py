# -*- coding: utf-8 -*-
"""第一阶段 LLM 语义兜底（docs/29）。

职责边界（与导航播报解耦）：
- 只在本地规则解析“失败/未命中/有歧义”或位置直答时被调用；
- 模型按系统提示词里的动作清单（由 actions skill 注册表汇总）自行选 action，
  本模块只负责把 action 映射成 API-04 既有执行计划，不再判断“意图是否受支持”；
- 动作名不在注册表、或执行体产不出结果时返回 None，由调用方沿用规则结果；
- 输出必须是词典内标准名。

本模块不参与导航指令播报，也不建立语音会话状态。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Protocol

from src.voice_agent.services.context_builder import SpatialIndex, build_spatial_context
from src.voice_agent.services.dictionary_store import (
    DEFAULT_DICT_PATH,
    DictionaryError,
    PoiDictionary,
    PoiInstance,
)
from src.voice_agent.services.intent_parser import IntentParser
from src.voice_agent.services.llm_client import LlmClient, LlmConfig
from src.voice_agent.skills.base import PlaceRef, SkillContext, SkillOutcome
from src.voice_agent.skills.registry import get_skill

from .config import Settings
from .map_repository import PublishedMap
from .schemas.common import IntentType, PlaceCandidate

logger = logging.getLogger(__name__)

#: 类别实例“明显更近”的判定阈值：差值不足则反问，避免猜错卫生间/办公室。
NEAREST_MARGIN_M = 3.0
#: 澄清候选上限（与 API-04 既有规则一致）。
_MAX_CANDIDATES = 5
#: 提示词里候选地址上限：控制长度，避免把整本词典塞进上下文。
_MAX_PROMPT_CANDIDATES = 60
#: 地名/POI 未精确命中时的统一话术（明确告知没有找到，并给出可复述的提示）。
_NOT_FOUND_TEXT = "没有找到这个地点，请说清楚房间名或附近的标志物，我再帮您找。"

#: 兼容旧模型/旧提示词：只给 intent_type 时推导等价动作（迁移垫片，不是白名单）。
_LEGACY_INTENT_ACTIONS = {
    "navigate_to_destination": "navigate_to_place",
    "query_where_am_i": "query_current_location",
    "start_navigation": "start_navigation",
}


class ChatModel(Protocol):
    def chat(self, messages: list[dict], response_schema: dict | None = None,
             schema_name: str = "voice_agent_output") -> object: ...


@dataclass(frozen=True)
class SemanticAnswer:
    """映射到 API-04 响应字段的语义解析结果。"""

    intent: IntentType
    confidence: float
    needs_clarification: bool
    destination: PlaceCandidate | None
    candidates: list[PlaceCandidate]
    response_text: str
    #: 模型选定的动作名（已在注册表中校验）
    action: str | None = None


# --------------------------------------------------------------------- #
# 词典与空间索引缓存：词典只在导出后变化，进程内复用即可
# --------------------------------------------------------------------- #
_dictionary: PoiDictionary | None = None
_index_cache: dict[tuple[str, str, str], SpatialIndex] = {}


def load_dictionary() -> PoiDictionary | None:
    global _dictionary
    if _dictionary is None:
        try:
            _dictionary = PoiDictionary.from_path(DEFAULT_DICT_PATH)
        except DictionaryError as err:
            logger.warning("语义兜底不可用：%s", err)
            return None
    return _dictionary


def _spatial_index(published: PublishedMap) -> SpatialIndex:
    key = (published.site_id, published.map_version, published.sha256)
    cached = _index_cache.get(key)
    if cached is not None:
        return cached
    index = SpatialIndex.from_package(published.data)
    if len(_index_cache) >= 4:  # 只保留最近几个版本，避免切版后内存增长
        _index_cache.clear()
    _index_cache[key] = index
    return index


def _node_xy(index: SpatialIndex, floor_id: str, node_id: str) -> tuple[float, float] | None:
    floor = index.floors.get(floor_id)
    if floor is None:
        return None
    for node in floor.nodes:
        if node.node_id == node_id:
            return node.coordinates
    return None


def _to_place(ref: PlaceRef | None) -> PlaceCandidate | None:
    if ref is None:
        return None
    return PlaceCandidate(placeId=ref.place_id, name=ref.name, floorId=ref.floor_id)


def _place_row(place) -> dict:
    """候选地址行：给模型看的最小充分信息（标识 / 名称 / 别名 / 楼层 / 类型）。"""
    return {
        "placeId": place.placeId,
        "name": place.name,
        "aliases": [place.spokenName, *place.aliases][:6],
        "floorId": place.floorId,
        "type": place.type,
    }


def _available_rows(text: str, places) -> list[dict]:
    """零命中时也给出“可用地点清单”，让模型按用途/功能/常识去匹配。

    场所 POI 不多时直接全量；超过上限才用类型/楼层/关键词粗筛，
    而不是只挑字面相近的（「放乐器的那间」与 POI 名称毫无重叠）。
    """
    place_list = list(places)
    if len(place_list) <= _MAX_PROMPT_CANDIDATES:
        return [_place_row(place) for place in place_list]
    chars = set(text)
    scored: list[tuple[int, object]] = []
    for place in place_list:
        score = 0
        for key in (place.name, place.type, place.spokenName, *place.aliases):
            if not key:
                continue
            if key in text:
                score += 10 * len(key)
            else:
                score += len(chars & set(key))
        scored.append((score, place))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [_place_row(place) for _, place in scored[:_MAX_PROMPT_CANDIDATES]]


# --------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------- #
class SemanticIntentService:
    def __init__(self, dictionary: PoiDictionary, llm: ChatModel):
        self._dictionary = dictionary
        self._parser = IntentParser(dictionary, llm)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ #
    def parse(
        self, text: str, published: PublishedMap, location: object | None
    ) -> SemanticAnswer | None:
        """返回 None 表示“不接管”，调用方沿用规则解析结果。"""
        if published.map_version != self._dictionary.map_version:
            logger.warning(
                "语义兜底跳过：词典 %s 与地图 %s 不一致",
                self._dictionary.map_version,
                published.map_version,
            )
            return None

        index = _spatial_index(published)
        places = {
            p.placeId: PlaceCandidate(placeId=p.placeId, name=p.name, floorId=p.floorId)
            for p in published.pkg.places
        }
        ctx = self._build_context(index, location)
        # 零命中：把当前场所的可用地点清单交给模型按用途/功能理解（不是只给字面相近的）
        rows = _available_rows(text, published.pkg.places)
        logger.info(
            "零命中候选清单 text=%r prompt_rows=%d names=%s",
            text,
            len(rows),
            [row["name"] for row in rows][:12],
        )
        if not rows:
            logger.warning("地点清单为空（地图未加载），直接回复未找到：text=%r", text)
            return SemanticAnswer(
                intent=IntentType.UNKNOWN,
                confidence=0.2,
                needs_clarification=True,
                destination=None,
                candidates=[],
                response_text=_NOT_FOUND_TEXT,
            )
        logger.info("语义兜底调用大模型 text=%r", text)
        try:
            outcome = self._parser.parse(text, ctx, (), rows)
        except Exception as err:  # 兜底路径绝不能把异常抛给语音主流程
            logger.warning("语义兜底异常：%s", err)
            return None
        logger.info(
            "语义兜底大模型结果 source=%s degraded=%s action=%s intent=%s ttft_ms=%s",
            outcome.source,
            outcome.degraded,
            outcome.result.action,
            outcome.result.intent_type,
            outcome.llm_ttft_ms,
        )
        if outcome.source != "llm" and outcome.degraded:
            logger.warning("大模型不可用，继续沿用规则结果：text=%r", text)
            return None

        result = outcome.result
        logger.info(
            "大模型候选 top3=%s",
            [
                {
                    "placeId": candidate.place_id,
                    "name": candidate.standard_name,
                    "confidence": candidate.confidence,
                }
                for candidate in result.top_candidates[:3]
            ],
        )
        action_name = result.action or _LEGACY_INTENT_ACTIONS.get(result.intent_type, "")
        if result.action is None and action_name:
            logger.info(
                "模型未返回 action，按 intent_type=%s 兼容映射为 %s", result.intent_type, action_name
            )
        skill = get_skill(action_name)
        if skill is None:
            logger.warning(
                "未知动作 text=%r action=%r intent=%s，按既定降级沿用规则结果",
                text,
                result.action,
                result.intent_type,
            )
            return None
        logger.info("命中动作注册表 action=%s text=%r", skill.name, text)

        skill_ctx = SkillContext(
            text=text,
            result=result,
            resolve_destination=lambda: self._destination_outcome(
                result, index, places, location
            ),
            answer_location=lambda: self._location_outcome(result, ctx),
        )
        try:
            skill_outcome = skill.execute(skill_ctx)
        except Exception as err:  # 单个 skill 失败不应影响语音主流程
            logger.warning("动作执行失败 action=%s err=%s，沿用规则结果", skill.name, err)
            return None
        if skill_outcome is None:
            logger.info("动作 %s 未产出可用结果，沿用规则结果 text=%r", skill.name, text)
            return None
        try:
            intent = IntentType(skill_outcome.intent)
        except ValueError:
            logger.warning(
                "动作 %s 返回未知意图 %s，沿用规则结果", skill.name, skill_outcome.intent
            )
            return None
        candidates: list[PlaceCandidate] = []
        for ref in skill_outcome.candidates:
            place = _to_place(ref)
            if place is not None:
                candidates.append(place)
        answer = SemanticAnswer(
            intent=intent,
            confidence=skill_outcome.confidence,
            needs_clarification=skill_outcome.needs_clarification,
            destination=_to_place(skill_outcome.destination),
            candidates=candidates,
            response_text=skill_outcome.response_text,
            action=skill_outcome.action or skill.name,
        )
        logger.info(
            "语义层最终选择 action=%s intent=%s destination=%s candidates=%s needs_clarification=%s",
            answer.action,
            answer.intent.value,
            answer.destination.placeId if answer.destination else None,
            [place.placeId for place in answer.candidates],
            answer.needs_clarification,
        )
        return answer

    # ------------------------------------------------------------------ #
    def _build_context(self, index: SpatialIndex, location: object | None):
        if location is None:
            return None
        floor_id = getattr(location, "floorId", None)
        if not floor_id:
            return None
        try:
            return build_spatial_context(
                index,
                floor_id,
                float(getattr(location, "x")),
                float(getattr(location, "y")),
                float(getattr(location, "headingDeg") or 0.0),
                "stationary",
            )
        except (ValueError, KeyError) as err:
            logger.warning("语义兜底：无法构建空间上下文（%s）", err)
            return None

    def _location_outcome(self, result, ctx) -> SkillOutcome | None:
        # 没有定位上下文时不硬答位置，沿用“正在查询”模板，避免编造
        if ctx is None:
            return None
        speech = result.direct_response_speech
        if not speech:
            return None
        return SkillOutcome(
            intent="query_location",
            confidence=0.95,
            needs_clarification=False,
            destination=None,
            candidates=(),
            response_text=speech,
        )

    def _destination_outcome(
        self,
        result,
        index: SpatialIndex,
        places: dict[str, PlaceCandidate],
        location: object | None,
    ) -> SkillOutcome:
        pairs: list[tuple[object, PoiInstance]] = []
        for candidate in result.top_candidates:
            entry = self._dictionary.get(candidate.standard_name)
            if entry is None:
                continue
            locked = getattr(candidate, "node_id", None)
            instances = (
                tuple(i for i in entry.instances if i.node_id == locked)
                if locked
                else entry.instances
            )
            pairs.extend((candidate, inst) for inst in instances)

        if not pairs:
            return SkillOutcome(
                intent="start_navigation",
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=(),
                response_text=result.clarification_question or _NOT_FOUND_TEXT,
            )

        # 只有一个物理实例：直接锁定
        if len(pairs) == 1:
            return self._unique(pairs[0][1], pairs[0][0], places)

        nearest = self._nearest(pairs, index, location)
        if nearest is not None:
            best_inst, best_cand, best_d, second_d = nearest
            if second_d is None or second_d - best_d >= NEAREST_MARGIN_M:
                return self._unique(best_inst, best_cand, places)

        return self._clarify(pairs, result, index, places, location)

    def _unique(
        self, inst: PoiInstance, candidate, places: dict[str, PlaceCandidate]
    ) -> SkillOutcome:
        place = places.get(inst.place_id)
        if place is None:
            return SkillOutcome(
                intent="start_navigation",
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=(),
                response_text=_NOT_FOUND_TEXT,
            )
        return SkillOutcome(
            intent="start_navigation",
            confidence=max(0.6, float(getattr(candidate, "confidence", 0.6))),
            needs_clarification=False,
            destination=PlaceRef(place.placeId, place.name, place.floorId),
            candidates=(),
            response_text=f"准备导航到{place.name}，请确认。",
        )

    def _nearest(
        self, pairs: list[tuple[object, PoiInstance]], index: SpatialIndex, location: object | None
    ):
        floor_id = getattr(location, "floorId", None) if location is not None else None
        if location is None or not floor_id:
            return None
        origin = (float(getattr(location, "x")), float(getattr(location, "y")))
        scored: list[tuple[float, object, PoiInstance]] = []
        for candidate, inst in pairs:
            if inst.floor_id != floor_id:
                continue
            xy = _node_xy(index, inst.floor_id, inst.node_id)
            if xy is None:
                continue
            scored.append((math.hypot(xy[0] - origin[0], xy[1] - origin[1]), candidate, inst))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0])
        best_d, best_cand, best_inst = scored[0]
        second_d = scored[1][0] if len(scored) > 1 else None
        return best_inst, best_cand, best_d, second_d

    def _clarify(
        self,
        pairs: Iterable[tuple[object, PoiInstance]],
        result,
        index: SpatialIndex,
        places: dict[str, PlaceCandidate],
        location: object | None,
    ) -> SkillOutcome:
        floor_id = getattr(location, "floorId", None) if location is not None else None
        origin = (
            (float(getattr(location, "x")), float(getattr(location, "y")))
            if location is not None
            else None
        )

        def sort_key(pair: tuple[object, PoiInstance]) -> tuple[int, float]:
            inst = pair[1]
            xy = _node_xy(index, inst.floor_id, inst.node_id)
            if origin is not None and xy is not None and inst.floor_id == floor_id:
                return (0, math.hypot(xy[0] - origin[0], xy[1] - origin[1]))
            return (1, 0.0)

        ordered = sorted(pairs, key=sort_key)[:_MAX_CANDIDATES]
        candidates: list[PlaceRef] = []
        seen: set[str] = set()
        for _, inst in ordered:
            if inst.place_id in seen:
                continue
            place = places.get(inst.place_id)
            if place is not None:
                seen.add(inst.place_id)
                candidates.append(PlaceRef(place.placeId, place.name, place.floorId))

        if not candidates:
            return SkillOutcome(
                intent="start_navigation",
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=(),
                response_text=result.clarification_question or _NOT_FOUND_TEXT,
            )
        names = "、".join(c.name for c in candidates)
        return SkillOutcome(
            intent="start_navigation",
            confidence=max(0.4, float(getattr(result.top_candidates[0], "confidence", 0.4))),
            needs_clarification=True,
            destination=None,
            candidates=tuple(candidates),
            response_text=result.clarification_question
            or f"找到{len(candidates)}个可能的地点：{names}，请说明楼层或完整名称。",
        )


# --------------------------------------------------------------------- #
# 进程级工厂：未配置 LLM 或词典缺失时返回 None（业务自动降级）
# --------------------------------------------------------------------- #
_service_cache: dict[int, SemanticIntentService | None] = {}


def get_semantic_service(settings: Settings) -> SemanticIntentService | None:
    key = id(settings)
    if key not in _service_cache:
        _service_cache[key] = _build_service(settings)
    return _service_cache[key]


def _build_service(settings: Settings) -> SemanticIntentService | None:
    if not settings.llm_configured:
        return None
    dictionary = load_dictionary()
    if dictionary is None:
        return None
    try:
        llm = LlmClient(
            LlmConfig(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_s=settings.llm_timeout_s,
                structured_mode=settings.llm_structured_mode,
                trace=settings.llm_trace,
            )
        )
    except Exception as err:  # 配置合法但客户端初始化失败
        logger.warning("语义兜底不可用：%s", err)
        return None
    return SemanticIntentService(dictionary, llm)
