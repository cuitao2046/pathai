# -*- coding: utf-8 -*-
"""给 TD / TDX 拓扑节点增加引用字段：
- TD (doorway hub):  `sourceDoorIds` = 该门合并自的 geometry.doors id 列表
- TDX 子节点:        `sourceDoorIds` = 其 hub 的 sourceDoorIds（同一物理门）

回填依据（双判据，避免坐标漂移误配）：
1. rooms 交集：TD.rooms ∩ 几何门.properties.rooms 非空（强判据）
2. 距离：TD 坐标 ↔ 几何门坐标 < 1.5m（弱判据，兜底 rooms 漂移）

用法: python debug/backfill_door_refs.py [geojson]
"""
from __future__ import annotations
import argparse, json, math
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MATCH_TOL = 1.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))
    bak = p.with_name(p.stem + "_before_doorrefs" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        # 几何门
        geo_doors = []
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords:
                continue
            geo_doors.append({
                "id": d["id"],
                "rooms": set(pr.get("rooms") or []),
                "coords": tuple(coords),
            })

        # TDX → hub
        tdx_hub = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b:
                continue
            if a["type"] == "doorway" and b["type"] == "doorway":
                ta, tb = a["id"], b["id"]
                if "TDX" in ta and "TDX" not in tb:
                    tdx_hub[ta] = tb
                elif "TDX" in tb and "TDX" not in ta:
                    tdx_hub[tb] = ta

        # ---- Pass 1: 给 hub TD 回填 sourceDoorIds ----
        stats = {"hub_filled": 0, "tdx_inherit": 0, "hub_none": 0}
        for n in nodes:
            if n["type"] != "doorway" or "TDX" in n["id"]:
                continue
            n_rooms = set(n.get("rooms") or [])
            c = tuple(n["coordinates"])
            matched = []
            for g in geo_doors:
                dist = math.hypot(c[0]-g["coords"][0], c[1]-g["coords"][1])
                if dist >= MATCH_TOL:
                    continue
                share = bool(n_rooms & g["rooms"])
                matched.append((dist, share, g["id"]))
            if not matched:
                stats["hub_none"] += 1
                continue
            # 优先 rooms 交集的；否则取最近
            share_ids = [gid for _, sh, gid in matched if sh]
            if share_ids:
                src = sorted(share_ids)
            else:
                best = min(matched, key=lambda x: x[0])
                src = [best[2]]
            n["sourceDoorIds"] = src
            stats["hub_filled"] += 1

        # ---- Pass 2: TDX 继承 hub ----
        for n in nodes:
            if n["type"] != "doorway" or "TDX" not in n["id"]:
                continue
            hub = tdx_hub.get(n["id"])
            if hub and hub in nmap and nmap[hub].get("sourceDoorIds"):
                n["sourceDoorIds"] = list(nmap[hub]["sourceDoorIds"])
                stats["tdx_inherit"] += 1

        print(f"F{fk}: hub回填={stats['hub_filled']} TDX继承={stats['tdx_inherit']} "
              f"hub无匹配={stats['hub_none']}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
