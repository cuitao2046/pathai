# -*- coding: utf-8 -*-
"""需求⑤(c)：重排门节点(TD)坐标，使「房间质心↔门」拓扑边不再穿墙。

核心思路：
- 对每扇门 TD，收集「实际与它相连的房间节点」(来自拓扑边) ∪ (td.rooms 字段)，
  这些房间质心→门的连线都必须留在房间多边形内部（不穿墙）。
- 在「每个归属房间的自身边界」上密集采样候选点（共享墙会同时出现在两房间边界上，
  故 shared-door 可达），用严格穿墙判定（排除端点落墙的假阳性）筛出对全部质心都
  「可见」(连线不穿墙且中点落在房间内) 的点，取距原 TD 位置最近者。
- 若一个候选都无法满足全部房间（极少见，如房间分布于门两侧被隔墙分开），则退化为
  满足最多房间的点（best-effort）。

用法: python debug/fix_td_visibility.py [geojson] [--step 0.3]
"""
from __future__ import annotations
import argparse, json, math, sys, copy
from pathlib import Path
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))


def side(px, py, ax, ay, bx, by):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def seg_crosses_wall_strict(p1, p2, A, B):
    """严格穿墙：路径段内部与墙段内部相交（排除端点落在墙线上的假阳性）。"""
    ax, ay = A[0], A[1]
    bx, by, = B[0], B[1]
    px, py = p1[0], p1[1]
    qx, qy = p2[0], p2[1]
    dx, dy = bx - ax, by - ay
    s1 = side(px, py, ax, ay, bx, by)
    s2 = side(qx, qy, ax, ay, bx, by)
    if s1 == 0 and s2 == 0:
        return False  # 共线
    if s1 == 0 or s2 == 0:
        return False  # 端点落在墙线 -> 门口/墙角，非穿透
    if s1 * s2 > 0:
        return False  # 同侧并行，非穿透
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return False
    ex, ey = qx - px, qy - py
    det = dx * ey - dy * ex
    if abs(det) < 1e-12:
        return False
    u = (ex * (ay - py) - ey * (ax - px)) / det
    t = (dy * (px - ax) - dx * (py - ay)) / det
    return (-1e-9) <= t <= (1 + 1e-9) and (-1e-9) <= u <= (1 + 1e-9)


def sample_ring(ring, step):
    """沿 LinearRing/LineString 按步长采样点 (x,y)。"""
    coords = list(ring.coords)
    pts = []
    for i in range(len(coords) - 1):
        x0, y0 = coords[i][0], coords[i][1]
        x1, y1 = coords[i + 1][0], coords[i + 1][1]
        seglen = math.hypot(x1 - x0, y1 - y0)
        if seglen < 1e-9:
            continue
        n = max(1, int(seglen / step))
        for k in range(n + 1):
            f = k / n
            pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    ap.add_argument("--step", type=float, default=0.3)
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))

    # 备份
    bak = p.with_name(p.stem + "_before_vis" + p.suffix)
    bak.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")
    print("备份 ->", bak)

    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        fd = geo["floors"][fk]
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}

        room_poly = {}
        room_centroid = {}
        for n in nodes:
            if n["type"] == "room":
                rid = n.get("roomId")
                if rid:
                    room_centroid[rid] = n["coordinates"]
        for r in fd["geometry"].get("rooms", []):
            try:
                room_poly[r["id"]] = shape(r["geometry"])
            except Exception:
                pass

        # 墙体 STRtree（中心线）
        wall_list = []
        for w in fd["geometry"].get("walls", []):
            g = w.get("geometry", {})
            if g.get("type") == "LineString":
                ls = LineString(g["coordinates"])
                if ls.length < 1e-6:
                    continue
                wall_list.append(ls)
        tree = STRtree(wall_list) if wall_list else None

        def query_walls(seg_bbox, margin=0.3):
            if tree is None:
                return []
            minx, miny, maxx, maxy = seg_bbox
            box = Polygon([(minx - margin, miny - margin), (maxx + margin, miny - margin),
                           (maxx + margin, maxy + margin), (minx - margin, maxy + margin)])
            idx = tree.query(box)
            return [wall_list[i] for i in idx]

        # 每扇门连接的房间集合
        td_rooms = {}
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                continue
            if a["type"] == "doorway" and b["type"] == "room":
                td_rooms.setdefault(a["id"], set()).add(b.get("roomId"))
            elif b["type"] == "doorway" and a["type"] == "room":
                td_rooms.setdefault(b["id"], set()).add(a.get("roomId"))

        moved = 0
        unfixed = 0
        for n in nodes:
            if n["type"] != "doorway":
                continue
            rset = set(td_rooms.get(n["id"], set()))
            # 补充 td.rooms 字段中确实存在质心节点的房间
            for rid in (n.get("rooms") or []):
                if rid in room_centroid:
                    rset.add(rid)
            rset = {rid for rid in rset if rid in room_poly and rid in room_centroid}
            if not rset:
                continue  # 无归属房间，跳过

            # 候选点：各归属房间自身边界采样
            cands = {}
            for rid in rset:
                poly = room_poly[rid]
                for ring in [poly.exterior] + list(poly.interiors):
                    for pt in sample_ring(ring, args.step):
                        key = (round(pt[0], 2), round(pt[1], 2))
                        cands[key] = pt
            # 也把原 TD 坐标作为候选
            cands[(round(n["coordinates"][0], 2), round(n["coordinates"][1], 2))] = tuple(n["coordinates"])

            cur = tuple(n["coordinates"])

            def is_valid(P):
                # 字面要求：房间质心↔门 不「穿墙」（端点落墙不算）。
                # 不要求整段落在多边形内（非凸房间门在远臂时直线会越过凹口但不穿墙，
                # 属合法；仅当凹口恰被另一房间墙填充时才会触发严格穿墙，被正确拒绝）。
                for rid in rset:
                    c = room_centroid[rid]
                    seg = LineString([c, P])
                    for w in query_walls(seg.bounds):
                        wc = w.coords
                        if seg_crosses_wall_strict(c, P, wc[0], wc[-1]):
                            return False
                return True

            valid = []
            for P in cands.values():
                if is_valid(P):
                    # 是否「落在其全部归属房间多边形内」(优先，避免边划出房间轮廓)
                    all_covered = True
                    for rid in rset:
                        c = room_centroid[rid]
                        if not room_poly[rid].covers(LineString([c, P])):
                            all_covered = False
                            break
                    valid.append((0 if all_covered else 1,
                                  math.hypot(P[0] - cur[0], P[1] - cur[1]), P))
            if valid:
                valid.sort(key=lambda x: (x[0], x[1]))
                best = valid[0][2]
                moved += 1
            else:
                # best-effort：满足最多房间且距原位置最近的点
                best_per_room = {}
                for P in cands.values():
                    ok = 0
                    for rid in rset:
                        c = room_centroid[rid]
                        seg = LineString([c, P])
                        cross = False
                        for w in query_walls(seg.bounds):
                            wc = w.coords
                            if seg_crosses_wall_strict(c, P, wc[0], wc[-1]):
                                cross = True
                                break
                        if not cross:
                            ok += 1
                    cost = math.hypot(P[0] - cur[0], P[1] - cur[1])
                    best_per_room.setdefault(ok, []).append((cost, P))
                sorted_ok = sorted(best_per_room.keys(), reverse=True)
                if sorted_ok:
                    bucket = sorted(best_per_room[sorted_ok[0]])
                    best = bucket[0][1]
                    unfixed += 1
                    moved += 1
                else:
                    best = cur
            n["coordinates"] = [round(best[0], 3), round(best[1], 3)]

        # 重算边距离 / 时间
        for e in edges:
            a = nmap.get(e["from"]); b = nmap.get(e["to"])
            if not a or not b:
                continue
            d = math.hypot(a["coordinates"][0] - b["coordinates"][0],
                           a["coordinates"][1] - b["coordinates"][1])
            e["distance"] = round(float(d), 2)
            e["estimatedTime"] = round(float(d) / 0.8, 1)

        fd["topology"]["nodes"] = nodes
        fd["topology"]["edges"] = edges
        print(f"F{fk}: TD 重排={moved} (best-effort={unfixed})")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
