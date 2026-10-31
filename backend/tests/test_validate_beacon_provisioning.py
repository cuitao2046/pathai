# -*- coding: utf-8 -*-
"""P2 信标入库一致性校验器测试（docs/27 §7 P-R1~P-R11）。

数据约束：使用真实规划台账 `result/ble_deployment.json` 与真实地图包，
真实部署台账由规划+地图包派生到临时 SQLite（不虚构信标）。
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNED = REPO_ROOT / "result" / "ble_deployment.json"
MAP = REPO_ROOT / "backend" / "maps_data" / "school-building-01" / "2026-10-30.1.json"


def _validator():
    path = REPO_ROOT / "backend" / "scripts" / "validate_beacon_provisioning.py"
    spec = importlib.util.spec_from_file_location("validate_beacon_provisioning", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _device_name(map_item, beacon):
    return map_item.get("deviceName") or f"F{beacon['floor']}-{int(beacon['minor']) % 1000:03d}"


def _records():
    planned = json.loads(PLANNED.read_text(encoding="utf-8"))["beacons"]
    package = json.loads(MAP.read_text(encoding="utf-8"))
    map_beacons = {b["beaconId"]: b for b in package["beacons"]}
    rows = []
    for beacon in planned:
        map_item = map_beacons[beacon["beaconId"]]
        rows.append(
            {
                "beacon_id": beacon["beaconId"],
                "map_version": package["mapVersion"],
                "device_mac": f"AA:BB:CC:00:{int(beacon['minor']) % 1000:02X}:01",
                "device_name": map_item["deviceName"],
                "name_write": "written",
                "written": {
                    "uuid": beacon["uuid"],
                    "major": beacon["major"],
                    "minor": beacon["minor"],
                    "name": map_item["deviceName"],
                    "txPower": beacon["txPower"],
                    "interval": beacon["broadcastInterval"],
                },
                "verify": {"passed": True, "mode": "full-readback", "platform": "windows"},
                "source": "intake",
            }
        )
    return rows


def _write_db(path: Path, rows) -> Path:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE beacon_deployment_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id TEXT NOT NULL,
                beacon_id TEXT NOT NULL,
                map_version TEXT NOT NULL,
                device_mac TEXT,
                device_name TEXT,
                name_write TEXT,
                written_json TEXT NOT NULL,
                verify_json TEXT NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        for row in rows:
            conn.execute(
                "INSERT INTO beacon_deployment_records "
                "(site_id, beacon_id, map_version, device_mac, device_name, name_write, "
                "written_json, verify_json, source) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "school-building-01",
                    row["beacon_id"],
                    row["map_version"],
                    row["device_mac"],
                    row["device_name"],
                    row["name_write"],
                    json.dumps(row["written"]),
                    json.dumps(row["verify"]),
                    row["source"],
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def _validate(tmp_path: Path, rows=None, map_path: Path = MAP):
    module = _validator()
    db = _write_db(tmp_path / "pathai.db", rows if rows is not None else _records())
    return module.validate("school-building-01", PLANNED, map_path, db, None)


def _rules(report):
    return {(issue["rule"], issue["beaconId"]) for issue in report["issues"]}


def test_green_report_has_no_errors(tmp_path: Path):
    report = _validate(tmp_path)
    assert report["summary"]["planned"] == 61
    assert report["summary"]["recorded"] == 61
    assert report["summary"]["verified"] == 61
    assert report["summary"]["errors"] == 0


def test_missing_record_is_error_p_r1(tmp_path: Path):
    rows = _records()
    missing = rows.pop(1)
    report = _validate(tmp_path, rows)
    assert ("P-R1", missing["beacon_id"]) in _rules(report)
    assert report["summary"]["errors"] >= 1


def test_verify_failure_is_error_p_r2(tmp_path: Path):
    rows = _records()
    rows[0]["verify"] = {"passed": False}
    report = _validate(tmp_path, rows)
    assert ("P-R2", rows[0]["beacon_id"]) in _rules(report)


def test_triplet_mismatch_is_error_p_r3(tmp_path: Path):
    rows = _records()
    rows[0]["written"]["minor"] = int(rows[0]["written"]["minor"]) + 1
    report = _validate(tmp_path, rows)
    assert ("P-R3", rows[0]["beacon_id"]) in _rules(report)


def test_duplicate_triplet_is_error_p_r4(tmp_path: Path):
    rows = _records()
    rows[1]["written"]["major"] = rows[0]["written"]["major"]
    rows[1]["written"]["minor"] = rows[0]["written"]["minor"]
    report = _validate(tmp_path, rows)
    assert any(rule == "P-R4" for rule, _ in _rules(report))


def test_major_mismatch_with_map_is_error_p_r5(tmp_path: Path):
    package = json.loads(MAP.read_text(encoding="utf-8"))
    package["beacons"][0]["major"] = package["beacons"][0]["major"] + 1
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    report = _validate(tmp_path, map_path=map_path)
    assert ("P-R5", package["beacons"][0]["beaconId"]) in _rules(report)


def test_field_edit_diffs_and_audit_warnings(tmp_path: Path):
    rows = _records()
    row = rows[0]
    row["source"] = "field_edit"
    row["written"]["txPower"] = -8
    row["written"]["interval"] = 500
    row["device_mac"] = None
    row["written"]["name"] = "F9-999"
    report = _validate(tmp_path, rows)
    assert ("P-R6", row["beacon_id"]) in _rules(report)
    assert ("P-R9", row["beacon_id"]) in _rules(report)
    assert ("P-R10", row["beacon_id"]) in _rules(report)
    assert ("P-R11", row["beacon_id"]) in _rules(report)
    assert report["summary"]["errors"] == 0
