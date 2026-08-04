# -*- coding: utf-8 -*-
"""在 PDF 渲染图上标注门中心/轴线/弧中点"""
import sys
import fitz
import numpy as np
import cv2

sys.path.insert(0, r"E:\code\pathai\src")

pdf = r"E:\code\pathai\A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf"
cx, cy, half = 1227, 970, 110
out = r"E:\code\pathai\result\_debug_door_mark.png"

# 门数据（从 debug_door_attr 已知）
doors = [
    {"center": (1227, 946), "axis": None, "arc_mid": (1241, 942), "tag": "D1-0020"},
]

doc = fitz.open(pdf)
page = doc[0]
page.set_rotation(0)
clip = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
ZM = 6
pix = page.get_pixmap(matrix=fitz.Matrix(ZM, ZM), clip=clip)
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
    pix.height, pix.width, pix.n).copy()
if pix.n == 4:
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
else:
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def to_px(p):
    return (int((p[0] - (cx - half)) * ZM), int((p[1] - (cy - half)) * ZM))


for d in doors:
    cv2.circle(img, to_px(d["center"]), 14, (0, 0, 255), 3)       # 中心红圈
    cv2.circle(img, to_px(d["arc_mid"]), 12, (255, 0, 0), 3)     # 弧中点蓝圈
cv2.imwrite(out, img)
print("saved", out)
