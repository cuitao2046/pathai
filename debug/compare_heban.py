# -*- coding: utf-8 -*-
"""Compare new geojson vs baseline: room counts per floor + heban shape + other-room ids stability."""
import json, sys

def load(p):
    return json.load(open(p, encoding="utf-8"))

def rooms_of(g, fk):
    return (g["floors"][fk].get("semantic") or {}).get("rooms") or []

def summ(g):
    out = {}
    for fk in ("1", "2"):
        rs = rooms_of(g, fk)
        out[fk] = {
            "count": len(rs),
            "ids": {r["id"] for r in rs},
            "heban": [(r["id"], r.get("label"),
                       round(abs(sum(c[0]*n[1]-n[0]*c[1] for c,n in zip(
                           r["geometry"]["coordinates"][0],
                           r["geometry"]["coordinates"][0][1:]+[r["geometry"]["coordinates"][0][0]])))/2,2)
                       if r.get("geometry",{}).get("type")=="Polygon" else None)
                      for r in rs if "合班" in str(r.get("label",""))],
        }
    return out

base = summ(load(sys.argv[1] if len(sys.argv)>1 else "/tmp/baseline_v9.geojson"))
new = summ(load(sys.argv[2] if len(sys.argv)>2 else "result/school_building_01_map_v9.geojson"))

for fk in ("1", "2"):
    bc, nc = base[fk]["count"], new[fk]["count"]
    print(f"F{fk}: baseline_rooms={bc}  new_rooms={nc}  delta={nc-bc}")
    only_base = base[fk]["ids"] - new[fk]["ids"]
    only_new = new[fk]["ids"] - base[fk]["ids"]
    if only_base: print(f"   rooms only in baseline: {sorted(only_base)}")
    if only_new: print(f"   rooms only in NEW:      {sorted(only_new)}")
    print(f"   heban (baseline): {base[fk]['heban']}")
    print(f"   heban (NEW):       {new[fk]['heban']}")
