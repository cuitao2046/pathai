# -*- coding: utf-8 -*-
"""楼梯/电梯井识别：bbox 检测、电梯门识别、电梯门接入拓扑。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  STAIR_MAX_ASPECT(_CODED) / STAIR_AREA_* / STAIR_CLUSTER_GAP_M /
  ELEV_AREA_* / detect_stair_boxes / detect_elevator_boxes /
  detect_elevator_doors / attach_elevator_door_nodes

依赖 src/geometry 聚类与米制常量、src/parsing.pdf_layers 的图层名、
src/topology 的 obj_id/OBJ_TYPE（电梯门 TD 节点/边编号）。
"""
import math

from src.common.constants import BLIND_WALK_SPEED, PT_PER_M, SCALE
from src.geometry.clustering import _bbox_area_m2, _bbox_aspect, bbox_clusters
from src.geometry.geo_utils import pt2m
from src.parsing.pdf_layers import LAYER_ELEVATOR
from src.topology import OBJ_TYPE, obj_id

STAIR_MAX_ASPECT = 3.0       # 无编号且长宽比超此值 → 判为伪楼梯并剔除
STAIR_MAX_ASPECT_CODED = 5.0 # 有编号时仍拒绝极端细长条（防走廊+踏步被吸进）
STAIR_AREA_MIN_M2 = 3.0      # 楼梯 bbox 最小面积（覆盖踏步线缺失的碎片）
STAIR_AREA_MAX_M2 = 80.0     # 楼梯 bbox 最大面积
STAIR_CLUSTER_GAP_M = 1.5    # 楼梯聚类间距（米）——紧间距避免不同井道合并
ELEV_AREA_MIN_M2 = 1.0       # 电梯井最小面积
ELEV_AREA_MAX_M2 = 30.0      # 电梯井最大面积


def detect_stair_boxes(items_by_layer):
    """统一楼梯 bbox 检测：STAIR + A-FLOR-STRS 合并聚类 + 面积/长宽比过滤。

    返回 list[(x0,y0,x1,y1)]（pt）。早期注入 staircase room、门洞范围判定、
    最终 geometry 共用同一套结果，消除多路径参数不一致问题。
    """
    pts = []
    for lname in ("STAIR", "A-FLOR-STRS"):
        si = items_by_layer.get(lname, {"lines": [], "quads": []})
        for seg in si.get("lines", []):
            pts.append(seg)
        for q in si.get("quads", []):
            if len(q) >= 3:
                pts.append((q[0], q[2]))
    if not pts:
        return []
    boxes = bbox_clusters(pts, gap_pt=STAIR_CLUSTER_GAP_M * PT_PER_M)
    out = []
    for b in boxes:
        area = _bbox_area_m2(b)
        if area < STAIR_AREA_MIN_M2 or area > STAIR_AREA_MAX_M2:
            continue
        if _bbox_aspect(b) > STAIR_MAX_ASPECT:
            continue
        out.append(b)
    return out


def detect_elevator_boxes(items_by_layer):
    """电梯井 bbox：A-FLOR-EVTR 聚类 + 面积过滤。"""
    evtr = items_by_layer.get(LAYER_ELEVATOR, {"lines": [], "quads": [], "curves": []})
    pts = list(evtr.get("lines", []))
    for q in evtr.get("quads", []):
        if len(q) >= 2:
            pts.append(q[:2])
    if not pts:
        return []
    boxes = bbox_clusters(pts, gap_pt=2 * PT_PER_M)
    out = []
    for b in boxes:
        area = _bbox_area_m2(b)
        if ELEV_AREA_MIN_M2 <= area <= ELEV_AREA_MAX_M2:
            out.append(b)
    return out


def detect_elevator_doors(window_groups, evtr_boxes, floor_no,
                          gap_m=1.5, max_width_m=3.0):
    """把「电梯井外墙上的窗户」识别为电梯门元素（需求⑱）。

    现实建筑中电梯井道外墙的采光/检修窗即电梯门所在位置；此处将
    电梯 bbox buffer 范围内的 window 组识别为电梯门，归属对应电梯。

    参数：
      window_groups: parse_floor 的 window_groups（pt 坐标系，含 axis/center/length_pt）
      evtr_boxes:   电梯井 bbox 列表（pt：(x0,y0,x1,y1)）
      floor_no:     楼层号
      gap_m:        窗户距电梯墙的最大距离（米）
      max_width_m:  电梯门最大宽度（米，超出视为建筑窗不识别）

    返回: [{index, elev_index, center_m, axis_m, width_m}]（米制坐标）
    """
    if not window_groups or not evtr_boxes:
        return []
    gap_pt = gap_m / SCALE
    max_w_pt = max_width_m / SCALE
    elev_polys = []
    for bxd in evtr_boxes:
        x0, y0, x1, y1 = bxd
        elev_polys.append((x0, y0, x1, y1))
    out = []
    for i, wg in enumerate(window_groups):
        a, b = wg["axis"]
        # 窗组中心（pt）
        mx = (a[0] + b[0]) / 2.0
        my = (a[1] + b[1]) / 2.0
        # 窗宽（pt）
        w_pt = wg["length_pt"]
        if w_pt > max_w_pt:
            continue
        # 找最近的电梯 bbox：中心到 bbox 矩形的最短距离
        best_d, best_bi = float("inf"), None
        for bi, (x0, y0, x1, y1) in enumerate(elev_polys):
            dx = max(0.0, max(x0 - mx, mx - x1))
            dy = max(0.0, max(y0 - my, my - y1))
            d = math.hypot(dx, dy)
            if d < best_d:
                best_d, best_bi = d, bi
        if best_bi is None or best_d > gap_pt:
            continue
        _mid_pt = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        _c = pt2m(_mid_pt)
        out.append({
            "index": len(out),
            "elev_index": best_bi,
            "center_m": [round(_c[0], 3), round(_c[1], 3)],
            "axis_m": [[round(pt2m(a)[0], 3), round(pt2m(a)[1], 3)],
                       [round(pt2m(b)[0], 3), round(pt2m(b)[1], 3)]],
            "width_m": round(w_pt * SCALE, 3),
        })
    return out


def attach_elevator_door_nodes(nodes, edges, elevator_doors, elevators,
                               floor_no, link_radius_m=15.0):
    """把电梯门元素接入拓扑：生成 TD 节点，连对应电梯 TF 与最近公共节点。

    规则：
      - 每个电梯门生成独立 doorway 节点（doorType="elevator"，label=所属电梯）；
      - 连到对应电梯的 facility(TF) 节点（按 elev_index 匹配）；
      - 连到距门 ≤link_radius_m 的开放空间（intersection / facility_entrance /
        facility / doorway 均可，取最近者，保证门可达）；
      - 编号从当前最大 TD 序号之后续号（不与既有门冲突）。
    """
    if not elevator_doors:
        return nodes, edges
    # 当前最大 TD 序号
    max_td = 0
    for n in nodes:
        if n.get("type") == "doorway":
            try:
                max_td = max(max_td, int(n["id"].split("-")[-1]))
            except ValueError:
                pass
    # 电梯 TF：facilityType=elevator。按坐标最近匹配（reconcile 重排后 index
    # 不可靠），并回填 elevatorId（需求⑳：门归属一律用元素 ID）。
    # TF 自身 label 即电梯编号，但归属字段统一用 ID。
    tf_nodes = [n for n in nodes
                if n.get("type") == "facility" and n.get("facilityType") == "elevator"]
    elev_by_centroid = {}
    for n in tf_nodes:
        n.setdefault("elevatorId", None)  # 由调用方按坐标回填
    # 开放空间候选（不含纯管井门——规则 5 已剔除连接，但节点仍可作挂接点）
    cand = [n for n in nodes if n.get("type") in
            ("intersection", "facility_entrance", "doorway")]
    new_nodes, new_edges = [], []
    node_id_set = {n["id"] for n in nodes}
    edge_id_set = {e["id"] for e in edges}
    seq = max_td
    # 边序号：从既有最大 TE 序号 +1 起，单调递增（避免死循环）
    max_te = 0
    for e in edges:
        try:
            max_te = max(max_te, int(e["id"].split("-")[-1]))
        except (ValueError, IndexError):
            pass
    edge_seq = max_te
    # 电梯门 → 电梯 TF 匹配：优先 elevatorId（Feature 格式，归属用 ID），
    # 否则坐标最近（兼容 detect 原始 dict，规避 reconcile 重排后的 index 错位）
    def _match_tf(ed):
        _ep = ed.get("properties", {}) if "elev_index" not in ed else {}
        ec = list(ed["geometry"]["coordinates"]) if "elev_index" not in ed \
            else list(ed["center_m"])
        if _ep.get("elevatorId"):
            for n in nodes:
                if n.get("type") == "facility" \
                   and n.get("facilityType") == "elevator" \
                   and n.get("elevatorId") == _ep["elevatorId"]:
                    return n
            # TF 未带 elevatorId：按坐标最近回退（并回填）
        best_n, best_d = None, float("inf")
        for n in tf_nodes:
            d = math.hypot(ec[0] - n["coordinates"][0],
                           ec[1] - n["coordinates"][1])
            if d < best_d:
                best_d, best_n = d, n
        return best_n

    for i, ed in enumerate(elevator_doors):
        # 兼容两种格式：detect 原始 dict（elev_index）或 build_geojson Feature
        if "elev_index" in ed:
            ei = ed["elev_index"]
            center = ed["center_m"]
            el_label = (elevators[ei]["properties"]["label"]
                        if 0 <= ei < len(elevators) else f"电梯{floor_no}F-{ei + 1}")
            el_id = (elevators[ei]["id"]
                     if 0 <= ei < len(elevators) else None)
        else:
            _ep = ed.get("properties", {})
            ei = _ep.get("elevatorIndex", 0)
            center = list(ed["geometry"]["coordinates"])
            el_label = _ep.get("elevatorLabel", f"电梯{floor_no}F-{ei + 1}")
            el_id = _ep.get("elevatorId")  # 归属元素 ID（需求⑳）
        seq += 1
        td_id = obj_id(f"F{floor_no}", OBJ_TYPE["topo_doorway"], seq)
        while td_id in node_id_set:
            seq += 1
            td_id = obj_id(f"F{floor_no}", OBJ_TYPE["topo_doorway"], seq)
        node_id_set.add(td_id)
        nd = {
            "id": td_id,
            "type": "doorway",
            "doorType": "elevator",
            "label": f"电梯门（{el_label}）",
            "coordinates": list(center),
            "rooms": [el_id] if el_id else [],
            "elevatorId": el_id,
            "elevatorLabel": el_label,
            "elevatorIndex": ei,
            "blindAccessible": True,
            "wheelchairAccessible": True,
        }
        new_nodes.append(nd)
        # 连对应电梯 TF（需求⑳：用 elevatorId 归属匹配，回退坐标最近）
        tf_node = _match_tf(ed)
        if tf_node:
            tf_id = tf_node["id"]
            # 回填 TF 的 elevatorId（需求⑳：归属用元素 ID）
            if el_id and tf_node.get("elevatorId") is None:
                tf_node["elevatorId"] = el_id
            d = math.hypot(center[0] - tf_node["coordinates"][0],
                           center[1] - tf_node["coordinates"][1])
            edge_seq += 1
            eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            while eid in edge_id_set:
                edge_seq += 1
                eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            edge_id_set.add(eid)
            new_edges.append({
                "id": eid, "from": tf_id, "to": td_id,
                "distance": round(d, 2),
                "estimatedTime": round(d / BLIND_WALK_SPEED, 1),
                "accessibilityLevel": 0, "riskLevel": 1,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
                "type": "elevator_door",
            })
        # 连最近公共节点
        best = None
        best_d = link_radius_m
        for c in cand:
            d = math.hypot(center[0] - c["coordinates"][0],
                           center[1] - c["coordinates"][1])
            if d < best_d:
                best_d, best = d, c
        if best is not None:
            edge_seq += 1
            eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            while eid in edge_id_set:
                edge_seq += 1
                eid = obj_id(f"F{floor_no}", OBJ_TYPE["topo_edge"], edge_seq)
            edge_id_set.add(eid)
            new_edges.append({
                "id": eid, "from": td_id, "to": best["id"],
                "distance": round(best_d, 2),
                "estimatedTime": round(best_d / BLIND_WALK_SPEED, 1),
                "accessibilityLevel": 0, "riskLevel": 1,
                "walkable": True, "wheelchairAccessible": True,
                "blindAccessible": True,
                "type": "elevator_door",
            })
    return nodes + new_nodes, edges + new_edges
