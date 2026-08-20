# -*- coding: utf-8 -*-
"""恢复缺失的 result/fingerprint_grid_routes.json（测试路线指纹采集网格）。

背景
----
result/fingerprint_grid_routes.json 从未入库且磁盘缺失（git log 无记录），但它是
render_interactive.py（src/rendering/render_interactive.py:983 自动探测）、
refine_beacon_placement.py 与 gen_trilateration_plan_routes.py 的正式输入。
其内容 = 测试路线走廊上的指纹采集点（HTML 中 data-info 显示「来源 route」）。

入库版 HTML（git show HEAD:result/floor_layout_v9_interactive.html）完整包含这些
指纹点，可反向重建该 JSON：
  1. 正则解析每个 <g class="layer_fingerprint" data-info='...'><circle cx cy/>
     的转义 JSON（data-info 为 JSON 字符串，需 json.loads 解码）与像素坐标；
  2. 用 HTML 内嵌 GEOX（与 deploySvg2geo 一致的 svg2geo 反变换）把像素坐标
     反算为米制局部坐标；
  3. 组装与 result/fingerprint_grid.json 同构的 JSON 并写盘。

用法
----
    python src/tools/recover_fingerprint_routes.py [--html PATH] [--out PATH]
默认输入 result/floor_layout_v9_interactive.html（入库版 HTML），
默认输出 result/fingerprint_grid_routes.json。

注意
----
HTML 中「648 个 layer_fingerprint」含 1 条 CSS 规则
（.layer_fingerprint circle{cursor:pointer;}），实际 <g class="layer_fingerprint">
元素为 647 个（F1:611 / F2:36）。本脚本按实际元素恢复 647 点；
坐标保留反变换全精度，可让重渲染 HTML 的指纹元素与原入库版逐字节一致。
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量集中区
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HTML = ROOT / "result" / "floor_layout_v9_interactive.html"
DEFAULT_OUT = ROOT / "result" / "fingerprint_grid_routes.json"
SIBLING_GRID = ROOT / "result" / "fingerprint_grid.json"

# 反变换常量兜底（与入库版 HTML 内嵌 GEOX 一致；优先从 HTML 解析，解析失败才用此值）。
GEOX_FALLBACK = {
    "ox": -83.7935398305038,
    "oy": 27.00297849202474,
    "scale": 7.0,
    "marginX": 50,
    "marginY": 30,
    "titleH": 46,
    "perFloor": 929,
    "nFloors": 2,
    "floorKeys": ["1", "2"],
}

# 区域类型中文 -> 枚举值（与 generate_fingerprint_grid.py 口径一致：
# 普通 -> normal，安全节点/安全 -> safe；未知类型回退 normal 并告警）。
REGION_TYPE_MAP = {
    "普通": "normal",
    "安全节点": "safe",
    "安全": "safe",
}

SOURCE_FIXED = "route"  # 指纹点来源固定为 route（HTML data-info「来源」行亦为 route）

# 单个指纹元素正则：data-info 用单引号包裹 JSON；JSON 内单引号会被
# render_interactive 转义为 \'，故用转义感知匹配（[^'\\] 或 \\.）而非 [^']*。
_ELEM_RE = re.compile(
    r'<g class="layer_fingerprint"\s+data-info=\'(?P<info>(?:[^\'\\]|\\.)*)\'>'
    r'<circle cx="(?P<cx>[^"]+)" cy="(?P<cy>[^"]+)"[^>]*/>'
    r'</g>'
)
_GEOX_RE = re.compile(r"var GEOX = \{([\s\S]*?)\};")


def parse_geox(html):
    """从 HTML 内嵌脚本解析 GEOX 常量；解析失败回退 GEOX_FALLBACK。"""
    m = _GEOX_RE.search(html)
    if not m:
        return dict(GEOX_FALLBACK)
    obj_lit = "{" + m.group(1) + "}"
    # JS 对象字面量 -> JSON：给裸 key 加双引号（值内数组 ["1","2"] 已带引号不受影响）。
    obj_lit = re.sub(r'([{,]\s*)([A-Za-z_]\w*)\s*:', r'\1"\2":', obj_lit)
    try:
        geox = json.loads(obj_lit)
    except Exception:
        return dict(GEOX_FALLBACK)
    if not all(k in geox for k in ("ox", "oy", "scale", "marginX", "marginY",
                                   "titleH", "perFloor", "nFloors", "floorKeys")):
        return dict(GEOX_FALLBACK)
    return geox


def svg2geo(sx, sy, geox):
    """SVG 用户空间 -> 米制局部坐标（与 render_interactive.py 的 svg2geo 一致）。
    返回 (gx, gy, floor_key)。"""
    i = min(geox["nFloors"] - 1, max(0, int(sy // geox["perFloor"])))
    fk = geox["floorKeys"][i]
    gx = (sx - geox["marginX"]) / geox["scale"] + geox["ox"]
    gy = geox["oy"] - (sy - i * geox["perFloor"] - geox["titleH"]
                       - geox["marginY"]) / geox["scale"]
    return gx, gy, fk


def extract_points(html, geox):
    """解析 HTML 中全部 layer_fingerprint 元素，返回恢复后的指纹点 dict 列表。"""
    points = []
    seen = set()
    for m in _ELEM_RE.finditer(html):
        # render_interactive.info_attr 将 JSON 内单引号转义为 \'（HTML 属性无转义
        # 语义、原样写盘）；json.loads 前还原，保证未来含撇号数据也能解析。
        # 安全：合法 JSON 中 \' 不会是有效转义，出现即来自该 replace。
        info_text = m.group("info").replace("\\'", "'")
        try:
            info = json.loads(info_text)
        except Exception as e:
            print(f"  [warn] 跳过无法解析 data-info 的元素: {e}")
            continue
        if info.get("kind") != "fingerprint":
            continue
        pid = str(info.get("id", ""))
        rows = {k: v for k, v in info.get("detail", {}).get("rows", [])}

        # 楼层：优先「楼层」行（渲染端写入 p.get('floor')），与 id 前缀交叉校验。
        floor_row = str(rows.get("楼层", "")).rstrip("F")
        try:
            floor = int(floor_row)
        except (TypeError, ValueError):
            floor = None
        id_prefix = re.match(r"FP-(\d+)-", pid)
        id_floor = int(id_prefix.group(1)) if id_prefix else None
        if floor is None and id_floor is not None:
            floor = id_floor
        if floor is None:
            print(f"  [warn] {pid} 无法确定楼层，跳过")
            continue
        if id_floor is not None and id_floor != floor:
            print(f"  [warn] {pid} id 前缀楼层({id_floor}) 与详情行({floor}) 不一致，"
                  f"以详情行为准")

        # 区域类型映射。
        region_cn = str(rows.get("区域类型", ""))
        region_type = REGION_TYPE_MAP.get(region_cn)
        if region_type is None:
            print(f"  [warn] {pid} 未知区域类型「{region_cn}」，回退 normal")
            region_type = "normal"

        # 优先级。
        try:
            priority = int(rows.get("采集优先级", 3))
        except (TypeError, ValueError):
            priority = 3

        # 来源（HTML 应为 route；异常时仍固定 route 并告警）。
        source = str(rows.get("来源", SOURCE_FIXED))
        if source != SOURCE_FIXED:
            print(f"  [warn] {pid} 来源为「{source}」，强制为 {SOURCE_FIXED}")

        # 像素坐标 -> 米制坐标（保留全精度，保证重渲染逐字节一致）。
        try:
            sx = float(m.group("cx"))
            sy = float(m.group("cy"))
        except ValueError as e:
            print(f"  [warn] {pid} 非法像素坐标: {e}")
            continue
        gx, gy, fk = svg2geo(sx, sy, geox)
        if fk != str(floor):
            print(f"  [warn] {pid} 像素坐标所属楼层({fk}) 与详情行({floor}) 不一致，"
                  f"以详情行为准")

        if pid in seen:
            print(f"  [warn] 重复指纹点 id {pid}，保留首个")
            continue
        seen.add(pid)

        points.append({
            "id": pid,
            "floor": floor,
            "coordinates": [gx, gy],
            "regionType": region_type,
            "priority": priority,
            "source": SOURCE_FIXED,
            "nearNodeId": None,
            "nearNodeType": None,
        })
    return points


def build_document(points, geox, generated_at):
    """组装与 result/fingerprint_grid.json 同构的顶层文档。"""
    # 顶层元信息：优先沿用兄弟文件 fingerprint_grid.json，缺失时兜底。
    venue_id, venue_name, version = "school-building-01", "初中学部1#教学楼", "9.0.0"
    if SIBLING_GRID.exists():
        try:
            sib = json.load(open(SIBLING_GRID, encoding="utf-8"))
            venue_id = sib.get("venueId", venue_id)
            venue_name = sib.get("venueName", venue_name)
            version = sib.get("version", version)
        except Exception as e:
            print(f"  [warn] 读取 {SIBLING_GRID.name} 失败，使用兜底元信息: {e}")

    # 按楼层分组并按 id 排序（确定性输出）。
    by_floor = {}
    for p in points:
        by_floor.setdefault(p["floor"], []).append(p)
    floors_out = {}
    summary = {}
    for fl in sorted(by_floor):
        fk = str(fl)
        fps = sorted(by_floor[fl], key=lambda x: x["id"])
        n_safe = sum(1 for p in fps if p["regionType"] == "safe")
        n_normal = len(fps) - n_safe
        floors_out[fk] = {
            "floor": fl,
            "parameters": {
                "normalSpacingM": None,
                "safeSpacingM": None,
                "safeRadiusM": None,
                "largeSpacingM": None,
            },
            "points": fps,
        }
        summary[fk] = {
            "normal_points": n_normal,
            "safe_anchors": n_safe,
            "large_rooms": 0,
            "total": len(fps),
            "safe": n_safe,
            "normal": n_normal,
            "by_source": {SOURCE_FIXED: len(fps)},
        }

    return {
        "venueId": venue_id,
        "venueName": venue_name,
        "version": version,
        "generator": "recover_fingerprint_routes.py",
        "generatedAt": generated_at,
        "parameters": {
            "source": "floor_layout_v9_interactive.html (committed HEAD)",
            "recovered": True,
            "transform": {
                "ox": geox["ox"],
                "oy": geox["oy"],
                "scale": geox["scale"],
                "perFloor": geox["perFloor"],
            },
        },
        "summary": summary,
        "floors": floors_out,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="从入库版 HTML 恢复 fingerprint_grid_routes.json")
    ap.add_argument("--html", default=str(DEFAULT_HTML), help="入库版 HTML 路径（含 layer_fingerprint 元素）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSON 路径")
    args = ap.parse_args(argv)

    html_path = Path(args.html)
    if not html_path.exists():
        print(f"[error] 输入 HTML 不存在: {html_path}")
        return 2
    html = html_path.read_text(encoding="utf-8")
    geox = parse_geox(html)
    print(f"  [info] GEOX: ox={geox['ox']} oy={geox['oy']} scale={geox['scale']} "
          f"perFloor={geox['perFloor']} floorKeys={geox['floorKeys']}")

    points = extract_points(html, geox)
    if not points:
        print("[error] 未解析到任何指纹点")
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()
    doc = build_document(points, geox, generated_at)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 输出统计。
    print(f"  [info] 已写入 {out_path}，共 {len(points)} 点")
    for fk in sorted(doc["summary"]):
        s = doc["summary"][fk]
        print(f"  [F{fk}] total={s['total']} normal={s['normal']} safe={s['safe']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
