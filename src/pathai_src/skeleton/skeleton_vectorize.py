# -*- coding: utf-8 -*-
"""T4: 骨架像素 → NetworkX 图 + 矢量化 LineString。"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import networkx as nx
import numpy as np
from shapely.geometry import LineString, Point

from .medial_axis import pixel_to_world, _neighbors8


def skeleton_to_graph(
    skeleton_mask: np.ndarray,
    origin_x: float,
    origin_y: float,
    resolution: float,
) -> nx.Graph:
    """
    8-邻域连通骨架像素 → 无向图。
    节点 id = (row, col)；属性 x,y 为米制世界坐标。
    边属性 length = 像素中心距 * resolution 的欧氏长度。
    """
    G = nx.Graph()
    h, w = skeleton_mask.shape
    rs, cs = np.where(skeleton_mask)
    for r, c in zip(rs.tolist(), cs.tolist()):
        x, y = pixel_to_world(r, c, origin_x, origin_y, resolution)
        G.add_node((r, c), x=x, y=y, row=r, col=c)

    for r, c in list(G.nodes()):
        for rr, cc in _neighbors8(r, c, h, w):
            if (rr, cc) in G and (r, c) < (rr, cc):
                x1, y1 = G.nodes[(r, c)]["x"], G.nodes[(r, c)]["y"]
                x2, y2 = G.nodes[(rr, cc)]["x"], G.nodes[(rr, cc)]["y"]
                G.add_edge((r, c), (rr, cc), length=math.hypot(x2 - x1, y2 - y1))
    return G


def _douglas_peucker(coords: List[Tuple[float, float]], tol: float):
    """简单 Douglas-Peucker 折线简化。"""
    if len(coords) <= 2:
        return coords

    def _perp_dist(p, a, b):
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    def _rec(pts):
        if len(pts) <= 2:
            return pts
        a, b = pts[0], pts[-1]
        max_d, idx = -1.0, 0
        for i in range(1, len(pts) - 1):
            d = _perp_dist(pts[i], a, b)
            if d > max_d:
                max_d, idx = d, i
        if max_d > tol:
            left = _rec(pts[: idx + 1])
            right = _rec(pts[idx:])
            return left[:-1] + right
        return [a, b]

    return _rec(coords)


def graph_to_linestrings(
    G: nx.Graph,
    simplify_tol_m: float = 0.1,
) -> List[LineString]:
    """
    将骨架图拆成路径段并矢量化。

    策略：在 degree≠2 的节点处断开，提取 degree=2 链；
    每条链简化为 LineString。
    """
    if G.number_of_nodes() == 0:
        return []

    deg = dict(G.degree())
    key_nodes = {n for n, d in deg.items() if d != 2}
    # 孤立点
    if not key_nodes and G.number_of_nodes() > 0:
        # 整图是一个环或单链
        key_nodes = {next(iter(G.nodes()))}

    visited_edges = set()
    lines: List[LineString] = []

    def edge_key(a, b):
        return (a, b) if a <= b else (b, a)

    for start in key_nodes:
        for nbr in list(G.neighbors(start)):
            ek = edge_key(start, nbr)
            if ek in visited_edges:
                continue
            # 沿链走
            path = [start, nbr]
            visited_edges.add(ek)
            prev, cur = start, nbr
            while deg.get(cur, 0) == 2:
                nxts = [n for n in G.neighbors(cur) if n != prev]
                if not nxts:
                    break
                nxt = nxts[0]
                visited_edges.add(edge_key(cur, nxt))
                path.append(nxt)
                prev, cur = cur, nxt
            coords = [(G.nodes[n]["x"], G.nodes[n]["y"]) for n in path]
            if simplify_tol_m > 0 and len(coords) > 2:
                coords = _douglas_peucker(coords, simplify_tol_m)
            if len(coords) >= 2:
                lines.append(LineString(coords))

    # 未被访问的边（小环）
    for a, b in G.edges():
        ek = edge_key(a, b)
        if ek in visited_edges:
            continue
        coords = [(G.nodes[a]["x"], G.nodes[a]["y"]),
                  (G.nodes[b]["x"], G.nodes[b]["y"])]
        lines.append(LineString(coords))
        visited_edges.add(ek)

    return lines


def geodesic_length(G: nx.Graph, u, v) -> float:
    """图上最短路径长度（米）。"""
    try:
        return nx.shortest_path_length(G, u, v, weight="length")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return float("inf")


def nearest_graph_node(G: nx.Graph, x: float, y: float):
    """欧氏最近骨架节点。"""
    best, best_d = None, float("inf")
    for n, data in G.nodes(data=True):
        d = math.hypot(data["x"] - x, data["y"] - y)
        if d < best_d:
            best, best_d = n, d
    return best, best_d
