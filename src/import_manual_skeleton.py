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


def _path_to_points(d):
    tokens = re.findall(r"[MLHVCSZmlhvz]|[-+]?[\d.]+(?:[eE][-+]?\d+)?", d)
    pts = []
    cur = [0.0, 0.0]
    i = 0
    cmd = "M"
    while i < len(tokens):
        t = tokens[i]
        if t in "MLHVCSZmlhvz":
            cmd = t
            i += 1
            continue
        try:
            v1 = float(t)
            v2 = float(tokens[i + 1])
        except (ValueError, IndexError):
            break
        i += 2
        c = cmd.upper()
        if c in ("M", "L"):
            cur = [v1, v2]
            pts.append((v1, v2))
        elif c == "H":
            cur = [v1, cur[1]]; pts.append(tuple(cur))
        elif c == "V":
            cur = [cur[0], v1]; pts.append(tuple(cur))
        elif c in ("C", "S"):
            cur = [v1, v2]; pts.append(tuple(cur))
    return pts


def svg_to_meters(pts, min_x, max_y, band_base):
    """渲染页面 SVG 坐标 → 米制（单楼层带）。"""
    out = []
    for sx, sy in pts:
        cx = min_x + (sx - MARGIN_X) / SCALE
        cy = max_y - (sy - band_base - FLOOR_TITLE_H - MARGIN_Y) / SCALE
        out.append((cx, cy))
    return out


def build_from_lines(m_lines):
    """骨架线 → (segments, ti_nodes, edges)。端点/交点<0.3m 合并为 TI。"""
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
    nodes_pts = []
    for ep in endpoints:
        if not any(ep.distance(p) < 0.3 for p in nodes_pts):
            nodes_pts.append(ep)
    # 交点
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            inter = segments[i].intersection(segments[j])
            if inter.is_empty:
                continue
            pts = ([inter] if inter.geom_type == "Point" else
                   (list(inter.geoms) if hasattr(inter, "geoms") else []))
            for ip in pts:
                if not any(ip.distance(p) < 0.3 for p in nodes_pts):
                    nodes_pts.append(ip)
    ti_nodes = [{
        "id": f"TI-{k + 1:03d}", "type": "intersection",
        "coordinates": [round(p.x, 3), round(p.y, 3)],
        "public": True, "accessible": True, "riskLevel": 0.5,
    } for k, p in enumerate(nodes_pts)]
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
                "id": f"TE-{edge_seq:03d}", "from": aid, "to": bid,
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
        segments, ti_nodes, edges = build_from_lines(m_lines)
        results[fk] = {"lines": m_lines, "segments": segments,
                       "ti": ti_nodes, "edges": edges}
        print(f"F{fk}: {len(m_lines)} 条线 → {len(segments)} 段骨架, "
              f"{len(ti_nodes)} 个 TI, {len(edges)} 条边")

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
        for fk, r in results.items():
            fl = geo["floors"][fk]
            fl["skeleton"] = {"features": payload[fk]["skeleton_features"]}
            old_nodes = [n for n in fl["topology"]["nodes"]
                         if n.get("type") != "intersection"]
            ti_ids = {n["id"] for n in r["ti"]}
            keep_edges = [e for e in fl["topology"]["edges"]
                          if e.get("from") not in ti_ids and e.get("to") not in ti_ids]
            hung = 0
            for n in old_nodes:
                if n.get("type") not in ("doorway", "facility", "facility_entrance"):
                    continue
                best, bd = None, 12.0
                for ti in r["ti"]:
                    c = ti["coordinates"]
                    d = math.hypot(n["coordinates"][0] - c[0],
                                   n["coordinates"][1] - c[1])
                    if d < bd:
                        bd, best = d, ti["id"]
                if best:
                    seq = max([int(e["id"].split("-")[-1]) for e in keep_edges] + [0])
                    is_stair = n.get("facilityType") == "staircase"
                    keep_edges.append({
                        "id": f"TE-{seq + 1:03d}", "from": n["id"], "to": best,
                        "distance": round(bd, 3), "estimatedTime": round(bd / WALK_SPEED, 2),
                        "accessibilityLevel": 999 if is_stair else 0,
                        "riskLevel": 10 if is_stair else 0.5,
                        "walkable": not is_stair, "wheelchairAccessible": not is_stair,
                        "blindAccessible": not is_stair,
                    })
                    hung += 1
            fl["topology"]["nodes"] = old_nodes + r["ti"]
            fl["topology"]["edges"] = keep_edges + r["edges"]
            print(f"F{fk} 已写回: 保留旧边 {len(keep_edges)} 条(含挂接 {hung}), "
                  f"新骨架边 {len(r['edges'])} 条")
        Path(GEO_IN).write_text(json.dumps(geo, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print("已写回", GEO_IN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
