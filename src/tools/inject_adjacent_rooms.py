# -*- coding: utf-8 -*-
"""
为信标部署方案注入 adjacentRooms 字段（TODO-1：补相邻房间语义）。

规则：以信标坐标为圆心、ADJ_RADIUS(3.0m) 为半径，命中（多边形距离 ≤ 半径）的房间
即视为「相邻房间」，按到信标的距离升序写入 adjacentRooms:[roomId...]。

- 与 docs/18 五层模型 L4 一致：adjacentRooms 是信标语义层的「相邻房间」引用，
  房间名（label）由 GeoJSON 房间属性解析，不在本层重复存储。
- 保留全部既有字段与单坐标 schema（coordinates 真值不变）；幂等（重跑结果稳定）。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe src/tools/inject_adjacent_rooms.py
（依赖 shapely；路径基于 __file__ 推导，禁止 hardcode）
"""
from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Point, Polygon

# ---------- 阈值常量 ----------
ADJ_RADIUS = 3.0  # 相邻房间判定半径（m），与项目 3m 语义吸附/归属阈值一致

# ---------- 路径推导（项目惯例：基于 __file__，不 hardcode） ----------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BEACON_JSON = BASE_DIR / "result" / "ble_deployment.json"
MAP_JSON = BASE_DIR / "result" / "school_building_01_map_v9.geojson"


def load_data():
    beacons = json.load(open(BEACON_JSON, encoding="utf-8"))
    geo = json.load(open(MAP_JSON, encoding="utf-8"))
    return beacons, geo


def room_polys(geo):
    """floor -> [(roomId, label, Polygon), ...]"""
    out = {}
    for fk, fd in geo["floors"].items():
        lst = []
        for r in fd["geometry"].get("rooms", []):
            p = r["properties"]
            rid = p.get("roomId") or r["id"]
            lst.append((rid, p.get("label", ""), Polygon(r["geometry"]["coordinates"][0])))
        out[str(fk)] = lst
    return out


def main() -> int:
    data, geo = load_data()
    polys = room_polys(geo)

    changed = 0
    total = len(data["beacons"])
    sizes = []
    for b in data["beacons"]:
        fl = str(b.get("floor"))
        try:
            pt = Point(float(b["coordinates"][0]), float(b["coordinates"][1]))
        except (KeyError, TypeError, ValueError):
            continue
        near = []
        for rid, _label, poly in polys.get(fl, []):
            d = poly.distance(pt)
            if d <= ADJ_RADIUS:
                near.append((round(d, 3), rid))
        near.sort()
        adj = [rid for _, rid in near]
        if b.get("adjacentRooms") != adj:
            b["adjacentRooms"] = adj
            changed += 1
        sizes.append(len(adj))

    json.dump(data, open(BEACON_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    with_adj = sum(1 for b in data["beacons"] if b.get("adjacentRooms"))
    print(f"[inject_adjacent_rooms] 处理 {total} 枚信标，更新 {changed} 枚；"
          f"含 adjacentRooms 的信标 {with_adj}/{total}（半径={ADJ_RADIUS}m）")
    if sizes:
        print(f"[inject_adjacent_rooms] 相邻房间数: min={min(sizes)} "
              f"max={max(sizes)} avg={sum(sizes)/len(sizes):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
