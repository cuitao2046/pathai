# -*- coding: utf-8 -*-
"""指纹采集网格生成模块（对应地图构建指南 §八）

目标：在已建好的室内地图上生成「指纹采集网格」，指导工程师去现场采集
BLE 指纹库。算法严格按指南 §8.1 的规则：

  1. 普通区域：沿走廊中线（拓扑图的边）按 2 米间距均匀布点。
  2. 安全节点区域：在楼梯口 / 电梯口 / 交叉口 / 门口周边 3 米范围内，
     以 1 米间距加密（采集优先级更高）。
  3. 大型公共空间（阶梯教室 / 大厅 / 活动区 / 会议室 / 中庭）内部，
     按 3 米间距布点。
  4. 教室内部不布点——视障用户导航目的地通常是门口而非教室内。
     由于所有采集点都强制落在 walkable_regions（可通行多边形）内，
     而教室是封闭房间、不在可通行区内，因此天然被排除。

输入：parse_cad_pdf.py 生成的 v9 GeoJSON
      （需要 floors[fk].topology.{nodes,edges}、
        floors[fk].walkable_regions.features、
        floors[fk].semantic.rooms、floors[fk].geometry.rooms、
        floors[fk].accessibility.riskNodes）
输出：result/fingerprint_grid.json（采集点清单，供工程师 / 下游服务消费）

依赖：shapely（与 parse_cad_pdf.py 同一 venv）

用法：
    python generate_fingerprint_grid.py [geojson_path] [out_path]
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Point, shape, LineString
from shapely.ops import unary_union

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
OUT_DEFAULT = str(BASE_DIR / "result" / "fingerprint_grid.json")

# ----------------------------- 可调参数（指南 §8.1）-----------------------------
NORMAL_SPACING_M = 2.0     # 普通区（走廊中线）间距
SAFE_SPACING_M = 1.0       # 安全节点区间距
SAFE_RADIUS_M = 3.0        # 安全节点加密半径
LARGE_SPACING_M = 3.0      # 大型公共空间内部间距
LARGE_AREA_MIN_M2 = 30.0   # 仅面积 >= 该值的公共空间才内填
DEDUP_DIST_M = 0.6         # 去重最小间距（< 该距离的点只保留优先级更高者）

# 大型公共空间类型（内部按 3m 布点）
LARGE_PUBLIC_TYPES = {"activity", "lobby", "reception", "meeting", "atrium"}
# 安全节点：高优先级（楼梯 / 电梯 / 风险节点）
HIGH_SAFE_NODE_KINDS = {"staircase", "elevator", "stair_entrance", "facility_stair", "facility_elevator"}


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def build_walkable(geo_floor):
    """把该层 walkable_regions.features 合并为一个 shapely 几何（含柱洞），
    用于判定「点是否落在可通行区内」。"""
    feats = (geo_floor.get("walkable_regions") or {}).get("features", [])
    polys = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        try:
            polys.append(shape(g))
        except Exception:
            continue
    if not polys:
        return None
    return unary_union(polys)


def grid_points_in_polygon(poly, spacing, bounds=None):
    """在多边形内按 spacing 生成规则网格点（含边界）。返回 [(x, y), ...]。"""
    if bounds is None:
        minx, miny, maxx, maxy = poly.bounds
    else:
        minx, miny, maxx, maxy = bounds
    pts = []
    x = minx
    while x <= maxx + 1e-9:
        y = miny
        while y <= maxy + 1e-9:
            p = Point(x, y)
            if poly.contains(p) or poly.distance(p) < 1e-6:
                pts.append((x, y))
            y += spacing
        x += spacing
    return pts


def collect_safe_anchors(geo_floor):
    """收集安全节点锚点：返回 [(x, y, priority, kind, nodeId), ...]。

    指南 §8.1 把「楼梯口、电梯口、交叉口、门口」都列为安全节点，但本建筑
    拓扑极密（单层 146 交叉口 + 129 门口），若对每个节点都做 3m·1m 加密，
    磁盘会覆盖整条走廊，使整层退化为 1m 网格、点数膨胀约 40×，远超指南
    §8.2 估算的 40~60/30~50。

    因此按指南 §6.3「风险节点」的语义收紧：仅把**高风险过渡点**——楼梯口 /
    电梯口 / 风险节点——作为加密锚点（priority 1）。普通交叉口与门口本就由
    2m 走廊中线均匀覆盖，不再单独加密，避免重复与膨胀。

    如需严格按 §8.1 字面（含交叉口/门口）加密，把下方两行注释恢复即可。
    """
    anchors = []
    nodes = geo_floor.get("topology", {}).get("nodes", [])
    for n in nodes:
        t = n.get("type")
        rt = n.get("roomType") or n.get("facilityType") or ""
        coords = n.get("coordinates")
        if not coords or len(coords) < 2:
            continue
        x, y = coords[0], coords[1]
        # 仅楼梯 / 电梯（含设施与房间两类节点）作为高优先级加密锚点
        if t == "facility" and rt in ("staircase", "elevator"):
            anchors.append((x, y, 1, "facility_" + rt, n.get("id")))
        elif t == "room" and rt in ("staircase", "elevator"):
            anchors.append((x, y, 1, rt, n.get("id")))
        # 普通交叉口 / 门口不单独加密（已由 2m 中线覆盖）：
        # elif t in ("intersection", "doorway"):
        #     anchors.append((x, y, 2, t, n.get("id")))
    # 风险节点（来自 accessibility.riskNodes，多为楼梯口）
    for rn in geo_floor.get("accessibility", {}).get("riskNodes", []):
        c = rn.get("coordinates")
        if not c or len(c) < 2:
            continue
        anchors.append((c[0], c[1], 1, "stair_entrance", rn.get("id")))
    return anchors


def collect_large_rooms(geo_floor):
    """收集需要内部布点的大型公共空间：返回 [(polygon, roomType, label), ...]。"""
    geom_rooms = {r.get("id"): r for r in geo_floor.get("geometry", {}).get("rooms", [])}
    out = []
    for r in geo_floor.get("semantic", {}).get("rooms", []):
        if r.get("type") not in LARGE_PUBLIC_TYPES:
            continue
        gid = r.get("geometryId") or r.get("id")
        gr = geom_rooms.get(gid)
        if not gr:
            continue
        g = gr.get("geometry")
        if not g:
            continue
        try:
            poly = shape(g)
        except Exception:
            continue
        # 面积用 shapely（米制，已含 SCALE 换算后的真实坐标）
        if getattr(poly, "area", 0) < LARGE_AREA_MIN_M2:
            continue
        out.append((poly, r.get("type"), r.get("label", "")))
    return out


def generate_floor(geo_floor, floor_no):
    walk = build_walkable(geo_floor)
    if walk is None:
        return [], {"error": "no walkable regions"}

    nodes_by_id = {n["id"]: n for n in geo_floor.get("topology", {}).get("nodes", [])}
    edges = geo_floor.get("topology", {}).get("edges", [])

    candidates = []  # (x, y, regionType, priority, source, nearId, nearKind)

    # ---- A. 普通采集点：可通行区 2m 规则网格填充 ----
    # 指南 §8.1 字面为「沿走廊中线插值」，但纯中线在宽走廊/大厅会留下 5~7m
    # 死区（实测最远间距 5.45m），不利离线指纹定位。改为对整个可通行区做
    # 2m 面积填充：既保留 2m 间距、又保证全区域 ≤2m 覆盖（中线点天然包含）。
    normal_fill = grid_points_in_polygon(walk, NORMAL_SPACING_M)
    for (x, y) in normal_fill:
        candidates.append((x, y, "normal", 3, "area_fill", "", ""))
    edge_count = len(normal_fill)  # 仅作统计用名

    # ---- B. 安全节点加密（楼梯/电梯/风险节点周边 3m 内的可通行区，1m 网格）----
    # 锚点（楼梯/电梯质心）通常在房间内、不在可通行区，直接以锚点为中心做
    # 3m 圆盘再与 walkable 求交，只保留「走廊侧」那段，才是对定位最关键、
    # 且真正可站立采集的楼梯/电梯入口区域。
    anchor_count = 0
    for (ax, ay, prio, kind, nid) in collect_safe_anchors(geo_floor):
        disk = Point(ax, ay).buffer(SAFE_RADIUS_M)
        sub = disk.intersection(walk)
        if sub.is_empty:
            continue
        for (x, y) in grid_points_in_polygon(sub, SAFE_SPACING_M):
            candidates.append((x, y, "safe", prio, "safe_density", nid or "", kind))
        anchor_count += 1

    # ---- C. 大型公共空间 ----
    # 已由 A 的 2m 面积填充完整覆盖（间距更密于指南的 3m），无需单独处理。
    # 保留函数 collect_large_rooms 供需要「仅大空间 3m 稀疏布点」的场景调用。
    large_count = 0

    # ---- D. 去重（保留高优先级 / 避免重叠）----
    # 按优先级升序（safe=1/2 先于 normal=3），重叠时高优先级胜出
    candidates.sort(key=lambda c: c[3])
    kept = []
    for c in candidates:
        x, y = c[0], c[1]
        dup = False
        for k in kept:
            if math.hypot(x - k[0], y - k[1]) < DEDUP_DIST_M:
                dup = True
                break
        if not dup:
            kept.append(c)

    # 排序输出：按楼层 y 降序、x 升序，便于阅读
    kept.sort(key=lambda c: (-c[1], c[0]))
    points = []
    for i, c in enumerate(kept, 1):
        x, y, region_type, priority, source, near_id, near_kind = c
        points.append({
            "id": f"FP-{floor_no}-{i:03d}",
            "floor": floor_no,
            "coordinates": [round(x, 3), round(y, 3)],
            "regionType": region_type,
            "priority": priority,
            "source": source,
            "nearNodeId": near_id or None,
            "nearNodeType": near_kind or None,
        })

    stats = {
        "normal_points": edge_count,
        "safe_anchors": anchor_count,
        "large_rooms": large_count,
        "total": len(points),
        "safe": sum(1 for p in points if p["regionType"] == "safe"),
        "normal": sum(1 for p in points if p["regionType"] == "normal"),
        "by_source": {
            "area_fill": sum(1 for p in points if p["source"] == "area_fill"),
            "safe_density": sum(1 for p in points if p["source"] == "safe_density"),
        },
    }
    return points, stats


# --------------------------- 路线模式（按测试导航路径布点）---------------------------
# 与 gen_beacon_plan.py --mode route 对称：仅在两条测试导航路径上布指纹采集点，
# 与「只沿路线部署信标」的稀疏方案自洽——全楼 2m 网格里大量点远离任何信标、
# 收不到信号，属无效采集；路线模式把采集范围收敛到路线走廊+楼梯/电梯口。
ROUTE_FP_SPACING_M = 1.0        # 路线走廊指纹间距（视障导航：1-2步内感知偏离并纠正）
ROUTE_FP_SAFE_SPACING_M = 1.0   # 路线楼梯/电梯口加密间距
ROUTE_FP_SAFE_RADIUS_M = 3.0    # 路线楼梯/电梯口加密半径
ROUTE_CORRIDOR_HALF_M = 3.0     # 路线走廊半宽：从中心线向两侧各扩 3m 覆盖走廊全截面


def _load_route_graph(geo):
    """按需加载导航路由模块（src/topology/route_rules.py），复用其受限 Dijkstra 还原路径。"""
    import importlib.util
    topo_py = Path(__file__).resolve().parents[1] / "topology" / "route_rules.py"
    spec = importlib.util.spec_from_file_location("route_rules_fp", str(topo_py))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RouteGraph(geo)


def generate_route_mode(geo, routes, spacing=ROUTE_FP_SPACING_M,
                        safe_spacing=ROUTE_FP_SAFE_SPACING_M, safe_radius=ROUTE_FP_SAFE_RADIUS_M):
    """按指定导航路线生成指纹采集点（路线模式）。

    routes: list of {start, end, mode?, label?}，复用 RouteGraph.generate_route 还原路径。
    对每条路线：同层相邻节点段按 spacing 重采样（仅保留可通行区内点，房间内部段自然排除）；
    路线上的楼梯/电梯节点周边 safe_radius 内、与可通行区相交部分按 safe_spacing 加密。
    区别于全楼模式：仅沿路线线段向两侧缓冲 corridor_half 宽度、与可通行区求交后做面积网格填充，
    而非只沿中心线 1D 采样——保证覆盖整个走廊截面，视障用户偏离中线时仍能正确定位。
    """
    graph = _load_route_graph(geo)
    walk_by_floor = {int(fk): build_walkable(fl) for fk, fl in (geo.get("floors") or {}).items()}

    # 收集路线几何线段（按楼层）与楼梯/电梯节点
    route_lines = {}     # floor -> list of LineString
    stair_elev_nodes = []  # (x, y, floor)
    route_meta = []
    for rt in routes:
        start, end = rt["start"], rt["end"]
        mode = rt.get("mode", "normal")
        res = graph.generate_route(start, end, mode)
        if not res.get("reachable"):
            print(f"  [warn] 路线不可达，跳过：{start} -> {end} (mode={mode})")
            continue
        path = res["path"]
        route_meta.append({
            "label": rt.get("label", f"{start} -> {end}"),
            "start": start, "end": end, "mode": mode,
            "distance_m": res["distance"], "nodes": len(path),
            "crossFloor": res["cross_floor"], "usedElevator": res["used_elevator"],
            "usedStair": res["used_stair"],
        })
        # 有序节点序列（坐标 / 节点类型 / 房间或设施子类型 / 楼层）
        seq = []
        for nid in path:
            n = graph.nodes.get(nid)
            if not n:
                continue
            c = n.get("coords")
            if not c or len(c) < 2:
                continue
            sub = (n.get("roomType") or n.get("facilityType") or "")
            seq.append((c[0], c[1], n.get("type"), sub, n.get("floor")))
        # 收集同层线段（用于走廊缓冲面填充）
        for (x0, y0, t0, s0, f0), (x1, y1, t1, s1, f1) in zip(seq, seq[1:]):
            if f0 != f1:
                continue
            route_lines.setdefault(f0, []).append(LineString([(x0, y0), (x1, y1)]))
        # 收集楼梯 / 电梯节点
        for (x, y, nt, sub, f) in seq:
            if nt in ("facility", "room") and sub in ("staircase", "elevator"):
                stair_elev_nodes.append((x, y, int(f)))

    # ---- 走廊：以路线线段为中心向两侧缓冲，与可通行区求交后做面积填充 ----
    # 视障用户实际走路会偏离中心线，若只沿中线采集 1D 指纹，
    # 偏到墙边时 RSSI 指纹匹配不上，定位精度差。缓冲填满走廊全截面
    # 才能正确区分走廊左侧/中间/右侧。
    corridor_half = ROUTE_CORRIDOR_HALF_M
    candidates = []  # (x, y, floor, regionType, priority)
    for fk, lines in route_lines.items():
        walk = walk_by_floor.get(int(fk))
        if walk is None:
            continue
        merged = unary_union(lines)
        buffered = merged.buffer(corridor_half)
        route_walkable = buffered.intersection(walk)
        if route_walkable.is_empty:
            continue
        for (gx, gy) in grid_points_in_polygon(route_walkable, spacing):
            candidates.append((gx, gy, int(fk), "normal", 3))

    # ---- 楼梯 / 电梯口加密（取走廊侧可站立部分） ----
    for (x, y, f) in stair_elev_nodes:
        walk = walk_by_floor.get(int(f))
        if walk is None:
            continue
        disk = Point(x, y).buffer(safe_radius)
        sub_poly = disk.intersection(walk)
        if sub_poly.is_empty:
            continue
        for (gx, gy) in grid_points_in_polygon(sub_poly, safe_spacing):
            candidates.append((gx, gy, int(f), "safe", 1))

    # 去重（按楼层，保留高优先级；同优先级近邻只留其一）
    candidates.sort(key=lambda c: c[4])
    kept = []
    for c in candidates:
        x, y, fl, rg, prio = c
        dup = False
        for k in kept:
            if k[2] == fl and math.hypot(x - k[0], y - k[1]) < DEDUP_DIST_M:
                dup = True
                break
        if not dup:
            kept.append(c)

    # 按楼层分组、排序输出
    floors_out = {}
    summary = {}
    for fk in sorted({c[2] for c in kept}):
        fpts = [c for c in kept if c[2] == fk]
        fpts.sort(key=lambda c: (-c[1], c[0]))
        points = []
        for i, c in enumerate(fpts, 1):
            x, y, fl, rg, prio = c
            points.append({
                "id": f"FP-{fl}-R{i:03d}",
                "floor": fl,
                "coordinates": [round(x, 3), round(y, 3)],
                "regionType": rg,
                "priority": prio,
                "source": "route",
                "nearNodeId": None,
                "nearNodeType": None,
            })
        floors_out[str(fk)] = {
            "floor": fk,
            "parameters": {
                "normalSpacingM": spacing,
                "safeSpacingM": safe_spacing,
                "safeRadiusM": safe_radius,
                "mode": "route",
            },
            "points": points,
        }
        summary[str(fk)] = {
            "total": len(points),
            "safe": sum(1 for p in points if p["regionType"] == "safe"),
            "normal": sum(1 for p in points if p["regionType"] == "normal"),
        }
    total_all = sum(s["total"] for s in summary.values())
    return floors_out, summary, route_meta, total_all


def main():
    import argparse
    ap = argparse.ArgumentParser(description="指纹采集网格生成（全楼 / 路线模式）")
    ap.add_argument("geo", nargs="?", default=GEO_IN, help="输入 GeoJSON 路径")
    ap.add_argument("--out", default=OUT_DEFAULT, help="输出 JSON 路径")
    ap.add_argument("--mode", default="full", choices=("full", "route"),
                    help="full=全楼均匀网格(默认)；route=仅沿测试导航路线布点")
    ap.add_argument("--routes", default=None,
                    help="route 模式：路线定义 JSON（同 gen_beacon_plan.py 的 beacon_routes.json）")
    ap.add_argument("--spacing", type=float, default=ROUTE_FP_SPACING_M,
                    help="route 模式：路线走廊指纹间距(米)，默认 2")
    args = ap.parse_args()

    geo_path = args.geo
    out_path = args.out

    geo = json.load(open(geo_path, encoding="utf-8"))

    if args.mode == "route":
        if not args.routes:
            ap.error("--mode route 必须提供 --routes 路线定义 JSON")
        routes = json.load(open(args.routes, encoding="utf-8")).get("routes", [])
        floors_out, summary, route_meta, total_all = generate_route_mode(
            geo, routes, spacing=args.spacing)
        print(f"  [route] 路线数={len(route_meta)} 间距={args.spacing}m → 采集点 {total_all}")
        for rm in route_meta:
            print(f"    · {rm['label']}: {rm['distance_m']:.1f}m / {rm['nodes']}节点 "
                  f"(跨层={rm['crossFloor']} 电梯={rm['usedElevator']} 楼梯={rm['usedStair']})")
        out = {
            "venueId": geo.get("venueId"),
            "venueName": geo.get("venueName"),
            "version": geo.get("version"),
            "generator": "generate_fingerprint_grid.py",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "mode": "route",
            "parameters": {
                "normalSpacingM": args.spacing,
                "safeSpacingM": ROUTE_FP_SAFE_SPACING_M,
                "safeRadiusM": ROUTE_FP_SAFE_RADIUS_M,
                "dedupDistM": DEDUP_DIST_M,
            },
            "routes": route_meta,
            "summary": summary,
            "floors": floors_out,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n总计 {total_all} 个路线采集点 → {out_path}")
        return

    floors_out = {}
    summary = {}
    total_all = 0
    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        floor_no = int(fk)
        pts, stats = generate_floor(fd, floor_no)
        floors_out[fk] = {
            "floor": floor_no,
            "parameters": {
                "normalSpacingM": NORMAL_SPACING_M,
                "safeSpacingM": SAFE_SPACING_M,
                "safeRadiusM": SAFE_RADIUS_M,
                "largeSpacingM": LARGE_SPACING_M,
            },
            "points": pts,
        }
        summary[fk] = stats
        total_all += len(pts)
        print(f"  [F{fk}] 普通点={stats['normal_points']} 安全锚点={stats['safe_anchors']} "
              f"大型空间={stats['large_rooms']} → 采集点 {stats['total']} "
              f"(安全={stats['safe']} 普通={stats['normal']}; "
              f"来源 area_fill={stats['by_source']['area_fill']} safe={stats['by_source']['safe_density']})")

    out = {
        "venueId": geo.get("venueId"),
        "venueName": geo.get("venueName"),
        "version": geo.get("version"),
        "generator": "generate_fingerprint_grid.py",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "parameters": {
            "normalSpacingM": NORMAL_SPACING_M,
            "safeSpacingM": SAFE_SPACING_M,
            "safeRadiusM": SAFE_RADIUS_M,
            "largeSpacingM": LARGE_SPACING_M,
            "largeAreaMinM2": LARGE_AREA_MIN_M2,
            "dedupDistM": DEDUP_DIST_M,
            "largePublicTypes": sorted(LARGE_PUBLIC_TYPES),
        },
        "summary": summary,
        "floors": floors_out,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n总计 {total_all} 个采集点 → {out_path}")


if __name__ == "__main__":
    main()
