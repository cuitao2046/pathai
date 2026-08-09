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
NON_ACCESSIBLE_TYPES = {"staircase", "infrastructure"}
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


def bridge_disconnected_components(
    floor_no,
    nodes,
    edges,
    min_island_nodes=5,
    max_bridge_dist_m=120.0,
    bridges_per_island=2,
):
    """
    同层断连补边：当存在较大「导航孤岛」（如被屋顶平台隔开的东翼）时，
    在孤岛与主分量的最近 intersection（走道）节点之间添加临时走廊边。

    边属性（视障不可用）：
      accessibilityLevel=999, blindAccessible=False,
      linkType=cross_wing_platform, riskLevel=8
    轮椅仍可用（平台可走）。

    不修改原有边，仅追加；无足够大的孤岛时返回原 edges。
    """
    if not nodes or not edges:
        return edges

    nmap = {n["id"]: n for n in nodes}
    adj = collections.defaultdict(set)
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in nmap and b in nmap:
            adj[a].add(b)
            adj[b].add(a)

    seen = set()
    comps = []
    for nid in nmap:
        if nid in seen:
            continue
        stack = [nid]
        seen.add(nid)
        comp = {nid}
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    comp.add(v)
                    stack.append(v)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    if len(comps) < 2:
        return edges

    main = comps[0]
    # 仅处理规模达到阈值的孤岛（避免给单个无门卫生间补边）
    islands = [c for c in comps[1:] if len(c) >= min_island_nodes]
    if not islands:
        return edges

    def ti_nodes(comp):
        out = []
        for nid in comp:
            n = nmap[nid]
            if n.get("type") == "intersection":
                out.append(n)
        # 无 TI 时退化为 doorway
        if not out:
            for nid in comp:
                n = nmap[nid]
                if n.get("type") == "doorway":
                    out.append(n)
        return out

    # 下一 TE 序号
    edge_seq = 0
    for e in edges:
        eid = e.get("id") or ""
        try:
            # F2-TE-0182
            edge_seq = max(edge_seq, int(eid.rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            pass

    main_ti = ti_nodes(main)
    if not main_ti:
        return edges

    new_edges = []
    for island in islands:
        isl_ti = ti_nodes(island)
        if not isl_ti:
            continue
        pairs = []
        for a in isl_ti:
            ax, ay = _to_xy(a.get("coordinates"))
            for b in main_ti:
                bx, by = _to_xy(b.get("coordinates"))
                d = math.hypot(ax - bx, ay - by)
                if d <= max_bridge_dist_m:
                    pairs.append((d, a["id"], b["id"]))
        pairs.sort()
        used_i, used_m = set(), set()
        added = 0
        for d, ai, bi in pairs:
            if ai in used_i:
                continue
            edge_seq += 1
            new_edges.append({
                "id": obj_id(_floor_tag(floor_no), OBJ_TYPE["topo_edge"], edge_seq),
                "from": ai,
                "to": bi,
                "distance": round(float(d), 2),
                "estimatedTime": round(float(d) / BLIND_WALK_SPEED, 1),
                "accessibilityLevel": 999,
                "riskLevel": 8,
                "walkable": True,
                "wheelchairAccessible": True,
                "blindAccessible": False,
                "linkType": "cross_wing_platform",
                "note": "临时跨翼连接（开放平台/屋面分隔区域），视障人士不可用",
            })
            used_i.add(ai)
            used_m.add(bi)
            added += 1
            if added >= bridges_per_island:
                break
        if added:
            print(f"[F{floor_no}] 跨翼补边: 孤岛{len(island)}节点 → 主分量 "
                  f"新增 {added} 条 (blindAccessible=false)")

    if new_edges:
        edges = list(edges) + new_edges
    return edges


def assign_node_risk_levels(nodes, rooms):
    """给拓扑节点逐个赋风险等级（指南 §6.3）。

    指南定义：楼梯口 r=10、玻璃门 r=5、自动门 r=3、普通走廊 r=0.5。
    玻璃门/自动门图纸不可判（列入 accessibility.surveyRequired 待现场补录），
    故此处只落实可从图纸推导的部分；另按视障场景补一条：
    外开门的门扇会扫入走廊、无法提前感知，风险高于普通门口，取 r=2。

    作为独立函数以便骨架拓扑与质心拓扑两条路径共用。
    """
    rtype_by_id = {r["id"]: r["roomType"] for r in rooms}
    for n in nodes:
        ntype = n.get("type")
        if ntype == "facility":
            r_lv = 10.0 if n.get("facilityType") == "staircase" else 1.0
        elif ntype == "facility_entrance":
            r_lv = 1.0
        elif ntype == "doorway":
            # 门口紧邻楼梯间 → 视为楼梯口，取最高风险
            if any(rtype_by_id.get(rid) == "staircase"
                   for rid in n.get("rooms", [])):
                r_lv = 10.0
            elif n.get("openDirection") == "outward":
                r_lv = 2.0
            else:
                r_lv = 1.0
        elif ntype == "room":
            r_lv = 10.0 if n.get("roomType") == "staircase" else 0.5
        else:                      # intersection（走廊/门厅/大厅等）
            r_lv = 0.5
        n["riskLevel"] = r_lv
    return nodes


def _merge_nearby_doors(doors, max_dist_m=0.8, coords=None):
    """合并坐标距 < max_dist_m 的门为单个 doorway 节点（同一开口的摆弧/防火/门洞）。

    同一物理开口常被识别为多条门记录（swing + fire + opening），几何中心重合；
    coords 可传入预先算好的合并依据坐标（如投影后的最终坐标），为 None 时退回用
    door 的 center_m。合并后 rooms 取并集、kind 取 fire 优先、width 取最大。
    返回合并后的门列表，次序按簇首排列。
    """
    if not doors:
        return []
    if coords is None:
        try:
            coords = [tuple(_to_xy(dr.get("center_m") or (0.0, 0.0))) for dr in doors]
        except Exception:
            coords = [tuple(dr.get("center_m") or (0.0, 0.0)) for dr in doors]
    used = [False] * len(doors)
    merged = []
    for i in range(len(doors)):
        if used[i]:
            continue
        ci = centers[i]
        cluster = [i]
        used[i] = True
        for j in range(i + 1, len(doors)):
            if used[j]:
                continue
            cj = centers[j]
            if math.hypot(ci[0] - cj[0], ci[1] - cj[1]) < max_dist_m:
                cluster.append(j)
                used[j] = True
        rooms_u = []
        for j in cluster:
            for rid in (doors[j].get("rooms") or []):
                if rid not in rooms_u:
                    rooms_u.append(rid)
        kinds = [doors[j].get("kind", "swing") for j in cluster]
        kind = "fire" if "fire" in kinds else (kinds[0] if kinds else "swing")
        width = max((doors[j].get("width_pt") or 0) for j in cluster)
        md = dict(doors[cluster[0]])
        md["center_m"] = list(ci)
        md["kind"] = kind
        md["width_pt"] = width
        md["rooms"] = rooms_u
        merged.append(md)
    return merged


def build_floor_topology(floor_no, rooms, doors, stairs, elevators,
                         corridor_adjacency=None, extra_nodes=None):
    """
    构建单层拓扑图节点与边。

    rooms: [{id, label, roomType, centroid_m, polygon_pt?}, ...]
    doors: [{center_m, axis, kind, width_pt, rooms[], ...}]  (parsed 后)
    stairs: [{id, label, centroid_m, properties: {label}}]
    elevators: [{id, label, centroid_m, properties: {label}}]
    extra_nodes: [{type:'facility_entrance', label, coordinates, rooms?: []}, ...]
        未匹配到房间多边形但语义上属于公共空间（门厅/出入口等）的标签；合班教室为封闭教室，不在此列，
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
    # 门不做合并（用户明确约定）：每扇门独立成 TD，禁止 _merge_nearby_doors 合并；
    # 跳过无房间归属的门（避免悬空 corridor-only 节点）
    td_doors = list(doors)
    door_node_ids = []  # 与 td_doors 对齐，被跳过的门记为 None
    for i, dr in enumerate(td_doors):
        has_room = any(rid in room_index for rid in dr.get("rooms", []))
        if not has_room:
            door_node_ids.append(None)
            continue
        nid = _nid(floor_no, OBJ_TYPE["topo_doorway"], len(door_node_ids) + 1)
        door_node_ids.append(nid)
        kind = dr.get("kind", "swing")
        nodes.append({
            "id": nid,
            "type": "doorway",
            "label": {"swing": "普通门", "fire": "防火门", "opening": "门洞"}[kind],
            "doorType": kind,
            "width_m": round(float(dr.get("width_pt", 0)) * 0.0529, 3),
            "coordinates": list(_to_xy(dr.get("center_m"))),
            "rooms": dr.get("rooms", []),
            # 指南 §3.2 开向：外开门（门扇扫入走廊）对视障用户风险更高
            "openDirection": dr.get("openDirection"),
            "hingeSide": dr.get("hingeSide"),
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
    for i, dr in enumerate(td_doors):
        dnid = door_node_ids[i]
        if dnid is None:
            continue
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

    # 2) doorway <-> 开放流通空间（走廊/门厅/前室）
    #    优先：门归属中已标明的开放空间；其次：距门口 ≤15m 的所有开放空间
    #   （旧逻辑只连最近一个，会导致两侧走廊无法经同一门洞互通）
    if cor_node_ids:
        for i, dr in enumerate(td_doors):
            dnid = door_node_ids[i]
            if dnid is None:
                continue
            center = _to_xy(dr.get("center_m"))
            linked = set()
            for rid in dr.get("rooms", []):
                if rid in cor_node_ids and rid not in linked:
                    c = room_by_id[rid]
                    d = _dist(center, c["centroid_m"])
                    add_edge(cor_node_ids[rid], dnid, d)
                    linked.add(rid)
            for cid, cnid in cor_node_ids.items():
                if cid in linked:
                    continue
                c = room_by_id[cid]
                d = _dist(center, c["centroid_m"])
                if d <= 15.0:
                    add_edge(cnid, dnid, d)
                    linked.add(cid)
            # 兜底：一个都没有时仍连最近开放空间
            if not linked:
                best_cor_id, best_d = None, float("inf")
                for cid, cnid in cor_node_ids.items():
                    c = room_by_id[cid]
                    d = _dist(center, c["centroid_m"])
                    if d < best_d:
                        best_d, best_cor_id = d, cid
                if best_cor_id:
                    add_edge(cor_node_ids[best_cor_id], dnid, best_d)

    # 3) facility <-> 最近的 doorway（设施接入：楼梯/电梯口连最近的门）
    #    需求⑲：电梯节点(TF)不得连防火门(fire)拓扑节点——电梯井道门已由
    #    电梯门(elevator door)元素承载，防火门属前室/房间隔断，不接入电梯。
    all_doorways = [(door_node_ids[i], _to_xy(td_doors[i].get("center_m")),
                     td_doors[i].get("kind"))
                    for i in range(len(td_doors)) if door_node_ids[i] is not None]
    facility_nodes = [n for n in nodes if n["type"] == "facility"]
    for fn in facility_nodes:
        if not all_doorways:
            break
        if fn["facilityType"] == "elevator":
            # 电梯：只允许连非 fire 门（需求⑲）；无候选则跳过（电梯门由后续步骤接入）
            _cands = [kv for kv in all_doorways if kv[2] != "fire"]
            if not _cands:
                continue
        else:
            _cands = all_doorways
        best = min(_cands,
                   key=lambda kv: _dist(kv[1], fn["coordinates"]))
        best_dnid, best_d = best[0], _dist(best[1], fn["coordinates"])
        a_level = 999 if fn["facilityType"] == "staircase" else 0
        r_level = 10 if fn["facilityType"] == "staircase" else 1
        wheel = fn["facilityType"] != "staircase"
        blind = fn["facilityType"] != "staircase"
        add_edge(fn["id"], best_dnid, best_d, a_level=a_level,
                 r_level=r_level, wheel=wheel, blind=blind)

    # 4) intersection <-> intersection（走廊骨架：近距离走廊直连）
    #    阈值 50m：教学楼翼展较大，质心距离常 >30m 但仍属同一流通网络
    cor_list = list(cor_node_ids.items())
    for i in range(len(cor_list)):
        for j in range(i + 1, len(cor_list)):
            cid_i, nid_i = cor_list[i]
            cid_j, nid_j = cor_list[j]
            ci = _to_xy(room_by_id[cid_i]["centroid_m"])
            cj = _to_xy(room_by_id[cid_j]["centroid_m"])
            d = _dist(ci, cj)
            if d > 50:
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

    # 同层大孤岛补边（如 F2 东翼被屋顶平台隔开）：视障不可用
    edges = bridge_disconnected_components(floor_no, nodes, edges)

    return {"nodes": nodes, "edges": edges}


def build_cross_floor_edges(f1, f2, floor_height_m=4.2):
    """
    T10: 跨楼层边配对。

    优先按设施编号（properties.code，如 II-B2-01#ST）配对；
    无编号时退化为中心距离 <2.5m。
    TF 编号约定：先楼梯(1..ns)后电梯(ns+1..)，与骨架/质心拓扑一致。
    """
    edges = []

    def center_m(feat):
        return feat["properties"]["centroid"]

    def code_of(feat):
        return (feat.get("properties") or {}).get("code") or None

    ns1 = len(f1.get("stairs", []))
    ns2 = len(f2.get("stairs", []))
    for kind, key, blind_ok, t_cross in (
            ("staircase", "stairs", False, 60.0),
            ("elevator", "elevators", True, 15.0)):
        feats1 = f1.get(key, []) or []
        feats2 = f2.get(key, []) or []
        codes2 = {code_of(s2): j for j, s2 in enumerate(feats2) if code_of(s2)}
        used2 = set()
        for i, s1 in enumerate(feats1):
            c1 = center_m(s1)
            code = code_of(s1)
            best = codes2.get(code) if code else None
            matched_by = "code" if best is not None else None
            if best is None:
                best_d = 2.5
                for j, s2 in enumerate(feats2):
                    if j in used2:
                        continue
                    # 已被编号占用的目标不参与几何配对
                    if code_of(s2) and code_of(s2) in codes2:
                        continue
                    c2 = center_m(s2)
                    d = math.hypot(c1[0] - c2[0], c1[1] - c2[1])
                    if d < best_d:
                        best, best_d = j, d
                if best is not None:
                    matched_by = "geometry"
            if best is None:
                continue
            used2.add(best)
            if kind == "staircase":
                nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], i + 1)
                nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], best + 1)
            else:
                nid_src = obj_id("F1", OBJ_TYPE["topo_facility"], ns1 + i + 1)
                nid_dst = obj_id("F2", OBJ_TYPE["topo_facility"], ns2 + best + 1)
            edges.append({
                "id": obj_id("FX", OBJ_TYPE["cross_edge"], len(edges) + 1),
                "code": code,
                "from": nid_src,
                "to": nid_dst,
                "fromFloor": 1,
                "toFloor": 2,
                "type": kind,
                "matchedBy": matched_by,
                "distance": floor_height_m,
                "estimatedTime": t_cross,
                "accessibilityLevel": 999 if kind == "staircase" else 0,
                "riskLevel": 10 if kind == "staircase" else 1,
                "walkable": True,
                "wheelchairAccessible": blind_ok,
                "blindAccessible": blind_ok,
            })
    return edges