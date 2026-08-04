# -*- coding: utf-8 -*-
"""
渲染 GeoJSON v7 为 HTML — 基于最新 PDF OCG 可见性，米制坐标系
"""
import json
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GEO_IN = str(BASE_DIR / "school_building_01_map_v7.geojson")
HTML_OUT = str(BASE_DIR / "floor_layout_v7.html")

SCALE = 6.0           # 1m = 6px
MARGIN_X = 40
MARGIN_Y = 30
FLOOR_TITLE_H = 40

ROOM_COLORS = {
    "lobby": "#FFF3E0", "corridor": "#F5F5F5", "library": "#DCEDC8",
    "classroom": "#FFF9C4", "lab": "#B3E5FC", "office": "#D7CCC8",
    "restroom": "#B2DFDB", "staircase": "#FFCDD2", "meeting_room": "#F8BBD0",
    "storage": "#CFD8DC", "equipment": "#B0BEC5", "shaft": "#ECEFF1",
    "archive": "#BCAAA4", "workshop": "#C5CAE9", "art_room": "#C5E1A5",
    "counseling": "#C8E6C9", "broadcast": "#D1C4E9",
    "control_room": "#B39DDB", "print_room": "#D7CCC8", "infirmary": "#FFEBEE",
    "elevator": "#F8BBD0", "courtyard": "#FAFAFA",
    "entrance": "#C8E6C9", "activity_room": "#E1F5FE",
    "lecture_hall": "#FFF9C4", "music_room": "#F0F4C3",
    "calligraphy_room": "#DCE775", "resource_room": "#C5E1A5",
    "chemistry_room": "#B3E5FC", "bio_room": "#B2DFDB",
    "physics_room": "#B2EBF2", "geography_room": "#C5CAE9",
    "history_room": "#D7CCC8", "prep_room": "#CFD8DC",
    "multi_classroom": "#FFF176",
    "room": "#FAFAFA", "other": "#FAFAFA",
}

RISK_STYLES = {
    "stair_entrance":  {"shape": "circle", "color": "#F44336", "size": 5},
    "fire_door":       {"shape": "circle", "color": "#FF9800", "size": 4},
    "glass_door":      {"shape": "triangle", "color": "#2196F3", "size": 6},
    "wet_floor":       {"shape": "droplet", "color": "#00BCD4", "size": 6},
    "narrow_passage":  {"shape": "narrow", "color": "#FF9800", "size": 5},
}

TOPOLOGY_NODE_STYLES = {
    "entrance":          {"shape": "star",   "color": "#1976D2", "size": 7},
    "intersection":      {"shape": "dot",    "color": "#9E9E9E", "size": 3},
    "door":              {"shape": "circle", "color": "#4CAF50", "size": 3},
    "facility":          {"shape": "circle", "color": "#FF9800", "size": 4},
    "stair_access":      {"shape": "circle", "color": "#FF5722", "size": 5},
    "elevator_access":   {"shape": "circle", "color": "#E91E63", "size": 5},
}


def fmt(v):
    return f"{v:.1f}"


def build_node_lookup(geo_json):
    """构建全局 node_id → {floor, coordinates} 查找表"""
    lookup = {}
    for fk, fd in geo_json["floors"].items():
        for n in fd["topology"].get("nodes", []):
            lookup[n["id"]] = {
                "floor": int(fk),
                "coordinates": tuple(n["coordinates"]),
            }
    return lookup


def main():
    geo = json.load(open(GEO_IN, encoding="utf-8"))
    node_lookup = build_node_lookup(geo)

    # 计算范围
    min_x, min_y, max_x, max_y = float("inf"), float("inf"), float("-inf"), float("-inf")
    for fk in geo["floors"]:
        for room in geo["floors"][fk]["geometry"].get("rooms", []):
            for p in room["geometry"]["coordinates"][0]:
                min_x, min_y = min(min_x, p[0]), min(min_y, p[1])
                max_x, max_y = max(max_x, p[0]), max(max_y, p[1])

    svw = int((max_x - min_x) * SCALE + MARGIN_X * 2)
    svh_per_floor = int((max_y - min_y) * SCALE + MARGIN_Y * 2 + FLOOR_TITLE_H)
    svh = svh_per_floor * len(geo["floors"]) + 20
    ox, oy = min_x, max_y

    sorted_floors = sorted(geo["floors"].keys(), key=lambda x: int(x))
    total_windows = sum(
        len(geo["floors"][fk]["geometry"].get("windowSegments", []))
        for fk in sorted_floors
    )
    cf = geo.get("crossFloorEdges", [])
    n_cf_stair = sum(1 for e in cf if e.get("type") == "staircase")
    n_cf_elev = sum(1 for e in cf if e.get("type") == "elevator")

    parts = []

    # ── HTML head ──
    parts.append(f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>室内盲导 · 楼层可视化 v7</title>
<style>
body {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; background: #f8f9fa; margin: 0; padding: 20px; color: #333; }}
.header {{ max-width: 1200px; margin: 0 auto 15px; }}
.header h2 {{ margin: 0 0 4px; font-size: 20px; }}
.header .meta {{ color: #888; font-size: 12px; line-height: 1.6; }}
.header .meta .tag {{ display: inline-block; background: #E8F5E9; color: #2E7D32; padding: 1px 8px; border-radius: 3px; font-weight: bold; }}
#svg-container {{ position: relative; border: 1px solid #ddd; background: #fefefe; overflow: hidden; max-width: 1200px; margin: 0 auto; border-radius: 6px; }}
#svg-wrapper {{ overflow: hidden; cursor: default; }}
svg {{ display: block; background: #fff; }}
.layer_room polygon {{ opacity: 0.55; }}
.layer_wall path {{ stroke: #333; stroke-width: 0.8; fill: none; stroke-linecap: round; }}
.layer_window path {{ stroke: #81D4FA; stroke-width: 0.7; fill: none; stroke-dasharray: 4,2; }}
.layer_stairs path {{ stroke: #999; stroke-width: 0.5; fill: none; }}
.layer_elevator path {{ stroke: #E91E63; stroke-width: 0.8; fill: none; }}
.layer_column polygon {{ fill: #B0BEC5; stroke: #78909C; stroke-width: 0.3; opacity: 0.7; }}
.layer_door_normal circle {{ fill: #2196F3; opacity: 0.8; }}
.layer_door_fire rect {{ fill: #FF5722; opacity: 0.9; }}
.layer_door_label text {{ font-size: 5px; font-weight: bold; }}
.layer_topo_node circle {{ fill: none; }}
.layer_topo_edge path {{ stroke: #4CAF50; stroke-width: 0.3; fill: none; opacity: 0.4; stroke-dasharray: 2,1; }}
.layer_risk {{ }}
.layer_ramp {{ }}
.layer_tactile {{ }}
.layer_material {{ }}
.layer_crossfloor {{ }}
text {{ font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; }}
.zoom-controls {{ position: absolute; top: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; z-index: 10; }}
.zoom-btn {{ width: 36px; height: 36px; border: 1px solid #ccc; background: #fff; border-radius: 4px; cursor: pointer; font-size: 18px; display: flex; align-items: center; justify-content: center; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.zoom-btn:hover {{ background: #f0f0f0; }}
.zoom-info {{ position: absolute; bottom: 10px; right: 10px; background: rgba(255,255,255,0.9); padding: 4px 8px; border-radius: 4px; font-size: 12px; color: #666; z-index: 10; border: 1px solid #ddd; }}
.layer-controls {{ max-width: 1200px; margin: 10px auto; font-size: 12px; background: #fff; padding: 8px 14px; border-radius: 6px; border: 1px solid #e0e0e0; display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; }}
.layer-controls b {{ margin-right: 4px; }}
.layer-controls label {{ cursor: pointer; white-space: nowrap; user-select: none; }}
.layer-controls label input {{ margin-right: 3px; }}
.legend-panel {{ position: absolute; top: 10px; left: 10px; background: rgba(255,255,255,0.96); border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; z-index: 10; font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); max-height: 80vh; overflow-y: auto; max-width: 220px; }}
.legend-panel h4 {{ margin: 0 0 8px 0; font-size: 13px; color: #333; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
.legend-section {{ margin-bottom: 8px; }}
.legend-section-title {{ font-weight: bold; color: #555; margin-bottom: 3px; font-size: 11px; }}
.legend-item {{ display: flex; align-items: center; margin: 2px 0; line-height: 1.6; font-size: 11px; }}
.legend-swatch {{ width: 14px; height: 14px; margin-right: 6px; flex-shrink: 0; border-radius: 2px; border: 1px solid #ccc; }}
.legend-swatch.line {{ background: transparent; border: none; position: relative; }}
.legend-swatch.line::before {{ content: ''; position: absolute; top: 50%; left: 0; right: 0; height: 2px; transform: translateY(-50%); }}
.legend-swatch.door-normal {{ background: #2196F3; border-radius: 50%; border: none; width: 10px; height: 10px; margin-left: 2px; }}
.legend-swatch.door-fire {{ background: #FF5722; width: 10px; height: 10px; border: none; margin-left: 2px; }}
.legend-swatch.column {{ background: #B0BEC5; border: 1px solid #78909C; }}
.legend-swatch.topo-line::before {{ background: #4CAF50; }}
.legend-swatch.topo-node {{ background: transparent; border: 1.5px solid #4CAF50; border-radius: 50%; width: 10px; height: 10px; margin-left: 2px; }}
.legend-swatch.star {{ background: #1976D2; width: 12px; height: 12px; margin-left: 1px; clip-path: polygon(50% 0%, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%); }}
.legend-swatch.droplet {{ background: #00BCD4; border-radius: 50% 0 50% 50%; transform: rotate(45deg); width: 10px; height: 10px; margin-left: 2px; }}
.legend-swatch.narrow {{ background: #FF9800; width: 14px; height: 5px; margin-left: 0; clip-path: polygon(0% 0%, 100% 0%, 70% 100%, 30% 100%); }}
.legend-swatch.tactile-line::before {{ background: #FFD600; height: 3px; }}
.legend-swatch.window-line::before {{ background: #81D4FA; border-bottom: 2px dashed #81D4FA; height: 0; }}
.legend-swatch.ramp-line::before {{ background: none; border-bottom: 2px dashed #4CAF50; height: 0; }}
.legend-swatch.risk {{ background: #F44336; border-radius: 50%; opacity: 0.8; width: 12px; height: 12px; margin-left: 1px; }}
.legend-swatch.risk-glass {{ background: transparent; border: 1.5px solid #2196F3; width: 12px; height: 10px; }}
.legend-swatch.risk-wet {{ background: #00BCD4; opacity: 0.7; width: 10px; height: 12px; border-radius: 40% 0 40% 40%; transform: rotate(45deg); }}
.legend-swatch.risk-narrow {{ background: #FF9800; opacity: 0.7; width: 14px; height: 6px; }}
.legend-swatch.material {{ background: #9C27B0; width: 12px; height: 12px; transform: rotate(45deg); margin: 0 5px; }}
.legend-swatch.crossfloor::before {{ background: #9C27B0; border-bottom: 2px dashed #9C27B0; height: 0; }}
.legend-swatch.stair-access {{ background: #FF5722; border-radius: 50%; width: 10px; height: 10px; opacity: 0.8; margin-left: 2px; }}
.legend-swatch.elev-access {{ background: #E91E63; border-radius: 50%; width: 10px; height: 10px; opacity: 0.8; margin-left: 2px; }}
</style></head><body>
<div class="header">
  <h2>初中学部1#教学楼 · 楼层布局图 <span class="tag">v7</span></h2>
  <p class="meta">
    坐标系: <b>米制局部坐标系</b>（原点=({ox:.1f},{oy:.1f})pt 归一化，x向右 y向上）&nbsp;|&nbsp;
    缩放: 1m = {SCALE:.0f}px &nbsp;|&nbsp;
    范围: x∈[{min_x:.1f}, {max_x:.1f}]m, y∈[{min_y:.1f}, {max_y:.1f}]m &nbsp;|&nbsp;
    建筑尺寸: ~{max_x-min_x:.0f}m × {max_y-min_y:.0f}m
  </p>
  <p class="meta" style="margin-top:2px">
    v7 更新: 基于PDF最新OCG可见性重新提取 · 窗户段长度过滤(保留{total_windows}条) · 四层语义地图方案(solution.md)
  </p>
</div>
<div class="layer-controls">
  <b>图层:</b>
  <label style="cursor:pointer;color:#1565C0;font-weight:bold;margin-right:6px" onclick="selectAllLayers()">全选</label>
  <label style="cursor:pointer;color:#888;margin-right:10px" onclick="deselectAllLayers()">取消</label>
  <label><input type="checkbox" id="cb_room" checked onchange="toggleLayer('room')"> 房间</label>
  <label><input type="checkbox" id="cb_wall" checked onchange="toggleLayer('wall')"> 墙体</label>
  <label><input type="checkbox" id="cb_window" checked onchange="toggleLayer('window')"> 窗户</label>
  <label><input type="checkbox" id="cb_stairs" checked onchange="toggleLayer('stairs')"> 楼梯</label>
  <label><input type="checkbox" id="cb_elevator" checked onchange="toggleLayer('elevator')"> 电梯</label>
  <label><input type="checkbox" id="cb_column" checked onchange="toggleLayer('column')"> 柱子</label>
  <label><input type="checkbox" id="cb_door_normal" checked onchange="toggleLayer('door_normal')"> 普通门</label>
  <label><input type="checkbox" id="cb_door_fire" checked onchange="toggleLayer('door_fire')"> 防火门</label>
  <label><input type="checkbox" id="cb_door_label" checked onchange="toggleLayer('door_label')"> 门编号</label>
  <label><input type="checkbox" id="cb_crossfloor" checked onchange="toggleLayer('crossfloor')"> 跨层连接</label>
  <label><input type="checkbox" id="cb_topo" onchange="toggleLayer('topo_node');toggleLayer('topo_edge')"> 拓扑</label>
  <label><input type="checkbox" id="cb_risk" onchange="toggleLayer('risk')"> 风险节点</label>
  <label><input type="checkbox" id="cb_ramp" onchange="toggleLayer('ramp')"> 坡道</label>
  <label><input type="checkbox" id="cb_tactile" onchange="toggleLayer('tactile')"> 盲道路径</label>
  <label><input type="checkbox" id="cb_material" onchange="toggleLayer('material')"> 材质变化</label>
</div>
<div id="svg-container">
<div class="legend-panel" id="legendPanel">
  <h4>图例说明 (v7)</h4>
  <div class="legend-section">
    <div class="legend-section-title">建筑要素</div>
    <div class="legend-item"><div class="legend-swatch line" style="background:#333;height:2px;border:none"></div>墙体</div>
    <div class="legend-item"><div class="legend-swatch line window-line"></div>窗户段</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#FFF9C4"></div>房间/教室</div>
    <div class="legend-item"><div class="legend-swatch column"></div>柱子</div>
  </div>
  <div class="legend-section">
    <div class="legend-section-title">门</div>
    <div class="legend-item"><div class="legend-swatch door-normal"></div>普通门</div>
    <div class="legend-item"><div class="legend-swatch door-fire"></div>防火门</div>
  </div>
  <div class="legend-section">
    <div class="legend-section-title">拓扑节点 (v7)</div>
    <div class="legend-item"><div class="legend-swatch star"></div>出入口 (entrance)</div>
    <div class="legend-item"><div class="legend-swatch topo-node"></div>门节点</div>
    <div class="legend-item"><div class="legend-swatch" style="background:#9E9E9E;border-radius:50%;width:6px;height:6px;margin-left:6px"></div>交叉点 (intersection)</div>
    <div class="legend-item"><div class="legend-swatch stair-access"></div>楼梯接入</div>
    <div class="legend-item"><div class="legend-swatch elev-access"></div>电梯接入</div>
  </div>
  <div class="legend-section">
    <div class="legend-section-title">风险节点</div>
    <div class="legend-item"><div class="legend-swatch risk"></div>楼梯口风险</div>
    <div class="legend-item"><div class="legend-swatch risk-glass"></div>玻璃门风险</div>
    <div class="legend-item"><div class="legend-swatch risk-wet"></div>湿滑地面</div>
    <div class="legend-item"><div class="legend-swatch risk-narrow"></div>窄道风险</div>
  </div>
  <div class="legend-section">
    <div class="legend-section-title">无障碍</div>
    <div class="legend-item"><div class="legend-swatch line ramp-line"></div>坡道</div>
    <div class="legend-item"><div class="legend-swatch line tactile-line"></div>盲道路径</div>
    <div class="legend-item"><div class="legend-swatch material"></div>地面材质变化</div>
  </div>
  <div class="legend-section">
    <div class="legend-section-title">跨楼层</div>
    <div class="legend-item"><div class="legend-swatch line crossfloor"></div>跨楼层连接</div>
  </div>
</div>
<div id="svg-wrapper">
<svg id="main-svg" xmlns="http://www.w3.org/2000/svg" width="{svw}" height="{svh}" style="display:block">
''')

    # ── 逐层生成 SVG ──
    for i, fk in enumerate(sorted_floors):
        fbase_y = i * svh_per_floor
        floor = int(fk)
        fd = geo["floors"][fk]
        geom = fd["geometry"]
        topo = fd["topology"]
        acc = fd["accessibility"]

        def tosvg(cx, cy):
            sx = MARGIN_X + (cx - ox) * SCALE
            sy = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - cy) * SCALE
            return fmt(sx), fmt(sy)

        title_cn = "首层" if floor == 1 else "二层"
        n_wall = len(geom.get("walls", []))
        n_room = len(geom.get("rooms", []))
        n_door = len(geom.get("doors", []))
        n_stair = len(geom.get("stairs", []))
        n_elev = len(geom.get("elevators", []))
        n_col = len(geom.get("columns", []))
        n_win = len(geom.get("windowSegments", []))
        n_node = len(topo.get("nodes", []))
        n_edge = len(topo.get("edges", []))
        n_risk = len(acc.get("riskNodes", []))
        n_ramp = len(acc.get("ramps", []))
        n_tp = len(acc.get("tactilePaths", []))
        n_gmc = len(acc.get("groundMaterialChanges", []))

        parts.append(f'<!-- Floor {fk} -->\n')
        parts.append(
            f'<text x="20" y="{fbase_y + 24}" font-size="14" font-weight="bold" fill="#333">'
            f'{title_cn} {floor}F (v7 米制)</text>\n'
        )
        stats = (
            f'墙:{n_wall} 窗:{n_win} 房间:{n_room} 门:{n_door} '
            f'楼梯:{n_stair} 电梯:{n_elev} 柱:{n_col} '
            f'节点:{n_node} 边:{n_edge} 风险:{n_risk} '
            f'坡道:{n_ramp} 盲道:{n_tp} 材质:{n_gmc}'
        )
        parts.append(
            f'<text x="180" y="{fbase_y + 24}" font-size="9" fill="#999">{stats}</text>\n'
        )

        # 1. 房间
        for f in geom.get("rooms", []):
            ring = f["geometry"]["coordinates"][0]
            pts = " ".join(
                f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring
            )
            rtype = f["properties"].get("roomType", "room")
            color = ROOM_COLORS.get(rtype, "#FAFAFA")
            label = f["properties"].get("label", "")
            parts.append(
                f'<g class="layer_room"><polygon points="{pts}" fill="{color}" '
                f'stroke="#999" stroke-width="0.5"/></g>\n'
            )
            if label:
                cx_s = sum(p[0] for p in ring[:-1]) / max(len(ring) - 1, 1)
                cy_s = sum(p[1] for p in ring[:-1]) / max(len(ring) - 1, 1)
                sx_s, sy_s = tosvg(cx_s, cy_s)
                parts.append(
                    f'<g class="layer_room"><text x="{sx_s}" y="{sy_s}" '
                    f'font-size="6" text-anchor="middle" fill="#333">{label}</text></g>\n'
                )

        # 2. 墙体
        for f in geom.get("walls", []):
            c = f["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            parts.append(
                f'<g class="layer_wall"><path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

        # 3. 窗户段
        for f in geom.get("windowSegments", []):
            c = f["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            parts.append(
                f'<g class="layer_window"><path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

        # 4. 楼梯 (Polygon)
        for f in geom.get("stairs", []):
            ring = f["geometry"]["coordinates"][0]
            pts = " ".join(
                f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring
            )
            label_s = f["properties"].get("label", "")
            parts.append(
                f'<g class="layer_stairs"><polygon points="{pts}" '
                f'fill="#FFCCBC" stroke="#E64A19" stroke-width="0.8" opacity="0.6"/></g>\n'
            )
            if label_s:
                cent = f["properties"].get("centroid")
                if cent:
                    sx_s, sy_s = tosvg(cent[0], cent[1])
                    parts.append(
                        f'<g class="layer_stairs"><text x="{sx_s}" y="{sy_s}" '
                        f'font-size="5" text-anchor="middle" fill="#BF360C">{label_s}</text></g>\n'
                    )

        # 5. 电梯 (Polygon)
        for f in geom.get("elevators", []):
            ring = f["geometry"]["coordinates"][0]
            pts = " ".join(
                f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring
            )
            label_e = f["properties"].get("label", "")
            parts.append(
                f'<g class="layer_elevator"><polygon points="{pts}" '
                f'fill="#F8BBD0" stroke="#C2185B" stroke-width="0.8" opacity="0.6"/></g>\n'
            )
            if label_e:
                cent = f["properties"].get("centroid")
                if cent:
                    sx_s, sy_s = tosvg(cent[0], cent[1])
                    parts.append(
                        f'<g class="layer_elevator"><text x="{sx_s}" y="{sy_s}" '
                        f'font-size="5" text-anchor="middle" fill="#880E4F">{label_e}</text></g>\n'
                    )

        # 6. 柱子
        for f in geom.get("columns", []):
            ring = f["geometry"]["coordinates"][0]
            pts = " ".join(
                f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring
            )
            parts.append(
                f'<g class="layer_column"><polygon points="{pts}"/></g>\n'
            )

        # 7. 门
        for f in geom.get("doors", []):
            c = f["geometry"]["coordinates"]
            sx, sy = tosvg(c[0], c[1])
            dtype = f["properties"].get("doorType", "normal")
            did = f["properties"].get("doorId", "")
            if dtype == "fire":
                parts.append(
                    f'<g class="layer_door_fire">'
                    f'<rect x="{float(sx)-3}" y="{float(sy)-3}" width="6" height="6"/>'
                    f'</g>\n'
                )
                if did:
                    parts.append(
                        f'<g class="layer_door_label">'
                        f'<text x="{float(sx)+4}" y="{float(sy)+2}" fill="#FF5722">{did}</text>'
                        f'</g>\n'
                    )
            else:
                parts.append(
                    f'<g class="layer_door_normal">'
                    f'<circle cx="{sx}" cy="{sy}" r="2"/>'
                    f'</g>\n'
                )

        # 8. 拓扑节点
        node_map = {n["id"]: n for n in topo.get("nodes", [])}
        for n in topo.get("nodes", []):
            cx, cy = n["coordinates"]
            sx, sy = tosvg(cx, cy)
            ntype = n.get("type", "door")
            style = TOPOLOGY_NODE_STYLES.get(ntype, TOPOLOGY_NODE_STYLES["door"])
            label_n = n.get("label", "")
            size = style["size"]

            if style["shape"] == "star":
                outer, inner = size, size * 0.4
                svg_pts = []
                for k in range(10):
                    angle = math.pi / 2 + k * math.pi / 5
                    r = outer if k % 2 == 0 else inner
                    svg_pts.append(
                        f"{float(sx) + r * math.cos(angle):.1f},"
                        f"{float(sy) - r * math.sin(angle):.1f}"
                    )
                parts.append(
                    f'<g class="layer_topo_node" style="display:none">'
                    f'<polygon points="{" ".join(svg_pts)}" '
                    f'fill="{style["color"]}" opacity="0.85"/></g>\n'
                )
                if label_n:
                    parts.append(
                        f'<g class="layer_topo_node" style="display:none">'
                        f'<text x="{float(sx)+size+3}" y="{float(sy)+3}" '
                        f'font-size="5" fill="{style["color"]}">{label_n}</text></g>\n'
                    )
            elif style["shape"] == "dot":
                parts.append(
                    f'<g class="layer_topo_node" style="display:none">'
                    f'<circle cx="{sx}" cy="{sy}" r="{size}" '
                    f'fill="{style["color"]}" opacity="0.6"/></g>\n'
                )
            else:
                parts.append(
                    f'<g class="layer_topo_node" style="display:none">'
                    f'<circle cx="{sx}" cy="{sy}" r="{size}" '
                    f'fill="none" stroke="{style["color"]}" stroke-width="0.8"/></g>\n'
                )

        # 9. 拓扑边
        for e in topo.get("edges", []):
            n1 = node_map.get(e.get("from"))
            n2 = node_map.get(e.get("to"))
            if not n1 or not n2:
                continue
            x1, y1 = tosvg(n1["coordinates"][0], n1["coordinates"][1])
            x2, y2 = tosvg(n2["coordinates"][0], n2["coordinates"][1])
            parts.append(
                f'<g class="layer_topo_edge" style="display:none">'
                f'<path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

        # 10. 风险节点
        for r in acc.get("riskNodes", []):
            cx, cy = r["coordinates"]
            sx, sy = tosvg(cx, cy)
            rtype = r.get("type", r.get("riskType", "stair_entrance"))
            rlabel = r.get("label", "")
            style_r = RISK_STYLES.get(rtype, RISK_STYLES["stair_entrance"])
            size_r = style_r["size"]

            if style_r["shape"] == "triangle":
                pts_r = (
                    f"{float(sx)},{float(sy)-size_r*0.67:.1f} "
                    f"{float(sx)-size_r*0.5:.1f},{float(sy)+size_r*0.33:.1f} "
                    f"{float(sx)+size_r*0.5:.1f},{float(sy)+size_r*0.33:.1f}"
                )
                parts.append(
                    f'<g class="layer_risk" style="display:none">'
                    f'<polygon points="{pts_r}" fill="none" '
                    f'stroke="{style_r["color"]}" stroke-width="1.2"/>'
                    f'<text x="{sx}" y="{float(sy)+size_r*0.67:.1f}" '
                    f'font-size="3.5" text-anchor="middle" fill="{style_r["color"]}">!</text></g>\n'
                )
            elif style_r["shape"] == "droplet":
                parts.append(
                    f'<g class="layer_risk" style="display:none">'
                    f'<ellipse cx="{sx}" cy="{float(sy)-1}" '
                    f'rx="{size_r*0.55:.1f}" ry="{size_r*0.7:.1f}" '
                    f'fill="{style_r["color"]}" opacity="0.7"/>'
                    f'<polygon points="{float(sx)-size_r*0.55:.1f},{float(sy)-1} '
                    f'{float(sx)+size_r*0.55:.1f},{float(sy)-1} '
                    f'{sx},{float(sy)+size_r*0.35:.1f}" '
                    f'fill="{style_r["color"]}" opacity="0.7"/></g>\n'
                )
            elif style_r["shape"] == "narrow":
                w = size_r * 0.4
                pts_r = (
                    f"{float(sx)-size_r*0.5:.1f},{float(sy)-w:.1f} "
                    f"{float(sx)+size_r*0.5:.1f},{float(sy)-w:.1f} "
                    f"{float(sx)+size_r*0.3:.1f},{float(sy)+w:.1f} "
                    f"{float(sx)-size_r*0.3:.1f},{float(sy)+w:.1f}"
                )
                parts.append(
                    f'<g class="layer_risk" style="display:none">'
                    f'<polygon points="{pts_r}" fill="{style_r["color"]}" '
                    f'opacity="0.6" stroke="{style_r["color"]}" stroke-width="0.5"/></g>\n'
                )
            else:
                parts.append(
                    f'<g class="layer_risk" style="display:none">'
                    f'<circle cx="{sx}" cy="{sy}" r="{size_r}" '
                    f'fill="{style_r["color"]}" opacity="0.6"/></g>\n'
                )
            if rlabel:
                parts.append(
                    f'<g class="layer_risk" style="display:none">'
                    f'<text x="{float(sx)+size_r+2}" y="{float(sy)+2}" '
                    f'font-size="4" fill="{style_r["color"]}">{rlabel}</text></g>\n'
                )

        # 11. 坡道
        for rp in acc.get("ramps", []):
            loc = rp.get("location", rp.get("coordinates", [0, 0]))
            sx, sy = tosvg(loc[0], loc[1])
            rid = rp.get("id", "")
            parts.append(
                f'<g class="layer_ramp" style="display:none">'
                f'<circle cx="{sx}" cy="{sy}" r="8" fill="none" '
                f'stroke="#4CAF50" stroke-width="1.5" stroke-dasharray="3,2"/>'
                f'<text x="{float(sx)+10}" y="{float(sy)-5}" '
                f'font-size="5" fill="#2E7D32">{rid}</text></g>\n'
            )

        # 12. 盲道路径
        for tp in acc.get("tactilePaths", []):
            path = tp.get("path", [])
            if len(path) < 2:
                continue
            d_parts = []
            for pt in path:
                sx_pt, sy_pt = tosvg(pt[0], pt[1])
                if not d_parts:
                    d_parts.append(f"M {sx_pt} {sy_pt}")
                else:
                    d_parts.append(f"L {sx_pt} {sy_pt}")
            d_str = " ".join(d_parts)
            parts.append(
                f'<g class="layer_tactile" style="display:none">'
                f'<path d="{d_str}" stroke="#FFD600" stroke-width="1.8" '
                f'fill="none" stroke-linecap="round" opacity="0.8"/></g>\n'
            )
            parts.append(
                f'<g class="layer_tactile" style="display:none">'
                f'<path d="{d_str}" stroke="#FF8F00" stroke-width="1.8" '
                f'fill="none" stroke-dasharray="2,5" stroke-linecap="round" opacity="0.6"/></g>\n'
            )

        # 13. 地面材质变化
        for gmc in acc.get("groundMaterialChanges", []):
            pt = gmc.get("coordinates", [0, 0])
            sx, sy = tosvg(pt[0], pt[1])
            sz = 6
            pts_d = (
                f"{sx},{float(sy)-sz} "
                f"{float(sx)+sz*0.7:.1f},{sy} "
                f"{sx},{float(sy)+sz} "
                f"{float(sx)-sz*0.7:.1f},{sy}"
            )
            desc_g = gmc.get("description", gmc.get("id", ""))[:25]
            parts.append(
                f'<g class="layer_material" style="display:none">'
                f'<polygon points="{pts_d}" fill="#9C27B0" opacity="0.75" '
                f'stroke="#6A1B9A" stroke-width="0.5"/>'
                f'<text x="{float(sx)+9}" y="{float(sy)+3}" '
                f'font-size="4" fill="#6A1B9A">{desc_g}</text></g>\n'
            )

        # 楼层分隔线
        if i < len(sorted_floors) - 1:
            sep_y = (i + 1) * svh_per_floor
            parts.append(
                f'<line x1="0" y1="{sep_y}" x2="{svw}" y2="{sep_y}" '
                f'stroke="#e0e0e0" stroke-width="1" stroke-dasharray="4,2"/>\n'
            )

    # ── 跨楼层连接线 ──
    # v7: crossFloorEdges 使用 from/to 是 node ID，需通过 node_lookup 解析坐标
    for e in cf:
        from_id = e.get("from", "")
        to_id = e.get("to", "")
        from_info = node_lookup.get(from_id)
        to_info = node_lookup.get(to_id)

        if not from_info or not to_info:
            continue

        f1, f2 = from_info["floor"], to_info["floor"]
        c1 = from_info["coordinates"]
        c2 = to_info["coordinates"]

        idx1 = sorted_floors.index(str(f1)) if str(f1) in sorted_floors else 0
        idx2 = sorted_floors.index(str(f2)) if str(f2) in sorted_floors else 0
        base1 = idx1 * svh_per_floor + FLOOR_TITLE_H + MARGIN_Y
        base2 = idx2 * svh_per_floor + FLOOR_TITLE_H + MARGIN_Y

        sx1 = MARGIN_X + (c1[0] - ox) * SCALE
        sy1 = base1 + (oy - c1[1]) * SCALE
        sx2 = MARGIN_X + (c2[0] - ox) * SCALE
        sy2 = base2 + (oy - c2[1]) * SCALE

        etype = e.get("type", "")
        if etype == "staircase":
            line_color, text_color = "#E53935", "#C62828"
            etype_label = "楼梯连接"
        elif etype == "elevator":
            line_color, text_color = "#1E88E5", "#1565C0"
            etype_label = "电梯连接"
        else:
            line_color, text_color = "#9C27B0", "#7B1FA2"
            etype_label = etype

        mid_x = (float(sx1) + float(sx2)) / 2
        mid_y = (float(sy1) + float(sy2)) / 2
        eid = e.get("id", "")
        blind_ok = "✓" if e.get("blindAccessible") else "✗"
        wheel_ok = "✓" if e.get("wheelchairAccessible") else "✗"

        parts.append(
            f'<g class="layer_crossfloor">'
            f'<path d="M {fmt(sx1)} {fmt(sy1)} L {fmt(sx2)} {fmt(sy2)}" '
            f'stroke="{line_color}" stroke-width="1.5" fill="none" '
            f'stroke-dasharray="6,4" opacity="0.6"/>'
            f'<circle cx="{fmt(sx1)}" cy="{fmt(sy1)}" r="3" fill="{line_color}" opacity="0.6"/>'
            f'<circle cx="{fmt(sx2)}" cy="{fmt(sy2)}" r="3" fill="{line_color}" opacity="0.6"/>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y - 10)}" font-size="6" '
            f'fill="{text_color}" text-anchor="middle" opacity="0.78">{eid}</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y + 4)}" font-size="5" '
            f'fill="{text_color}" text-anchor="middle" opacity="0.7">'
            f'{etype_label} ({f1}F↔{f2}F)</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y + 15)}" font-size="4" '
            f'fill="{text_color}" text-anchor="middle" opacity="0.65">'
            f'盲:{blind_ok} 轮椅:{wheel_ok}</text></g>\n'
        )

    # ── 底部信息 ──
    cf_ids = ", ".join(e.get("id", "?") for e in cf) if cf else "无"
    parts.append(
        f'<text x="20" y="{svh - 8}" font-size="9" fill="#999">'
        f'跨楼层连接: {len(cf)} 条 ({cf_ids}) | '
        f'建筑: {geo.get("venueName", "")} | 版本: {geo.get("version", "?")}</text>\n'
    )

    # ── JS 交互 ──
    parts.append(f'''</svg>
</div>
<div class="zoom-controls">
  <button class="zoom-btn" onclick="zoomIn()" title="放大">+</button>
  <button class="zoom-btn" onclick="zoomOut()" title="缩小">-</button>
  <button class="zoom-btn" onclick="resetView()" title="重置">&#8634;</button>
</div>
<div class="zoom-info" id="zoom-info">缩放: 100%</div>
</div>
<script>
var svg = document.getElementById('main-svg');
var wrapper = document.getElementById('svg-wrapper');
var scale = 1;
var translateX = 0, translateY = 0;
var isDragging = false;
var startX = 0, startY = 0;

function applyTransform() {{
  svg.style.transform = 'translate(' + translateX + 'px, ' + translateY + 'px) scale(' + scale + ')';
  svg.style.transformOrigin = '0 0';
  document.getElementById('zoom-info').textContent = '缩放: ' + Math.round(scale * 100) + '%';
}}

function zoomIn() {{ scale = Math.min(scale * 1.3, 20); applyTransform(); }}
function zoomOut() {{ scale = Math.max(scale / 1.3, 0.15); applyTransform(); }}
function resetView() {{ scale = 1; translateX = 0; translateY = 0; applyTransform(); }}

wrapper.addEventListener('wheel', function(e) {{
  e.preventDefault();
  var delta = e.deltaY > 0 ? 0.9 : 1.1;
  var rect = wrapper.getBoundingClientRect();
  var mouseX = e.clientX - rect.left;
  var mouseY = e.clientY - rect.top;
  var newScale = Math.max(0.15, Math.min(20, scale * delta));
  var ratio = newScale / scale;
  translateX = mouseX - ratio * (mouseX - translateX);
  translateY = mouseY - ratio * (mouseY - translateY);
  scale = newScale;
  applyTransform();
}}, {{ passive: false }});

wrapper.addEventListener('mousedown', function(e) {{
  isDragging = true;
  startX = e.clientX - translateX;
  startY = e.clientY - translateY;
}});

document.addEventListener('mousemove', function(e) {{
  if (!isDragging) return;
  wrapper.style.cursor = 'grabbing';
  translateX = e.clientX - startX;
  translateY = e.clientY - startY;
  applyTransform();
}});

document.addEventListener('mouseup', function() {{
  isDragging = false;
  wrapper.style.cursor = 'default';
}});

var allLayers = ['room','wall','window','stairs','elevator','column',
  'door_normal','door_fire','door_label','crossfloor',
  'topo_node','topo_edge','risk','ramp','tactile','material'];

function setAllLayers(visible) {{
  var display = visible ? '' : 'none';
  allLayers.forEach(function(name) {{
    var els = document.querySelectorAll('.layer_' + name);
    els.forEach(function(el) {{ el.style.display = display; }});
  }});
  var cbIds = ['cb_room','cb_wall','cb_window','cb_stairs','cb_elevator',
    'cb_column','cb_door_normal','cb_door_fire','cb_door_label',
    'cb_crossfloor','cb_topo','cb_risk','cb_ramp','cb_tactile','cb_material'];
  cbIds.forEach(function(id) {{
    var cb = document.getElementById(id);
    if (cb) cb.checked = visible;
  }});
}}

function selectAllLayers() {{ setAllLayers(true); }}
function deselectAllLayers() {{ setAllLayers(false); }}

function toggleLayer(name) {{
  var els = document.querySelectorAll('.layer_' + name);
  els.forEach(function(el) {{
    el.style.display = el.style.display === 'none' ? '' : 'none';
  }});
}}
</script>
</body></html>''')

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write("".join(parts))

    print(f"已生成: {HTML_OUT}")
    print(f"  SVG: {svw} × {svh} px")
    print(f"  坐标范围: x[{min_x:.1f}, {max_x:.1f}], y[{min_y:.1f}, {max_y:.1f}]")
    print(f"  楼层: {len(sorted_floors)} 层")
    print(f"  跨楼层连接: {len(cf)} 条")


if __name__ == "__main__":
    main()
