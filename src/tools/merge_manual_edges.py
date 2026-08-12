#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动拓扑边合并工具（配合渲染图「手动加边」功能）

工作流：
  1. 打开 result/floor_layout_v9_interactive.html → 「手动加边」模式，
     依次点击两个拓扑节点 → 新边实时绘制并存入 localStorage；
  2. 点「导出手工边」下载 manual_edges.json（JSON 数组）；
  3. 运行本脚本把它合并进 GeoJSON：

        python src/merge_manual_edges.py result/manual_edges.json

合并规则：
  - 两端同层 → floors[N].topology.edges
  - 跨层     → 顶层 crossFloorEdges
  - 自动计算 distance（米制欧氏距离）/ estimatedTime（视障步速 0.8 m/s）
  - 自动编号（F{f}-TE-{4 位序号} / FX-XE-{4 位序号}，延续现有最大序号）
  - 同对端点（任一方向）已存在 → 跳过并提示
  - 新边带 manual:true 标记

保存 GeoJSON 后默认调用 src/render_interactive.py 重渲染交互式 HTML。

参数：
  list        手工边清单 JSON 路径（默认 result/manual_edges.json）
  --out       输出 GeoJSON 路径（默认覆盖 result/school_building_01_map_v9.geojson）
  --no-render 合并后不重渲染 HTML（配合 --out 做试运行）
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GEOJSON = ROOT / "result" / "school_building_01_map_v9.geojson"
RENDER = ROOT / "src" / "rendering" / "render_interactive.py"

# 视障步速 m/s（统一来源 src/common/constants.py；独立运行兜底同值）
try:
    from src.common.constants import BLIND_WALK_SPEED
except ImportError:
    BLIND_WALK_SPEED = 0.8


def main(argv=None):
    ap = argparse.ArgumentParser(description="合并手工拓扑边到 GeoJSON")
    ap.add_argument("list", nargs="?", default=str(ROOT / "result" / "manual_edges.json"),
                    help="手工边清单 JSON 路径（渲染图「导出手工边」产物）")
    ap.add_argument("--out", default=None, help="输出 GeoJSON 路径（默认覆盖正式产物）")
    ap.add_argument("--no-render", action="store_true", help="合并后不重渲染 HTML")
    args = ap.parse_args(argv)

    lst_path = Path(args.list)
    if not lst_path.exists():
        print(f"错误：清单文件不存在 {lst_path}")
        return 1
    lst = json.load(open(lst_path, encoding="utf-8"))
    if not isinstance(lst, list) or not lst:
        print("清单为空或格式错误（应为 JSON 数组）")
        return 1

    out_path = Path(args.out) if args.out else GEOJSON
    g = json.load(open(GEOJSON, encoding="utf-8"))
    floors = g["floors"]

    # ---- 节点索引：id -> {floor, coords} ----
    node_index = {}
    for fk, fd in floors.items():
        for n in (fd.get("topology") or {}).get("nodes") or []:
            node_index[n["id"]] = {"floor": int(fk), "coords": list(n.get("coordinates") or [0, 0])}

    # ---- 已有边（去重，含反向） ----
    existing = set()
    for fk, fd in floors.items():
        for e in (fd.get("topology") or {}).get("edges") or []:
            existing.add((e.get("from"), e.get("to")))
            existing.add((e.get("to"), e.get("from")))
    for e in g.get("crossFloorEdges") or []:
        existing.add((e.get("from"), e.get("to")))
        existing.add((e.get("to"), e.get("from")))

    # ---- 各层现有边数（编号续接） ----
    seq = {}
    for fk, fd in floors.items():
        seq[int(fk)] = len([e for e in (fd.get("topology") or {}).get("edges") or [] if e.get("id", "").startswith(f"F{int(fk)}-TE-")])

    added_same, added_cross, skipped = [], [], []
    for i, m in enumerate(lst, 1):
        a, b = m.get("from"), m.get("to")
        if not a or not b or a == b:
            skipped.append((i, f"from/to 缺失或相同 ({a}->{b})"))
            continue
        if a not in node_index or b not in node_index:
            missing = a if a not in node_index else b
            skipped.append((i, f"节点不存在: {missing}"))
            continue
        if (a, b) in existing:
            skipped.append((i, f"已存在边 {a}↔{b}"))
            continue
        na, nb = node_index[a], node_index[b]
        dist = math.hypot(na["coords"][0] - nb["coords"][0],
                          na["coords"][1] - nb["coords"][1])
        base = {
            "from": a, "to": b,
            "distance": round(dist, 2),
            "estimatedTime": round(dist / BLIND_WALK_SPEED, 1),
            "accessibilityLevel": 0,
            "riskLevel": 0.5,
            "walkable": True,
            "wheelchairAccessible": True,
            "blindAccessible": True,
            "manual": True,
        }
        if na["floor"] == nb["floor"]:
            fk = str(na["floor"])
            seq[na["floor"]] += 1
            edge = dict(base)
            edge["id"] = f"F{na['floor']}-TE-{seq[na['floor']]:04d}"
            floors[fk]["topology"]["edges"].append(edge)
            added_same.append(edge["id"])
        else:
            n_cross = len(g.get("crossFloorEdges") or [])
            edge = dict(base)
            edge["id"] = f"FX-XE-{n_cross + 1:04d}"
            edge.update({"fromFloor": na["floor"], "toFloor": nb["floor"],
                         "type": "manual", "matchedBy": "manual"})
            g.setdefault("crossFloorEdges", []).append(edge)
            added_cross.append(edge["id"])
        existing.add((a, b))
        existing.add((b, a))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(g, fp, ensure_ascii=False, indent=2)

    print(f"清单共 {len(lst)} 条手工边")
    print(f"新增同层边 {len(added_same)} 条: {added_same}")
    print(f"新增跨层边 {len(added_cross)} 条: {added_cross}")
    if skipped:
        print(f"跳过 {len(skipped)} 条:")
        for i, reason in skipped:
            print(f"  #{i}: {reason}")
    if not args.no_render and out_path == GEOJSON:
        print("重渲染交互式 HTML ...")
        subprocess.run([sys.executable, str(RENDER)], cwd=str(ROOT), check=False)
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
