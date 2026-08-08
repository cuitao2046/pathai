#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""残差校验（精确）：
  (1) 非门口/非房间的公共拓扑边 不得穿过封闭房间内部；
  (2) 骨架 LineString 不得穿过封闭房间内部；
  (3) 全边连通分量应保持 1（与原始一致）。
"""
import json, sys
from shapely.geometry import Polygon, LineString, shape

OPEN = {"corridor","lobby","activity","atrium","elevator_lobby","stair_lobby","entrance","accessible_entrance"}
CLOSED = {"classroom","lab","office","meeting","toilet","storage","equipment","library","medical","shaft","staircase","elevator_hall","room","reception","counseling"}
TOL = 0.08

geo = json.load(open(sys.argv[1], encoding="utf-8"))
bad = 0
for fk, fl in geo["floors"].items():
    rooms = [(r["id"], r["properties"].get("roomType",""), r["properties"].get("label",""),
              Polygon(r["geometry"]["coordinates"][0]))
             for r in fl["geometry"]["rooms"]
             if r["properties"].get("roomType","") in CLOSED]
    nodes = {n["id"]: n for n in fl["topology"]["nodes"]}
    tmap = {n["id"]: n.get("type") for n in fl["topology"]["nodes"]}
    # (1) 拓扑边
    res_e = 0
    for e in fl["topology"]["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b: continue
        if tmap[e["from"]] in ("room",) or tmap[e["to"]] in ("room",): continue
        if tmap[e["from"]] == "doorway" or tmap[e["to"]] == "doorway": continue
        seg = LineString([a["coordinates"], b["coordinates"]])
        for rid, rt, lab, poly in rooms:
            if seg.intersects(poly.buffer(-TOL)):
                res_e += 1; bad += 1
                print(f"[F{fk}] 拓扑残留 {e['id']}: {e['from']}→{e['to']} 穿 {rid}[{rt}]{lab}")
                break
    # (2) 骨架线
    res_s = 0
    for feat in (fl.get("skeleton") or {}).get("features") or []:
        try:
            line = shape(feat["geometry"])
        except Exception:
            continue
        for rid, rt, lab, poly in rooms:
            if line.intersects(poly.buffer(-TOL)):
                res_s += 1; bad += 1
                print(f"[F{fk}] 骨架残留 {feat.get('id')} 穿 {rid}[{rt}]{lab}")
                break
    # (3) 连通分量
    adj = __import__("collections").defaultdict(set)
    for e in fl["topology"]["edges"]:
        a, b = e.get("from"), e.get("to")
        if a in nodes and b in nodes:
            adj[a].add(b); adj[b].add(a)
    seen=set(); comps=0
    for nid in nodes:
        if nid in seen: continue
        comps+=1; q=[nid]; seen.add(nid)
        while q:
            u=q.pop()
            for v in adj.get(u,()):
                if v not in seen: seen.add(v); q.append(v)
    print(f"[F{fk}] 拓扑残留 {res_e} / 骨架残留 {res_s} / 全边连通分量 {comps}")
print(f"\n总计问题: {bad}")
sys.exit(1 if bad else 0)
