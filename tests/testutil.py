"""golden 回归测试公共工具：路径解析、统计计算、golden 断言与再生成。

用法见 tests/README.md。统计复用 src/qa/validate_geojson 的校验函数，
保证「校验口径」与「golden 口径」同源。
"""
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RESULT_DIR = PROJECT_ROOT / "result"
GEOJSON_PATH = RESULT_DIR / "school_building_01_map_v9.geojson"
GOLDEN_PATH = Path(__file__).with_name("golden_stats.json")
STRUCT_GOLDEN_PATH = Path(__file__).with_name("parsing_result_golden.json")

FLOOR_STAT_KEYS = [
    "nodes", "edges", "TR", "TD", "TI", "TF", "TEN",
    "doors_geom", "skeleton_segs", "walkable_feats",
    "rooms_total", "rooms_with_door", "main_component", "components",
    "coverage", "skeleton_short_ratio", "skeleton_total_len_m",
]
XE_STAT_KEYS = ["count", "stair", "elevator"]

# 结构指纹中只锁数量、不锁 id 列表的原始几何集合（段级噪声大、语义弱）
GEOMETRY_COUNT_KEYS = ["walls", "columns", "stairs", "elevators", "elevatorDoors", "windowSegments"]


def load_json(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compute_stats(geo):
    """从 GeoJSON 提取 golden 统计：每层结构计数 + 跨层边计数。

    口径与 validate_geojson.py 完全一致（同一函数计算）。
    """
    from src.qa.validate_geojson import validate_floor, validate_cross_floor
    floors = {}
    for fk in geo.get("floors") or {}:
        st = validate_floor(fk, geo["floors"][fk], [])
        floors[str(fk)] = {k: st[k] for k in FLOOR_STAT_KEYS}
    xe = validate_cross_floor(geo, [])
    return {"floors": floors, "crossFloorEdges": {k: xe[k] for k in XE_STAT_KEYS}}


def compute_struct_fingerprint(geo):
    """从 GeoJSON 提取「解析结果级」结构指纹。

    与 compute_stats 互补：统计只锁聚合数字，这里锁定结构形态——
    每层节点/边/门/房间/骨架/可步行区的 id 集合、拓扑连接 from->to、
    跨层边清单与类型分布。B1 拆分等重构导致 id 漂移、连接变更、
    要素增删时，能精确定位到楼层与集合。
    """
    floors = {}
    for fk in geo.get("floors") or {}:
        fl = geo["floors"][fk]
        topo = fl.get("topology") or {}
        geom = fl.get("geometry") or {}
        nodes = topo.get("nodes") or []
        edges = topo.get("edges") or []
        node_types = {}
        for n in nodes:
            t = n.get("type")
            node_types[t] = node_types.get(t, 0) + 1
        rooms = geom.get("rooms") or []
        room_types = {}
        for r in rooms:
            room_types[r["id"]] = (r.get("properties") or {}).get("roomType")
        acc = fl.get("accessibility") or {}
        floors[str(fk)] = {
            "nodeIds": sorted(n["id"] for n in nodes),
            "nodeTypes": dict(sorted(node_types.items())),
            "edgeIds": sorted(e["id"] for e in edges),
            "edgeRefs": sorted(f"{e['from']}->{e['to']}" for e in edges),
            "doorIds": sorted(d["id"] for d in geom.get("doors") or []),
            "roomIds": sorted(r["id"] for r in rooms),
            "roomTypes": dict(sorted(room_types.items())),
            "skeletonIds": sorted(f["id"] for f in (fl.get("skeleton") or {}).get("features") or []),
            "walkableIds": sorted(
                f["id"] for f in (fl.get("walkable_regions") or {}).get("features") or []
            ),
            "geometryCounts": {k: len(geom.get(k) or []) for k in GEOMETRY_COUNT_KEYS},
            "accessibilityElevatorIds": sorted(x["id"] for x in acc.get("elevators") or []),
        }
    xe = geo.get("crossFloorEdges") or []
    xe_types = {}
    for x in xe:
        t = x.get("type")
        xe_types[t] = xe_types.get(t, 0) + 1
    return {
        "floors": floors,
        "crossFloorEdges": {
            "ids": sorted(x["id"] for x in xe),
            "pairs": sorted(f"{x['fromFloor']}->{x['toFloor']}" for x in xe),
            "links": sorted(f"{x['from']}->{x['to']}" for x in xe),
            "types": dict(sorted(xe_types.items())),
        },
    }


def load_golden():
    return load_json(GOLDEN_PATH)


def save_golden(stats, source_path):
    payload = {
        "schema": 1,
        "venueId": "school-building-01",
        "version": "9.0.0",
        "generated_at": "2026-08-12",
        "source": str(source_path),
        "source_sha256": sha256(source_path),
        "stats": stats,
    }
    with open(GOLDEN_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return payload


def regen_enabled():
    return os.environ.get("REGEN_GOLDEN", "").lower() in ("1", "true", "yes")


def _collect_diffs(a, e, prefix=""):
    """逐 key 收集 a 与 e 的差异（a=实际，e=期望）。"""
    diffs = []
    if isinstance(e, dict):
        for k in sorted(set(a) | set(e)):
            diffs.extend(_collect_diffs(a.get(k), e.get(k), f"{prefix}{k}."))
    elif a != e:
        diffs.append(f"{prefix[:-1]}: {e} -> {a}")
    return diffs


def assert_golden(testcase, actual, golden, source_path=GEOJSON_PATH):
    """断言 actual == golden["stats"]；不一致时列出差异，REGEN_GOLDEN=1 时自动更新。"""
    expected = golden["stats"]
    if actual == expected:
        return
    if regen_enabled():
        save_golden(actual, source_path)
        print(f"[REGEN_GOLDEN] 统计已更新 -> {GOLDEN_PATH.name}")
        return
    testcase.maxDiff = None
    raise AssertionError(
        "统计与 golden 不符（若为有意变更，请以 REGEN_GOLDEN=1 重跑 "
        "python -m unittest tests.test_geojson_golden）：\n"
        + "\n".join(_collect_diffs(actual, expected))
    )


def load_struct_golden():
    return load_json(STRUCT_GOLDEN_PATH)


def save_struct_golden(fingerprint, source_path):
    payload = {
        "schema": 1,
        "venueId": "school-building-01",
        "version": "9.0.0",
        "generated_at": "2026-08-12",
        "source": str(source_path),
        "source_sha256": sha256(source_path),
        "fingerprint": fingerprint,
    }
    with open(STRUCT_GOLDEN_PATH, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    return payload


def assert_struct_golden(testcase, actual, golden, source_path=GEOJSON_PATH):
    """断言 actual == golden["fingerprint"]；不一致或缺失时按 REGEN_GOLDEN 处理。"""
    if golden is None:
        if regen_enabled():
            save_struct_golden(actual, source_path)
            print(f"[REGEN_GOLDEN] 结构指纹已生成 -> {STRUCT_GOLDEN_PATH.name}")
            return
        raise AssertionError(
            f"结构指纹 golden 缺失：{STRUCT_GOLDEN_PATH.name}"
            "（首次生成请以 REGEN_GOLDEN=1 重跑）"
        )
    expected = golden["fingerprint"]
    if actual == expected:
        return
    if regen_enabled():
        save_struct_golden(actual, source_path)
        print(f"[REGEN_GOLDEN] 结构指纹已更新 -> {STRUCT_GOLDEN_PATH.name}")
        return
    testcase.maxDiff = None
    raise AssertionError(
        "解析结果结构与 golden 不符（若为有意变更，请以 REGEN_GOLDEN=1 重跑 "
        "python -m unittest tests.test_parsing_result_golden）：\n"
        + "\n".join(_collect_diffs(actual, expected))
    )


def skip_if_missing(*modnames):
    import importlib.util
    import unittest
    missing = [m for m in modnames if importlib.util.find_spec(m) is None]
    if missing:
        raise unittest.SkipTest(f"缺少依赖，跳过（{', '.join(missing)}）")
