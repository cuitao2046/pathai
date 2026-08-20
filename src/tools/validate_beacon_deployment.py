# -*- coding: utf-8 -*-
"""
信标部署方案 QA 校验器（正式脚本，非调试脚本；调试脚本才放 debug/）。

数据源：
  - result/ble_deployment.json                     （61 枚信标人工部署方案）
  - result/school_building_01_map_v9.geojson       （v9 楼层拓扑/房间/楼梯）

校验规则（阈值常量见下方 T_*/ROOM_BUF，可调）：
  R1 [ERROR] sourceNodeId 非空 → coords↔sourceNodeId 距离 > T_SRC_DRIFT（部署真值偏离
             拓扑归属节点；阈值与吸附阈值一致，杜绝「10m 吸附 5m 归属」被 snap 豁免掩盖）
  R4 [WARN ] 距最近楼梯 ≤ T_STAIR 或命中 staircase/stair_lobby 房间的信标，
             locationDesc/subType 缺少楼梯关键词（楼梯口语义缺失）
  R5 [ERROR] 缺失 beaconId / coordinates / floor 任一必填字段

注：方案 B 单坐标 schema（2026-08-20 起）：coordinates 为唯一真值，
    plannedCoordinates/snapDist_m 字段已废弃 → R2（planned 脱节）/ R3（人工信标同步）
    一并删除；R1 改为直接比较 coords↔sourceNodeId（不再有 snap_ok 豁免——
    旧逻辑 planned:=coords 后 snap_ok 恒真，5m 级归属永远查不出来）。

注：R1 阈值与渲染图 deployResnap 吸附阈值（3m）一致，保证「渲染图重吸附后的信标
    必然通过校验」与「超过吸附范围的漂移必然报错」双向闭环。

输出：逐条明细 `[R1][ERROR] BK-01-008: coords↔sourceNodeId=5.06m` + 汇总计数；
      任一 ERROR → exit code 1，否则 exit 0。

判定逻辑参照 debug/audit_beacon_semantics.py 的 room_of / nearest_dist（0.5m 房间命中缓冲）。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe src/tools/validate_beacon_deployment.py
  （依赖 shapely/numpy，已随项目环境安装；路径基于 __file__ 推导，禁止 hardcode）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

# ---------- 阈值常量（可调） ----------
T_SRC_DRIFT = 3.0        # R1：coords↔sourceNodeId 允许最大距离（m），与吸附阈值一致
                         #   —— 渲染图 deployResnap ≤3m 类型加权吸附；超过 3m 归属视为漂移
T_STAIR = 6.0            # R4：距最近楼梯判定阈值（m）
ROOM_BUF = 0.5           # 房间命中缓冲（墙上安装点落多边形边界）
STAIR_ROOM_TYPES = ("staircase", "stair_lobby")   # R4：楼梯语义房间类型
STAIR_KEYWORDS = ("楼梯", "staircase", "stair", "ST-")  # R4：楼梯关键词（大小写不敏感）
REQUIRED_FIELDS = ("beaconId", "coordinates", "floor")  # R5：必填字段

# ---------- 路径推导（项目惯例：基于 __file__，不 hardcode） ----------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BEACON_JSON = BASE_DIR / "result" / "ble_deployment.json"
MAP_JSON = BASE_DIR / "result" / "school_building_01_map_v9.geojson"


def load_data():
    """加载信标部署方案与 v9 地图 GeoJSON。"""
    beacons = json.load(open(BEACON_JSON, encoding="utf-8"))["beacons"]
    geo = json.load(open(MAP_JSON, encoding="utf-8"))
    return beacons, geo


def build_ctx(geo, fl):
    """楼层上下文：房间多边形（含 STRtree）、楼梯点、拓扑节点索引（参照 audit_beacon_semantics）。"""
    fd = geo["floors"][str(fl)]
    gd = fd["geometry"]
    rooms = []
    for r in gd["rooms"]:
        ring = r["geometry"]["coordinates"][0]
        p = r["properties"]
        rooms.append({
            "id": p.get("roomId") or r["id"],
            "label": p.get("label", ""),
            "roomType": p.get("roomType", ""),
            "poly": Polygon(ring),
        })
    room_tree = STRtree([x["poly"] for x in rooms])
    stairs = [Point(s["properties"]["centroid"]) for s in gd.get("stairs", [])]
    nodes = {n["id"]: n for n in fd["topology"]["nodes"]}
    return rooms, room_tree, stairs, nodes


def room_of(p, rooms, tree):
    """命中房间（含 ROOM_BUF 缓冲）：返回最近房间 dict 或 None（参照 audit_beacon_semantics）。"""
    best, best_d = None, 1e9
    for r in rooms:
        d = r["poly"].distance(p)
        if d < best_d:
            best_d, best = d, r
    return best if best_d <= ROOM_BUF else None


def nearest_dist(p, pts):
    """到最近点集合的距离；空集合返回 None（参照 audit_beacon_semantics）。"""
    if not pts:
        return None
    return min(p.distance(x) for x in pts)


def _num(v):
    """安全取数值；缺失/非法返回 None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _has_stair_keyword(beacon):
    """locationDesc/subType 是否含楼梯关键词（大小写不敏感）。"""
    text = " ".join(str(beacon.get(k) or "") for k in ("locationDesc", "subType"))
    low = text.lower()
    return any(kw.lower() in low for kw in STAIR_KEYWORDS)


def validate_beacon(beacon, ctx):
    """对单枚信标执行 R1~R5，返回 [(rule, level, message), ...]。"""
    rooms, tree, stairs, nodes = ctx
    out = []
    bid = beacon.get("beaconId") or "?"

    # R5：必填字段
    missing = [k for k in REQUIRED_FIELDS
               if k not in beacon or beacon[k] in (None, "")]
    coords = beacon.get("coordinates")
    bad_coords = (not missing and not (isinstance(coords, (list, tuple)) and len(coords) >= 2
                                       and all(_num(v) is not None for v in coords[:2])))
    if missing or bad_coords:
        detail = []
        if missing:
            detail.append(f"缺失必填字段: {', '.join(missing)}")
        if bad_coords:
            detail.append("coordinates 非法（需 [x, y] 数值列表）")
        out.append(("R5", "ERROR", "; ".join(detail)))
        return out  # 必填字段缺失/非法时后续几何校验无意义，直接返回

    p = Point(_num(coords[0]), _num(coords[1]))

    # R1：sourceNodeId 非空 → coords↔源节点距离（直接比较，无 snap 豁免）
    #     —— 方案 B 单坐标 schema 下 coordinates 为唯一真值，超过吸附阈值即漂移
    sid = beacon.get("sourceNodeId") or ""
    if sid:
        node = nodes.get(sid)
        if node is not None:
            d = p.distance(Point(node["coordinates"]))
            if d > T_SRC_DRIFT:
                out.append(("R1", "ERROR",
                            f"coords↔sourceNodeId={d:.2f}m（> {T_SRC_DRIFT}m，部署真值偏离归属节点）"))
        # sourceNodeId 在拓扑中缺失：不属 R1 定义范围，跳过（由语义审计另行核验）

    # R4：楼梯语义区（距楼梯 ≤ T_STAIR 或命中 staircase/stair_lobby）→ 声明缺楼梯关键词
    r = room_of(p, rooms, tree) if rooms is not None else None
    rtype = r["roomType"] if r else ""
    ds = nearest_dist(p, stairs) if stairs else None
    in_stair_area = (rtype in STAIR_ROOM_TYPES) or (ds is not None and ds <= T_STAIR)
    if rooms is not None and in_stair_area and not _has_stair_keyword(beacon):
        hit = f"房间={rtype}" if rtype in STAIR_ROOM_TYPES else f"距最近楼梯={ds:.1f}m"
        out.append(("R4", "WARN",
                    f"楼梯口语义缺失（{hit}，locationDesc/subType 无楼梯关键词）"))

    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not BEACON_JSON.exists() or not MAP_JSON.exists():
        print(f"[fatal] 数据文件缺失：{BEACON_JSON} / {MAP_JSON}")
        return 2

    beacons, geo = load_data()
    ctxs = {fl: build_ctx(geo, fl) for fl in geo["floors"].keys()}

    counts = {"ERROR": 0, "WARN": 0}
    by_rule = {}
    for beacon in beacons:
        fl = str(beacon.get("floor"))
        if fl not in ctxs:
            counts["WARN"] += 1
            print(f"[R5][WARN] {beacon.get('beaconId') or '?'}: floor={fl} 不在地图楼层（{'/'.join(sorted(ctxs))}），跳过几何校验")
            continue
        results = validate_beacon(beacon, ctxs[fl])
        for rule, level, msg in results:
            counts[level] += 1
            by_rule.setdefault(rule, {level: 0})
            by_rule[rule][level] = by_rule[rule].get(level, 0) + 1
            bid = beacon.get("beaconId") or "?"
            print(f"[{rule}][{level}] {bid}: {msg}")

    print("-" * 70)
    print(f"汇总: 共 {len(beacons)} 枚信标")
    for rule in ("R1", "R4", "R5"):
        parts = [f"{lv}={by_rule.get(rule, {}).get(lv, 0)}" for lv in ("ERROR", "WARN")]
        print(f"  {rule}: " + " | ".join(parts))
    print(f"合计: ERROR={counts['ERROR']}  WARN={counts['WARN']}")
    if counts["ERROR"]:
        print(f"校验未通过（存在 {counts['ERROR']} 个 ERROR）→ exit 1")
        return 1
    print("校验通过（无 ERROR）→ exit 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
