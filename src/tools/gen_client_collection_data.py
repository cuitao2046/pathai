#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成导航小程序现场采集数据文件（client/data/*.ts）。

产物（AUTO-GENERATED，勿手改）：
- client/data/calibration_points.ts  预置静态标定点 20 个（docs/28 §3.1 配额：F1 16 / F2 4，分两批）
- client/data/fingerprint_grid.ts    设计层全量指纹网格（result/fingerprint_grid.json 转出）
- fingerprint-collector/data/fingerprint_grid.js  full/route 两个数据集（透传可采集标记）

标定点选点规则（确定性，从测试路线网格 result/fingerprint_grid_routes.json 就近选取）：
- corridor_mid   直线走廊中段：距最近 intersection/doorway 节点最远的路线网格点
- intersection   走廊交叉口：度数最高的 intersection 节点就近点（按间隔分散挑选）
- doorway        门洞：路线上 doorway 节点就近点
- elevator       电梯厅：跨层电梯边 F1 端点就近点（按 3m 聚类去重后取路线覆盖最密的两处）
- facility       设施入口（替代 docs/28 的「楼梯口」——楼梯语义缺失 R4）：facility_entrance 节点就近点
- lobby          开阔大厅：最大 lobby 房间质心就近点
- window_end     靠窗/尽端（金属遮挡代理）：距建筑质心最远的路线网格点
- weak_signal    已知信号弱区：可见信标数最少且 ≥3 的路线网格点（15m 平面半径）
批次：首批 12 点（F1 9 + F2 3，出全局参数）/ 次批 8 点（分组判定与阈值精修）。

用法：python src/tools/gen_client_collection_data.py
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAP_PACKAGE = PROJECT_ROOT / "backend" / "maps_data" / "school-building-01" / "2026-11-03.1.json"
ROUTES_GRID = PROJECT_ROOT / "result" / "fingerprint_grid_routes.json"
FULL_GRID = PROJECT_ROOT / "result" / "fingerprint_grid.json"
OUT_DIR = PROJECT_ROOT / "client" / "data"
COLLECTOR_JS = PROJECT_ROOT / "fingerprint-collector" / "data" / "fingerprint_grid.js"

VIS_RADIUS_M = 15.0  # E5 -12dBm 标称覆盖半径（与选弱区口径一致）

GRID_FULL_KEYS = [
    "fpId", "floorId", "x", "y", "regionType", "shortId", "col", "row", "isBase2m",
    "collectable", "collectable15m", "routeTags",
    "visible10m", "visible15m", "nearestBeaconM", "strongestRssi",
]

GRID_JS_KEYS = [
    "id", "fpId", "shortId", "floor", "coordinates", "x", "y", "col", "row",
    "regionType", "priority", "source", "tier", "isBase2m",
    "collectable", "collectable15m", "routeTags",
    "visible10m", "visible15m", "nearestBeaconM", "strongestRssi",
    "nearNodeId", "nearNodeType",
]


def dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


# ---------------------------------------------------------------------------
# 候选点集（测试路线网格）
# ---------------------------------------------------------------------------

def load_route_points() -> dict[str, list[dict]]:
    grid = load_json(ROUTES_GRID)
    return {
        fl: [
            {
                "id": p["id"],
                "xy": (float(p["coordinates"][0]), float(p["coordinates"][1])),
                "regionType": p.get("regionType") or "normal",
            }
            for p in data["points"]
        ]
        for fl, data in grid["floors"].items()
    }


def load_full_grid() -> list[dict]:
    grid = load_json(FULL_GRID)
    out: list[dict] = []
    for fl, data in grid["floors"].items():
        for p in data["points"]:
            out.append({
                "fpId": p["fpId"],
                "floorId": fl,
                "x": float(p["x"]),
                "y": float(p["y"]),
                "regionType": p.get("regionType") or "normal",
                "shortId": p.get("shortId") or "",
                "col": int(p.get("col") or 0),
                "row": int(p.get("row") or 0),
                "isBase2m": bool(p.get("isBase2m")),
                "collectable": bool(p.get("collectablePlan10m")),
                "collectable15m": bool(p.get("collectableGeo15m")),
                "routeTags": list(p.get("routeTags") or []),
                "visible10m": int(p.get("visible10m") or 0),
                "visible15m": int(p.get("visible15m") or 0),
                "nearestBeaconM": p.get("nearestBeaconM"),
                "strongestRssi": p.get("strongestRssi"),
            })
    out.sort(key=lambda r: (int(r["floorId"]), r["row"], r["col"]))
    return out


# ---------------------------------------------------------------------------
# 预置标定点选取
# ---------------------------------------------------------------------------

def nodes_by_floor(pkg: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for fl, data in pkg["floors"].items():
        out[fl] = data["topology"]["nodes"]
    return out


def walk_degree(pkg: dict, floor_id: str) -> dict[str, int]:
    deg: dict[str, int] = defaultdict(int)
    for e in pkg["floors"][floor_id]["topology"]["edges"]:
        if not (e.get("walkable") and e.get("blindAccessible", True)):
            continue
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    return dict(deg)


def nearest_point(points: list[dict], xy: tuple[float, float], used: set[str]) -> dict | None:
    best, best_d = None, math.inf
    for p in points:
        if p["id"] in used:
            continue
        d = dist(p["xy"], xy)
        if d < best_d:
            best, best_d = p, d
    return best


def pick_spread(points: list[dict], anchors: list[dict], used: set[str], count: int, min_sep_m: float) -> list[dict]:
    """按 anchor 顺序就近取点，已选点间保持 min_sep_m 间隔（分散覆盖）。"""
    picked: list[dict] = []
    for a in anchors:
        if len(picked) >= count:
            break
        p = nearest_point(points, (a["x"], a["y"]), used)
        if p is None:
            continue
        if any(dist(p["xy"], q["xy"]) < min_sep_m for q in picked):
            continue
        used.add(p["id"])
        picked.append(p)
    return picked


def elevator_anchor_clusters(pkg: dict, floor_id: str) -> list[dict]:
    """跨层电梯边在本层的端点按 3m 聚类；返回簇质心列表（按路线覆盖降序无效化，保持稳定序）。"""
    ends: list[tuple[float, float]] = []
    for e in pkg["crossFloorEdges"]:
        if e.get("type") != "elevator":
            continue
        fl = e["fromFloor"] if e["fromFloor"] == floor_id else e["toFloor"]
        nid = e["from"] if e["fromFloor"] == floor_id else e["to"]
        node = next(n for n in pkg["floors"][fl]["topology"]["nodes"] if n["id"] == nid)
        ends.append((float(node["coordinates"][0]), float(node["coordinates"][1])))
    clusters: list[list[tuple[float, float]]] = []
    for xy in sorted(ends):
        for c in clusters:
            if dist(xy, c[0]) < 3.0:
                c.append(xy)
                break
        else:
            clusters.append([xy])
    return [{"x": sum(p[0] for p in c) / len(c), "y": sum(p[1] for p in c) / len(c)} for c in clusters]


def largest_lobby_centroid(pkg: dict, floor_id: str) -> dict | None:
    best, best_area = None, 0.0
    for room in pkg["floors"][floor_id]["geometry"].get("rooms") or []:
        props = room.get("properties") or {}
        rtype = (props.get("roomType") or props.get("type") or "").lower()
        if "lobby" not in rtype:
            continue
        rings = room["geometry"]["coordinates"]
        area = 0.0
        cx = cy = 0.0
        n = 0
        for ring in rings:
            for i in range(len(ring) - 1):
                x1, y1 = ring[i][0], ring[i][1]
                x2, y2 = ring[i + 1][0], ring[i + 1][1]
                area += x1 * y2 - x2 * y1
                cx += (x1 + x2) / 2
                cy += (y1 + y2) / 2
                n += 1
        if n == 0:
            continue
        if abs(area) > best_area:
            best_area = abs(area)
            best = {"x": cx / n, "y": cy / n}
    return best


def visibility_counts(points: list[dict], beacons: list[dict], floor_id: str) -> dict[str, int]:
    bc = [(float(b["x"]), float(b["y"])) for b in beacons if str(b.get("floorId")) == floor_id]
    out: dict[str, int] = {}
    for p in points:
        out[p["id"]] = sum(1 for bx, by in bc if dist(p["xy"], (bx, by)) <= VIS_RADIUS_M)
    return out


def building_centroid(points: list[dict]) -> tuple[float, float]:
    xs = [p["xy"][0] for p in points]
    ys = [p["xy"][1] for p in points]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def select_points(pkg: dict) -> list[dict]:
    route = load_route_points()
    beacons = pkg["beacons"]
    selected: list[dict] = []
    used: set[str] = set()

    # F1 16 点
    fl = "1"
    pts = route[fl]
    deg = walk_degree(pkg, fl)
    nodes = nodes_by_floor(pkg)[fl]

    def typed_nodes(t: str, min_deg: int = 0) -> list[dict]:
        out = []
        for n in nodes:
            if n["type"] != t or deg.get(n["id"], 0) < min_deg:
                continue
            out.append({"x": float(n["coordinates"][0]), "y": float(n["coordinates"][1]), "id": n["id"]})
        return out

    inter = sorted(typed_nodes("intersection"), key=lambda a: -deg.get(a["id"], 0))
    doors = typed_nodes("doorway")
    fac = typed_nodes("facility_entrance")

    picks: list[tuple[str, int, list[dict]]] = [
        # (scene, 批次, anchors)
        ("intersection", 1, inter[:6]),
        ("doorway", 1, doors[:8]),
        ("corridor_mid", 1, []),  # 下面特判
        ("elevator", 1, elevator_anchor_clusters(pkg, fl)[:2]),
        ("lobby", 1, [largest_lobby_centroid(pkg, fl) or {"x": pts[0]["xy"][0], "y": pts[0]["xy"][1]}]),
        ("intersection", 2, inter[6:]),
        ("doorway", 2, doors[8:]),
        ("corridor_mid", 2, []),
        ("elevator", 2, elevator_anchor_clusters(pkg, fl)[2:4]),
        ("facility", 2, fac[:4]),
    ]

    def corridor_mid_points(count: int, exclude: set[str]) -> list[dict]:
        # 距最近 intersection/doorway 最远的 normal 路线点
        anchors = typed_nodes("intersection") + typed_nodes("doorway")
        scored = []
        for p in pts:
            if p["id"] in exclude or p["regionType"] != "normal":
                continue
            dmin = min(dist(p["xy"], (a["x"], a["y"])) for a in anchors)
            scored.append((dmin, p))
        scored.sort(key=lambda t: -t[0])
        # 保持间隔 ≥15m，避免三点扎堆同一走廊
        out: list[dict] = []
        for _, p in scored:
            if len(out) >= count:
                break
            if any(dist(p["xy"], q["xy"]) < 15.0 for q in out):
                continue
            out.append(p)
        return out

    # 楼层质心 / 弱区（次批）
    centroid = building_centroid(pts)
    vis = visibility_counts(pts, beacons, fl)
    weak = min((p for p in pts if vis[p["id"]] >= 3), key=lambda p: vis[p["id"]])
    far = max(
        (p for p in pts if p["regionType"] == "normal" and dist(p["xy"], weak["xy"]) >= 8.0),
        key=lambda p: dist(p["xy"], centroid),
    )

    # 按批次顺序落位
    quota: list[tuple[str, int, int]] = [
        # (scene, batch, count)
        ("intersection", 1, 3), ("doorway", 1, 2), ("corridor_mid", 1, 2),
        ("elevator", 1, 1), ("lobby", 1, 1),
        ("intersection", 2, 1), ("corridor_mid", 2, 1),
        ("elevator", 2, 1), ("facility", 2, 2), ("window_end", 2, 1), ("weak_signal", 2, 1),
    ]
    for scene, batch, count in quota:
        if scene == "corridor_mid":
            picked = corridor_mid_points(count, used)
            for p in picked:
                used.add(p["id"])
        elif scene == "window_end":
            picked = [far] if far["id"] not in used else []
            used.add(far["id"])
        elif scene == "weak_signal":
            picked = [weak] if weak["id"] not in used else []
            used.add(weak["id"])
        else:
            anchors = next(a for s, _, a in picks if s == scene and _ == batch)
            picked = pick_spread(pts, anchors, used, count, min_sep_m=8.0)
        for p in picked:
            selected.append({
                "floorId": fl, "truth": p["xy"], "scene": scene, "batch": batch,
                "nearId": p["id"],
            })

    # F2 4 点：交叉口2 / 门洞1 / 电梯前室1
    fl = "2"
    pts2 = route[fl]
    deg2 = walk_degree(pkg, fl)
    nodes2 = nodes_by_floor(pkg)[fl]
    inter2 = sorted(
        [{"x": float(n["coordinates"][0]), "y": float(n["coordinates"][1]), "id": n["id"]}
         for n in nodes2 if n["type"] == "intersection"],
        key=lambda a: -deg2.get(a["id"], 0),
    )
    doors2 = [{"x": float(n["coordinates"][0]), "y": float(n["coordinates"][1]), "id": n["id"]}
              for n in nodes2 if n["type"] == "doorway"]
    quota2 = [("intersection", 1, 2), ("doorway", 1, 1), ("intersection", 2, 1)]
    for scene, batch, count in quota2:
        anchors = inter2 if scene == "intersection" else doors2
        picked = pick_spread(pts2, anchors, used, count, min_sep_m=8.0)
        for p in picked:
            used.add(p["id"])
            selected.append({"floorId": fl, "truth": p["xy"], "scene": scene, "batch": batch, "nearId": p["id"]})

    # 编号：楼层内连续编号（与批次无关，保证 pointId 唯一）
    counters: dict[str, int] = defaultdict(int)
    out = []
    for p in sorted(selected, key=lambda r: (r["floorId"], r["batch"], r["scene"])):
        counters[p["floorId"]] += 1
        n = counters[p["floorId"]]
        out.append({
            "pointId": f"CAL-{'F1' if p['floorId'] == '1' else 'F2'}-{n:02d}",
            "floorId": p["floorId"],
            "truth": [round(p["truth"][0], 3), round(p["truth"][1], 3)],
            "scene": p["scene"],
            "batch": p["batch"],
            "nearGridId": p["nearId"],
        })
    return out


# ---------------------------------------------------------------------------
# TS 渲染
# ---------------------------------------------------------------------------

CAL_TS_HEADER = """// AUTO-GENERATED by src/tools/gen_client_collection_data.py — do not edit.
// 数据来源：result/fingerprint_grid_routes.json（测试路线网格）+ 地图包拓扑（选点锚点）
// 预置静态标定点：docs/28 §3.1 配额 F1 16 / F2 4；批次 1=首批（全局参数）2=次批（分组判定）
// 楼梯口场景因地图楼梯语义缺失（R4）由 facility（设施入口）替代。

/** 预置标定点（真值坐标为地图局部米制）。 */
export interface PresetCalibrationPoint {
  pointId: string;
  floorId: string;
  truth: [number, number];
  /** 场景：corridor_mid/intersection/doorway/elevator/facility/lobby/window_end/weak_signal */
  scene: string;
  /** 采集批次：1=首批 12 点出全局参数；2=次批 8 点做分组判定 */
  batch: 1 | 2;
  /** 选点参照的路线网格点（现场找点辅助） */
  nearGridId: string;
}

export const PRESET_CALIBRATION_POINTS: PresetCalibrationPoint[] = """

FP_TS_HEADER = """// AUTO-GENERATED by src/tools/gen_client_collection_data.py — do not edit.
// 数据来源：result/fingerprint_grid.json（设计层全量 + 采集层标记，v{version}）

/** 指纹网格点（设计层全量；collectable=当前实际部署下 10m/-85/≥3 可采集）。 */
export interface FpGridPoint {
  fpId: string;
  floorId: string;
  x: number;
  y: number;
  /** normal=普通 / intersection=交叉路口 / doorway=门口 / high_risk=楼梯电梯口 */
  regionType: string;
  /** 短号，如 C056R100-J */
  shortId: string;
  /** 1m 格位列号 */
  col: number;
  /** 1m 格位行号 */
  row: number;
  /** 是否 2m 底网点（低缩放只画这些） */
  isBase2m: boolean;
  /** 默认采集口径：10m / 预测 >=-85dBm / >=3 信标 */
  collectable: boolean;
  /** 备用口径：15m / >=3 信标 */
  collectable15m: boolean;
  /** 命中的测试路线标签，如 ['R1'] */
  routeTags: string[];
  visible10m: number;
  visible15m: number;
  nearestBeaconM: number | null;
  strongestRssi: number | null;
}

export const FINGERPRINT_GRID_VERSION = '{version}';

export const FINGERPRINT_GRID: FpGridPoint[] = """


def render_value(v):
    if isinstance(v, str):
        return f"'{v}'"
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, list):
        return "[" + ", ".join(render_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k}: {render_value(x)}" for k, x in v.items()) + " }"
    return str(v)


def render_ts(header: str, items: list[dict], item_keys: list[str]) -> str:
    lines = [header, "["]
    for i, item in enumerate(items):
        parts = [f"{k}: {render_value(item[k])}" for k in item_keys]
        lines.append("  { " + ", ".join(parts) + " }" + ("," if i < len(items) - 1 else ","))
    lines.append("];")
    return "\n".join(lines) + "\n"


def _pick(item: dict, keys: list[str]) -> dict:
    return {k: item.get(k) for k in keys}


def write_collector_js(version: str) -> int:
    """生成 fingerprint-collector/data/fingerprint_grid.js（保留 full + route 两个数据集）。"""
    full = load_json(FULL_GRID)
    routes = load_json(ROUTES_GRID)

    full_points: list[dict] = []
    for fl, data in sorted(full["floors"].items(), key=lambda kv: int(kv[0])):
        for p in sorted(data["points"], key=lambda q: (q["row"], q["col"])):
            rec = _pick(p, [k for k in GRID_JS_KEYS if k not in ("collectable", "collectable15m")])
            rec["id"] = p["fpId"]
            rec["fpId"] = p["fpId"]
            rec["floor"] = int(fl)
            rec["coordinates"] = [p["x"], p["y"]]
            rec["collectable"] = bool(p.get("collectablePlan10m"))
            rec["collectable15m"] = bool(p.get("collectableGeo15m"))
            full_points.append(rec)

    route_points: list[dict] = []
    for fl, data in sorted(routes["floors"].items(), key=lambda kv: int(kv[0])):
        for p in data["points"]:
            rec = dict(p)
            rec.setdefault("fpId", p["id"])
            rec.setdefault("shortId", p["id"])
            route_points.append(rec)

    def dump(points: list[dict]) -> str:
        return "[" + ",\n".join(
            json.dumps(p, ensure_ascii=False, separators=(", ", ": ")) for p in points
        ) + "]"

    js = (
        "// AUTO-GENERATED by src/tools/gen_client_collection_data.py — do not edit.\n"
        f"// 数据来源: result/fingerprint_grid.json (v{version}) + result/fingerprint_grid_routes.json\n"
        "// 设计层=全楼假设全量网格；collectable=当前实际部署 61 枚信标下 10m/-85/>=3 可采集。\n"
        "module.exports = {\n"
        f"  venueId: {json.dumps(full.get('venueId'), ensure_ascii=False)},\n"
        f"  venueName: {json.dumps(full.get('venueName'), ensure_ascii=False)},\n"
        f"  version: {json.dumps(version)},\n"
        "  datasets: {\n"
        f"    full:  {{ label: \"全楼网格(2m底网+1m细分)\", points: {dump(full_points)} }},\n"
        f"    route: {{ label: \"路线网格(1m)\", points: {dump(route_points)} }}\n"
        "  }\n"
        "};\n"
    )
    COLLECTOR_JS.parent.mkdir(parents=True, exist_ok=True)
    COLLECTOR_JS.write_text(js, encoding="utf-8")
    return len(full_points)


def main() -> int:
    pkg = load_json(MAP_PACKAGE)
    points = select_points(pkg)
    assert len(points) == 20, f"预置标定点应为 20，实际 {len(points)}"
    scenes = defaultdict(int)
    for p in points:
        scenes[(p["floorId"], p["scene"])] += 1

    grid = load_full_grid()
    grid_version = str(load_json(FULL_GRID).get("version") or "unknown")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cal_ts = render_ts(
        CAL_TS_HEADER, points, ["pointId", "floorId", "truth", "scene", "batch", "nearGridId"],
    )
    (OUT_DIR / "calibration_points.ts").write_text(cal_ts, encoding="utf-8")
    fp_ts = (
        FP_TS_HEADER.replace("{version}", grid_version)
        + render_ts("", grid, GRID_FULL_KEYS).lstrip("\n")
    )
    (OUT_DIR / "fingerprint_grid.ts").write_text(fp_ts, encoding="utf-8")
    js_count = write_collector_js(grid_version)

    b1 = sum(1 for p in points if p["batch"] == 1)
    print(f"已生成 client/data/calibration_points.ts：20 点（批次1={b1} 批次2={20 - b1}）")
    for (fl, scene), n in sorted(scenes.items()):
        print(f"  F{fl} {scene}: {n}")
    print(f"已生成 client/data/fingerprint_grid.ts：{len(grid)} 点（v{grid_version}）")
    print(f"已生成 fingerprint-collector/data/fingerprint_grid.js：full {js_count} 点")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
