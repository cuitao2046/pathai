import contextlib
import io
import unittest
from unittest.mock import patch

import networkx as nx

from src.skeleton import pipeline
from src.tools.import_manual_skeleton import build_from_lines


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
            [[(0.0, 0.0), (4.0, 0.0)], [(0.0, 0.2), (4.0, 0.2)]],
            "1",
        )

        self.assertEqual(len(nodes), 4)
        self.assertEqual(len(edges), 2)
        components = [{e["from"], e["to"]} for e in edges]
        self.assertTrue(components[0].isdisjoint(components[1]))

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


if __name__ == "__main__":
    unittest.main()
