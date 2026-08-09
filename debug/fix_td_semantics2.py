# -*- coding: utf-8 -*-
"""TD 语义权威化 v2（只改 rooms 字段，不动拓扑边，零失联风险）。

权威真相 = 几何门坐标贴墙封闭房间(<0.6m)。
对每个 TD（含 TDX）：
- 收集其 sourceDoorIds（无则就近几何门 <2.5m 补）→ 贴墙封闭房间并集 = 权威封闭集
- 开放空间房间（corridor/lobby 等）保留原样（TD.rooms 本可含它们用于展示）
- TD.rooms = 权威封闭集 + 原开放空间集
验证：真污染（rooms 含非贴墙封闭房间）应清零。

用法: python debug/fix_td_semantics2.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
WALL_TOL = 0.6
TD_TOL = 2.5
OPENP = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
         "stair_lobby", "staircase", "infrastructure", "elevator_hall"}


def room_type_of(fd, rid):
    for r in fd["geometry"].get("rooms", []):
        if r["id"] == rid:
            return r.get("properties", {}).get("roomType") or r.get("type")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))
    bak = p.with_name(p.stem + "_before_sem2" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        room_poly = {}
        for r in fd["geometry"].get("rooms", []):
            try: room_poly[r["id"]] = shape(r["geometry"])
            except Exception: pass
        dmap = {d["id"]: d for d in fd["geometry"].get("doors", [])}

        def wall_rooms(coords):
            pt = Point(coords)
            return {rid for rid in room_poly
                    if room_type_of(fd, rid) not in OPENP
                    and room_poly[rid].boundary.distance(pt) < WALL_TOL}

        # TDX → hub
        tdx_hub = {}
        for e in edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "doorway" and b["type"] == "doorway":
                ta, tb = a["id"], b["id"]
                if "TDX" in ta and "TDX" not in tb: tdx_hub[ta] = tb
                elif "TDX" in tb and "TDX" not in ta: tdx_hub[tb] = ta

        # 就近几何门匹配（距离优先）
        def nearest_doors(coords, tol=TD_TOL):
            c = tuple(coords)
            hits = []
            for did, d in dmap.items():
                pr = d.get("properties", {})
                if pr.get("doorType") not in ("swing", "fire"):
                    continue
                dc = tuple(d["geometry"]["coordinates"])
                dd = math.hypot(c[0]-dc[0], c[1]-dc[1])
                if dd < tol:
                    hits.append((dd, did))
            hits.sort()
            return [did for _, did in hits]

        stats = {"rebuild": 0, "no_src": 0, "filled": 0}
        for n in nodes:
            if n["type"] != "doorway":
                continue
            # source 几何门（已有优先，缺失则就近补）
            src = list(n.get("sourceDoorIds") or [])
            if not src:
                src = nearest_doors(n["coordinates"])
                if src:
                    n["sourceDoorIds"] = src
                    stats["filled"] += 1
            truth = set()
            for sid in src:
                d = dmap.get(sid)
                if d:
                    truth |= wall_rooms(d["geometry"]["coordinates"])
            cur_open = {rid for rid in (n.get("rooms") or [])
                        if room_type_of(fd, rid) in OPENP}
            if truth or cur_open or src:
                n["rooms"] = sorted(truth) + sorted(cur_open)
                stats["rebuild"] += 1
            else:
                stats["no_src"] += 1
        print(f"F{fk}: rooms重写={stats['rebuild']} 无源={stats['no_src']} 补source={stats['filled']}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
