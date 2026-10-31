# -*- coding: utf-8 -*-
"""第一阶段语义兜底（docs/29）：触发、映射、类别就近与失败回退。"""
from __future__ import annotations

import json

import pytest

from pathai_backend import semantic_intent
from pathai_backend.config import Settings
from pathai_backend.map_repository import MapRepository
from pathai_backend.schemas.intent import IntentLocation
from src.voice_agent.services.dictionary_store import PoiDictionary
from src.voice_agent.services.llm_client import LlmError, LlmResult

REAL_MAP_VERSION = "2026-10-25.1"


class FakeLlm:
    def __init__(self, payload: dict | None = None, text: str | None = None):
        self.payload = payload
        self.text = text
        self.calls = 0

    def chat(self, messages, response_schema=None, schema_name="voice_agent_output"):
        self.calls += 1
        body = self.text if self.text is not None else json.dumps(self.payload, ensure_ascii=False)
        return LlmResult(text=body, ttft_ms=12, total_ms=24)


class RaisingLlm:
    def chat(self, messages, response_schema=None, schema_name="voice_agent_output"):
        raise LlmError("boom")


def _published(maps_dir):
    return MapRepository.load(maps_dir, "https://api.test").latest("school-building-01")


def _dictionary(published, version: str | None = None) -> PoiDictionary:
    names = ("男卫生间", "女卫生间", "卫生室")
    pois = []
    for name in names:
        places = [p for p in published.pkg.places if p.name == name]
        pois.append(
            {
                "standard_name": name,
                "category": "facility",
                "is_public_utility": True,
                "aliases": ["卫生间", "厕所"] if "卫生" in name else [],
                "instances": [
                    {
                        "floor": f"{p.floorId}F",
                        "floor_id": p.floorId,
                        "place_id": p.placeId,
                        "node_id": p.nodeId,
                        "zone_hint": "",
                    }
                    for p in places
                ],
            }
        )
    return PoiDictionary(
        {
            "building_name": "教学楼",
            "site_id": "school-building-01",
            "map_version": version or published.map_version,
            "exported_at": "test",
            "pois": pois,
        }
    )


def _node_xy(published, node_id: str) -> tuple[float, float]:
    floor = published.pkg.floors["1"]
    for node in floor.topology.nodes:
        if node.id == node_id:
            return node.coordinates
    raise AssertionError(f"node not found: {node_id}")


def _ambiguous_payload() -> dict:
    return {
        "intent_type": "navigate_to_destination",
        "resolution_status": "ambiguous_concepts",
        "top_candidates": [
            {"standard_name": "男卫生间", "match_type": "exact_or_synonym",
             "confidence": 0.8, "reasoning": "类别词"},
            {"standard_name": "女卫生间", "match_type": "exact_or_synonym",
             "confidence": 0.8, "reasoning": "类别词"},
        ],
        "clarification_question": "请问去男卫生间还是女卫生间？",
    }


class TestDestinationResolution:
    def test_nearest_instance_wins_with_location(self, maps_dir):
        published = _published(maps_dir)
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(_ambiguous_payload()))
        x, y = _node_xy(published, "F1-TR-0002")
        answer = service.parse(
            "我要去卫生间",
            published,
            IntentLocation(x=x, y=y, floorId="1", confidence=0.9, headingDeg=0),
        )
        assert answer is not None
        assert answer.needs_clarification is False
        assert answer.destination is not None
        assert answer.destination.placeId == "F1-RM-0002"
        assert "女卫生间" in answer.response_text

    def test_close_candidates_ask_for_clarification(self, maps_dir):
        published = _published(maps_dir)
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(_ambiguous_payload()))
        x1, y1 = _node_xy(published, "F1-TR-0002")
        x2, y2 = _node_xy(published, "F1-TR-0004")
        answer = service.parse(
            "我要去卫生间",
            published,
            IntentLocation(x=(x1 + x2) / 2, y=(y1 + y2) / 2, floorId="1", confidence=0.9),
        )
        assert answer is not None
        assert answer.needs_clarification is True
        assert answer.destination is None
        assert len(answer.candidates) >= 2
        assert answer.response_text

    def test_without_location_returns_candidates(self, maps_dir):
        published = _published(maps_dir)
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(_ambiguous_payload()))
        answer = service.parse("我要去卫生间", published, None)
        assert answer is not None
        assert answer.needs_clarification is True
        assert {c.name for c in answer.candidates} <= {"男卫生间", "女卫生间"}

    def test_not_found_still_answers(self, maps_dir):
        published = _published(maps_dir)
        payload = {
            "intent_type": "navigate_to_destination",
            "resolution_status": "not_found_in_building",
            "top_candidates": [],
            "clarification_question": "楼内没有游泳馆，您是要去别的地方吗？",
        }
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(payload))
        answer = service.parse("我要去游泳馆", published, None)
        assert answer is not None
        assert answer.needs_clarification is True
        assert answer.response_text.startswith("楼内没有")
        assert answer.candidates == []


class TestLocationAnswer:
    def test_direct_speech_is_returned(self, maps_dir):
        published = _published(maps_dir)
        payload = {
            "intent_type": "query_where_am_i",
            "direct_response_speech": "你在一楼中段走道，本楼未铺设盲道，前方 3 点钟有门。",
        }
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(payload))
        x, y = _node_xy(published, "F1-TR-0002")
        answer = service.parse(
            "我现在在什么地方",
            published,
            IntentLocation(x=x, y=y, floorId="1", confidence=0.9, headingDeg=0),
        )
        assert answer is not None
        assert answer.intent.value == "query_location"
        assert answer.destination is None
        assert "一楼" in answer.response_text

    def test_without_location_does_not_answer(self, maps_dir):
        published = _published(maps_dir)
        payload = {"intent_type": "query_where_am_i", "direct_response_speech": "你在一楼。"}
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(payload))
        assert service.parse("我在什么地方", published, None) is None


class TestGuards:
    def test_llm_error_returns_none(self, maps_dir):
        published = _published(maps_dir)
        service = semantic_intent.SemanticIntentService(_dictionary(published), RaisingLlm())
        assert service.parse("我要去卫生间", published, None) is None

    def test_stale_dictionary_returns_none(self, maps_dir):
        published = _published(maps_dir)
        stale = _dictionary(published, version="1999-01-01.1")
        service = semantic_intent.SemanticIntentService(stale, FakeLlm(_ambiguous_payload()))
        assert service.parse("我要去卫生间", published, None) is None

    def test_unsupported_intent_returns_none(self, maps_dir):
        published = _published(maps_dir)
        payload = {"intent_type": "navigation_control", "direct_response_speech": "好的。"}
        service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(payload))
        assert service.parse("暂停一下", published, None) is None


def test_router_uses_semantic_answer(client, auth_headers, maps_dir, monkeypatch):
    published = _published(maps_dir)
    payload = {
        "intent_type": "navigate_to_destination",
        "resolution_status": "resolved_unique_concept",
        "top_candidates": [
            {"standard_name": "卫生室", "match_type": "exact_or_synonym",
             "confidence": 0.95, "reasoning": "医务室别名"},
        ],
    }
    service = semantic_intent.SemanticIntentService(_dictionary(published), FakeLlm(payload))
    monkeypatch.setattr(semantic_intent, "get_semantic_service", lambda settings: service)

    resp = client.post(
        "/api/v1/intents/parse",
        json={
            "siteId": "school-building-01",
            "mapVersion": REAL_MAP_VERSION,
            "text": "我要去拿药",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "start_navigation"
    assert body["destination"]["placeId"] == "F1-RM-0039"
    assert body["needsClarification"] is False
