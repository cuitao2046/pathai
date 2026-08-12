# -*- coding: utf-8 -*-
"""聚类工具：并查集 + 通用 items 聚类 + bbox 网格聚类。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  UnionFind / cluster_items / bbox_clusters / _bbox_area_m2 / _bbox_aspect

门洞识别（detect_doors）、窗线分类（classify_window_layer）、楼梯/电梯
bbox 归并（detect_stair_boxes / detect_elevator_boxes）等均基于此层。
"""
import collections
import math

from src.common.constants import SCALE


class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def cluster_items(items, should_link):
    """通用聚类：items 列表 + should_link(i, j) -> 簇列表"""
    n = len(items)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if should_link(items[i], items[j]):
                uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(items[i])
    return list(groups.values())


def bbox_clusters(items, gap_pt):
    """对 drawings 的 bbox 中心做网格聚类，返回 bbox 多边形列表（pt）"""
    if not items:
        return []
    boxes = []
    for it in items:
        xs = [p[0] for p in it]
        ys = [p[1] for p in it]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    n = len(boxes)
    uf = UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            # bbox 间距 < gap
            dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
            dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
            if math.hypot(dx, dy) < gap_pt:
                uf.union(i, j)
    groups = collections.defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(boxes[i])
    out = []
    for g in groups.values():
        x0 = min(b[0] for b in g)
        y0 = min(b[1] for b in g)
        x1 = max(b[2] for b in g)
        y1 = max(b[3] for b in g)
        out.append((x0, y0, x1, y1))
    return out


def _bbox_area_m2(b):
    return (b[2] - b[0]) * (b[3] - b[1]) * SCALE * SCALE


def _bbox_aspect(b):
    w, h = abs(b[2] - b[0]), abs(b[3] - b[1])
    lo, hi = min(w, h), max(w, h)
    return (hi / lo) if lo > 1e-6 else 999.0
