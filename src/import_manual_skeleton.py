#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「渲染页面导出的 SVG + 手动红线标注」转成骨架 GeoJSON 数据。

工作流（用户实际操作）：
  1. 打开 result/floor_layout_v9_interactive.html
  2. 勾选图层：可通行区域、墙体、房间（及需要的门/楼梯），
     点「导出所选图层 SVG」得到全幅 SVG（两个楼层，原始坐标）
  3. 用任意 SVG 编辑器在**走道区域用红色描边线**画骨架
     （stroke:#e74c3c 或 stroke:red，stroke-width≥3，fill:none）
  4. 保存文件，告诉我路径，例如 debug/manual_skel.svg
  5. 本脚本解析：
     - 提取红色 stroke 的 <line>/<path>/<polyline>
     - 按渲染页面坐标公式反算回米制（SCALE=7, MARGIN_X=50, MARGIN_Y=30,
       FLOOR_TITLE_H=46, 每层一个 y 带, ox/oy=全局 min_x/max_y）
     - 按 sy 落在哪个楼层带判断属于 1F/2F
     - 生成 skeleton features（每段 LineString）
     - 从端点/交点生成 TI 交叉点节点
     - 重建骨架 TI-TI 拓扑边
  6. --apply 时写回 geojson 的 skeleton + topology
     （保留 TR/TD/TF/TEN 原节点/边，TD/TF/TEN 重新挂到最近新 TI）

用法:
  python src/import_manual_skeleton.py --input path/to/manual.svg
  python src/import_manual_skeleton.py --input path/to/manual.svg --apply
  # --apply 前建议备份 result/school_building_01_map_v9.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from pathlib import Path

from shapely.geometry import LineString, Point, shape, mapping

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")

# 渲染页面坐标系统（与 render_interactive.py 完全一致）
SCALE = 7.0
MARGIN_X = 50
MARGIN_Y = 30
FLOOR_TITLE_H = 46
WALK_SPEED = 0.8
RED_RE = re.compile(r"#e74c3c|#ff0000|#f00|stroke:red|\bred\b", re.I)
NS = {"s": "http://www.w3.org/2000/svg"}


def floor_layout_params(geo):
    """计算渲染页面的全局 min_x/max_y 和每层 y 带。"""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for fk in geo["floors"]:
        for room in geo["floors"][fk]["geometry"].get("rooms", []):
            for p in room["geometry"]["coordinates"][0]:
                min_x, min_y = min(min_x, p[0]), min(min_y, p[1])
                max_x, max_y = max(max_x, p[0]), max(max_y, p[1])
    svh_per_floor = int((max_y - min_y) * SCALE + MARGIN_Y * 2 + FLOOR_TITLE_H)
    sorted_floors = sorted(geo["floors"].keys(), key=lambda x: int(x))
    bands = {}
    for idx, fk in enumerate(sorted_floors):
        base = idx * svh_per_floor
        bands[fk] = (base, base + svh_per_floor)
    return min_x, max_y, svh_per_floor, bands


def parse_svg_red_lines(svg_path):
    """提取所有红色 stroke 的折线（SVG 坐标，list of [(x,y),...]）。"""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    lines = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        style = (elem.get("style") or "") + " " + (elem.get("stroke") or "")
        if not RED_RE.search(style):
            continue
        # 只认明显的描边线（stroke-width≥2 或有描边色）；忽略填充色
        sw = re.search(r"stroke-width:?\s*([\d.]+)", style)
        if sw and float(sw.group(1)) < 2:
            continue
        fill = elem.get("fill") or ""
        if fill in ("none", "") or RED_RE.search(fill):
            pass
        if tag == "line":
            lines.append([(float(elem.get("x1")), float(elem.get("y1"))),
                          (float(elem.get("x2")), float(elem.get("y2")))])
        elif tag == "polyline":
            raw = elem.get("points", "")
            lines.append([(float(a), float(b)) for a, b in
                          re.findall(r"([-\d.]+)[,\s]+([-\d.]+)", raw)])
        elif tag == "path":
            lines.append(_path_to_points(elem.get("d", "")))
    return lines


def _pt_line_dist(p, a, b):
    """点 p 到线段 a-b 的垂直距离。"""
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _rdp(points, eps):
    """Ramer-Douglas-Peucker 折线简化。"""
    if len(points) < 3:
        return list(points)
    start, end = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _pt_line_dist(points[i], start, end)
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = _rdp(points[:idx + 1], eps)
        right = _rdp(points[idx:], eps)
        return left[:-1] + right
    return [start, end]


def _path_to_points(d, min_dist=1.5):
    """解析 SVG path 的 d 属性为折线点（SVG 坐标）。

    支持绝对/相对命令 M m L l H h V v C c S s Z z；曲线（C/S）按 N 步采样为折线，
    避免只取首控制点导致路径错乱（手绘连廊常用相对 c 命令）。最后用 RDP(eps=min_dist)
    简化，在保留曲线形状的前提下去除冗余点，防止自由曲线产生过多 TI 节点；
    始终保留首尾端点（供吸附到既有 TI）。
    """
    tokens = re.findall(r"[MLHVCSZmlhvcsz]|[-+]?[\d.]+(?:[eE][-+]?\d+)?", d)
    pts = []
    cur = [0.0, 0.0]
    start = [0.0, 0.0]
    i = 0
    cmd = "M"
    prev_ctrl = None

    def nxt():
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    while i < len(tokens):
        t = tokens[i]
        if t in "MLHVCSZmlhvcsz":
            cmd = t
            i += 1
            continue
        c = cmd.upper()
        rel = (cmd == cmd.lower())
        if c in ("M", "L"):
            x = nxt(); y = nxt()
            if rel:
                x += cur[0]; y += cur[1]
            cur = [x, y]
            if c == "M":
                start = [x, y]
            pts.append(tuple(cur))
            prev_ctrl = None
        elif c == "H":
            x = nxt()
            if rel:
                x += cur[0]
            cur = [x, cur[1]]; pts.append(tuple(cur)); prev_ctrl = None
        elif c == "V":
            y = nxt()
            if rel:
                y += cur[1]
            cur = [cur[0], y]; pts.append(tuple(cur)); prev_ctrl = None
        elif c in ("C", "S"):
            if c == "C":
                x1 = nxt(); y1 = nxt(); x2 = nxt(); y2 = nxt(); x = nxt(); y = nxt()
            else:
                x2 = nxt(); y2 = nxt(); x = nxt(); y = nxt()
            if rel:
                if c == "C":
                    x1 += cur[0]; y1 += cur[1]; x2 += cur[0]; y2 += cur[1]
                else:
                    x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            if c == "S":
                if prev_ctrl is not None:
                    x1 = 2 * cur[0] - prev_ctrl[0]; y1 = 2 * cur[1] - prev_ctrl[1]
                else:
                    x1, y1 = cur[0], cur[1]
            p0 = tuple(cur); c1 = (x1, y1); c2 = (x2, y2); p3 = (x, y)
            N = 6
            for s in range(1, N + 1):
                tt = s / N; mt = 1 - tt
                bx = mt**3 * p0[0] + 3 * mt**2 * tt * c1[0] + 3 * mt * tt**2 * c2[0] + tt**3 * p3[0]
                by = mt**3 * p0[1] + 3 * mt**2 * tt * c1[1] + 3 * mt * tt**2 * c2[1] + tt**3 * p3[1]
                pts.append((bx, by))
            prev_ctrl = (x2, y2)
            cur = [x, y]
        elif c == "Z":
            cur = list(start); prev_ctrl = None
        else:
            break
    # RDP 简化（保留首尾端点）
    if min_dist and len(pts) > 2:
        return _rdp(pts, min_dist)
    return pts


def svg_to_meters(pts, min_x, max_y, band_base):
    """渲染页面 SVG 坐标 → 米制（单楼层带）。"""
    out = []
    for sx, sy in pts:
        cx = min_x + (sx - MARGIN_X) / SCALE
        cy = max_y - (sy - band_base - FLOOR_TITLE_H - MARGIN_Y) / SCALE
        out.append((cx, cy))
    return out


def build_from_lines(m_lines, fk, start_seq=0, existing_ti=None, snap_tol=0.8):
    """骨架线 → (segments, ti_nodes, edges)。端点/交点<0.3m 合并为 TI。

    TI/TE 节点与边的 id 统一带楼层前缀（F{fk}-TI-NNNN / F{fk}-TE-NNNN），
    与现有拓扑约定一致；边编号从 start_seq 接续，避免与保留边 id 冲突。

    existing_ti：该楼层 geojson 中已有的 intersection 节点列表。若手动线端点
    落在某既有 TI 的 snap_tol(默认 0.5m) 内，则**复用其 id**（吸附），
    使新画的连廊线能真正桥接既有网络，而非生成坐标重合、id 不同的幽灵节点。
    """
    existing_ti = existing_ti or []
    exti_pts = [(Point(n["coordinates"]), n["id"]) for n in existing_ti]

    def snap_to_existing(pt):
        best, bid = snap_tol, None
        for ep, eid in exti_pts:
            d = pt.distance(ep)
            if d < best:
                best, bid = d, eid
        return bid

    segments = []
    endpoints = []
    for pts in m_lines:
        for i in range(len(pts) - 1):
            seg = LineString([pts[i], pts[i + 1]])
            if seg.length < 0.05:
                continue
            segments.append(seg)
            endpoints.append(Point(pts[i]))
            endpoints.append(Point(pts[i + 1]))

    # 既有 TI 坐标表（吸附时直接用既有坐标，避免手绘端点误差引入抖动）
    exti_coord = {n["id"]: n["coordinates"] for n in existing_ti}

    # 候选节点：端点 / 交点；优先吸附到既有 TI
    cand = []  # (Point, snap_id_or_None)
    for ep in endpoints:
        sid = snap_to_existing(ep)
        cand.append((ep, sid))
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            inter = segments[i].intersection(segments[j])
            if inter.is_empty:
                continue
            ips = ([inter] if inter.geom_type == "Point" else
                   (list(inter.geoms) if hasattr(inter, "geoms") else []))
            for ip in ips:
                cand.append((ip, snap_to_existing(ip)))

    # 合并候选为节点：
    #  - 吸附到同一既有 TI 的候选 → 合并为「唯一」节点（用既有 TI 的精确坐标，
    #    避免多个手绘端点各自落在 0.5m 容差内却相距 >0.3m 而被保留为重复 id）。
    #  - 其余候选按空间 <0.3m 合并；吸附节点优先。
    nodes_pts = []  # (Point, snap_id_or_None)
    for cpt, csid in cand:
        if csid and csid in exti_coord:
            ec = exti_coord[csid]
            found = any(nsid == csid for _, nsid in nodes_pts)
            if not found:
                nodes_pts.append((Point(ec), csid))
            continue
        merged = False
        for k, (npt, nsid) in enumerate(nodes_pts):
            if cpt.distance(npt) < 0.3:
                # 已存在：若任一方吸附到既有 TI，则标记吸附
                if csid or nsid:
                    nodes_pts[k] = (npt if nsid else cpt, nsid or csid)
                merged = True
                break
        if not merged:
            nodes_pts.append((cpt, csid))

    # 分配 id：吸附节点用既有 id；新节点顺序编号（接续既有最大号）
    used_ids = {nid for _, nid in nodes_pts if nid}
    max_existing = 0
    for n in existing_ti:
        try:
            max_existing = max(max_existing, int(n["id"].split("-")[-1]))
        except (ValueError, IndexError):
            pass
    seq = max_existing
    ti_nodes = []
    for pt, sid in nodes_pts:
        if sid:
            nid = sid
        else:
            seq += 1
            nid = f"F{fk}-TI-{seq:04d}"
        ti_nodes.append({
            "id": nid, "type": "intersection",
            "coordinates": [round(pt.x, 3), round(pt.y, 3)],
            "public": True, "accessible": True, "riskLevel": 0.5,
        })

    # 打断线段：取线段上的节点投影切点
    edges, edge_seq, seen = [], 0, set()
    for seg in segments:
        cuts = []
        for n in ti_nodes:
            p = Point(n["coordinates"])
            if seg.distance(p) < 0.3:
                cuts.append((seg.project(p), n["id"]))
        cuts.sort()
        for (pa_, aid), (pb_, bid) in zip(cuts, cuts[1:]):
            pa, pb = Point(seg.interpolate(pa_)), Point(seg.interpolate(pb_))
            d = pa.distance(pb)
            if d < 0.05:
                continue
            key = tuple(sorted((aid, bid)))
            if key in seen:
                continue
            seen.add(key)
            edge_seq += 1
            edges.append({
                "id": f"F{fk}-TE-{start_seq + edge_seq:04d}", "from": aid, "to": bid,
                "distance": round(d, 3), "estimatedTime": round(d / WALK_SPEED, 2),
                "accessibilityLevel": 0, "riskLevel": 0.5,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
            })
    # ---------- 缺口缝合 ----------
    # 手绘骨架端点常留 <1m 的小缺口（线没精确压到既有节点）。对同层内分属不同
    # 连通分量、且相距 < BRIDGE_TOL 的 TI 自动补一条连接边，接通因手绘误差断开的
    # 走廊。仅在跨分量时补，不会误连同分量内部的并行走廊。
    BRIDGE_TOL = 2.0
    ti_ids = [n["id"] for n in ti_nodes]
    coord = {n["id"]: n["coordinates"] for n in ti_nodes}
    # 并查集分量
    parent = {t: t for t in ti_ids}
    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb
    for e in edges:
        if e["from"] in parent and e["to"] in parent:
            _union(e["from"], e["to"])
    max_seq = start_seq + edge_seq
    for a in ti_ids:
        ca = coord[a]
        best = None
        for b in ti_ids:
            if b == a or _find(a) == _find(b):
                continue
            d = math.hypot(coord[b][0] - ca[0], coord[b][1] - ca[1])
            if d < BRIDGE_TOL and (best is None or d < best[0]):
                best = (d, b)
        if best:
            d, b = best
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            max_seq += 1
            edges.append({
                "id": f"F{fk}-TE-{max_seq:04d}", "from": a, "to": b,
                "distance": round(d, 3), "estimatedTime": round(d / WALK_SPEED, 2),
                "accessibilityLevel": 0, "riskLevel": 0.5,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
            })
    return segments, ti_nodes, edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="用户标注的 SVG 文件路径")
    ap.add_argument("--apply", action="store_true", help="写回 geojson")
    args = ap.parse_args()

    svg_path = Path(args.input)
    if not svg_path.exists():
        print(f"未找到文件: {svg_path}")
        return 1

    geo = json.loads(Path(GEO_IN).read_text(encoding="utf-8"))
    min_x, max_y, svh_per_floor, bands = floor_layout_params(geo)

    raw_lines = parse_svg_red_lines(str(svg_path))
    print(f"解析到 {len(raw_lines)} 条红色线")

    # 按楼层带分组
    per_floor = defaultdict(list)
    for pts in raw_lines:
        if not pts:
            continue
        sy0 = pts[0][1]
        fk = next((f for f, (lo, hi) in bands.items() if lo <= sy0 < hi), None)
        if fk is None:
            print(f"  ⚠️ 线起点 y={sy0:.1f} 不在任何楼层带内，忽略")
            continue
        per_floor[fk].append(pts)
        print(f"  F{fk}: 线 {pts[0]} → {pts[-1]}")

    results = {}
    for fk, pts_list in per_floor.items():
        band_base = bands[fk][0]
        m_lines = [svg_to_meters(pts, min_x, max_y, band_base) for pts in pts_list]
        # 吸附用：该楼层既有 intersection 节点
        existing_ti = [n for n in geo["floors"][fk]["topology"]["nodes"]
                       if n.get("type") == "intersection"]
        segments, ti_nodes, edges = build_from_lines(
            m_lines, fk, 0, existing_ti=existing_ti, snap_tol=0.5)
        snapped = sum(1 for n in ti_nodes
                      if any(e["id"] == n["id"] for e in existing_ti))
        results[fk] = {"lines": m_lines, "segments": segments,
                       "ti": ti_nodes, "edges": edges}
        print(f"F{fk}: {len(m_lines)} 条线 → {len(segments)} 段骨架, "
              f"{len(ti_nodes)} 个 TI (吸附既有 {snapped}), {len(edges)} 条边")

    if not results:
        print("未解析到任何红线（请确认使用 #e74c3c / red 描边线）")
        return 1

    # 输出骨架 GeoJSON 数据（不修改主文件）
    out = BASE_DIR / "result" / "skeleton_manual_parsed.json"
    payload = {}
    for fk, r in results.items():
        payload[fk] = {
            "skeleton_features": [
                {"type": "Feature", "id": f"SK-HC-{i + 1:03d}",
                 "geometry": mapping(seg),
                 "properties": {"type": "skeleton", "length_m": round(seg.length, 2)}}
                for i, seg in enumerate(r["segments"])
            ],
            "ti_nodes": r["ti"],
            "edges": r["edges"],
        }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"骨架 GeoJSON 数据已写入: {out}")

    if args.apply:
        # 接续现有边编号，避免与保留边 id 冲突
        def _seq(eid):
            m = re.search(r"(\d+)$", eid or "")
            return int(m.group(1)) if m else 0

        for fk, r in results.items():
            fl = geo["floors"][fk]
            fl["skeleton"] = {"features": payload[fk]["skeleton_features"]}
            # 旧 intersection(TI) 节点及引用它们的边需整体移除，
            # 再换上手动骨架生成的 TI/TI-TI 边。
            old_ti_ids = {n["id"] for n in fl["topology"]["nodes"]
                          if n.get("type") == "intersection"}
            old_nodes = [n for n in fl["topology"]["nodes"]
                         if n.get("type") != "intersection"]
            keep_edges = [e for e in fl["topology"]["edges"]
                          if e.get("from") not in old_ti_ids
                          and e.get("to") not in old_ti_ids]
            # 边编号从「保留边最大值」接续
            max_seq = max([_seq(e["id"]) for e in keep_edges] + [0])
            ti_nodes = r["ti"]
            ti_edges = []
            # 手动骨架边接续编号
            for e in r["edges"]:
                max_seq += 1
                new_e = dict(e)
                new_e["id"] = f"F{fk}-TE-{max_seq:04d}"
                ti_edges.append(new_e)

            # 把 doorway / facility / facility_entrance 重新挂到最近的新 TI
            hung = 0
            for n in old_nodes:
                if n.get("type") not in ("doorway", "facility", "facility_entrance"):
                    continue
                best, bd = None, 20.0
                for ti in ti_nodes:
                    c = ti["coordinates"]
                    d = math.hypot(n["coordinates"][0] - c[0],
                                   n["coordinates"][1] - c[1])
                    if d < bd:
                        bd, best = d, ti["id"]
                if best:
                    max_seq += 1
                    is_stair = n.get("facilityType") == "staircase"
                    keep_edges.append({
                        "id": f"F{fk}-TE-{max_seq:04d}", "from": n["id"], "to": best,
                        "distance": round(bd, 3), "estimatedTime": round(bd / WALK_SPEED, 2),
                        "accessibilityLevel": 999 if is_stair else 0,
                        "riskLevel": 10 if is_stair else 0.5,
                        "walkable": not is_stair, "wheelchairAccessible": not is_stair,
                        "blindAccessible": not is_stair,
                    })
                    hung += 1
            fl["topology"]["nodes"] = old_nodes + ti_nodes
            fl["topology"]["edges"] = keep_edges + ti_edges
            print(f"F{fk} 已写回: 移除数={len(old_ti_ids)} 旧TI + 其边; "
                  f"保留非TI节点={len(old_nodes)} 旧边={len(keep_edges)-hung} "
                  f"(挂接 {hung}); 新TI={len(ti_nodes)} 新骨架边={len(ti_edges)}")
        Path(GEO_IN).write_text(json.dumps(geo, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print("已写回", GEO_IN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
