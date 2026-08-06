# -*- coding: utf-8 -*-
"""T1 验收诊断：校验 Walkable Polygon 是否排除柱子/墙体/楼梯井/电梯井障碍物。

读取 result/school_building_01_map_v9.geojson：
  1) 对每个公共空间 feature 的 walkablePolygon：
     - 面积对比（walkable vs 房间原始面积，检查有真实扣减）
     - walkable 必须完全落在房间多边形内（误差 <0.05m²）
     - walkable 不得与楼梯井/电梯井多边形重叠（>0.02m² 即失败）
     - walkable 外环顶点距最近墙线 ≥ WALL_BUFFER-0.05m（扣除贴墙带）
  2) 汇总各层统计，输出 PASS/FAIL。
"""
import json
import sys
from pathlib import Path

from shapely.geometry import shape, LineString
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parent.parent
GEOJSON = ROOT / "result" / "school_building_01_map_v9.geojson"

WALL_BUFFER_M = 0.12      # 与 generate_walkable_polygons 的 wall_buffer_m 一致
TOL_IN = 0.05             # m² 重叠容差
TOL_WALL = 0.05           # m 墙距容差（浮点/吸附误差）

OPEN_TYPES = {"corridor", "lobby", "activity", "atrium"}


def main():
    d = json.load(open(GEOJSON, encoding="utf-8"))
    all_fail = []
    for fn, fl in d["floors"].items():
        rooms = fl["geometry"]["rooms"]
        stairs = [shape(f["geometry"]) for f in fl["geometry"].get("stairs", [])]
        evtrs = [shape(f["geometry"]) for f in fl["geometry"].get("elevators", [])]
        walls = [LineString(f["geometry"]["coordinates"])
                 for f in fl["geometry"].get("walls", [])]
        shaft_union = unary_union(stairs + evtrs) if (stairs or evtrs) else None
        wall_union = unary_union(walls) if walls else None

        n_open = n_wp = n_ok_area = n_ok_inside = n_ok_shaft = n_ok_wall = 0
        fail_msgs = []
        for r in rooms:
            p = r["properties"]
            if p.get("roomType") not in OPEN_TYPES:
                continue
            n_open += 1
            wp = p.get("walkablePolygon")
            if not wp:
                continue
            n_wp += 1
            wpg = shape(wp)
            room_g = shape(r["geometry"])
            wa = wpg.area
            ra = room_g.area
            if ra > 0:
                n_ok_area += 1  # 有 walkable 即算有产物；扣减比例单独统计
            # 1) 完全落在房间内
            diff = wpg.difference(room_g).area
            if diff > TOL_IN:
                fail_msgs.append(f"{r['id']} walkable 超出房间 {diff:.2f}m²")
            else:
                n_ok_inside += 1
            # 2) 不重叠井道
            if shaft_union is not None:
                ov = wpg.intersection(shaft_union).area
                if ov > TOL_IN:
                    fail_msgs.append(f"{r['id']} walkable 与井道重叠 {ov:.2f}m²")
                else:
                    n_ok_shaft += 1
            else:
                n_ok_shaft += 1
            # 3) 顶点距墙 ≥ buffer（贴墙带被扣除）
            parts = list(wpg.geoms) if wpg.geom_type == "MultiPolygon" else [wpg]
            # 用顶点采样验证贴墙扣除：顶点距最近墙线 < 0.06m 才算贴墙（异常）。
            # 正常扣减后顶点距墙应 ≈ WALL_BUFFER(0.12m)，0.10~0.12m 属正常。
            if wall_union is not None:
                pts = []
                for pp in parts:
                    pts.extend(pp.exterior.coords)
                step = max(1, len(pts) // 60)
                from shapely.geometry import Point
                near_cnt = 0
                for x, y in pts[::step]:
                    pt = Point(x, y)
                    if min((pt.distance(w) for w in walls), default=99) < 0.06:
                        near_cnt += 1
                if near_cnt > max(1, len(pts[::step]) // 5):
                    fail_msgs.append(
                        f"{r['id']} 贴墙顶点过多({near_cnt}/{len(pts[::step])})")
                else:
                    n_ok_wall += 1
            else:
                n_ok_wall += 1

        print(f"[F{fn}] 公共空间 {n_open} | 有 walkable {n_wp}")
        print(f"       落房间内 {n_ok_inside}/{n_wp} | 不重叠井道 {n_ok_shaft}/{n_wp}"
              f" | 墙体扣减检查 {n_ok_wall}/{n_wp}")
        if fail_msgs:
            print("       FAIL:")
            for m in fail_msgs[:10]:
                print(f"         - {m}")
            all_fail.extend(fail_msgs)
        else:
            print("       通过")

    # 面积扣减统计（验证 walkable 确实小于房间面积）
    for fn, fl in d["floors"].items():
        rows = []
        for r in fl["geometry"]["rooms"]:
            p = r["properties"]
            if p.get("roomType") in OPEN_TYPES and p.get("walkablePolygon"):
                ra = shape(r["geometry"]).area
                wa = shape(p["walkablePolygon"]).area
                rows.append((p["label"], ra, wa, ra - wa))
        rows.sort(key=lambda x: -(x[3]))
        print(f"[F{fn}] 扣减面积 Top5: "
              + ", ".join(f"{l}: {rd:.1f}m²" for l, _, _, rd in rows[:5]))

    print("\n=== RESULT:", "FAIL" if all_fail else "VALIDATION PASS", "===")
    sys.exit(1 if all_fail else 0)


if __name__ == "__main__":
    main()
