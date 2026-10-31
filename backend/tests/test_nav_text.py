# -*- coding: utf-8 -*-
"""导航指令文本改写（docs/29 §3）：只换 text，失败回退原文。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pathai_backend.config import Settings
from pathai_backend.map_repository import MapRepository, PublishedMap
from pathai_backend.nav_text import rewrite_route_texts
from pathai_backend.schemas.common import (
    GeoPosition,
    InstructionAction,
    InstructionPriority,
    NavigationInstruction,
    Trigger,
)
from pathai_backend.schemas.nav import RouteInfo
from src.voice_agent.services.dictionary_store import PoiDictionary
from src.voice_agent.services.llm_client import LlmError, LlmResult


class FakeLlm:
    def __init__(self, payload: dict | None = None):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, response_schema=None, schema_name="voice_agent_output"):
        self.calls += 1
        text = json.dumps(self.payload, ensure_ascii=False) if self.payload is not None else "{}"
        return LlmResult(text=text, ttft_ms=8, total_ms=16)


class RaisingLlm:
    def chat(self, messages, response_schema=None, schema_name="voice_agent_output"):
        raise LlmError("boom")


def _published(maps_dir) -> PublishedMap:
    return MapRepository.load(maps_dir, "https://api.test").latest("school-building-01")


def _dictionary(published: PublishedMap) -> PoiDictionary:
    return PoiDictionary(
        {
            "building_name": "教学楼",
            "site_id": "school-building-01",
            "map_version": published.map_version,
            "exported_at": "test",
            "pois": [],
        }
    )


def _route() -> RouteInfo:
    now = datetime.now(timezone.utc)
    segments = [
        NavigationInstruction(
            instructionId="ins_1",
            instructionSequence=1,
            action=InstructionAction.CONTINUE,
            text="请沿当前通道直行 10 米。",
            distanceM=10.0,
            priority=InstructionPriority.NORMAL,
            trigger=Trigger(type="immediate"),
            expiresAt=now,
        ),
        NavigationInstruction(
            instructionId="ins_2",
            instructionSequence=2,
            action=InstructionAction.TURN_LEFT,
            text="前方路口请左转。",
            distanceM=8.0,
            priority=InstructionPriority.TURN,
            trigger=Trigger(type="decision_point", nodeId="F1-TR-0002"),
            expiresAt=now,
        ),
        NavigationInstruction(
            instructionId="ins_3",
            instructionSequence=3,
            action=InstructionAction.ARRIVE,
            text="已到达目的地附近，请留意周围环境。",
            distanceM=0.0,
            priority=InstructionPriority.NORMAL,
            trigger=Trigger(type="decision_point", nodeId="F1-RM-0002"),
            expiresAt=now,
        ),
    ]
    return RouteInfo(
        routeId="route_test",
        mapVersion="2026-10-25.1",
        distanceM=18.0,
        polyline=[
            GeoPosition(x=0.0, y=0.0, floorId="1"),
            GeoPosition(x=1.0, y=1.0, floorId="1"),
        ],
        segments=segments,
    )


def _valid_payload() -> dict:
    return {
        "instructions": [
            {"instructionId": "ins_1", "text": "沿通道直行 10 米。"},
            {"instructionId": "ins_2", "text": "前方路口左转。"},
            {"instructionId": "ins_3", "text": "已到目的地附近，请留意脚下。"},
        ]
    }


def test_system_prompt_mentions_json_for_json_object_mode(maps_dir):
    """火山方舟要求 json_object 模式下提示词必须出现 "json"，否则 400。"""
    from src.voice_agent.config.prompts import navigation_system_prompt

    prompt = navigation_system_prompt(_dictionary(_published(maps_dir)))
    assert "JSON" in prompt


def test_rewrites_texts_and_keeps_route_shape(maps_dir):
    published = _published(maps_dir)
    route = _route()
    llm = FakeLlm(_valid_payload())
    new_route = rewrite_route_texts(
        route, published, Settings(), "女卫生间", llm=llm, dictionary=_dictionary(published)
    )
    assert llm.calls == 1
    assert [s.text for s in new_route.segments] == [
        "沿通道直行 10 米。",
        "前方路口左转。",
        "已到目的地附近，请留意脚下。",
    ]
    for old, new in zip(route.segments, new_route.segments):
        assert old.instructionId == new.instructionId
        assert old.action == new.action
        assert old.distanceM == new.distanceM
        assert old.trigger == new.trigger
        assert old.priority == new.priority


def test_missing_instruction_falls_back(maps_dir):
    published = _published(maps_dir)
    route = _route()
    payload = {"instructions": _valid_payload()["instructions"][:2]}
    new_route = rewrite_route_texts(
        route, published, Settings(), "女卫生间",
        llm=FakeLlm(payload), dictionary=_dictionary(published),
    )
    assert new_route == route


def test_number_loss_falls_back(maps_dir):
    published = _published(maps_dir)
    route = _route()
    payload = {"instructions": _valid_payload()["instructions"]}
    payload["instructions"][0] = {"instructionId": "ins_1", "text": "沿通道直行。"}
    new_route = rewrite_route_texts(
        route, published, Settings(), "女卫生间",
        llm=FakeLlm(payload), dictionary=_dictionary(published),
    )
    assert new_route == route


def test_llm_error_falls_back(maps_dir):
    published = _published(maps_dir)
    route = _route()
    new_route = rewrite_route_texts(
        route, published, Settings(), "女卫生间",
        llm=RaisingLlm(), dictionary=_dictionary(published),
    )
    assert new_route == route


def test_unconfigured_and_no_model_keeps_original(maps_dir):
    published = _published(maps_dir)
    route = _route()
    settings = Settings(llm_base_url="", llm_api_key="", llm_model="")
    assert rewrite_route_texts(
        route, published, settings, "女卫生间", dictionary=_dictionary(published)
    ) == route
