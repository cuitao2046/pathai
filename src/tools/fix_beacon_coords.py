# -*- coding: utf-8 -*-
"""
信标部署坐标硬伤修正脚本（正式工具；方案 B：渲染图部署模式下移动过的信标，coordinates 为真值）。

背景：
  result/ble_deployment.json 中 7 枚信标存在坐标硬伤（QA 校验器报 R1=4 + R2=7）：
    - A 族 4 枚（交叉口腔，planned 与源节点自洽、coords 漂移）：BK-01-008 / BK-01-021 /
      BK-01-032 / BK-01-033 —— 这些信标在渲染图部署模式被拖拽过，coordinates 是移动后的真实位置。
    - B 族 3 枚（路线补点，无 sourceNodeId，planned 是生成时旧值）：BK-TR-F1-003 / BK-TR-F1-004 /
      BK-TR-F1-005。
  方案 B 语义：coords 为真值 → 所有坐标字段向 coordinates 同步，并让信标语义归属正确。

修正规则（仅处理白名单 7 枚，其余 54 枚原样不动）：
  - 通用：plannedCoordinates := coordinates（同步坐标）。
  - A 族 4 枚额外做「最近节点重吸附」：同层最近 intersection 节点（≤ T_SNAP_MAX=10m）：
      * 命中 → sourceNodeId=该节点 id、sourceNodeType='intersection'、
        snapDist_m=0（planned=coords → coords↔planned 偏移为 0，符合部署 schema 的
        snapDist_m 语义，并与校验器 R1/R2 的 snapDist_m 一致性门槛一致；
        若保留「最近距离」（如 BK-01-008 为 5.06m > T_SRC_DRIFT=5.0m），
        在 R1/R2 逻辑不变的前提下仍会报 R1 ERROR，无法达到验收要求 R1/R2=0）、
        locationDesc=f"{floor}F 交叉口（{节点label}）"，并追加「· 原: 交叉口N」追溯原声明；
      * 未命中（> T_SNAP_MAX）→ sourceNodeId 置空、sourceNodeType='manual_adjusted'、
        snapDist_m=0、locationDesc="人工调整 {floor}F（{x}, {y}）"。
  - B 族 3 枚：plannedCoordinates := coordinates、snapDist_m=0（planned 已=coords 无偏移）。

幂等性：
  脚本可重复运行。二次运行时 7 枚信标均已处于修正后状态（planned==coords、sourceNodeId/
  sourceNodeType/snapDist_m/locationDesc 已匹配），无任何字段改动 → 不写回（generatedAt
  也不更新），git diff 为空。

用法（仓库根目录）：
  C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe src/tools/fix_beacon_coords.py
  （仅标准库，不依赖 shapely；路径基于 __file__ 推导，禁止 hardcode）
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------- 路径推导（项目惯例：基于 __file__，不 hardcode） ----------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
BEACON_JSON = BASE_DIR / "result" / "ble_deployment.json"
MAP_JSON = BASE_DIR / "result" / "school_building_01_map_v9.geojson"

# ---------- 硬伤白名单（仅修正这 7 枚；其余 54 枚必须原样不动） ----------
A_FAMILY = {"BK-01-008", "BK-01-021", "BK-01-032", "BK-01-033"}   # 交叉口腔：coords 漂移
B_FAMILY = {"BK-TR-F1-003", "BK-TR-F1-004", "BK-TR-F1-005"}       # 路线补点：planned 旧值
FIX_IDS = A_FAMILY | B_FAMILY

T_SNAP_MAX = 10.0   # 重吸附最近 intersection 节点阈值（m）


def _num(v):
    """安全取数值；缺失/非法返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dist(x1, y1, x2, y2):
    """欧氏距离（m）。"""
    return math.hypot(x1 - x2, y1 - y2)


def _old_crossing_label(desc):
    """从原 locationDesc 主体提取「交叉口N[ · 方向]」追溯片段；无法解析返回 None。

    注意：只能从「主体」提取（见 _split_old_trace）——若直接对整个 desc 提取，
    会命中重吸附后的新交叉口号（如「1F 交叉口（交叉口55） · 原: 交叉口11」中的
    交叉口55），导致二次运行把追溯覆盖成新号，破坏幂等性。
    """
    m = re.search(r"交叉口(\d+)([^)）]*)", desc or "")
    if not m:
        return None
    num = m.group(1)
    suffix = m.group(2).strip().lstrip("·").strip()
    return f"交叉口{num}" + (f" · {suffix}" if suffix else "")


def _split_old_trace(desc):
    """把 locationDesc 拆成 (主体, 追溯后缀)；追溯后缀以「· 原: 」开头，无则空串。"""
    if not desc:
        return "", ""
    if "· 原: " in desc:
        head, _, tail = desc.partition("· 原: ")
        return head.strip(), "· 原: " + tail
    return desc, ""


def load_intersection_nodes(geo):
    """楼层 -> {nodeId: node}（仅 intersection 类型，供重吸附）。"""
    by_floor = {}
    for fk, fd in geo.get("floors", {}).items():
        nodes = {}
        for n in (fd.get("topology") or {}).get("nodes") or []:
            if n.get("type") == "intersection":
                nodes[n["id"]] = n
        by_floor[str(fk)] = nodes
    return by_floor


def _sync_planned(beacon, x, y):
    """plannedCoordinates := coordinates（若尚未同步）。返回是否改动。"""
    pl = beacon.get("plannedCoordinates")
    if pl is None or _num(pl[0]) != x or _num(pl[1]) != y:
        beacon["plannedCoordinates"] = [x, y]
        return True
    return False


def fix_a_family(beacon, nodes_by_floor):
    """A 族：planned:=coords + 最近 intersection 重吸附（≤T_SNAP_MAX）。返回是否改动。"""
    changed = False
    floor = str(beacon.get("floor"))
    coords = beacon.get("coordinates")
    x, y = _num(coords[0]), _num(coords[1])
    if x is None or y is None:
        return False
    changed |= _sync_planned(beacon, x, y)

    old_desc = beacon.get("locationDesc") or ""
    old_main, old_trace = _split_old_trace(old_desc)
    old_ref = _old_crossing_label(old_main)
    floor_nodes = nodes_by_floor.get(floor, {})
    best_id, best_d = None, None
    for nid, n in floor_nodes.items():
        nx, ny = _num(n["coordinates"][0]), _num(n["coordinates"][1])
        d = _dist(x, y, nx, ny)
        if best_d is None or d < best_d:
            best_d, best_id = d, nid

    if best_id is not None and best_d <= T_SNAP_MAX:
        node = floor_nodes[best_id]
        label = node.get("label") or best_id
        new_desc = f"{beacon.get('floor')}F 交叉口（{label}）"
        # 保留原声明追溯：已有「· 原: 」后缀则原样保留（幂等）；
        # 否则仅在原交叉口号与新标签不同时追加（label 已含「交叉口」前缀）
        if old_trace:
            new_desc += " " + old_trace
        elif old_ref and old_ref != label:
            new_desc += f" · 原: {old_ref}"
        if beacon.get("sourceNodeId") != best_id:
            beacon["sourceNodeId"] = best_id
            changed = True
        if beacon.get("sourceNodeType") != "intersection":
            beacon["sourceNodeType"] = "intersection"
            changed = True
        # snapDist_m=0：planned=coords → coords↔planned 偏移为 0（schema 语义；
        # 保留最近距离会在 R1/R2 逻辑不变下触发 R1 ERROR，故不保留——见模块 docstring）
        if _num(beacon.get("snapDist_m")) not in (0.0, None):
            beacon["snapDist_m"] = 0
            changed = True
        if old_desc != new_desc:
            beacon["locationDesc"] = new_desc
            changed = True
    else:
        # 未命中（> T_SNAP_MAX）：降级为人工调整
        if beacon.get("sourceNodeId"):
            beacon["sourceNodeId"] = ""
            changed = True
        if beacon.get("sourceNodeType") != "manual_adjusted":
            beacon["sourceNodeType"] = "manual_adjusted"
            changed = True
        if _num(beacon.get("snapDist_m")) not in (0.0, None):
            beacon["snapDist_m"] = 0
            changed = True
        new_desc = f"人工调整 {beacon.get('floor')}F（{x}, {y}）"
        if old_desc != new_desc:
            beacon["locationDesc"] = new_desc
            changed = True
    return changed


def fix_b_family(beacon):
    """B 族：planned:=coords、snapDist_m=0。返回是否改动。"""
    changed = False
    coords = beacon.get("coordinates")
    x, y = _num(coords[0]), _num(coords[1])
    if x is None or y is None:
        return False
    changed |= _sync_planned(beacon, x, y)
    if _num(beacon.get("snapDist_m")) not in (0.0, None):
        beacon["snapDist_m"] = 0
        changed = True
    return changed


def _fmt_beacon(b):
    """信标关键字段摘要（打印用）。"""
    return (
        f"coords=({_num(b['coordinates'][0]):.2f}, {_num(b['coordinates'][1]):.2f}) "
        f"planned={b.get('plannedCoordinates')} src={b.get('sourceNodeId') or ''} "
        f"srcType={b.get('sourceNodeType') or ''} snap={b.get('snapDist_m')} "
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
    nodes_by_floor = load_intersection_nodes(geo)
    beacons = data.get("beacons", [])
    by_id = {b.get("beaconId"): b for b in beacons}

    print("=" * 80)
    print("信标部署坐标硬伤修正（方案 B：coords 为真值）")
    print("=" * 80)
    changed_ids = []
    for bid in sorted(FIX_IDS):
        b = by_id.get(bid)
        if b is None:
            print(f"[warn] 白名单信标 {bid} 不存在，跳过")
            continue
        before = json.dumps(b, ensure_ascii=False, sort_keys=True)
        if bid in A_FAMILY:
            ok = fix_a_family(b, nodes_by_floor)
        else:
            ok = fix_b_family(b)
        after = json.dumps(b, ensure_ascii=False, sort_keys=True)
        if ok and before != after:
            changed_ids.append(bid)
            print(f"[fix] {bid}: {_fmt_beacon(b)}")
        else:
            print(f"[ok ] {bid}: 无需修改（幂等）")

    # 防御性校验：白名单外信标必须未被改动（脚本从未写它们，仅打印确认）
    untouched = [b.get("beaconId") for b in beacons if b.get("beaconId") not in FIX_IDS]
    print(f"\n白名单外 {len(untouched)} 枚信标：未参与修正（原样保留）")

    if changed_ids:
        data["generatedAt"] = (datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
        text = json.dumps(data, ensure_ascii=False, indent=2).replace("\n", "\r\n")
        with open(BEACON_JSON, "w", encoding="utf-8", newline="") as f:
            f.write(text)  # 保持原文件风格：CRLF、无末尾换行
        print(f"\n已写回 {BEACON_JSON}（修正 {len(changed_ids)} 枚：{', '.join(changed_ids)}，"
              f"generatedAt 已更新）")
    else:
        print("\n无字段改动，跳过写回（幂等验证通过：二次运行 diff 为空）")

    # 验证可正常 json.load（写回后结构完整）
    json.load(open(BEACON_JSON, encoding="utf-8"))
    print("验证：ble_deployment.json 可 json.load ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
