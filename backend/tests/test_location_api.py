# -*- coding: utf-8 -*-
"""M7 行为测试：API-05 三点定位估计（docs/24 §8）。"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pathai_backend.map_repository import MapRepository

URL = "/api/v1/locations/estimate"
SAMPLE = "2026-10-01.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _estimate(client, headers, beacons, sequence=1, floor_hint=None, imu=None,
              map_version=SAMPLE) -> dict:
    body: dict = {
        "siteId": "school-building-01",
        "mapVersion": map_version,
        "sequence": sequence,
        "collectedAt": _now(),
        "floorHint": floor_hint,
        "beacons": [
            {"beaconId": b[0], "rssi": b[1], "observedAt": _now()} for b in beacons
        ],
        "imu": imu,
    }
    resp = client.post(URL, json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _reload(app, maps_dir: Path) -> None:
    app.state.map_repository = MapRepository.load(
        map_data_dir=maps_dir, public_base_url="https://api.test"
    )


@pytest.fixture()
def rich_maps(maps_dir, app):
    """样例地图自 C1 起每层自带 3 信标（(5,5)/(10,10)/(5,10)），直接可用。"""
    _reload(app, maps_dir)
    return app


@pytest.fixture()
def two_versions(maps_dir, app):
    """发布第二版地图：触发 MAP_VERSION_CONFLICT 与 latest 选择。"""
    path = maps_dir / "school-building-01" / f"{SAMPLE}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mapVersion"] = "2026-10-02.1"
    data["publishedAt"] = "2026-10-02T08:00:00Z"
    (maps_dir / "school-building-01" / "2026-10-02.1.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    _reload(app, maps_dir)
    return app


class TestAuthAndVersioning:
    def test_unauthenticated_401(self, client):
        resp = client.post(URL, json={})
        assert resp.status_code == 401

    def test_unknown_site_404(self, client, auth_headers):
        resp = client.post(
            URL,
            json={"siteId": "nope", "mapVersion": SAMPLE, "sequence": 1,
                  "collectedAt": _now(), "beacons": [{"beaconId": "B", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "SITE_NOT_FOUND"

    def test_unknown_version_404(self, client, auth_headers):
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": "1999-01-01.1",
                  "sequence": 1, "collectedAt": _now(),
                  "beacons": [{"beaconId": "B", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MAP_NOT_PUBLISHED"

    def test_stale_map_version_409_conflict(self, client, auth_headers, two_versions):
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": SAMPLE,
                  "sequence": 1, "collectedAt": _now(),
                  "beacons": [{"beaconId": "BK-01-001", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "MAP_VERSION_CONFLICT"

    def test_latest_keyword_uses_newest_version(self, client, auth_headers, two_versions):
        body = _estimate(client, auth_headers, [("BK-01-001", -100), ("BK-01-002", -100), ("BK-01-003", -100)],
                         map_version="latest")
        assert body["mapVersion"] == "2026-10-02.1"


class TestTrilateration:
    def test_reliable_estimate_near_true_position(self, client, auth_headers, rich_maps):
        # 真实点 (7,7)，三信标 (5,5)/(10,10)/(5,10)，txPower=-59 反推整数 RSSI
        def rssi_for(d: float) -> int:
            return round(-59 - 20 * math.log10(d))

        obs = [
            ("BK-01-001", rssi_for(math.sqrt(8))),
            ("BK-01-002", rssi_for(math.sqrt(8))),
            ("BK-01-003", rssi_for(math.sqrt(9 + 4))),
        ]
        body = _estimate(client, auth_headers, obs)
        assert body["quality"] == "reliable"
        assert body["position"]["floorId"] == "1"
        # 整数 RSSI 量化会在近距引入亚米~米级偏差，此处只断言邻域正确
        assert abs(body["position"]["x"] - 7.0) < 1.2
        assert abs(body["position"]["y"] - 7.0) < 1.2
        assert body["confidence"] >= 0.8
        assert body["ignoredBeaconIds"] == []
        assert body["algorithmVersion"] == "location-0.1"
        assert body["estimateId"].startswith("loc_")
        assert body["sequence"] == 1

    def test_unknown_beacons_reported_not_used(self, client, auth_headers, rich_maps):
        obs = [("BK-01-001", -62), ("BK-01-002", -62), ("BK-01-003", -62), ("GHOST-9", -40)]
        body = _estimate(client, auth_headers, obs)
        assert body["ignoredBeaconIds"] == ["GHOST-9"]
        assert body["quality"] in ("reliable", "degraded")

    def test_floor_hint_mismatch_beacons_ignored_lost(self, client, auth_headers, rich_maps):
        obs = [("BK-02-001", -50), ("BK-02-002", -50), ("BK-02-003", -50)]
        body = _estimate(client, auth_headers, obs, floor_hint="1")
        assert body["quality"] == "lost"
        assert body["position"] is None
        assert body["ignoredBeaconIds"] == ["BK-02-001", "BK-02-002", "BK-02-003"]

    def test_auto_floor_by_majority_and_rssi(self, client, auth_headers, rich_maps):
        # 两层各 3 条观测数量并列；一层信号更强（RSSI 更大）→ 选一层
        obs = [
            ("BK-01-001", -55), ("BK-01-002", -55), ("BK-01-003", -55),
            ("BK-02-001", -90), ("BK-02-002", -90), ("BK-02-003", -90),
        ]
        body = _estimate(client, auth_headers, obs)
        assert body["position"]["floorId"] == "1"

    def test_auto_floor_by_majority_count(self, client, auth_headers, rich_maps):
        # 一层 3 条、二层 2 条 → 数量多数选一层
        obs = [
            ("BK-01-001", -70), ("BK-01-002", -70), ("BK-01-003", -70),
            ("BK-02-001", -60), ("BK-02-002", -60),
        ]
        body = _estimate(client, auth_headers, obs)
        assert body["position"]["floorId"] == "1"

    def test_fewer_than_three_beacons_lost(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [("BK-01-001", -60), ("BK-01-002", -60)])
        assert body["quality"] == "lost"
        assert body["position"] is None
        assert body["confidence"] == 0.0

    def test_unknown_floor_hint_lost(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [("BK-01-001", -60)], floor_hint="9")
        assert body["quality"] == "lost"
        assert body["position"] is None

    def test_heading_echo_and_normalize(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [("BK-01-001", -60)], imu={"samples": [], "headingDeg": 405.0})
        assert body["headingDeg"] == 45.0
        body = _estimate(client, auth_headers, [("BK-01-001", -60)])
        assert body["headingDeg"] is None

    def test_matched_area_nearest_place(self, client, auth_headers, rich_maps):
        obs = [("BK-01-001", -62), ("BK-01-002", -62), ("BK-01-003", -62)]
        body = _estimate(client, auth_headers, obs)
        assert body["matchedArea"] is not None
        assert set(body["matchedArea"].keys()) == {"placeId", "name"}


class TestSequenceGuard:
    def test_first_sequence_accepted_then_stale_rejected(self, client, auth_headers, rich_maps):
        obs = [("BK-01-001", -60)]
        first = _estimate(client, auth_headers, obs, sequence=5)
        assert first["sequence"] == 5
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": SAMPLE, "sequence": 4,
                  "collectedAt": _now(), "floorHint": None,
                  "beacons": [{"beaconId": "BK-01-001", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "STALE_LOCATION_BATCH"

    def test_equal_sequence_not_stale(self, client, auth_headers, rich_maps):
        obs = [("BK-01-001", -60)]
        _estimate(client, auth_headers, obs, sequence=5)
        again = _estimate(client, auth_headers, obs, sequence=5)
        assert again["sequence"] == 5

    def test_sequences_independent_per_site(self, client, auth_headers, rich_maps, maps_dir):
        # 其他场所无历史：同 sequence 不同 site 不受影响
        path = maps_dir / "school-building-01" / f"{SAMPLE}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["siteId"] = "school-building-02"
        (maps_dir / "school-building-02").mkdir()
        (maps_dir / "school-building-02" / f"{SAMPLE}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        _reload(rich_maps, maps_dir)
        resp = client.post(
            URL,
            json={"siteId": "school-building-02", "mapVersion": SAMPLE, "sequence": 1,
                  "collectedAt": _now(), "floorHint": None,
                  "beacons": [{"beaconId": "BK-01-001", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("sequence", [0, -1])
    def test_invalid_sequence_400(self, client, auth_headers, sequence):
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": SAMPLE,
                  "sequence": sequence, "collectedAt": _now(),
                  "beacons": [{"beaconId": "B", "rssi": -60, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"

    def test_rssi_out_of_range_400(self, client, auth_headers):
        resp = client.post(
            URL,
            json={"siteId": "school-building-01", "mapVersion": SAMPLE,
                  "sequence": 1, "collectedAt": _now(),
                  "beacons": [{"beaconId": "B", "rssi": -10, "observedAt": _now()}]},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_REQUEST"
