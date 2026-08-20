# -*- coding: utf-8 -*-
"""
信标部署数据迁移 + 3m 漂移重归属工具（方案 B · 单坐标 schema）。

背景（2026-08-20 用户拍板）：
  1) 吸附阈值 10m → 3m（render_interactive.py deployResnap 已改为 ≤3m 类型加权）；
  2) 坐标字段收敛为单坐标 schema：coordinates 为唯一真值，
     plannedCoordinates / snapDist_m 字段废弃，历史位置降为可选
     originalPlannedCoordinates（仅当 ≠ coords 时存在，供 QA 审计「被移动过」）。

阶段 1（schema 迁移，幂等）：
  - 每枚信标：若 plannedCoordinates 存在：
      * 与 coordinates 不同且无 originalPlannedCoordinates → originalPlannedCoordinates=旧 planned；
      * 删除 plannedCoordinates；
  - snapDist_m 存在 → 删除。

阶段 2（3m 漂移重归属，幂等）：
  - 仅处理「有 sourceNodeId 且 coords↔该节点 > T_SRC_DRIFT=3.0m」的信标
    （否则 QA 校验器 R1 会报 ERROR——3m 阈值下坐标偏离归属节点即漂移）：
      * facility / facility_entrance 类（电梯口/楼梯口等设施归属）→ 保持归属不动；
      * 同类型节点（保持 sourceNodeType 语义）≤3m → 归属该节点；
      * 否则任意类型节点 ≤3m → 归属该节点（语义可能变化：交叉口→门口）；
      * 否则 → manual_adjusted（备份 originalSourceNodeId/Type/LocationDesc，
        locationDesc="人工调整 {floor}F（{x}, {y}）"）；
  - 无 sourceNodeId 的信标（route_corridor / route_fill / manual_deploy）不参与。

幂等性：
  脚本可重复运行。二次运行时阶段 1 无字段可删、阶段 2 无超差归属 → 无改动，
  不写回（generatedAt 也不更新），git diff 为空。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe src/tools/fix_beacon_coords.py
  （仅标准库，不依赖 shapely；路径基于 __file__ 推导，禁止 hardcode）
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------- 路径推导（项目惯例：基于 __file__，不 hardcode） ----------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BEACON_JSON = BASE_DIR / "result" / "ble_deployment.json"
MAP_JSON = BASE_DIR / "result" / "school_building_01_map_v9.geojson"

# ---------- 阈值常量（与 render_interactive.py deployResnap / 校验器 T_SRC_DRIFT 对齐） ----------
T_SRC_DRIFT = 3.0   # R1 漂移阈值 = 吸附阈值：超过 3m 的归属即漂移，需重归属或改判
FACILITY_TYPES = ("facility", "facility_entrance")   # 设施语义，不参与重吸附
SNAP_TYPES = ("intersection", "doorway")             # 重吸附候选节点类型


def _num(v):
    """安全取数值；缺失/非法返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dist(x1, y1, x2, y2):
    """欧氏距离（m）。"""
    return math.hypot(x1 - x2, y1 - y2)


def load_snap_nodes(geo):
    """楼层 -> {nodeId: {id,type,label,coordinates}}（仅 intersection/doorway，供重吸附）。"""
    by_floor = {}
    for fk, fd in geo.get("floors", {}).items():
        nodes = {}
        for n in (fd.get("topology") or {}).get("nodes") or []:
            if n.get("type") in SNAP_TYPES and n.get("coordinates"):
                nodes[n["id"]] = n
        by_floor[str(fk)] = nodes
    return by_floor


def migrate_schema(beacon):
    """阶段 1：剥旧 schema 字段（plannedCoordinates/snapDist_m），旧 planned≠coords 转 original。"""
    changed = False
    coords = beacon.get("coordinates")
    if not (isinstance(coords, (list, tuple)) and len(coords) >= 2):
        return False
    x, y = _num(coords[0]), _num(coords[1])
    planned = beacon.get("plannedCoordinates")
    if planned is not None and isinstance(planned, (list, tuple)) and len(planned) >= 2:
        px, py = _num(planned[0]), _num(planned[1])
        if px is not None and py is not None and (px != x or py != y):
            if not beacon.get("originalPlannedCoordinates"):
                beacon["originalPlannedCoordinates"] = [px, py]
                changed = True
        del beacon["plannedCoordinates"]
        changed = True
    if "snapDist_m" in beacon:
        del beacon["snapDist_m"]
        changed = True
    return changed


def resnap(beacon, nodes_by_floor):
    """阶段 2：coords↔sourceNodeId > 3m 的信标按 3m 类型加权重归属。返回是否改动。

    优先级：同类型 ≤3m → 任意类型 ≤3m → manual_adjusted。
    facility 类保持归属不动。
    """
    sid = beacon.get("sourceNodeId") or ""
    if not sid:
        return False  # 无归属（route_corridor/route_fill/manual）不参与
    if beacon.get("sourceNodeType") in FACILITY_TYPES:
        return False  # 设施语义保持归属
    coords = beacon.get("coordinates")
    x, y = _num(coords[0]), _num(coords[1])
    if x is None or y is None:
        return False
    floor = str(beacon.get("floor"))
    nodes = nodes_by_floor.get(floor, {})
    node = nodes.get(sid)
    # 仅处理超差归属（≤3m 无需动）
    if node is not None:
        nd = _dist(x, y, _num(node["coordinates"][0]), _num(node["coordinates"][1]))
        if nd <= T_SRC_DRIFT:
            return False

    changed = False
    prev_type = beacon.get("sourceNodeType")
    best_same, best_same_d = None, None
    best_any, best_any_d = None, None
    for nid, n in nodes.items():
        d = _dist(x, y, _num(n["coordinates"][0]), _num(n["coordinates"][1]))
        if prev_type == n.get("type") and (best_same_d is None or d < best_same_d):
            best_same, best_same_d = nid, d
        if best_any_d is None or d < best_any_d:
            best_any, best_any_d = nid, d

    best_id, best_d = None, None
    if best_same is not None and best_same_d <= T_SRC_DRIFT:
        best_id, best_d = best_same, best_same_d
    elif best_any is not None and best_any_d <= T_SRC_DRIFT:
        best_id, best_d = best_any, best_any_d

    # 备份原归属（仅首次：保留最早归属与完整 locationDesc，含「· 原: 」追溯）
    if not beacon.get("originalSourceNodeId"):
        beacon["originalSourceNodeId"] = beacon.get("sourceNodeId") or ""
        beacon["originalSourceNodeType"] = beacon.get("sourceNodeType") or ""
        beacon["originalLocationDesc"] = beacon.get("locationDesc") or ""
        changed = True

    if best_id is not None:
        bnode = nodes[best_id]
        label = bnode.get("label") or best_id
        sem = "门口" if bnode.get("type") == "doorway" else "交叉口"
        new_desc = f"{beacon.get('floor')}F {sem}（{label}）"
        if beacon.get("sourceNodeId") != best_id:
            beacon["sourceNodeId"] = best_id
            changed = True
        if beacon.get("sourceNodeType") != bnode.get("type"):
            beacon["sourceNodeType"] = bnode.get("type")
            changed = True
        if (beacon.get("locationDesc") or "") != new_desc:
            beacon["locationDesc"] = new_desc
            changed = True
    else:
        if beacon.get("sourceNodeId"):
            beacon["sourceNodeId"] = ""
            changed = True
        if beacon.get("sourceNodeType") != "manual_adjusted":
            beacon["sourceNodeType"] = "manual_adjusted"
            changed = True
        new_desc = f"人工调整 {beacon.get('floor')}F（{x}, {y}）"
        if (beacon.get("locationDesc") or "") != new_desc:
            beacon["locationDesc"] = new_desc
            changed = True
    return changed


def _fmt_beacon(b):
    """信标关键字段摘要（打印用）。"""
    return (
        f"coords=({_num(b['coordinates'][0]):.2f}, {_num(b['coordinates'][1]):.2f}) "
        f"src={b.get('sourceNodeId') or ''}({b.get('sourceNodeType') or ''}) "
        f"orig={b.get('originalPlannedCoordinates')} "
        f"desc={b.get('locationDesc') or ''}"
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not BEACON_JSON.exists() or not MAP_JSON.exists():
        print(f"[fatal] 数据文件缺失：{BEACON_JSON} / {MAP_JSON}")
        return 2

    data = json.load(open(BEACON_JSON, encoding="utf-8"))
    geo = json.load(open(MAP_JSON, encoding="utf-8"))
    nodes_by_floor = load_snap_nodes(geo)
    beacons = data.get("beacons", [])

    print("=" * 80)
    print("信标数据迁移 + 3m 漂移重归属（方案 B · 单坐标 schema）")
    print("=" * 80)
    changed_ids = []
    phase1_ids = []
    phase2_ids = []
    for b in beacons:
        bid = b.get("beaconId") or "?"
        before = json.dumps(b, ensure_ascii=False, sort_keys=True)
        c1 = migrate_schema(b)
        c2 = resnap(b, nodes_by_floor)
        after = json.dumps(b, ensure_ascii=False, sort_keys=True)
        if c1:
            phase1_ids.append(bid)
        if c2:
            phase2_ids.append(bid)
        if before != after:
            changed_ids.append(bid)
            print(f"[fix] {bid}: {_fmt_beacon(b)}")

    print(f"\n阶段1 schema 迁移 {len(phase1_ids)} 枚: {', '.join(phase1_ids) or '无'}")
    print(f"阶段2 3m 重归属 {len(phase2_ids)} 枚: {', '.join(phase2_ids) or '无'}")

    if changed_ids:
        data["generatedAt"] = (datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
        text = json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\r\n")
        with open(BEACON_JSON, "w", encoding="utf-8", newline="") as f:
            f.write(text)  # 保持原文件风格：CRLF、无末尾换行
        print(f"\n已写回 {BEACON_JSON}（{len(changed_ids)} 枚改动：{', '.join(changed_ids)}，"
              f"generatedAt 已更新）")
    else:
        print("\n无字段改动，跳过写回（幂等验证通过：二次运行 diff 为空）")

    json.load(open(BEACON_JSON, encoding="utf-8"))
    print("验证：ble_deployment.json 可 json.load ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
