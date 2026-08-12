"""解析结果级 golden 回归：锁定解析/几何输出的结构形态。

作为 B1 巨型文件拆分的前置安全网，与既有两层防护互补：
- SHA-256（test_geojson_golden）：只锁字节，无法定位漂移位置；
- 统计（golden_stats.json）：只锁聚合数字，抓不到 id 漂移/连接变更；
本测试锁定「结构指纹」——每层节点/边/门/房间/骨架/可步行区的
id 集合、拓扑连接 from->to、跨层边清单与类型分布，漂移时能精确
定位到楼层与集合；另附引用完整性不变量（边端点存在、跨层边可达）。
"""
import unittest

try:
    from testutil import (
        GEOJSON_PATH,
        STRUCT_GOLDEN_PATH,
        assert_struct_golden,
        compute_struct_fingerprint,
        load_json,
        load_struct_golden,
        regen_enabled,
        sha256,
    )
except ImportError:
    from tests.testutil import (
        GEOJSON_PATH,
        STRUCT_GOLDEN_PATH,
        assert_struct_golden,
        compute_struct_fingerprint,
        load_json,
        load_struct_golden,
        regen_enabled,
        sha256,
    )


class TestParsingResultGoldenSanity(unittest.TestCase):
    """结构指纹 golden 参照文件自身完整性。"""

    def test_golden_file_exists(self):
        self.assertTrue(
            STRUCT_GOLDEN_PATH.exists(), f"基线缺失: {STRUCT_GOLDEN_PATH}"
        )

    def test_source_sha256_matches(self):
        if regen_enabled():
            self.skipTest("REGEN_GOLDEN=1 模式，跳过哈希校验（参照将更新）")
        golden = load_struct_golden()
        self.assertEqual(sha256(GEOJSON_PATH), golden["source_sha256"])

    def test_golden_version(self):
        golden = load_struct_golden()
        geo = load_json(GEOJSON_PATH)
        self.assertEqual(geo.get("version"), golden.get("version"))


class TestParsingResultFingerprint(unittest.TestCase):
    """静态结构指纹 golden：结果文件结构必须与参照一致。"""

    def test_struct_fingerprint(self):
        geo = load_json(GEOJSON_PATH)
        actual = compute_struct_fingerprint(geo)
        try:
            golden = load_struct_golden()
        except FileNotFoundError:
            golden = None
        assert_struct_golden(self, actual, golden, source_path=GEOJSON_PATH)


class TestParsingResultReferenceIntegrity(unittest.TestCase):
    """结构不变量：引用完整性，独立于具体 golden 数值。"""

    def test_edge_endpoints_exist(self):
        geo = load_json(GEOJSON_PATH)
        for fk, fl in geo["floors"].items():
            node_ids = {n["id"] for n in fl["topology"]["nodes"]}
            for e in fl["topology"]["edges"]:
                self.assertIn(e["from"], node_ids, f"F{fk} {e['id']}.from 悬空")
                self.assertIn(e["to"], node_ids, f"F{fk} {e['id']}.to 悬空")

    def test_cross_floor_links_exist(self):
        geo = load_json(GEOJSON_PATH)
        floors = geo["floors"]
        for x in geo.get("crossFloorEdges") or []:
            self.assertIn(str(x["fromFloor"]), floors, f"{x['id']} fromFloor 缺失")
            self.assertIn(str(x["toFloor"]), floors, f"{x['id']} toFloor 缺失")
            src_ids = {n["id"] for n in floors[str(x["fromFloor"])]["topology"]["nodes"]}
            dst_ids = {n["id"] for n in floors[str(x["toFloor"])]["topology"]["nodes"]}
            self.assertIn(x["from"], src_ids, f"{x['id']}.from 节点缺失")
            self.assertIn(x["to"], dst_ids, f"{x['id']}.to 节点缺失")

    def test_ids_unique_within_collections(self):
        geo = load_json(GEOJSON_PATH)
        for fk, fl in geo["floors"].items():
            collections = {
                "nodes": [n["id"] for n in fl["topology"]["nodes"]],
                "edges": [e["id"] for e in fl["topology"]["edges"]],
                "doors": [d["id"] for d in fl["geometry"].get("doors") or []],
                "rooms": [r["id"] for r in fl["geometry"].get("rooms") or []],
                "skeleton": [f["id"] for f in fl["skeleton"]["features"]],
                "walkable": [f["id"] for f in fl["walkable_regions"]["features"]],
            }
            for name, ids in collections.items():
                seen = set()
                dup = sorted({i for i in ids if i in seen or seen.add(i)})
                self.assertEqual(dup, [], f"F{fk} {name} 存在重复 id")


if __name__ == "__main__":
    unittest.main()
