# -*- coding: utf-8 -*-
"""发布时危险层版本自动同步与启动审计的回归测试。

背景：危险层带 mapVersion，导航服务遇到不一致会整层忽略。此前发布新地图
只同步了危险区编辑器数据，危险层版本未跟进，导致危险层被静默丢弃。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pathai_backend.admin.publish import sync_hazard_layer_version
from pathai_backend.hazard_layer import (
    HazardRepository,
    log_version_mismatches,
    parse_layer,
)

SITE_ID = "site-x"
OLD_VERSION = "2026-11-01.1"
NEW_VERSION = "2026-11-02.1"


def _package(version: str, edge_distance: float = 1.0) -> dict:
    return {
        "siteId": SITE_ID,
        "mapVersion": version,
        "floors": {
            "1": {
                "topology": {
                    "nodes": [{"id": "N1", "type": "intersection", "coordinates": [0, 0]}],
                    "edges": [
                        {"id": "E1", "from": "N1", "to": "N1", "distance": edge_distance}
                    ],
                },
                "geometry": {"walls": [], "rooms": []},
            }
        },
        "crossFloorEdges": [],
    }


def _write_package(root: Path, version: str, **kwargs) -> None:
    site_dir = root / SITE_ID
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / f"{version}.json").write_text(
        json.dumps(_package(version, **kwargs), ensure_ascii=False), encoding="utf-8"
    )


def _write_hazard(root: Path, version: str = OLD_VERSION) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{SITE_ID}.json"
    path.write_text(
        json.dumps(
            {
                "siteId": SITE_ID,
                "mapVersion": version,
                "hazardVersion": "2026-11-01T00:00:00Z/1",
                "notes": f"地图数据：backend/maps_data/{SITE_ID}/{version}.json",
                "hazards": [{"hazardId": "H1", "edgeIds": ["E1"], "level": "avoid"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_sync_updates_version_when_geometry_unchanged(tmp_path: Path) -> None:
    maps = tmp_path / "maps"
    hazards = tmp_path / "hazards"
    _write_package(maps, OLD_VERSION)
    _write_package(maps, NEW_VERSION)
    hazard_file = _write_hazard(hazards)

    result = sync_hazard_layer_version(SITE_ID, maps, hazards, NEW_VERSION)

    assert result == {
        "status": "updated",
        "fromVersion": OLD_VERSION,
        "mapVersion": NEW_VERSION,
    }
    payload = json.loads(hazard_file.read_text(encoding="utf-8"))
    assert payload["mapVersion"] == NEW_VERSION
    assert NEW_VERSION in payload["notes"]
    assert OLD_VERSION not in payload["notes"]
    assert len(payload["hazards"]) == 1


def test_sync_keeps_file_when_topology_changed(tmp_path: Path, caplog) -> None:
    maps = tmp_path / "maps"
    hazards = tmp_path / "hazards"
    _write_package(maps, OLD_VERSION)
    _write_package(maps, NEW_VERSION, edge_distance=2.0)
    hazard_file = _write_hazard(hazards)
    before = hazard_file.read_text(encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        result = sync_hazard_layer_version(SITE_ID, maps, hazards, NEW_VERSION)

    assert result["status"] == "topology_changed"
    assert hazard_file.read_text(encoding="utf-8") == before
    assert "危险层未随地图升级" in caplog.text


def test_sync_skips_when_old_package_missing(tmp_path: Path, caplog) -> None:
    maps = tmp_path / "maps"
    hazards = tmp_path / "hazards"
    _write_package(maps, NEW_VERSION)
    hazard_file = _write_hazard(hazards)
    before = hazard_file.read_text(encoding="utf-8")

    with caplog.at_level(logging.ERROR):
        result = sync_hazard_layer_version(SITE_ID, maps, hazards, NEW_VERSION)

    assert result["status"] == "skipped_unknown_base"
    assert hazard_file.read_text(encoding="utf-8") == before
    assert "缺少可比对的地图包" in caplog.text


def test_sync_reports_up_to_date_and_missing(tmp_path: Path) -> None:
    maps = tmp_path / "maps"
    hazards = tmp_path / "hazards"
    _write_package(maps, NEW_VERSION)
    _write_hazard(hazards, NEW_VERSION)

    assert sync_hazard_layer_version(SITE_ID, maps, hazards, NEW_VERSION)["status"] == (
        "up_to_date"
    )
    assert sync_hazard_layer_version("other-site", maps, hazards, NEW_VERSION)["status"] == (
        "missing"
    )


def test_startup_audit_logs_version_mismatch_as_error(caplog) -> None:
    layer = parse_layer(
        SITE_ID, {"siteId": SITE_ID, "mapVersion": OLD_VERSION, "hazards": []}
    )
    repo = HazardRepository(layers={SITE_ID: layer})

    with caplog.at_level(logging.ERROR):
        mismatched = log_version_mismatches(repo, {SITE_ID: NEW_VERSION})

    assert mismatched == [SITE_ID]
    assert "危险层版本与当前地图不一致" in caplog.text
