# -*- coding: utf-8 -*-
"""第一阶段 LLM 语义兜底（docs/29）。

职责边界（与导航播报解耦）：
- 只在本地规则解析“失败/未命中/有歧义”或位置直答时被调用；
- 输出必须是词典内标准名，并映射回 API-04 既有契约；
- 类别型目的地（如「卫生间」）优先取同层最近实例，距离接近时反问；
- 任何异常（未配置/词典过期/超时/非法 JSON）都返回 None，由调用方沿用规则结果。

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

from .config import Settings
from .map_repository import PublishedMap
from .schemas.common import IntentType, PlaceCandidate

logger = logging.getLogger(__name__)

#: 类别实例“明显更近”的判定阈值：差值不足则反问，避免猜错卫生间/办公室。
NEAREST_MARGIN_M = 3.0
#: 澄清候选上限（与 API-04 既有规则一致）。
_MAX_CANDIDATES = 5

_SUPPORTED_INTENTS = {
    "navigate_to_destination": IntentType.START_NAVIGATION,
    "query_where_am_i": IntentType.QUERY_LOCATION,
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
        try:
            outcome = self._parser.parse(text, ctx, ())
        except Exception as err:  # 兜底路径绝不能把异常抛给语音主流程
            logger.warning("语义兜底异常：%s", err)
            return None
        if outcome.source != "llm" and outcome.degraded:
            return None
        result = outcome.result
        if result.intent_type not in _SUPPORTED_INTENTS:
            return None
        if result.intent_type == "query_where_am_i":
            return self._location_answer(result, ctx)
        return self._destination_answer(result, index, places, location)

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

    def _location_answer(self, result, ctx) -> SemanticAnswer | None:
        # 没有定位上下文时不硬答位置，沿用“正在查询”模板，避免编造
        if ctx is None:
            return None
        speech = result.direct_response_speech
        if not speech:
            return None
        return SemanticAnswer(
            intent=IntentType.QUERY_LOCATION,
            confidence=0.95,
            needs_clarification=False,
            destination=None,
            candidates=[],
            response_text=speech,
        )

    def _destination_answer(
        self,
        result,
        index: SpatialIndex,
        places: dict[str, PlaceCandidate],
        location: object | None,
    ) -> SemanticAnswer:
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
            return SemanticAnswer(
                intent=IntentType.START_NAVIGATION,
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=[],
                response_text=result.clarification_question
                or "楼内没有找到这个地点，请换个说法。",
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
    ) -> SemanticAnswer:
        place = places.get(inst.place_id)
        if place is None:
            return SemanticAnswer(
                intent=IntentType.START_NAVIGATION,
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=[],
                response_text="没有找到该地点，请换个说法。",
            )
        return SemanticAnswer(
            intent=IntentType.START_NAVIGATION,
            confidence=max(0.6, float(getattr(candidate, "confidence", 0.6))),
            needs_clarification=False,
            destination=place,
            candidates=[],
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
    ) -> SemanticAnswer:
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
        candidates: list[PlaceCandidate] = []
        seen: set[str] = set()
        for _, inst in ordered:
            if inst.place_id in seen:
                continue
            place = places.get(inst.place_id)
            if place is not None:
                seen.add(inst.place_id)
                candidates.append(place)

        if not candidates:
            return SemanticAnswer(
                intent=IntentType.START_NAVIGATION,
                confidence=0.3,
                needs_clarification=True,
                destination=None,
                candidates=[],
                response_text=result.clarification_question
                or "没有找到该地点，请换个说法。",
            )
        names = "、".join(c.name for c in candidates)
        return SemanticAnswer(
            intent=IntentType.START_NAVIGATION,
            confidence=max(0.4, float(getattr(result.top_candidates[0], "confidence", 0.4))),
            needs_clarification=True,
            destination=None,
            candidates=candidates,
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
            )
        )
    except Exception as err:  # 配置合法但客户端初始化失败
        logger.warning("语义兜底不可用：%s", err)
        return None
    return SemanticIntentService(dictionary, llm)
