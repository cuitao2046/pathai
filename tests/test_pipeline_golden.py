"""全链路 golden 回归：CAD PDF 解析 + 拓扑后处理，断言与已验收产物一致。

已验收产物 `result/school_building_01_map_v9.geojson` 的生成链路是：
  1) `parse_floor` × 2 → `build_geojson`（含手绘骨架优先路径）；
  2) `src.tools.fix_topology_walls.FloorRepair`（公共空间边不得穿墙、门/设施/出入口重挂）；
  3) `src.tools.merge_nearby_tis.merge_floor`（合并同层 <0.5m 的 TI↔TI）。
只跑 1) 会比产物多 24 个 TI 且边集不同，因此本测试按完整链路复跑，
参数与 CLI 默认值同源（DEFAULT_REPAIR_OPTS / DEFAULT_RADIUS）。

依赖 shapely/fitz，缺失时跳过；管线较慢且日志冗长，整链只跑一次并复用。
"""
import contextlib
import io
import unittest
from types import SimpleNamespace

from testutil import (
    assert_golden,
    assert_struct_golden,
    compute_stats,
    compute_struct_fingerprint,
    load_golden,
    load_struct_golden,
    skip_if_missing,
)
from src.tools.fix_topology_walls import DEFAULT_REPAIR_OPTS, FloorRepair
from src.tools.merge_nearby_tis import DEFAULT_RADIUS, merge_floor


def _run_full_chain(mod):
    """解析管线 + 两道拓扑后处理（原地修改 dict，不落盘）。"""
    opts = SimpleNamespace(**DEFAULT_REPAIR_OPTS)
    with contextlib.redirect_stdout(io.StringIO()):
        f1 = mod.parse_floor(mod.PDF_F1, 1)
        f2 = mod.parse_floor(mod.PDF_F2, 2)
        geo = mod.build_geojson(f1, f2)
        for floor_id in sorted(geo["floors"]):
            FloorRepair(floor_id, geo["floors"][floor_id], opts).run()
        for floor_id in sorted(geo["floors"]):
            merge_floor(geo["floors"][floor_id], DEFAULT_RADIUS)
    return geo


class TestPipelineGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skip_if_missing("shapely", "fitz")
        import src.parsing.parse_cad_pdf as mod

        cls.geo = _run_full_chain(mod)

    def test_pipeline_reproduces_golden(self):
        golden = load_golden()
        self.assertEqual(self.geo.get("venueId"), golden["venueId"])
        self.assertEqual(self.geo.get("version"), golden["version"])
        assert_golden(self, compute_stats(self.geo), golden)

    def test_pipeline_reproduces_struct_fingerprint(self):
        assert_struct_golden(self, compute_struct_fingerprint(self.geo), load_struct_golden())


class TestCandidateIsolation(unittest.TestCase):
    def setUp(self):
        skip_if_missing("shapely", "fitz")
        import src.parsing.parse_cad_pdf as mod

        self.mod = mod

    def test_detect_doors_preserves_each_arc_candidate(self):
        """相同位置的独立摆弧候选不得被聚类合并。"""
        curve = ((0.0, 0.0), (0.0, 5.0), (5.0, 10.0), (10.0, 10.0))
        doors = self.mod.detect_doors([curve, curve], [], [])
        self.assertEqual(len(doors), 2)
        self.assertTrue(all("merged" not in door for door in doors))

    def test_find_wall_openings_preserves_nearby_distinct_dk_candidates(self):
        """相邻但独立的 DK 候选不得按位置距离过滤。"""
        openings = self.mod.find_wall_openings(
            [(0.0, 0.0), (3.0, 0.0)],
            [((-20.0, 0.0), (20.0, 0.0))],
        )
        self.assertEqual(len(openings), 2)


if __name__ == "__main__":
    unittest.main()
