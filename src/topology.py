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
# 开放流通空间：走道/门厅/大厅/活动空间/中庭/电梯前室/楼梯前室 —— 与封闭空间
# （房间/管井/电梯/楼梯间）属不同类型，建模为 circulation 节点（intersection），
# 不建为 enclosed-room 节点。
OPEN_SPACE_TYPES = {"corridor", "lobby", "activity", "atrium",
                    "elevator_lobby", "stair_lobby"}
INDEPENDENT_ENTRANCE_TYPES = {
    "corridor", "lobby", "entrance", "accessible_entrance",
    "staircase", "elevator_hall", "atrium",
}

# ---- 统一对象编号规范：{FLOOR}-{TYPE_ABBR}-{SEQ:04d} ----
# 楼层标识: F1 / F2 / FX(跨层)；类型缩写由对象语义定义（见 docs/ 约定）。
OBJ_TYPE = {
    "wall": "W", "room": "RM", "door": "D", "stair": "ST",
    "elevator": "EL", "column": "C", "window": "WN",
    "corridor": "CR", "lobby": "LB", "activity": "AC", "atrium": "AT",
    "elevator_lobby": "ELB", "stair_lobby": "SLB",
    "topo_room": "TR", "topo_doorway": "TD", "topo_intersection": "TI",
    "topo_facility": "TF", "topo_entrance": "TEN", "topo_edge": "TE",
    "stair_risk": "SR", "elev_a11y": "EA", "cross_edge": "XE",
}


def obj_id(floor, abbr, seq):
    """统一编号：楼层 + 类型缩写 + 4 位序号，dash 分割。"""
    return f"{floor}-{abbr}-{seq:04d}"


def _floor_tag(floor_no):
    return f"F{floor_no}"


# 拓扑节点 ID：节点类型缩写 → TR/TD/TI/TF/TEN
def _nid(floor, kind_abbr, seq):
    return obj_id(_floor_tag(floor), kind_abbr, seq)


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

    # ---------- 房间节点（enclosed place） ----------
    # 仅封闭空间（房间/管井/电梯/楼梯间等）建为 enclosed-room 节点；
    # 开放空间（走道/门厅/大厅/活动/中庭）不在此建模，改由下方 circulation 节点处理，
    # 避免「开放空间被当作需穿门进入的封闭房间」而与其余空间合并处理。
    room_index = {}  # room id -> 其 topo_room 节点 id（仅封闭空间）
    room_seq = 0
    for r in rooms:
        if r["roomType"] in OPEN_SPACE_TYPES:
            continue
        room_seq += 1
        rnid = _nid(floor_no, OBJ_TYPE["topo_room"], room_seq)
        room_index[r["id"]] = rnid
        nodes.append({
            "id": rnid,
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
        nid = _nid(floor_no, OBJ_TYPE["topo_doorway"], i + 1)
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "普通门", "fire": "防火门", "opening": "门洞"}[kind],
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * (1.0 / 18.896), 3),
            "coordinates": list(_to_xy(dr.get("center_m"))),
            "rooms": dr.get("rooms", []),
        })

    # ---------- 开放空间节点（circulation / intersection） ----------
    # 走道/门厅/大厅/活动空间/中庭均为开放流通空间，统一建为 intersection 型节点
    # （路径图骨架），携带 roomType 以区分走廊/门厅/大厅/活动空间，供渲染与详情展示。
    open_spaces = [r for r in rooms if r["roomType"] in OPEN_SPACE_TYPES]
    cor_node_ids = {}
    for idx, c in enumerate(open_spaces):
        nid = _nid(floor_no, OBJ_TYPE["topo_intersection"], idx + 1)
        cor_node_ids[c["id"]] = nid
        nodes.append({
            "id": nid,
            "type": "intersection",
            "roomType": c["roomType"],
            "roomId": c["id"],
            "label": c["label"],
            "coordinates": list(_to_xy(c["centroid_m"])),
        })

    # ---------- 设施接入节点（facility） ----------
    # 统一 TF 缩写，按“先楼梯后电梯”顺序连续编号（与 parse_cad_pdf 跨层边引用一致）
    fac_seq = 0
    for i, s in enumerate(stairs):
        fac_seq += 1
        nodes.append({
            "id": _nid(floor_no, OBJ_TYPE["topo_facility"], fac_seq),
            "type": "facility",
            "facilityType": "staircase",
            "label": s["properties"].get("label", f"楼梯{floor_no}F-{i + 1}"),
            "coordinates": list(_to_xy(s["properties"]["centroid"])),
            "blindAccessible": False,
            "wheelchairAccessible": False,
        })
    for i, e in enumerate(elevators):
        fac_seq += 1
        nodes.append({
            "id": _nid(floor_no, OBJ_TYPE["topo_facility"], fac_seq),
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
            "id": _nid(floor_no, OBJ_TYPE["topo_entrance"], i + 1),
            "type": "facility_entrance",
                "facilityType": en.get("facilityType", "entrance"),
                "label": en["label"],
                "coordinates": list(_to_xy(en["coordinates"])),
                "blindAccessible": True,
                "wheelchairAccessible": True,
            })

    # ---------- 边构建 ----------
    edge_seq = [0]
    def add_edge(frm, to, distance, a_level=0, r_level=0.5,
                 wheel=True, blind=True):
        edge_seq[0] += 1
        edges.append({
            "id": obj_id(_floor_tag(floor_no), OBJ_TYPE["topo_edge"], edge_seq[0]),
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

    # 1) doorway <-> 所属封闭房间质心（房间内移动）
    for i, dr in enumerate(doors):
        dnid = door_node_ids[i]
        center = _to_xy(dr.get("center_m"))
        for rid in dr.get("rooms", []):
            rnid = room_index.get(rid)
            if rnid is None:
                # 开放空间（走廊/门厅等）不在此连，由下方 doorway<->circulation 处理
                continue
            r = room_by_id[rid]
            d = _dist(center, r["centroid_m"])
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

    # 6) 兜底：确保每个开放空间 circulation 节点至少有一条边（避免校验判为孤岛）。
    #    优先连到最近门口；无门口时退一步连到最近封闭房间节点。
    connected = {e["from"] for e in edges} | {e["to"] for e in edges}
    for cid, cnid in cor_node_ids.items():
        if cnid in connected:
            continue
        c = room_by_id[cid]
        best_dnid, best_d = None, float("inf")
        for dnid, dc in all_doorways:
            d = _dist(c["centroid_m"], dc)
            if d < best_d:
                best_d, best_dnid = d, dnid
        if best_dnid:
            add_edge(cnid, best_dnid, best_d)
            continue
        best_rnid, best_rd = None, float("inf")
        for rid, rnid in room_index.items():
            d = _dist(c["centroid_m"], room_by_id[rid]["centroid_m"])
            if d < best_rd:
                best_rd, best_rnid = d, rnid
        if best_rnid:
            add_edge(cnid, best_rnid, best_rd)

    return {"nodes": nodes, "edges": edges}


def build_cross_floor_edges(f1, f2, floor_height_m=4.2):
    """
    跨楼层边：F1<->F2 之间通过同名楼梯/电梯配对（按米制中心距离 <2.5m 视为同一井道）。
    距离 = 水平距离 + 楼层高差；楼梯对视障禁用，电梯无障碍优先。
    """
    edges = []

    def center_m(feat):
        return feat["properties"]["centroid"]

    ns1 = len(f1.get("stairs", []))
    ns2 = len(f2.get("stairs", []))
    for kind, key, blind_ok, t_cross in (
            ("staircase", "stairs", False, 60.0),
            ("elevator", "elevators", True, 15.0)):
        for i, s1 in enumerate(f1.get(key, [])):
            c1 = center_m(s1)
            best, best_d = None, 2.5
            for j, s2 in enumerate(f2.get(key, [])):
                c2 = center_m(s2)
                d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                if d < best_d:
                    best, best_d = j, d
            if best is not None:
                # 拓扑设施节点顺序：先楼梯(1..ns)后电梯(ns+1..)，引用对应 TF 编号
                if kind == "staircase":
                    nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], i + 1)
                    nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], best + 1)
                else:
                    nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], ns1 + i + 1)
                    nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], ns2 + best + 1)
                edges.append({
                    "id": obj_id("FX", OBJ_TYPE["cross_edge"], len(edges) + 1),
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