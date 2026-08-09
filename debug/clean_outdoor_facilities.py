# -*- coding: utf-8 -*-
"""户外设施清洗 v2：用户规则「户外设施不用考虑，所有元素只针对室内」。

判定：facility 节点坐标距室内并集（房间 union）> 1.5m 且类型为 staircase
      → 视为户外楼梯，剔除（电梯距室内 1.19m 属井道多边形缺失，保留）。
- 剔除户外 facility 节点及其拓扑边；
- 跨层边若引用被剔除节点：对端同 code 设施存活则改挂，否则删除；
- 关联 staircase/infrastructure TR 若失联 → 兜底连最近室内节点。

用法: python debug/clean_outdoor_facilities.py [geojson]
"""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
from shapely.geometry import shape, Point
from shapely.ops import unary_union

BASE = Path(__file__).resolve().parent.parent
OUTDOOR_TOL = 1.5  # 距室内 >1.5m 视为户外


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson", nargs="?",
                    default=str(BASE / "result" / "school_building_01_map_v9.geojson"))
    args = ap.parse_args()
    p = Path(args.geojson)
    geo = json.loads(p.read_text(encoding="utf-8"))

    indoor = {}
    for fk, fd in geo["floors"].items():
        polys = []
        for r in fd.get("geometry", {}).get("rooms", []):
            g = r.get("geometry", {})
            if g and g.get("type") in ("Polygon", "MultiPolygon"):
                try: polys.append(shape(g))
                except Exception: pass
        indoor[fk] = unary_union(polys) if polys else None

    removed = set()
    for fk, fd in geo["floors"].items():
        indoor_union = indoor.get(fk)
        out_fac = []
        for n in fd["topology"]["nodes"]:
            if n["type"] != "facility":
                continue
            # 仅剔除 staircase（户外楼梯）；电梯井道多边形缺失导致的 1.2m 偏差保留
            if n.get("facilityType") != "staircase":
                continue
            pt = Point(n["coordinates"])
            d = indoor_union.distance(pt) if indoor_union else 0.0
            if d > OUTDOOR_TOL:
                out_fac.append((n["id"], n.get("label"), round(d, 2)))
        if not out_fac:
            continue
        for nid, label, d in out_fac:
            removed.add(nid)
            print(f"  F{fk} 户外楼梯: {nid} {label} 距室内={d}m")
            fd["topology"]["nodes"] = [n for n in fd["topology"]["nodes"]
                                       if n["id"] != nid]
            fd["topology"]["edges"] = [e for e in fd["topology"]["edges"]
                                       if e["from"] != nid and e["to"] != nid]

    # 跨层边处理
    code_map = {}
    for fk, fd in geo["floors"].items():
        for n in fd["topology"]["nodes"]:
            if n["type"] == "facility":
                code_map.setdefault((n.get("label"), fk), n["id"])
    cf = geo.get("crossFloorEdges") or []
    new_cf = []
    for e in cf:
        if e.get("from") in removed or e.get("to") in removed:
            code = e.get("code")
            other_fk = e.get("toFloor") if e.get("from") in removed else e.get("fromFloor")
            if code:
                cand = code_map.get((code, str(other_fk)))
                if cand:
                    ne = dict(e)
                    if e.get("from") in removed:
                        ne["from"] = cand
                    else:
                        ne["to"] = cand
                    new_cf.append(ne)
                    print(f"  跨层 {e['id']} 改挂 -> {cand}")
                    continue
            print(f"  跨层 {e['id']} 删除（{code}）")
            continue
        new_cf.append(e)
    geo["crossFloorEdges"] = new_cf

    # 失联 TR 兜底
    OPENP = {"corridor", "lobby", "activity", "atrium", "elevator_lobby",
             "stair_lobby", "staircase", "infrastructure", "elevator_hall"}
    for fk, fd in geo["floors"].items():
        nodes = fd["topology"]["nodes"]
        edges = fd["topology"]["edges"]
        nmap = {n["id"]: n for n in nodes}
        tr_edges = {}
        for e in edges:
            a, b = nmap.get(e["from"], {}), nmap.get(e["to"], {})
            if a.get("type") == "room":
                tr_edges.setdefault(a.get("roomId"), set()).add(e["to"])
            elif b.get("type") == "room":
                tr_edges.setdefault(b.get("roomId"), set()).add(e["from"])
        def rtype(rid):
            for r in fd.get("geometry", {}).get("rooms", []):
                if r["id"] == rid:
                    return r.get("properties", {}).get("roomType") or r.get("type")
            return None
        cands = [n for n in nodes if n["type"] in ("facility", "doorway", "facility_entrance")]
        edge_counter = max([int(e["id"].split("E")[1].split("-")[0]) for e in edges
                            if e["id"].startswith("E") and e["id"][1].isdigit()], default=0)
        fixed = 0
        for n in nodes:
            if n["type"] != "room" or not n.get("roomId"):
                continue
            rid = n["roomId"]
            if tr_edges.get(rid):
                continue
            if rtype(rid) not in ("staircase", "infrastructure"):
                continue
            c = n["coordinates"]
            best, bd = None, 1e9
            for cn in cands:
                dd = math.hypot(c[0]-cn["coordinates"][0], c[1]-cn["coordinates"][1])
                if dd < bd: bd, best = dd, cn["id"]
            if best:
                edge_counter += 1
                d = round(bd, 2)
                edges.append({"id": f"E{edge_counter:06d}", "from": n["id"], "to": best,
                              "distance": d, "estimatedTime": round(d/0.8, 1),
                              "accessibilityLevel": 0, "riskLevel": 0.5,
                              "walkable": True, "wheelchairAccessible": True,
                              "blindAccessible": True, "crossFloor": False})
                fixed += 1
        if fixed:
            fd["topology"]["edges"] = edges
            print(f"  F{fk} 兜底连接失联楼梯间 {fixed}")

    p.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写回:", p)


if __name__ == "__main__":
    main()
