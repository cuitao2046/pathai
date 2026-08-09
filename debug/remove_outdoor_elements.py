# -*- coding: utf-8 -*-
"""需求⑨：按用户提供的截图列表剔除户外元素（不进拓扑、不参与导航路由）。

用户列表（严格一一对应）：
- F1 图1: F1-RM-0007 传达室（独立建筑）
- F1 图2: F1-RM-0034 室外疏散楼梯
- F1 图3: F1-RM-0072 楼梯间8
- F2 图1: F2-RM-0016 室外楼梯（非疏散）

删除内容：
1. geometry.rooms 中的房间多边形（含其 label/语义）
2. topology.nodes 中对应 TR 节点（roomId 匹配）
3. 依附于这些房间的 TD（doorway，其 rooms 字段仅含被删房间）与其 TR↔TD 边
4. 依附的 TF 设施（楼梯设施节点：F1-TF-0010 服务于 RM-0034、F1-TF-0008 服务于 RM-0072、
   F2-TF-0003 服务于 F2-RM-0016）——仅当设施不参与跨层边且无其他房间引用
5. 相关边（TR↔TD、TD↔TI、TR↔TF、TD 悬空边）
6. 跨层边若引用被删设施则删除（楼梯间8/室外疏散楼梯/室外楼梯均无跨层边，预期不触发）
"""
import json
import math
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
GEO = BASE / "result" / "school_building_01_map_v9.geojson"
BACKUP = BASE / "result" / "school_building_01_map_v9_before_outdoor_remove.geojson"

# 用户列表：(floor, room_id, 说明)
OUTDOOR_ROOMS = [
    ("1", "F1-RM-0007", "传达室"),
    ("1", "F1-RM-0034", "室外疏散楼梯"),
    ("1", "F1-RM-0072", "楼梯间8"),
    ("2", "F2-RM-0016", "室外楼梯（非疏散）"),
]

# 依附的楼梯设施（坐标与被删房间重合的楼梯设施节点，随房间删除）：
# F1-TF-0008 II-B3-03#ST = 楼梯间8(RM-0072) 的楼梯
# F1-TF-0010 II-B4-02#ST = 室外疏散楼梯(RM-0034) 的楼梯
# 注意：F2-TF-0003 是室内跨层楼梯(II-B1-03#ST，被 FX-XE-0003 引用)，不删。
OUTDOOR_FACILITIES = {
    "1": {"F1-TF-0008", "F1-TF-0010"},
}


def main():
    shutil.copy(GEO, BACKUP)
    geo = json.loads(GEO.read_text(encoding="utf-8"))

    # 1) 收集每层被删房间 -> 依附的 TF 设施
    floor_rooms = {}
    for fk, rid, note in OUTDOOR_ROOMS:
        floor_rooms.setdefault(fk, []).append(rid)

    removed_facilities = {}  # fk -> set of facility ids
    removed_doors = {}       # fk -> set of doorway ids
    for fk, rids in floor_rooms.items():
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        room_node_ids = {n["id"] for n in nodes
                         if n["type"] == "room" and n.get("roomId") in rids}

        # 2) 依附的 TD：rooms 字段 ⊆ 被删房间（或 roomId 指向被删房间的边另一端）
        hit_door_ids = set()
        for e in edges:
            a, b = e.get("from"), e.get("to")
            na, nb = nmap.get(a), nmap.get(b)
            if not na or not nb:
                continue
            if na["type"] == "room" and na.get("roomId") in rids and nb["type"] == "doorway":
                hit_door_ids.add(nb["id"])
            if nb["type"] == "room" and nb.get("roomId") in rids and na["type"] == "doorway":
                hit_door_ids.add(na["id"])
        # 仅删「不再服务任何保留房间」的门
        keep_doors = set()
        for did in hit_door_ids:
            dn = nmap.get(did)
            if not dn:
                continue
            serv = set(dn.get("rooms") or [])
            # 若门还服务保留房间（roomId 不在被删列表），保留
            if serv - set(rids):
                keep_doors.add(did)
        doomed_doors = hit_door_ids - keep_doors
        removed_doors[fk] = doomed_doors

        # 3) 依附的 TF：贴墙或直接引用（房间 TR 的边连到 facility），
        #    以及用户列表显式指定的户外楼梯设施（坐标与被删房间重合）
        hit_tf_ids = set(OUTDOOR_FACILITIES.get(fk, set()))
        for e in edges:
            a, b = e.get("from"), e.get("to")
            na, nb = nmap.get(a), nmap.get(b)
            if not na or not nb:
                continue
            if na["type"] == "room" and na.get("roomId") in rids and nb["type"] == "facility":
                hit_tf_ids.add(nb["id"])
            if nb["type"] == "room" and nb.get("roomId") in rids and na["type"] == "facility":
                hit_tf_ids.add(na["id"])
        # 设施是否被跨层边引用或服务其他保留房间 → 保留
        cf_from = {e.get("from") for e in geo.get("crossFloorEdges", [])}
        cf_to = {e.get("to") for e in geo.get("crossFloorEdges", [])}
        keep_tf = set()
        for tid in hit_tf_ids:
            tn = nmap.get(tid)
            if not tn:
                continue
            # 检查是否服务其他保留房间（通过边或 rooms）
            served_others = False
            for e in edges:
                a, b = e.get("from"), e.get("to")
                na, nb = nmap.get(a), nmap.get(b)
                if not na or not nb:
                    continue
                if na["id"] == tid and nb["type"] == "room" and nb.get("roomId") not in rids:
                    served_others = True
                if nb["id"] == tid and na["type"] == "room" and na.get("roomId") not in rids:
                    served_others = True
            if served_others or tid in cf_from or tid in cf_to:
                keep_tf.add(tid)
        doomed_tf = hit_tf_ids - keep_tf
        removed_facilities[fk] = doomed_tf

    # 4) 跨层边清理（引用被删设施）
    doomed_cf = set()
    for ci, e in enumerate(geo.get("crossFloorEdges", [])):
        for fk, tfs in removed_facilities.items():
            if e.get("from") in tfs or e.get("to") in tfs:
                doomed_cf.add(ci)
    if doomed_cf:
        geo["crossFloorEdges"] = [e for i, e in enumerate(geo["crossFloorEdges"])
                                  if i not in doomed_cf]

    # 5) 逐层执行删除
    stats = {}
    for fk, rids in floor_rooms.items():
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        doomed_nodes = set()
        doomed_nodes |= {n["id"] for n in nodes
                         if n["type"] == "room" and n.get("roomId") in rids}
        doomed_nodes |= removed_doors.get(fk, set())
        doomed_nodes |= removed_facilities.get(fk, set())

        # 边：任一端点被删即删除
        kept_edges = [e for e in edges
                      if e.get("from") not in doomed_nodes and e.get("to") not in doomed_nodes]

        # 房间多边形移除
        kept_rooms = [r for r in fd["geometry"].get("rooms", []) if r["id"] not in rids]

        fd["topology"]["nodes"] = [n for n in nodes if n["id"] not in doomed_nodes]
        fd["topology"]["edges"] = kept_edges
        fd["geometry"]["rooms"] = kept_rooms

        stats[fk] = {
            "rooms": len(rids),
            "tr": sum(1 for n in nodes if n["type"] == "room" and n.get("roomId") in rids),
            "doors": len(removed_doors.get(fk, set())),
            "tfs": len(removed_facilities.get(fk, set())),
            "edges_removed": len(edges) - len(kept_edges),
        }

    GEO.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== 户外元素剔除完成 ===")
    for fk, s in stats.items():
        print(f"F{fk}: 房间={s['rooms']} TR={s['tr']} TD={s['doors']} TF={s['tfs']} 边删={s['edges_removed']}")
    print(f"跨层边删除={len(doomed_cf)}")


if __name__ == "__main__":
    main()
