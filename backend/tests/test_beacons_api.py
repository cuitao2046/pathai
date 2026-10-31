# -*- coding: utf-8 -*-
"""信标配置台账与真实部署台账 API 测试（docs/27 §5.2 / §6.8 / §8）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pathai_backend.admin.deps import CSRF_HEADER
from pathai_backend.app import create_app
from pathai_backend.config import Settings
from pathai_backend.database import get_engine
from pathai_backend.models import Base

BACKEND = Path(__file__).resolve().parent.parent
SAMPLE_MAP = BACKEND / "maps_data" / "school-building-01" / "2026-10-25.1.json"
CONVERTER = BACKEND / "scripts" / "convert_map_v9.py"
SITE = "school-building-01"
VERSION = "2026-10-25.1"
NEW_PASSWORD = "safe-password-1"
BEACON_ID = "BK-01-001"


def _load_converter():
    spec = importlib.util.spec_from_file_location("convert_map_v9", CONVERTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


converter = _load_converter()


@pytest.fixture()
def maps_dir(tmp_path) -> Path:
    """真实样例地图包 + P0 新增字段（deviceName/broadcastInterval），其余字段保持原样。"""
    site_dir = tmp_path / SITE
    site_dir.mkdir()
    data = json.loads(SAMPLE_MAP.read_text(encoding="utf-8"))
    assert len(data["beacons"]) == 61
    for beacon in data["beacons"]:
        beacon["deviceName"] = converter.derive_device_name(
            str(beacon["floorId"]), int(beacon["minor"])
        )
        beacon["broadcastInterval"] = 300
    (site_dir / SAMPLE_MAP.name).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def settings(tmp_path, maps_dir) -> Settings:
    return Settings(
        env="local",
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        openid_salt="test-openid-salt",
        map_data_dir=maps_dir,
        public_base_url="https://api.test",
    )


@pytest.fixture()
def app(settings):
    application = create_app(settings)
    Base.metadata.create_all(get_engine())
    return application


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def admin(client) -> str:
    """登录并完成首改密，返回 csrf token。"""
    resp = client.post(
        "/api/v1/admin/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert resp.status_code == 200
    csrf = resp.json()["csrfToken"]
    resp = client.post(
        "/api/v1/admin/auth/password",
        json={"currentPassword": "admin", "newPassword": NEW_PASSWORD, "confirmPassword": NEW_PASSWORD},
        headers={CSRF_HEADER: csrf},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["csrfToken"]


def _written(item: dict, **overrides) -> dict:
    data = {
        "uuid": item["uuid"],
        "major": item["major"],
        "minor": item["minor"],
        "name": item["deviceName"],
        "txPower": item["txPower"],
        "interval": item["broadcastInterval"],
    }
    data.update(overrides)
    return data


def _body(item: dict, **overrides) -> dict:
    body = {
        "mapVersion": VERSION,
        "deviceMac": "8A:E5:84:7A:E7:C9",
        "deviceName": item["deviceName"],
        "nameWrite": "written",
        "written": _written(item),
        "verify": {"passed": True, "mode": "full-readback", "platform": "windows"},
        "operator": "张三",
        "source": "intake",
    }
    body.update(overrides)
    return body


def _put(client, csrf, beacon_id, body):
    return client.put(
        f"/api/v1/sites/{SITE}/deployment-records/{beacon_id}",
        json=body,
        headers={CSRF_HEADER: csrf},
    )


def _beacons(client) -> list[dict]:
    resp = client.get(f"/api/v1/sites/{SITE}/beacons?version={VERSION}")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


class TestBeaconConfig:
    def test_requires_admin_session(self, client):
        assert client.get(f"/api/v1/sites/{SITE}/beacons").status_code == 401

    def test_lists_published_beacons(self, client, admin):
        items = _beacons(client)
        assert len(items) == 61
        first = next(i for i in items if i["beaconId"] == BEACON_ID)
        assert first["deviceName"] == "F1-101"
        assert first["broadcastInterval"] == 300
        assert first["uuid"] == converter.SYSTEM_BEACON_UUID
        assert first["major"] == 1 and first["minor"] == 10101
        assert first["locationDesc"]
        assert first["semanticTag"] == "trilateration_route_base"

    def test_etag_304(self, client, admin):
        resp = client.get(f"/api/v1/sites/{SITE}/beacons?version={VERSION}")
        etag = resp.headers["ETag"]
        again = client.get(
            f"/api/v1/sites/{SITE}/beacons?version={VERSION}",
            headers={"If-None-Match": etag},
        )
        assert again.status_code == 304

    def test_unknown_site_and_version(self, client, admin):
        assert client.get("/api/v1/sites/nope/beacons").status_code == 404
        assert client.get(f"/api/v1/sites/{SITE}/beacons?version=2099-01-01.1").status_code == 404


class TestDeploymentRecords:
    def test_intake_upsert_and_summary(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = _put(client, admin, BEACON_ID, _body(item))
        assert resp.status_code == 200, resp.text
        assert resp.json()["written"]["name"] == "F1-101"

        # 幂等：重复上传不新增记录
        _put(client, admin, BEACON_ID, _body(item))
        listing = client.get(f"/api/v1/sites/{SITE}/deployment-records?version={VERSION}")
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["summary"]["recorded"] == 1
        assert body["summary"]["total"] == 61
        assert len(body["summary"]["missing"]) == 60

    def test_requires_csrf_for_write(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = client.put(
            f"/api/v1/sites/{SITE}/deployment-records/{BEACON_ID}", json=_body(item)
        )
        assert resp.status_code == 403

    def test_rejects_triplet_mismatch(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = _put(client, admin, BEACON_ID, _body(item, written=_written(item, minor=10102)))
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "BEACON_TRIPLET_MISMATCH"

    def test_rejects_intake_interval_mismatch(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = _put(client, admin, BEACON_ID, _body(item, written=_written(item, interval=500)))
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "BEACON_INTERVAL_MISMATCH"

    def test_field_edit_allows_interval_change(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = _put(
            client,
            admin,
            BEACON_ID,
            _body(item, written=_written(item, interval=500), source="field_edit"),
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "field_edit"
        assert resp.json()["written"]["interval"] == 500

    def test_rejects_unverified(self, client, admin):
        item = next(i for i in _beacons(client) if i["beaconId"] == BEACON_ID)
        resp = _put(client, admin, BEACON_ID, _body(item, verify={"passed": False}))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "BEACON_VERIFY_NOT_PASSED"

    def test_rejects_duplicate_triplet_across_beacons(self, client, admin):
        from pathai_backend.database import session_scope
        from pathai_backend.models import BeaconDeploymentRecord

        items = _beacons(client)
        first, second = items[0], items[1]
        # 绕过 API 直接落一条「占用首枚三元组」的记录（模拟历史脏数据）
        with session_scope() as session:
            session.add(
                BeaconDeploymentRecord(
                    site_id=SITE,
                    beacon_id=second["beaconId"],
                    map_version=VERSION,
                    written_json=json.dumps(
                        _written(second, major=first["major"], minor=first["minor"]),
                        ensure_ascii=False,
                    ),
                    verify_json=json.dumps({"passed": True}),
                    source="intake",
                    updated_at_epoch=0,
                )
            )
            session.commit()
        resp = _put(client, admin, first["beaconId"], _body(first))
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "BEACON_TRIPLET_CONFLICT"

    def test_commit_incomplete_then_complete(self, client, admin):
        items = _beacons(client)
        resp = client.post(
            f"/api/v1/sites/{SITE}/deployment-records/commit",
            json={"mapVersion": VERSION},
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "incomplete"
        assert resp.json()["roundId"] > 0

        for item in items:
            assert _put(client, admin, item["beaconId"], _body(item)).status_code == 200
        resp = client.post(
            f"/api/v1/sites/{SITE}/deployment-records/commit",
            json={"mapVersion": VERSION},
            headers={CSRF_HEADER: admin},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "committed"
        assert body["recorded"] == body["total"] == 61
        assert body["missing"] == []
        assert body["unverified"] == []


class TestConvertBeacons:
    def _entry(self, beacon_id="BK-01-001", floor=1, minor=10101, major=1, interval=300,
               uuid=converter.SYSTEM_BEACON_UUID):
        return {
            "beaconId": beacon_id,
            "floor": floor,
            "coordinates": [0.0, 0.0],
            "txPower": -12.0,
            "installHeight": 2.2,
            "uuid": uuid,
            "major": major,
            "minor": minor,
            "broadcastInterval": interval,
        }

    def test_adds_device_name_and_interval(self):
        out = converter.convert_beacons([self._entry()])
        assert out[0]["deviceName"] == "F1-101"
        assert out[0]["broadcastInterval"] == 300

    def test_requires_broadcast_interval(self):
        entry = self._entry()
        del entry["broadcastInterval"]
        with pytest.raises(ValueError, match="broadcastInterval"):
            converter.convert_beacons([entry])

    def test_rejects_interval_out_of_manual_range(self):
        with pytest.raises(ValueError, match="broadcastInterval"):
            converter.convert_beacons([self._entry(interval=50)])

    def test_rejects_uuid_mismatch(self):
        with pytest.raises(ValueError, match="UUID"):
            converter.convert_beacons([self._entry(uuid="00000000-0000-0000-0000-000000000000")])

    def test_rejects_duplicate_device_name(self):
        with pytest.raises(ValueError, match="设备名重复"):
            converter.convert_beacons(
                [
                    self._entry(),
                    self._entry(beacon_id="BK-02-001", major=2, minor=11101),
                ]
            )
