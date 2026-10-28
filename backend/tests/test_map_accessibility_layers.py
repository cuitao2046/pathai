# -*- coding: utf-8 -*-
"""地图包无障碍图层与防火门状态契约（spec REQ-VOICE-05）。

验证 2026-10-28.1 起发布包携带 accessibility 图层、防火门状态由门几何回填，
并把「全部防火门常闭导致的目的地丢失」固定为可回归的数据评估结论。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
for _path in (REPO_ROOT, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from pathai_backend.map_repository import MapRepository  # noqa: E402
from src.topology.route_rules import RouteGraph  # noqa: E402

#: 全闭假设下会失去盲可达的目的地（2026-10-28 真实数据评估结论）
FORCED_CLOSED_LOST = {"档案", "配电室", "仪器室"}


@pytest.fixture(scope="module")
def published():
    repo = MapRepository.load(BACKEND_DIR / "maps_data", "https://localhost")
    latest = repo.latest("school-building-01")
    assert latest is not None
    return latest


def _blind_reachable(data: dict, force_closed: bool = False) -> set[str]:
    if force_closed:
        import copy

        data = copy.deepcopy(data)
        for floor in data["floors"].values():
            for node in floor["topology"]["nodes"]:
                if node.get("type") == "doorway" and node.get("doorType") == "fire":
                    node["isNormallyOpen"] = False
    graph = RouteGraph(data)
    adjacency = graph._build_adjacency("blind", door_filter=True)  # noqa: SLF001
    start = next(
        nid
        for nid, node in graph.nodes.items()
        if node["type"] in ("facility", "facility_entrance")
        and node["facilityType"] == "elevator"
        and node["floor"] == 1
    )
    seen = {start}
    stack = [start]
    while stack:
        current = stack.pop()
        for neighbor, _weight, _edge_id in adjacency.get(current, ()):
            if neighbor not in seen:
                seen.add(neighbor)
                stack.append(neighbor)
    return seen


class TestAccessibilityLayers:
    def test_defaults_declared(self, published):
        defaults = published.data.get("accessibilityDefaults")
        assert defaults is not None
        assert defaults["tactilePaving"] == "none"
        assert defaults["ramps"] == "none"
        assert defaults["groundMaterial"] == "normal"

    def test_risk_nodes_carried(self, published):
        for floor_id, floor in published.data["floors"].items():
            risk_nodes = (floor.get("accessibility") or {}).get("riskNodes") or []
            assert risk_nodes, f"{floor_id} 层缺少风险点图层"
            assert all({"type", "riskLevel", "coordinates"} <= set(r) for r in risk_nodes)

    def test_pending_layers_empty_by_design(self, published):
        for floor in published.data["floors"].values():
            accessibility = floor.get("accessibility") or {}
            assert accessibility.get("tactilePaths") == []
            assert accessibility.get("ramps") == []
            assert accessibility.get("groundMaterialChanges") == []


class TestFireDoorState:
    def test_all_fire_doors_resolved(self, published):
        states = [
            n
            for floor in published.data["floors"].values()
            for n in floor["topology"]["nodes"]
            if n.get("type") == "doorway" and n.get("doorType") == "fire"
        ]
        assert len(states) == 91
        assert all(n.get("isNormallyOpen") is not None for n in states)
        assert all(n.get("isNormallyOpenSource") == "geometry.doors" for n in states)
        assert sum(1 for n in states if n["isNormallyOpen"]) == 25
        assert sum(1 for n in states if not n["isNormallyOpen"]) == 66


class TestBlindReachability:
    def test_all_destinations_reachable(self, published):
        reachable = _blind_reachable(published.data)
        unreachable = [p.name for p in published.pkg.places if p.nodeId not in reachable]
        assert unreachable == []

    def test_forced_all_closed_loses_documented_rooms(self, published):
        reachable = _blind_reachable(published.data, force_closed=True)
        lost = {p.name for p in published.pkg.places if p.nodeId not in reachable}
        assert lost == FORCED_CLOSED_LOST
