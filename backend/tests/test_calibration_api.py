# -*- coding: utf-8 -*-
"""标定控制台接口：上传→计算→apply→版本→回滚→覆盖层（spec Part B/C）。

网格重算在全楼真实数据上约 3-4 分钟，接口测试用等价 staging 替身验证状态机与
原子性；生成器本身由 tests/test_fit_logdist.py 与 CLI 冒烟覆盖。
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pathai_backend import calibration_service as svc
from pathai_backend.app import create_app
from pathai_backend.database import get_engine
from pathai_backend.grid_recompute import GridRecomputeResult
from pathai_backend.models import Base

BACKEND = Path(__file__).resolve().parent.parent
BASELINE_DIR = BACKEND.parent / "result"
SAMPLE_MAP = BACKEND / "maps_data" / "school-building-01" / "2026-10-25.1.json"

A_TRUE = -52.0
N_TRUE = 3.5
STOPS = [0, 1, 1.5, 2, 3, 4.5, 6.5, 9, 13, 18]


def logdist_sample() -> dict:
    stops = []
    for index, h in enumerate(STOPS):
        d = math.sqrt(h ** 2 + (2.5 - 1.2) ** 2)
        rssi = round(A_TRUE - 10 * N_TRUE * math.log10(d))
        stops.append(
            {
                "stopId": f"LD-{index:02d}",
                "horizontalDistanceM": h,
                "slantDistanceM": round(d, 2),
                "sessions": [
                    {
                        "startedAt": "2026-11-09T09:00:00Z",
                        "durationS": 60,
                        "captureIndex": i + 1,
                        "installHeightM": 2.5,
                        "observations": [
                            {
                                "beaconId": "BK-01-007",
                                "n": 20,
                                "rssiMedian": rssi,
                                "rssiMin": rssi - 3,
                                "rssiMax": rssi + 3,
                                "rssiStd": 1.5,
                                "truthDistanceM": round(d, 2),
                            }
                        ],
                    }
                    for i in range(3)
                ],
            }
        )
    return {
        "schemaVersion": "1.0",
        "type": "positioning_calibration_logdist",
        "deployable": False,
        "operator": "tester",
        "mapVersion": "2026-11-08.2",
        "beacon": {"beaconId": "BK-01-007"},
        "installHeightM": 2.5,
        "phoneHeightM": 1.2,
        "stops": stops,
    }


def static_sample() -> dict:
    return {
        "schemaVersion": "1.0",
        "type": "positioning_calibration_static",
        "deployable": True,
        "operator": "tester",
        "mapVersion": "2026-10-25.1",
        "points": [
            {
                "pointId": "P-01",
                "truth": [-25.0, -68.0],
                "floor": 1,
                "sessions": [
                    {
                        "sessionIndex": 1,
                        "observations": [
                            {"beaconId": "BK-01-001", "rssiMedian": -68},
                            {"beaconId": "BK-01-004", "rssiMedian": -72},
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture()
def cal_settings(tmp_path):
    from pathai_backend.config import Settings

    maps = tmp_path / "maps"
    site = maps / "school-building-01"
    site.mkdir(parents=True)
    site.joinpath(SAMPLE_MAP.name).write_bytes(SAMPLE_MAP.read_bytes())
    return Settings(
        env="local",
        database_url=f"sqlite:///{(tmp_path / 'cal.db').as_posix()}",
        openid_salt="salt",
        map_data_dir=maps,
        public_base_url="https://api.test",
        calibration_data_dir=tmp_path / "calib",
        calibration_grid_baseline_dir=BASELINE_DIR,
        calibration_params_ttl_s=0.0,
    )


@pytest.fixture()
def cal_client(cal_settings, monkeypatch):
    from pathai_backend.routers import calibration as cal_router

    cal_router._LAST_WRITE.clear()
    # 限流由 test_write_rate_limited 单独覆盖；功能用例不因进程内 5s 窗口互相干扰。
    monkeypatch.setattr(cal_router, "_rate_limit", lambda key, interval_s: None)
    application = create_app(cal_settings)
    Base.metadata.create_all(get_engine())
    with TestClient(application, raise_server_exceptions=False) as client:
        yield client


def _fake_recompute(settings, *, run_id, params_version, a, n):
    staging = Path(settings.calibration_data_dir) / "staging" / run_id
    staging.mkdir(parents=True, exist_ok=True)
    grid_version = f"g-test-{run_id[-6:]}"
    manifest = {
        "schemaVersion": "fingerprint_grid_manifest_v1",
        "runId": run_id,
        "gridVersion": grid_version,
        "baseGridVersion": "11.1.0",
        "paramsVersion": params_version,
        "params": {"rssiAt1mDbm": a, "pathLossN": n},
        "counts": {"points": 10, "collectable": 8},
    }
    overlay = {
        "schemaVersion": "fingerprint_grid_overlay_v1",
        "baseGridVersion": "11.1.0",
        "gridVersion": grid_version,
        "paramsVersion": params_version,
        "points": [{"fpId": "FP-1-C001R001", "collectable": True}],
    }
    (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (staging / "overlay.json").write_text(json.dumps(overlay), encoding="utf-8")
    return GridRecomputeResult(
        grid_version=grid_version,
        base_grid_version="11.1.0",
        staging_dir=staging,
        grid_path=staging / "grid.json",
        overlay_path=staging / "overlay.json",
        manifest_path=staging / "manifest.json",
        map_version="2026-10-25.1",
        point_count=10,
        collectable_count=8,
        elapsed_s=0.01,
    )


def _fake_fit(settings, sample, run_id, out_dir):
    a, n = -55.0, 3.0
    return {
        "schemaVersion": "1.0",
        "type": "positioning_calibration_static_result",
        "deployable": True,
        "generatedAt": "2026-11-09T00:00:00Z",
        "rssiAt1m": a,
        "pathLossExponent": n,
        "thresholds": {"residualReliableM": 1.4, "residualDegradedM": 3.8},
        "fit": {"samples": 12, "r2": 0.9, "rmseDb": 1.2},
        "staticMetrics": {"sessions": 6, "estimated": 5, "lost": 1},
        "groups": None,
        "warnings": [],
    }


def _wait_computed(client, run_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = client.get(f"/api/v1/calibration/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["status"] not in ("queued", "computing"):
            return data
        time.sleep(0.05)
    raise AssertionError("计算任务超时")


def test_readiness_lists_baseline_files(cal_client):
    resp = cal_client.get("/api/v1/calibration/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["active"]["paramVersion"] is None
    assert {f["key"] for f in body["files"]} >= {"geo", "ledger", "routes", "baselineGrid"}


def test_logdist_compute_is_not_deployable(cal_client):
    content = json.dumps(logdist_sample(), ensure_ascii=False)
    up = cal_client.post(
        "/api/v1/calibration/samples",
        json={"fileName": "logdist_samples.json", "content": content, "operator": "tester"},
    )
    assert up.status_code == 200, up.text
    sample_id = up.json()["sampleId"]
    assert up.json()["deployable"] is False

    run = cal_client.post("/api/v1/calibration/runs", json={"sampleId": sample_id})
    assert run.status_code == 200, run.text
    data = _wait_computed(cal_client, run.json()["runId"])
    assert data["status"] == "computed"
    assert data["result"]["deployable"] is False
    assert data["result"]["applyBlockedReason"]
    assert abs(data["result"]["params"]["rssiAt1mDbm"] - A_TRUE) < 0.3
    assert data["result"]["gridPreview"] is None

    denied = cal_client.post(
        f"/api/v1/calibration/runs/{data['runId']}/apply",
        json={"confirm": True, "expectParamVersion": None},
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "NOT_DEPLOYABLE"


def test_logdist_constant_rssi_reports_fit_failed(cal_client):
    sample = logdist_sample()
    for stop in sample["stops"]:
        for session in stop["sessions"]:
            for observation in session["observations"]:
                observation["rssiMedian"] = -100
    content = json.dumps(sample, ensure_ascii=False)
    up = cal_client.post(
        "/api/v1/calibration/samples",
        json={"fileName": "logdist_constant.json", "content": content, "operator": "tester"},
    )
    assert up.status_code == 200, up.text
    run = cal_client.post("/api/v1/calibration/runs", json={"sampleId": up.json()["sampleId"]})
    assert run.status_code == 200, run.text
    data = _wait_computed(cal_client, run.json()["runId"])
    assert data["status"] == "compute_failed"
    assert data["errorCode"] == "fit_failed"
    assert "-100 dBm" in data["errorMessage"]
    assert "没有随距离衰减" in data["errorMessage"]
    assert "重新采集" in data["errorMessage"]


def test_static_apply_versions_and_rollback(cal_client, monkeypatch):
    monkeypatch.setattr(svc, "_fit_document", _fake_fit)
    monkeypatch.setattr(svc, "recompute_grid", _fake_recompute)

    def upload_and_compute(a: float, n: float) -> str:
        content = json.dumps(static_sample(), ensure_ascii=False)
        up = cal_client.post(
            "/api/v1/calibration/samples",
            json={"fileName": f"static_{a}.json", "content": content, "operator": "tester"},
        )
        assert up.status_code == 200, up.text
        run = cal_client.post(
            "/api/v1/calibration/runs", json={"sampleId": up.json()["sampleId"]}
        )
        assert run.status_code == 200, run.text
        data = _wait_computed(cal_client, run.json()["runId"])
        assert data["status"] == "computed", data
        assert data["result"]["gridPreview"]["pointCount"] == 10
        return data["runId"]

    run1 = upload_and_compute(-55.0, 3.0)
    conflict = cal_client.post(
        f"/api/v1/calibration/runs/{run1}/apply",
        json={"confirm": True, "expectParamVersion": "loc-wrong"},
    )
    assert conflict.status_code == 409

    applied1 = cal_client.post(
        f"/api/v1/calibration/runs/{run1}/apply",
        json={"confirm": True, "expectParamVersion": None},
    )
    assert applied1.status_code == 200, applied1.text
    v1 = applied1.json()["paramVersion"]
    assert applied1.json()["gridVersion"].startswith("g-test-")

    params = cal_client.get("/api/v1/config/location-params").json()
    assert params["paramsVersion"] == v1
    assert params["rssiAt1mDbm"] == -55.0
    assert params["pathLossN"] == 3.0
    assert params["source"] == "db"

    overlay = cal_client.get(
        "/api/v1/config/fingerprint-grid/overlay", params={"baseGridVersion": "11.1.0"}
    )
    assert overlay.status_code == 200, overlay.text
    assert overlay.json()["baseGridVersion"] == "11.1.0"
    etag = overlay.headers["ETag"]
    assert cal_client.get(
        "/api/v1/config/fingerprint-grid/overlay",
        params={"baseGridVersion": "11.1.0"},
        headers={"If-None-Match": etag},
    ).status_code == 304
    mismatch = cal_client.get(
        "/api/v1/config/fingerprint-grid/overlay", params={"baseGridVersion": "10.0.0"}
    )
    assert mismatch.status_code == 409

    # 第二个版本（同一样本内容会去重，所以改一个会话值制造不同 SHA）
    sample2 = static_sample()
    sample2["points"][0]["sessions"][0]["observations"][0]["rssiMedian"] = -66
    content2 = json.dumps(sample2, ensure_ascii=False)
    up2 = cal_client.post(
        "/api/v1/calibration/samples",
        json={"fileName": "static2.json", "content": content2, "operator": "tester"},
    )
    run2 = cal_client.post(
        "/api/v1/calibration/runs", json={"sampleId": up2.json()["sampleId"]}
    )
    data2 = _wait_computed(cal_client, run2.json()["runId"])
    applied2 = cal_client.post(
        f"/api/v1/calibration/runs/{data2['runId']}/apply",
        json={"confirm": True, "expectParamVersion": v1},
    )
    assert applied2.status_code == 200, applied2.text
    v2 = applied2.json()["paramVersion"]

    versions = cal_client.get("/api/v1/calibration/versions").json()
    assert versions["active"]["paramVersion"] == v2
    assert len(versions["items"]) == 2

    rolled = cal_client.post(
        "/api/v1/calibration/rollback",
        json={"paramVersion": v1, "confirm": True},
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["paramVersion"] == v1
    assert cal_client.get("/api/v1/config/location-params").json()["paramsVersion"] == v1

    no_confirm = cal_client.post(
        "/api/v1/calibration/rollback",
        json={"paramVersion": v1, "confirm": False},
    )
    assert no_confirm.status_code == 400


def test_write_rate_limited(cal_settings):
    """写接口进程内限流：同 key 窗口内第二次直接 429。"""
    from pathai_backend.routers import calibration as cal_router

    cal_router._LAST_WRITE.clear()
    application = create_app(cal_settings)
    Base.metadata.create_all(get_engine())
    body = {
        "fileName": "static.json",
        "content": json.dumps(static_sample(), ensure_ascii=False),
        "operator": "tester",
    }
    with TestClient(application, raise_server_exceptions=False) as client:
        first = client.post("/api/v1/calibration/samples", json=body)
        assert first.status_code == 200, first.text
        second = client.post("/api/v1/calibration/samples", json=body)
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "RATE_LIMITED"
