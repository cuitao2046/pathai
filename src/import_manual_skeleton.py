#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把用户手动标注的骨架 SVG 转成骨架代码（hardcode）。

工作流（与 export_skeleton_template.py 配套）：
  1. 用户拿到 result/skeleton_template_f{floor}.svg，
     用矢量编辑器在走道区域用 **红色描边线**（stroke:#e74c3c; stroke-width≥3; fill:none）
     画出导航骨架，保存为 debug/skeleton_manual_f{floor}.svg
  2. 本脚本解析该 SVG：
     - 提取所有红色 stroke 的 <line>/<path>/<polyline>
     - 按模板同款公式反算回米制坐标
       cx = MINX + (sx - MARGIN_X) / SCALE
       cy = MAXY - (sy - MARGIN_Y) / SCALE
     - 生成 skeleton features（每段一条 LineString）
     - 从骨架线的端点/交点生成 TI 交叉点节点
     - 重建拓扑边（TI-TI 邻接 + 孤点挂接）
  3. 硬编码：同时把骨架线写进 result/skeleton_hardcode_f{floor}.json，
     之后解析无需再依赖 SVG（重复运行直接读 JSON）。

用法:
  python src/import_manual_skeleton.py            # 读 debug/skeleton_manual_f{1,2}.svg
  python src/import_manual_skeleton.py --floor 1  # 只处理 1F
  python src/import_manual_skeleton.py --apply    # 解析后写回 geojson 的 skeleton+topology
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point, shape, mapping
from shapely.ops import unary_union

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
SCALE = 8.0
MARGIN_X = 60
MARGIN_Y = 60
WALK_SPEED = 0.8
RED_RE = re.compile(r"#e74c3c|#ff0000|red", re.I)
STROKE_WIDTH_RE = re.compile(r"stroke-width:?\s*([\d.]+)")


# ---------------- SVG 解析 ----------------
def parse_svg_lines(svg_path, floor_no, minx, maxy):
    """返回米制 LineString 列表（从红色 stroke 元素提取）。"""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {"s": "http://www.w3.org/2000/svg"}
    lines = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        style = (elem.get("style") or "") + " " + (elem.get("stroke") or "")
        if not RED_RE.search(style):
            continue
        if tag == "line":
            pts = [(float(elem.get("x1")), float(elem.get("y1"))),
                   (float(elem.get("x2")), float(elem.get("y2")))]
            lines.append(pts)
        elif tag == "polyline":
            raw = elem.get("points", "")
            pts = [(float(a), float(b)) for a, b in
                   re.findall(r"([-\d.]+)[,\s]+([-\d.]+)", raw)]
            lines.append(pts)
        elif tag == "path":
            d = elem.get("d", "")
            pts = _path_to_points(d)
            lines.append(pts)
    # 转米制
    m_lines = []
    for pts in lines:
        if len(pts) < 2:
            continue
        m_pts = [(minx + (x - MARGIN_X) / SCALE,
                  maxy - (y - MARGIN_Y) / SCALE) for x, y in pts]
        m_lines.append(m_pts)
    return m_lines


def _path_to_points(d):
    """极简 path 解析：支持 M/L/H/V/C 基本指令，忽略曲线控制点。"""
    tokens = re.findall(r"[MLHVCHVZmlhvchvz]|[-+]?[\d.]+(?:[eE][-+]?\d+)?", d)
    pts = []
    cur = [0.0, 0.0]
    i = 0
    pending_cmd = "M"
    while i < len(tokens):
        t = tokens[i]
        if t in "MLHVCHVZmlhvchvz":
            pending_cmd = t
            i += 1
            continue
        try:
            v1 = float(t)
            v2 = float(tokens[i + 1])
        except (ValueError, IndexError):
            break
        i += 2
        cmd = pending_cmd.upper()
        if cmd in ("M", "L"):
            cur = [v1, v2]
            pts.append((v1, v2))
        elif cmd == "H":
            cur = [v1, cur[1]]
            pts.append(tuple(cur))
        elif cmd == "V":
            cur = [cur[0], v1]
            pts.append(tuple(cur))
        elif cmd in ("C", "S"):
            cur = [v1, v2]  # 取控制点后的终点近似
            pts.append(tuple(cur))
    return pts


# ---------------- 骨架 → 拓扑 ----------------
def build_from_lines(m_lines, min_dist=1.0):
    """从骨架线生成 (lines, ti_nodes, edges)。
    lines: 拆分后的 LineString 段（在交点处打断）
    ti_nodes: [{id, coordinates, type:intersection}]
    edges: [{id, from, to, distance, estimatedTime, ...}]
    """
    # 1. 收集所有端点 + 交点
    endpoints = []
    segments = []
    for pts in m_lines:
        for i in range(len(pts) - 1):
            seg = LineString([pts[i], pts[i + 1]])
            if seg.length < 0.05:
                continue
            segments.append(seg)
            endpoints.append(Point(pts[i]))
            endpoints.append(Point(pts[i + 1]))

    # 2. 端点去重（距离 < 0.3m 视为同一点），得到 TI 节点候选
    nodes_pts = []
    for ep in endpoints:
        dup = False
        for p in nodes_pts:
            if p.distance(ep) < 0.3:
                dup = True
                break
        if not dup:
            nodes_pts.append(ep)

    # 3. 交点：线段两两相交，交点若离所有节点 > 0.3m 则加入
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            inter = segments[i].intersection(segments[j])
            if inter.is_empty:
                continue
            pts = [inter] if inter.geom_type == "Point" else \
                (list(inter.geoms) if hasattr(inter, "geoms") else [])
            for ip in pts:
                if any(ip.distance(p) < 0.3 for p in nodes_pts):
                    continue
                nodes_pts.append(ip)

    # 4. 每个节点分配 TI id，坐标取整
    ti_nodes = []
    for k, p in enumerate(nodes_pts):
        ti_nodes.append({
            "id": f"TI-{k + 1:03d}",
            "type": "intersection",
            "coordinates": [round(p.x, 3), round(p.y, 3)],
            "public": True, "accessible": True, "riskLevel": 0.5,
        })

    # 5. 把每条线段打断：取线段与节点集的交集
    node_pts = [Point(n["coordinates"]) for n in ti_nodes]
    node_ids = {n["id"]: n for n in ti_nodes}
    edges = []
    edge_seq = 0
    seen_pairs = set()
    for seg in segments:
        # 线段与每个节点的距离：收集「在线段上」的节点投影点
        cut_pts = []
        for n in ti_nodes:
            p = Point(n["coordinates"])
            if seg.distance(p) < 0.3:
                proj = seg.project(p)
                cut_pts.append((proj, n["id"]))
        cut_pts.sort()
        # 分段：每两个相邻 cut 之间一段
        for a, b in zip(cut_pts, cut_pts[1:]):
            pa = Point(seg.interpolate(a[0]))
            pb = Point(seg.interpolate(b[0]))
            d = pa.distance(pb)
            if d < 0.05:
                continue
            key = tuple(sorted((a[1], b[1])))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            edge_seq += 1
            edges.append({
                "id": f"TE-{edge_seq:03d}",
                "from": a[1], "to": b[1],
                "distance": round(d, 3),
                "estimatedTime": round(d / WALK_SPEED, 2),
                "accessibilityLevel": 0, "riskLevel": 0.5,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
            })

    return segments, ti_nodes, edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", default=None)
    ap.add_argument("--apply", action="store_true",
                    help="解析后写回 geojson 的 skeleton + topology")
    args = ap.parse_args()

    geo = json.loads(Path(GEO_IN).read_text(encoding="utf-8"))
    for fk in ("1", "2"):
        if args.floor and fk != args.floor:
            continue
        svg_path = BASE_DIR / "debug" / f"skeleton_manual_f{fk}.svg"
        if not svg_path.exists():
            print(f"[F{fk}] 未找到 {svg_path}，跳过")
            continue
        fl = geo["floors"][fk]
        # 米制范围从模板一致：读 geojson 房间范围
        allb = []
        for r in fl["geometry"]["rooms"]:
            allb.append(shape(r["geometry"]).bounds)
        minx = min(b[0] for b in allb)
        maxy = max(b[3] for b in allb)

        m_lines = parse_svg_lines(str(svg_path), fk, minx, maxy)
        print(f"[F{fk}] 解析到 {len(m_lines)} 条红色线")

        # 硬编码 JSON
        hard_json = BASE_DIR / "result" / f"skeleton_hardcode_f{fk}.json"
        hard_json.write_text(
            json.dumps({"lines": [list(map(list, l)) for l in m_lines]},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  硬编码已写: {hard_json}")

        if args.apply:
            sk_segments, ti_nodes, edges = build_from_lines(m_lines)
            # 写 skeleton features
            feats = []
            for k, seg in enumerate(sk_segments):
                feats.append({
                    "type": "Feature", "id": f"SK-HC-{k + 1:03d}",
                    "geometry": mapping(seg),
                    "properties": {"type": "skeleton", "length_m": round(seg.length, 2)},
                })
            fl["skeleton"] = {"features": feats}
            # 写 topology：
            #   - 保留 TR/TD/TF/TEN 原节点 + 所有「不涉及 TI」的原边
            #     （TD→TR 内部边、TF→TD 楼梯/电梯连接、跨层 XE 等）
            #   - 替换所有 TI 节点为骨架生成的新 TI
            #   - 骨架 TI-TI 边 用新的；再把 TD/TF/TEN 挂到最近的新 TI
            old_nodes = [n for n in fl["topology"]["nodes"]
                         if n.get("type") != "intersection"]
            ti_ids = {n["id"] for n in ti_nodes}
            keep_edges = []
            for e in fl["topology"]["edges"]:
                a = e.get("from"); b = e.get("to")
                if a in ti_ids or b in ti_ids:
                    continue  # 旧 TI 边丢弃（骨架已重生成）
                keep_edges.append(e)
            # TD/TF/TEN → 最近 TI 挂接
            hung = 0
            for n in old_nodes:
                nt = n.get("type")
                if nt not in ("doorway", "facility", "facility_entrance"):
                    continue
                best, bd = None, 12.0
                for ti in ti_nodes:
                    c = ti["coordinates"]
                    d = math.hypot(n["coordinates"][0] - c[0],
                                   n["coordinates"][1] - c[1])
                    if d < bd:
                        bd, best = d, ti["id"]
                if best:
                    seq = max([int(e["id"].split("-")[-1]) for e in keep_edges] + [0])
                    is_stair = n.get("facilityType") == "staircase"
                    keep_edges.append({
                        "id": f"TE-{seq + 1:03d}",
                        "from": n["id"], "to": best,
                        "distance": round(bd, 3),
                        "estimatedTime": round(bd / WALK_SPEED, 2),
                        "accessibilityLevel": 999 if is_stair else 0,
                        "riskLevel": 10 if is_stair else 0.5,
                        "walkable": not is_stair,
                        "wheelchairAccessible": not is_stair,
                        "blindAccessible": not is_stair,
                    })
                    hung += 1
            fl["topology"]["nodes"] = old_nodes + ti_nodes
            fl["topology"]["edges"] = keep_edges + edges
            print(f"  骨架 {len(feats)} 段, TI {len(ti_nodes)} 个, 骨架边 {len(edges)} 条, "
                  f"保留旧边 {len(keep_edges)} 条(含挂接 {hung}), 已写入")
        else:
            sk_segments, ti_nodes, edges = build_from_lines(m_lines)
            print(f"  (预览) 骨架 {len(sk_segments)} 段, TI {len(ti_nodes)} 个, 边 {len(edges)} 条")

    if args.apply:
        Path(GEO_IN).write_text(json.dumps(geo, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print("已写回", GEO_IN)


if __name__ == "__main__":
    sys.exit(main())
