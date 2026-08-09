# -*- coding: utf-8 -*-
"""诊断当前拓扑中 room 节点的边类型与穿墙情况。"""
import json, sys
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE / "src" / "skeleton"))
import route_rules as rr

geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))
g = rr.RouteGraph(geo)

# 1) 边类型统计（按端点 type）
from collections import Counter
ety = Counter()
tr_ti = []
for e in g.edges:
    a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
    if not a or not b:
        continue
    key = tuple(sorted((a["type"], b["type"])))
    ety[key] += 1
    if "room" in (a["type"], b["type"]) and "intersection" in (a["type"], b["type"]):
        tr_ti.append((e["id"], a["type"], a.get("roomType"), b["type"]))

print("=== 边类型分布 ===")
for k, v in sorted(ety.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

print(f"\n=== room->intersection 直接边: {len(tr_ti)} ===")
for t in tr_ti[:50]:
    print("  ", t)

# 2) 穿墙边统计（按类型）
wc = Counter()
wc_examples = []
for e in g.edges:
    a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
    if not a or not b:
        continue
    ca = a["coords"]; cb = b["coords"]
    if ca is None or cb is None:
        continue
    if g._seg_crosses_any_wall(ca, cb):
        key = tuple(sorted((a["type"], b["type"])))
        wc[key] += 1
        if len(wc_examples) < 40:
            wc_examples.append((e["id"], a["type"], b["type"], a.get("roomType","-"), b.get("roomType","-")))

print(f"\n=== 穿墙边分布: 共 {sum(wc.values())} ===")
for k, v in sorted(wc.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")
print("示例:")
for t in wc_examples:
    print("  ", t)

# 3) room->doorway 边中，门类型为 opening 的数量
op = 0
for e in g.edges:
    a = g.nodes.get(e["from"]); b = g.nodes.get(e["to"])
    if not a or not b:
        continue
    if a["type"] == "room" and b["type"] == "doorway":
        if b.get("doorType") == "opening":
            op += 1
    if a["type"] == "doorway" and b["type"] == "room":
        if a.get("doorType") == "opening":
            op += 1
print(f"\n=== room<->opening(门洞) 边: {op} ===")

