# -*- coding: utf-8 -*-
"""M5 行为测试：API-02 地图版本摘要/数据下载 与 API-03 地点查询（docs/24 §8）。

mock 数据使用真实地图包 2026-10-25.1（56 地点），列表类断言从包内动态推导。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

BASE = "/api/v1/sites/school-building-01"
MAP_URL = f"{BASE}/map"
DATA_URL = f"{BASE}/map/data"
PLACES_URL = f"{BASE}/places"

#: 真实地图版本与发布时间（与地图包一致）。
REAL_MAP_VERSION = "2026-10-25.1"
REAL_PUBLISHED_AT = "2026-10-25T14:10:01Z"

#: 真实地图包路径与地点目录（模块级读一次，用于动态推导期望）。
_REAL_MAP_FILE = (
    Path(__file__).resolve().parent.parent
    / "maps_data" / "school-building-01" / f"{REAL_MAP_VERSION}.json"
)
_REAL_PLACES: list[dict] = json.loads(_REAL_MAP_FILE.read_text(encoding="utf-8"))["places"]


class TestAuthRequired:
    @pytest.mark.parametrize("url", [MAP_URL, DATA_URL, PLACES_URL])
    def test_unauthenticated_401(self, client, url):
        resp = client.get(url)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


class TestNotFoundSemantics:
    def test_unknown_site_404_site_not_found(self, client, auth_headers):
        resp = client.get("/api/v1/sites/no-such-site/map", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SITE_NOT_FOUND"

    def test_known_site_without_published_map_404_map_not_published(self, client, auth_headers, app, maps_dir):
        from pathai_backend.map_repository import MapRepository

        (maps_dir / "empty-site").mkdir()
        app.state.map_repository = MapRepository.load(
            map_data_dir=maps_dir, public_base_url="https://api.test"
        )
        resp = client.get("/api/v1/sites/empty-site/map", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"

    def test_unknown_version_404_map_not_published(self, client, auth_headers):
        resp = client.get(f"{MAP_URL}?version=1999-01-01.1", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"


class TestMapVersionEndpoint:
    def test_success_shape(self, client, auth_headers):
        resp = client.get(MAP_URL, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["siteId"] == "school-building-01"
        assert body["mapVersion"] == REAL_MAP_VERSION
        assert body["publishedAt"] == REAL_PUBLISHED_AT
        assert body["coordinateSystem"]["unit"] == "m"
        assert body["coordinateSystem"]["headingReference"] == "map_north"
        assert body["floors"] == [
            {"floorId": "1", "displayName": "一楼", "level": 1},
            {"floorId": "2", "displayName": "二楼", "level": 2},
        ]
        assert body["dataUrl"].startswith("https://")
        assert f"/api/v1/sites/school-building-01/map/data" in body["dataUrl"]
        assert len(body["dataSha256"]) == 64

    def test_etag_header_and_304(self, client, auth_headers):
        first = client.get(MAP_URL, headers=auth_headers)
        etag = first.headers["ETag"]
        assert len(etag) >= 66  # 带引号的 64 位 sha256
        cached = client.get(MAP_URL, headers={**auth_headers, "If-None-Match": etag})
        assert cached.status_code == 304
        assert cached.content == b""

    def test_data_sha256_matches_download(self, client, auth_headers):
        summary = client.get(MAP_URL, headers=auth_headers).json()
        data = client.get(DATA_URL, headers=auth_headers)
        assert data.status_code == 200
        assert data.headers["ETag"] == f'"{summary["dataSha256"]}"'
        import hashlib

        assert hashlib.sha256(data.content).hexdigest() == summary["dataSha256"]

    def test_data_endpoint_returns_full_package(self, client, auth_headers, maps_dir):
        data = client.get(DATA_URL, headers=auth_headers)
        payload = json.loads(data.content)
        assert payload["siteId"] == "school-building-01"
        assert set(payload["floors"].keys()) == {"1", "2"}
        assert payload["places"], "样例应包含地点目录"

    def test_data_endpoint_304(self, client, auth_headers):
        etag = client.get(DATA_URL, headers=auth_headers).headers["ETag"]
        cached = client.get(DATA_URL, headers={**auth_headers, "If-None-Match": etag})
        assert cached.status_code == 304

    def test_weak_etag_validator_accepted(self, client, auth_headers):
        etag = client.get(MAP_URL, headers=auth_headers).headers["ETag"]
        cached = client.get(MAP_URL, headers={**auth_headers, "If-None-Match": f"W/{etag}"})
        assert cached.status_code == 304

    def test_specific_version_served(self, client, auth_headers):
        resp = client.get(f"{MAP_URL}?version={REAL_MAP_VERSION}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["mapVersion"] == REAL_MAP_VERSION


class TestPlacesEndpoint:
    def test_list_all_sorted_by_place_id(self, client, auth_headers):
        resp = client.get(PLACES_URL, headers=auth_headers, params={"limit": 50})
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapVersion"] == REAL_MAP_VERSION
        ids = [item["placeId"] for item in body["items"]]
        # 地点共 56 个，limit 上限 50 → 返回按 placeId 排序的前 50 个
        expected = sorted(p["placeId"] for p in _REAL_PLACES)[:50]
        assert ids == expected
        assert body["nextCursor"] is None

    def test_place_item_shape(self, client, auth_headers):
        items = client.get(PLACES_URL, headers=auth_headers, params={"limit": 50}).json()["items"]
        room = next(i for i in items if i["placeId"] == "F1-RM-0003")
        assert room == {
            "placeId": "F1-RM-0003",
            "name": "资源教室",
            "spokenName": "资源教室",
            "aliases": [],
            "type": "room",
            "floorId": "1",
            "nodeId": "F1-TR-0003",
            "accessible": True,
        }

    def test_query_exact_name_first(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "资源教室", "limit": 50}, headers=auth_headers)
        items = resp.json()["items"]
        assert [i["placeId"] for i in items] == ["F1-RM-0003"]

    def test_query_multi_match_sorted_by_place_id(self, client, auth_headers):
        # 同名多处（1 层 5 间音乐教室）按 placeId 排序返回
        resp = client.get(PLACES_URL, params={"q": "音乐教室", "limit": 50}, headers=auth_headers)
        ids = [i["placeId"] for i in resp.json()["items"]]
        expected = sorted(
            p["placeId"] for p in _REAL_PLACES if "音乐教室" in p["name"]
        )
        assert ids == expected
        assert len(ids) == 5

    def test_query_substring_match(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "教室", "limit": 50}, headers=auth_headers)
        items = resp.json()["items"]
        assert items, "含『教室』的地点应非空"
        assert all("教室" in i["name"] for i in items)
        # 同 rank（全部为包含匹配）内按 placeId 排序
        ids = [i["placeId"] for i in items]
        assert ids == sorted(ids)

    def test_no_result_empty_items_not_error(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "不存在的地点"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_floor_filter(self, client, auth_headers):
        resp = client.get(
            PLACES_URL, params={"floor": "2", "limit": 50}, headers=auth_headers
        )
        items = resp.json()["items"]
        assert items
        assert all(i["floorId"] == "2" for i in items)
        expected = sorted(
            p["placeId"] for p in _REAL_PLACES if p["floorId"] == "2"
        )
        assert [i["placeId"] for i in items] == expected

    def test_type_filter(self, client, auth_headers):
        resp = client.get(
            PLACES_URL, params={"type": "toilet", "limit": 50}, headers=auth_headers
        )
        items = resp.json()["items"]
        assert items
        assert all(i["type"] == "toilet" for i in items)
        expected = sorted(
            p["placeId"] for p in _REAL_PLACES if p["type"] == "toilet"
        )
        assert [i["placeId"] for i in items] == expected

    def test_limit_truncates(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"limit": 2}, headers=auth_headers)
        assert len(resp.json()["items"]) == 2

    @pytest.mark.parametrize("limit", [0, 51, -1])
    def test_invalid_limit_400(self, client, auth_headers, limit):
        resp = client.get(PLACES_URL, params={"limit": limit}, headers=auth_headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_unknown_floor_filter_returns_empty(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"floor": "9"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []
