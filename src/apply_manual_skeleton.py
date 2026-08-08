#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把手动骨架（含桥接）覆盖进 geojson：skeleton + topology。

数据源：result/skeleton_manual_bridged.json（用户画的红线 + 自动桥接）
   - skeleton_features / ti_nodes / edges / bridges
写入：
   - floors[fk].skeleton = 手动骨架 features
   - floors[fk].topology.nodes = 保留 TR/TD/TF/TEN + 新 TI
   - floors[fk].topology.edges = 保留「不涉及旧 TI」的边 + 新骨架边
     + TD/TF/TEN 挂到最近新 TI
节点/边 id 加楼层前缀（F1-/F2-），与 geojson 其它 id 风格一致。

用法：
  python src/apply_manual_skeleton.py [--src result/skeleton_manual_bridged.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GEO = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
WALK_SPEED = 0.8


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(BASE_DIR / "result" / "skeleton_manual_bridged.json"))
    ap.add_argument("--out", default=GEO)
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    geo = json.loads(Path(GEO).read_text(encoding="utf-8"))

    for fk in ("1", "2"):
        if fk not in data:
            print(f"[F{fk}] 数据缺失，跳过")
            continue
        d = data[fk]
        fl = geo["floors"][fk]

        # 1) skeleton features（加楼层前缀 id）
        feats = []
        for i, f in enumerate(d["skeleton_features"]):
            f = dict(f)
            f["id"] = f"{fk}-SK-HC-{i + 1:04d}"
            feats.append(f)
        fl["skeleton"] = {"features": feats}

        # 2) 节点：保留 TR/TD/TF/TEN，替换 TI（加楼层前缀）
        old_nodes = [n for n in fl["topology"]["nodes"]
                     if n.get("type") != "intersection"]
        ti_nodes = []
        for n in d["ti_nodes"]:
            ti_nodes.append({
                "id": f"{fk}-{n['id']}",
                "type": "intersection",
                "floor": fk,
                "coordinates": n["coordinates"],
                "public": True, "accessible": True, "riskLevel": 0.5,
            })
        ti_by_src = {n["id"]: f"{fk}-{n['id']}" for n in d["ti_nodes"]}
        ti_ids = {tn["id"] for tn in ti_nodes}

        # 3) 边：保留不涉及旧 TI 的原边；丢弃旧 TI 相关边
        keep_edges = []
        for e in fl["topology"]["edges"]:
            a, b = e.get("from"), e.get("to")
            if (a or "").startswith(f"{fk}-TI-") or (b or "").startswith(f"{fk}-TI-"):
                continue  # 旧自动骨架 TI 边丢弃
            keep_edges.append(e)

        # 4) 新骨架边（from/to 映射到新 id）
        new_edges = []
        for e in d["edges"]:
            fa, fb = e["from"], e["to"]
            if fa not in ti_by_src or fb not in ti_by_src:
                continue
            new_edges.append({
                "id": f"{fk}-TE-HC-{len(new_edges) + 1:04d}",
                "from": ti_by_src[fa], "to": ti_by_src[fb],
                "distance": e["distance"],
                "estimatedTime": e["estimatedTime"],
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "riskLevel": e.get("riskLevel", 0.5),
                "walkable": e.get("walkable", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "blindAccessible": e.get("blindAccessible", True),
            })

        # 5) TD/TF/TEN 挂到最近新 TI
        seq = 0
        hung = 0
        for n in old_nodes:
            nt = n.get("type")
            if nt not in ("doorway", "facility", "facility_entrance"):
                continue
            best, bd = None, 12.0
            for tn in ti_nodes:
                c = tn["coordinates"]
                dd = math.hypot(n["coordinates"][0] - c[0],
                                n["coordinates"][1] - c[1])
                if dd < bd:
                    bd, best = dd, tn["id"]
            if best:
                is_stair = n.get("facilityType") == "staircase"
                seq += 1
                keep_edges.append({
                    "id": f"{fk}-TE-HC-{1000 + seq:04d}",
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
        fl["topology"]["edges"] = keep_edges + new_edges
        print(f"[F{fk}] 骨架 {len(feats)} 段, TI {len(ti_nodes)}, "
              f"新边 {len(new_edges)}, 保留边 {len(keep_edges)} (挂接 {hung})")

    Path(args.out).write_text(json.dumps(geo, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("已写入", args.out)


if __name__ == "__main__":
    sys.exit(main())
