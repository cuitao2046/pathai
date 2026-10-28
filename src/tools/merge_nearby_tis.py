# -*- coding: utf-8 -*-
"""合并同层相邻（< 半径）的 TI↔TI 拓扑节点，保留原有边长。

语义（按用户确认的合并策略）：
  - 只合并 intersection ↔ intersection（不碰房间/门/设施/出入口）；
  - 代表节点取度数最大者（并列取 id 小者），坐标保持不动；
  - 被合并节点上的边改接到代表节点，**distance/estimatedTime 保持原值**；
  - 合并簇内部的连接边成为自环被移除；同端点并行边保留较短者；
  - 合并后必须仍满足公共空间边不穿墙（否则拒绝写入）。

用法（仓库根目录）：
  python -m src.tools.merge_nearby_tis --dry-run
  python -m src.tools.merge_nearby_tis          # 原地改写，先备份 .bak
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from src.tools.fix_topology_walls import (  # noqa: E402
    WallIndex, _cross_dists, _pt_seg_dist)

DEFAULT_GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
START_RADIUS = {"doorway": 1.5, "facility_entrance": 1.5, "facility": 2.5,
                "intersection": 0.1}


def public_edge_crossings(floor_geo, nodes):
    """公共空间边穿墙数（与转换器发布门槛同口径）。"""
    walls = WallIndex(floor_geo)
    public_types = {"intersection", "doorway", "facility_entrance"}
    bad = []
    for e in floor_geo["topology"]["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b:
            continue
        types = {a["type"], b["type"]}
        if not types <= public_types:
            continue
        if types in ({"doorway"}, {"facility_entrance"}):
            continue
        radius = START_RADIUS.get(a["type"], 0.1)
        for wa, wb in walls._near(a["coordinates"], b["coordinates"]):
            hit = _cross_dists(a["coordinates"], b["coordinates"], wa, wb)
            if not hit:
                continue
            if hit[0] <= radius or hit[1] <= 0.2:
                continue
            if _pt_seg_dist(a["coordinates"], wa, wb) <= radius:
                continue
            bad.append(e["id"])
            break
    return bad


def merge_floor(floor_geo, radius, dry_run=False):
    nodes = floor_geo["topology"]["nodes"]
    by_id = {n["id"]: n for n in nodes}
    deg = Counter()
    for e in floor_geo["topology"]["edges"]:
        deg[e["from"]] += 1
        deg[e["to"]] += 1

    # 1) 收集 TI↔TI 近邻对并聚类
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    pairs = []
    tis = [n for n in nodes if n["type"] == "intersection"]
    for i in range(len(tis)):
        for j in range(i + 1, len(tis)):
            a, b = tis[i], tis[j]
            d = math.hypot(a["coordinates"][0] - b["coordinates"][0],
                           a["coordinates"][1] - b["coordinates"][1])
            if d < radius:
                pairs.append((a["id"], b["id"], round(d, 3)))
                union(a["id"], b["id"])
    groups = {}
    for a, b, _ in pairs:
        for x in (a, b):
            groups.setdefault(find(x), set()).add(x)
    clusters = [v for v in groups.values() if len(v) > 1]
    if dry_run:
        return clusters, pairs, []

    # 2) 选代表、删节点
    remap = {}
    detail = []
    for group in clusters:
        members = sorted((by_id[x] for x in group),
                         key=lambda n: (-deg[n["id"]], n["id"]))
        rep = members[0]
        rep["riskLevel"] = max(m["riskLevel"] for m in members)
        rep["public"] = any(m.get("public") for m in members)
        rep["accessible"] = any(m.get("accessible") for m in members)
        rep["blindAccessible"] = all(m.get("blindAccessible", True) for m in members)
        rep["wheelchairAccessible"] = all(
            m.get("wheelchairAccessible", True) for m in members)
        for m in members[1:]:
            remap[m["id"]] = rep["id"]
        detail.append((rep["id"], [m["id"] for m in members[1:]]))
    floor_geo["topology"]["nodes"] = [n for n in nodes if n["id"] not in remap]

    # 3) 边改接：保留原 distance/estimatedTime；去自环；并行边取短
    kept, seen = [], {}
    for e in floor_geo["topology"]["edges"]:
        a, b = remap.get(e["from"], e["from"]), remap.get(e["to"], e["to"])
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        ec = dict(e, **{"from": a, "to": b})
        if key not in seen or ec.get("distance", 0) < seen[key].get("distance", 0):
            seen[key] = ec
    kept = list(seen.values())
    floor_geo["topology"]["edges"] = kept
    return clusters, pairs, detail


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--geo", default=str(DEFAULT_GEO))
    ap.add_argument("--out", default=None)
    ap.add_argument("--radius", type=float, default=0.5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = Path(args.geo)
    geo = json.loads(path.read_text(encoding="utf-8"))
    total_removed = 0
    for fk in sorted(geo["floors"]):
        fl = geo["floors"][fk]
        clusters, pairs, detail = merge_floor(fl, args.radius, args.dry_run)
        print(f"[F{fk}] 近邻对 {len(pairs)}，合并簇 {len(clusters)}，"
              f"删除节点 {sum(len(d[1]) for d in detail) if not args.dry_run else sum(len(c) - 1 for c in clusters)}")
        if not args.dry_run:
            for rep, gone in detail:
                print(f"    {rep} ← {gone}")
        total_removed += (sum(len(d[1]) for d in detail) if not args.dry_run
                          else sum(len(c) - 1 for c in clusters))

    if args.dry_run:
        print("（dry-run，未写文件）")
        return

    # 合并后穿墙复核
    for fk in sorted(geo["floors"]):
        fl = geo["floors"][fk]
        nodes = {n["id"]: n for n in fl["topology"]["nodes"]}
        bad = public_edge_crossings(fl, nodes)
        assert not bad, f"F{fk} 合并后出现公共空间边穿墙：{bad[:5]}"
    print(f"合并完成：删除 {total_removed} 个 TI；公共空间边穿墙 0")

    out = Path(args.out) if args.out else path
    if out == path:
        bak = out.with_suffix(out.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(out, bak)
            print(f"已备份 {bak}")
    out.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
