#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对已有 GeoJSON 执行 TI(交叉口)节点聚集合并。

复用 src/skeleton/pipeline.py 的 _merge_nearby_ti_nodes，保持与生成管线一致。
用法:
    python debug/merge_ti_clusters.py [geojson_path] [--radius 1.5]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from skeleton.pipeline import _merge_nearby_ti_nodes, TI_MERGE_RADIUS_M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE_DIR / "result" / "school_building_01_map_v9.geojson"))
    ap.add_argument("--radius", type=float, default=TI_MERGE_RADIUS_M)
    args = ap.parse_args()

    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))

    total_before = total_after = clusters = 0
    for fk in sorted(geo.get("floors", {}).keys(), key=lambda x: int(x)):
        topo = geo["floors"][fk]["topology"]
        before = sum(1 for n in topo["nodes"] if n.get("type") == "intersection")
        nodes, edges, n_clu = _merge_nearby_ti_nodes(topo["nodes"], topo["edges"],
                                                     radius_m=args.radius)
        after = sum(1 for n in nodes if n.get("type") == "intersection")
        topo["nodes"] = nodes
        topo["edges"] = edges
        total_before += before
        total_after += after
        clusters += n_clu
        print(f"F{fk}: TI {before} -> {after} (合并簇 {n_clu})")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"总计: TI {total_before} -> {total_after}, 合并簇 {clusters}")
    print(f"已写回: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
