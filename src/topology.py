# -*- coding: utf-8 -*-
"""
按 docs/03-地图构建指南.md 第五章规范构建室内导航拓扑图。

节点三类（5.1）：
  - intersection 走廊交叉口节点（每条走廊生成 1 个，位于其质心；可达任意房门）
  - doorway      门口节点（每个门洞一个，连接两侧空间）
  - facility     设施接入节点（楼梯口/电梯口/卫生间入口/建筑出入口），跨层建筑出入口为 facility_entrance

边属性（5.2）：
  distance, estimatedTime (v=0.8 m/s, 视障步速),
  accessibilityLevel (0 平直走廊, 2 含门槛/坡度, 999 含楼梯对视障禁用),
  riskLevel (0.5 普通走廊, 5 玻璃门, 10 楼梯口),
  walkable, wheelchairAccessible, blindAccessible

跨楼层边（5.3）：电梯 a=0, r=1；楼梯 a=999, r=10；distance=水平距离+楼层高差。
"""
import math
import collections

# 视障步速 0.8 m/s（指南 5.2）
BLIND_WALK_SPEED = 0.8
# 普通步行 1.2 m/s（房间-门距离等微路径）
NORMAL_WALK_SPEED = 1.2

# 公共/可达性配置（与 parse_cad_pdf 保持一致）
PUBLIC_TYPES = {"toilet", "staircase", "elevator_hall", "corridor", "lobby",
                "entrance", "accessible_entrance", "atrium"}
ACCESSIBLE_TYPES = {
    "classroom", "lab", "office", "meeting", "equipment", "storage",
    "library", "medical", "counseling", "activity", "reception",
    "corridor", "lobby", "entrance", "accessible_entrance",
    "elevator_hall", "atrium", "toilet",
}
NON_ACCESSIBLE_TYPES = {"staircase", "shaft"}
INDEPENDENT_ENTRANCE_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance",
    "staircase", "elevator_hall", "atrium",
}

# 节点 ID 命名规范（与 v7 对齐：N{floor}-{kind}{idx}）
def _nid(floor, kind, idx):
    return f"N{floor}-{kind}{idx:03d}"


def _eid(floor, from_id, to_id):
    return f"E{floor}-{from_id}-{to_id}"


def _to_xy(coord):
    if coord is None:
        return (0.0, 0.0)
    if isinstance(coord, (list, tuple)):
        return (float(coord[0]), float(coord[1]))
    return (float(coord[0]), float(coord[1]))


def _dist(a, b):
    ax, ay = _to_xy(a)
    bx, by = _to_xy(b)
    return math.hypot(ax - bx, ay - by)


def build_floor_topology(floor_no, rooms, doors, stairs, elevators,
                         corridor_adjacency=None, extra_nodes=None):
    """
    构建单层拓扑图节点与边。

    rooms: [{id, label, roomType, centroid_m, polygon_pt?}, ...]
    doors: [{center_m, axis, kind, width_pt, rooms[], ...}]  (parsed 后)
    stairs: [{id, label, centroid_m, properties: {label}}]
    elevators: [{id, label, centroid_m, properties: {label}}]
    extra_nodes: [{type:'facility_entrance', label, coordinates, rooms?: []}, ...]
        未匹配到房间多边形但语义上属于公共空间（门厅/出入口/合班教室等）的标签，
        直接作为 facility_entrance 节点接入拓扑图。
    """
    nodes = []
    edges = []
    # 索引
    room_by_id = {r["id"]: r for r in rooms}
    door_idx = []  # 顺序生成 doorway 节点 ID
    used_room_ids = {r["id"] for r in rooms}

    # ---------- 房间节点（place，指南 5.1 没单列；但所有路径都需经过房间质心） ----------
    for idx, r in enumerate(rooms):
        nodes.append({
            "id": _nid(floor_no, "R", idx + 1),
            "type": "room",
            "roomType": r["roomType"],
            "roomId": r["id"],
            "label": r["label"],
            "coordinates": list(_to_xy(r["centroid_m"])),
            "public": r["roomType"] in PUBLIC_TYPES,
            "accessible": r["roomType"] not in NON_ACCESSIBLE_TYPES,
        })

    # ---------- 门口节点（doorway） ----------
    door_node_ids = []
    for i, dr in enumerate(doors):
        nid = _nid(floor_no, "D", i + 1)
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "门", "fire": "防火门", "opening": "通道"}[kind],
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * (1.0 / 18.896), 3),
            "coordinates": list(_to_xy(dr.get("center_m"))),
            "rooms": dr.get("rooms", []),
        })

    # ---------- 走廊交叉口节点（intersection） ----------
    # 每条走廊生成 1 个交叉口节点（质心位置），用于路径图骨架
    corridors = [r for r in rooms if r["roomType"] == "corridor"]
    cor_node_ids = {}
    for idx, c in enumerate(corridors):
        nid = _nid(floor_no, "I", idx + 1)
        cor_node_ids[c["id"]] = nid
        nodes.append({
            "id": nid,
            "type": "intersection",
            "roomId": c["id"],
            "label": c["label"],
            "coordinates": list(_to_xy(c["centroid_m"])),
        })

    # ---------- 设施接入节点（facility） ----------
    for i, s in enumerate(stairs):
        nodes.append({
            "id": _nid(floor_no, "ST", i + 1),
            "type": "facility",
            "facilityType": "staircase",
            "label": s["properties"].get("label", f"楼梯{floor_no}F-{i + 1}"),
            "coordinates": list(_to_xy(s["properties"]["centroid"])),
            "blindAccessible": False,
            "wheelchairAccessible": False,
        })
    for i, e in enumerate(elevators):
        nodes.append({
            "id": _nid(floor_no, "EL", i + 1),
            "type": "facility",
            "facilityType": "elevator",
            "label": e["properties"].get("label", f"电梯{floor_no}F-{i + 1}"),
            "coordinates": list(_to_xy(e["properties"]["centroid"])),
            "blindAccessible": True,
            "wheelchairAccessible": True,
        })

    # ---------- 设施接入节点：室外出入口（无房间多边形匹配的公共空间标签） ----------
    if extra_nodes:
        for i, en in enumerate(extra_nodes):
            nodes.append({
                "id": _nid(floor_no, "EN", i + 1),
                "type": "facility_entrance",
                "facilityType": en.get("facilityType", "entrance"),
                "label": en["label"],
                "coordinates": list(_to_xy(en["coordinates"])),
                "blindAccessible": True,
                "wheelchairAccessible": True,
            })

    # ---------- 边构建 ----------
    room_index = {r["id"]: idx for idx, r in enumerate(rooms)}

    def add_edge(frm, to, distance, a_level=0, r_level=0.5,
                 wheel=True, blind=True):
        edges.append({
            "id": _eid(floor_no, frm, to),
            "from": frm,
            "to": to,
            "distance": round(float(distance), 2),
            "estimatedTime": round(float(distance) / BLIND_WALK_SPEED, 1),
            "accessibilityLevel": a_level,
            "riskLevel": r_level,
            "walkable": True,
            "wheelchairAccessible": wheel,
            "blindAccessible": blind,
        })

    # 1) doorway <-> 所属房间质心（房间内移动）
    for i, dr in enumerate(doors):
        dnid = door_node_ids[i]
        center = _to_xy(dr.get("center_m"))
        for rid in dr.get("rooms", []):
            idx = room_index.get(rid)
            if idx is None:
                continue
            r = rooms[idx]
            d = _dist(center, r["centroid_m"])
            rnid = _nid(floor_no, "R", idx + 1)
            add_edge(rnid, dnid, d, a_level=2 if dr.get("kind") == "fire" else 0,
                     r_level=(5 if dr.get("kind") == "fire" else 0.5))

    # 2) doorway <-> 走廊交叉口（房间-走廊移动）
    if cor_node_ids:
        for i, dr in enumerate(doors):
            dnid = door_node_ids[i]
            center = _to_xy(dr.get("center_m"))
            best_cor_id, best_d = None, float("inf")
            for cid, cnid in cor_node_ids.items():
                c = room_by_id[cid]
                d = _dist(center, c["centroid_m"])
                if d < best_d:
                    best_d = d
                    best_cor_id = cid
            if best_cor_id:
                cnid = cor_node_ids[best_cor_id]
                add_edge(cnid, dnid, best_d)

    # 3) facility <-> 最近的 doorway（设施接入：楼梯/电梯口连最近的门）
    all_doorways = [(door_node_ids[i], _to_xy(doors[i].get("center_m")))
                    for i in range(len(doors))]
    facility_nodes = [n for n in nodes if n["type"] == "facility"]
    for fn in facility_nodes:
        if not all_doorways:
            break
        best = min(all_doorways,
                   key=lambda kv: _dist(kv[1], fn["coordinates"]))
        best_dnid, best_d = best[0], _dist(best[1], fn["coordinates"])
        a_level = 999 if fn["facilityType"] == "staircase" else 0
        r_level = 10 if fn["facilityType"] == "staircase" else 1
        wheel = fn["facilityType"] != "staircase"
        blind = fn["facilityType"] != "staircase"
        add_edge(fn["id"], best_dnid, best_d, a_level=a_level,
                 r_level=r_level, wheel=wheel, blind=blind)

    # 4) intersection <-> intersection（走廊骨架：近距离走廊直连）
    cor_list = list(cor_node_ids.items())
    for i in range(len(cor_list)):
        for j in range(i + 1, len(cor_list)):
            cid_i, nid_i = cor_list[i]
            cid_j, nid_j = cor_list[j]
            ci = _to_xy(room_by_id[cid_i]["centroid_m"])
            cj = _to_xy(room_by_id[cid_j]["centroid_m"])
            d = _dist(ci, cj)
            if d > 30:  # 远距离走廊不直连，靠房门串接
                continue
            add_edge(nid_i, nid_j, d)

    # 5) facility_entrance <-> 最近的 doorway（室外出入口接入最近的门洞）
    if extra_nodes and all_doorways:
        ent_nodes = [n for n in nodes if n["type"] == "facility_entrance"]
        for en in ent_nodes:
            best = min(all_doorways,
                       key=lambda kv: _dist(kv[1], en["coordinates"]))
            best_dnid, best_d = best[0], _dist(best[1], en["coordinates"])
            add_edge(en["id"], best_dnid, best_d, a_level=0, r_level=5)

    return {"nodes": nodes, "edges": edges}


def build_cross_floor_edges(f1, f2, floor_height_m=4.2):
    """
    跨楼层边：F1<->F2 之间通过同名楼梯/电梯配对（按米制中心距离 <2.5m 视为同一井道）。
    距离 = 水平距离 + 楼层高差；楼梯对视障禁用，电梯无障碍优先。
    """
    edges = []

    def center_m(feat):
        return feat["properties"]["centroid"]

    for kind, key, prefix, blind_ok, t_cross in (
            ("staircase", "stairs", "CF-ST", False, 60.0),
            ("elevator", "elevators", "CF-EL", True, 15.0)):
        for i, s1 in enumerate(f1.get(key, [])):
            c1 = center_m(s1)
            best, best_d = None, 2.5
            for j, s2 in enumerate(f2.get(key, [])):
                c2 = center_m(s2)
                d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                if d < best_d:
                    best, best_d = j, d
            if best is not None:
                nid_src = f"N1-{('ST' if kind == 'staircase' else 'EL')}{i + 1:03d}"
                nid_dst = f"N2-{('ST' if kind == 'staircase' else 'EL')}{best + 1:03d}"
                edges.append({
                    "id": f"{prefix}-{i + 1:03d}",
                    "from": nid_src,
                    "to": nid_dst,
                    "fromFloor": 1,
                    "toFloor": 2,
                    "type": kind,
                    "distance": floor_height_m,
                    "estimatedTime": t_cross,
                    "accessibilityLevel": 999 if kind == "staircase" else 0,
                    "riskLevel": 10 if kind == "staircase" else 1,
                    "walkable": True,
                    "wheelchairAccessible": blind_ok,
                    "blindAccessible": blind_ok,
                })
    return edges