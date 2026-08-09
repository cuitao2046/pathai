# -*- coding: utf-8 -*-
"""后处理：把「穿过墙/房间」的拓扑公共边改为「绕行」（贴走道、避开墙与房间），保连通。

背景
----
src/fix_crossing_edges.py 用「可见图」绕行，但候选走道顶点只取距直线弦 ≤3m 的点，
对「贴墙拐角的穿墙捷径」（走廊在墙另一侧绕行 >3m）会找不到路径而保留穿墙边。
本项目实测有 38 条公共拓扑边穿墙，其中含若干「桥边」（移除即断网），可见图方案失败。

本模块改用**可通行区栅格 A***：
  - 把每层 walkable（建筑轮廓 − 封闭房间）栅格化；
  - 对每条穿墙公共边，A* 在栅格里求 A→B 绕行路径（必在 walkable 内 → 不穿墙/房间）；
  - 对 A* 路径做贪心「拉绳」简化（仅保留直线段仍不穿墙的途径点）；
  - 用新增的 TWW 途径点把原边替换为多段链，端点不变 → 连通性不变。

与 fix_crossing_edges.py 的关系：本模块专注于「拓扑边不穿墙」，不改动骨架(skeleton)
渲染层；可独立运行，亦可在 fix_crossing_edges 之后再跑以补齐其漏掉的贴墙捷径。

用法
----
  python src/fix_wall_crossing.py result/school_building_01_map_v9.geojson
  python src/fix_wall_crossing.py in.geojson -o out.geojson
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

# 复用 route_rules 的穿墙判别（与导航校验同源，保证一致）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.topology.route_rules import RouteGraph  # noqa: E402
# 复用 fix_crossing_edges 的可通行区收集
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "fce", __file__.replace("fix_wall_crossing.py", "fix_crossing_edges.py"))
    fce = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(fce)
    collect_walkable = fce.collect_walkable
except Exception:  # pragma: no cover
    collect_walkable = None

WALK_SPEED_M_S = 0.8
RES_DEFAULT = 0.5  # 栅格分辨率（m）

PUBLIC_TYPES = {"intersection", "facility_entrance", "facility"}


class WalkGrid:
    """可通行区栅格 + 8 邻接 A*。"""

    def __init__(self, walkable_poly, res=RES_DEFAULT):
        self.res = res
        if walkable_poly is None:
            self.valid = False
            return
        self.valid = True
        self.poly = walkable_poly
        minx, miny, maxx, maxy = walkable_poly.bounds
        self.minx, self.miny = minx, miny
        self.nx = max(1, int(math.ceil((maxx - minx) / res)) + 1)
        self.ny = max(1, int(math.ceil((maxy - miny) / res)) + 1)
        # free[i,j] = True 表示可走
        self.free = np.zeros((self.nx, self.ny), dtype=bool)
        # 采样栅格中心是否在 walkable 内
        xs = self.minx + (np.arange(self.nx) + 0.5) * res
        ys = self.miny + (np.arange(self.ny) + 0.5) * res
        for ix in range(self.nx):
            cx = xs[ix]
            for iy in range(self.ny):
                self.free[ix, iy] = walkable_poly.contains(Point(cx, ys[iy]))

    def cell_of(self, pt):
        ix = int((pt[0] - self.minx) / self.res)
        iy = int((pt[1] - self.miny) / self.res)
        ix = min(max(ix, 0), self.nx - 1)
        iy = min(max(iy, 0), self.ny - 1)
        return ix, iy

    def world_of(self, ix, iy):
        return (self.minx + (ix + 0.5) * self.res,
                self.miny + (iy + 0.5) * self.res)

    def nearest_free(self, pt):
        ix, iy = self.cell_of(pt)
        if self.free[ix, iy]:
            return ix, iy
        # BFS 向外找最近空闲格
        seen = {(ix, iy)}
        q = [(ix, iy)]
        while q:
            x, y = q.pop(0)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self.nx and 0 <= ny < self.ny and (nx, ny) not in seen:
                        if self.free[nx, ny]:
                            return nx, ny
                        seen.add((nx, ny))
                        q.append((nx, ny))
        return None

    def astar(self, start, goal):
        """返回世界坐标路径（含起终点），失败返回 None。"""
        if not self.valid:
            return None
        s = self.nearest_free(start)
        g = self.nearest_free(goal)
        if s is None or g is None:
            return None
        if s == g:
            return [list(start), list(goal)]
        sx, sy = s
        gx, gy = g
        import heapq
        h = lambda x, y: math.hypot(x - gx, y - gy)
        dist = {s: 0.0}
        prev = {s: None}
        pq = [(h(sx, sy), 0.0, s)]
        while pq:
            _, d, (x, y) = heapq.heappop(pq)
            if (x, y) == g:
                break
            if d > dist[(x, y)]:
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if not (0 <= nx < self.nx and 0 <= ny < self.ny):
                        continue
                    if not self.free[nx, ny]:
                        continue
                    # 禁止对角切墙角：对角移动要求两个正交相邻格均空闲
                    if dx != 0 and dy != 0:
                        if not (self.free[x + dx, y] and self.free[x, y + dy]):
                            continue
                    nd = d + math.hypot(dx, dy) * self.res
                    if (nx, ny) not in dist or nd < dist[(nx, ny)]:
                        dist[(nx, ny)] = nd
                        prev[(nx, ny)] = (x, y)
                        heapq.heappush(pq, (nd + h(nx, ny), nd, (nx, ny)))
        if g not in prev:
            return None
        # 回溯
        cells = []
        cur = g
        while cur is not None:
            cells.append(cur)
            cur = prev[cur]
        cells.reverse()
        return [list(self.world_of(cx, cy)) for cx, cy in cells]


def string_pull(pts, wall_cross_fn):
    """贪心拉绳：只保留「直线段仍不穿墙」的途径点。返回简化后世界坐标点列表。"""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    i = 0
    n = len(pts)
    while i < n - 1:
        j = n - 1
        while j > i + 1:
            if not wall_cross_fn(pts[i], pts[j]):
                break
            j -= 1
        out.append(pts[j])
        i = j
    return out


def _mk_chain_edge(prefix, seq, frm, to, dist, src):
    return {
        "id": f"{prefix}-TE-{seq:04d}",
        "from": frm, "to": to,
        "distance": round(dist, 3),
        "estimatedTime": round(dist / WALK_SPEED_M_S, 2),
        "accessibilityLevel": src.get("accessibilityLevel", 0),
        "riskLevel": src.get("riskLevel", 1),
        "walkable": src.get("walkable", True),
        "wheelchairAccessible": src.get("wheelchairAccessible", True),
        "blindAccessible": src.get("blindAccessible", True),
        "type": src.get("type"),
        "doorType": src.get("doorType"),
        "crossFloor": bool(src.get("crossFloor")),
    }


def process_floor(fl, floor_no, g, res=RES_DEFAULT):
    if collect_walkable is None:
        return 0, 0, 0, 0
    W, W_buf, _ = collect_walkable(fl)
    if W is None:
        return 0, 0, 0, 0
    grid = WalkGrid(W, res)
    if not grid.valid:
        return 0, 0, 0, 0
    nodes = fl.get("topology", {}).get("nodes") or []
    edges = fl.get("topology", {}).get("edges") or []
    nmap = {n["id"]: n for n in nodes}
    new_edges = []
    new_nodes = []
    rerouted = 0
    kept = 0
    failed = 0
    seq = max([int(e["id"].split("-")[-1]) for e in edges
               if e.get("id", "").startswith(f"{floor_no}-TE-")] + [0])
    wi = 0

    for e in edges:
        a = nmap.get(e["from"])
        b = nmap.get(e["to"])
        if not a or not b:
            new_edges.append(e); kept += 1; continue
        # 仅处理公共节点之间的边（排除 room/doorway 端点，它们是合法通道）
        ta, tb = a.get("type"), b.get("type")
        if ta in ("room", "doorway") or tb in ("room", "doorway"):
            new_edges.append(e); kept += 1; continue
        # 排除跨层边（合法穿层）
        if e.get("crossFloor") or a.get("floor") != b.get("floor"):
            new_edges.append(e); kept += 1; continue
        if not (ta == "intersection" and tb == "intersection"):
            # 仅重路由「纯走道段」(intersection↔intersection)。
            # facility/facility_entrance 端点边位于设施自身墙开口处（合法通道），
            # room/doorway 端点边是房间门口（合法通道），均不在此处理。
            new_edges.append(e); kept += 1; continue
        ac, bc = a.get("coordinates"), b.get("coordinates")
        if not ac or not bc:
            new_edges.append(e); kept += 1; continue
        # 是否穿墙（与 route_rules 同源判别）
        if not seg_crosses_any_wall(g, ac, bc):
            # 不穿墙：保留
            new_edges.append(e); kept += 1; continue
        # 穿墙：先把两端 TI 节点拉回走道内（部分骨架节点略偏出 walkable，
        # 若直接用原坐标做绕行，首/末段会再次切墙）。原地更新节点坐标（移动 <0.5m）。
        ac2 = grid.world_of(*grid.nearest_free(ac))
        bc2 = grid.world_of(*grid.nearest_free(bc))
        a["coordinates"] = [round(ac2[0], 3), round(ac2[1], 3)]
        b["coordinates"] = [round(bc2[0], 3), round(bc2[1], 3)]
        ac, bc = a["coordinates"], b["coordinates"]
        # 栅格 A* 绕行（起始/终点均已在走道内 → 路径不穿墙/房间）
        path = grid.astar(ac, bc)
        if not path or len(path) < 2:
            new_edges.append(e); failed += 1; continue
        simplified = string_pull(path, lambda p, q: seg_crosses_any_wall(g, p, q))
        # 若简化后只剩端点（退化），回退用完整 A* 路径
        if len(simplified) < 2:
            simplified = path
        # 重建链：ac -> wp1 -> ... -> wpk -> bc
        waypoints = simplified[1:-1] if len(simplified) >= 2 else []
        prev = e["from"]
        seg_coords = [ac] + waypoints + [bc]
        for k, wp in enumerate(waypoints):
            wi += 1
            nid = f"{floor_no}-TWW-{wi:04d}"
            new_nodes.append({
                "id": nid, "type": "intersection", "floor": floor_no,
                "coordinates": [round(wp[0], 3), round(wp[1], 3)],
                "public": True, "accessible": True,
                "riskLevel": e.get("riskLevel", 1),
            })
            d = math.hypot(seg_coords[k][0] - wp[0], seg_coords[k][1] - wp[1])
            seq += 1
            new_edges.append(_mk_chain_edge(f"{floor_no}", seq, prev, nid, d, e))
            prev = nid
        d = math.hypot(seg_coords[-2][0] - bc[0], seg_coords[-2][1] - bc[1])
        seq += 1
        new_edges.append(_mk_chain_edge(f"{floor_no}", seq, prev, e["to"], d, e))
        rerouted += 1

    fl.setdefault("topology", {})["edges"] = new_edges
    fl["topology"]["nodes"] = nodes + new_nodes
    return len(edges), len(new_edges), rerouted, kept, failed


def seg_crosses_any_wall(g, p1, p2):
    """p1->p2 是否真正穿墙（与 route_rules.validate_wall_crossing 同源判别，
    遍历 bbox 内所有墙线段）。"""
    minx = min(p1[0], p2[0]); maxx = max(p1[0], p2[0])
    miny = min(p1[1], p2[1]); maxy = max(p1[1], p2[1])
    for wl, wb in zip(g.wall_lines, g.wall_bounds):
        if wb[0] > maxx or wb[2] < minx or wb[1] > maxy or wb[3] < miny:
            continue
        if g._segment_crosses_wall(p1, p2, wl.coords[0], wl.coords[-1]):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="将穿墙拓扑边改为栅格A*绕行（保连通）")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--res", type=float, default=RES_DEFAULT)
    args = ap.parse_args()
    path = __import__("pathlib").Path(args.input)
    geo = json.loads(path.read_text(encoding="utf-8"))
    g = RouteGraph(geo)

    for fk, fl in (geo.get("floors") or {}).items():
        fn = int(fk)
        total, kept_edges, rerouted, kept, failed = process_floor(fl, fn, g, args.res)
        print(f"[F{fk}] 边 {total} → {kept_edges}（栅格绕行 {rerouted}，保留 {kept}"
              f"，绕行失败保留 {failed}）")

    out = __import__("pathlib").Path(args.output) if args.output else path
    out.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入: {out}")


if __name__ == "__main__":
    main()
