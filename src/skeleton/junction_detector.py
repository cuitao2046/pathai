# -*- coding: utf-8 -*-
"""T6: 骨架交叉口（degree≥3）与端点检测，degree=2 路径简化。"""

from __future__ import annotations

from typing import Dict, List, Tuple

import networkx as nx


def detect_junctions(G: nx.Graph) -> dict:
    """
    按度数分类节点。

    Returns
    -------
    {
      "junctions": [(node_id, x, y), ...],   # degree ≥ 3 → TI
      "terminals": [(node_id, x, y), ...],   # degree = 1
      "passing":   [(node_id, x, y), ...],   # degree = 2
    }
    """
    junctions, terminals, passing = [], [], []
    for n, d in G.degree():
        data = G.nodes[n]
        item = (n, data["x"], data["y"])
        if d >= 3:
            junctions.append(item)
        elif d == 1:
            terminals.append(item)
        elif d == 2:
            passing.append(item)
    return {
        "junctions": junctions,
        "terminals": terminals,
        "passing": passing,
    }


def simplify_degree2_paths(G: nx.Graph) -> nx.Graph:
    """
    合并连续 degree=2 节点为一条边，减少中间路径点。
    保留所有 degree≠2 节点及边权 = 原路径测地长度之和。
    """
    if G.number_of_nodes() == 0:
        return G.copy()

    H = nx.Graph()
    deg = dict(G.degree())
    key_nodes = [n for n, d in deg.items() if d != 2]
    if not key_nodes:
        # 纯环：保留一个代表点
        n0 = next(iter(G.nodes()))
        H.add_node(n0, **G.nodes[n0])
        return H

    for n in key_nodes:
        H.add_node(n, **G.nodes[n])

    visited_edges = set()

    def ek(a, b):
        return (a, b) if a <= b else (b, a)

    for start in key_nodes:
        for nbr in G.neighbors(start):
            e = ek(start, nbr)
            if e in visited_edges:
                continue
            path = [start, nbr]
            visited_edges.add(e)
            prev, cur = start, nbr
            length = G.edges[start, nbr].get("length", 0.0)
            while deg.get(cur, 0) == 2:
                nxts = [x for x in G.neighbors(cur) if x != prev]
                if not nxts:
                    break
                nxt = nxts[0]
                visited_edges.add(ek(cur, nxt))
                length += G.edges[cur, nxt].get("length", 0.0)
                path.append(nxt)
                prev, cur = cur, nxt
            end = path[-1]
            if end not in H:
                H.add_node(end, **G.nodes[end])
            if start != end:
                if H.has_edge(start, end):
                    # 并行路径取较短
                    if length < H.edges[start, end].get("length", float("inf")):
                        H.edges[start, end]["length"] = length
                else:
                    H.add_edge(start, end, length=length)

    return H
