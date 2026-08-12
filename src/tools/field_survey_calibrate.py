#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现场勘测校准工具（地图构建指南 第七章）

两个子命令：
  export-ref  从 v9 GeoJSON 导出"图纸基准坐标"CSV（拓扑节点 + 门洞中心），
              作为现场激光实测的比对基准。
  compare     读取现场激光实测值，自动换算到项目局部米制坐标系，
              计算每个控制点的实测-图纸偏差，并按 <0.3 / 0.3~0.5 / >0.5 m
              三档判定，输出报告 CSV（标红超阈值点）。

坐标变换与 src/parsing/parse_cad_pdf.py 完全一致：
  x_m = (x_pt - 2019.1) * 0.0529
  y_m = (1154.8 - y_pt) * 0.0529      # Y 轴翻转
"""

import argparse
import csv
import json
import math
import os
import sys

# ---- 与 parse_cad_pdf.py 同步的坐标变换常量（统一来源 src/common/constants.py）----
# 本脚本可独立运行，无法导入 src 包时兜底同值。
try:
    from src.common.constants import SCALE, ORIGIN_X, ORIGIN_Y
except ImportError:
    SCALE = 0.0529        # 米 / pt
    ORIGIN_X = 2019.1     # pt
    ORIGIN_Y = 1154.8     # pt

# 偏差判定阈值（米），与指南第十节验收标准一致
THRESH_OK = 0.3       # < 0.3 可用
THRESH_WARN = 0.5     # 0.3~0.5 局部修正；> 0.5 以实测为准


def pt_to_m(x_pt, y_pt):
    """CAD 工程坐标(pt) -> 局部米制坐标(m)，Y 轴翻转。"""
    return ((x_pt - ORIGIN_X) * SCALE, (ORIGIN_Y - y_pt) * SCALE)


# ---------------------------------------------------------------- export-ref
def export_ref(geojson_path, out_path, floor=None):
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    floors = data.get("floors", {})
    rows = []
    for fnum, fdata in floors.items():
        if floor and str(fnum) != str(floor):
            continue
        # 拓扑节点（room / intersection / doorway / facility / entrance ...）
        for n in fdata.get("topology", {}).get("nodes", []):
            coord = n.get("coordinates") or [None, None]
            rows.append({
                "floor": fnum,
                "id": n.get("id", ""),
                "type": n.get("type", ""),
                "label": n.get("label", ""),
                "x_m": round(coord[0], 4) if coord[0] is not None else "",
                "y_m": round(coord[1], 4) if coord[1] is not None else "",
            })
        # 门洞中心（geometry.doors，Point Feature）
        for d in fdata.get("geometry", {}).get("doors", []):
            did = d.get("id", "")
            coord = (d.get("geometry") or {}).get("coordinates") or [None, None]
            rows.append({
                "floor": fnum,
                "id": did,
                "type": "door",
                "label": (d.get("properties") or {}).get("kind", ""),
                "x_m": round(coord[0], 4) if coord[0] is not None else "",
                "y_m": round(coord[1], 4) if coord[1] is not None else "",
            })

    _write_csv(out_path, rows, ["floor", "id", "type", "label", "x_m", "y_m"])
    print(f"[export-ref] 已导出 {len(rows)} 个基准点 -> {out_path}")


# ---------------------------------------------------------------- compare
def _load_geojson_refs(geojson_path):
    """返回 {id: (floor, type, label, x_m, y_m)}。"""
    with open(geojson_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    refs = {}
    for fnum, fdata in data.get("floors", {}).items():
        for n in fdata.get("topology", {}).get("nodes", []):
            c = n.get("coordinates") or [None, None]
            refs[n.get("id", "")] = (fnum, n.get("type", ""), n.get("label", ""), c[0], c[1])
        for d in fdata.get("geometry", {}).get("doors", []):
            c = (d.get("geometry") or {}).get("coordinates") or [None, None]
            refs[d.get("id", "")] = (fnum, "door",
                                     (d.get("properties") or {}).get("kind", ""),
                                     c[0], c[1])
    return refs


def _to_float(v):
    if v is None or v == "":
        return None
    return float(v)


def compare(measured_path, out_path, geojson_path=None):
    # 参考坐标来源
    gj_refs = _load_geojson_refs(geojson_path) if geojson_path else {}

    with open(measured_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("[compare] 实测文件为空", file=sys.stderr)
        sys.exit(1)

    report = []
    for r in rows:
        rid = r.get("id", "").strip()
        if not rid:
            print("[compare] 跳过缺 id 的行", file=sys.stderr)
            continue

        # --- 参考坐标（图纸）---
        ref_x = _to_float(r.get("ref_x_m"))
        ref_y = _to_float(r.get("ref_y_m"))
        if ref_x is None or ref_y is None:
            if rid in gj_refs:
                _, _, _, ref_x, ref_y = gj_refs[rid]
            else:
                print(f"[compare] 控制点 {rid} 缺参考坐标（CSV 无 ref 且 GeoJSON 未匹配）",
                      file=sys.stderr)
                continue

        # --- 实测坐标（三种输入模式）---
        mx = _to_float(r.get("meas_x_m"))
        my = _to_float(r.get("meas_y_m"))
        if mx is not None and my is not None:
            meas_x, meas_y = mx, my
        else:
            dx = _to_float(r.get("meas_dx_m"))
            dy = _to_float(r.get("meas_dy_m"))
            anchor = r.get("anchor", "").strip()
            if dx is not None and dy is not None and anchor:
                # 锚点法：实测 = 锚点参考坐标 + 相对偏移
                if anchor in gj_refs:
                    ax = gj_refs[anchor][3]
                    ay = gj_refs[anchor][4]
                else:
                    ax = _to_float(r.get("anchor_x_m"))
                    ay = _to_float(r.get("anchor_y_m"))
                if ax is None or ay is None:
                    print(f"[compare] 锚点 {anchor} 参考坐标缺失，跳过 {rid}", file=sys.stderr)
                    continue
                meas_x, meas_y = ax + dx, ay + dy
            else:
                px = _to_float(r.get("meas_x_pt"))
                py = _to_float(r.get("meas_y_pt"))
                if px is not None and py is not None:
                    meas_x, meas_y = pt_to_m(px, py)
                else:
                    print(f"[compare] 控制点 {rid} 实测坐标缺失（需 meas_x_m/meas_y_m "
                          f"或 meas_dx_m/meas_dy_m+anchor 或 meas_x_pt/meas_y_pt）", file=sys.stderr)
                    continue

        dev = math.hypot(meas_x - ref_x, meas_y - ref_y)
        if dev < THRESH_OK:
            cls, action = "OK", "可用，仅微调"
        elif dev < THRESH_WARN:
            cls, action = "WARN", "局部修正几何图层"
        else:
            cls, action = "FAIL", "以实测为准改几何图层"

        report.append({
            "id": rid,
            "ref_x_m": round(ref_x, 4),
            "ref_y_m": round(ref_y, 4),
            "meas_x_m": round(meas_x, 4),
            "meas_y_m": round(meas_y, 4),
            "dev_m": round(dev, 4),
            "class": cls,
            "action": action,
            "note": r.get("note", ""),
        })

    _write_csv(out_path, report,
               ["id", "ref_x_m", "ref_y_m", "meas_x_m", "meas_y_m", "dev_m", "class", "action", "note"])

    # 汇总
    n_ok = sum(1 for x in report if x["class"] == "OK")
    n_warn = sum(1 for x in report if x["class"] == "WARN")
    n_fail = sum(1 for x in report if x["class"] == "FAIL")
    print(f"[compare] 共比对 {len(report)} 个控制点")
    print(f"  OK  (<0.3m) : {n_ok}")
    print(f"  WARN(0.3~0.5): {n_warn}")
    print(f"  FAIL(>0.5m) : {n_fail}")
    print(f"[compare] 报告已写出 -> {out_path}")
    if n_fail:
        print(f"[compare] ⚠ {n_fail} 个点偏差 >0.5m，必须以实测为准修正几何图层")


def _write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="现场勘测校准工具（第七章）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("export-ref", help="从 GeoJSON 导出图纸基准坐标 CSV")
    p1.add_argument("--geojson", required=True, help="v9 GeoJSON 路径")
    p1.add_argument("--out", required=True, help="输出 CSV 路径")
    p1.add_argument("--floor", default=None, help="仅导出某层（如 1 / 2），默认全部")

    p2 = sub.add_parser("compare", help="比对激光实测与图纸坐标，输出偏差报告")
    p2.add_argument("--measured", required=True, help="实测 CSV（见下方字段说明）")
    p2.add_argument("--out", required=True, help="输出报告 CSV 路径")
    p2.add_argument("--geojson", default=None,
                    help="可选：提供则按 id 自动补全参考坐标，无需在实测 CSV 写 ref_*")

    args = ap.parse_args()
    if args.cmd == "export-ref":
        export_ref(args.geojson, args.out, args.floor)
    elif args.cmd == "compare":
        compare(args.measured, args.out, args.geojson)


if __name__ == "__main__":
    main()
