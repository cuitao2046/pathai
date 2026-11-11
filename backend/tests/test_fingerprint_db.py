# -*- coding: utf-8 -*-
"""指纹库：入库版本化/幂等/合并冲突、active、匹配与降级、定位链路开关。

覆盖范围（对应本轮需求一/二）：
- 每次有效入库生成不可变版本并可追溯（条数/点位数/冲突/操作人/设备/版本）；
- 同一内容重复入库幂等：不新增版本、不切 active；
- 增量合并：新键插入、同键变化计冲突、本批未覆盖的点保留；
- 指纹匹配成功走指纹库，未启用/无库/阈值不满足时回退三点定位且行为与旧版一致。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from pathai_backend.app import create_app
from pathai_backend.database import get_engine
from pathai_backend.models import Base

ESTIMATE_URL = "/api/v1/locations/estimate"
INGEST_URL = "/api/v1/fingerprint-db/ingest"

#: 样例地图包（conftest maps_dir）中的 1F 三枚真实信标。
BEACON_1 = ("BK-01-001", 10101)
BEACON_2 = ("BK-01-004", 10104)
BEACON_3 = ("BK-01-005", 10105)
UUID = "B9407F30-F5F8-466E-AFF9-25556B57FE6D"
POINT_XY = [-25.0, -68.0]
#: 与样例地图几何一致的三点定位观测（期望点 (-25,-68)，A=-71、n=2 反推）。
RSSI_TRILAT = (-91, -96, -94)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _beacons(rssi: tuple[int, int, int]) -> list[dict]:
    return [
        {
            "uuid": UUID,
            "major": 1,
            "minor": minor,
            "rssi": value,
            "txPower": -52,
            "accuracy": 1.0,
        }
        for (_, minor), value in zip((BEACON_1, BEACON_2, BEACON_3), rssi)
    ]


def _record(
    fp_id: str,
    rssi: tuple[int, int, int],
    *,
    coords=None,
    ts="2026-11-09T10:00:00Z",
    grid: str | None = None,
    params: str | None = None,
    floor: int = 1,
):
    record = {
        "fpId": fp_id,
        "floor": floor,
        "coordinates": coords if coords is not None else POINT_XY,
        "relCoordinates": None,
        "zoneId": None,
        "zoneName": None,
        "anchor": None,
        "regionType": "normal",
        "captureIndex": 1,
        "collectedAt": ts,
        "beaconCount": 3,
        "beacons": _beacons(rssi),
    }
    if grid is not None:
        record["gridVersion"] = grid
    if params is not None:
        record["paramsVersion"] = params
    return record


def _payload(
    records: list[dict],
    *,
    deletions: list[dict] | None = None,
    mode: str | None = None,
    replace_floors: list[str] | None = None,
    schema: str = "1.2.0",
) -> dict:
    payload = {
        "schemaVersion": schema,
        "type": "ble_fingerprint_collection",
        "venueId": "school-building-01",
        "venueName": "school-building-01",
        "dataset": "routes",
        "exportedAt": _now(),
        "appVersion": "1.0.0",
        "operator": "tester",
        "device": {"model": "test", "system": "test", "platform": "devtools"},
        "beaconConfig": {"uuid": UUID, "majors": [1]},
        "exportScope": "all",
        "zones": [],
        "recordCount": len(records),
        "fpCount": len({r["fpId"] for r in records}),
        "records": records,
        "deletions": deletions or [],
    }
    if mode is not None:
        payload["mode"] = mode
    if replace_floors is not None:
        payload["replaceFloors"] = replace_floors
    return payload


@pytest.fixture()
def fp_client(settings, monkeypatch):
    from pathai_backend.routers import calibration as cal_router
    from pathai_backend.routers import fingerprint_db as fp_router
    from tests.conftest import FakeWechatClient
    from tests.test_calibration_api import _as_ready_admin

    cal_router._LAST_WRITE.clear()
    # 限流由校准控制台用例覆盖；功能用例不因进程内 5s 窗口互相干扰。
    monkeypatch.setattr(fp_router, "_rate_limit", lambda key, interval_s: None)
    application = create_app(settings)
    application.state.wechat_client = FakeWechatClient({"code-ok": "openid-FP"})
    Base.metadata.create_all(get_engine())
    with TestClient(application, raise_server_exceptions=False) as client:
        # ingest/rollback 已收敛为 admin 会话 + CSRF；包装器自动补 CSRF 头
        yield _as_ready_admin(client)


@pytest.fixture()
def fp_headers(fp_client) -> dict[str, str]:
    resp = fp_client.post("/api/v1/auth/wechat", json={"code": "code-ok"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def _ingest(client, records: list[dict], file_name: str = "fp.json") -> dict:
    resp = client.post(
        INGEST_URL,
        json={"fileName": file_name, "content": _json_dump(_payload(records)), "operator": "tester"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _json_dump(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _ingest_payload(client, payload: dict, file_name: str = "fp.json"):
    return client.post(
        INGEST_URL,
        json={
            "fileName": file_name,
            "content": _json_dump(payload),
            "operator": "tester",
        },
    )


def _versions(client) -> dict:
    resp = client.get("/api/v1/fingerprint-db/versions?limit=100")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _manifest(client, **params) -> dict:
    resp = client.get("/api/v1/fingerprint-db/manifest", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _beacon_obs() -> list[tuple[str, int]]:
    return [(BEACON_1[0], RSSI_TRILAT[0]), (BEACON_2[0], RSSI_TRILAT[1]), (BEACON_3[0], RSSI_TRILAT[2])]


def _estimate(client, headers, beacons, sequence: int, *, use_fp: bool | None = None) -> dict:
    body: dict = {
        "siteId": "school-building-01",
        "mapVersion": "latest",
        "sequence": sequence,
        "collectedAt": _now(),
        "beacons": [
            {"beaconId": beacon_id, "rssi": rssi, "observedAt": _now()}
            for beacon_id, rssi in beacons
        ],
    }
    if use_fp is not None:
        body["useFingerprintDb"] = use_fp
    resp = client.post(ESTIMATE_URL, json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestIngest:
    def test_creates_traceable_version(self, fp_client):
        summary = _ingest(
            fp_client,
            [
                _record(
                    "G1-001",
                    (-60, -65, -70),
                    grid="fp-grid-2026.11",
                    params="loc-20261109-abcd",
                )
            ],
        )
        assert summary["version"].startswith("fpl-")
        assert summary["recordCount"] == 1
        assert summary["fpCount"] == 1
        assert summary["insertedCount"] == 1
        assert summary["conflictCount"] == 0
        assert summary["operator"] == "tester"
        assert summary["device"] == {"model": "test", "system": "test", "platform": "devtools"}
        assert summary["mapVersion"] is None
        # 导出载荷没有顶层版本字段时从记录中取（参数/网格版本可追溯）
        assert summary["gridVersion"] == "fp-grid-2026.11"
        assert summary["paramsVersion"] == "loc-20261109-abcd"
        assert summary["active"] is True and summary["duplicated"] is False

        active = fp_client.get("/api/v1/fingerprint-db/active").json()["active"]
        assert active["version"] == summary["version"]
        assert active["recordCount"] == 1

    def test_same_content_is_idempotent(self, fp_client):
        records = [_record("G1-001", (-60, -65, -70))]
        first = _ingest(fp_client, records)
        second = _ingest(fp_client, records, file_name="again.json")
        assert second["duplicated"] is True
        assert second["version"] == first["version"]
        versions = fp_client.get("/api/v1/fingerprint-db/versions").json()
        assert len(versions["items"]) == 1
        assert versions["active"]["version"] == first["version"]

    def test_idempotency_key_is_scoped_per_site(self, fp_client):
        """同内容入库到不同场所各自成版本，不跨场所判重（评审第二批）。"""
        from pathai_backend import fingerprint_library as svc
        from pathai_backend.database import session_scope

        content = _json_dump(_payload([_record("G1-001", (-60, -65, -70))]))
        with session_scope() as db:
            first = svc.ingest_export(
                db, site_id="site-a", file_name="a.json", content=content
            )
            db.commit()
        with session_scope() as db:
            second = svc.ingest_export(
                db, site_id="site-b", file_name="b.json", content=content
            )
            db.commit()
        assert first["duplicated"] is False
        assert second["duplicated"] is False
        assert second["version"] != first["version"]

    def test_incremental_merge_keeps_old_points_and_counts_conflicts(self, fp_client):
        first = _ingest(
            fp_client,
            [
                _record("G1-001", (-60, -65, -70)),
                _record("G1-002", (-70, -72, -75), coords=[-10.0, -10.0]),
            ],
        )
        second = _ingest(
            fp_client,
            [
                # 同键内容变化 → 冲突
                _record("G1-001", (-62, -66, -71)),
                # 新点位 → 插入；G1-002 未出现在本批次但必须保留
                _record("G1-003", (-80, -82, -85), coords=[-5.0, -5.0]),
            ],
        )
        assert second["version"] != first["version"]
        assert second["recordCount"] == 3
        assert second["fpCount"] == 3
        assert second["insertedCount"] == 1
        assert second["conflictCount"] == 1
        versions = fp_client.get("/api/v1/fingerprint-db/versions").json()
        assert versions["active"]["version"] == second["version"]
        assert {item["version"] for item in versions["items"]} == {
            first["version"],
            second["version"],
        }

    def test_rejects_invalid_payload(self, fp_client):
        bad_type = _payload([_record("G1-001", (-60, -65, -70))])
        bad_type["type"] = "positioning_calibration_static"
        resp = fp_client.post(
            INGEST_URL, json={"fileName": "x.json", "content": _json_dump(bad_type)}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_TYPE"

        bad_rssi = _payload([_record("G1-001", (-5, -65, -70))])
        resp = fp_client.post(
            INGEST_URL, json={"fileName": "x.json", "content": _json_dump(bad_rssi)}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_RSSI"


class TestMatchAndFallback:
    def _seed_library(self, client) -> str:
        summary = _ingest(
            client,
            [
                _record("G1-001", RSSI_TRILAT),
                _record("G1-002", (-70, -70, -70), coords=[-10.0, -10.0]),
            ],
        )
        return summary["version"]

    def test_query_returns_matched_point(self, fp_client):
        version = self._seed_library(fp_client)
        resp = fp_client.post(
            "/api/v1/fingerprint-db/query",
            json={
                "siteId": "school-building-01",
                "mapVersion": "latest",
                "floorHint": "1",
                "beacons": [
                    {"beaconId": beacon_id, "rssi": rssi} for beacon_id, rssi in _beacon_obs()
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] is True
        assert body["libraryVersion"] == version
        assert body["fpId"] == "G1-001"
        assert body["position"] == {"x": POINT_XY[0], "y": POINT_XY[1], "floorId": "1"}
        assert body["confidence"] > 0.9

    def test_query_reports_reason_without_library(self, fp_client):
        resp = fp_client.post(
            "/api/v1/fingerprint-db/query",
            json={
                "siteId": "school-building-01",
                "mapVersion": "latest",
                "beacons": [
                    {"beaconId": beacon_id, "rssi": rssi} for beacon_id, rssi in _beacon_obs()
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] is False
        assert body["reason"] == "no_active_library"

    def test_estimate_off_state_is_unchanged(self, fp_client, fp_headers):
        """未启用开关：不触达指纹库，响应字段与旧版一致（新增字段取默认值）。"""
        self._seed_library(fp_client)
        beacons = _beacon_obs()
        off = _estimate(fp_client, fp_headers, beacons, sequence=1)
        assert off["positionSource"] == "trilateration"
        assert off["fingerprint"] is None
        assert off["fallbackReason"] is None
        assert off["algorithmVersion"] == "location-0.3"
        assert off["position"] is not None

    def test_estimate_uses_fingerprint_when_enabled(self, fp_client, fp_headers):
        version = self._seed_library(fp_client)
        beacons = _beacon_obs()
        body = _estimate(fp_client, fp_headers, beacons, sequence=1, use_fp=True)
        assert body["positionSource"] == "fingerprint"
        assert body["algorithmVersion"] == "fingerprint-0.1"
        assert body["fallbackReason"] is None
        assert body["fingerprint"]["libraryVersion"] == version
        assert body["fingerprint"]["fpId"] == "G1-001"
        assert body["position"] == {"x": POINT_XY[0], "y": POINT_XY[1], "floorId": "1"}
        assert body["quality"] == "reliable"

    def test_estimate_falls_back_when_no_library(self, fp_client, fp_headers):
        beacons = _beacon_obs()
        off = _estimate(fp_client, fp_headers, beacons, sequence=1)
        on = _estimate(fp_client, fp_headers, beacons, sequence=2, use_fp=True)
        assert on["positionSource"] == "trilateration"
        assert on["fallbackReason"] == "no_active_library"
        # 降级后与关闭状态解算结果完全一致
        assert on["position"] == off["position"]
        assert on["quality"] == off["quality"]
        assert on["confidence"] == off["confidence"]

    def test_estimate_falls_back_when_match_is_ambiguous(self, fp_client, fp_headers):
        # 两点观测向量完全相同 → 无法区分（margin=0），必须回退三点定位
        _ingest(
            fp_client,
            [
                _record("G1-001", RSSI_TRILAT),
                _record("G1-002", RSSI_TRILAT, coords=[-10.0, -10.0]),
            ],
        )
        beacons = _beacon_obs()
        body = _estimate(fp_client, fp_headers, beacons, sequence=1, use_fp=True)
        assert body["positionSource"] == "trilateration"
        assert body["fallbackReason"] == "ambiguous"


class TestDeletionSemantics:
    """显式墓碑删除：版本可追溯、幂等、分批入库不误删、整层换采可控。"""

    def _seed(self, client) -> dict:
        return _ingest(
            client,
            [
                _record("G1-001", RSSI_TRILAT),
                _record("G1-002", (-70, -72, -75), coords=[-10.0, -10.0]),
            ],
        )

    def _tombstone(self, client, fp_id: str, collected_at: str = "2026-11-09T10:00:00Z") -> dict:
        items = {item["fpId"]: item for item in _manifest(client)["items"]}
        item = items[fp_id]
        return {
            "fpId": item["fpId"],
            "collectedAt": item["collectedAt"],
            "contentSha256": item["contentSha256"],
        }

    def test_tombstone_creates_version_and_removes_vector(self, fp_client):
        first = self._seed(fp_client)
        payload = _payload([], deletions=[self._tombstone(fp_client, "G1-001")])
        resp = _ingest_payload(fp_client, payload)
        assert resp.status_code == 200, resp.text
        summary = resp.json()
        assert summary["version"] != first["version"]
        assert summary["recordCount"] == 1
        assert summary["fpCount"] == 1
        assert summary["insertedCount"] == 0
        assert summary["updatedCount"] == 0
        assert summary["deletedCount"] == 1
        assert summary["retainedCount"] == 1
        assert summary["previousCount"] == 2
        # 四类计数可复算：recordCount = retained + inserted + updated
        assert summary["recordCount"] == (
            summary["retainedCount"] + summary["insertedCount"] + summary["updatedCount"]
        )
        assert summary["recordCount"] == (
            summary["previousCount"] - summary["deletedCount"] + summary["insertedCount"] + summary["updatedCount"]
        )

        # 旧向量不再命中：G1-001 已删，观测向量只可能匹配 G1-002（不重叠）
        match = fp_client.post(
            "/api/v1/fingerprint-db/query",
            json={
                "siteId": "school-building-01",
                "mapVersion": "latest",
                "beacons": [
                    {"beaconId": beacon_id, "rssi": rssi} for beacon_id, rssi in _beacon_obs()
                ],
            },
        ).json()
        assert match["matched"] is False
        assert _manifest(fp_client)["recordCount"] == 1

    def test_tombstone_replay_is_idempotent(self, fp_client):
        self._seed(fp_client)
        deletion = self._tombstone(fp_client, "G1-001")
        first = _ingest_payload(fp_client, _payload([], deletions=[deletion])).json()
        count_after_first = len(_versions(fp_client)["items"])

        # 同一导出文件重放：内容哈希相同 → 复用版本
        again = _ingest_payload(fp_client, _payload([], deletions=[deletion])).json()
        assert again["duplicated"] is True
        assert again["version"] == first["version"]
        assert len(_versions(fp_client)["items"]) == count_after_first

        # 换一份包含同一墓碑的文件：无实际变更 → 不产生空版本
        noop = _ingest_payload(
            fp_client,
            _payload(
                [_record("G1-002", (-70, -72, -75), coords=[-10.0, -10.0])],
                deletions=[deletion],
            ),
        ).json()
        assert noop["duplicated"] is True
        assert noop["noChanges"] is True
        assert noop["version"] == first["version"]
        assert len(_versions(fp_client)["items"]) == count_after_first

    def test_delete_then_recollect_same_point(self, fp_client):
        # 单条采坏：删旧键 + 同 fpId 新时间戳重采，旧向量不得残留
        _ingest(fp_client, [_record("G1-001", RSSI_TRILAT)])
        old = _manifest(fp_client)["items"][0]
        new = _record("G1-001", (-60, -65, -70), ts="2026-11-09T10:05:00Z")
        summary = _ingest_payload(
            fp_client,
            _payload(
                [new],
                deletions=[
                    {
                        "fpId": old["fpId"],
                        "collectedAt": old["collectedAt"],
                        "contentSha256": old["contentSha256"],
                    }
                ],
            ),
        ).json()
        assert summary["recordCount"] == 1
        assert summary["deletedCount"] == 1
        assert summary["insertedCount"] == 1
        assert summary["retainedCount"] == 0
        items = _manifest(fp_client)["items"]
        assert len(items) == 1
        assert items[0]["collectedAt"] == "2026-11-09T10:05:00Z"
        assert items[0]["contentSha256"] != old["contentSha256"]

    def test_staged_ingest_does_not_delete_absent_records(self, fp_client):
        _ingest(fp_client, [_record("G1-001", (-60, -65, -70), floor=1)])
        second = _ingest(fp_client, [_record("G2-001", (-70, -72, -75), floor=2)])
        assert second["deletedCount"] == 0
        assert second["recordCount"] == 2
        assert second["previousCount"] == 1
        # 再入库一层，仍不误删已有记录
        third = _ingest(fp_client, [_record("G3-001", (-80, -82, -85), floor=3)])
        assert third["deletedCount"] == 0
        assert third["recordCount"] == 3

    def test_replace_floor_is_explicit_and_scoped(self, fp_client):
        _ingest(
            fp_client,
            [
                _record("G1-001", (-60, -65, -70), floor=1),
                _record("G1-002", (-62, -66, -71), coords=[-10.0, -10.0], floor=1),
                _record("G2-001", (-70, -72, -75), floor=2),
            ],
        )
        # 未声明 mode 的普通入库：只增改，G1-002 必须保留
        merge = _ingest(fp_client, [_record("G1-001", (-61, -65, -70), floor=1)])
        assert merge["deletedCount"] == 0
        assert merge["recordCount"] == 3

        # 显式整层替换：仅 1F 中未重新采到的 G1-002 被删除
        payload = _payload(
            [_record("G1-001", (-63, -67, -72), floor=1)],
            mode="replaceFloor",
            replace_floors=["1"],
        )
        resp = _ingest_payload(fp_client, payload)
        assert resp.status_code == 200, resp.text
        replaced = resp.json()
        assert replaced["mode"] == "replaceFloor"
        assert replaced["replaceFloors"] == ["1"]
        assert replaced["deletedCount"] == 1
        assert replaced["updatedCount"] == 1
        assert replaced["retainedCount"] == 1
        assert replaced["recordCount"] == 2
        assert {item["fpId"] for item in _manifest(fp_client)["items"]} == {"G1-001", "G2-001"}

    def test_rejects_implicit_deletion_inputs(self, fp_client):
        self._seed(fp_client)
        # 整层替换必须显式给楼层
        resp = _ingest_payload(fp_client, _payload([], mode="replaceFloor"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MISSING_REPLACE_FLOORS"
        # 整层替换与墓碑互斥（避免两种删除语义叠加）
        resp = _ingest_payload(
            fp_client,
            _payload([], mode="replaceFloor", replace_floors=["1"], deletions=[self._tombstone(fp_client, "G1-001")]),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_MODE_COMBINATION"
        # 墓碑与同批记录指向同一键 → 显式冲突
        resp = _ingest_payload(
            fp_client,
            _payload(
                [_record("G1-001", RSSI_TRILAT)],
                deletions=[self._tombstone(fp_client, "G1-001")],
            ),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "DELETION_CONFLICT"
        # 墓碑内容哈希不匹配 → 拒绝，避免删错版本
        bad = self._tombstone(fp_client, "G1-001")
        bad["contentSha256"] = "0" * 64
        resp = _ingest_payload(fp_client, _payload([], deletions=[bad]))
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "TOMBSTONE_MISMATCH"

    def test_manifest_exposes_hashes_and_floor_scope(self, fp_client):
        self._seed(fp_client)
        items = _manifest(fp_client)["items"]
        assert len(items) == 2
        assert all(len(item["contentSha256"]) == 64 for item in items)
        assert {item["floorId"] for item in items} == {"1"}
        floor1 = _manifest(fp_client, floor="1")["items"]
        floor2 = _manifest(fp_client, floor="2")["items"]
        assert len(floor1) == 2 and floor2 == []


class TestVersionsAndRollback:
    def test_rollback_restores_version_with_deletions(self, fp_client):
        first = _ingest(
            fp_client,
            [
                _record("G1-001", RSSI_TRILAT),
                _record("G1-002", (-70, -72, -75), coords=[-10.0, -10.0]),
            ],
        )
        item = {i["fpId"]: i for i in _manifest(fp_client)["items"]}["G1-001"]
        deleted = _ingest_payload(
            fp_client,
            _payload(
                [],
                deletions=[
                    {
                        "fpId": item["fpId"],
                        "collectedAt": item["collectedAt"],
                        "contentSha256": item["contentSha256"],
                    }
                ],
            ),
        ).json()
        assert deleted["recordCount"] == 1

        resp = fp_client.post(
            "/api/v1/fingerprint-db/rollback", json={"version": first["version"]}
        )
        assert resp.status_code == 200, resp.text
        rolled = resp.json()
        assert rolled["rolledBack"] is True
        assert rolled["active"] is True
        assert rolled["recordCount"] == 2
        assert _versions(fp_client)["active"]["version"] == first["version"]

        # 回滚后指纹匹配恢复
        match = fp_client.post(
            "/api/v1/fingerprint-db/query",
            json={
                "siteId": "school-building-01",
                "mapVersion": "latest",
                "beacons": [
                    {"beaconId": beacon_id, "rssi": rssi} for beacon_id, rssi in _beacon_obs()
                ],
            },
        ).json()
        assert match["matched"] is True and match["fpId"] == "G1-001"

        # 再切回含删除的版本
        resp = fp_client.post(
            "/api/v1/fingerprint-db/rollback", json={"version": deleted["version"]}
        )
        assert resp.status_code == 200
        assert resp.json()["recordCount"] == 1

    def test_retention_keeps_ten_versions_and_expires_rollback(self, fp_client):
        versions: list[str] = []
        for i in range(11):
            summary = _ingest(
                fp_client,
                [
                    _record(
                        f"G1-{i:03d}",
                        (-60 - i, -65 - i, -70 - i),
                        ts=f"2026-11-09T10:{i:02d}:00Z",
                        coords=[float(i), float(i)],
                    )
                ],
                file_name=f"fp-{i}.json",
            )
            versions.append(summary["version"])
        # 每次入库只新增一条，累计 11 条，没有删除
        assert summary["recordCount"] == 11
        assert summary["deletedCount"] == 0
        # 第 11 次入库裁剪了最老一个版本的明细
        assert summary["prunedVersions"] == [versions[0]]

        listing = _versions(fp_client)
        assert listing["retention"]["keepVersions"] == 10
        assert listing["retention"]["versionCount"] == 11
        assert listing["retention"]["detailVersions"] == 10
        assert listing["retention"]["prunedVersions"] == 1
        assert listing["retention"]["estimatedDetailBytes"] == 10 * 5_200_000
        assert listing["active"]["version"] == versions[-1]
        assert listing["active"]["detailsPruned"] is False
        pruned = {item["version"]: item for item in listing["items"]}
        assert pruned[versions[0]]["detailsPruned"] is True
        assert pruned[versions[0]]["prunedAt"] is not None

        # 已裁剪版本：对账与回滚都必须明确报「已过期」
        resp = fp_client.get("/api/v1/fingerprint-db/manifest", params={"version": versions[0]})
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "VERSION_EXPIRED"
        resp = fp_client.post(
            "/api/v1/fingerprint-db/rollback", json={"version": versions[0]}
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "VERSION_EXPIRED"
        assert "版本已过期" in resp.json()["error"]["message"]

        # 窗口内的版本可回滚
        resp = fp_client.post(
            "/api/v1/fingerprint-db/rollback", json={"version": versions[1]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["recordCount"] == 2

    def test_rollback_unknown_version(self, fp_client):
        resp = fp_client.post(
            "/api/v1/fingerprint-db/rollback", json={"version": "fpl-does-not-exist"}
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "VERSION_NOT_FOUND"
