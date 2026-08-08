#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比两个 GeoJSON 的拓扑连通性（原图 vs 去穿墙边后）。"""
import json, sys
from collections import defaultdict, deque

def load(path):
    return json.loads(open(path, encoding="utf-8").read())

def components(fl, active_only=False):
    """返回 (components, isolated_nodes, node_count)。
    active_only: 仅统计 walkable & blindAccessible & acc<900 的边（真实可导航）。"""
    nodes = fl.get("topology", {}).get("nodes") or []
    edges = fl.get("topology", {}).get("edges") or []
    nmap = {n["id"]: n for n in nodes}
    adj = defaultdict(set)
    used_nodes = set()
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a is None or b is None:
            continue
        if active_only:
            if not e.get("walkable") or not e.get("blindAccessible"):
                continue
            if (e.get("accessibilityLevel") or 0) >= 900:
                continue
        if a not in nmap or b not in nmap:
            continue
        adj[a].add(b); adj[b].add(a)
        used_nodes.add(a); used_nodes.add(b)
    # 连通分量（包含所有拓扑节点，即使度0）
    all_ids = set(nmap.keys())
    seen = set()
    comps = []
    for nid in all_ids:
        if nid in seen:
            continue
        # BFS
        q = deque([nid]); seen.add(nid); comp = []
        while q:
            u = q.popleft(); comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v); q.append(v)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    deg = {nid: len(adj.get(nid, ())) for nid in all_ids}
    isolated = [nid for nid in all_ids if deg[nid] == 0]
    return comps, isolated, len(all_ids)

def summarize(path, label):
    geo = load(path)
    print(f"\n===== {label} ({path}) =====")
    for fk, fl in (geo.get("floors") or {}).items():
        c_all, iso_all, nn = components(fl, active_only=False)
        c_act, iso_act, _ = components(fl, active_only=True)
        print(f"\n[F{fk}] 节点 {nn}  全边连通分量 {len(c_all)}  可导航连通分量 {len(c_act)}")
        print(f"   全边主分量 {len(c_all[0])} 节点；其余: " +
              ", ".join(str(len(c)) for c in c_all[1:20]) +
              (f" ...(+{len(c_all)-20})" if len(c_all) > 20 else ""))
        print(f"   可导航主分量 {len(c_act[0])} 节点；其余: " +
              ", ".join(str(len(c)) for c in c_act[1:20]) +
              (f" ...(+{len(c_act)-20})" if len(c_act) > 20 else ""))
        # 孤立节点
        if iso_all:
            print(f"   孤立(度0)节点 {len(iso_all)}: {iso_all[:30]}")
    return geo

if __name__ == "__main__":
    orig = sys.argv[1] if len(sys.argv) > 1 else "debug/_v9_orig_copy.geojson"
    fixed = sys.argv[2] if len(sys.argv) > 2 else "debug/_v9_fixed_test.geojson"
    summarize(orig, "原始 (ORIGINAL)")
    summarize(fixed, "去穿墙边后 (FIXED)")
