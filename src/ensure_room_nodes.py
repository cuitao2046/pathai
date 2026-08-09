#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T13: 为每个语义房间补齐拓扑 TR 节点。

背景
----
parse_cad_pdf → skeleton pipeline 在生成拓扑节点时，会跳过部分「有标签、
有几何多边形，但因 room_index/门归属等原因为未生成 TR」的房间。
结果：semantic.rooms 里存在房间，但 topology 中没有对应的 room 节点，
导览图上缺橙色圆点，也影响搜索/路径起点选择。

修复策略
--------
- 以 semantic.rooms 为权威清单，确保每个封闭房间都有 F{floor}-TR-xxxx 节点。
- 若该房间在 geometry.doors 中已有门引用，则为这些门补建 TD 节点
  （原 pipeline 因房间未入 room_index 而跳过了这些门），并连接 TR↔TD、TD↔最近 TI。
- 若房间没有任何门引用（内部分隔套房），则把它挂到最近的一扇现有 TD 上，
  保证 validate「每个 TR 至少有一条 TD 边」且能从路网到达。
- 开放空间（corridor/lobby/activity/atrium/elevator_lobby/stair_lobby/staircase）
  默认不补 TR，它们由 TI 网络表示；如需补齐可传 --include-open。

用法
----
    python src/ensure_room_nodes.py [geojson_path] [--include-open]

默认原地修改 result/school_building_01_map_v9.geojson 并生成 .bak 备份。
"""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

BASE = Path(__file__).resolve().parent.parent
DEF_GEO = BASE / "result" / "school_building_01_map_v9.geojson"

OPEN_TYPES = {
    "corridor", "lobby", "activity", "atrium",
    "elevator_lobby", "stair_lobby", "staircase",
}

PUBLIC_TYPES = {"corridor", "lobby", "activity", "atrium",
                "elevator_lobby", "stair_lobby", "staircase",
                "toilet"}
NON_ACCESSIBLE_TYPES: Set[str] = set()  # 默认都可达


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_geojson(path: Path, geo: dict) -> None:
    path.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")


def _max_seq(ids: List[str], prefix: str) -> int:
    best = 0
    for s in ids:
        if not s.startswith(prefix + "-"):
            continue
        try:
            best = max(best, int(s.rsplit("-", 1)[-1]))
        except ValueError:
            pass
    return best


def _next_seq(existing: List[str], prefix: str, start: int = 1) -> int:
    return max(_max_seq(existing, prefix), start - 1) + 1


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def ensure_floor(
    floor: dict,
    floor_no: str,
    include_open: bool = False,
) -> Dict[str, int]:
    """补全一层楼的缺失房间节点。返回统计信息。"""
    topo = floor.setdefault("topology", {"nodes": [], "edges": []})
    nodes: List[dict] = topo["nodes"]
    edges: List[dict] = topo["edges"]

    sem_rooms = {r.get("id"): r for r in floor.get("semantic", {}).get("rooms", [])}
    geom_rooms = {r.get("id"): r for r in floor.get("geometry", {}).get("rooms", [])}

    existing_ids = {n.get("id") for n in nodes}
    existing_tr_by_room: Dict[str, dict] = {
        n.get("roomId"): n for n in nodes if n.get("type") == "room"
    }
    tds = [n for n in nodes if n.get("type") == "doorway"]
    tis = [n for n in nodes if n.get("type") == "intersection"]

    # 下一可用编号
    tr_seq = _next_seq(list(existing_ids), f"F{floor_no}-TR")
    td_seq = _next_seq(list(existing_ids), f"F{floor_no}-TD")
    te_seq = _next_seq(list(existing_ids), f"F{floor_no}-TE")

    def new_tr_id() -> str:
        nonlocal tr_seq
        rid = f"F{floor_no}-TR-{tr_seq:04d}"
        tr_seq += 1
        return rid

    def new_td_id() -> str:
        nonlocal td_seq
        rid = f"F{floor_no}-TD-{td_seq:04d}"
        td_seq += 1
        return rid

    def new_te_id() -> str:
        nonlocal te_seq
        rid = f"F{floor_no}-TE-{te_seq:04d}"
        te_seq += 1
        return rid

    def add_edge(a: str, b: str, dist: float, **kwargs) -> None:
        edges.append({
            "id": new_te_id(),
            "from": a, "to": b,
            "distance": round(dist, 2),
            "accessibilityLevel": kwargs.get("accessibilityLevel", 0),
            "riskLevel": kwargs.get("riskLevel", 0.5),
            "blindAccessible": kwargs.get("blindAccessible", True),
            "wheelchairAccessible": kwargs.get("wheelchairAccessible", True),
            "crossFloor": False,
            "type": kwargs.get("type"),
        })

    def nearest_ti(coord: Tuple[float, float]) -> Optional[Tuple[str, Tuple[float, float], float]]:
        best = None
        for ti in tis:
            tc = tuple(ti["coordinates"])
            d = _dist(coord, tc)
            if best is None or d < best[2]:
                best = (ti["id"], tc, d)
        return best

    def nearest_td(coord: Tuple[float, float]) -> Optional[Tuple[str, Tuple[float, float], float]]:
        best = None
        for td in tds:
            tc = tuple(td["coordinates"])
            d = _dist(coord, tc)
            if best is None or d < best[2]:
                best = (td["id"], tc, d)
        return best

    created_tr = 0
    created_td = 0
    created_edges = 0
    # 避免为同一扇 geometry door 重复建 TD
    td_by_geo_door: Dict[str, str] = {}

    for rid, sroom in sem_rooms.items():
        rtype = sroom.get("type")
        if not include_open and rtype in OPEN_TYPES:
            continue
        if rid in existing_tr_by_room:
            continue
        geom = geom_rooms.get(rid)
        centroid = sroom.get("centroid") or (
            (geom.get("properties") or {}).get("centroid") if geom else None
        )
        if not centroid or len(centroid) < 2:
            continue
        coord = (float(centroid[0]), float(centroid[1]))

        tr_id = new_tr_id()
        nodes.append({
            "id": tr_id,
            "type": "room",
            "roomType": rtype,
            "roomId": rid,
            "label": sroom.get("label", ""),
            "coordinates": [round(coord[0], 6), round(coord[1], 6)],
            "public": sroom.get("public", rtype in PUBLIC_TYPES),
            "accessible": sroom.get("accessible", rtype not in NON_ACCESSIBLE_TYPES),
            "riskLevel": 0.5,
        })
        existing_ids.add(tr_id)
        created_tr += 1

        connected = False

        # 1) 找到所有引用该房间的 geometry door
        for gd in floor.get("geometry", {}).get("doors", []):
            gprops = gd.get("properties") or {}
            if rid not in (gprops.get("rooms") or []):
                continue
            gid = gd.get("id")
            gcoord = gd.get("geometry", {}).get("coordinates")
            if not gcoord or len(gcoord) < 2:
                continue
            dcoord = (float(gcoord[0]), float(gcoord[1]))

            td_id = td_by_geo_door.get(gid)
            if td_id is None:
                td_id = new_td_id()
                kind = gprops.get("doorType", "swing")
                label = {"swing": "普通门", "fire": "防火门", "opening": "门洞"}.get(kind, "门")
                width_m = gprops.get("width_m")
                if width_m is None:
                    width_m = 1.0
                nodes.append({
                    "id": td_id,
                    "type": "doorway",
                    "label": label,
                    "doorType": kind,
                    "width_m": round(float(width_m), 3),
                    "coordinates": [round(dcoord[0], 6), round(dcoord[1], 6)],
                    "rooms": list(gprops.get("rooms") or []),
                    "openDirection": gprops.get("openDirection"),
                    "hingeSide": gprops.get("hingeSide"),
                })
                existing_ids.add(td_id)
                tds.append(nodes[-1])
                td_by_geo_door[gid] = td_id
                created_td += 1

                # TD ↔ 最近 TI，让门接入走廊骨架（同 pipeline 逻辑）
                ti = nearest_ti(dcoord)
                if ti and ti[2] < 55.0:
                    add_edge(td_id, ti[0], ti[2], riskLevel=0.5)
                    created_edges += 1

            # TR ↔ TD
            dtr = _dist(coord, dcoord)
            a_level = 2 if nodes[-1].get("doorType") == "fire" else 0
            r_level = 5 if nodes[-1].get("doorType") == "fire" else 0.5
            add_edge(tr_id, td_id, dtr,
                     accessibilityLevel=a_level, riskLevel=r_level,
                     type="room_door")
            created_edges += 1
            connected = True

        # 2) 无门的房间：挂到最近 TD（套房/内部分隔）
        if not connected:
            td = nearest_td(coord)
            if td and td[2] < 20.0:
                add_edge(tr_id, td[0], td[2],
                         accessibilityLevel=0, riskLevel=0.5,
                         type="room_door_fallback")
                created_edges += 1
                connected = True

        if not connected:
            # 兜底：连到最近 TI，避免孤立
            ti = nearest_ti(coord)
            if ti and ti[2] < 30.0:
                add_edge(tr_id, ti[0], ti[2],
                         accessibilityLevel=0, riskLevel=0.5,
                         type="room_corridor_fallback")
                created_edges += 1

    return {
        "created_tr": created_tr,
        "created_td": created_td,
        "created_edges": created_edges,
    }


def main():
    parser = argparse.ArgumentParser(description="为语义房间补齐拓扑 TR 节点")
    parser.add_argument("geojson", nargs="?", type=Path, default=DEF_GEO,
                        help="待处理的 GeoJSON 文件路径")
    parser.add_argument("--include-open", action="store_true",
                        help="同时 corridor/lobby/activity 等开放空间也补 TR")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅统计，不修改文件")
    args = parser.parse_args()

    geo = load_geojson(args.geojson)
    summary: List[Tuple[str, Dict[str, int]]] = []

    for fk in sorted(geo.get("floors", {}).keys()):
        stats = ensure_floor(geo["floors"][fk], fk, args.include_open)
        if any(stats.values()):
            summary.append((fk, stats))

    if args.dry_run:
        print("--dry-run 模式，仅统计：")
        for fk, st in summary:
            print(f"  F{fk}: 新增 TR={st['created_tr']} TD={st['created_td']} 边={st['created_edges']}")
        if not summary:
            print("无需补充的房间节点。")
        return 0

    if not summary:
        print("无需补充的房间节点。")
        return 0

    bak = args.geojson.with_suffix(args.geojson.suffix + ".bak")
    shutil.copy2(args.geojson, bak)
    save_geojson(args.geojson, geo)
    print(f"已备份：{bak}")
    print(f"已写入：{args.geojson}")
    print("补全详情：")
    for fk, st in summary:
        print(f"  F{fk}: 新增 TR={st['created_tr']} TD={st['created_td']} 边={st['created_edges']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
