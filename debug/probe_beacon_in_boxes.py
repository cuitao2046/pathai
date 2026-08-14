# 调试：识别红框内信标 ID + 关联 refined 方案的位置描述
import re
import json

svg_path = r"C:/Users/Administrator/Downloads/unnecessary-beacons.svg"
svg = open(svg_path, encoding="utf-8").read()

def attr(tag, name):
    m = re.search(rf'(?<![-\w]){name}="([\d.]+)"', tag)
    return float(m.group(1)) if m else None

# 1) 红框
red_rects = []
for m in re.finditer(r'<rect[^>]*stroke="#ff0000"[^>]*>', svg):
    tag = m.group(0)
    x, y = attr(tag, "x"), attr(tag, "y")
    w, h = attr(tag, "width"), attr(tag, "height")
    red_rects.append({"id": re.search(r'id="([^"]+)"', tag).group(1),
                      "x": x, "y": y, "w": w, "h": h,
                      "x1": x + w, "y1": y + h})

# 2) 信标
beacons = []
for m in re.finditer(r'<g class="layer_beacon"[^>]*>(.*?)</g>', svg, re.S):
    chunk = m.group(1)
    cm = re.search(r'<circle[^>]*cx="([\d.]+)"[^>]*cy="([\d.]+)"', chunk)
    tm = re.search(r'<text[^>]*>([^<]*BK-[^<]*)</text>', chunk)
    if cm and tm:
        beacons.append({"id": tm.group(1).strip(),
                        "cx": float(cm.group(1)), "cy": float(cm.group(2))})

# 3) refined 方案索引
refined = {}
try:
    d = json.load(open("result/beacon_deployment_plan_trilateration_routes_refined.json",
                       encoding="utf-8"))
    for b in d.get("beacons", []):
        bid = b.get("beaconId", "")
        loc = b.get("locationDesc") or b.get("semanticTag") or ""
        refined[bid] = {
            "loc": loc,
            "mount": b.get("mountType", ""),
            "floor": b.get("floor", ""),
            "tag": b.get("semanticTag", ""),
        }
except Exception as e:
    print("refined 加载失败:", e)
print(f"refined 方案信标数: {len(refined)}")

# 4) 逐框输出
FLOOR_LABEL = {1: "F1", 2: "F2"}
total = 0
for i, box in enumerate(red_rects, 1):
    inside = [b for b in beacons
              if box["x"] <= b["cx"] <= box["x1"] and box["y"] <= b["cy"] <= box["y1"]]
    inside.sort(key=lambda b: (b["cy"], b["cx"]))
    total += len(inside)
    # 由信标 ID 前缀推断楼层
    floor_guess = "F1" if inside and ("-01-" in inside[0]["id"] or "-F1-" in inside[0]["id"]) \
        else ("F2" if inside and ("-02-" in inside[0]["id"] or "-F2-" in inside[0]["id"]) else "?")
    print(f"\n=== 红框#{i} {box['id']} 坐标({box['x']:.1f},{box['y']:.1f}) "
          f"{box['w']:.1f}x{box['h']:.1f} 楼层≈{floor_guess} 信标 {len(inside)} 个 ===")
    for b in inside:
        r = refined.get(b["id"], {})
        loc = r.get("loc", "")
        mount = r.get("mount", "")
        extra = f" | {loc}" if loc else ""
        if mount:
            extra += f" [{mount}]"
        print(f"   {b['id']}  @({b['cx']:.1f},{b['cy']:.1f}){extra}")
print(f"\n合计: {total} 个信标")
