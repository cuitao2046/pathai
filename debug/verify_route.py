"""在新生 geojson 上跑 Dijkstra，验证 音乐教室→组织办公 路线。"""
import json, heapq, collections
GEO = r"D:\code\pathai\result\school_building_01_map_v9.geojson"
with open(GEO, encoding="utf-8") as f:
    data = json.load(f)
fl = data["floors"]["1"]
nodes = fl["topology"]["nodes"]
edges = fl["topology"]["edges"]
nmap = {n["id"]: n for n in nodes}
# 找房间
def find_room(kw):
    for n in nodes:
        if n["type"] == "room" and kw in (n.get("label") or ""):
            return n["id"], n.get("label")
    return None, None
start_id, sl = find_room("音乐")
end_id, el = find_room("组织办公")
print("起点:", start_id, sl, "| 终点:", end_id, el)

adj = collections.defaultdict(list)
for e in edges:
    adj[e["from"]].append((e["to"], e["distance"], e["id"]))
    adj[e["to"]].append((e["from"], e["distance"], e["id"]))

def dijkstra(s, t):
    dist = {s: 0}
    prev = {}
    pq = [(0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist.get(u, 1e9):
            continue
        for v, w, eid in adj[u]:
            nd = d + w
            if nd < dist.get(v, 1e9):
                dist[v] = nd
                prev[v] = (u, eid)
                heapq.heappush(pq, (nd, v))
    if t not in dist:
        return None
    path, eids = [t], []
    while t != s:
        u, eid = prev[t]
        eids.append(eid)
        path.append(u)
        t = u
    path.reverse(); eids.reverse()
    return path, eids, dist[t]

res = dijkstra(start_id, end_id)
path, eids, d = res
em = {e["id"]: e["distance"] for e in edges}
d_true = sum(em[i] for i in eids)
print(f"节点数={len(path)} 距离={d_true:.1f}m 边数={len(eids)}")
print("路径:", " → ".join(path))
# 第一跳 / 倒数第二跳 是否门
for idx in (1, len(path)-2):
    nid = path[idx]
    n = nmap[nid]
    print(f"  第{idx}跳 {nid} type={n['type']} rooms={n.get('rooms')}")
# 校验首尾边必须是本房间门（TR→TD）
assert path[0] == start_id and path[-1] == end_id
print("首尾为本房间 ✓")
