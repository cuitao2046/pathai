# -*- coding: utf-8 -*-
"""PDF 读取与图层/文本提取（PyMuPDF 独立层）。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  默认开启图层读取 + 页面矢量元素按图层提取
  + 房间标签 / 井道编号 / 门窗 DK 编号文本提取。

本模块只依赖 PyMuPDF 与标准库，不含 shapely / 拓扑逻辑；
parse_cad_pdf.py 的 parse_floor 在此装配 PDF 生命周期（打开→提取→关闭）。
"""
import math
import re

LABEL_MIN_SIZE = 8.5       # 房间名称最小字号(pt)（略降以捕获 ~9pt 卫生间等小标签）
TITLE_BLOCK_X = 2900.0       # 图签区 x 起点（右侧剔除）
LAYER_ELEVATOR = "A-FLOR-EVTR"  # 电梯井图层名
LAYER_WALL = "WALL"          # 墙体图层（含外墙/内隔墙，双线表示）

# 楼梯/电梯井编号（图纸权威标识，如 II-B2-01#ST / II-02#EL）。
# 同一井道在各层沿用同一编号，是比"几何中心距离"可靠得多的跨层配对依据。
FACILITY_CODE_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*#(ST|EL)$")


def get_default_on_layers(doc):
    """读取 PDF 默认打开(ON)的图层名集合"""
    return {item["text"] for item in doc.layer_ui_configs() if item["on"]}


def extract_layer_items(page, layer_names):
    """
    从页面提取指定图层的矢量元素。
    返回 dict: layer -> {'lines': [(a,b)], 'quads': [[p1..p4]], 'curves': [bezier4]}
    """
    out = {name: {"lines": [], "quads": [], "curves": []} for name in layer_names}
    for d in page.get_drawings():
        layer = d.get("layer") or ""
        if layer not in out:
            continue
        bucket = out[layer]
        for it in d["items"]:
            kind = it[0]
            if kind == "l":
                p1, p2 = it[1], it[2]
                bucket["lines"].append(((p1.x, p1.y), (p2.x, p2.y)))
            elif kind == "qu":
                q = it[1]
                bucket["quads"].append([(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                                        (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)])
            elif kind == "re":
                r = it[1]
                bucket["quads"].append([(r.x0, r.y0), (r.x1, r.y0),
                                        (r.x1, r.y1), (r.x0, r.y1)])
            elif kind == "c":
                p1, p2, p3, p4 = it[1], it[2], it[3], it[4]
                bucket["curves"].append([(p1.x, p1.y), (p2.x, p2.y),
                                         (p3.x, p3.y), (p4.x, p4.y)])
    return out


def extract_facility_codes(page):
    """
    提取楼梯井/电梯井编号（II-B2-01#ST、II-02#EL），返回 [(code, (cx, cy)_pt, kind)]。

    这些编号是图纸的权威标识：同一井道在各楼层沿用同一编号，
    因此可直接作为跨层配对键，避免"按中心距离猜"带来的漏配/错配；
    编号字号较小（#EL 约 8.4pt），不能复用 extract_room_labels 的字号门槛。
    """
    out = []
    d = page.get_text("dict")
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"]).strip()
            m = FACILITY_CODE_RE.match(txt)
            if not m:
                continue
            x0, y0, x1, y1 = line["bbox"]
            if x1 > TITLE_BLOCK_X:
                continue
            out.append((txt, ((x0 + x1) / 2, (y0 + y1) / 2), m.group(1)))
    return out


def extract_dk_text_labels(page):
    """
    从文本对象中提取 DK 门窗编号（如 DK1224、DK2424）。

    CAD 导出的 DK 标注有两种形态：
      1) 矢量曲线（被 cluster_window_glyph_codes 聚类后由 is_dk_block 识别）；
      2) 文本实体（text span，尤其竖排/旋转标注更常以此存储）。
    后者用正则直接匹配最稳定，且天然支持旋转；本函数作为矢量 DK 识别的重要补充。

    返回 [(cx, cy), ...]（PDF pt，已按 8pt 去重）。
    """
    out = []
    seen = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            txt = "".join(s["text"] for s in line["spans"]).strip()
            # 去掉可能夹杂的空格/全角空格
            txt_clean = txt.replace(" ", "").replace("\u3000", "")
            if not re.match(r"^DK\d+$", txt_clean):
                continue
            x0, y0, x1, y1 = line["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if any(math.hypot(cx - sx, cy - sy) < 8.0 for sx, sy in seen):
                continue
            seen.append((cx, cy))
            out.append((cx, cy))
    return out


def extract_room_labels(page):
    """
    提取房间标签文本（大字号、排除图签区），合并多行中文（如 学生/社团/活动区）；
    同时收集 A-ANNO-150-TXT 英文编号（如 II-WR-03）作为 roomCode。

    返回 (names, codes):
      names = [(chinese_text, (cx, cy)_pt], ...]   → 空间名（用于房间探测）
      codes = [(code_text, (cx, cy)_pt], ...]       → 编号（后与房间匹配）
    """
    d = page.get_text("dict")
    raw = []
    for block in d["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            txt = "".join(s["text"] for s in line["spans"]).strip()
            if not txt:
                continue
            size = max(s["size"] for s in line["spans"])
            x0, y0, x1, y1 = line["bbox"]
            if x1 > TITLE_BLOCK_X:
                continue
            raw.append({"text": txt, "size": size,
                        "bbox": (x0, y0, x1, y1),
                        "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                        "w": x1 - x0, "h": y1 - y0})

    def _blocked(t):
        if any(k in t for k in ("归档记录", "出图日期", "图号", "版本号",
                                 "图名", "会  签", "会 签", "会签",
                                 "验证签字", "教育建筑", "产业发展研究院",
                                 "初中学部", "教学楼", "项目名称", "工程名称",
                                 "出图", "设计", "校对", "审核", "审定",
                                 "BIAD", "A20-", "平面图", "学校",
                                 "建设单位", "设计单位", "图纸",
                                 "2m范围内", "门窗洞口", "范围内", "年   月   日",
                                 "年 月 日", "年 月")):
            return True
        if re.fullmatch(r"[0-9.\s%㎡()（）*≥<=-]+", t):
            return True
        if any(k in t for k in ("面积", "分区", "排烟", "平面", "教学", "项目", "设计")):
            return True
        if re.search(r"[0-9a-zA-Z]{3,}", t):
            return True
        if len(t) > 12:
            return True
        return False

    # A-ANNO-150-TXT 英文编号（II-WR-03 / II-LAB-01 / R1001 等）
    _ANNO_CODE_RE = re.compile(r"^(II-[\w\d]+-[\d]+|R\d{3,})$")

    names = []  # 中文空间名
    codes = []  # 英文编号
    for r in raw:
        t = r["text"]
        if _blocked(t):
            continue
        if _ANNO_CODE_RE.match(t):
            codes.append((t, (r["cx"], r["cy"])))
        elif bool(re.search(r"[一-鿿]", t)) and r["size"] >= LABEL_MIN_SIZE:
            names.append(r)

    # 合并垂直堆叠的多行中文标签（行间距 < 2.2 倍行高，水平重叠）；
    # 不跨语义类合并——管井（风井/水井/排风井）与房间标签各自成串，
    # 避免「水井排风井」与「女卫生间」拼接成「水井排风井女卫生间」。
    # 开放空间(走廊/门厅/大厅/活动/出入口)与封闭空间(房间/管井/电梯/楼梯间等)
    # 也分属不同语义类，绝不拼接——避免「走道」+「历史地理资料室」合成「走道历史地理资料室」。
    _UTIL_TAGS = {"风井", "水井", "排风井", "强电井", "弱电井", "管井"}
    _OPEN_TAGS = {"走道", "走廊", "过道", "门厅", "大厅", "活动", "社团",
                  "庭园", "上空", "门厅无障碍出入口", "无障碍出入口",
                  "出入口", "人防主出入口"}
    def _cat(t):
        if t in _UTIL_TAGS: return "util"
        if t in _OPEN_TAGS: return "open"
        return "room"
    names.sort(key=lambda r: (r["cx"], r["cy"]))
    used = [False] * len(names)
    merged = []
    for i, r in enumerate(names):
        if used[i]:
            continue
        group = [r]
        used[i] = True
        r_cat = _cat(r["text"])
        for j in range(i + 1, len(names)):
            r2 = names[j]
            if used[j]:
                continue
            # 不同语义类不合并
            if r_cat != _cat(r2["text"]):
                continue
            if abs(r2["cx"] - r["cx"]) < max(r["w"], r2["w"], 20) * 0.6 + 10 and \
               abs(r2["cy"] - r["cy"]) < (r["h"] + r2["h"]) * 2.2:
                group.append(r2)
                used[j] = True
        group.sort(key=lambda g: g["cy"])
        text = "".join(g["text"] for g in group)
        cx = sum(g["cx"] for g in group) / len(group)
        cy = sum(g["cy"] for g in group) / len(group)
        # 携带该标签组的最大字号，供后续「同一空间内字号最大者作为语义层」规则使用
        gsize = max(g["size"] for g in group)
        merged.append((text, (cx, cy), gsize))
    return merged, codes
