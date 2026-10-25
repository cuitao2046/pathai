# -*- coding: utf-8 -*-
"""M7 行为测试：API-05 三点定位估计（docs/24 §8）。

mock 数据使用真实地图包 2026-10-25.1 的信标目录（1 层 52 个、2 层 9 个，
txPower=-10），RSSI 按真实坐标反推。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pathai_backend.map_repository import MapRepository

URL = "/api/v1/locations/estimate"
SAMPLE = "2026-10-25.1"
#: 1 层三点定位用真实信标（坐标取自地图包，局部米制）。
BK_A = ("BK-01-001", -30.25, -75.08)
BK_B = ("BK-01-004", -18.777, -83.987)
BK_C = ("BK-01-005", -36.91, -59.51)
#: 真实信标 txPower（dBm）。
TX_POWER = -10.0
#: 可解算的公共期望点：距三信标 8.8/17.2/14.6m，均 ≥4m（RSSI 上限内）。
EXPECT_P = (-25.0, -68.0)


def _rssi_for(p: tuple[float, float], beacon: tuple[str, float, float]) -> int:
    """按期望点与真实信标坐标反推整数 RSSI（txPower 对数路径损耗模型）。"""
    return round(TX_POWER - 20 * math.log10(math.dist(p, beacon[1:])))


def _obs_at_expect_p() -> list[tuple[str, int]]:
    """三真实信标在期望点的一致观测（几何一致，解算可靠）。"""
    return [(b[0], _rssi_for(EXPECT_P, b)) for b in (BK_A, BK_B, BK_C)]


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
    """真实地图包每层自带信标（1 层 52 / 2 层 9），直接可用。"""
    _reload(app, maps_dir)
    return app


@pytest.fixture()
def two_versions(maps_dir, app):
    """发布第二版地图（基于真实包派生）：触发 MAP_VERSION_CONFLICT 与 latest 选择。

    派生版本 publishedAt 必须晚于真实包（2026-10-25T14:10:01Z），否则 latest 反转。
    """
    path = maps_dir / "school-building-01" / f"{SAMPLE}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["mapVersion"] = "2026-10-26.1"
    data["publishedAt"] = "2026-10-26T08:00:01Z"
    (maps_dir / "school-building-01" / "2026-10-26.1.json").write_text(
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
        body = _estimate(client, auth_headers, [(BK_A[0], -100), (BK_B[0], -100), (BK_C[0], -100)],
                         map_version="latest")
        assert body["mapVersion"] == "2026-10-26.1"


class TestTrilateration:
    def test_reliable_estimate_near_true_position(self, client, auth_headers, rich_maps):
        # 期望点 (-25,-68)：距三真实信标 8.8/17.2/14.6m（均 ≥4m，RSSI 上限内），
        # txPower=-10 反推整数 RSSI 后解算偏差经真实 solver 验证 <1m
        obs = _obs_at_expect_p()
        # 几何前提：三观测点距期望点均 ≥4m（RSSI 上限 -20 对应 3.16m）
        assert min(math.dist(EXPECT_P, b[1:]) for b in (BK_A, BK_B, BK_C)) >= 4.0
        body = _estimate(client, auth_headers, obs)
        assert body["quality"] == "reliable"
        assert body["position"]["floorId"] == "1"
        # 整数 RSSI 量化会引入亚米级偏差，此处只断言邻域正确
        assert abs(body["position"]["x"] - EXPECT_P[0]) < 1.5
        assert abs(body["position"]["y"] - EXPECT_P[1]) < 1.5
        assert body["confidence"] >= 0.8
        assert body["ignoredBeaconIds"] == []
        assert body["algorithmVersion"] == "location-0.1"
        assert body["estimateId"].startswith("loc_")
        assert body["sequence"] == 1

    def test_unknown_beacons_reported_not_used(self, client, auth_headers, rich_maps):
        obs = [*_obs_at_expect_p(), ("GHOST-9", -40)]
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
            *_obs_at_expect_p(),
            ("BK-02-001", -90), ("BK-02-002", -90), ("BK-02-003", -90),
        ]
        body = _estimate(client, auth_headers, obs)
        assert body["position"]["floorId"] == "1"

    def test_auto_floor_by_majority_count(self, client, auth_headers, rich_maps):
        # 一层 3 条、二层 2 条 → 数量多数选一层
        obs = [
            *_obs_at_expect_p(),
            ("BK-02-001", -60), ("BK-02-002", -60),
        ]
        body = _estimate(client, auth_headers, obs)
        assert body["position"]["floorId"] == "1"

    def test_fewer_than_three_beacons_lost(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [(BK_A[0], -60), (BK_B[0], -60)])
        assert body["quality"] == "lost"
        assert body["position"] is None
        assert body["confidence"] == 0.0

    def test_unknown_floor_hint_lost(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [(BK_A[0], -60)], floor_hint="9")
        assert body["quality"] == "lost"
        assert body["position"] is None

    def test_heading_echo_and_normalize(self, client, auth_headers, rich_maps):
        body = _estimate(client, auth_headers, [(BK_A[0], -60)], imu={"samples": [], "headingDeg": 405.0})
        assert body["headingDeg"] == 45.0
        body = _estimate(client, auth_headers, [(BK_A[0], -60)])
        assert body["headingDeg"] is None

    def test_matched_area_nearest_place(self, client, auth_headers, rich_maps):
        obs = _obs_at_expect_p()
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
