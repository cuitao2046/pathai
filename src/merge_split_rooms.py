#!/usr/bin/env python3
"""合并因 CAD 房间多边形过切分而产生的「同标签、近距离、共享门」房间节点。

背景
----
parse_cad_pdf.py 按多边形提取房间时，偶尔会把同一物理房间切成多个 room 节点
（典型：长向大教室、办公区被内墙/柱网切成 2~3 块）。后果：
- 普通门只挂到其中一块，另一块只能走防火门；
- 违反「房间到房间优先走普通门」的规则。

合并条件（同时满足）
--------------------
1. 同一 floor 内；
2. label 相同；
3. 中心距 <= max_dist 米（默认 8.0），或者共享至少一扇门；
4. 两节点均通过门与公共空间连通（避免把两个独立卫生间强行合并）。
   这里简化为：是封闭房间（roomType 不是 corridor/lobby/activity/atrium）。

合并策略
--------
- 保留「连接门数量最多」的节点作为 keeper；数量相同时保留 roomId 最小者。
- 将其余节点重定向到 keeper：把边 from/to 改为 keeper，并删除重复边（保留距离短的）。
- 更新 door.rooms 中旧 roomId 为 keeper.roomId。
- 删除被合并的 room 节点。

用法
----
    python src/merge_split_rooms.py [geojson_path] [--max-dist 8.0]

默认处理 result/school_building_01_map_v9.geojson，原地修改并生成
result/school_building_01_map_v9.geojson.bak 备份。
"""
import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

BASE = Path(__file__).resolve().parent.parent
DEF_GEO = BASE / "result" / "school_building_01_map_v9.geojson"


def load_geojson(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_geojson(path: Path, geo: dict) -> None:
    path.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")


def room_door_map(topology: dict) -> Tuple[Dict[str, Set[str]], Dict[str, dict]]:
    """返回 room_id -> set(door_id) 与 node_id -> node 两个字典。"""
    nodes = {n["id"]: n for n in topology.get("nodes", [])}
    rd: Dict[str, Set[str]] = {}
    for e in topology.get("edges", []):
        a, b = e["from"], e["to"]
        ta = nodes.get(a, {}).get("type")
        tb = nodes.get(b, {}).get("type")
        if ta == "room" and tb == "doorway":
            rd.setdefault(a, set()).add(b)
        elif tb == "room" and ta == "doorway":
            rd.setdefault(b, set()).add(a)
    return rd, nodes


def distance(a: dict, b: dict) -> float:
    ax, ay = a.get("coordinates", [0, 0])
    bx, by = b.get("coordinates", [0, 0])
    return math.hypot(ax - bx, ay - by)


def find_merge_groups(rooms: List[dict], room_doors: Dict[str, Set[str]], max_dist: float) -> List[Set[str]]:
    """按合并条件找出需合并的 room_id 组。"""
    # 先按距离/共享门建邻接关系
    adj: Dict[str, Set[str]] = {r["id"]: set() for r in rooms}
    for i, a in enumerate(rooms):
        for b in rooms[i + 1 :]:
            aid, bid = a["id"], b["id"]
            d = distance(a, b)
            shared = (room_doors.get(aid, set()) & room_doors.get(bid, set()))
            if d <= max_dist or shared:
                adj[aid].add(bid)
                adj[bid].add(aid)
    # 求连通分量
    seen = set()
    groups: List[Set[str]] = []
    for r in rooms:
        rid = r["id"]
        if rid in seen:
            continue
        stack = [rid]
        comp = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            comp.add(cur)
            stack.extend(adj[cur] - seen)
        if len(comp) > 1:
            groups.append(comp)
    return groups


def choose_keeper(rooms: List[dict], room_doors: Dict[str, Set[str]]) -> dict:
    """优先保留 roomId 字典序最小的房间（语义上更“正”的编号）；
    相同时选门最多的，保证功能不丢失。"""
    def key(r: dict) -> Tuple[str, int, str]:
        return (
            r.get("roomId") or r["id"],             # roomId 小者优先
            -len(room_doors.get(r["id"], set())),  # 门多者优先
            r["id"],
        )
    return min(rooms, key=key)


def merge_group(
    floor: dict,
    group_ids: Set[str],
    nodes: Dict[str, dict],
    room_doors: Dict[str, Set[str]],
) -> Tuple[int, int]:
    """在 floor topology 内合并指定 room 组。返回 (removed_nodes, removed_edges)。"""
    topo = floor["topology"]
    group_rooms = [nodes[rid] for rid in group_ids]
    keeper = choose_keeper(group_rooms, room_doors)
    keeper_id = keeper["id"]
    keeper_room_id = keeper.get("roomId") or keeper_id

    removed_ids = group_ids - {keeper_id}
    removed_nodes = len(removed_ids)

    # 重定向边
    new_edges = []
    removed_edges = 0
    seen_edges: Set[Tuple[str, str]] = set()  # 无序对，保证唯一
    edge_key = lambda a, b: tuple(sorted([a, b]))

    for e in topo["edges"]:
        a, b = e["from"], e["to"]
        if a in removed_ids:
            a = keeper_id
        if b in removed_ids:
            b = keeper_id
        if a == b:
            # 自环（合并后两端变成同一个节点）直接删除
            removed_edges += 1
            continue
        ek = edge_key(a, b)
        if ek in seen_edges:
            # 重复边：保留距离短的
            removed_edges += 1
            # 找到已存在的边并更新为更短的距离
            for ex in new_edges:
                if edge_key(ex["from"], ex["to"]) == ek:
                    if e.get("distance", float("inf")) < ex.get("distance", float("inf")):
                        ex["from"], ex["to"] = a, b
                        for k in e:
                            if k not in ("from", "to"):
                                ex[k] = e[k]
                    break
            continue
        seen_edges.add(ek)
        new_edge = dict(e)
        new_edge["from"], new_edge["to"] = a, b
        new_edges.append(new_edge)

    # 更新 door.rooms 中旧 roomId 为 keeper.roomId
    old_room_ids = {nodes[rid].get("roomId") or rid for rid in removed_ids}
    for n in topo["nodes"]:
        if n.get("type") == "doorway" and n.get("rooms"):
            n["rooms"] = [keeper_room_id if r in old_room_ids else r for r in n["rooms"]]
            # 去重并保持顺序
            seen = set()
            uniq = []
            for r in n["rooms"]:
                if r not in seen:
                    seen.add(r)
                    uniq.append(r)
            n["rooms"] = uniq

    # 删除被合并的 room 节点
    topo["nodes"] = [n for n in topo["nodes"] if n["id"] not in removed_ids]
    topo["edges"] = new_edges

    # 同步 geometry/semantic 中的房间引用（如果存在 roomId）
    # 仅处理 geometry.rooms 与 semantic.rooms 的 roomId 替换
    old_to_new = {old: keeper_room_id for old in old_room_ids}
    for sec in ("geometry", "semantic"):
        sec_data = floor.get(sec) or {}
        for r in sec_data.get("rooms", []):
            props = r.get("properties") or r
            if props.get("roomId") in old_to_new:
                props["roomId"] = old_to_new[props["roomId"]]

    return removed_nodes, removed_edges


def process_floor(floor: dict, max_dist: float) -> List[Set[str]]:
    topo = floor["topology"]
    room_doors, nodes = room_door_map(topo)
    rooms = [n for n in topo["nodes"] if n.get("type") == "room"]
    # 按相同 label 分组，避免把不同房间但同标签（如多个卫生间）合并
    by_label: Dict[str, List[dict]] = {}
    for r in rooms:
        label = r.get("label") or ""
        if not label:
            continue  # 空标签房间不参与合并
        by_label.setdefault(label, []).append(r)

    all_groups: List[Set[str]] = []
    for label, rs in by_label.items():
        groups = find_merge_groups(rs, room_doors, max_dist)
        all_groups.extend(groups)
    for g in all_groups:
        merge_group(floor, g, nodes, room_doors)
    return all_groups


def main():
    parser = argparse.ArgumentParser(description="合并过切分的同标签近距离房间节点")
    parser.add_argument("geojson", nargs="?", type=Path, default=DEF_GEO,
                        help="待处理的 GeoJSON 文件路径")
    parser.add_argument("--max-dist", type=float, default=8.0,
                        help="中心距阈值（米），小于等于此值或共享门的同标签房间会合并")
    parser.add_argument("--dry-run", action="store_true",
                        help="只输出将要合并的组，不修改文件")
    args = parser.parse_args()

    geo = load_geojson(args.geojson)
    summary: List[str] = []

    for fk in sorted(geo["floors"].keys()):
        floor = geo["floors"][fk]
        groups = process_floor(floor, args.max_dist)
        if groups:
            summary.append(f"  F{fk}: 合并 {len(groups)} 组 -> "
                             + ", ".join(f"{{{','.join(sorted(g))}}}" for g in groups))

    if args.dry_run:
        print("--dry-run 模式，仅列出合并组：")
        print("\n".join(summary) if summary else "未发现需合并的房间分组。")
        return 0

    if not summary:
        print("未发现需合并的房间分组。")
        return 0

    # 备份并写入
    bak = args.geojson.with_suffix(args.geojson.suffix + ".bak")
    shutil.copy2(args.geojson, bak)
    save_geojson(args.geojson, geo)
    print(f"已备份：{bak}")
    print(f"已写入：{args.geojson}")
    print("合并详情：")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
