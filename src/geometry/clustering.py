# -*- coding: utf-8 -*-
"""聚类工具：并查集 + 通用 items 聚类 + bbox 网格聚类。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  UnionFind / cluster_items / bbox_clusters / _bbox_area_m2 / _bbox_aspect

门洞识别（detect_doors）、窗线分类（classify_window_layer）、楼梯/电梯
bbox 归并（detect_stair_boxes / detect_elevator_boxes）等均基于此层。
"""
import collections
import math

from shapely.geometry import box as sbox
from shapely.strtree import STRtree

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
    """通用聚类：items 列表 + should_link(i, j) -> 簇列表

    审查 C3：通用 `should_link` 语义由调用方决定，无法通用剪枝，保持 O(n²)
    全对试探。预期规模：单层门/窗线段 ≤ 数百条（约 1e5 对），解析耗时可忽略；
    若未来扩展到多层/多楼批量解析，再按各调用点的距离判据改用 STRtree。
    """
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
    """对 drawings 的 bbox 中心做网格聚类，返回 bbox 多边形列表（pt）

    审查 C3：候选剪枝由 O(n²) 全对扫描降为 STRtree 半径近邻——
    对每个 box 以其膨胀 gap_pt 的邻域查询候选（超集），再用与原逻辑
    完全相同的精确间距判定 union，结果逐对一致。
    """
    if not items:
        return []
    boxes = []
    for it in items:
        xs = [p[0] for p in it]
        ys = [p[1] for p in it]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    n = len(boxes)
    geoms = [sbox(*b) for b in boxes]
    tree = STRtree(geoms)
    uf = UnionFind(n)
    for i in range(n):
        a = boxes[i]
        # 膨胀 gap_pt 的 box 为查询几何：原 box 间距 < gap_pt 的对必相交（超集）
        for j in tree.query(geoms[i].buffer(gap_pt)):
            j = int(j)
            if j <= i:
                continue
            b = boxes[j]
            # 与原实现一致的精确间距判定
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
