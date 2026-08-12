"""跨输出不变量测试：锁定「一扇门一 TD」「跨层边特殊值」「常量唯一来源」。

这些不变量独立于具体统计数值，后续重构（A2 路由收敛 / B1 拆分 / B5 配置外置）
之后仍须成立，是 golden 统计之外的第二道防线。
"""
import ast
import unittest
from pathlib import Path

from testutil import GEOJSON_PATH, PROJECT_ROOT, load_json

# D5：跨层边特殊值（楼梯/电梯 accessibility 与时间成本）
XE_STAIR = {"distance": 4.2, "estimatedTime": 60.0, "accessibilityLevel": 999, "riskLevel": 10}
XE_ELEVATOR = {"distance": 4.2, "estimatedTime": 15.0, "accessibilityLevel": 0, "riskLevel": 1}


class TestDoorUniqueness(unittest.TestCase):
    """门不合并不变量（ADR-01）：每扇门恰好映射到一个 doorway 节点。"""

    def test_one_to_one_mapping(self):
        geo = load_json(GEOJSON_PATH)
        for fk, fl in geo["floors"].items():
            doors = fl["geometry"].get("doors") or []
            dws = [n for n in fl["topology"]["nodes"] if n.get("type") == "doorway"]
            counter = {d["id"]: 0 for d in doors}
            no_src = multi_src = unknown = 0
            for td in dws:
                ids = td.get("sourceDoorIds") or []
                if not ids:
                    no_src += 1
                if len(ids) > 1:
                    multi_src += 1
                for i in ids:
                    if i in counter:
                        counter[i] += 1
                    else:
                        unknown += 1
            dup = [k for k, v in counter.items() if v != 1]
            self.assertEqual(multi_src, 0, f"F{fk} 存在 TD 合并多扇门")
            self.assertEqual(unknown, 0, f"F{fk} 存在未识别门 id")
            self.assertEqual(dup, [], f"F{fk} 门被多个 TD 引用")
            # 无源 TD 数量 = TD - 门数（E1：无门卫生间/其他设施链接）
            self.assertEqual(no_src, len(dws) - len(doors), f"F{fk} 无源 TD 数异常")

    def test_door_counts_stable(self):
        geo = load_json(GEOJSON_PATH)
        expected = {"1": 132, "2": 76}
        for fk, n in expected.items():
            doors = geo["floors"][fk]["geometry"].get("doors") or []
            self.assertEqual(len(doors), n, f"F{fk} 门数量漂移")


class TestCrossFloorSpecialValues(unittest.TestCase):
    """跨层边特殊值（D5）。"""

    def test_stair_and_elevator_values(self):
        geo = load_json(GEOJSON_PATH)
        stairs = [x for x in geo["crossFloorEdges"] if x.get("type") == "staircase"]
        elevs = [x for x in geo["crossFloorEdges"] if x.get("type") == "elevator"]
        self.assertTrue(stairs, "缺少楼梯跨层边")
        self.assertTrue(elevs, "缺少电梯跨层边")
        for x in stairs:
            for k, v in XE_STAIR.items():
                self.assertEqual(x.get(k), v, f"{x['id']}.{k}")
        for x in elevs:
            for k, v in XE_ELEVATOR.items():
                self.assertEqual(x.get(k), v, f"{x['id']}.{k}")


class TestConstantsSingleSource(unittest.TestCase):
    """常量唯一来源（D1-D4）：业务常量只能在 constants.py 定义；
    允许 try/except 兜底，但值必须与 constants 一致；
    渲染像素比例（1m=Npx）语义不同，允许存在且须与米/pt 比例尺区分。
    """

    BUSINESS_CONSTS = {"SCALE", "ORIGIN_X", "ORIGIN_Y",
                       "BLIND_WALK_SPEED", "NORMAL_WALK_SPEED", "DOOR_PENALTY",
                       "DOOR_DEFAULT_PENALTY", "SAME_FLOOR_MID_TYPES", "CROSS_FLOOR_MID_TYPES"}

    # 允许的兜底重复定义（值必须与 constants 同名常量相等）
    CONSISTENT_ALLOWED = {
        "src/rendering/render_interactive.py": {"DOOR_PENALTY", "DOOR_DEFAULT_PENALTY",
                                                "SAME_FLOOR_MID_TYPES", "CROSS_FLOOR_MID_TYPES"},
        "src/tools/field_survey_calibrate.py": {"SCALE", "ORIGIN_X", "ORIGIN_Y"},
        "src/tools/merge_manual_edges.py": {"BLIND_WALK_SPEED"},
    }

    # 渲染像素比例：1m=Npx，语义不同于米/pt，允许且值必须不等于 constants.SCALE
    PIXEL_SCALE_ALLOWED = {
        "src/rendering/render_interactive.py": {"SCALE"},
        "src/tools/export_skeleton_template.py": {"SCALE"},
        "src/tools/import_manual_skeleton.py": {"SCALE"},
    }

    CORE_FILES = [
        "src/parsing/parse_cad_pdf.py",
        "src/topology/topology.py",
        "src/topology/route_rules.py",
        "src/pipeline/pipeline.py",
        "src/rendering/render_interactive.py",
        "src/tools/export_skeleton_template.py",
        "src/tools/field_survey_calibrate.py",
        "src/tools/import_manual_skeleton.py",
        "src/tools/merge_manual_edges.py",
    ]

    def _collect_assignments(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = {}

        def resolve(node):
            if isinstance(node, ast.Name):
                return found.get(node.id, "<nonliteral>")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                left, right = resolve(node.left), resolve(node.right)
                if isinstance(left, set) and isinstance(right, set):
                    return left | right
                return "<nonliteral>"
            try:
                return ast.literal_eval(node)
            except Exception:
                return "<nonliteral>"

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in self.BUSINESS_CONSTS:
                        found[t.id] = resolve(node.value)
        return found

    def test_no_rogue_constant_redefinitions(self):
        import src.common.constants as c
        for rel in self.CORE_FILES:
            p = PROJECT_ROOT / rel
            if not p.exists():
                continue
            for name, value in self._collect_assignments(p).items():
                if name in self.CONSISTENT_ALLOWED.get(rel, set()):
                    self.assertEqual(
                        value, getattr(c, name),
                        f"{rel} 兜底常量 {name} 与 constants.py 不一致")
                elif name in self.PIXEL_SCALE_ALLOWED.get(rel, set()):
                    self.assertNotEqual(
                        value, c.SCALE,
                        f"{rel} 的 {name} 疑似误用米/pt 比例尺")
                else:
                    self.fail(f"{rel} 重复定义了业务常量 {name}")


if __name__ == "__main__":
    unittest.main()
