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

FLOOR_STAT_KEYS = [
    "nodes", "edges", "TR", "TD", "TI", "TF", "TEN",
    "doors_geom", "skeleton_segs", "walkable_feats",
    "rooms_total", "rooms_with_door", "main_component", "components",
    "coverage", "skeleton_short_ratio", "skeleton_total_len_m",
]
XE_STAT_KEYS = ["count", "stair", "elevator"]


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
    diffs = []

    def walk(a, e, prefix=""):
        if isinstance(e, dict):
            for k in sorted(set(a) | set(e)):
                walk(a.get(k), e.get(k), f"{prefix}{k}.")
        elif a != e:
            diffs.append(f"{prefix[:-1]}: {e} -> {a}")

    walk(actual, expected)
    raise AssertionError(
        "统计与 golden 不符（若为有意变更，请运行 python -m tests.gen_golden）：\n" + "\n".join(diffs)
    )


def skip_if_missing(*modnames):
    import importlib.util
    import unittest
    missing = [m for m in modnames if importlib.util.find_spec(m) is None]
    if missing:
        raise unittest.SkipTest(f"缺少依赖，跳过（{', '.join(missing)}）")
