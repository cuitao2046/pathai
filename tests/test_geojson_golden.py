"""静态 golden 回归：对已验收输出 result/*_map_v9.geojson 断言。

两重校验：
1. 文件 SHA-256 与 golden 记录一致（内容级锁定，任何改动即触发）；
2. 关键统计（复用 validate_geojson 口径）与 golden_stats.json 一致。
有意变更时用 REGEN_GOLDEN=1 更新参照（见 tests/README.md）。
"""
import unittest

from testutil import (
    GEOJSON_PATH,
    assert_golden,
    compute_stats,
    load_golden,
    load_json,
    regen_enabled,
    sha256,
)


class TestGoldenReferenceSanity(unittest.TestCase):
    """golden 参照文件自身完整性。"""

    def test_golden_file_exists(self):
        self.assertTrue(GEOJSON_PATH.exists(), f"基线缺失: {GEOJSON_PATH}")

    def test_source_sha256_matches(self):
        if regen_enabled():
            self.skipTest("REGEN_GOLDEN=1 模式，跳过哈希校验（参照将更新）")
        golden = load_golden()
        self.assertEqual(sha256(GEOJSON_PATH), golden["source_sha256"])

    def test_golden_version(self):
        golden = load_golden()
        geo = load_json(GEOJSON_PATH)
        self.assertEqual(geo.get("version"), golden.get("version"))


class TestStaticGoldenStats(unittest.TestCase):
    """静态统计 golden：结果文件当前统计必须与参照一致。"""

    def test_floor_and_cross_floor_stats(self):
        geo = load_json(GEOJSON_PATH)
        actual = compute_stats(geo)
        assert_golden(self, actual, load_golden(), source_path=GEOJSON_PATH)


if __name__ == "__main__":
    unittest.main()
