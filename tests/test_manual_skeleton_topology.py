import contextlib
import io
import unittest
from unittest.mock import patch

import networkx as nx

from src.skeleton import pipeline
from src.tools.import_manual_skeleton import build_from_lines


def _components(nodes, edges):
    """按骨架边求 TI 连通分量。"""
    parent = {n["id"]: n["id"] for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        if e["from"] not in parent or e["to"] not in parent:
            continue
        ra, rb = find(e["from"]), find(e["to"])
        if ra != rb:
            parent[ra] = rb
    groups = {}
    for nid in parent:
        groups.setdefault(find(nid), []).append(nid)
    return list(groups.values())


class TestManualSkeletonTopology(unittest.TestCase):
    def test_crossing_snapped_ti_splits_both_source_segments(self):
        existing = [{"id": "F1-TI-0099", "coordinates": [0.4, 0.0]}]
        _, nodes, edges = build_from_lines(
            [[(-2.0, 0.0), (2.0, 0.0)], [(0.0, -2.0), (0.0, 2.0)]],
            "1",
            existing_ti=existing,
            snap_tol=0.5,
        )

        snapped_id = "F1-TI-0099"
        incident = [e for e in edges if snapped_id in (e["from"], e["to"])]
        self.assertEqual(sum(n["id"] == snapped_id for n in nodes), 1)
        self.assertEqual(len(incident), 4)
        self.assertEqual(len({e["id"] for e in incident}), 4)
        self.assertEqual(len(edges), 4)

    def test_near_but_non_crossing_lines_remain_disconnected(self):
        _, nodes, edges = build_from_lines(
            [[(0.0, 0.0), (4.0, 0.0)], [(0.0, 0.5), (4.0, 0.5)]],
            "1",
        )

        self.assertEqual(len(nodes), 4)
        self.assertEqual(len(edges), 2)
        components = [{e["from"], e["to"]} for e in edges]
        self.assertTrue(components[0].isdisjoint(components[1]))

    def test_crossings_within_drafting_error_share_one_junction(self):
        """同一物理路口的两个候选点（相距 4cm 手绘误差）必须归并为一个 TI。

        回归：不归并会产生相距厘米级的重复交叉口，进而把走廊切成孤岛
        （历史 F2 东翼 93 节点的岛就是 0.04m 的双节点造成的）。
        """
        _, nodes, edges = build_from_lines(
            [[(-2.0, 0.0), (2.0, 0.0)],
             [(0.0, -2.0), (0.0, 2.0)],
             [(0.04, -2.0), (0.04, 2.0)]],
            "1",
        )

        # 4cm 间距的重复笔画连端点一起归并：横线两端 + 一个路口 + 竖线两端 = 5
        self.assertEqual(len(nodes), 5)
        coords = [tuple(n["coordinates"]) for n in nodes]
        closest = min(
            ((coords[i][0] - coords[j][0]) ** 2 + (coords[i][1] - coords[j][1]) ** 2) ** 0.5
            for i in range(len(coords)) for j in range(i + 1, len(coords))
        )
        self.assertGreaterEqual(closest, 0.3, "TI 之间仍存在厘米级重复节点")
        self.assertEqual(len(_components(nodes, edges)), 1)

    def test_gap_stitching_joins_a_stroke_that_stops_short(self):
        """线端差 0.15m 没压到另一条线的中段（笔画误差）→ 缝到投影点。"""
        _, nodes, edges = build_from_lines(
            [[(0.0, 0.0), (4.0, 0.0)], [(2.0, 0.15), (2.0, 4.0)]],
            "1",
        )

        self.assertEqual(len(_components(nodes, edges)), 1)
        # 缝合把切点插到横线中段 → 横线被切成两段约 2m 的边
        self.assertEqual(len([e for e in edges if abs(e["distance"] - 2.0) < 0.01]), 2)

    def test_gap_beyond_tolerance_is_not_bridged(self):
        """缺口 > 0.2m 视为真实断开，不得用距离补桥掩盖。"""
        _, nodes, edges = build_from_lines(
            [[(0.0, 0.0), (4.0, 0.0)], [(2.0, 0.5), (2.0, 4.0)]],
            "1",
        )

        self.assertEqual(len(_components(nodes, edges)), 2)

    def test_manual_pipeline_does_not_add_distance_based_ti_bridges(self):
        ti_nodes = []
        manual_edges = []
        for group, y in enumerate((0.0, 4.0)):
            ids = [f"F1-TI-{group * 5 + i + 1:04d}" for i in range(5)]
            for i, node_id in enumerate(ids):
                ti_nodes.append({
                    "id": node_id,
                    "type": "intersection",
                    "coordinates": [float(i), y],
                })
            for i, (a, b) in enumerate(zip(ids, ids[1:])):
                manual_edges.append({
                    "id": f"F1-TE-{group * 4 + i + 1:04d}",
                    "from": a,
                    "to": b,
                    "distance": 1.0,
                })

        with contextlib.redirect_stdout(io.StringIO()):
            result = pipeline.build_skeleton_topology(
                floor_no=1,
                rooms=[],
                doors=[],
                stairs=[],
                elevators=[],
                manual_skeleton={
                    "ti_nodes": ti_nodes,
                    "edges": manual_edges,
                    "skeleton_features": [],
                },
            )

        self.assertEqual(len(result["edges"]), len(manual_edges))
        self.assertEqual(
            {tuple(sorted((e["from"], e["to"]))) for e in result["edges"]},
            {tuple(sorted((e["from"], e["to"]))) for e in manual_edges},
        )

    def test_automatic_pipeline_keeps_distance_based_ti_attachment(self):
        graph = nx.Graph()
        graph.add_node("a", x=0.0, y=0.0)
        graph.add_node("b", x=5.0, y=0.0)
        skeleton = {
            "graph": graph,
            "lines": [],
            "junctions": [],
            "terminals": [],
            "key_nodes": [("a", 0.0, 0.0), ("b", 5.0, 0.0)],
            "empty": False,
        }
        with patch.object(pipeline, "build_skeleton_for_walkables", return_value=skeleton):
            with contextlib.redirect_stdout(io.StringIO()):
                result = pipeline.build_skeleton_topology(
                    floor_no=1,
                    rooms=[],
                    doors=[],
                    stairs=[],
                    elevators=[],
                )

        ti_ids = {n["id"] for n in result["nodes"] if n.get("type") == "intersection"}
        self.assertEqual(len(ti_ids), 2)
        self.assertTrue(any(
            e["from"] in ti_ids and e["to"] in ti_ids for e in result["edges"]
        ))


class TestParsedSkeletonConnectivity(unittest.TestCase):
    """真实手绘骨架（result/skeleton_manual_parsed.json）的连通性门禁。

    回归背景：交点切分后若不做厘米级去重与缺口缝合，F2 会被切成
    199+93 的孤岛，连带 geojson 的 QA 由 PASS 变 FAIL。
    """

    @classmethod
    def setUpClass(cls):
        try:
            from testutil import PROJECT_ROOT, load_json
        except ImportError:
            from tests.testutil import PROJECT_ROOT, load_json
        cls.payload = load_json(PROJECT_ROOT / "result" / "skeleton_manual_parsed.json")

    def test_each_floor_is_one_component(self):
        for fk, r in self.payload.items():
            comps = _components(r["ti_nodes"], r["edges"])
            self.assertEqual(len(comps), 1, f"F{fk} 骨架分成 {len(comps)} 个分量")

    def test_no_centimetre_duplicate_junctions(self):
        for fk, r in self.payload.items():
            coords = [n["coordinates"] for n in r["ti_nodes"]]
            closest = min(
                ((coords[i][0] - coords[j][0]) ** 2
                 + (coords[i][1] - coords[j][1]) ** 2) ** 0.5
                for i in range(len(coords)) for j in range(i + 1, len(coords))
            )
            self.assertGreater(closest, 0.29,
                               f"F{fk} 仍存在厘米级重复交叉口（最小间距 {closest:.3f}m）")


if __name__ == "__main__":
    unittest.main()
