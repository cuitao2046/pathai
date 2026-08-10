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

输出 beacon_deployment_plan.json 字段对齐文档 §5.1 台账：
    beacon_id / uuid / major / minor / coordinates / floor / location_desc /
    install_height / tx_power / broadcast_interval / battery_model /
    expected_lifespan / semantic_tag / install_date
并附带 sourceNodeId（可追溯回拓扑节点）与节点原始属性。

用法：
    python src/tools/gen_beacon_plan.py
    python src/tools/gen_beacon_plan.py --geo result/school_building_01_map_v9.geojson \
        --out result/beacon_deployment_plan.json --uuid <UUID> --install-date 2026-08-10
"""
from __future__ import annotations

import argparse
import json
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


def _is_pure_infra_doorway(n: dict, room_type: dict) -> bool:
    """门口连接的房间是否全部为基础设施类型（纯管道井/水井/风井门口）。"""
    rs = n.get("rooms") or []
    return bool(rs) and all(room_type.get(r) in INFRASTRUCTURE_ROOM_TYPES for r in rs)


def _doorway_open_key(coord, q: float = 1.0):
    """门口坐标量化到 q 米网格，用于识别「同一物理开口」的多个门口节点。"""
    if not coord or len(coord) < 2:
        return None
    return (round(coord[0] / q), round(coord[1] / q))


def node_semantic(n: dict) -> str | None:
    """把拓扑节点映射为信标语义标签；room 中心节点返回 None（不部署）。"""
    t = n.get("type")
    if t == "room":
        return None
    if t == "facility":
        ft = n.get("facilityType", "staircase")
        return "elevator" if ft == "elevator" else "stair"
    return NODE_TYPE_TO_SEMANTIC.get(t)


def build_location_desc(floor: int, sem: str, n: dict) -> str:
    label = n.get("label") or ""
    if sem == "door":
        rooms = n.get("rooms") or []
        rstr = "、".join(rooms) if rooms else "—"
        return f"{floor}F 门口（连接：{rstr}）"
    if sem == "intersection":
        return f"{floor}F 交叉口（{label or '转角'}）"
    if sem == "stair":
        return f"{floor}F 楼梯（{label or n.get('id','')}）"
    if sem == "elevator":
        return f"{floor}F 电梯（{label or n.get('id','')}）"
    if sem == "entrance":
        lab = (label or "建筑入口")
        if lab == "出入口":
            lab = "建筑入口"
        return f"{floor}F 出入口（{lab}）"
    return f"{floor}F 信标"


def gen_plan(geo: dict, uuid: str, install_height: float, interval: int,
             install_date: str) -> dict:
    beacons = []
    # 收集所有房间类型（room id 含楼层前缀，全局唯一），用于判定基础设施门口
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
        nodes = geo["floors"][fk].get("topology", {}).get("nodes", [])
        # 同门口(同一物理开口)只布 1 个信标：按坐标量化分组，每组选代表节点部署，
        # 其余重复门口节点跳过（避免一个开口生成多个信标）。
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
            # 多节点同开口：优先非纯基础设施节点、房间数多、id 最小
            rep = sorted(grp, key=lambda n: (
                1 if _is_pure_infra_doorway(n, room_type) else 0,
                -len(n.get("rooms") or []),
                n["id"],
            ))[0]
            kept_doorway.add(rep["id"])
        # 楼层内按语义分组，便于 minor 的类别内序号递增
        cat_seq = Counter()           # semantic -> 楼层内序号
        floor_seq = 0                 # 楼层内信标总序号（beacon_id 用）
        for n in nodes:
            sem = node_semantic(n)
            if sem is None:
                continue
            # 户外/建筑出入口类节点（室内外交界设施）按需求不部署信标
            if n.get("type") in OUTDOOR_EXCLUDED_NODE_TYPES:
                continue
            # 门口：同一物理开口只保留代表节点；纯基础设施门口不部署信标
            if n.get("type") == "doorway":
                if n["id"] not in kept_doorway:
                    continue
                _rooms = n.get("rooms") or []
                if _rooms and all(room_type.get(_rid) in INFRASTRUCTURE_ROOM_TYPES
                                   for _rid in _rooms):
                    continue
                # 防火门排除：
                #  - 常闭防火门（isNormallyOpen != True，默认常闭）不部署；
                #  - 内开且归属房间（swingIntoRoom 为 room 类型）的防火门不部署。
                if n.get("doorType") == "fire":
                    _dis = [door_info[s] for s in (n.get("sourceDoorIds") or [])
                            if s in door_info]
                    _normally_open = any((d or {}).get("isNormallyOpen") is True
                                         for d in _dis)
                    _normally_closed = not _normally_open
                    _belongs_room = False
                    if n.get("openDirection") == "inward":
                        for d in _dis:
                            _sir = (d or {}).get("swingIntoRoom")
                            if _sir and room_type.get(_sir) == "room":
                                _belongs_room = True
                                break
                    if _normally_closed or _belongs_room:
                        continue
            coord = n.get("coordinates")
            if not coord or len(coord) < 2:
                continue
            meta = SEMANTIC_META[sem]
            cat_seq[sem] += 1
            floor_seq += 1
            minor = meta["minor_base"] * 100 + cat_seq[sem]   # 10101 / 10203 / 10305 ...
            beacon_id = f"BK-{floor:02d}-{floor_seq:03d}"
            cx, cy = round(float(coord[0]), 3), round(float(coord[1]), 3)
            rows = {
                "beaconId": beacon_id,
                "uuid": uuid,
                "major": floor,
                "minor": minor,
                "coordinates": [cx, cy],
                "floor": floor,
                "locationDesc": build_location_desc(floor, sem, n),
                "installHeight": install_height,
                "txPower": meta["tx"],
                "broadcastInterval": interval,
                "batteryModel": DEFAULT_BATTERY,
                "expectedLifespan": DEFAULT_LIFESPAN,
                "semanticTag": sem,
                "installDate": install_date,
                # 可追溯元数据
                "sourceNodeId": n.get("id"),
                "sourceNodeType": n.get("type"),
                "riskLevel": n.get("riskLevel"),
            }
            # 可选 accessibility 标记（楼梯/电梯常不可视障/轮椅）
            if "blindAccessible" in n:
                rows["blindAccessible"] = bool(n["blindAccessible"])
            if "wheelchairAccessible" in n:
                rows["wheelchairAccessible"] = bool(n["wheelchairAccessible"])
            if n.get("facilityType"):
                rows["facilityType"] = n["facilityType"]
            if n.get("rooms"):
                rows["adjacentRooms"] = n["rooms"]
            beacons.append(rows)
    return beacons


def summarize(beacons: list) -> dict:
    by_floor = Counter(b["floor"] for b in beacons)
    by_sem = Counter(b["semanticTag"] for b in beacons)
    return {
        "total": len(beacons),
        "byFloor": {str(k): v for k, v in sorted(by_floor.items())},
        "bySemantic": dict(by_sem),
    }


def main():
    ap = argparse.ArgumentParser(description="生成信标部署方案 JSON（决策节点优先）")
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
    }
    plan["strategy"] = {
        "principle": "决策节点优先（非均匀覆盖）；覆盖安全节点与导航决策点",
        "sources": "topology.nodes 中 intersection/doorway/facility；room 中心节点不部署",
        "simplifications": [
            "每个交叉口/门口/设施节点布 1 个信标（文档 §3.3 四向、§3.1 楼梯两端为后续加密方向）",
            "建筑出入口（facility_entrance）属室内外交界户外设施，按需求不部署信标；室外预警信标为后续独立扩展",
            "室外楼梯（室外疏散楼梯/室外楼梯）为 room 中心节点，本就不部署信标",
            "纯基础设施门口（管道井/水井/风井等，连接的房间全部为 infrastructure 类型）不部署信标",
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


if __name__ == "__main__":
    main()
