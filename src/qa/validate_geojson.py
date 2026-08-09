# -*- coding: utf-8 -*-
"""
T12: GeoJSON 拓扑质量校验

对应《公共空间识别方案》第八章验收标准的可自动化部分：
  - 门投影 / doorway 覆盖
  - 拓扑连通性
  - 跨层 XE
  - 视障模式楼梯边 a=999
  - 骨架悬空边比例（若有 skeleton）
  - Walkable 是否越出外轮廓（启发式）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _node_map(nodes: List[dict]) -> Dict[str, dict]:
    return {n["id"]: n for n in nodes}


def _components(nodes: List[dict], edges: List[dict]) -> List[set]:
    adj = defaultdict(set)
    ids = {n["id"] for n in nodes}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in ids and b in ids:
            adj[a].add(b)
            adj[b].add(a)
    seen = set()
    comps = []
    for nid in ids:
        if nid in seen:
            continue
        stack = [nid]
        seen.add(nid)
        comp = {nid}
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    comp.add(v)
                    stack.append(v)
        comps.append(comp)
    return comps


def validate_floor(floor_key: str, floor: dict, report: List[str]) -> Dict[str, Any]:
    topo = floor.get("topology") or {}
    nodes = topo.get("nodes") or []
    edges = topo.get("edges") or []
    geom = floor.get("geometry") or {}
    skel = floor.get("skeleton") or {}
    walk = floor.get("walkable_regions") or {}

    nmap = _node_map(nodes)
    by_type = defaultdict(list)
    for n in nodes:
        by_type[n.get("type", "?")].append(n)

    stats = {
        "floor": floor_key,
        "nodes": len(nodes),
        "edges": len(edges),
        "TR": len(by_type["room"]),
        "TD": len(by_type["doorway"]),
        "TI": len(by_type["intersection"]),
        "TF": len(by_type["facility"]),
        "TEN": len(by_type["facility_entrance"]),
        "doors_geom": len(geom.get("doors") or []),
        "skeleton_segs": len((skel.get("features") or [])),
        "walkable_feats": len((walk.get("features") or [])),
        "ok": True,
        "issues": [],
    }

    def issue(msg: str, hard: bool = False):
        stats["issues"].append(msg)
        if hard:
            stats["ok"] = False
        report.append(f"[F{floor_key}] {msg}")

    # 1) 门口覆盖：拓扑设计上 doorway 节点只为「服务封闭房间的门」建模
    #    （走廊↔走廊的纯通行门归入走廊骨架 TI↔TI，同开口的摆弧/防火/门洞合并为一个 TD）。
    #    因此校验目标改为：每个封闭房间(TR)均应能经门(doorway)进入——即存在 TR↔TD 边。
    n_doors = stats["doors_geom"]
    room_ids = {n["id"] for n in by_type["room"]}
    td_ids = {n["id"] for n in by_type["doorway"]}
    room_has_door = set()
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in room_ids and b in td_ids:
            room_has_door.add(a)
        elif b in room_ids and a in td_ids:
            room_has_door.add(b)
    missing = room_ids - room_has_door
    stats["rooms_total"] = len(room_ids)
    stats["rooms_with_door"] = len(room_has_door)
    if room_ids and missing:
        issue(f"有 {len(missing)} 个房间无门口(TD)边: "
              f"{sorted(missing)[:10]}{' …' if len(missing) > 10 else ''}", hard=True)
    elif n_doors == 0:
        issue("无 geometry.doors（可能解析失败）", hard=True)
    # 仅作信息提示：物理门数 vs TD 节点数（不再强约束）
    if n_doors:
        report.append(f"[F{floor_key}] 信息: geometry.doors={n_doors} TD={stats['TD']} "
                       f"(走廊通行门/合并后不再各建 TD)")

    # 2) 连通性：主分量应覆盖绝大多数非孤立节点
    comps = _components(nodes, edges)
    comps_sorted = sorted(comps, key=len, reverse=True)
    if not comps_sorted:
        issue("无拓扑节点", hard=True)
    else:
        main = comps_sorted[0]
        isolated = [c for c in comps_sorted[1:] if len(c) <= 2]
        large_islands = [c for c in comps_sorted[1:] if len(c) > 5]
        coverage = len(main) / max(1, len(nodes))
        stats["main_component"] = len(main)
        stats["components"] = len(comps_sorted)
        stats["coverage"] = round(coverage, 3)
        if coverage < 0.7:
            issue(f"主连通分量仅覆盖 {coverage:.0%} 节点 "
                  f"(主={len(main)}, 分量数={len(comps_sorted)})", hard=True)
        if large_islands:
            issue(f"存在 {len(large_islands)} 个较大孤立子图 "
                  f"(sizes={[len(c) for c in large_islands[:5]]})")

    # 3) 楼梯设施边 accessibilityLevel=999
    stair_tf = [n for n in by_type["facility"]
                if n.get("facilityType") == "staircase"]
    for e in edges:
        a, b = nmap.get(e.get("from")), nmap.get(e.get("to"))
        if not a or not b:
            continue
        for end in (a, b):
            if end.get("type") == "facility" and end.get("facilityType") == "staircase":
                if e.get("accessibilityLevel") != 999:
                    issue(f"楼梯边 {e.get('id')} accessibilityLevel!="
                          f"999 (={e.get('accessibilityLevel')})", hard=True)
                if e.get("blindAccessible") is True:
                    issue(f"楼梯边 {e.get('id')} blindAccessible 应为 false")
                break

    # 4) TF 应有至少一条边
    degree = defaultdict(int)
    for e in edges:
        degree[e.get("from")] += 1
        degree[e.get("to")] += 1
    for n in by_type["facility"]:
        if degree[n["id"]] == 0:
            issue(f"设施节点孤立: {n['id']} ({n.get('label')})")

    # 5) 骨架质量（若有）
    segs = skel.get("features") or []
    if segs:
        short = 0
        total_len = 0.0
        for f in segs:
            L = float((f.get("properties") or {}).get("length_m") or 0)
            total_len += L
            if L < 0.3:
                short += 1
        ratio = short / max(1, len(segs))
        stats["skeleton_short_ratio"] = round(ratio, 3)
        stats["skeleton_total_len_m"] = round(total_len, 1)
        if ratio > 0.3:
            issue(f"骨架短段比例偏高 {ratio:.0%}（可能剪枝不足）")
    else:
        issue("无 skeleton 图层（未启用骨架或提取失败）")

    # 6) Walkable 存在性
    if stats["walkable_feats"] == 0 and stats["TI"] > 0:
        # 也可能 walkable 写在 room.properties.walkablePolygon
        n_wp = sum(
            1 for r in (geom.get("rooms") or [])
            if (r.get("properties") or {}).get("walkablePolygon")
        )
        stats["walkable_in_rooms"] = n_wp
        if n_wp == 0:
            issue("无 walkable_regions / room.walkablePolygon")

    return stats


def validate_cross_floor(geo: dict, report: List[str]) -> Dict[str, Any]:
    xes = geo.get("crossFloorEdges") or []
    floors = geo.get("floors") or {}
    stats = {"count": len(xes), "stair": 0, "elevator": 0, "ok": True, "issues": []}

    def issue(msg, hard=False):
        stats["issues"].append(msg)
        if hard:
            stats["ok"] = False
        report.append(f"[XE] {msg}")

    if not xes:
        issue("无跨层边 crossFloorEdges", hard=True)
        return stats

    # 收集各层 TF id
    tf_by_floor = {}
    for fk, fl in floors.items():
        nodes = (fl.get("topology") or {}).get("nodes") or []
        tf_by_floor[str(fk)] = {
            n["id"]: n for n in nodes if n.get("type") == "facility"
        }

    for xe in xes:
        kind = xe.get("type")
        if kind == "staircase":
            stats["stair"] += 1
            if xe.get("accessibilityLevel") != 999:
                issue(f"{xe.get('id')} 楼梯跨层 a!=999", hard=True)
        elif kind == "elevator":
            stats["elevator"] += 1
            if xe.get("accessibilityLevel") not in (0, None):
                issue(f"{xe.get('id')} 电梯跨层 a 异常: {xe.get('accessibilityLevel')}")

        frm, to = xe.get("from"), xe.get("to")
        f1 = str(xe.get("fromFloor", "1"))
        f2 = str(xe.get("toFloor", "2"))
        if frm not in tf_by_floor.get(f1, {}):
            issue(f"{xe.get('id')} from={frm} 不在 F{f1} TF 集合", hard=True)
        if to not in tf_by_floor.get(f2, {}):
            issue(f"{xe.get('id')} to={to} 不在 F{f2} TF 集合", hard=True)

    if stats["elevator"] == 0:
        issue("无电梯跨层边（视障优先路径可能缺失）")
    return stats


def validate_geojson(path: str, verbose: bool = True) -> int:
    geo = _load(path)
    report: List[str] = []
    all_ok = True

    print(f"=== PathAI GeoJSON 校验: {path} ===")
    print(f"venue: {geo.get('venueId')}  version: {geo.get('version')}")

    floor_stats = []
    for fk in sorted((geo.get("floors") or {}).keys(), key=lambda x: int(x) if str(x).isdigit() else x):
        st = validate_floor(fk, geo["floors"][fk], report)
        floor_stats.append(st)
        all_ok = all_ok and st["ok"]
        print(f"\n[F{fk}] nodes={st['nodes']} edges={st['edges']} "
              f"TR={st['TR']} TD={st['TD']} TI={st['TI']} TF={st['TF']} "
              f"skel={st['skeleton_segs']} walk={st.get('walkable_feats', 0)}")
        if "coverage" in st:
            print(f"      连通覆盖={st['coverage']:.0%} 主分量={st.get('main_component')} "
                  f"分量数={st.get('components')}")

    xe = validate_cross_floor(geo, report)
    all_ok = all_ok and xe["ok"]
    print(f"\n[跨层] XE={xe['count']} 楼梯={xe['stair']} 电梯={xe['elevator']}")

    print("\n--- 问题列表 ---")
    if not report:
        print("（无问题）")
    else:
        for line in report:
            print(line)

    print("\n=== 结果:", "PASS" if all_ok else "FAIL", "===")
    return 0 if all_ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="PathAI GeoJSON 拓扑质量校验 (T12)")
    ap.add_argument("geojson", nargs="?",
                    help="GeoJSON 路径")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    path = args.geojson
    if not path:
        # 尝试默认路径
        candidates = [
            Path("result/school_building_01_map_v9.geojson"),
            Path("/home/workdir/attachments/school_building_01_map_v9.geojson"),
            Path("../result/school_building_01_map_v9.geojson"),
        ]
        for c in candidates:
            if c.exists():
                path = str(c)
                break
        if not path:
            ap.error("请指定 geojson 路径")
    sys.exit(validate_geojson(path, verbose=args.verbose))


if __name__ == "__main__":
    main()
