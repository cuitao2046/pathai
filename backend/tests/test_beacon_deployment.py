# -*- coding: utf-8 -*-
"""部署参考墙离线预计算回归（与客户端 beacon-deployment.ts 同口径）。

验收基准取自客户端真实地图包用例：BK-01-006 / BK-Q-F1-012；
并检查最新地图包里每颗信标都带预计算结果且引用的墙确实存在。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAPS_DIR = REPO_ROOT / "backend" / "maps_data" / "school-building-01"


def _deployment_module():
    name = "beacon_deployment_test"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "backend" / "scripts" / "beacon_deployment.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass 在 3.14 需要能通过 cls.__module__ 回查模块
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _latest_package() -> dict:
    candidates = []
    for path in MAPS_DIR.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        candidates.append((data.get("publishedAt", ""), data))
    assert candidates, "maps_data 下没有地图包"
    return max(candidates, key=lambda item: item[0])[1]


def _analyze(package: dict, beacon_id: str) -> dict:
    module = _deployment_module()
    beacon = next(b for b in package["beacons"] if b["beaconId"] == beacon_id)
    geometry = package["floors"][beacon["floorId"]]["geometry"]
    analyzer = module.BeaconDeploymentAnalyzer(
        geometry["walls"], geometry.get("rooms") or []
    )
    return analyzer.analyze(beacon["x"], beacon["y"])


def test_known_beacons_match_client_baseline():
    package = _latest_package()

    plan = _analyze(package, "BK-01-006")
    assert plan["installationWallId"] == "F1-W-1268"
    assert plan["installationDistanceM"] < 0.2
    assert [(r["wallId"], r["side"]) for r in plan["references"]] == [
        ("F1-W-0221", "left"),
        ("F1-W-0476", "right"),
    ]
    assert abs(plan["references"][0]["distanceM"] - 6.017) < 0.01
    assert abs(plan["references"][1]["distanceM"] - 17.527) < 0.01

    plan = _analyze(package, "BK-Q-F1-012")
    assert plan["installationWallId"] == "F1-W-0908"
    assert abs(plan["installationDistanceM"] - 0.179) < 0.001
    assert [(r["wallId"], r["side"]) for r in plan["references"]] == [
        ("F1-W-0476", "right"),
    ]
    assert abs(plan["references"][0]["distanceM"] - 13.657) < 0.01


def test_published_package_precomputes_every_beacon():
    package = _latest_package()
    assert package["beacons"], "信标目录为空"
    for beacon in package["beacons"]:
        deployment = beacon.get("deployment")
        assert deployment is not None, f"{beacon['beaconId']} 缺少 deployment"
        assert deployment["installationWallId"], f"{beacon['beaconId']} 安装墙为空"
        assert deployment["references"], f"{beacon['beaconId']} 参考墙为空"

        floor = package["floors"][beacon["floorId"]]
        wall_ids = {
            wall.get("id")
            for wall in (floor.get("geometry") or {}).get("walls") or []
        }
        for reference in deployment["references"]:
            assert reference["wallId"] in wall_ids
            assert reference["distanceM"] > 0
        assert deployment["installationWallId"] in wall_ids
