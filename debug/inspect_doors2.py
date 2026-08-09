# -*- coding: utf-8 -*-
import json
from pathlib import Path
BASE = Path(__file__).resolve().parent.parent
geo = json.loads((BASE / "result" / "school_building_01_map_v9.geojson").read_text(encoding="utf-8"))

fk = "1"
fd = geo["floors"][fk]
doors = fd["geometry"].get("doors", [])
tds = [n for n in fd["topology"]["nodes"] if n["type"] == "doorway"]

# 索引对齐检验：F1-D-{i} 的 rooms/type 是否与 F1-TD-{i} 一致
def seq_of(eid):
    return int(eid.rsplit("-", 1)[-1])

door_by_seq = {seq_of(d["id"]): d for d in doors}
td_by_seq = {seq_of(n["id"]): n for n in tds}

mismatch = 0
checked = 0
for s, td in td_by_seq.items():
    d = door_by_seq.get(s)
    if d is None:
        print(f"  TD {td['id']} 无对应 geom door 序号 {s}")
        mismatch += 1
        continue
    checked += 1
    if sorted(d["properties"].get("rooms", [])) != sorted(td.get("rooms", [])):
        # 允许顺序不同
        if set(d["properties"].get("rooms", [])) != set(td.get("rooms", [])):
            print(f"  rooms 不一致: TD {td['id']} {td.get('rooms')} vs DOOR {d['id']} {d['properties'].get('rooms')}")
            mismatch += 1
    if d["properties"].get("doorType") != td.get("doorType"):
        print(f"  type 不一致: TD {td['id']} {td.get('doorType')} vs DOOR {d['id']} {d['properties'].get('doorType')}")
        mismatch += 1
print(f"对齐检验: 检查 {checked} 个 TD, 不一致 {mismatch}")

# 看一个 door 的 geometry
print("\n--- 一个 door 的 geometry ---")
print(json.dumps(doors[0], ensure_ascii=False)[:600])
# 提取门中心
def door_center(d):
    g = d.get("geometry", {})
    if g.get("type") == "Point":
        return g["coordinates"]
    if g.get("type") == "LineString":
        c = g["coordinates"]
        return [(c[0][0]+c[1][0])/2, (c[0][1]+c[1][1])/2]
    return None
print("\nF1-D-0001 门中心:", door_center(doors[0]))
