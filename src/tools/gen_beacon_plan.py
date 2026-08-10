#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_beacon_plan.py — 依据 docs/07-信标部署方案.md 的方案说明，从 v9 楼层 GeoJSON
自动生成「信标部署方案 JSON（新表台账）」。

设计原则（见文档 §二/§三）：
  - 决策节点优先，而非均匀覆盖。重点覆盖安全节点与导航决策点。
  - 现有拓扑节点（topology.nodes）即天然「决策节点」：
        intersection  -> 走廊交叉口 / 转角
        doorway       -> 重要房间门口
        facility      -> 楼梯 / 电梯（最高风险，密集部署）
        facility_entrance -> 建筑出入口
    （room 中心节点不是部署点，排除。）
  - 发射功率按节点类型区分（文档 §4.1）：
        密集 / 高风险节点（楼梯、电梯）降低功率以缩小重叠、提升指纹区分度；
        交叉口为决策密集点，适度降低；门口 / 出入口用开阔走廊功率。

v2 精修（ROI 导向，见 docs/07 + 密度分析）：
  ① 门口只保留「重要房间」（教室/办公室/卫生间/楼梯间/电梯厅等），剔管道井等次要门口；
  ② 高风险节点加密：交叉口四向（沿各连通边偏移）、楼梯入口+预警（入口前3~5m）、
     电梯厅（每个电梯口 + 厅中央）；
  ③ 合并「不同语义且坐标重合 <1.5m」的冗余信标（门口↔交叉口、电梯↔门口），零覆盖损失。

输出 beacon_deployment_plan.json 字段对齐文档 §5.1 台账：
    beacon_id / uuid / major / minor / coordinates / floor / location_desc /
    install_height / tx_power / broadcast_interval / battery_model /
    expected_lifespan / semantic_tag / install_date
并附带 sourceNodeId（可追溯回拓扑节点）、subType（dir/warning/elevator_door/hall_center）等元数据。

用法：
    python src/tools/gen_beacon_plan.py
    python src/tools/gen_beacon_plan.py --geo result/school_building_01_map_v9.geojson \
        --out result/beacon_deployment_plan.json --uuid <UUID> --install-date 2026-08-10
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, OrderedDict, defaultdict
from datetime import date
from pathlib import Path

# ---- 路径解析（与 render_interactive.py 一致：脚本在 src/tools/ 下） ----
ROOT = Path(__file__).resolve().parents[2]          # .../pathai
DEFAULT_GEO = ROOT / "result" / "school_building_01_map_v9.geojson"
DEFAULT_OUT = ROOT / "result" / "beacon_deployment_plan.json"

# ---- 文档 §4.1 / §4.2 默认参数 ----
DEFAULT_UUID = "B9407F30-F5F8-466E-AFF9-25556B57FE6D"   # 场馆维度统一 UUID
DEFAULT_INSTALL_HEIGHT = 2.5      # m
DEFAULT_INTERVAL = 200            # ms
DEFAULT_BATTERY = "CR2477"
DEFAULT_LIFESPAN = 3              # 年
# 发射功率（dBm）：密集/高风险节点降功率以缩小覆盖重叠（文档 §4.1）
TX_DENSE = -12        # 楼梯 / 电梯
TX_INTERSECTION = -10 # 交叉口（决策密集点）
TX_NORMAL = -8        # 门口 / 出入口（开阔走廊）

# 语义 -> 显示名 / Minor 类别基码（文档示例 101 入口 / 102 转角 / 103 交叉口）
SEMANTIC_META = OrderedDict([
    ("entrance",     {"label": "出入口",   "minor_base": 101, "tx": TX_NORMAL}),
    ("door",         {"label": "门口",     "minor_base": 102, "tx": TX_NORMAL}),
    ("intersection", {"label": "交叉口",   "minor_base": 103, "tx": TX_INTERSECTION}),
    ("stair",        {"label": "楼梯",     "minor_base": 104, "tx": TX_DENSE}),
    ("elevator",     {"label": "电梯",     "minor_base": 105, "tx": TX_DENSE}),
])

# 拓扑节点 type -> 语义标签
NODE_TYPE_TO_SEMANTIC = {
    "facility_entrance": "entrance",
    "doorway": "door",
    "intersection": "intersection",
    "facility": "stair",   # facilityType 可能为 staircase / elevator，下方再细分
}

# 基础设施房间类型（管道井/水井/风井等）：纯基础设施门口不部署信标
INFRASTRUCTURE_ROOM_TYPES = {"infrastructure"}

# 户外/建筑出入口类节点（facility_entrance）：属室内外交界设施，按需求不部署信标；
# 文档 §室外预警信标 列为后续独立扩展方向。
OUTDOOR_EXCLUDED_NODE_TYPES = {"facility_entrance"}

# v2 ①：门口只保留「重要房间」类型；其余（基础设施/纯走廊连通）门口不布信标。
IMPORTANT_ROOM_TYPES = {
    "room", "toilet", "staircase", "stair_lobby",
    "elevator_lobby", "lobby", "activity", "atrium",
}

# v2 ②：高风险节点加密的几何偏移
OFFSET_INTERSECTION = 2.5   # m，交叉口四向信标离交叉口距离（沿连通边方向）
OFFSET_WARN = 4.0           # m，楼梯预警信标距楼梯入口距离（沿进入走廊方向）

# v2 ③：合并「不同语义且坐标重合 < 此距离」的冗余信标（门口↔交叉口 / 电梯↔门口）
CONSOLIDATE_DIST = 1.5
# 合并时保留优先级高的语义（高风险/具体 = 优先）
SEM_PRIORITY = {"stair": 5, "elevator": 5, "door": 3, "intersection": 2, "entrance": 1}


def _is_pure_infra_doorway(n: dict, room_type: dict) -> bool:
    """门口连接的房间是否全部为基础设施类型（纯管道井/水井/风井门口）。"""
    rs = n.get("rooms") or []
    return bool(rs) and all(room_type.get(r) in INFRASTRUCTURE_ROOM_TYPES for r in rs)


def _doorway_open_key(coord, q: float = 1.0):
    """门口坐标量化到 q 米网格，用于识别「同一物理开口」的多个门口节点。"""
    if not coord or len(coord) < 2:
        return None
    return (round(coord[0] / q), round(coord[1] / q))


def _doorway_has_important_room(n: dict, room_type: dict) -> bool:
    """门口是否连接任一『重要房间』类型（v2 ①：只部署重要房间门口信标）。"""
    rs = n.get("rooms") or []
    if not rs:
        return False
    return any(room_type.get(r) in IMPORTANT_ROOM_TYPES for r in rs)


def node_semantic(n: dict) -> str | None:
    """把拓扑节点映射为信标语义标签；room 中心节点返回 None（不部署）。"""
    t = n.get("type")
    if t == "room":
        return None
    if t == "facility":
        ft = n.get("facilityType", "staircase")
        return "elevator" if ft == "elevator" else "stair"
    return NODE_TYPE_TO_SEMANTIC.get(t)


def _unit(a, b):
    """由 a 指向 b 的单位向量；a==b 返回 None。"""
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy)
    if d < 1e-9:
        return None
    return dx / d, dy / d


def _compass(u):
    """单位向量 -> 东/西/南/北（粗略方向标注，用于交叉口四向信标）。"""
    if not u:
        return ""
    x, y = u
    if abs(x) >= abs(y):
        return "东" if x > 0 else "西"
    return "北" if y > 0 else "南"


def build_location_desc(floor: int, sem: str, n: dict,
                        sub_type: str | None = None,
                        direction: str | None = None,
                        offset_m: float | None = None) -> str:
    label = n.get("label") or ""
    if sem == "door":
        rooms = n.get("rooms") or []
        rstr = "、".join(rooms) if rooms else "—"
        return f"{floor}F 门口（连接：{rstr}）"
    if sem == "intersection":
        if sub_type == "dir":
            return f"{floor}F 交叉口（{label or '转角'} · {direction or ''}向）"
        return f"{floor}F 交叉口（{label or '转角'}）"
    if sem == "stair":
        if sub_type == "warning":
            return f"{floor}F 楼梯预警（{label or n.get('id','')} · 入口前{offset_m or OFFSET_WARN:.0f}m）"
        return f"{floor}F 楼梯（{label or n.get('id','')}）"
    if sem == "elevator":
        if sub_type == "elevator_door":
            return f"{floor}F 电梯口（{label or n.get('id','')}）"
        if sub_type == "hall_center":
            return f"{floor}F 电梯厅中央（{label or n.get('id','')}）"
        return f"{floor}F 电梯（{label or n.get('id','')}）"
    if sem == "entrance":
        lab = (label or "建筑入口")
        if lab == "出入口":
            lab = "建筑入口"
        return f"{floor}F 出入口（{lab}）"
    return f"{floor}F 信标"


def _make_beacon(floor, sem, coord, src_node, seq, uuid, install_height,
                 interval, install_date, sub_type=None, direction=None,
                 offset_m=None):
    meta = SEMANTIC_META[sem]
    cx, cy = round(float(coord[0]), 3), round(float(coord[1]), 3)
    rows = {
        "beaconId": None,      # 由 _renumber 统一重新编号
        "uuid": uuid,
        "major": floor,
        "minor": meta["minor_base"] * 100 + seq,   # 暂定，_renumber 修正
        "coordinates": [cx, cy],
        "floor": floor,
        "locationDesc": build_location_desc(floor, sem, src_node, sub_type, direction, offset_m),
        "installHeight": install_height,
        "txPower": meta["tx"],
        "broadcastInterval": interval,
        "batteryModel": DEFAULT_BATTERY,
        "expectedLifespan": DEFAULT_LIFESPAN,
        "semanticTag": sem,
        "installDate": install_date,
        "sourceNodeId": src_node.get("id"),
        "sourceNodeType": src_node.get("type"),
        "riskLevel": src_node.get("riskLevel"),
    }
    if sub_type:
        rows["subType"] = sub_type
    if direction:
        rows["direction"] = direction
    if "blindAccessible" in src_node:
        rows["blindAccessible"] = bool(src_node["blindAccessible"])
    if "wheelchairAccessible" in src_node:
        rows["wheelchairAccessible"] = bool(src_node["wheelchairAccessible"])
    if src_node.get("facilityType"):
        rows["facilityType"] = src_node["facilityType"]
    if src_node.get("rooms"):
        rows["adjacentRooms"] = src_node["rooms"]
    return rows


def _consolidate(bs: list) -> list:
    """v2 ③：合并「不同语义 + 坐标重合 < CONSOLIDATE_DIST」的冗余信标，保留优先级高的。

    同语义的相邻信标（如交叉口四向的各方向点）不合并——它们服务不同方向，非冗余。
    """
    n = len(bs)
    drop = set()
    for i in range(n):
        if i in drop:
            continue
        bi = bs[i]
        for j in range(i + 1, n):
            if j in drop:
                continue
            bj = bs[j]
            if bi["semanticTag"] == bj["semanticTag"]:
                continue
            dx = bi["coordinates"][0] - bj["coordinates"][0]
            dy = bi["coordinates"][1] - bj["coordinates"][1]
            if math.hypot(dx, dy) < CONSOLIDATE_DIST:
                pi = SEM_PRIORITY.get(bi["semanticTag"], 0)
                pj = SEM_PRIORITY.get(bj["semanticTag"], 0)
                if pi >= pj:
                    drop.add(j)
                else:
                    drop.add(i)
                    break
    return [b for i, b in enumerate(bs) if i not in drop]


def _renumber(beacons: list):
    """楼层内 beacon_id 连续编号；同语义 minor 类别内序号递增。"""
    floor_seq = defaultdict(int)
    sem_seq = defaultdict(int)
    for b in beacons:
        f = b["floor"]
        s = b["semanticTag"]
        floor_seq[f] += 1
        sem_seq[(f, s)] += 1
        b["beaconId"] = f"BK-{f:02d}-{floor_seq[f]:03d}"
        b["minor"] = SEMANTIC_META[s]["minor_base"] * 100 + sem_seq[(f, s)]


def gen_plan(geo: dict, uuid: str, install_height: float, interval: int,
             install_date: str) -> dict:
    beacons = []
    # 收集所有房间类型（room id 含楼层前缀，全局唯一），用于判定门口重要性
    room_type = {}
    for _fk in geo["floors"].values():
        for r in _fk.get("geometry", {}).get("rooms", []):
            room_type[r.get("id")] = (r.get("properties", {}) or {}).get("type")

    # 门对象属性查表（isNormallyOpen / swingIntoRoom / doorType），用于防火门排除判定
    door_info = {}
    for _fk in geo["floors"].values():
        for d in _fk.get("geometry", {}).get("doors", []):
            pid = d.get("id")
            if pid:
                p = d.get("properties", {}) or {}
                door_info[pid] = {
                    "isNormallyOpen": p.get("isNormallyOpen"),
                    "swingIntoRoom": p.get("swingIntoRoom"),
                    "doorType": p.get("doorType"),
                }

    # 逐楼层处理，保证 beacon_id / minor 在楼层内有序且可复现
    for fk in sorted(geo["floors"].keys(), key=lambda x: int(x)):
        floor = int(fk)
        fdata = geo["floors"][fk]
        topo = fdata.get("topology", {})
        nodes = topo.get("nodes", [])
        edges = topo.get("edges", [])

        # 本层查表：节点坐标、邻接、电梯门、电梯质心
        node_by_id = {n["id"]: n for n in nodes}
        edges_by_node = defaultdict(list)
        for e in edges:
            a, b = e.get("from"), e.get("to")
            if a in node_by_id and b in node_by_id:
                edges_by_node[a].append(b)
                edges_by_node[b].append(a)
        elev_doors = defaultdict(list)
        for ed in fdata.get("geometry", {}).get("elevatorDoors", []):
            elev_doors[ed.get("properties", {}).get("elevatorId")].append(ed)
        elev_centroid = {}
        for el in fdata.get("geometry", {}).get("elevators", []):
            cid = (el.get("properties", {}) or {}).get("centroid")
            if cid:
                elev_centroid[el.get("id")] = cid

        # 同门口(同一物理开口)只布 1 个信标：按坐标量化分组，每组选代表节点部署
        doorway_groups = defaultdict(list)
        for n in nodes:
            if n.get("type") == "doorway":
                k = _doorway_open_key(n.get("coordinates"))
                if k is not None:
                    doorway_groups[k].append(n)
        kept_doorway = set()
        for grp in doorway_groups.values():
            if len(grp) == 1:
                kept_doorway.add(grp[0]["id"])
                continue
            rep = sorted(grp, key=lambda n: (
                1 if _is_pure_infra_doorway(n, room_type) else 0,
                -len(n.get("rooms") or []),
                n["id"],
            ))[0]
            kept_doorway.add(rep["id"])

        cat_seq = Counter()           # semantic -> 楼层内序号
        floor_beacons = []

        def emit(sem, coord, src_node, sub_type=None, direction=None, offset_m=None):
            if not coord or len(coord) < 2:
                return
            cat_seq[sem] += 1
            floor_beacons.append(_make_beacon(
                floor, sem, coord, src_node, cat_seq[sem], uuid,
                install_height, interval, install_date,
                sub_type=sub_type, direction=direction, offset_m=offset_m))

        for n in nodes:
            sem = node_semantic(n)
            if sem is None:
                continue
            # 户外/建筑出入口类节点（室内外交界设施）按需求不部署信标
            if n.get("type") in OUTDOOR_EXCLUDED_NODE_TYPES:
                continue
            # 门口：同开口只留代表；重要房间门口才布；防火门排除
            if n.get("type") == "doorway":
                if n["id"] not in kept_doorway:
                    continue
                if not _doorway_has_important_room(n, room_type):
                    continue
                if n.get("doorType") == "fire":
                    _dis = [door_info[s] for s in (n.get("sourceDoorIds") or [])
                            if s in door_info]
                    _normally_open = any((d or {}).get("isNormallyOpen") is True
                                         for d in _dis)
                    _belongs_room = False
                    if n.get("openDirection") == "inward":
                        for d in _dis:
                            _sir = (d or {}).get("swingIntoRoom")
                            if _sir and room_type.get(_sir) == "room":
                                _belongs_room = True
                                break
                    if (not _normally_open) or _belongs_room:
                        continue
            coord = n.get("coordinates")
            # ---- 电梯厅：厅中央(质心) + 每个电梯口（v2 ②） ----
            if sem == "elevator":
                eid = n.get("elevatorId")
                cc = elev_centroid.get(eid) or coord
                emit("elevator", cc, n, sub_type="hall_center")
                for ed in elev_doors.get(eid, []):
                    coord_ed = (ed.get("geometry", {}) or {}).get("coordinates")
                    emit("elevator", coord_ed, n, sub_type="elevator_door")
                continue
            # ---- 基础信标 ----
            emit(sem, coord, n)
            # ---- 高风险节点加密（v2 ②） ----
            if sem == "intersection":
                # 四向：沿每个连通的走廊/门口/设施边偏移放置方向信标
                for oid in edges_by_node.get(n["id"], []):
                    on = node_by_id.get(oid)
                    if on is None:
                        continue
                    ot = on.get("type")
                    if ot not in ("intersection", "doorway", "facility"):
                        continue
                    u = _unit(coord, on.get("coordinates"))
                    if u is None:
                        continue
                    pos = [coord[0] + u[0] * OFFSET_INTERSECTION,
                           coord[1] + u[1] * OFFSET_INTERSECTION]
                    emit("intersection", pos, n, sub_type="dir", direction=_compass(u))
            elif sem == "stair":
                # 入口(基础) + 预警(入口前 OFFSET_WARN m，沿进入走廊方向)
                for oid in edges_by_node.get(n["id"], []):
                    on = node_by_id.get(oid)
                    if on is None:
                        continue
                    u = _unit(coord, on.get("coordinates"))
                    if u is None:
                        continue
                    pos = [coord[0] + u[0] * OFFSET_WARN,
                           coord[1] + u[1] * OFFSET_WARN]
                    emit("stair", pos, n, sub_type="warning", offset_m=OFFSET_WARN)

        # v2 ③ 合并重合冗余信标（同层内）
        floor_beacons = _consolidate(floor_beacons)
        beacons.extend(floor_beacons)

    _renumber(beacons)
    return beacons


def summarize(beacons: list) -> dict:
    by_floor = Counter(b["floor"] for b in beacons)
    by_sem = Counter(b["semanticTag"] for b in beacons)
    by_sub = Counter((b["semanticTag"], b.get("subType") or "base") for b in beacons)
    return {
        "total": len(beacons),
        "byFloor": {str(k): v for k, v in sorted(by_floor.items())},
        "bySemantic": dict(by_sem),
        "bySubType": {f"{k[0]}/{k[1]}": v for k, v in sorted(by_sub.items())},
    }


def main():
    ap = argparse.ArgumentParser(description="生成信标部署方案 JSON（决策节点优先 + 高风险加密）")
    ap.add_argument("--geo", default=str(DEFAULT_GEO), help="v9 楼层 GeoJSON 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出信标部署方案 JSON")
    ap.add_argument("--uuid", default=DEFAULT_UUID, help="iBeacon UUID（场馆维度统一）")
    ap.add_argument("--install-height", type=float, default=DEFAULT_INSTALL_HEIGHT)
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="广播间隔 ms")
    ap.add_argument("--install-date", default=date.today().isoformat(),
                    help="安装日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--tx-dense", type=int, default=TX_DENSE, help="楼梯/电梯发射功率 dBm")
    ap.add_argument("--tx-intersection", type=int, default=TX_INTERSECTION)
    ap.add_argument("--tx-normal", type=int, default=TX_NORMAL, help="门口/出入口发射功率 dBm")
    args = ap.parse_args()

    # 允许命令行微调发射功率
    SEMANTIC_META["stair"]["tx"] = args.tx_dense
    SEMANTIC_META["elevator"]["tx"] = args.tx_dense
    SEMANTIC_META["intersection"]["tx"] = args.tx_intersection
    SEMANTIC_META["door"]["tx"] = args.tx_normal
    SEMANTIC_META["entrance"]["tx"] = args.tx_normal

    geo_path = Path(args.geo)
    if not geo_path.exists():
        print(f"[error] 找不到 GeoJSON：{geo_path}", file=sys.stderr)
        sys.exit(1)
    geo = json.load(open(geo_path, encoding="utf-8"))

    beacons = gen_plan(geo, args.uuid, args.install_height, args.interval, args.install_date)
    summary = summarize(beacons)

    plan = OrderedDict()
    plan["schemaVersion"] = "1.0"
    plan["generatedBy"] = "gen_beacon_plan.py"
    plan["generatedAt"] = date.today().isoformat()
    plan["venueId"] = geo.get("venueId")
    plan["venueName"] = geo.get("venueName")
    plan["sourceGeojson"] = str(geo_path.name)
    plan["docRef"] = "docs/07-信标部署方案.md"
    plan["uuid"] = args.uuid
    plan["defaultParams"] = {
        "installHeight": args.install_height,
        "broadcastInterval": args.interval,
        "batteryModel": DEFAULT_BATTERY,
        "expectedLifespan": DEFAULT_LIFESPAN,
        "txPowerBySemantic": {k: SEMANTIC_META[k]["tx"] for k in SEMANTIC_META},
        "offsets": {"intersectionDir": OFFSET_INTERSECTION, "stairWarning": OFFSET_WARN},
        "consolidateDist": CONSOLIDATE_DIST,
    }
    plan["strategy"] = {
        "principle": "决策节点优先（非均匀覆盖）；覆盖安全节点与导航决策点",
        "sources": "topology.nodes 中 intersection/doorway/facility；room 中心节点不部署",
        "simplifications": [
            "门口只保留『重要房间』(教室/办公室/卫生间/楼梯间/电梯厅等)，剔管道井等次要门口（v2 ①）",
            "高风险节点加密：交叉口四向（沿各连通边偏移 2.5m）、楼梯入口+预警（入口前 4m）、"
            "电梯厅（每个电梯口 + 厅中央）（v2 ②，对应文档 §3.1/§3.2/§3.3）",
            "合并『不同语义且坐标重合 <1.5m』的冗余信标（门口↔交叉口、电梯↔门口），零覆盖损失（v2 ③）",
            "建筑出入口（facility_entrance）属室内外交界户外设施，按需求不部署信标；室外预警信标为后续独立扩展",
            "纯基础设施门口（管道井/水井/风井等）不部署信标",
            "同一物理门口（坐标量化同开口的多个门口节点）只布 1 个信标，取代表节点",
            "防火门排除：常闭防火门（isNormallyOpen!=True）与内开且归属房间的防火门不部署信标",
        ],
    }
    plan["beacons"] = beacons
    plan["summary"] = summary

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    json.dump(plan, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"[ok] 信标部署方案已生成：{out_path}")
    print(f"  总计 {summary['total']} 个信标")
    print(f"  按楼层：{summary['byFloor']}")
    print(f"  按语义：{summary['bySemantic']}")
    print("  按子类：")
    for k, v in summary["bySubType"].items():
        print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
