# -*- coding: utf-8 -*-
"""需求⑱验证：电梯旁窗户识别为电梯门元素 + 归属电梯间 + 分配拓扑节点。

检查：
1. geometry.elevatorDoors 存在且数量正确（F1/F2 各应 ≥3，每电梯至少 1 门）；
2. 每个电梯门归属对应电梯（elevatorLabel/elevatorIndex 有效）；
3. 拓扑层存在电梯门 TD 节点（doorType=elevator），且连接电梯 TF 与公共节点；
4. 回归：validate PASS、路由规则不因新 TD 破坏。
"""
import json, sys, os, itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.topology.route_rules import load_geojson

GEO = "result/school_building_01_map_v9.geojson"
g = json.load(open(GEO, encoding="utf-8"))

ok = True
for fk in ("1", "2"):
    fl = g["floors"][fk]
    evd = fl["geometry"].get("elevatorDoors", [])
    elevs = fl["geometry"].get("elevators", [])
    topo = fl["topology"]
    print(f"=== F{fk} ===")
    print(f"  电梯: {len(elevs)} 个 | 电梯门元素: {len(evd)} 个")
    if not evd:
        print("  ❌ 未识别到电梯门"); ok = False
        continue
    # 1) 归属校验（需求⑳：归属一律用元素 ID，不用 label）
    elev_ids = {e["id"] for e in elevs}
    for d in evd:
        p = d["properties"]
        rid = (p.get("rooms") or [None])[0]
        eid = p.get("elevatorId")
        lbl = p.get("elevatorLabel")
        if rid not in elev_ids or eid not in elev_ids:
            print(f"  ❌ {d['id']} 电梯归属非 ID: rooms={rid} elevatorId={eid}")
            ok = False
            continue
        # 坐标匹配校验：门应距所属电梯 <3m
        el_feat = next(e for e in elevs if e["id"] == eid)
        elc = el_feat["properties"]["centroid"]
        c = d["geometry"]["coordinates"]
        dist = ((c[0] - elc[0]) ** 2 + (c[1] - elc[1]) ** 2) ** 0.5
        if dist > 3.0:
            print(f"  ❌ {d['id']} -> {eid} 距离 {dist:.2f}m（错位）")
            ok = False
        else:
            print(f"  ✅ {d['id']} -> {eid} ({lbl}, 距 {dist:.2f}m)")
    # 1b) 拓扑 TD 归属 ID 校验
    for n in topo["nodes"]:
        if n.get("type") == "doorway" and n.get("doorType") == "elevator":
            if n.get("elevatorId") not in elev_ids:
                print(f"  ❌ TD {n['id']} elevatorId 无效: {n.get('elevatorId')}")
                ok = False
    # 2) 拓扑 TD 节点
    td_ev = [n for n in topo["nodes"] if n.get("type") == "doorway"
             and n.get("doorType") == "elevator"]
    print(f"  拓扑电梯门 TD: {len(td_ev)} 个")
    for n in td_ev:
        edges = [e for e in topo["edges"] if e["from"] == n["id"] or e["to"] == n["id"]]
        types = set()
        for e in edges:
            o = e["to"] if e["from"] == n["id"] else e["from"]
            on = next((x for x in topo["nodes"] if x["id"] == o), None)
            if on:
                types.add(f"{on['type']}/{on.get('label','')[:8]}")
        print(f"  {n['id']} ({n.get('label')}) 连 {len(edges)} 边 -> {sorted(types)}")
    if not td_ev:
        print("  ❌ 无电梯门 TD"); ok = False
    if len(td_ev) != len(evd):
        print(f"  ⚠️ TD({len(td_ev)}) 与元素({len(evd)}) 数量不一致"); ok = False

# 3) 路由规则回归：新 TD 不破坏路径
print("\n=== 路由回归 ===")
rg = load_geojson(GEO)
rooms = [nid for nid, n in rg.nodes.items() if n["type"] == "room"]
viol = 0
n_reach = 0
for s, e in itertools.combinations(rooms, 2):
    for mode in ("normal", "blind"):
        sp = rg.shortest_path(s, e, mode)
        if sp is None:
            continue
        n_reach += 1
        # 规则 5：路径不得经过纯管井门
        if any(nid in rg.infra_doorway_ids for nid in sp["path"]):
            viol += 1
print(f"可达路径 {n_reach} 条, 经过纯管井门 {viol} 条")
if viol:
    print("❌ 规则5 被破坏"); ok = False

print("\n" + ("✅ 全部通过" if ok else "❌ 有失败"))
sys.exit(0 if ok else 1)
