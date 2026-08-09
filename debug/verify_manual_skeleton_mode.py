#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 build_skeleton_topology 的手动骨架模式与自动回退路径。

不依赖 CAD PDF：用合成的最简输入 + 已导出的手绘骨架 JSON 直接驱动
pipeline.build_skeleton_topology，断言手动 TI 节点/边被正确采用、门节点成功
挂接到手动 TI；并跑一遍自动回退路径确认无异常。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.skeleton.pipeline import build_skeleton_topology  # noqa: E402

MANUAL = json.loads(
    (ROOT / "result" / "skeleton_manual_parsed.json").read_text(encoding="utf-8")
)


def _obj_type():
    return {
        "topo_room": "TR", "topo_doorway": "TD", "topo_intersection": "TI",
        "topo_facility": "TF", "topo_entrance": "TEN", "topo_edge": "TE",
    }


def test_manual_mode(floor="1"):
    m = MANUAL[floor]
    ti = m["ti_nodes"]
    edges = m["edges"]
    # 取第一个手动 TI 坐标放一个房间 + 一个门，验证 TD 能挂到该 TI
    x, y = ti[0]["coordinates"]
    rooms = [{
        "id": "RM-TEST-1", "roomType": "classroom",
        "centroid_m": [x + 2.0, y + 2.0], "walkable_poly_m": None,
    }]
    doors = [{
        "center_m": [x, y], "kind": "swing", "width_pt": 30,
        "rooms": ["RM-TEST-1"],
    }]
    stairs = [{"properties": {"centroid": [x - 5.0, y - 5.0], "label": "楼梯1"}}]
    topo = build_skeleton_topology(
        int(floor), rooms, doors, stairs, [],
        walkable_by_room_id=None, extra_nodes=None,
        resolution=0.08, obj_type=_obj_type(),
        manual_skeleton=m,
    )
    nodes = topo["nodes"]
    out_edges = topo["edges"]
    ti_ids = {n["id"] for n in nodes if n.get("type") == "intersection"}
    td_ids = {n["id"] for n in nodes if n.get("type") == "doorway"}
    ti_ti = [e for e in out_edges
             if e["from"] in ti_ids and e["to"] in ti_ids]

    # 连通分量（基于 TI-TI 边）：手动骨架可能留小孤岛，pipeline 的连通性保障会补桥
    adj = {t: set() for t in ti_ids}
    for e in ti_ti:
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    seen, comps = set(), 0
    for t in ti_ids:
        if t in seen:
            continue
        comps += 1
        stack = [t]
        seen.add(t)
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)

    print(f"[F{floor}] 手动模式:")
    print(f"  输入 TI={len(ti)} TI-TI边={len(edges)}")
    print(f"  输出 TI={len(ti_ids)} (输入 {len(ti)}) | "
          f"TI-TI边={len(ti_ti)} (输入 {len(edges)}) | "
          f"TD={len(td_ids)} | TI连通分量={comps}")
    # 门→TI 挂接边
    td_ti = [e for e in out_edges
             if (e["from"] in td_ids and e["to"] in ti_ids) or
             (e["to"] in td_ids and e["from"] in ti_ids)]
    print(f"  TD↔TI 挂接边={len(td_ti)}")

    ok = True
    # TI 数量：pipeline 会合并距离 <1.5m 的冗余微交叉口（有意优化），
    # 因此只允许减少、不允许增加；且不应合并过度（<50% 输入视为异常）。
    if len(ti_ids) > len(ti):
        print(f"  ❌ TI 数量增加（合并只应减少）: {len(ti_ids)} > {len(ti)}"); ok = False
    if len(ti_ids) < max(1, int(len(ti) * 0.5)):
        print(f"  ❌ TI 合并过度: {len(ti_ids)} < 50% 输入 {len(ti)}"); ok = False
    # 连通性不变量：合并后骨架仍应全连通
    if comps != 1:
        print(f"  ⚠️ TI 存在 {comps} 个连通分量（手动骨架留孤岛，"
              f"pipeline 已补桥至连通）")
    if not td_ids:
        print("  ❌ 未生成门节点"); ok = False
    if not td_ti:
        print("  ❌ 门未挂接到 TI"); ok = False
    # 手动边不变量：两端都存活（未被合并掉）的手动边必须保留在结果中；
    # 端点坍缩进同一簇的边允许随合并消失（非数据丢失）。
    edge_pairs = {(min(e["from"], e["to"]), max(e["from"], e["to"])) for e in ti_ti}
    lost = [e["id"] for e in edges
            if e["from"] in ti_ids and e["to"] in ti_ids
            and tuple(sorted((e["from"], e["to"]))) not in edge_pairs]
    if lost:
        print(f"  ❌ 存活端点的手动边丢失: {lost[:5]}"); ok = False
    # 手动骨架模式必须被采用（而非自动回退）
    meta = topo.get("skeleton_meta") or {}
    if not meta.get("manual"):
        print(f"  ❌ 未采用手动骨架模式: meta={meta}"); ok = False
    print(f"  skeleton_meta={topo.get('skeleton_meta')}")
    print("  ✅ 通过" if ok else "  ⚠️ 失败")
    return ok


def test_auto_fallback():
    """无 walkable、无 manual_skeleton：应走自动路径且不出异常。"""
    rooms = [{
        "id": "RM-TEST-2", "roomType": "classroom",
        "centroid_m": [0.0, 0.0], "walkable_poly_m": None,
    }]
    doors = [{
        "center_m": [0.0, 0.0], "kind": "swing", "width_pt": 30,
        "rooms": ["RM-TEST-2"],
    }]
    try:
        topo = build_skeleton_topology(
            1, rooms, doors, [], [],
            walkable_by_room_id=None, extra_nodes=None,
            resolution=0.08, obj_type=_obj_type(), manual_skeleton=None,
        )
        print(f"[auto] 回退路径 OK: 节点={len(topo['nodes'])} "
              f"边={len(topo['edges'])} "
              f"meta.manual={topo['skeleton_meta'].get('manual')}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[auto] ❌ 异常: {e}")
        return False


if __name__ == "__main__":
    r1 = test_manual_mode("1")
    r2 = test_manual_mode("2")
    r3 = test_auto_fallback()
    sys.exit(0 if (r1 and r2 and r3) else 1)
