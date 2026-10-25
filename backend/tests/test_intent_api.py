# -*- coding: utf-8 -*-
"""M6 行为测试：API-04 受控意图解析（docs/24 §8）。"""
from __future__ import annotations

import pytest

URL = "/api/v1/intents/parse"


def _parse(client, headers, text: str, **overrides) -> dict:
    body = {
        "siteId": "school-building-01",
        "mapVersion": "2026-10-01.1",
        "text": text,
        "sessionId": None,
        "locale": "zh-CN",
    }
    body.update(overrides)
    resp = client.post(URL, json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestAuthAndLookup:
    def test_unauthenticated_401(self, client):
        resp = client.post(URL, json={"siteId": "s", "mapVersion": "v", "text": "去图书馆"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_unknown_site_404(self, client, auth_headers):
        resp = client.post(
            URL,
            json={"siteId": "no-such", "mapVersion": "2026-10-01.1", "text": "去图书馆"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SITE_NOT_FOUND"

    def test_unpublished_map_version_404(self, client, auth_headers):
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": "1999-01-01.1", "text": "去图书馆"},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"


class TestNavigationIntent:
    def test_unique_match(self, client, auth_headers):
        body = _parse(client, auth_headers, "带我去一楼图书馆")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is False
        assert body["destination"] == {
            "placeId": "library-1f", "name": "一楼图书馆", "floorId": "1"
        }
        assert body["candidates"] == []
        assert body["confidence"] >= 0.9
        assert "一楼图书馆" in body["responseText"]

    def test_longest_alias_disambiguates_floor(self, client, auth_headers):
        body = _parse(client, auth_headers, "带我去二楼图书馆")
        assert body["needsClarification"] is False
        assert body["destination"]["placeId"] == "library-2f"

    def test_ambiguous_alias_needs_clarification(self, client, auth_headers):
        body = _parse(client, auth_headers, "带我去图书馆")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is True
        assert body["destination"] is None
        ids = [c["placeId"] for c in body["candidates"]]
        assert ids == ["library-1f", "library-2f"]
        assert body["confidence"] < 0.9
        assert "图书馆" in body["responseText"]

    def test_unknown_place_needs_clarification_empty_candidates(self, client, auth_headers):
        body = _parse(client, auth_headers, "带我去火星大厅")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is True
        assert body["destination"] is None
        assert body["candidates"] == []

    def test_candidates_share_contract_shape(self, client, auth_headers):
        body = _parse(client, auth_headers, "去图书馆")
        for candidate in body["candidates"]:
            assert set(candidate.keys()) == {"placeId", "name", "floorId"}


class TestControlIntents:
    @pytest.mark.parametrize(
        ("text", "intent"),
        [
            ("取消", "cancel"),
            ("取消导航", "cancel"),
            ("结束导航", "end_navigation"),
            ("我到了", "end_navigation"),
            ("再说一遍", "repeat_instruction"),
            ("重复指令", "repeat_instruction"),
            ("我在哪里", "query_location"),
            ("我的位置在哪", "query_location"),
            ("最近的电梯", "search_facility"),
        ],
    )
    def test_control_intent_recognized(self, client, auth_headers, text, intent):
        body = _parse(client, auth_headers, text)
        assert body["intent"] == intent
        assert body["needsClarification"] is False
        assert body["destination"] is None
        assert body["candidates"] == []
        assert body["confidence"] >= 0.9
        assert body["responseText"]

    def test_unknown_intent(self, client, auth_headers):
        body = _parse(client, auth_headers, "今天天气怎么样")
        assert body["intent"] == "unknown"
        assert body["needsClarification"] is True
        assert body["confidence"] < 0.5


class TestContractDetails:
    def test_intent_id_prefixed_and_unique(self, client, auth_headers):
        first = _parse(client, auth_headers, "取消")
        second = _parse(client, auth_headers, "取消")
        assert first["intentId"].startswith("intent_")
        assert first["intentId"] != second["intentId"]

    def test_session_id_accepted_and_ignored(self, client, auth_headers):
        body = _parse(client, auth_headers, "取消", sessionId="nav_abc123")
        assert body["intent"] == "cancel"

    @pytest.mark.parametrize(
        "body",
        [
            {"siteId": "school-building-01", "mapVersion": "2026-10-01.1", "text": ""},
            {"siteId": "school-building-01", "mapVersion": "2026-10-01.1", "text": "好" * 501},
            {"siteId": "school-building-01", "mapVersion": "", "text": "取消"},
            {"siteId": "", "mapVersion": "2026-10-01.1", "text": "取消"},
            {"siteId": "school-building-01", "mapVersion": "2026-10-01.1", "text": "取消", "extra": 1},
        ],
    )
    def test_invalid_request_400(self, client, auth_headers, body):
        resp = client.post(URL, json=body, headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
