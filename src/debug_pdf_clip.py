# -*- coding: utf-8 -*-
"""渲染原始 PDF 指定 pt 区域为 PNG（set_rotation(0)，与提取坐标一致）"""
import sys
import fitz

pdf = sys.argv[1] if len(sys.argv) > 1 else r"E:\code\pathai\A20-002-II-初中学部 1# 教学楼首层平面图-A0_BIAD-无签名.pdf"
cx, cy, half = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
out = sys.argv[5]

doc = fitz.open(pdf)
page = doc[0]
page.set_rotation(0)
clip = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
pix = page.get_pixmap(matrix=fitz.Matrix(6, 6), clip=clip)
pix.save(out)
print("saved", out, pix.width, "x", pix.height)
