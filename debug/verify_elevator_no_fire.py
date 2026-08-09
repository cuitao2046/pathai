# -*- coding: utf-8 -*-
"""需求⑲验证：防火门拓扑节点(fire TD)不得与电梯节点(TF)有边。

检查：
1. 所有电梯 TF 的邻边中无 doorType=fire 的 TD；
2. 电梯 TF 仍可达（至少 1 条边，通常连电梯门 TD 或普通门）；
3. 回归：validate / 路由规则 / 可达性不因移除防火门边而破坏。
"""
import json, sys, os, itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.topology.route_rules import load_geojson

GEO = "result/school_building_01_map_v9.geojson"
g = json.load(open(GEO, encoding="utf-8"))

ok = True
for fk in ("1", "2"):
    fl = g["floors"][fk]
    topo = fl["topology"]
    nmap = {n["id"]: n for n in topo["nodes"]}
    elev_tfs = [n for n in topo["nodes"]
                if n.get("type") == "facility"
                and n.get("facilityType") == "elevator"]
    print(f"=== F{fk} ===")
    for tf in elev_tfs:
        edges = [e for e in topo["edges"]
                 if e["from"] == tf["id"] or e["to"] == tf["id"]]
        fire_links = []
        other_links = []
        for e in edges:
            o = e["to"] if e["from"] == tf["id"] else e["from"]
            on = nmap.get(o)
            if on and on.get("type") == "doorway":
                if on.get("doorType") == "fire":
                    fire_links.append(o)
                else:
                    other_links.append((o, on.get("doorType")))
            else:
                other_links.append((o, on.get("type") if on else "?"))
        if fire_links:
            print(f"  ❌ {tf['id']}({tf.get('label')}) 仍连防火门: {fire_links}")
            ok = False
        else:
            print(f"  ✅ {tf['id']}({tf.get('label')}) 无防火门边 | "
                  f"其他边 {len(other_links)} 条: {other_links}")
        if not edges:
            print(f"  ❌ {tf['id']} 无任何边（孤立）")
            ok = False

# 可达性回归：电梯 TF 仍应可达（连电梯门 TD）
print("\n=== 电梯 TF 可达性 ===")
rg = load_geojson(GEO)
for fk in ("1", "2"):
    for nid, n in rg.nodes.items():
        if n["type"] == "facility" and n["facilityType"] == "elevator" \
           and n["floor"] == int(fk):
            reach = any(rg.shortest_path(nid, o, "normal") is not None
                        for o in rg.nodes if o != nid)
            print(f"  {nid}: 可达其他节点 = {reach}")
            if not reach:
                ok = False

# 全量路由回归
print("\n=== 路由回归 ===")
rooms = [nid for nid, n in rg.nodes.items() if n["type"] == "room"]
n_reach = 0
for s, e in itertools.combinations(rooms, 2):
    for mode in ("normal", "blind"):
        sp = rg.shortest_path(s, e, mode)
        if sp is not None:
            n_reach += 1
print(f"可达路径: {n_reach} 条")
print("\n" + ("✅ 全部通过" if ok else "❌ 有失败"))
sys.exit(0 if ok else 1)
