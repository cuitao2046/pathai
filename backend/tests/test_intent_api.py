# -*- coding: utf-8 -*-
"""M6 行为测试：API-04 受控意图解析（docs/24 §8）。

mock 数据使用真实地图包 2026-10-25.1 的地点目录（56 地点）。
"""
from __future__ import annotations

import pytest

URL = "/api/v1/intents/parse"

#: 真实地图版本。
REAL_MAP_VERSION = "2026-10-25.1"


def _parse(client, headers, text: str, **overrides) -> dict:
    body = {
        "siteId": "school-building-01",
        "mapVersion": REAL_MAP_VERSION,
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
            json={"siteId": "no-such", "mapVersion": REAL_MAP_VERSION, "text": "去图书馆"},
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
        # 「资源教室」在真实目录中唯一名
        body = _parse(client, auth_headers, "带我去资源教室")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is False
        assert body["destination"] == {
            "placeId": "F1-RM-0003", "name": "资源教室", "floorId": "1"
        }
        assert body["candidates"] == []
        assert body["confidence"] >= 0.9
        assert "资源教室" in body["responseText"]

    def test_longest_match_unique_full_name(self, client, auth_headers):
        # 完整长名称命中唯一（真实数据地点名无楼层前缀，消歧依赖完整名称唯一性）
        body = _parse(client, auth_headers, "带我去美术书法资料室")
        assert body["needsClarification"] is False
        assert body["destination"]["placeId"] == "F1-RM-0009"

    def test_ambiguous_name_needs_clarification(self, client, auth_headers):
        # 「教师办公室」在 1/2 层共 3 处 → 需澄清，候选按 placeId 排序
        body = _parse(client, auth_headers, "带我去教师办公室")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is True
        assert body["destination"] is None
        ids = [c["placeId"] for c in body["candidates"]]
        assert ids == ["F1-RM-0033", "F2-RM-0025", "F2-RM-0031"]
        assert body["confidence"] < 0.9
        assert "教师办公室" in body["responseText"]

    def test_unknown_place_needs_clarification_empty_candidates(self, client, auth_headers):
        body = _parse(client, auth_headers, "带我去火星大厅")
        assert body["intent"] == "start_navigation"
        assert body["needsClarification"] is True
        assert body["destination"] is None
        assert body["candidates"] == []

    def test_candidates_share_contract_shape(self, client, auth_headers):
        body = _parse(client, auth_headers, "去教师办公室")
        assert body["candidates"], "同名多处应产生候选"
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
            {"siteId": "school-building-01", "mapVersion": REAL_MAP_VERSION, "text": ""},
            {"siteId": "school-building-01", "mapVersion": REAL_MAP_VERSION, "text": "好" * 501},
            {"siteId": "school-building-01", "mapVersion": "", "text": "取消"},
            {"siteId": "", "mapVersion": REAL_MAP_VERSION, "text": "取消"},
            {"siteId": "school-building-01", "mapVersion": REAL_MAP_VERSION, "text": "取消", "extra": 1},
        ],
    )
    def test_invalid_request_400(self, client, auth_headers, body):
        resp = client.post(URL, json=body, headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
