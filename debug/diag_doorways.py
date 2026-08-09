"""诊断当前 geojson 的 doorway 拓扑节点：
   - 悬空 corridor-only 门（只连 TI，不连任何 room TR）
   - 重叠门（同坐标多个 TD）
   - corridor-only 门的 rooms 字段 + 这些 room 是否存在于 TR 节点
"""
import json, collections, os

GEO = r"D:\code\pathai\result\school_building_01_map_v9.geojson"

with open(GEO, encoding="utf-8") as f:
    data = json.load(f)

def analyze(floor_key):
    fl = data["floors"][floor_key]
    topo = fl["topology"]
    nodes = topo["nodes"]
    edges = topo["edges"]
    nmap = {n["id"]: n for n in nodes}
    degree = collections.Counter()
    adj = collections.defaultdict(set)
    for e in edges:
        degree[e["from"]] += 1
        degree[e["to"]] += 1
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])

    rooms_present = {n["id"] for n in nodes if n["type"] == "room"}
    room_ids_present = {n.get("roomId") for n in nodes if n["type"] == "room"}

    td = [n for n in nodes if n["type"] == "doorway"]
    tr = [n for n in nodes if n["type"] == "room"]

    # 分类 doorway
    corridor_only = []
    orphan = []
    normal = []
    for n in td:
        nid = n["id"]
        d = degree.get(nid, 0)
        neigh = adj.get(nid, set())
        neigh_types = {nmap[m]["type"] for m in neigh}
        if d == 0:
            orphan.append(n)
        elif "room" not in neigh_types:
            corridor_only.append(n)
        else:
            normal.append(n)

    print(f"\n===== 楼层 {floor_key} =====")
    print(f"节点总数={len(nodes)} | 门 TD={len(td)} | 房间 TR={len(tr)}")
    print(f"悬空 corridor-only 门={len(corridor_only)} | 完全孤立 orphan={len(orphan)} | 正常(连房间)={len(normal)}")

    print("\n--- corridor-only 门明细 ---")
    for n in corridor_only:
        rooms = n.get("rooms", [])
        # 这些 room 是否在 TR 节点里
        room_status = []
        for rid in rooms:
            in_tr = rid in room_ids_present
            room_status.append(f"{rid}({'TR存在' if in_tr else 'NOTR'})")
        print(f"{n['id']} coord={n['coordinates']} rooms={room_status} neigh={[m for m in adj.get(n['id'],[])][:4]}")

    # 重叠门（坐标一致）
    coord_groups = collections.defaultdict(list)
    for n in td:
        c = n["coordinates"]
        coord_groups[(round(c[0],2), round(c[1],2))].append(n["id"])
    overlaps = {k:v for k,v in coord_groups.items() if len(v) > 1}
    print(f"\n--- 重叠门坐标组(同坐标>=2个TD) {len(overlaps)} 组 ---")
    for c, ids in sorted(overlaps.items(), key=lambda kv: -len(kv[1])):
        # 打印每组的 rooms
        info = []
        for nid in ids:
            n = nmap[nid]
            info.append(f"{nid}[rooms={n.get('rooms',[])}]")
        print(f"  {c}: {len(ids)}个 -> " + " ".join(info))

    # 可达性 BFS from 1-TI-001（或 2-TI-xxx）
    start_ti = None
    for n in nodes:
        if n["type"] == "intersection":
            start_ti = n["id"]
            break
    reachable = set()
    if start_ti:
        stack = [start_ti]
        reachable.add(start_ti)
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in reachable:
                    reachable.add(v)
                    stack.append(v)

    # BUG 类：corridor-only 但其 rooms 含 TR 存在的房间 → 房间可能失联
    print("\n--- BUG 候选：corridor-only 但其 rooms 含 TR 房间 ---")
    bug_rooms = set()
    for n in corridor_only:
        tr_rooms = [rid for rid in n.get("rooms", [])
                    if rid in room_ids_present]
        if tr_rooms:
            for rid in tr_rooms:
                # 找该房间 TR 节点
                trn = next((m for m in nodes
                            if m["type"] == "room" and m.get("roomId") == rid), None)
                if trn:
                    deg = degree.get(trn["id"], 0)
                    reach = trn["id"] in reachable
                    bug_rooms.add(rid)
                    print(f"  {n['id']} -> {rid} (TR={trn['id']} deg={deg} reachable={reach})")
    print(f"  涉及 BUG 房间数={len(bug_rooms)}: {sorted(bug_rooms)}")

    # 不可达的房间（TR）节点
    unreach_rooms = [n["id"] for n in nodes
                     if n["type"] == "room" and n["id"] not in reachable]
    print(f"\n--- 不可达房间(TR)节点: {len(unreach_rooms)} ---")
    for rid in unreach_rooms:
        n = nmap[rid]
        print(f"  {rid} roomId={n.get('roomId')} label={n.get('label')} deg={degree.get(rid,0)}")

    # 总悬空影响：从 TI 可达节点数
    reach_count = len(reachable)
    print(f"\n  从 {start_ti} 可达节点={reach_count} / 总 {len(nodes)}")
    print(f"  悬空 corridor-only={len(corridor_only)} | 重叠组={len(overlaps)} | 不可达房间={len(unreach_rooms)}")
    return corridor_only, orphan, overlaps

for fk in ["1", "2"]:
    analyze(fk)
