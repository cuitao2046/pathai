# -*- coding: utf-8 -*-
"""API-05 查周边：把定位坐标转换为 8m 内可播报要素（spec REQ-VOICE-02）。

复用语音链路同一套空间索引与自我中心转换，保证「查周边」与语音直答对
门/电梯/楼梯/风险点/柱子的口径一致；只输出名称、时钟方向、距离与提示，
不暴露拓扑编号或绝对坐标。
"""
from __future__ import annotations

from src.voice_agent.services.context_builder import (
    SpatialIndex,
    build_spatial_context,
)

from .map_repository import PublishedMap
from .schemas.common import TRUSTED_QUALITIES, GeoPosition, LocationQuality
from .schemas.location import SurroundingElement

#: 地图包不可变：按内容摘要缓存空间索引，避免每次定位重解析整包。
_INDEX_CACHE: dict[tuple[str, str, str], SpatialIndex] = {}


def _index(published: PublishedMap) -> SpatialIndex:
    key = (published.site_id, published.map_version, published.sha256)
    cached = _INDEX_CACHE.get(key)
    if cached is None:
        cached = SpatialIndex.from_package(published.data)
        if len(_INDEX_CACHE) >= 4:  # 只保留最近几个版本，避免切版后内存增长
            _INDEX_CACHE.clear()
        _INDEX_CACHE[key] = cached
    return cached


def nearby_surroundings(
    published: PublishedMap,
    position: GeoPosition | None,
    quality: LocationQuality | str,
    heading_deg: float | None,
) -> list[SurroundingElement]:
    """可信定位下返回周边要素（按距离升序，最多 8 条）；否则返回空清单。"""
    if quality not in TRUSTED_QUALITIES or position is None or not position.floorId:
        return []
    context = build_spatial_context(
        _index(published),
        position.floorId,
        position.x,
        position.y,
        heading_deg if heading_deg is not None else 0.0,
    )
    return [
        SurroundingElement(
            name=element.name,
            elementType=element.element_type,
            clockDirection=element.clock_direction,
            distanceMeters=element.distance_meters,
            accessibilityNote=element.accessibility_note,
        )
        for element in context.nearby_elements
    ]
