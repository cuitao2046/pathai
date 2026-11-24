# -*- coding: utf-8 -*-
"""信标标签贴纸 DOCX 生成器（89×127mm 打印纸，5×4=20 枚/张，每枚 14×25mm）。

标签内容：仅 `beaconId`，竖排（顺时针 90°）。
页面尺寸与打印机可选纸型 89×127mm 一致，避免驱动"适合可打印区域"缩放；
版面按实纸实测：左边距/上边距 4mm，列间隙 2mm，行间隙 3mm。
数据源与 `gen_beacon_labels.py` 相同：`backend/maps_data/<siteId>/<version>.json`
的 `beacons`，排序、预检逻辑直接复用，保证与台账/物联标签一致。

用法（仓库根目录）：
  python src/tools/gen_beacon_labels_sheet.py
  python src/tools/gen_beacon_labels_sheet.py --version 2026-11-22.1
  python src/tools/gen_beacon_labels_sheet.py --out result/labels.docx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_beacon_labels import RESULT_DIR, load_site, precheck  # noqa: E402

PAGE_W, PAGE_H = 89.0, 127.0
COLS, ROWS = 5, 4
LABEL_W, LABEL_H = 14.0, 25.0
GAP_X, GAP_Y = 2.0, 3.0
MARGIN_LEFT, MARGIN_TOP = 4.0, 4.0
PER_SHEET = COLS * ROWS
ID_FONT_PT = 9.5


def _mm(value: float) -> int:
    return Mm(value).twips


def _set_tbl_pr(table) -> None:
    # tblPr 子元素必须按 OOXML schema 顺序排列，否则 Word 会忽略部分属性
    tbl_pr = table._tbl.tblPr
    for tag in ("w:tblW", "w:tblInd", "w:tblBorders", "w:tblLayout", "w:tblCellMar", "w:tblLook"):
        for element in tbl_pr.findall(qn(tag)):
            tbl_pr.remove(element)

    width = OxmlElement("w:tblW")
    width.set(qn("w:w"), str(_mm(COLS * LABEL_W + (COLS - 1) * GAP_X)))
    width.set(qn("w:type"), "dxa")
    tbl_pr.append(width)

    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:w"), "0")
    indent.set(qn("w:type"), "dxa")
    tbl_pr.append(indent)

    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        item = OxmlElement(f"w:{edge}")
        item.set(qn("w:val"), "none")
        item.set(qn("w:sz"), "0")
        item.set(qn("w:space"), "0")
        item.set(qn("w:color"), "auto")
        borders.append(item)
    tbl_pr.append(borders)

    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)

    margins = OxmlElement("w:tblCellMar")
    for edge in ("top", "left", "bottom", "right"):
        item = OxmlElement(f"w:{edge}")
        item.set(qn("w:w"), "0")
        item.set(qn("w:type"), "dxa")
        margins.append(item)
    tbl_pr.append(margins)

    look = OxmlElement("w:tblLook")
    look.set(qn("w:val"), "04A0")
    look.set(qn("w:firstRow"), "0")
    look.set(qn("w:lastRow"), "0")
    look.set(qn("w:firstColumn"), "0")
    look.set(qn("w:lastColumn"), "0")
    look.set(qn("w:noHBand"), "1")
    look.set(qn("w:noVBand"), "1")
    tbl_pr.append(look)


def _set_row_height(row, height_mm: float) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    cant_split = OxmlElement("w:cantSplit")
    tr_pr.append(cant_split)
    tr_height = OxmlElement("w:trHeight")
    tr_height.set(qn("w:val"), str(_mm(height_mm)))
    tr_height.set(qn("w:hRule"), "exact")
    tr_pr.append(tr_height)


def _write_text(cell, beacon_id: str) -> None:
    # 标签竖贴：文字整体顺时针旋转 90°（Word 表格单元格"旋转所有文字 90°"）
    tc_pr = cell._tc.get_or_add_tcPr()
    direction = OxmlElement("w:textDirection")
    direction.set(qn("w:val"), "tbRl")
    v_align = tc_pr.find(qn("w:vAlign"))
    if v_align is not None:
        v_align.addprevious(direction)
    else:
        tc_pr.append(direction)
    cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    paragraph = cell.paragraphs[0]
    fmt = paragraph.paragraph_format
    fmt.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = Pt(ID_FONT_PT + 1.6)
    run = paragraph.add_run(beacon_id)
    run.bold = True
    run.font.size = Pt(ID_FONT_PT)
    run.font.name = "Arial"
    run_pr = run._element.get_or_add_rPr()
    fonts = run_pr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        run_pr.append(fonts)
    fonts.set(qn("w:ascii"), "Arial")
    fonts.set(qn("w:hAnsi"), "Arial")
    fonts.set(qn("w:eastAsia"), "Arial")


def _add_sheet(doc: Document, chunk: list[dict], first: bool) -> None:
    if not first:
        para = doc.add_paragraph()
        fmt = para.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        fmt.line_spacing = Pt(1)
        fmt.page_break_before = True

    table = doc.add_table(rows=ROWS * 2 - 1, cols=COLS * 2 - 1)
    _set_tbl_pr(table)

    widths = [LABEL_W if index % 2 == 0 else GAP_X for index in range(COLS * 2 - 1)]
    heights = [LABEL_H if index % 2 == 0 else GAP_Y for index in range(ROWS * 2 - 1)]

    for row in table.rows:
        for index, cell in enumerate(row.cells):
            cell.width = Mm(widths[index])
    for index, row in enumerate(table.rows):
        _set_row_height(row, heights[index])

    for position, beacon in enumerate(chunk):
        row_index = position // COLS * 2
        col_index = position % COLS * 2
        _write_text(table.cell(row_index, col_index), beacon["beaconId"])


def build_docx(pkg: dict, beacons: list[dict]) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(6)
    style.paragraph_format.space_before = Pt(0)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.line_spacing = Pt(7.5)

    section = doc.sections[0]
    section.page_width = Mm(PAGE_W)
    section.page_height = Mm(PAGE_H)
    section.left_margin = Mm(MARGIN_LEFT)
    section.right_margin = Mm(PAGE_W - MARGIN_LEFT - COLS * LABEL_W - (COLS - 1) * GAP_X)
    section.top_margin = Mm(MARGIN_TOP)
    section.bottom_margin = Mm(1)
    section.header_distance = Mm(0)
    section.footer_distance = Mm(0)

    ordered = sorted(beacons, key=lambda beacon: beacon["beaconId"])
    for start in range(0, len(ordered), PER_SHEET):
        _add_sheet(doc, ordered[start : start + PER_SHEET], first=start == 0)

    # 表格后必须有段落；用 1pt 空段落避免把内容顶到下一页
    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(0)
    tail.paragraph_format.space_after = Pt(0)
    tail.paragraph_format.line_spacing = Pt(1)
    tail.add_run().font.size = Pt(1)

    doc.core_properties.title = f"信标标签 89x127mm · {pkg['siteId']} · {pkg['mapVersion']}"
    return doc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 89x127mm 信标贴纸 DOCX（20 枚/张，14x25mm）")
    parser.add_argument("--site", default="school-building-01", help="场所 ID")
    parser.add_argument("--version", default="latest", help="地图版本")
    parser.add_argument("--out", default="", help="输出 DOCX 路径")
    args = parser.parse_args(argv)

    pkg = load_site(args.site, args.version)
    beacons = pkg.get("beacons") or []
    precheck(beacons)

    doc = build_docx(pkg, beacons)
    out = Path(args.out) if args.out else RESULT_DIR / f"beacon_labels_89x127_{pkg['mapVersion']}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)

    sheets = (len(beacons) + PER_SHEET - 1) // PER_SHEET
    print(f"信标标签：{len(beacons)} 枚 / {sheets} 张（{args.site} · {pkg['mapVersion']}）")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
