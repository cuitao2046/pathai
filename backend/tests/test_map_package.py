# -*- coding: utf-8 -*-
"""地图包契约测试：schema 校验、语义校验、加载摘要与路由引擎兼容性（REQ-MAP）。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pathai_backend.map_package import (
    MapPackage,
    load_map_package,
    parse_map_package_bytes,
    sha256_of_bytes,
    validate_map_package,
)

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "maps_data" / "school-building-01" / "2026-10-01.1.json"


@pytest.fixture(scope="module")
def sample_raw() -> bytes:
    return SAMPLE_PATH.read_bytes()


@pytest.fixture(scope="module")
def sample_pkg(sample_raw) -> MapPackage:
    pkg, _ = parse_map_package_bytes(sample_raw)
    return pkg


class TestSamplePackage:
    """合成样例必须通过 schema + 语义全部校验。"""

    def test_sample_loads_and_validates(self, sample_pkg):
        assert sample_pkg.siteId == "school-building-01"
        assert sample_pkg.mapVersion == "2026-10-01.1"
        assert set(sample_pkg.floors.keys()) == {"1", "2"}

    def test_sample_sha256_matches(self, sample_raw):
        _, digest = parse_map_package_bytes(sample_raw)
        assert digest == sha256_of_bytes(sample_raw)
        assert len(digest) == 64

    def test_load_map_package_from_path(self):
        pkg, digest = load_map_package(SAMPLE_PATH)
        assert pkg.mapVersion == "2026-10-01.1"
        assert digest == sha256_of_bytes(SAMPLE_PATH.read_bytes())

    def test_places_reference_existing_nodes(self, sample_pkg):
        node_ids = set()
        for floor in sample_pkg.floors.values():
            node_ids.update(n.id for n in floor.topology.nodes)
        for place in sample_pkg.places:
            assert place.nodeId in node_ids

    def test_every_floor_has_elevator_facility(self, sample_pkg):
        for floor_id, floor in sample_pkg.floors.items():
            elevators = [
                n for n in floor.topology.nodes
                if n.type == "facility" and n.facilityType == "elevator"
            ]
            assert elevators, f"楼层 {floor_id} 缺少电梯设施节点"


class TestSemanticValidation:
    """语义校验：引用完整性 / 唯一性 / 连通性 / 盲模式跨层。"""

    def _mutated(self, sample_pkg) -> dict:
        return json.loads(sample_pkg.model_dump_json())

    def test_edge_to_unknown_node_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["floors"]["1"]["topology"]["edges"][0]["to"] = "F1-TX-9999"
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("不存在的节点" in p for p in problems)

    def test_duplicate_place_id_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["places"].append(copy.deepcopy(data["places"][0]))
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("地点 ID 重复" in p for p in problems)

    def test_duplicate_beacon_id_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["beacons"].append(copy.deepcopy(data["beacons"][0]))
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("信标 ID 重复" in p for p in problems)

    def test_place_on_wrong_floor_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["places"][0]["floorId"] = "2"  # F1-TR-0001 在 1 层
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("不一致" in p for p in problems)

    def test_beacon_on_unknown_floor_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["beacons"][0]["floorId"] = "9"
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("不存在的楼层" in p for p in problems)

    def test_isolated_node_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["floors"]["1"]["topology"]["nodes"].append(
            {
                "id": "F1-TR-0003", "type": "room", "roomType": "office",
                "roomId": "F1-RM-0004", "label": "孤立办公室",
                "coordinates": [20.0, 20.0],
            }
        )
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("孤立节点" in p for p in problems)

    def test_disconnected_topology_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        # 删除两层的全部同层边 → 跨层边把两个楼梯/电梯分量连着，但房间脱离
        data["floors"]["1"]["topology"]["edges"] = []
        data["floors"]["2"]["topology"]["edges"] = []
        pkg = MapPackage.model_validate(data)
        problems = validate_map_package(pkg)
        assert any("不连通" in p for p in problems)

    def test_multistory_without_elevator_edge_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["crossFloorEdges"] = [
            e for e in data["crossFloorEdges"] if e["type"] != "elevator"
        ]
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("电梯跨层边" in p for p in problems)

    def test_cross_floor_edge_same_floor_rejected(self, sample_pkg):
        data = self._mutated(sample_pkg)
        data["crossFloorEdges"][0]["toFloor"] = "1"
        problems = validate_map_package(MapPackage.model_validate(data))
        assert any("起终楼层相同" in p for p in problems)


class TestSchemaRejection:
    def test_invalid_node_type_rejected(self, sample_pkg):
        data = json.loads(sample_pkg.model_dump_json())
        data["floors"]["1"]["topology"]["nodes"][0]["type"] = "portal"
        with pytest.raises(Exception):
            MapPackage.model_validate(data)

    def test_negative_edge_distance_rejected(self, sample_pkg):
        data = json.loads(sample_pkg.model_dump_json())
        data["floors"]["1"]["topology"]["edges"][0]["distance"] = -1.0
        with pytest.raises(Exception):
            MapPackage.model_validate(data)

    def test_non_linestring_wall_rejected(self, sample_pkg):
        data = json.loads(sample_pkg.model_dump_json())
        data["floors"]["1"]["geometry"]["walls"][0]["geometry"]["type"] = "Polygon"
        with pytest.raises(Exception):
            MapPackage.model_validate(data)

    def test_missing_published_at_rejected(self, sample_pkg):
        data = json.loads(sample_pkg.model_dump_json())
        del data["publishedAt"]
        with pytest.raises(Exception):
            MapPackage.model_validate(data)


class TestRouteEngineCompat:
    """地图包必须能直接被已验证的受限 Dijkstra 引擎（src/topology）消费。

    注意：RouteGraph 消费原始 JSON dict（from/to 原字段名）；
    MapPackage 模型只是校验视图。
    """

    @pytest.fixture(scope="class")
    def raw_dict(self, sample_raw) -> dict:
        return json.loads(sample_raw)

    def test_route_graph_consumes_map_package(self, raw_dict):
        from src.topology.route_rules import RouteGraph

        graph = RouteGraph(raw_dict)
        assert len(graph.nodes) == 14  # 每层 7 节点 × 2 层

    def test_blind_same_floor_route(self, raw_dict):
        from src.topology.route_rules import RouteGraph

        graph = RouteGraph(raw_dict)
        route = graph.generate_route("F2-TR-0001", "F2-TI-0001", mode="blind")
        assert route["reachable"] is True
        assert route["valid"] is True
        assert route["cross_floor"] is False

    def test_blind_cross_floor_uses_elevator(self, raw_dict):
        from src.topology.route_rules import RouteGraph

        graph = RouteGraph(raw_dict)
        route = graph.generate_route("F1-TR-0002", "F2-TR-0001", mode="blind")
        assert route["reachable"] is True
        assert route["cross_floor"] is True
        assert route["used_elevator"] is True
        assert route["used_stair"] is False

    def test_normal_mode_cross_floor_allowed_via_stair(self, raw_dict):
        from src.topology.route_rules import RouteGraph

        graph = RouteGraph(raw_dict)
        route = graph.generate_route("F1-TF-0001", "F2-TF-0001", mode="normal")
        assert route["reachable"] is True
        assert route["used_stair"] is True
