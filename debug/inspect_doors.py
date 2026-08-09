# -*- coding: utf-8 -*-
import json
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

fk = "1"
fd = geo["floors"][fk]
# 看 geometry.doors 结构
doors = fd["geometry"].get("doors", [])
print("geom.doors 数量:", len(doors))
for d in doors[:3]:
    print(" DOOR keys:", list(d.keys()))
    print("   ", {k: d[k] for k in d if k != "geometry"})
# 看 topology 中 doorway 节点结构
tds = [n for n in fd["topology"]["nodes"] if n["type"] == "doorway"]
print("\ntopology doorway 数量:", len(tds))
for n in tds[:3]:
    print(" TD keys:", list(n.keys()))
    print("   ", {k: n[k] for k in n if k not in ("coordinates",)})
# 门洞(opening) 的 geometry door 是否有 center_m
print("\n--- geometry.door 中 kind/roomId/center_m 样例 ---")
for d in doors[:5]:
    props = d.get("properties", {})
    print("   ", d.get("id"), "roomId=", props.get("roomId"), "kind=", props.get("kind"),
          "center=", d.get("center_m"))
