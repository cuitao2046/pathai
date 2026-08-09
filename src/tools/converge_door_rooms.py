#!/usr/bin/env python3
"""清洗 geometry.doors / topology 门节点的 rooms 归属，使其只指向有效封闭房间。

问题来源
--------
1. parse_cad_pdf.py 的「两侧投票」会把门轴两侧空间（房间 + 走廊/门厅/前室）都写进
   rooms，导致大量普通门同时归属「封闭房间 + 开放空间」。
2. merge_split_rooms.py 合并同标签房间时，只更新了 topology 门节点的 rooms，未同步
   geometry.doors；且被合并消失的 roomId 仍残留在 door.rooms 中形成悬空引用。

门归属规则（普通门 / 普通摆弧门）
-------------------------------
- 若普通门两侧都是封闭房间，该门归属两个房间（两房间共用的门）；
- 仅当普通门连接「封闭房间 + 公共空间」时，普通门只归属该封闭房间（剔除公共空间）。
本脚本对全部 doorway 节点统一执行该原则：剔除开放空间类型、保留封闭房间（可多个）。

清洗规则（幂等）
--------------
对每个 geometry.door 与 topology doorway 节点的 rooms：
1. 删除「已不存在于房间节点集合」的 roomId（合并产生的悬空引用）；
2. 删除开放空间类型（corridor/lobby/activity/atrium/elevator_lobby/stair_lobby/staircase）；
3. 保留其余封闭房间；若某扇门同时归属多个封闭房间（套间直通门）则全部保留；
4. 若清洗后为空，则保留原始 rooms（走廊↔走廊的连通门等）；
5. 去重并保持顺序。

开放空间类型
------------
corridor, lobby, activity, atrium, elevator_lobby, stair_lobby, staircase

用法
----
    python src/converge_door_rooms.py [geojson_path] [--dry-run]
默认处理 result/school_building_01_map_v9.geojson，原地修改并生成 .bak 备份。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

BASE = Path(__file__).resolve().parent.parent.parent
DEF_GEO = BASE / "result" / "school_building_01_map_v9.geojson"

OPEN_TYPES: Set[str] = {
    "corridor", "lobby", "activity", "atrium",
    "elevator_lobby", "stair_lobby", "staircase",
}


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_geojson(path: Path, geo: dict) -> None:
    path.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_rooms(rooms: List[str], valid_room_ids: Set[str], rtype: Dict[str, str]) -> List[str]:
    """返回清洗后的 rooms 列表。"""
    out: List[str] = []
    for r in rooms:
        if r not in valid_room_ids:
            continue  # 规则1：剔除悬空引用
        if rtype.get(r) in OPEN_TYPES:
            continue  # 规则2：剔除开放空间
        out.append(r)
    # 规则3/4：失败时回退原始
    if not out:
        return list(rooms)
    # 规则5：去重保序
    seen: Set[str] = set()
    uniq: List[str] = []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def converge_floor(fd: dict) -> Dict[str, Tuple[int, int, int]]:
    # 用 roomId 与 id 双键建索引：部分房间（楼梯/设备用房等）的 semantic 条目
    # 可能没有 roomId 或二者不一致，而门节点 rooms 引用的是 id；只按 roomId 索引会
    # 漏判这些门，使其既没被清洗、也没被识别为开放空间（如楼梯间的普通门）。
    rtype: Dict[str, str] = {}
    valid_room_ids: Set[str] = set()
    for s in fd.get("semantic", {}).get("rooms", []):
        for key in (s.get("roomId"), s.get("id")):
            if key:
                rtype.setdefault(key, s.get("type"))
                valid_room_ids.add(key)
    stats: Dict[str, Tuple[int, int, int]] = {}

    # ---- 1) 先清洗 topology doorway 节点（权威来源） ----
    topo_door_rooms: Dict[str, List[str]] = {}
    for n in fd.get("topology", {}).get("nodes", []):
        if n.get("type") != "doorway":
            continue
        rooms = list(n.get("rooms") or [])
        if not rooms:
            continue
        new = clean_rooms(rooms, valid_room_ids, rtype)
        topo_door_rooms[n["id"]] = new
        if new != rooms:
            stats[n["id"]] = (len(rooms), len(new), len(rooms) - len(new))
            n["rooms"] = new

    # ---- 2) geometry.doors 以拓扑门为权威同步（merge 只更新了拓扑门，
    #         几何门仍是合并前的旧引用，必须同步，否则悬空） ----
    for d in fd.get("geometry", {}).get("doors", []):
        did = d.get("id") or (d.get("properties") or {}).get("id") or ""
        props = d.get("properties") or {}
        td_id = None
        if did.startswith("F1-D-"):
            td_id = did.replace("F1-D-", "F1-TD-")
        elif did.startswith("F2-D-"):
            td_id = did.replace("F2-D-", "F2-TD-")
        if td_id and td_id in topo_door_rooms:
            new = topo_door_rooms[td_id]
        else:
            rooms = list(props.get("rooms") or [])
            new = clean_rooms(rooms, valid_room_ids, rtype)
        before = len(props.get("rooms") or [])
        after = len(new)
        if new != list(props.get("rooms") or []):
            if did not in stats:
                stats[did] = (before, after, before - after)
            props["rooms"] = new
    return stats


def main():
    parser = argparse.ArgumentParser(description="清洗门 rooms 归属（剔除悬空引用与开放空间）")
    parser.add_argument("geojson", nargs="?", type=Path, default=DEF_GEO,
                        help="待处理的 GeoJSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只输出统计，不修改文件")
    args = parser.parse_args()

    geo = load_geojson(args.geojson)
    total = 0
    for fk, fd in geo.get("floors", {}).items():
        st = converge_floor(fd)
        if not st:
            print(f"Floor {fk}: 无需清洗")
            continue
        print(f"Floor {fk}: 清洗 {len(st)} 扇门")
        for did, (b, a, d) in sorted(st.items()):
            print(f"  {did}: {b} -> {a} (剔除 {d})")
        total += len(st)
    print(f"\n总计 {total} 扇门被清洗")

    if args.dry_run:
        print("(dry-run，未修改文件)")
        return

    bak = args.geojson.with_suffix(".geojson.bak")
    shutil.copy2(args.geojson, bak)
    save_geojson(args.geojson, geo)
    print(f"已保存，备份: {bak}")


if __name__ == "__main__":
    main()
