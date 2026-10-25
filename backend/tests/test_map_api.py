# -*- coding: utf-8 -*-
"""M5 行为测试：API-02 地图版本摘要/数据下载 与 API-03 地点查询（docs/24 §8）。"""
from __future__ import annotations

import json

import pytest

BASE = "/api/v1/sites/school-building-01"
MAP_URL = f"{BASE}/map"
DATA_URL = f"{BASE}/map/data"
PLACES_URL = f"{BASE}/places"


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
        assert body["mapVersion"] == "2026-10-01.1"
        assert body["publishedAt"] == "2026-10-01T08:00:00Z"
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
        resp = client.get(f"{MAP_URL}?version=2026-10-01.1", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["mapVersion"] == "2026-10-01.1"


class TestPlacesEndpoint:
    def test_list_all_sorted_by_place_id(self, client, auth_headers):
        resp = client.get(PLACES_URL, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["mapVersion"] == "2026-10-01.1"
        ids = [item["placeId"] for item in body["items"]]
        assert ids == sorted(ids)
        assert body["nextCursor"] is None

    def test_place_item_shape(self, client, auth_headers):
        items = client.get(PLACES_URL, headers=auth_headers).json()["items"]
        library = next(i for i in items if i["placeId"] == "library-1f")
        assert library == {
            "placeId": "library-1f",
            "name": "一楼图书馆",
            "spokenName": "一楼图书馆",
            "aliases": ["图书馆", "一楼图书室"],
            "type": "library",
            "floorId": "1",
            "nodeId": "F1-TR-0001",
            "accessible": True,
        }

    def test_query_exact_name_first(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "二楼图书馆"}, headers=auth_headers)
        items = resp.json()["items"]
        assert items[0]["placeId"] == "library-2f"

    def test_query_alias_match(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "图书室"}, headers=auth_headers)
        ids = [i["placeId"] for i in resp.json()["items"]]
        assert "library-1f" in ids and "library-2f" in ids

    def test_query_substring_match(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "图书馆"}, headers=auth_headers)
        ids = [i["placeId"] for i in resp.json()["items"]]
        assert ids == ["library-1f", "library-2f"]

    def test_no_result_empty_items_not_error(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"q": "不存在的地点"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_floor_filter(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"floor": "2"}, headers=auth_headers)
        ids = [i["placeId"] for i in resp.json()["items"]]
        assert ids == ["library-2f", "study-room-2f"]

    def test_type_filter(self, client, auth_headers):
        resp = client.get(PLACES_URL, params={"type": "activity"}, headers=auth_headers)
        ids = [i["placeId"] for i in resp.json()["items"]]
        assert ids == ["study-room-2f"]

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
