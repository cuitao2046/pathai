"""检查 geometry.doors 中那些产生重叠 TD 节点的原始门的中心距。"""
import json
GEO = r"D:\code\pathai\result/school_building_01_map_v9.geojson"
# 比例尺（与 parse_cad_pdf 一致）：SCALE=0.0529 m/pt, 原点(2019.1,1154.8)pt, Y翻转
SCALE = 0.0529
OX, OY = 2019.1, 1154.8
def pt2m(pt):
    x, y = pt
    return ((x - OX) * SCALE, (OY - y) * SCALE)

with open(GEO, encoding="utf-8") as f:
    data = json.load(f)

for fk in ["1", "2"]:
    fl = data["floors"][fk]
    doors = fl["geometry"]["doors"]
    # 找重叠 TD 组涉及的 rooms 组合
    print(f"\n===== 楼层 {fk}：重叠 TD 组的原始门 center_m =====")
    # 直接扫描所有门，找出中心距 < 1.5m 的簇
    cms = []
    for d in doors:
        c = d.get("center")
        if c:
            cms.append((d["id"], pt2m(c), d.get("kind"), d.get("rooms")))
    import math
    for i, (did, cm, kind, rooms) in enumerate(cms):
        for j in range(i+1, len(cms)):
            dj, cmj, kindj, roomsj = cms[j]
            dist = math.hypot(cm[0]-cmj[0], cm[1]-cmj[1])
            if dist < 1.5:
                print(f"  {did}[{kind}] {tuple(round(v,3) for v in cm)} rooms={rooms}")
                print(f"  {dj}[{kindj}] {tuple(round(v,3) for v in cmj)} rooms={roomsj}  dist={dist:.3f}")
