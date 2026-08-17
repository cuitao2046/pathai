# -*- coding: utf-8 -*-
"""
信标部署语义审计：61 信标的信息（locationDesc / subType / sourceNodeId）与实际部署坐标
在地图上的语义（房间类型 / 楼梯 / 电梯 / 门 / 交叉口）是否一致。

核验四层：
  A. 源节点一致性：sourceNodeId 是否存在于对应楼层 topology.nodes？节点坐标 ↔ 信标坐标
     距离是否 ≈ snapDist_m？sourceNodeType 是否与节点实际 type 一致？
  B. 坐标地图语义：信标坐标命中哪个房间（type=corridor/staircase/stair_lobby/elevator_lobby/
     lobby/toilet/room/infrastructure）；到最近楼梯/电梯/电梯门/TD门/TI交叉口的距离。
  C. 声明 vs 实际匹配：locationDesc/subType/sourceNodeType 关键词 vs 实际语义
     → MATCH / CHECK / MISMATCH / NODESC。
  D. 语义盲区扫描：地图上楼梯/电梯/电梯厅等语义点周边是否有信标覆盖；部署声明是否缺失楼梯。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe debug/audit_beacon_semantics.py
"""
import json, math, sys
import numpy as np
from shapely.geometry import Point, LineString, Polygon
from shapely.ops import nearest_points
from shapely.strtree import STRtree

BEACON_JSON = "result/ble_deployment.json"
MAP_JSON = "result/school_building_01_map_v9.geojson"

# 判定阈值
T_ELEV = 5.0     # 电梯口：距电梯/电梯门 ≤5m 或命中 elevator_lobby
T_STAIR = 6.0    # 楼梯口：距楼梯 ≤6m 或命中 staircase/stair_lobby
T_DOOR = 3.0     # 门口：距最近 TD 门节点 ≤3m
T_TI = 10.0      # 交叉口：命中开放空间或距最近 TI ≤10m
ROOM_BUF = 0.5   # 房间命中缓冲（墙上安装点落多边形边界）


def load_data():
    b = json.load(open(BEACON_JSON, encoding="utf-8"))["beacons"]
    g = json.load(open(MAP_JSON, encoding="utf-8"))
    return b, g


def build_ctx(g, fl):
    """楼层上下文：房间多边形、楼梯/电梯/电梯门、拓扑节点索引。"""
    geo = g["floors"][str(fl)]["geometry"]
    topo = g["floors"][str(fl)]["topology"]["nodes"]
    rooms = []
    for r in geo["rooms"]:
        ring = r["geometry"]["coordinates"][0]
        poly = Polygon(ring)
        p = r["properties"]
        rooms.append({"id": p.get("roomId") or r["id"], "label": p.get("label", ""),
                      "type": p.get("type", ""), "roomType": p.get("roomType", ""),
                      "poly": poly})
    room_tree = STRtree([x["poly"] for x in rooms])
    stairs = [{"id": s["id"], "label": s["properties"].get("label", ""),
               "pt": Point(s["properties"]["centroid"])} for s in geo["stairs"]]
    elevs = [{"id": e["id"], "label": e["properties"].get("label", ""),
              "pt": Point(e["properties"]["centroid"])} for e in geo["elevators"]]
    eldoors = []
    for d in geo["elevatorDoors"]:
        ax = d["properties"].get("axis")
        if ax and len(ax) >= 2:
            eldoors.append({"id": d["id"],
                            "label": d["properties"].get("elevatorLabel", ""),
                            "line": LineString(ax)})
    nodes = {n["id"]: n for n in topo}
    return rooms, room_tree, stairs, elevs, eldoors, nodes


def room_of(p, rooms, tree):
    """命中房间（含 0.5m 缓冲）：返回最近房间 dict 或 None。"""
    best, best_d = None, 1e9
    for i, r in enumerate(rooms):
        d = r["poly"].distance(p)
        if d < best_d:
            best_d, best = d, r
    return best if best_d <= ROOM_BUF else None


def nearest_dist(p, pts):
    if not pts:
        return None
    return min(p.distance(x) for x in pts)


def describe_actual(p, ctx):
    """坐标的实际语义描述：命中房间 + 最近设施距离。"""
    rooms, tree, stairs, elevs, eldoors, nodes = ctx
    r = room_of(p, rooms, tree)
    ds = nearest_dist(p, [s["pt"] for s in stairs])
    de = nearest_dist(p, [e["pt"] for e in elevs])
    ded = nearest_dist(p, [d["line"] for d in eldoors]) if eldoors else None
    return r, ds, de, ded


def classify(beacon, p, ctx):
    """声明语义 vs 实际语义。返回 (verdict, detail)。"""
    rooms, tree, stairs, elevs, eldoors, nodes = ctx
    decl = beacon.get("locationDesc", "") or ""
    sub = beacon.get("subType", "") or ""
    stype = beacon.get("sourceNodeType", "") or ""
    r, ds, de, ded = describe_actual(p, ctx)
    rtype = r["roomType"] if r else ""
    rlabel = r["label"] if r else ""

    is_elev_area = (rtype == "elevator_lobby") or (de is not None and de <= T_ELEV) or (ded is not None and ded <= T_ELEV)
    is_stair_area = (rtype in ("staircase", "stair_lobby")) or (ds is not None and ds <= T_STAIR)
    is_open = rtype in ("corridor", "lobby")

    # 楼梯口/楼梯厅：无论声明如何，先记录实际语义（盲区扫描用）
    stair_note = ""
    if is_stair_area:
        stair_note = f"【楼梯语义区: 房间={rtype} ds={ds:.1f}m】"

    # 1) 人工部署（无源节点）→ 只报告实际语义
    if not stype and not sub:
        return "NODESC", f"无声明; 实际={rtype or '?'}/{rlabel or '无房间'} 楼梯{ds is not None and f'{ds:.1f}m' or '-'} 电梯{de is not None and f'{de:.1f}m' or '-'}{stair_note}"
    # 2) 电梯口
    if sub == "elevator_door" or "电梯" in decl:
        if is_elev_area:
            return "MATCH", f"电梯口✓ rtype={rtype} de={de is not None and f'{de:.1f}m' or '-'} ded={ded is not None and f'{ded:.1f}m' or '-'}"
        return "MISMATCH", f"声明电梯口但实际 rtype={rtype} de={de is not None and f'{de:.1f}m' or '-'} ded={ded is not None and f'{ded:.1f}m' or '-'}"
    # 3) 门口
    if stype == "doorway" or "门口" in decl:
        td_d = min((p.distance(Point(n["coordinates"])) for n in nodes.values() if n["type"] == "doorway"), default=1e9)
        if td_d <= T_DOOR:
            return "MATCH", f"门口✓ 最近TD={td_d:.1f}m rtype={rtype}"
        return "MISMATCH", f"声明门口但最近TD={td_d:.1f}m rtype={rtype}"
    # 4) 交叉口
    if stype == "intersection" or "交叉口" in decl:
        ti_d = min((p.distance(Point(n["coordinates"])) for n in nodes.values() if n["type"] == "intersection"), default=1e9)
        if is_open or ti_d <= T_TI:
            return "MATCH", f"交叉口✓ 最近TI={ti_d:.1f}m rtype={rtype}"
        return "CHECK", f"交叉口但实际 rtype={rtype} 最近TI={ti_d:.1f}m"
    # 5) 路线/测试路径补点
    if stype in ("route_corridor", "route_fill") or "路线" in decl or "测试路径" in decl:
        if is_open:
            return "MATCH", f"走廊补点✓ rtype={rtype}"
        return "CHECK", f"补点但实际 rtype={rtype}{stair_note}"
    return "CHECK", f"未分类 实际 rtype={rtype}{stair_note}"


def main():
    beacons, g = load_data()
    ctxs = {fl: build_ctx(g, fl) for fl in ("1", "2")}
    rows = []
    n_stat = {"MATCH": 0, "CHECK": 0, "MISMATCH": 0, "NODESC": 0}
    stair_adj = []      # 实际在楼梯语义区的信标（无论声明）
    no_room = []        # 未命中任何房间
    coord_anom = []     # 坐标/规划/源节点 三者矛盾的硬伤
    cross_mis = []      # 声明交叉口号 ≠ 实际最近交叉口号

    for x in beacons:
        fl = str(x["floor"])
        ctx = ctxs[fl]
        rooms, tree, stairs, elevs, eldoors, nodes = ctx
        p = Point(x["coordinates"])
        r, ds, de, ded = describe_actual(p, ctx)
        # A. 源节点核验
        sid = x.get("sourceNodeId") or ""
        src_ok, src_d = "-", None
        pl = x.get("plannedCoordinates")
        pl_d = p.distance(Point(pl)) if pl else None
        snap = x.get("snapDist_m") or 0
        snap_ok = "OK" if (pl_d is not None and abs(pl_d - snap) <= 0.5) else "BAD"
        if sid:
            n = nodes.get(sid)
            if n:
                src_d = p.distance(Point(n["coordinates"]))
                src_ok = "OK" if abs(src_d - snap) <= 2.0 else "DIFF"
            else:
                src_ok = "缺失"
        rtype = r["roomType"] if r else "-"
        rlabel = r["label"] if r else "-"
        ver, detail = classify(x, p, ctx)
        n_stat[ver] += 1
        if (ds is not None and ds <= T_STAIR) or rtype in ("staircase", "stair_lobby"):
            stair_adj.append((x["beaconId"], rtype, f"{ds:.1f}m" if ds is not None else "-"))
        if r is None:
            no_room.append(x["beaconId"])
        # 硬伤：coords 与 planned/snap 矛盾（>5m 且 snap 对不上）
        if pl_d is not None and pl_d > 5.0 and snap_ok == "BAD":
            coord_anom.append((x["beaconId"], f"coords↔planned={pl_d:.1f}m snap={snap}"))
        # 交叉口号声明 vs 实际最近 TI 号
        m = __import__("re").search(r"交叉口(\d+)", x.get("locationDesc", "") or "")
        if m:
            tis = sorted([(p.distance(Point(nn["coordinates"])), nn.get("label", ""))
                          for nn in nodes.values() if nn["type"] == "intersection"])
            if tis:
                d_ti, lbl = tis[0]
                if lbl != f"交叉口{m.group(1)}" and d_ti <= 10.0:
                    cross_mis.append((x["beaconId"], f"声明=交叉口{m.group(1)} 实际最近={lbl}@{d_ti:.1f}m"))
        rows.append((x["beaconId"], fl, x.get("sourceNodeType") or "-", x.get("subType") or "-",
                     src_ok, f"{src_d:.1f}" if src_d is not None else "-",
                     f"{snap}", f"{pl_d:.1f}" if pl_d is not None else "-", snap_ok,
                     rtype, f"{ds:.1f}" if ds is not None else "-",
                     f"{de:.1f}" if de is not None else "-",
                     f"{ded:.1f}" if ded is not None else "-",
                     ver, detail))

    print("=" * 108)
    print("信标部署语义审计（61 信标）—— locationDesc/sourceNodeId vs 坐标地图语义")
    print("=" * 108)
    hdr = (f"{'ID':<14}{'F':<3}{'srcType':<14}{'src':<5}{'srcD':>5}{'snap':>5}{'plD':>5}{'snapOK':<6}"
           f"{'roomType':<12}{'stair':>6}{'elev':>5}{'eDoor':>6}  判定/说明")
    print(hdr)
    print("-" * 108)
    for r_ in rows:
        print(f"{r_[0]:<14}{r_[1]:<3}{r_[2]:<14}{r_[3]:<5}{r_[4]:>5}{r_[5]:>5}{r_[6]:>5}{r_[7]:<6}{r_[8]:<12}{r_[9]:>6}{r_[10]:>5}{r_[11]:>6}  {r_[12]:<8}{r_[13]}")
    print("-" * 108)
    print(f"判定汇总: MATCH={n_stat['MATCH']}  CHECK={n_stat['CHECK']}  MISMATCH={n_stat['MISMATCH']}  NODESC={n_stat['NODESC']}")
    print(f"未命中任何房间的信标: {no_room if no_room else '无'}")
    print(f"\n== 硬伤：坐标↔规划/吸附字段矛盾（>5m 且 snap 对不上）== {len(coord_anom)} 个")
    for i in coord_anom:
        print(f"  {i[0]}: {i[1]}")
    print(f"\n== 声明交叉口号 ≠ 实际最近交叉口号 == {len(cross_mis)} 个")
    for i in cross_mis:
        print(f"  {i[0]}: {i[1]}")
    print(f"\n== 楼梯语义区信标（距楼梯≤{T_STAIR}m 或命中 staircase/stair_lobby）==: {len(stair_adj)} 个")
    for sid_, rt, d in stair_adj:
        print(f"  {sid_:<14} 房间={rt:<14} 楼梯距离={d}")
    # 语义盲区扫描：楼梯设施周边是否有信标
    for fl in ("1", "2"):
        rooms, tree, stairs, elevs, eldoors, nodes = ctxs[fl]
        for s_ in stairs:
            near = [x["beaconId"] for x in beacons if str(x["floor"]) == fl
                    and Point(x["coordinates"]).distance(s_["pt"]) <= T_STAIR]
            if not near:
                print(f"  [盲区] F{fl} 楼梯 {s_['id']} ({s_['label']}) 周边 {T_STAIR}m 无信标")
        for e_ in elevs:
            near = [x["beaconId"] for x in beacons if str(x["floor"]) == fl
                    and Point(x["coordinates"]).distance(e_["pt"]) <= T_ELEV]
            if not near:
                print(f"  [盲区] F{fl} 电梯 {e_['id']} ({e_['label']}) 周边 {T_ELEV}m 无信标")


if __name__ == "__main__":
    main()
