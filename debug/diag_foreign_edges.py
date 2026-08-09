# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
from shapely.geometry import shape, LineString
BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

legit_exit = 0
foreign_exit = 0
foreign_total = 0
foreign_removable = 0   # 房间还有别的合法门边
foreign_orphan = 0      # 房间只有这条 foreign 边（删除会孤立）
room_door_edges = {}    # roomId -> list of (edge, td_rooms)
for fk, fd in geo["floors"].items():
    nmap = {n["id"]: n for n in fd["topology"]["nodes"]}
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass
    for e in fd["topology"]["edges"]:
        a = nmap.get(e["from"]); b = nmap.get(e["to"])
        if not a or not b: continue
        if not ({a["type"], b["type"]} == {"room", "doorway"}): continue
        rn = a if a["type"] == "room" else b
        dn = b if a["type"] == "room" else a
        rid = rn.get("roomId")
        room_door_edges.setdefault(rid, []).append((e, dn))

for fk, fd in geo["floors"].items():
    nmap = {n["id"]: n for n in fd["topology"]["nodes"]}
    room_poly = {}
    for r in fd["geometry"].get("rooms", []):
        try: room_poly[r["id"]] = shape(r["geometry"])
        except Exception: pass
    for rid, lst in room_door_edges.items():
        if rid not in room_poly: continue
        poly = room_poly[rid]
        for e, dn in lst:
            # 该房间此边对应的门节点
            door_rooms = set(dn.get("rooms") or [])
            foreign = rid not in door_rooms
            if foreign: foreign_total += 1
            seg = LineString([nmap[e["from"]]["coordinates"], nmap[e["to"]]["coordinates"]])
            exits = not seg.covered_by(poly)
            if foreign and exits:
                foreign_exit += 1
                # 该房间是否还有别的合法门边
                has_other = any((rid in set(o[1].get("rooms") or [])) and o[1]["id"] != dn["id"]
                                for o in room_door_edges.get(rid, []))
                if has_other: foreign_removable += 1
                else: foreign_orphan += 1
            if (not foreign) and exits:
                legit_exit += 1

print(f"foreign 门边(房间不在门.rooms中) 总数={foreign_total}")
print(f"  foreign 且出界={foreign_exit}  其中可删除(房间另有合法门)={foreign_removable}  孤立风险(仅此一条)={foreign_orphan}")
print(f"legitimate(房间在门.rooms中) 且出界={legit_exit}")
