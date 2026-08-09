# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

# 收集真实跳跃的 TD id（与 diag 同口径）
sys.path.insert(0, str(BASE / "src"))
import route_rules as rr
g = rr.RouteGraph(geo)
def real_jump_tds(fk, fd):
    out = set()
    for e in g.edges:
        a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
        if not a or not b: continue
        if not ({a["type"], b["type"]} == {"room", "doorway"}): continue
        rn = a if a["type"] == "room" else b
        dn = b if a["type"] == "room" else a
        if not g._seg_crosses_any_wall(rn["coords"], dn["coords"]): continue
        out.add(dn["id"])
    return out

for fk, fd in geo["floors"].items():
    nmap = {n["id"]: n for n in fd["topology"]["nodes"]}
    rj = real_jump_tds(fk, fd)
    cnt = 0
    for td_id in sorted(rj):
        td = nmap[td_id]
        # 该 TD 的所有边
        eds = [e for e in fd["topology"]["edges"] if e["from"] == td_id or e["to"] == td_id]
        print(f"\n[{fk}] {td_id} coord={tuple(round(x,2) for x in td['coordinates'])} doorType={td.get('doorType')} rooms(field)={td.get('rooms')}")
        for e in eds:
            oid = e["to"] if e["from"] == td_id else e["from"]
            o = nmap.get(oid)
            if o is None:
                print(f"   edge -> {oid} (MISSING)"); continue
            if o["type"] == "room":
                print(f"   edge -> ROOM {oid} roomId={o.get('roomId')} coord={tuple(round(x,2) for x in o['coordinates'])}")
            else:
                print(f"   edge -> {o['type']} {oid} coord={tuple(round(x,2) for x in o['coordinates'])}")
        cnt += 1
        if cnt >= 3:
            break
    print(f"\n--- {fk} real-jump TD 总数={len(rj)} (showing first 3) ---")
    break  # 只看 F1
