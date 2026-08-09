# -*- coding: utf-8 -*-
"""TD 层重构：每扇 geometry.door 严格对应一个 TD 节点（1:1，序号一致），删除 TDX 子节点。

- 删除现有所有 doorway 节点（hub TD + TDX）及其边
- 为每扇 geometry.door 建 TD：
    id = F{floor}-TD-{序号}（序号 = door id 数字，如 F1-D-0018 → F1-TD-0018）
    坐标 = 门坐标；doorType/width/openDirection 等从门 properties 复制
    rooms = 贴墙封闭房间（权威贴墙判据 <0.6m）；sourceDoorIds=[门id]
- 重建边：
    TR↔TD：房间 → 贴其墙的门 TD（非卫生间房间仅 swing/fire；卫生间允许 opening）
    TD↔TI：每 TD 连最近 TI（走廊接入）
- 不穿墙保证：TR↔TD 直连（用户已确认接受多房间门可能的穿墙，后续单独处理）

用法: python debug/rebuild_td_1to1.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parent.parent
WALL_TOL = 0.6
OPENP = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
         "stair_lobby", "staircase", "infrastructure", "elevator_hall"}


def room_type_of(fd, rid):
    for r in fd["geometry"].get("rooms", []):
        if r["id"] == rid:
            return r.get("properties", {}).get("roomType") or r.get("type")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))
    bak = p.with_name(p.stem + "_before_td1to1" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]

        # 房间多边形 + 质心
        room_poly = {}
        room_centroid = {}
        for n in nodes:
            if n["type"] == "room" and n.get("roomId"):
                room_centroid[n["roomId"]] = n["coordinates"]
        for r in fd["geometry"].get("rooms", []):
            try: room_poly[r["id"]] = shape(r["geometry"])
            except Exception: pass

        # TI 节点
        tis = [n for n in nodes if n["type"] == "intersection"]

        # 删除所有 doorway 节点（hub + TDX）
        new_nodes = [n for n in nodes if n["type"] != "doorway"]

        # 为每扇门建 TD
        tds = []       # 新 TD 节点
        door_to_td = {}  # door id -> td id
        for d in fd["geometry"].get("doors", []):
            pr = d.get("properties", {})
            coords = d.get("geometry", {}).get("coordinates")
            if not coords:
                continue
            # id: F1-D-0018 -> F1-TD-0018
            seq = d["id"].split("-")[-1]
            td_id = f"F{fk}-TD-{seq}"
            pt = Point(coords)
            # 贴墙封闭房间（权威）
            wall = {rid for rid in room_poly
                    if room_type_of(fd, rid) not in OPENP
                    and room_poly[rid].boundary.distance(pt) < WALL_TOL}
            doorType = pr.get("doorType")
            td = {
                "id": td_id,
                "type": "doorway",
                "label": {"swing": "普通门", "fire": "防火门",
                          "opening": "门洞"}.get(doorType, "门"),
                "doorType": doorType,
                "width_m": pr.get("width_m"),
                "coordinates": [round(coords[0], 3), round(coords[1], 3)],
                "rooms": sorted(wall),
                "sourceDoorIds": [d["id"]],
            }
            for k in ("openDirection", "hingeSide", "openDirectionSource",
                      "swingIntoRoom", "wheelchairAccessible"):
                if pr.get(k) is not None:
                    td[k] = pr[k]
            tds.append(td)
            door_to_td[d["id"]] = td_id
        new_nodes += tds

        # 重建边：保留非 doorway 边（TI↔TI/TF↔TI/TEN↔TI 等），重建 doorway 相关边
        nmap = {n["id"]: n for n in new_nodes}
        new_edges = []
        edge_counter = 0

        def mk_edge(frm, to, acc=0, blind=True, wheel=True, rl=0.5):
            nonlocal edge_counter
            c1 = nmap[frm]["coordinates"]; c2 = nmap[to]["coordinates"]
            dist = round(math.hypot(c1[0]-c2[0], c1[1]-c2[1]), 2)
            edge_counter += 1
            # 边 id 带楼层前缀，保证跨层全局唯一（F1-E000001 / F2-E000001）
            return {"id": f"F{fk}-E{edge_counter:06d}", "from": frm, "to": to,
                    "distance": dist, "estimatedTime": round(dist/0.8, 1),
                    "accessibilityLevel": acc, "riskLevel": rl,
                    "walkable": True, "wheelchairAccessible": wheel,
                    "blindAccessible": blind, "crossFloor": False}

        # 保留原拓扑中不含 doorway 的边（TI↔TI、TF↔TI、TEN↔TI、crossFloor）
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                continue
            if a["type"] != "doorway" and b["type"] != "doorway":
                new_edges.append(e)

        # TR↔TD：房间连贴其墙的门 TD（贴墙 <0.6m 或门 rooms 字段标注）
        # TR↔TD：房间连贴其墙的门 TD（贴墙 <0.6m 或门 rooms 字段标注含该房间）。
        # 注意：门 rooms 字段偶有漂移（CAD 标注错误），标注兜底也必须校验
        #     门与房间质心距离（≤ ANNOTATED_MAX_M），否则会建跨楼穿墙边。
        # 非卫生间房间只连 swing/fire；卫生间可连 opening。
        ANNOTATED_MAX_M = 8.0
        tr_count = 0
        room_door_candidates = {}
        for n in new_nodes:
            if n["type"] != "room" or not n.get("roomId"):
                continue
            rid = n["roomId"]
            rt = room_type_of(fd, rid)
            for d in fd["geometry"].get("doors", []):
                pr = d.get("properties", {})
                coords = d.get("geometry", {}).get("coordinates")
                if not coords:
                    continue
                td_id = door_to_td.get(d["id"])
                if not td_id or td_id not in nmap:
                    continue
                dt = pr.get("doorType")
                # 非卫生间房间只连 swing/fire；卫生间可连 opening
                if rt not in ("toilet",) and dt not in ("swing", "fire"):
                    continue
                pt = Point(coords)
                if rid not in room_poly:
                    continue
                # 判据：贴墙 <0.6m 或 门 rooms 字段标注含该房间（距离合规）
                # 标注兜底也必须校验门与房间质心距离（≤ ANNOTATED_MAX_M），
                # 否则 rooms 字段漂移会建跨楼穿墙边。
                marked = rid in (pr.get("rooms") or [])
                if room_poly[rid].boundary.distance(pt) < WALL_TOL:
                    room_door_candidates.setdefault(n["id"], []).append((td_id, dt))
                elif marked:
                    d_m = math.hypot(n["coordinates"][0] - coords[0],
                                       n["coordinates"][1] - coords[1])
                    if d_m <= ANNOTATED_MAX_M:
                        room_door_candidates.setdefault(n["id"], []).append((td_id, dt))
        for nid, lst in room_door_candidates.items():
            rid = next(nn["roomId"] for nn in new_nodes if nn["id"] == nid)
            for td_id, dt in lst:
                new_edges.append(mk_edge(nid, td_id,
                                         acc=2 if dt == "fire" else 0,
                                         rl=5 if dt == "fire" else 0.5))
                tr_count += 1
                # 同步：房间加入 TD.rooms（route_rules 用 rooms 判定无门卫生间）
                td_node = nmap[td_id]
                if rid not in (td_node.get("rooms") or []):
                    td_node["rooms"] = list(td_node.get("rooms") or []) + [rid]

        # 兜底：无任何门边的封闭房间 → 连最近 TD（保连通，validate 要求 TR 有 TD 边）
        # 适用：infrastructure（管井/风井，无门开口）、部分 staircase（数据无门）、
        #     无贴墙门且无标注门的卫生间/设备房——恢复重构前的 TDX 兜底连通性。
        # 注意：staircase/infrastructure 的 TR 节点虽属开放语义，但 validate 要求
        #     每个 TR 有 TD 边，且楼梯间是跨层枢纽必须可达 → 不跳过。
        tr_edges_now = {}
        for e in new_edges:
            a, b = nmap.get(e["from"]), nmap.get(e["to"])
            if not a or not b: continue
            if a["type"] == "room": tr_edges_now.setdefault(a["id"], set()).add(e["to"])
            if b["type"] == "room": tr_edges_now.setdefault(b["id"], set()).add(e["from"])
        fallback_count = 0
        for n in new_nodes:
            if n["type"] != "room" or not n.get("roomId"):
                continue
            if tr_edges_now.get(n["id"]):
                continue
            if not tds:
                continue
            c = n["coordinates"]
            best, bd = None, 1e9
            for td in tds:
                dd = math.hypot(c[0]-td["coordinates"][0], c[1]-td["coordinates"][1])
                if dd < bd: bd, best = dd, td["id"]
            if best:
                new_edges.append(mk_edge(n["id"], best))
                fallback_count += 1
                # 同步：兜底房间加入 TD.rooms（route_rules 用 rooms 判定无门卫生间）
                td_node = nmap[best]
                if n.get("roomId") not in (td_node.get("rooms") or []):
                    td_node["rooms"] = list(td_node.get("rooms") or []) + [n.get("roomId")]

        # TD↔TI：每 TD 连最近 TI
        td_ti_count = 0
        for td in tds:
            if not tis:
                break
            c = td["coordinates"]
            best, bd = None, 1e9
            for ti in tis:
                dd = math.hypot(c[0]-ti["coordinates"][0], c[1]-ti["coordinates"][1])
                if dd < bd: bd, best = dd, ti["id"]
            if best:
                new_edges.append(mk_edge(td["id"], best))
                td_ti_count += 1

        fd["topology"]["nodes"] = new_nodes
        fd["topology"]["edges"] = new_edges
        print(f"F{fk}: 门={len(door_to_td)} TD={len(tds)} (删除旧TD+TDX="
              f"{len(nodes)-len([n for n in nodes if n['type']=='doorway'])}"
              f") TR↔TD边={tr_count} 兜底={fallback_count} TD↔TI边={td_ti_count}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
