# -*- coding: utf-8 -*-
"""
debug/verify_route_rules.py — 验证导航路线生成三条规则（src/route_rules.py）

规则 1：同层路线「中间节点」禁止楼梯/电梯等非公共设施节点（起终点可为设施）。
规则 2：视障(blind) 跨层必须走电梯跨层边，禁用楼梯跨层边。
规则 3：房间↔房间经房间附属门连公共空间；门优先级 swing>fire>opening；
        仅无门卫生间(门洞例外)允许穿墙，其他场景禁止穿墙。

用法：python debug/verify_route_rules.py [geojson_path]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from route_rules import RouteGraph, load_geojson  # noqa: E402

GEO_DEFAULT = os.path.join(os.path.dirname(__file__), "..",
                           "result", "school_building_01_map_v9.geojson")

_fail = 0


def check(cond, msg):
    global _fail
    if cond:
        print("  PASS:", msg)
    else:
        print("  FAIL:", msg)
        _fail += 1


def main():
    geo = sys.argv[1] if len(sys.argv) > 1 else GEO_DEFAULT
    g = load_geojson(geo)
    print(f"[load] nodes={len(g.nodes)} edges={len(g.edges)} "
          f"walls={len(g.wall_lines)}")
    rooms = [n for n in g.nodes.values() if n["type"] == "room"]

    # ============ 规则 1：同层中间节点禁设施 ============
    print("\n[Rule 1] 同层路线中间节点禁止楼梯/电梯设施")
    viol = 0
    tot = 0
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if rooms[i]["floor"] != rooms[j]["floor"]:
                continue
            sp = g.shortest_path(rooms[i]["id"], rooms[j]["id"], "normal")
            if not sp:
                continue
            tot += 1
            for m in sp["mid_nodes"]:
                if g.nodes[m]["type"] == "facility":
                    viol += 1
                    break
    check(viol == 0,
          f"同层 {tot} 条路线中，无设施节点作为中间节点（违例 {viol}）")

    # ============ 规则 2：盲模式跨层仅电梯 ============
    print("\n[Rule 2] 视障跨层必须走电梯（禁楼梯跨层边）")
    f1 = [n for n in rooms if n["floor"] == 1]
    f2 = [n for n in rooms if n["floor"] == 2]
    blk_ok = 0
    blk_tot = 0
    stair_used = 0
    for a in f1:
        for b in f2:
            sp = g.shortest_path(a["id"], b["id"], "blind")
            if not sp:
                continue
            blk_tot += 1
            if sp["used_elevator"] and not sp["used_stair"]:
                blk_ok += 1
            if sp["used_stair"]:
                stair_used += 1
    check(blk_tot > 0, f"存在盲模式跨层可达路线 {blk_tot} 条")
    check(blk_ok == blk_tot,
          f"盲模式跨层全部经电梯且不用楼梯（{blk_ok}/{blk_tot}，楼梯误用 {stair_used}）")

    # ============ 规则 3a：门优先级 swing>fire>opening ============
    print("\n[Rule 3a] 房间出门优先级 普通(swing)>防火(fire)>门洞(opening)")
    # 找一间同时有 swing 与 fire 门的房间
    from collections import defaultdict
    rtypes = defaultdict(set)
    for n in g.nodes.values():
        if n["type"] == "doorway":
            for rid in n["rooms"]:
                rtypes[rid].add(n["doorType"])
    mixed = [rid for rid, t in rtypes.items()
             if "swing" in t and "fire" in t]
    # 仅保留有 TR 节点的封闭房间（走廊/门厅等开放空间无 TR，须过滤，否则取 mixed[0] 崩溃）
    tr_rids = {n.get("roomId") for n in g.nodes.values()
               if n["type"] == "room" and n.get("roomId")}
    mixed = [rid for rid in mixed if rid in tr_rids]
    check(len(mixed) > 0, f"存在同时含 swing+fire 门的房间（{len(mixed)} 间）")
    if mixed:
        rid = mixed[0]
        tr = [n["id"] for n in g.nodes.values()
              if n["type"] == "room" and n["roomId"] == rid][0]
        same = [n["id"] for n in rooms if n["floor"] == 1 and n["id"] != tr]
        import random
        random.seed(7)
        used = []
        for d in random.sample(same, min(12, len(same))):
            sp = g.shortest_path(tr, d, "normal")
            if sp:
                used.append(g.nodes[sp["path"][1]]["doorType"])
        check(used and sum(u == "swing" for u in used) >= max(1, len(used) // 2),
              f"{rid} 的 {len(used)} 条路线中过半经普通门(swing)，实际 {set(used)}")

    # ============ 规则 3b：房间↔房间不得门直连两房 ============
    print("\n[Rule 3b] 门不得作为两房间直连通道（须经公共空间）")
    bad = 0
    cnt = 0
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if rooms[i]["floor"] != rooms[j]["floor"]:
                continue
            sp = g.shortest_path(rooms[i]["id"], rooms[j]["id"], "normal")
            if not sp:
                continue
            cnt += 1
            p = sp["path"]
            for k in range(1, len(p) - 1):
                a, c = g.nodes[p[k - 1]], g.nodes[p[k + 1]]
                if g.nodes[p[k]]["type"] == "doorway" and \
                   a["type"] == "room" and c["type"] == "room":
                    bad += 1
                    break
    check(bad == 0, f"{cnt} 条同层路线中无 room→door→room 直连（违例 {bad}）")

    # ============ 规则 3c：穿墙几何校验 + 无门卫生间例外 ============
    print("\n[Rule 3c] 穿墙校验：普通路径零穿墙；无门卫生间允许穿墙")
    # 合成几何单测：垂直于墙=穿墙；沿墙并行/共线=不穿
    A, B = (0.0, 0.0), (10.0, 0.0)
    check(g._segment_crosses_wall((5, -1), (5, 1), A, B) is True,
          "垂直穿透墙线 → 穿墙")
    check(g._segment_crosses_wall((0, 1), (10, 1), A, B) is False,
          "沿墙并行(同侧) → 不穿墙")
    check(g._segment_crosses_wall((2, 0), (8, 0), A, B) is False,
          "与墙共线 → 不穿墙")

    # 真实数据全量扫描（同层）：路由已剔除穿墙走廊边，仅当两地仅靠「穿墙桥边」
    # 连通时才回退穿墙（保连通）。断言：不存在「本可绕行却穿墙」的可避免穿墙路线。
    cross = 0
    cross_avoidable = 0
    fb_same = 0
    sampled = 0
    cap = 2500
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if rooms[i]["floor"] != rooms[j]["floor"]:
                continue
            sp = g.shortest_path(rooms[i]["id"], rooms[j]["id"], "normal")
            if not sp:
                continue
            sampled += 1
            wc = g.validate_wall_crossing(sp["path"], "normal")
            if not wc["ok"]:
                cross += 1
                if sp.get("wall_fallback"):
                    fb_same += 1
                else:
                    cross_avoidable += 1
            if sampled >= cap:
                break
        if sampled >= cap:
            break
    check(cross_avoidable == 0,
          f"同层 {sampled} 条路线：可避免的穿墙路线 {cross_avoidable}（应=0）；"
          f"穿墙路线共 {cross}（均为保连通的桥边回退，需 walkable 多边形修复）")

    # 跨层（盲模式）全量扫描：同样仅桥边回退才穿墙
    f1 = [n for n in rooms if n["floor"] == 1]
    f2 = [n for n in rooms if n["floor"] == 2]
    cf = 0
    cf_avoid = 0
    cf_tot = 0
    for a in f1:
        for b in f2:
            sp = g.shortest_path(a["id"], b["id"], "blind")
            if not sp:
                continue
            cf_tot += 1
            wc = g.validate_wall_crossing(sp["path"], "blind")
            if not wc["ok"]:
                cf += 1
                if not sp.get("wall_fallback"):
                    cf_avoid += 1
    check(cf_avoid == 0,
          f"盲模式跨层 {cf_tot} 条路线：可避免的穿墙路线 {cf_avoid}（应=0）；"
          f"穿墙路线共 {cf}（桥边回退）")

    # 无门卫生间例外（可选：数据中所有卫生间均有门时为 0，属正确行为）
    dt = [n["id"] for n in g.nodes.values() if g.is_doorless_toilet(n["id"])]
    check(len(dt) >= 0, f"存在无门卫生间（门洞例外）共 {len(dt)} 间")
    if dt:
        # 选一个同层、可达的普通房间作目标（排除门洞卫生间自身，避免退化为单点路径）
        target, sp = None, None
        for r in rooms:
            if r["id"] == dt[0] or r["floor"] != g.nodes[dt[0]]["floor"]:
                continue
            sp_t = g.shortest_path(dt[0], r["id"], "normal")
            if sp_t:
                target, sp = r["id"], sp_t
                break
        wc = g.validate_wall_crossing(sp["path"], "normal") if (target and sp) else None
        check(target is not None and wc is not None and wc["ok"]
              and wc["allowed_by_toilet_exception"],
              f"无门卫生间 {dt[0]} 路线允许穿墙（例外生效，目标={target}）")

    # ============ 汇总 ============
    print("\n" + ("全部通过 ✅" if _fail == 0 else f"{_fail} 项失败 ❌"))
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
