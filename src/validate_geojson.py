# -*- coding: utf-8 -*-
"""GeoJSON 输出验证（QA）脚本。

对 parse_cad_pdf.py 生成的楼层 GeoJSON 做结构与引用完整性检查：
  1. 顶层 schema 与 v7 参考格式对齐
  2. 每层 geometry 七类要素齐全、坐标有限
  3. 封闭空间（教室/办公室/卫生间等）必须至少有 1 扇门 —— 核心需求
  4. 门的 rooms 引用必须指向存在的房间
  5. semantic 与 geometry 房间集合一致；拓扑边无悬空引用

用法:
    python validate_geojson.py [geojson_path]
退出码: 0 = PASS, 1 = FAIL
"""
import json
import math
import sys

DEFAULT = r"E:\code\pathai\result\school_building_01_map_v8.geojson"

# 非"封闭房间"的类型：不参与零门检查
NON_ENCLOSED = ("staircase", "elevator_hall", "shaft", "atrium",
                "corridor", "lobby")

GEOM_KEYS = ["walls", "rooms", "doors", "stairs", "elevators",
             "columns", "windowSegments"]
TOP_KEYS = ["venueId", "venueName", "version", "coordinateSystem",
            "scale", "origin", "floors", "crossFloorEdges"]


def _finite(geom):
    t = geom["type"]
    cs = geom["coordinates"]
    if t == "Point":
        pts = [cs]
    elif t == "LineString":
        pts = cs
    elif t == "Polygon":
        pts = cs[0]
    else:  # MultiPolygon
        pts = cs[0][0]
    return all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in pts)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    ok = True

    missing = [k for k in TOP_KEYS if k not in d]
    print("schema 顶层缺失:", missing or "无")
    ok &= not missing
    print("coordinateSystem =", d.get("coordinateSystem"),
          "| scale =", d.get("scale"), "| origin =", d.get("origin"))

    floors = d["floors"]
    items = floors.items() if isinstance(floors, dict) else enumerate(floors, 1)
    for fid, f in sorted(items, key=lambda kv: int(kv[0])):
        g = f["geometry"]
        gm = [k for k in GEOM_KEYS if k not in g]
        print(f"--- F{fid} ---")
        print("  geometry 缺失:", gm or "无")
        ok &= not gm

        rooms = g["rooms"]
        doors = g["doors"]
        rids = {r["id"] for r in rooms}

        door_cnt = {}
        for dr in doors:
            for rid in dr["properties"].get("rooms", []):
                door_cnt[rid] = door_cnt.get(rid, 0) + 1
        zero = [r["properties"].get("label") for r in rooms
                if r["properties"].get("roomType") not in NON_ENCLOSED
                and door_cnt.get(r["id"], 0) == 0]
        orphan = [dr["id"] for dr in doors if not dr["properties"].get("rooms")]
        n_swing = sum(1 for x in doors if x["properties"].get("doorType") == "swing")
        n_fire = len(doors) - n_swing
        print(f"  房间 {len(rooms)} | 门 {len(doors)} "
              f"(swing {n_swing}/fire {n_fire})")
        print(f"  无门封闭房间: {len(zero)} {zero or ''} | "
              f"无归属门: {len(orphan)}")
        ok &= len(zero) == 0

        bad_ref = [dr["id"] for dr in doors
                   for rid in dr["properties"].get("rooms", [])
                   if rid not in rids]
        print("  门引用不存在房间:", bad_ref or "无")
        ok &= not bad_ref

        bad_geo = sum(1 for k in GEOM_KEYS for ft in g[k]
                      if not _finite(ft["geometry"]))
        print("  非法坐标要素数:", bad_geo)
        ok &= bad_geo == 0

        sem_ids = {r["id"] for r in f["semantic"]["rooms"]}
        same = sem_ids == rids
        print("  semantic/geometry 房间一致:", same)
        ok &= same

        nids = {n["id"] for n in f["topology"]["nodes"]}
        ebad = [e["id"] for e in f["topology"]["edges"]
                if e["from"] not in nids or e["to"] not in nids]
        print("  拓扑边悬空引用:", ebad or "无")
        ok &= not ebad

    print("crossFloorEdges:", len(d.get("crossFloorEdges", [])))
    print()
    print("VALIDATION", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
