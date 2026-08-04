# -*- coding: utf-8 -*-
"""校验：检测房间面积 vs 图纸标注面积；无门房间的门距离分析"""
import sys
sys.path.insert(0, r"E:\code\pathai\src")
import parse_cad_pdf as P
from shapely.geometry import Point

floor = int(sys.argv[1]) if len(sys.argv) > 1 else 1
pdf = P.PDF_F1 if floor == 1 else P.PDF_F2
data = P.parse_floor(pdf, floor)

print(f"=== F{floor} 房间面积（标注参考: 音乐教室 75.2/73.8, 合班教室 ~160, 图书 90人~?）===")
for r in sorted(data["rooms"], key=lambda r: -r["polygon_pt"].area):
    a_pt2 = r["polygon_pt"].area
    a_m2 = a_pt2 * P.SCALE * P.SCALE
    ndoors = sum(1 for dr in data["doors"] if r["id"] in dr["rooms"])
    print(f"  {r['id']} {r['label']:14s} {a_m2:7.1f}m2  doors={ndoors}")

print("=== 无门房间的最近门距离 ===")
for r in data["rooms"]:
    ndoors = sum(1 for dr in data["doors"] if r["id"] in dr["rooms"])
    if ndoors == 0:
        c = r["polygon_pt"].centroid
        near = []
        for dr in data["doors"]:
            d = r["polygon_pt"].exterior.distance(Point(dr["center"]))
            near.append((d, dr["center"], dr["width_pt"] * P.SCALE))
        near.sort()
        print(f"  {r['label']} @({c.x:.0f},{c.y:.0f}): "
              + ", ".join(f"d={d:.1f}pt@({x:.0f},{y:.0f})w={w:.2f}m"
                          for d, (x, y), w in near[:4]))
