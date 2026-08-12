"""全链路 golden 回归：以当前源码重跑 CAD PDF 解析管线，断言统计与参照一致。

覆盖 parse_floor + build_geojson 全链路（含手动骨架优先路径），
可发现「静态校验通过但管线漂移」的回归。依赖 shapely/fitz，缺失时跳过。
解析 PDF 较慢且日志冗长，输出重定向到缓冲区。
"""
import contextlib
import io
import unittest

from testutil import (
    assert_golden,
    assert_struct_golden,
    compute_stats,
    compute_struct_fingerprint,
    load_golden,
    load_struct_golden,
    skip_if_missing,
)


class TestPipelineGolden(unittest.TestCase):
    def setUp(self):
        skip_if_missing("shapely", "fitz")
        import src.parsing.parse_cad_pdf as mod
        self.mod = mod
        # 重置手动骨架缓存，确保按当前 result/skeleton_manual_parsed.json 重新加载
        mod.MANUAL_SKELETON = None

    def _run_pipeline(self):
        mod = self.mod
        with contextlib.redirect_stdout(io.StringIO()):
            f1 = mod.parse_floor(mod.PDF_F1, 1)
            f2 = mod.parse_floor(mod.PDF_F2, 2)
            geo = mod.build_geojson(f1, f2)
        return geo

    def test_pipeline_reproduces_golden(self):
        geo = self._run_pipeline()
        golden = load_golden()
        self.assertEqual(geo.get("venueId"), golden["venueId"])
        self.assertEqual(geo.get("version"), golden["version"])
        actual = compute_stats(geo)
        assert_golden(self, actual, golden)

    def test_pipeline_reproduces_struct_fingerprint(self):
        geo = self._run_pipeline()
        golden = load_struct_golden()
        actual = compute_struct_fingerprint(geo)
        assert_struct_golden(self, actual, golden)


if __name__ == "__main__":
    unittest.main()
