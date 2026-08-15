# -*- coding: utf-8 -*-
"""交互式网页版楼层渲染 —— 读取 parse_cad_pdf.py 生成的 v9 GeoJSON，
生成一个自包含的 HTML（SVG + 原生 JS），支持：
  - 缩放 / 平移（滚轮 + 拖拽 + 按钮）
  - 图层开关（房间/墙体/窗/楼梯/电梯/柱/各类门/拓扑节点·边/跨层/无障碍）
  - 悬停提示（tooltip）与点击查看详情（右侧面板）
  - 楼层快速跳转（1F / 2F）
  - 点击拓扑节点高亮其相连边

参照 render_v7.py 的坐标变换与图层组织方式，但面向 v9 schema
（拓扑节点类型 room/doorway/intersection/facility/facility_entrance；
门类型 swing/fire/opening；边用 from/to 节点 id 引用）。

用法:
    python -m src.rendering.render_interactive [geojson_path]
默认输入 result/school_building_01_map_v9.geojson，输出 result/floor_layout_v9_interactive.html
"""
import collections
import json
import math
from pathlib import Path

# 路由规则常量统一来源 src/common/constants.py（D4/A2）；
# 本脚本可独立运行，故无法导入 src 包时兜底同值（兜底值必须与 constants 保持一致，
# test_invariants 会核对；前端 Dijkstra 常量一律通过 build_path_rules_js 序列化注入）。
try:
    from src.common.constants import (
        DOOR_PENALTY,
        DOOR_DEFAULT_PENALTY,
        SAME_FLOOR_MID_TYPES,
        CROSS_FLOOR_MID_TYPES,
    )
except ImportError:
    DOOR_PENALTY = {"swing": 0.0, "fire": 0.5, "opening": 1.0}
    DOOR_DEFAULT_PENALTY = 9.0
    SAME_FLOOR_MID_TYPES = {"intersection", "facility_entrance", "doorway"}
    CROSS_FLOOR_MID_TYPES = SAME_FLOOR_MID_TYPES | {"facility"}

# B2：建筑外轮廓纯 Python 计算已下沉到 src/geometry/contour.py（parsing 与 rendering 共用）。
# 正式运行方式：`pathai-render`（pip install -e . 后）或 `python -m src.rendering.render_interactive`。
# 不再在模块导入时修改 sys.path（审查 B3：移除包内导入副作用）。
from src.geometry.contour import _area, building_outline
# C4：路由规则辅助量由 build_geojson 一次性写入顶层 routeExtras，渲染端直接读取；
# 旧 GeoJSON 缺少该字段时回退到共享计算（需 shapely，独立运行环境同样具备）。
try:
    from src.io.geojson_writer import compute_route_rule_extras
except ImportError:  # 无 shapely 环境：GeoJSON 带 routeExtras 时仍可渲染
    compute_route_rule_extras = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GEO_IN = str(BASE_DIR / "result" / "school_building_01_map_v9.geojson")
HTML_OUT = str(BASE_DIR / "result" / "floor_layout_v9_interactive.html")

SCALE = 7.0          # 1m = 7px
MARGIN_X = 50
MARGIN_Y = 30
FLOOR_TITLE_H = 46

# 房间类型配色（v9 roomType 关键词）
ROOM_COLORS = {
    "classroom": "#FFF9C4", "office": "#D7CCC8", "meeting": "#F8BBD0",
    "toilet": "#B2DFDB", "corridor": "#F5F5F5", "lobby": "#FFF3E0",
    "staircase": "none", "elevator_hall": "#F8BBD0", "storage": "#CFD8DC",
    "equipment": "#B0BEC5", "medical": "#FFEBEE", "lab": "#B3E5FC",
    "reception": "#FCE4EC", "infrastructure": "#ECEFF1", "atrium": "#FAFAFA",
    "library": "#DCEDC8", "activity": "#E1F5FE", "entrance": "#C8E6C9",
    "accessible_entrance": "#BBDEFB", "room": "#FAFAFA", "other": "#FAFAFA",
    "elevator_lobby": "#FFE0B2", "stair_lobby": "#D7CCC8",
}
DOOR_COLORS = {"swing": "#2196F3", "fire": "#FF5722", "opening": "#1E8449",
               "fire_closed": "#8B0000"}  # 常闭防火门：暗红
# 门类型中文名（与 topology.py 的 doorway 节点 label 保持一致）
DOOR_TYPE_CN = {"swing": "普通门", "fire": "防火门", "opening": "门洞"}
NODE_COLORS = {
    "room": "#E67E22", "doorway": "#C0392B", "intersection": "#27AE60",
    "facility_entrance": "#2980B9",
}
FACILITY_COLORS = {"staircase": "#8E44AD", "elevator": "#16A085"}
# 信标部署点配色（与图例一致）：交叉口/门口/楼梯/电梯/走廊覆盖点 + 三点定位/放置质量语义
BEACON_COLORS = {
    "intersection": "#FB8C00",
    "door": "#8E24AA",
    "stair": "#E53935",
    "elevator": "#1E88E5",
    "entrance": "#43A047",
    "corridor": "#00897B",
    "trilateration_base": "#1565C0",        # 全楼三点定位：基础信标（深蓝）
    "trilateration_fill": "#6A1B9A",        # 全楼三点定位：覆盖补点（深紫）
    "trilateration_route_base": "#0277BD",  # 路线三点定位：基础信标（亮蓝）
    "trilateration_route_fill": "#4A148C",  # 路线三点定位：覆盖补点（亮紫）
    "placement_quality_fill": "#EF6C00",    # 放置质量后处理补点（深橙）
}

# 建筑外轮廓：面积过滤阈值（m²）。小于该值的连通块视为家具/孤立柱簇噪声，不绘制。
OUTLINE_MIN_AREA = 100.0


def fmt(v):
    return f"{v:.1f}"


# 文字标签包围盒启发式过滤阈值（CAD PDF 中部分文字标签的矢量矩形被
# 误识别为房间多边形，会与真实房间重叠。典型特征：面积小 + 形状窄长。）
LABEL_BBOX_MAX_AREA = 6.0     # m²，小于此值才有嫌疑
LABEL_BBOX_MIN_ASPECT = 1.5   # 长边/短边，超过此值判定为文字框


def _is_label_bbox(ring):
    """判定闭合多边形是否疑似文字标签的包围盒（小面积 + 窄长形状）。"""
    xs = [p[0] for p in ring[:-1]]
    ys = [p[1] for p in ring[:-1]]
    if len(xs) < 3:
        return True
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return True
    a = 0.0
    for j in range(len(ring) - 1):
        a += ring[j][0] * ring[j + 1][1] - ring[j + 1][0] * ring[j][1]
    a = abs(a) / 2.0
    if a >= LABEL_BBOX_MAX_AREA:
        return False
    aspect = max(w, h) / min(w, h)
    return aspect >= LABEL_BBOX_MIN_ASPECT


def info_attr(d, floor=None, coll=None, pid=None, store=None, key=None):
    """把任意可序列化 dict 编码为 data-info 属性（JS 端 JSON.parse）。
    floor/coll/pid/store/key 用于前端「详情编辑」定位 GeoJSON 元素（排除 id）。
      floor: 楼层整数；coll: 集合名(rooms/doors/nodes/edges/stairs/elevators/columns/crossFloorEdges)
      pid:   元素 id 值；store: 数组所在容器(geometry/topology/root)；key: 定位字段名(默认 id)
    """
    if isinstance(d, dict) and isinstance(d.get("detail"), dict):
        meta = {}
        if floor is not None:
            meta["_edit_floor"] = floor
        if coll is not None:
            meta["_edit_coll"] = coll
        if pid is not None:
            meta["_edit_pid"] = pid
        if store is not None:
            meta["_edit_store"] = store
        if key is not None:
            meta["_edit_key"] = key
        if meta:
            nd = dict(d["detail"])
            nd.update(meta)
            d = dict(d)
            d["detail"] = nd
    s = json.dumps(d, ensure_ascii=False).replace("'", "\\'")
    return "data-info='" + s + "'"


def link_obj(tid, text=None):
    """构造「可点击 ID 链接对象」：JS 端 renderCell 识别 _l 字段，
    渲染为点击后居中定位到对应 SVG 元素的超链接。tid 为目标要素/节点 ID。"""
    return {"_l": tid, "t": text if text is not None else tid}


def _centroid_ring(ring):
    """多边形外环质心（CAD 坐标）。ring 首尾可能重合，需去重计数。"""
    n = len(ring) - 1 if ring and ring[0] == ring[-1] else len(ring)
    if n <= 0:
        return 0.0, 0.0
    xs = [p[0] for p in ring[:n]]
    ys = [p[1] for p in ring[:n]]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def build_node_lookup(geo_json):
    lookup = {}
    for fk, fd in geo_json["floors"].items():
        for n in fd.get("topology", {}).get("nodes", []):
            lookup[n["id"]] = {"floor": int(fk), "coordinates": tuple(n["coordinates"])}
    return lookup


def build_path_rules_js():
    """路由规则数据序列化（A2：前端 Dijkstra 不再内嵌常量，唯一来源 constants）。

    注入到 path-graph-data 的 rules 字段，JS 端 doorPenalty / MID_TYPES 一律从
    PATH_GRAPH.rules 读取；任何规则数值改动只改 constants.py，前端自动跟随。
    """
    return {
        "doorPenalty": DOOR_PENALTY,
        "doorDefaultPenalty": DOOR_DEFAULT_PENALTY,
        "midTypesSameFloor": sorted(SAME_FLOOR_MID_TYPES),
        "midTypesCrossFloor": sorted(CROSS_FLOOR_MID_TYPES),
    }


def build_anno_script(min_x, max_y, svh_per_floor, sorted_floors):
    """生成「区域标注」交互脚本（独立 <script>，普通字符串，花括号为字面量）。

    通过 __CONSTS__ 占位符注入坐标变换常量，使浏览器端能把屏幕拖拽框
    （SVG 用户空间）反算回米制局部坐标，并写回房间类型。
    """
    floor_keys_js = json.dumps([str(k) for k in sorted_floors], ensure_ascii=False)
    consts = (
        "var GEOX = {ox:%r, oy:%r, scale:7.0, marginX:50, marginY:30, "
        "titleH:46, perFloor:%d, nFloors:%d, floorKeys:%s};"
        % (min_x, max_y, svh_per_floor, len(sorted_floors), floor_keys_js)
    )
    tpl = '''<script>
// ===== 区域标注：手动指定房间类型并写回 GeoJSON =====
__CONSTS__

var ANNO_MODE = false;
var annoDrawing = false;
var annoStartSvg = null;
var annoRectEl = null;
var ANNO_OVERRIDES = [];

var ANNO_ROOM_COLORS = {
  "classroom":"#FFF9C4","office":"#D7CCC8","meeting":"#F8BBD0","reception":"#FFE0B2",
  "medical":"#C8E6C9","storage":"#D7CCC8","equipment":"#CFD8DC","infrastructure":"#B0BEC5",
  "toilet":"#BBDEFB","staircase":"#FFCDD2","corridor":"#FAFAFA","lobby":"#FFF59D",
  "activity":"#F8BBD0","atrium":"#F3E5F5","elevator_lobby":"#FFCCBC","stair_lobby":"#FFE0B2",
  "room":"#FAFAFA","other":"#FAFAFA","entrance":"#BBDEFB","accessible_entrance":"#BBDEFB"
};

function roomLayerClass(t){
  if (t==="elevator_lobby") return "layer_lobby_elevator";
  if (t==="stair_lobby") return "layer_lobby_stair";
  if (t==="corridor"||t==="lobby"||t==="activity"||t==="atrium") return "layer_"+t;
  return "layer_room";
}
// SVG 用户空间 -> 米制局部坐标（精确复刻 Python tosvg 的反变换）
function svg2geo(sx, sy){
  var i = Math.min(GEOX.nFloors-1, Math.max(0, Math.floor(sy / GEOX.perFloor)));
  var fk = GEOX.floorKeys[i];
  var gx = (sx - GEOX.marginX) / GEOX.scale + GEOX.ox;
  var gy = GEOX.oy - (sy - i*GEOX.perFloor - GEOX.titleH - GEOX.marginY) / GEOX.scale;
  return {floor: fk, x: gx, y: gy};
}
// 屏幕坐标 -> SVG 用户空间（自动含缩放/平移的 CSS transform）
function clientToSvg(cx, cy){
  var pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy;
  var m = svg.getScreenCTM(); if(!m) return {x:cx, y:cy};
  var p = pt.matrixTransform(m.inverse());
  return {x: p.x, y: p.y};
}
function annoHint(m){ var h=document.getElementById('anno-hint'); if(h) h.textContent=m; }
function annoList(m){ var h=document.getElementById('anno-list'); if(h) h.textContent=m; }
function toggleAnnoMode(){
  ANNO_MODE = !ANNO_MODE; window.annoMode = ANNO_MODE;
  var btn = document.getElementById('btn-anno-toggle');
  btn.textContent = ANNO_MODE ? '退出标注模式' : '进入标注模式';
  btn.classList.toggle('active', ANNO_MODE);
  wrapper.style.cursor = ANNO_MODE ? 'crosshair' : '';
  annoHint(ANNO_MODE ? '标注模式：在图上拖拽框选区域，松手即把落在框内(质心)的房间标记为所选类型。' : '已退出标注模式。');
}
function startAnnoDraw(e){
  if(!ANNO_MODE) return;
  annoDrawing = true; annoStartSvg = clientToSvg(e.clientX, e.clientY);
  if(annoRectEl && annoRectEl.parentNode) annoRectEl.parentNode.removeChild(annoRectEl);
  annoRectEl = document.createElementNS('http://www.w3.org/2000/svg','rect');
  annoRectEl.setAttribute('fill','rgba(33,150,243,0.18)');
  annoRectEl.setAttribute('stroke','#1976D2'); annoRectEl.setAttribute('stroke-dasharray','4,3');
  annoRectEl.setAttribute('stroke-width','1');
  svg.appendChild(annoRectEl);
  e.preventDefault(); e.stopPropagation();
}
document.addEventListener('mousemove', function(e){
  if(!annoDrawing || !ANNO_MODE) return;
  var sp = clientToSvg(e.clientX, e.clientY);
  var x = Math.min(sp.x, annoStartSvg.x), y = Math.min(sp.y, annoStartSvg.y);
  annoRectEl.setAttribute('x', x); annoRectEl.setAttribute('y', y);
  annoRectEl.setAttribute('width', Math.abs(sp.x-annoStartSvg.x));
  annoRectEl.setAttribute('height', Math.abs(sp.y-annoStartSvg.y));
});
document.addEventListener('mouseup', function(e){
  if(!annoDrawing || !ANNO_MODE) return;
  annoDrawing = false;
  var sp = clientToSvg(e.clientX, e.clientY);
  var x0 = Math.min(sp.x, annoStartSvg.x), y0 = Math.min(sp.y, annoStartSvg.y);
  var x1 = Math.max(sp.x, annoStartSvg.x), y1 = Math.max(sp.y, annoStartSvg.y);
  if(annoRectEl && annoRectEl.parentNode) annoRectEl.parentNode.removeChild(annoRectEl);
  annoRectEl = null;
  if((x1-x0)<4 || (y1-y0)<4){ annoHint('框选过小，已取消。'); return; }
  applyAnnoRect(x0,y0,x1,y1);
});
// 把框选矩形（SVG 用户空间）映射到米制，标注重心落在框内的房间
function applyAnnoRect(x0,y0,x1,y1){
  var cs = [svg2geo(x0,y0), svg2geo(x1,y0), svg2geo(x0,y1), svg2geo(x1,y1)];
  var gxmin=1e9,gxmax=-1e9,gymin=1e9,gymax=-1e9;
  cs.forEach(function(c){ gxmin=Math.min(gxmin,c.x); gxmax=Math.max(gxmax,c.x); gymin=Math.min(gymin,c.y); gymax=Math.max(gymax,c.y); });
  var fk = cs[0].floor;
  var target = document.getElementById('anno-type').value;
  var gRooms = ((FULL_DATA.floors[fk]||{}).geometry||{}).rooms || [];
  var matched = [];
  gRooms.forEach(function(r){
    var props = r.properties||{};
    var c = props.centroid;
    if(!c && r.geometry && r.geometry.coordinates && r.geometry.coordinates[0]){
      var ring = r.geometry.coordinates[0]; var sx=0,sy=0;
      ring.forEach(function(p){ sx+=p[0]; sy+=p[1]; });
      c=[sx/ring.length, sy/ring.length];
    }
    if(!c) return;
    if(c[0]>=gxmin && c[0]<=gxmax && c[1]>=gymin && c[1]<=gymax) matched.push(r);
  });
  if(matched.length===0){ annoHint('框选区域内没有房间质心，未标注（可放大后重试）。'); return; }
  var changed = [];
  matched.forEach(function(r){
    var props = r.properties||{};
    var roomId = props.roomId;
    var oldType = props.roomType || props.type || 'room';
    props.roomType = target; if('type' in props) props.type = target;
    var rid = r.id;
    var sRooms = ((FULL_DATA.floors[fk]||{}).semantic||{}).rooms || [];
    sRooms.forEach(function(s){ if(s.geometryId===rid) s.type=target; });
    var el = document.querySelector('[data-roomid="'+ (roomId||'') +'"]');
    if(el){
      var poly = el.querySelector('polygon');
      var col = ANNO_ROOM_COLORS[target] || '#FAFAFA';
      if(poly){ poly.setAttribute('fill', target==='corridor'?'none':col); poly.setAttribute('stroke', target==='staircase'?'#E57373':'#999'); }
      el.setAttribute('class', roomLayerClass(target));
    }
    ANNO_OVERRIDES.push({floor: parseInt(fk,10), roomId: roomId, type: target});
    changed.push((props.label||roomId||rid) + ' (' + oldType + '->' + target + ')');
  });
  annoList('已标注 ' + changed.length + ' 间：' + changed.join('、'));
  annoHint('已应用。点「保存 GeoJSON」写回文件，或「导出标注」供重解析后复现。');
}
function exportAnnoOverrides(){
  if(ANNO_OVERRIDES.length===0){ alert('尚无标注项。'); return; }
  var blob = new Blob([JSON.stringify({overrides: ANNO_OVERRIDES}, null, 2)], {type:'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a'); a.href=url; a.download='room_overrides.json'; a.click();
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
  annoList('已导出 ' + ANNO_OVERRIDES.length + ' 条覆盖项 -> room_overrides.json');
}
</script>'''
    return tpl.replace('__CONSTS__', consts)


def build_deploy_script(min_x, max_y, svh_per_floor, sorted_floors, default_params):
    """生成「人工部署信标」交互脚本（独立 <script>，普通字符串，花括号为字面量）。

    与 build_anno_script 同构：通过 __CONSTS__ 占位符注入坐标变换常量
    （DEPLOY_GEOX，独立副本不依赖 anno 脚本的 GEOX）与人工部署默认参数
    （DEPLOY_DEFAULTS，新增信标沿用方案默认字段）。

    浏览器端能力（受浏览器沙箱约束，导出走 Blob + a.download）：
      1. 部署模式：点击地图空白处新增信标（BK-M-{层}-{序号:03d}），落点即进入拖拽；
      2. 任意模式下：按住信标拖拽移动，松手把新米制坐标写回 data-info（坐标行）；
      3. 导出：把内存中的测试路线模式信标（含新增/移动后坐标）组装为
         {beacons:[...], summary:{total, byFloor, bySemantic, byMount}} 下载为
         ble_deployment.json（用户保存到 result/ 目录）。

    人工部署只针对测试路线方案：进入部署模式自动切到「测试路线」方案（route），
    新增信标挂入 .mode-route 组；全局方案（beacon_floors）完全不受影响。
    """
    floor_keys_js = json.dumps([str(k) for k in sorted_floors], ensure_ascii=False)
    defaults_js = json.dumps(default_params, ensure_ascii=False)
    consts = (
        "var DEPLOY_GEOX = {ox:%r, oy:%r, scale:7.0, marginX:50, marginY:30, "
        "titleH:46, perFloor:%d, nFloors:%d, floorKeys:%s};\n"
        "var DEPLOY_DEFAULTS = %s;"
        % (min_x, max_y, svh_per_floor, len(sorted_floors), floor_keys_js, defaults_js)
    )
    tpl = '''<script>
// ===== 人工部署信标（测试路线）：拖拽移动 / 点选新增 / 导出 ble_deployment.json =====
// 独立 <script>，与「区域标注」同级。坐标反变换常量经下方占位符注入。
__CONSTS__

var DEPLOY_MODE = false;
var DEPLOY_COLOR = '#00695C';        // 人工部署信标（semanticTag=manual_deploy）颜色
var DEPLOY_BEACONS = [];             // 内存中的测试路线模式信标记录（含新增/移动后的 coordinates）
var DEPLOY_NEXT_SEQ = {};            // floor -> 下一可用序号（BK-M-{floor}-{seq:03d}）
var deployDrag = null;               // 当前拖拽中的信标 <g>
var deployDragMoved = false;         // 本次拖拽是否已超过点击阈值（4px）
var deployDragStartSvg = null;       // 拖拽起点（SVG 用户空间）
var deployDragBase = null;           // 拖拽起点的 circle cx/cy
var deployLastDragMoved = false;     // 上一次 mouseup 是否发生拖拽（抑制松手后的 click 详情）
var deployClickStart = null;         // 部署模式下点击空白处的候选 SVG 点
var deployClickMoved = false;        // 候选点击是否已移动超过阈值

function deployHint(m){ var h=document.getElementById('deploy-hint'); if(h) h.textContent=m; }
function deployList(m){ var h=document.getElementById('deploy-list'); if(h) h.textContent=m; }
function deployIdOf(g){
  try { var d = JSON.parse(g.getAttribute('data-info')); return d.id || ''; } catch(e){ return ''; }
}
// SVG 用户空间 -> 米制局部坐标（与 Python tosvg 反变换一致；独立实现不依赖 anno 脚本）
function deploySvg2geo(sx, sy){
  var i = Math.min(DEPLOY_GEOX.nFloors-1, Math.max(0, Math.floor(sy / DEPLOY_GEOX.perFloor)));
  var fk = DEPLOY_GEOX.floorKeys[i];
  var gx = (sx - DEPLOY_GEOX.marginX) / DEPLOY_GEOX.scale + DEPLOY_GEOX.ox;
  var gy = DEPLOY_GEOX.oy - (sy - i*DEPLOY_GEOX.perFloor - DEPLOY_GEOX.titleH - DEPLOY_GEOX.marginY) / DEPLOY_GEOX.scale;
  return {floor: fk, x: gx, y: gy};
}
// 屏幕坐标 -> SVG 用户空间（含缩放/平移 CSS transform）
function deployClientToSvg(cx, cy){
  var pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy;
  var m = svg.getScreenCTM(); if(!m) return {x:cx, y:cy};
  var p = pt.matrixTransform(m.inverse());
  return {x: p.x, y: p.y};
}
// 解析 data-info.b 信标记录；旧 HTML 无 b 时从 detail rows 兜底重建
function deployRecordFromDetail(info, g){
  if (!info || !info.detail) return null;
  var rows = {};
  (info.detail.rows || []).forEach(function(r){ if (r && r[0]) rows[String(r[0])] = r[1]; });
  var floor = g.getAttribute('data-floor') || '';
  var bid = info.id || ('BK-M-' + floor + '-001');
  var coordinates = [0, 0];
  var circle = g.querySelector('circle');
  if (circle) {
    var cx = parseFloat(circle.getAttribute('cx')), cy = parseFloat(circle.getAttribute('cy'));
    if (isFinite(cx) && isFinite(cy)) {
      var geo = deploySvg2geo(cx, cy);
      coordinates = [geo.x, geo.y];
    }
  }
  return {
    beaconId: bid,
    uuid: rows['UUID'] || DEPLOY_DEFAULTS.uuid,
    major: rows['Major'] != null ? Number(rows['Major']) : DEPLOY_DEFAULTS.major,
    minor: rows['Minor'] != null ? Number(rows['Minor']) : 1,
    coordinates: coordinates,
    floor: parseInt(floor, 10) || 1,
    locationDesc: rows['安装位置'] || ('人工部署 ' + floor + 'F'),
    mountType: rows['安装方式'] || 'wall',
    installHeight: rows['安装高度'] ? parseFloat(rows['安装高度']) : DEPLOY_DEFAULTS.installHeight,
    txPower: rows['发射功率'] ? parseFloat(rows['发射功率']) : DEPLOY_DEFAULTS.txPower,
    broadcastInterval: rows['广播间隔'] ? parseFloat(rows['广播间隔']) : DEPLOY_DEFAULTS.broadcastInterval,
    batteryModel: rows['电池型号'] || DEPLOY_DEFAULTS.batteryModel,
    expectedLifespan: rows['预期寿命'] ? parseFloat(rows['预期寿命']) : DEPLOY_DEFAULTS.expectedLifespan,
    semanticTag: rows['语义标签'] || 'manual_deploy',
    sourceNodeId: rows['来源节点'] || '',
    sourceNodeType: '',
    riskLevel: 1.0,
    snapDist_m: rows['吸附偏移'] ? parseFloat(rows['吸附偏移']) : 0.0,
    subType: rows['类型/方向'] ? String(rows['类型/方向']).split('/')[0] : ''
  };
}
// 收集测试路线模式信标（解析 data-info.b；同时缓存每层 BK-M-* 最大序号）
function deployCollectBeacons(){
  DEPLOY_BEACONS = [];
  DEPLOY_NEXT_SEQ = {};
  document.querySelectorAll('.layer_beacon[data-mode="route"]').forEach(function(g){
    var info = null;
    try { info = JSON.parse(g.getAttribute('data-info')); } catch(e) {}
    var rec = (info && info.b) ? info.b : deployRecordFromDetail(info, g);
    if (!rec) return;
    rec.coordinates = [Number(rec.coordinates[0]), Number(rec.coordinates[1])];
    DEPLOY_BEACONS.push({el: g, info: info, b: rec});
    var m = /^BK-M-(\\d+)-(\\d+)$/.exec(String(rec.beaconId || ''));
    if (m) {
      var seq = parseInt(m[2], 10);
      var fk2 = String(g.getAttribute('data-floor') != null ? g.getAttribute('data-floor') : rec.floor);
      if (!(fk2 in DEPLOY_NEXT_SEQ) || seq >= DEPLOY_NEXT_SEQ[fk2]) DEPLOY_NEXT_SEQ[fk2] = seq + 1;
    }
  });
  deployList('已加载 ' + DEPLOY_BEACONS.length + ' 个测试路线信标（可拖拽移动；部署模式下点击空白新增）');
}
// 找当前楼层的 mode-route 组（新增信标挂入，保持图层开关/模式切换语义）；缺失时补建
function deployFloorGroup(fk){
  var g = document.querySelector('.mode-route[data-floor="' + fk + '"]');
  if (g) return g;
  var NS = 'http://www.w3.org/2000/svg';
  g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'mode-route');
  g.setAttribute('data-floor', String(fk));
  svg.appendChild(g);
  return g;
}
// 构造信标 <g>（circle + text，与 Python render_scheme_group 视觉一致）
function deployCreateBeaconEl(rec, fk){
  var NS = 'http://www.w3.org/2000/svg';
  var g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'layer_beacon');
  g.setAttribute('data-floor', String(fk));
  g.setAttribute('data-mode', 'route');
  g.setAttribute('data-info', JSON.stringify(deployBuildInfo(rec)));
  var geo = rec.coordinates;
  var sx = DEPLOY_GEOX.marginX + (geo[0] - DEPLOY_GEOX.ox) * DEPLOY_GEOX.scale;
  var fi = DEPLOY_GEOX.floorKeys.indexOf(String(fk));
  var sy = fi * DEPLOY_GEOX.perFloor + DEPLOY_GEOX.titleH + DEPLOY_GEOX.marginY + (DEPLOY_GEOX.oy - geo[1]) * DEPLOY_GEOX.scale;
  var c = document.createElementNS(NS, 'circle');
  c.setAttribute('cx', sx.toFixed(1)); c.setAttribute('cy', sy.toFixed(1));
  c.setAttribute('r', 3.2);
  c.setAttribute('fill', DEPLOY_COLOR);
  c.setAttribute('fill-opacity', '0.9');
  c.setAttribute('stroke', '#ffffff'); c.setAttribute('stroke-width', '0.5');
  g.appendChild(c);
  var t = document.createElementNS(NS, 'text');
  t.setAttribute('x', (sx + 4).toFixed(1)); t.setAttribute('y', (sy + 1.5).toFixed(1));
  t.setAttribute('font-size', '4.5'); t.setAttribute('fill', DEPLOY_COLOR); t.setAttribute('opacity', '0.95');
  t.textContent = rec.beaconId;
  g.appendChild(t);
  return g;
}
// 构造信标 data-info（含完整记录 b，供导出/坐标写回；与 Python info_attr 语义一致）
function deployBuildInfo(rec){
  var bid = rec.beaconId;
  var rows = [
    ['信标 ID', bid],
    ['语义标签', rec.semanticTag || 'manual_deploy'],
    ['UUID', rec.uuid || ''],
    ['Major', rec.major != null ? rec.major : ''],
    ['Minor', rec.minor != null ? rec.minor : ''],
    ['楼层', (rec.floor != null ? rec.floor : '?') + 'F'],
    ['安装位置', rec.locationDesc || ''],
    ['安装方式', rec.mountType || ''],
    ['吸附偏移', (rec.snapDist_m != null ? rec.snapDist_m : 0) + ' m'],
    ['类型/方向', [rec.subType, rec.direction].filter(Boolean).join('/') || '—'],
    ['发射功率', (rec.txPower != null ? rec.txPower : '') + ' dBm'],
    ['广播间隔', (rec.broadcastInterval != null ? rec.broadcastInterval : '') + ' ms'],
    ['安装高度', (rec.installHeight != null ? rec.installHeight : '') + ' m'],
    ['电池型号', rec.batteryModel || ''],
    ['预期寿命', rec.expectedLifespan ? rec.expectedLifespan + ' 年' : ''],
    ['来源节点', rec.sourceNodeId || ''],
    ['坐标', '(' + rec.coordinates[0].toFixed(2) + ', ' + rec.coordinates[1].toFixed(2) + ')']
  ];
  return {
    tip: '信标 ' + bid + '\\\\n语义：' + (rec.semanticTag || 'manual_deploy') + ' · ' + rec.floor + 'F',
    detail: {title: '信标 ' + bid, rows: rows},
    kind: 'beacon',
    id: bid,
    b: rec
  };
}
// 拖拽结束时把新坐标写回 data-info 的「坐标」行（无则追加）
function deployUpdateInfo(entry, x, y){
  var info = entry.info;
  if (!info) return;
  if (!info.detail) info.detail = {title: info.id || '信标', rows: []};
  var rows = info.detail.rows;
  var idx = -1;
  for (var i = 0; i < rows.length; i++) {
    if (rows[i] && String(rows[i][0]).indexOf('坐标') !== -1) { idx = i; break; }
  }
  var val = '(' + x.toFixed(2) + ', ' + y.toFixed(2) + ')';
  if (idx >= 0) rows[idx][1] = val;
  else rows.push(['坐标', val]);
  entry.el.setAttribute('data-info', JSON.stringify(info));
}
// 当前层下一可用序号（BK-M-{floor}-{seq:03d}，seq 为该层最大编号+1）
function deployNextSeq(fk){
  if (DEPLOY_NEXT_SEQ[fk] == null) {
    var max = 0;
    DEPLOY_BEACONS.forEach(function(en){
      var m = /^BK-M-(\\d+)-(\\d+)$/.exec(String(en.b.beaconId || ''));
      if (m && parseInt(m[1], 10) === parseInt(fk, 10)) max = Math.max(max, parseInt(m[2], 10));
    });
    DEPLOY_NEXT_SEQ[fk] = max + 1;
  }
  return DEPLOY_NEXT_SEQ[fk]++;
}
// 部署模式：点击地图空白处新增信标（落点立即进入拖拽）
function deployAddBeacon(sp){
  var geo = deploySvg2geo(sp.x, sp.y);
  var fk = geo.floor;
  var seq = deployNextSeq(fk);
  var bid = 'BK-M-' + fk + '-' + ('000' + seq).slice(-3);
  var x = Math.round(geo.x * 100) / 100;
  var y = Math.round(geo.y * 100) / 100;
  var rec = {
    beaconId: bid,
    uuid: DEPLOY_DEFAULTS.uuid,
    major: DEPLOY_DEFAULTS.major,
    minor: parseInt(String(fk), 10) * 10000 + seq,
    coordinates: [x, y],
    floor: parseInt(fk, 10),
    locationDesc: '人工部署 ' + fk + 'F（' + x + ', ' + y + '）',
    mountType: 'wall',
    installHeight: DEPLOY_DEFAULTS.installHeight,
    txPower: DEPLOY_DEFAULTS.txPower,
    broadcastInterval: DEPLOY_DEFAULTS.broadcastInterval,
    batteryModel: DEPLOY_DEFAULTS.batteryModel,
    expectedLifespan: DEPLOY_DEFAULTS.expectedLifespan,
    semanticTag: 'manual_deploy',
    sourceNodeId: '',
    sourceNodeType: '',
    riskLevel: 1.0,
    snapDist_m: 0.0,
    subType: 'manual_deploy'
  };
  var g = deployCreateBeaconEl(rec, fk);
  g.classList.add('deploy-selected');   // 新增后立即处于「选中/可拖拽」状态
  var group = deployFloorGroup(fk);
  group.appendChild(g);
  var entry = {el: g, info: deployBuildInfo(rec), b: rec};
  DEPLOY_BEACONS.push(entry);
  deployHint('已新增信标 ' + bid + '（' + x + ', ' + y + '）· ' + fk + 'F，可立即拖拽调整位置');
  return entry;
}
function deployToggleMode(){
  DEPLOY_MODE = !DEPLOY_MODE;
  window.deployMode = DEPLOY_MODE;
  var btn = document.getElementById('btn-deploy-toggle');
  if (btn) {
    btn.textContent = DEPLOY_MODE ? '退出部署模式' : '进入部署模式';
    btn.classList.toggle('active', DEPLOY_MODE);
  }
  wrapper.style.cursor = DEPLOY_MODE ? 'crosshair' : '';
  if (DEPLOY_MODE) {
    // 人工部署只针对测试路线方案：进入部署模式自动切到「测试路线」，
    // 保证 .mode-route 组（默认隐藏）可见，新增/拖拽的信标可被看到。
    if (typeof window.setMode === 'function') {
      setMode('route');
    } else {
      document.querySelectorAll('.mode-route').forEach(function(el){ el.style.display = ''; });
      document.querySelectorAll('.mode-global').forEach(function(el){ el.style.display = 'none'; });
    }
    deployHint('部署模式（测试路线）：点击地图空白处新增测试路线信标（BK-M-{层}-{序号}），拖拽可移动；退出后仍可拖拽移动。');
  } else {
    deployHint('已退出部署模式（拖拽移动信标仍可用）。当前为测试路线方案，可点击右上角「全局」切回全局方案。');
  }
}
// ---- 拖拽移动（任意模式可用）：按下选中 -> 拖动更新 circle/text -> 松手写回米制坐标 ----
function deployStartDrag(e, g){
  var c = g.querySelector('circle');
  if (!c) return;
  deployDrag = g;
  deployDragMoved = false;
  deployDragStartSvg = deployClientToSvg(e.clientX, e.clientY);
  deployDragBase = { cx: parseFloat(c.getAttribute('cx')), cy: parseFloat(c.getAttribute('cy')) };
  g.classList.add('deploy-selected');
  e.preventDefault(); e.stopPropagation();
}
function deployMoveDrag(e){
  if (!deployDrag) return;
  var sp = deployClientToSvg(e.clientX, e.clientY);
  var dx = sp.x - deployDragStartSvg.x;
  var dy = sp.y - deployDragStartSvg.y;
  if (!deployDragMoved && Math.hypot(dx, dy) < 4) return;  // 未超阈值：视为点击（选中）
  deployDragMoved = true;
  var nx = deployDragBase.cx + dx;
  var ny = deployDragBase.cy + dy;
  var c = deployDrag.querySelector('circle');
  var t = deployDrag.querySelector('text');
  if (c) { c.setAttribute('cx', nx.toFixed(1)); c.setAttribute('cy', ny.toFixed(1)); }
  if (t) { t.setAttribute('x', (nx + 4).toFixed(1)); t.setAttribute('y', (ny + 1.5).toFixed(1)); }
}
function deployEndDrag(e){
  if (!deployDrag) return;
  var g = deployDrag;
  deployDrag = null;
  if (deployDragMoved) {
    deployLastDragMoved = true;   // 抑制松手后的 click 选中详情（点击与拖拽区分）
    var c = g.querySelector('circle');
    var nx = parseFloat(c.getAttribute('cx')), ny = parseFloat(c.getAttribute('cy'));
    var geo = deploySvg2geo(nx, ny);
    var x = Math.round(geo.x * 100) / 100;
    var y = Math.round(geo.y * 100) / 100;
    var fk = g.getAttribute('data-floor');
    var bid = deployIdOf(g);
    var entry = null;
    for (var i = 0; i < DEPLOY_BEACONS.length; i++) {
      if (DEPLOY_BEACONS[i].el === g) { entry = DEPLOY_BEACONS[i]; break; }
    }
    if (entry) {
      entry.b.coordinates = [x, y];
      deployUpdateInfo(entry, x, y);
    } else {
      // 全局模式信标：仅更新 DOM/data-info 坐标行（人工部署只针对测试路线，不纳入导出）
      var info = null;
      try { info = JSON.parse(g.getAttribute('data-info')); } catch(err) {}
      if (info) {
        if (info.b) info.b.coordinates = [x, y];
        deployUpdateInfo({el: g, info: info}, x, y);
      }
    }
    deployHint('已移动信标 ' + bid + ' → (' + x + ', ' + y + ') · ' + fk + 'F');
  } else {
    deployHint('已选中信标，可按住拖拽移动位置；部署模式下点击空白处新增。');
  }
  deployDragStartSvg = null;
  deployDragBase = null;
}
// ---- 导出：组装与部署方案同构 JSON（beacons + summary），Blob + a.download 下载 ----
// 关键边界：浏览器沙箱无法直接写磁盘，用户把下载的 ble_deployment.json 保存到 result/ 目录。
// 导出语义为「测试路线人工部署方案」：DEPLOY_BEACONS 收集的是 route 模式信标。
function deployExport(){
  var beacons = [];
  DEPLOY_BEACONS.forEach(function(entry){
    beacons.push(JSON.parse(JSON.stringify(entry.b)));  // 深拷贝快照
  });
  if (!beacons.length) {
    alert('当前测试路线方案没有信标可导出。请先加载 ble_deployment.json，或进入部署模式点击地图新增测试路线信标。');
    return;
  }
  var byFloor = {}, bySemantic = {}, byMount = {};
  beacons.forEach(function(b){
    var fk = String(b.floor);
    byFloor[fk] = (byFloor[fk] || 0) + 1;
    var sem = b.semanticTag || 'unknown';
    bySemantic[sem] = (bySemantic[sem] || 0) + 1;
    var mt = b.mountType || 'unknown';
    byMount[mt] = (byMount[mt] || 0) + 1;
  });
  var payload = {
    schemaVersion: '1.0',
    generatedBy: 'pathai-manual-deploy',
    generatedAt: new Date().toISOString(),
    beacons: beacons,
    summary: { total: beacons.length, byFloor: byFloor, bySemantic: bySemantic, byMount: byMount }
  };
  var blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = 'ble_deployment.json';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
  deployList('已导出 ' + beacons.length + ' 个测试路线信标 -> ble_deployment.json');
  deployHint('已导出 ble_deployment.json（测试路线人工部署方案），请保存到 result/ 目录（浏览器沙箱无法直接写磁盘）。');
}
// ---- 事件接线（capture 优先于主脚本 app.js 的 bubble 处理）----
svg.addEventListener('mousedown', function(e){
  deployLastDragMoved = false;
  document.querySelectorAll('.layer_beacon.deploy-selected').forEach(function(x){ x.classList.remove('deploy-selected'); });
  var t = e.target && e.target.closest ? e.target.closest('.layer_beacon') : null;
  if (t) { deployStartDrag(e, t); return; }
  if (DEPLOY_MODE) {
    // 部署模式：点击空白处新增信标（按下即记录候选点，并禁用画布平移）
    deployClickStart = deployClientToSvg(e.clientX, e.clientY);
    deployClickMoved = false;
    e.preventDefault(); e.stopPropagation();
    return;
  }
}, true);
document.addEventListener('mousemove', function(e){
  deployMoveDrag(e);
  if (DEPLOY_MODE && deployClickStart && !deployDrag) {
    var sp = deployClientToSvg(e.clientX, e.clientY);
    if (Math.hypot(sp.x - deployClickStart.x, sp.y - deployClickStart.y) > 4) deployClickMoved = true;
  }
});
document.addEventListener('mouseup', function(e){
  deployEndDrag(e);
  if (DEPLOY_MODE && deployClickStart && !deployDragMoved && !deployClickMoved) {
    // 部署模式：点击任意位置（房间/墙体/走廊/空白）都新增信标。
    // 点击信标本身不会走到这里：mousedown capture 已把信标拦截进拖拽分支
    // （deployStartDrag 设置 deployDrag，且不设置 deployClickStart），
    // 因此 mouseup 不会误新增。
    var sp = deployClientToSvg(e.clientX, e.clientY);
    deployAddBeacon(sp);
  }
  deployClickStart = null;
  deployClickMoved = false;
});
svg.addEventListener('click', function(e){
  if (DEPLOY_MODE) {
    // 部署模式下点击=新增信标（或按住信标=拖拽），不触发任何要素详情
    e.stopPropagation(); e.preventDefault();
    return;
  }
  if (deployLastDragMoved) {
    deployLastDragMoved = false;
    e.stopPropagation(); e.preventDefault();   // 拖拽松手后的 click 不再触发详情
    return;
  }
}, true);

deployCollectBeacons();
</script>'''
    return tpl.replace('__CONSTS__', consts)


def merge_deploy_defaults(defaults, src):
    """从信标方案 JSON（顶层 uuid + defaultParams）合并人工部署默认值。

    src 可为 None；已有值仅在 src 提供对应字段时覆盖（不删除 defaults 既有键）。
    """
    if not src:
        return
    if src.get("uuid"):
        defaults["uuid"] = src["uuid"]
    dp = src.get("defaultParams") or {}
    if dp.get("installHeightWall"):
        defaults["installHeight"] = dp["installHeightWall"]
    if dp.get("broadcastInterval"):
        defaults["broadcastInterval"] = dp["broadcastInterval"]
    if dp.get("batteryModel"):
        defaults["batteryModel"] = dp["batteryModel"]
    if dp.get("expectedLifespanYears"):
        defaults["expectedLifespan"] = dp["expectedLifespanYears"]
    tps = dp.get("txPowerBySemantic") or {}
    if tps.get("intersection"):
        defaults["txPower"] = tps["intersection"]


# ---- 三点定位覆盖（Trilateration coverage）无线模型 ----
# 与 src/tools/analyze_trilateration_coverage.py 同口径：射线穿墙 RSSI 衰减判定可见信标数，
# 每个指纹点需 >=3 个可见信标方可三点定位。覆盖随 --beacons 信标方案动态变化。
try:
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    _HAS_SHAPELY = True
except Exception:
    _HAS_SHAPELY = False

COV_TX_POWER = -10       # dBm，三角定位要求功率一致
COV_RSSI_REF_1M = -50    # dBm，TxPower=-10 时 1m 参考(RSSI_ref = TX - FSPL@1m, FSPL≈40dB)
COV_N = 3.5              # 室内路径损耗指数
COV_WALL_ATTEN = {"brick": 12, "concrete": 15, "partition": 8, "glass": 6, None: 12}  # 每面墙衰减 dB
COV_VISIBLE = -85        # dBm 稳定可检测阈值
COV_OFFSET = 0.25         # m，信标向采样点偏移(天线在墙内侧)
COV_D_MAX = 11.0          # m，超出即使 0 墙也不可见
# 可见信标数 -> 颜色（>=3 绿可定位；越少越红）
COV_COLORS = {0: "#E53935", 1: "#FB8C00", 2: "#FBC02D", 3: "#43A047"}


def build_coverage_index(geo_json):
    """为每层构建墙体线段 STRtree，返回 {floor: (segs, atten, tree)}。无 shapely 时返回 {}。"""
    if not _HAS_SHAPELY:
        return {}
    idx = {}
    for fk in geo_json["floors"]:
        fg = geo_json["floors"][fk]["geometry"]
        segs, atten = [], []
        for w in fg.get("walls", []):
            coords = w.get("geometry", {}).get("coordinates", [])
            if len(coords) < 2:
                continue
            segs.append(LineString([(x, y) for x, y in coords]))
            atten.append(COV_WALL_ATTEN.get(w.get("properties", {}).get("material"), 12))
        if segs:
            idx[str(fk)] = (segs, atten, STRtree(segs))
    return idx


def visible_beacon_count(px, py, beacons, cidx):
    """点(px,py)对同层 beacons[(x,y)...] 的可见信标数（射线穿墙 RSSI 模型）。
       cidx = (segs, atten, tree) 或 None。"""
    if cidx is None:
        return 0
    segs, atten, tree = cidx
    v = 0
    for (bx, by) in beacons:
        dx, dy = bx - px, by - py
        d = math.hypot(dx, dy)
        if d > COV_D_MAX or d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        ox, oy = bx - ux * COV_OFFSET, by - uy * COV_OFFSET
        seg = LineString([(px, py), (ox, oy)])
        loss = 0.0
        for j in tree.query(seg):
            if seg.intersects(segs[j]):
                loss += atten[j]
        rssi = COV_RSSI_REF_1M - 10 * COV_N * math.log10(d) - loss
        if rssi > COV_VISIBLE:
            v += 1
    return v


def visible_ids(px, py, beacons, cidx):
    """点(px,py)对同层 beacons[(bid,(x,y))...] 的可见信标 ID 列表（穿墙模型，与 coverage 同口径）。
       cidx = (segs, atten, tree) 或 None。"""
    if cidx is None:
        return []
    segs, atten, tree = cidx
    out = []
    for (bid, (bx, by)) in beacons:
        dx, dy = bx - px, by - py
        d = math.hypot(dx, dy)
        if d > COV_D_MAX or d < 1e-6:
            continue
        ux, uy = dx / d, dy / d
        ox, oy = bx - ux * COV_OFFSET, by - uy * COV_OFFSET
        seg = LineString([(px, py), (ox, oy)])
        loss = 0.0
        for j in tree.query(seg):
            if seg.intersects(segs[j]):
                loss += atten[j]
        rssi = COV_RSSI_REF_1M - 10 * COV_N * math.log10(d) - loss
        if rssi > COV_VISIBLE:
            out.append(bid)
    return out


def main():
    import sys as _sys
    # Windows GBK 控制台无法编码 ²/→/✓ 等非 GBK 字符，导致 print 抛 UnicodeEncodeError
    # 而中断渲染。强制 stdout/stderr 为 utf-8，使脚本在任意终端均可完整输出。
    try:
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse as _ap
    _a = _ap.ArgumentParser(description="交互式楼层渲染")
    _a.add_argument("--geo", default=GEO_IN, help="v9 楼层 GeoJSON 路径")
    _a.add_argument("--beacons", default=None, help="全局信标部署方案 JSON（覆盖默认 beacon_deployment_plan.json）")
    _a.add_argument("--fingerprint", default=None, help="全局指纹采集网格 JSON（覆盖默认 fingerprint_grid.json）")
    _a.add_argument("--beacons-routes", default=None, help="测试路线信标部署方案 JSON（缺省自动探测 *_routes.json）")
    _a.add_argument("--fingerprint-routes", default=None, help="测试路线指纹采集网格 JSON（缺省自动探测 fingerprint_grid_routes.json）")
    _a.add_argument("--out", default=HTML_OUT, help="输出 HTML 路径")
    _a.add_argument("--no-ble-deploy", action="store_true",
                    help="跳过 result/ble_deployment.json 优先加载（QA 回归旧逻辑）")
    _args = _a.parse_args()
    geo = json.load(open(_args.geo, encoding="utf-8"))
    node_lookup = build_node_lookup(geo)
    geo_dir = Path(_args.geo).parent

    # 指纹采集网格（两套：全局 + 测试路线，模式开关切换显示）
    fp_floors = {}
    fp_path = Path(_args.fingerprint) if _args.fingerprint else (geo_dir / "fingerprint_grid.json")
    if fp_path.exists():
        try:
            fp_floors = json.load(open(fp_path, encoding="utf-8")).get("floors", {})
            print(f"  [info] 全局指纹网格 {fp_path.name}: "
                  f"{sum(len(v.get('points', [])) for v in fp_floors.values())} 点")
        except Exception as e:
            print("  [warn] 读取指纹网格失败：", e)
    else:
        print("  [hint] 未找到 fingerprint_grid.json，可先运行 generate_fingerprint_grid.py")
    fp_floors_routes = {}
    fp_r_path = Path(_args.fingerprint_routes) if _args.fingerprint_routes else (geo_dir / "fingerprint_grid_routes.json")
    if fp_r_path.exists():
        try:
            fp_floors_routes = json.load(open(fp_r_path, encoding="utf-8")).get("floors", {})
            print(f"  [info] 路线指纹网格 {fp_r_path.name}: "
                  f"{sum(len(v.get('points', [])) for v in fp_floors_routes.values())} 点")
        except Exception as e:
            print("  [warn] 读取路线指纹网格失败：", e)

    # 信标部署方案（两套：全局 + 测试路线）
    beacon_floors = {}
    bc_data = None
    bc_path = Path(_args.beacons) if _args.beacons else (geo_dir / "beacon_deployment_plan.json")
    if bc_path.exists():
        try:
            bc_data = json.load(open(bc_path, encoding="utf-8"))
            for b in bc_data.get("beacons", []):
                beacon_floors.setdefault(str(b.get("floor")), []).append(b)
            print(f"  [info] 全局信标方案 {bc_path.name}: "
                  f"{len(bc_data.get('beacons', []))} 个信标")
        except Exception as e:
            print("  [warn] 读取全局信标方案失败：", e)
    else:
        print("  [hint] 未找到 beacon_deployment_plan.json，可先运行 gen_beacon_plan.py")
    beacon_floors_routes = {}
    bc_r_data = None
    bc_r_path = Path(_args.beacons_routes) if _args.beacons_routes else (geo_dir / "beacon_deployment_plan_trilateration_routes.json")
    if bc_r_path.exists():
        try:
            bc_r_data = json.load(open(bc_r_path, encoding="utf-8"))
            for b in bc_r_data.get("beacons", []):
                beacon_floors_routes.setdefault(str(b.get("floor")), []).append(b)
            print(f"  [info] 路线信标方案 {bc_r_path.name}: "
                  f"{len(bc_r_data.get('beacons', []))} 个信标")
        except Exception as e:
            print("  [warn] 读取路线信标方案失败：", e)

    # 人工部署方案优先加载：result/ble_deployment.json 存在且非空 beacons 列表时，
    # 作为「测试路线模式」（beacon_floors_routes）的唯一信标来源（全局模式保持独立）。
    # --no-ble-deploy 可跳过检测（QA 回归旧逻辑）。
    _ble_data = None
    if not _args.no_ble_deploy:
        _ble_path = geo_dir / "ble_deployment.json"
        if _ble_path.exists():
            try:
                _ble_data = json.load(open(_ble_path, encoding="utf-8"))
                _ble_beacons = _ble_data.get("beacons") or []
                if _ble_beacons:
                    beacon_floors_routes = {}
                    for _b in _ble_beacons:
                        beacon_floors_routes.setdefault(str(_b.get("floor")), []).append(_b)
                    print(f"  [info] 加载人工部署方案 ble_deployment.json（测试路线）: "
                          f"{len(_ble_beacons)} 个信标")
                else:
                    print("  [warn] ble_deployment.json 存在但 beacons 为空，忽略并沿用旧逻辑")
            except Exception as e:
                print("  [warn] 读取 ble_deployment.json 失败：", e)

    # 人工部署信标默认参数：新增信标沿用方案默认（顶层 uuid + defaultParams），
    # 优先级 ble_deployment > 路线方案 > 内置兜底。
    deploy_defaults = {
        "uuid": "B9407F30-F5F8-466E-AFF9-25556B57FE6D",
        "major": 1,
        "installHeight": 2.2,
        "txPower": -10,
        "broadcastInterval": 300,
        "batteryModel": "CR2477",
        "expectedLifespan": 5,
    }
    merge_deploy_defaults(deploy_defaults, bc_r_data)
    merge_deploy_defaults(deploy_defaults, _ble_data)

    # 柱开放度分析（open_column_wraps.json，由 detect_open_column_wraps.py 生成）：
    # 开放柱 openColumns / 被拒柱 rejectedColumns 均按柱 id 并入 col_open 字典
    # （open 标记 _status="open"，rejected 标记 _status="rejected"），
    # 供柱详情面板展示 openness / nOpen / closedRatio 与判定；缺省自动探测，不强加参数。
    col_open = {}
    oc_path = geo_dir / "open_column_wraps.json"
    if oc_path.exists():
        try:
            _oc_data = json.load(open(oc_path, encoding="utf-8"))
            for _, _oc_fd in _oc_data.get("floors", {}).items():
                for _oc in _oc_fd.get("openColumns", []):
                    _entry = dict(_oc)
                    _entry["_status"] = "open"
                    col_open[_oc["id"]] = _entry
                for _rc in _oc_fd.get("rejectedColumns", []):
                    _entry = dict(_rc)
                    _entry["_status"] = "rejected"
                    col_open[_rc["id"]] = _entry
            print(f"  [info] 柱开放度 {oc_path.name}: {len(col_open)} 个柱")
        except Exception as e:
            print("  [warn] 读取柱开放度分析失败：", e)
    else:
        print("  [hint] 未找到 open_column_wraps.json，可先运行 detect_open_column_wraps.py")

    # ---- 全局范围（所有楼层共用变换，便于跨层对齐） ----
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")
    for fk in geo["floors"]:
        for room in geo["floors"][fk]["geometry"].get("rooms", []):
            for p in room["geometry"]["coordinates"][0]:
                min_x, min_y = min(min_x, p[0]), min(min_y, p[1])
                max_x, max_y = max(max_x, p[0]), max(max_y, p[1])

    svw = int((max_x - min_x) * SCALE + MARGIN_X * 2) + 14  # +14px 容纳 x=0 轴与原点(0,0)
    svh_per_floor = int((max_y - min_y) * SCALE + MARGIN_Y * 2 + FLOOR_TITLE_H)
    svh = svh_per_floor * len(geo["floors"]) + 20
    ox, oy = min_x, max_y

    sorted_floors = sorted(geo["floors"].keys(), key=lambda x: int(x))
    cf = geo.get("crossFloorEdges", [])
    n_cf_stair = sum(1 for e in cf if e.get("type") == "staircase")
    n_cf_elev = sum(1 for e in cf if e.get("type") == "elevator")
    # 已建立跨层连接的井道编号（用于在楼梯/电梯上标注"是否跨层连通"）
    cf_codes = {e.get("code") for e in cf if e.get("code")}

    parts = []
    # 地图要素中心表：要素ID -> [svg像素中心x, y]，供详情面板点击ID后居中定位
    map_centers = {}
    # B1：静态 HTML/CSS/JS 抽到 templates/ + static/（独立文件可 lint/格式化），
    # 本文件仅保留动态 SVG 楼层与数据脚本，main() 尾部统一组装。
    _tpl_html = (Path(__file__).resolve().parent / "templates" / "interactive.html"
                 ).read_text(encoding="utf-8")
    _app_js = (Path(__file__).resolve().parent / "static" / "app.js"
               ).read_text(encoding="utf-8").rstrip("\n")
    _header_values = {
        "__SVW__": str(svw),
        "__SVH__": str(svh),
        "__SCALE__": f"{SCALE:.0f}",
        "__MIN_X__": f"{min_x:.1f}",
        "__MAX_X__": f"{max_x:.1f}",
        "__MIN_Y__": f"{min_y:.1f}",
        "__MAX_Y__": f"{max_y:.1f}",
        "__WIDTH_M__": f"{max_x - min_x:.0f}",
        "__HEIGHT_M__": f"{max_y - min_y:.0f}",
        "__N_CF_STAIR__": str(n_cf_stair),
        "__N_CF_ELEV__": str(n_cf_elev),
        "__N_CF__": str(len(cf)),
    }

    # ---------------- 逐层生成 SVG ----------------
    cov_index = build_coverage_index(geo)
    beacon_contrib = collections.defaultdict(list)  # beaconId -> 关键贡献点[[x,y],...]
    for i, fk in enumerate(sorted_floors):
        floor = int(fk)
        fd = geo["floors"][fk]
        geom = fd["geometry"]
        topo = fd.get("topology", {})
        acc = fd.get("accessibility", {})
        fbase_y = i * svh_per_floor

        def tosvg(cx, cy):
            sx = MARGIN_X + (cx - ox) * SCALE
            sy = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - cy) * SCALE
            return fmt(sx), fmt(sy)

        title_cn = "首层" if floor == 1 else f"{floor}层"
        n_wall = len(geom.get("walls", []))
        n_room = len(geom.get("rooms", []))
        n_door = len(geom.get("doors", []))
        _dt = collections.Counter(
            d["properties"].get("doorType", "swing") for d in geom.get("doors", []))
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
            f'<text x="20" y="{fbase_y + 26}" font-size="15" font-weight="bold" fill="#333">'
            f'{title_cn} {floor}F（v9 米制）</text>\n'
        )
        stats = (f'墙:{n_wall} 窗:{n_win} 房间:{n_room} '
                 f'门:{n_door}(普通门:{_dt.get("swing", 0)} 门洞:{_dt.get("opening", 0)} '
                 f'防火门:{_dt.get("fire", 0)}) '
                 f'楼梯:{n_stair} 电梯:{n_elev} 柱:{n_col} '
                 f'拓扑节点:{n_node} 边:{n_edge}')
        parts.append(
            f'<text x="200" y="{fbase_y + 26}" font-size="9" fill="#999">{stats}</text>\n'
        )

        # 1. 可通行区域（Walkable Polygon，T1：公共空间扣除柱子/井道/墙体障碍物）
        # 画在最底层：房间/前室颜色在其上，避免绿色覆盖电梯/楼梯前室等类型色
        n_walk = 0
        for r in geom.get("rooms", []):
            wp = r["properties"].get("walkablePolygon")
            if not wp:
                continue
            n_walk += 1
            _rid_w = r.get("id", "")
            _rlab_w = r["properties"].get("label", "")
            _rtype_w = r["properties"].get("roomType") or r["properties"].get("type", "")
            _wtitle = f"{_rid_w}（{_rlab_w or _rtype_w or '可通行区'}）"
            _wtip = f"可通行区域\\n所属：{_wtitle}"
            _wdet = {"title": "可通行区域", "rows": [
                ("所属房间", link_obj(_rid_w, _wtitle)),
                ("区域类型", _rtype_w or "—"),
                ("楼层", f"{fk}F"),
            ]}
            _wattr = info_attr({"tip": _wtip, "detail": _wdet, "kind": "walkable"})
            for rings in wp["coordinates"]:
                for ri, ring in enumerate(rings):
                    pts = " ".join(f"{tosvg(x, y)[0]},{tosvg(x, y)[1]}"
                                   for x, y in ring)
                    # 外环浅绿填充；内环（柱洞）仅描边不填充
                    fill = "none" if ri > 0 else "#A5D6A7"
                    parts.append(
                        f'<g class="layer_walkable" {_wattr}><polygon points="{pts}" '
                        f'fill="{fill}" stroke="#43A047" stroke-width="0.4"/></g>\n'
                    )
        if n_walk:
            print(f"  [F{fk}] 可通行区域图层: {n_walk} 个")

        # 1b. 走廊中轴骨架（T3–T5，来自 floors.N.skeleton）
        skel_fc = fd.get("skeleton") or {}
        skel_feats = skel_fc.get("features") or []
        n_skel = 0
        for feat in skel_feats:
            geom_s = feat.get("geometry") or {}
            if geom_s.get("type") != "LineString":
                continue
            coords = geom_s.get("coordinates") or []
            if len(coords) < 2:
                continue
            pts = " ".join(f"{tosvg(x, y)[0]},{tosvg(x, y)[1]}" for x, y in coords)
            _fid = feat.get("id", "")
            _flen = (feat.get("properties") or {}).get("length_m", "")
            tip = f"骨架段 {_fid}\\n长度：{_flen} m"
            det = {"title": f"骨架段 {_fid}", "rows": [
                ("类型", "走廊中轴骨架"),
                ("长度", f"{_flen} m"),
                ("楼层", f"{fk}F"),
            ]}
            attr = info_attr({"tip": tip, "detail": det, "kind": "skeleton", "id": _fid})
            parts.append(
                f'<g class="layer_skeleton" {attr}>'
                f'<polyline class="vis" points="{pts}"/>'
                f'<polyline class="hit" points="{pts}"/></g>\n'
            )
            n_skel += 1
        # 骨架交叉口（TI 中 type=intersection 且来自骨架）
        n_junc = 0
        for n in (topo.get("nodes") or []):
            if n.get("type") != "intersection":
                continue
            # 仅当有骨架时绘制红色交叉口点，避免与旧质心 TI 混淆过多
            if not skel_feats:
                break
            cx, cy = n.get("coordinates") or [0, 0]
            sx, sy = tosvg(cx, cy)
            _nid = n.get("id", "")
            _rt = n.get("roomType", "")
            _rt_cn = {"corridor": "走道/走廊", "lobby": "门厅/大厅",
                      "activity": "活动空间", "atrium": "中庭"}.get(_rt, _rt or "开放空间")
            _rows = [("节点ID", link_obj(_nid)), ("类型", "交叉口（骨架）"), ("空间类型", _rt_cn)]
            _rl = n.get("riskLevel")
            if isinstance(_rl, (int, float)):
                _rows.append(("风险等级", f"{_rl:g}"))
            if n.get("label"):
                _rows.append(("标签", n["label"]))
            det = {"title": n.get("label") or "骨架交叉口", "rows": _rows}
            tip = f"骨架交叉口 {_nid}\\n类型：{_rt_cn}"
            attr = info_attr({"tip": tip, "detail": det, "id": _nid, "kind": "node"})
            parts.append(
                f'<g class="layer_skeleton_node" {attr}>'
                f'<circle class="vis" cx="{sx}" cy="{sy}" r="2.2"/>'
                f'<circle class="hit" cx="{sx}" cy="{sy}" r="6"/></g>\n'
            )
            n_junc += 1
        if n_skel:
            print(f"  [F{fk}] 走廊骨架: {n_skel} 段, 交叉口标记 {n_junc} 个")

        # ---- 拓扑节点对照表：用于详情面板关联「选中元素 → 拓扑节点」----
        _tnodes = topo.get("nodes", [])
        _roomid_to_trid = {n["roomId"]: n["id"] for n in _tnodes
                           if n.get("type") == "room" and n.get("roomId")}
        _td_nodes = [n for n in _tnodes if n.get("type") == "doorway"]
        _td_by_key = {}
        for n in _td_nodes:
            _td_by_key.setdefault(
                (n.get("doorType"), frozenset(n.get("rooms") or [])), []).append(n)

        # ---- 反向对照表：拓扑节点 → 对应地图要素ID（用于详情展示，需求①）----
        _geom_rooms = geom.get("rooms", [])
        _room_by_id = {r["id"]: r for r in _geom_rooms}
        # 门要素按 (doorType, 房间集合) 建键 → 门要素ID列表
        _door_by_key = {}
        _door_by_id = {}
        for dr in geom.get("doors", []):
            p = dr.get("properties", {})
            _door_by_key.setdefault(
                (p.get("doorType"), frozenset(p.get("rooms") or [])), []).append(dr["id"])
            _door_by_id[dr["id"]] = dr
        # 设施（楼梯/电梯）按 编号code/标签 建键 → 要素ID
        _fac_by_code = {}
        for st in geom.get("stairs", []):
            _fac_by_code[st["properties"].get("code")] = st["id"]
            _fac_by_code[st["properties"].get("label")] = st["id"]
        for ev in geom.get("elevators", []):
            _fac_by_code[ev["properties"].get("code")] = ev["id"]
            _fac_by_code[ev["properties"].get("label")] = ev["id"]

        def _nearest_door_for_td(nd):
            """给拓扑门节点(TD)匹配几何门要素ID：优先 (门型,房间) 完全一致，
            其次 房间重叠 + 门型一致，最后 坐标最近兜底。"""
            dt = nd.get("doorType")
            rooms = frozenset(nd.get("rooms") or [])
            cands = _door_by_key.get((dt, rooms))
            if not cands:
                cands = [d["id"] for d in _door_by_id.values()
                         if d["properties"].get("doorType") == dt
                         and (set(d["properties"].get("rooms") or []) & rooms)]
            if not cands:
                cands = list(_door_by_id.keys())
            if not cands:
                return None
            nc = nd["coordinates"]
            def _d(did):
                c = _door_by_id[did]["geometry"]["coordinates"]
                return ((c[0] - nc[0]) ** 2 + (c[1] - nc[1]) ** 2) ** 0.5
            return min(cands, key=_d)

        # ---- 地图要素中心表（需求②：点击ID居中定位）----
        for r in _geom_rooms:
            _rid = r.get("id")
            if not _rid or _rid in map_centers:
                continue
            cx, cy = _centroid_ring(r["geometry"]["coordinates"][0])
            sx, sy = tosvg(cx, cy)
            map_centers[_rid] = [float(sx), float(sy)]
        for dr in geom.get("doors", []):
            _did = dr.get("id")
            if not _did or _did in map_centers:
                continue
            c = dr["geometry"]["coordinates"]
            sx, sy = tosvg(c[0], c[1])
            map_centers[_did] = [float(sx), float(sy)]
        for st in geom.get("stairs", []):
            _sid = st.get("id")
            if not _sid:
                continue
            cent = st["properties"].get("centroid")
            sx, sy = (tosvg(cent[0], cent[1]) if cent
                      else tosvg(*_centroid_ring(st["geometry"]["coordinates"][0])))
            map_centers[_sid] = [float(sx), float(sy)]
        for ev in geom.get("elevators", []):
            _eid = ev.get("id")
            if not _eid:
                continue
            cent = ev["properties"].get("centroid")
            sx, sy = (tosvg(cent[0], cent[1]) if cent
                      else tosvg(*_centroid_ring(ev["geometry"]["coordinates"][0])))
            map_centers[_eid] = [float(sx), float(sy)]

        def _nearest_topo(point, allow=("facility", "facility_entrance",
                                        "intersection", "room", "doorway"),
                          max_dist=15.0):
            """几何元素（楼梯/电梯等无直接 id 关联）→ 最近拓扑节点。超过阈值不关联。"""
            best, bd = None, 1e18
            for n in _tnodes:
                if n.get("type") not in allow:
                    continue
                dx = n["coordinates"][0] - point[0]
                dy = n["coordinates"][1] - point[1]
                d = (dx * dx + dy * dy) ** 0.5
                if d > max_dist:
                    continue
                if d < bd:
                    bd, best = d, n["id"]
            return best

        # 2. 房间
        n_skip_bbox = 0
        for r in geom.get("rooms", []):
            ring = r["geometry"]["coordinates"][0]
            if _is_label_bbox(ring):
                # 文字标签的包围盒：跳过绘制，避免与真实房间重叠
                n_skip_bbox += 1
                continue
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            p = r["properties"]
            rtype = p.get("roomType", "room")
            color = ROOM_COLORS.get(rtype, "#FAFAFA")
            label = p.get("label", "")
            _rid = r.get("id")  # 几何房间唯一标识，用作房间编号与拓扑节点匹配（properties.roomId 在部分房间有误）
            tip = f"房间：{label or '—'}\\n类型：{rtype}\\n编号：{_rid or '—'}"
            det = {"title": label or "房间", "rows": [
                ("房间编号", link_obj(_rid) if _rid else "—"),
                ("类型", rtype),
                ("楼层", p.get("floor", floor)),
                ("公共空间", "是" if p.get("public") else "否"),
                ("无障碍可达", "是" if p.get("accessible") else "否"),
                ("独立出入口", "是" if p.get("hasIndependentEntrance") else "否"),
            ]}
            # 归属本房间的门（全类型：swing/fire/opening）——按 rooms 字段匹配，
            # 每个门一行「编号 + 类型」可点击居中定位。数量多时折叠为「N 扇」。
            _room_doors = []
            for _did, _dr in _door_by_id.items():
                if _rid and _rid in (_dr.get("properties", {}).get("rooms") or []):
                    _room_doors.append(_dr)
            if _room_doors:
                _room_doors.sort(key=lambda d: d.get("id", ""))
                if len(_room_doors) <= 6:
                    _door_cells = []
                    for _dr in _room_doors:
                        _dtype = _dr.get("properties", {}).get("doorType", "swing")
                        _dname = DOOR_TYPE_CN.get(_dtype, _dtype)
                        # link_obj 返回 dict，直接放入 rows（renderCell 识别 _l 渲染为
                        # 可点击链接）；文本拼接「编号（类型）」，不能 f-string 格式化 dict
                        _door_cells.append(link_obj(
                            _dr["id"], f'{_dr["id"]}（{_dname}）'))
                    det["rows"].append(("门", _door_cells))
                else:
                    # 门多时折叠：按类型分组计数 + 详情行逐扇展开
                    _type_cnt = {}
                    for _dr in _room_doors:
                        _dtype = _dr.get("properties", {}).get("doorType", "swing")
                        _type_cnt[_dtype] = _type_cnt.get(_dtype, 0) + 1
                    _summary = "、".join(
                        f"{DOOR_TYPE_CN.get(t, t)}×{c}" for t, c in
                        sorted(_type_cnt.items(), key=lambda kv: -kv[1]))
                    det["rows"].append(("门（{0} 扇）".format(len(_room_doors)), _summary))
                    for _dr in _room_doors:
                        _dtype = _dr.get("properties", {}).get("doorType", "swing")
                        _dname = DOOR_TYPE_CN.get(_dtype, _dtype)
                        det["rows"].append(
                            (f"　{_dname}", link_obj(_dr["id"], f'{_dr["id"]}（{_dname}）')))
            else:
                det["rows"].append(("门", "—（无归属门）"))
            # 关联对应的拓扑房间节点（TR）
            trid = _roomid_to_trid.get(_rid)
            if trid:
                det["topoId"] = trid
            # corridor 类型常因文字标签/多区域合并产生，渲染时只画虚线轮廓（不填色），
            # 避免覆盖下方真房间图层。保留轮廓便于核实位置。
            if rtype == "corridor":
                _fill = "none"
                _stroke = "#B0BEC5"
                _sw = 0.6
                _dash = "4,3"
            elif rtype == "staircase":
                _fill = color
                _stroke = "#E57373"
                _sw = 1.2
                _dash = "6,3"
            else:
                _fill = color
                _stroke = "#999"
                _sw = 0.5
                _dash = "none"
            # 开放空间/前室为完全独立图层（不受「房间」开关影响）：
            # 交互走 [data-info] 属性、导出走 [class*="layer_"]，脱离 layer_room 无副作用
            if rtype == "elevator_lobby":
                layer_cls = "layer_lobby_elevator"
            elif rtype == "stair_lobby":
                layer_cls = "layer_lobby_stair"
            elif rtype == "infrastructure":
                # 风井/管道井等基础设施封闭空间：独立图层，不归入「房间」
                layer_cls = "layer_infrastructure"
            elif rtype in ("corridor", "lobby", "activity", "atrium"):
                layer_cls = "layer_" + rtype
            else:
                layer_cls = "layer_room"
            parts.append(
                f'<g class="{layer_cls}" data-roomid="{_rid or ""}" data-mid="{_rid or ""}" {info_attr({"tip": tip, "detail": det}, floor=floor, coll="rooms", pid=_rid, store="geometry", key="id")}>'
                f'<polygon points="{pts}" fill="{_fill}" stroke="{_stroke}" stroke-width="{_sw}" stroke-dasharray="{_dash}"/></g>\n'
            )
            if label:
                cx_s = sum(p_[0] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                cy_s = sum(p_[1] for p_ in ring[:-1]) / max(len(ring) - 1, 1)
                sx_s, sy_s = tosvg(cx_s, cy_s)
                parts.append(
                    f'<g class="{layer_cls}" {info_attr({"tip": tip, "detail": det})}>'
                    f'<text x="{sx_s}" y="{sy_s}" font-size="6" text-anchor="middle" '
                    f'fill="#333">{label}</text></g>\n'
                )
        if n_skip_bbox:
            print(f"  [F{fk}] 跳过 {n_skip_bbox} 个文字标签包围盒（面积<{LABEL_BBOX_MAX_AREA}m² 且长宽比≥{LABEL_BBOX_MIN_ASPECT}）")

        # 2. 墙体
        MAT_CN = {"concrete": "混凝土", "brick": "砖墙", "partition": "轻质隔墙"}
        for w in geom.get("walls", []):
            c = w["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            wp = w.get("properties", {})
            _wid = w.get("id", "")
            t_m = wp.get("thickness")
            mat = wp.get("material")
            t_disp = f"{t_m*100:.0f} cm" if isinstance(t_m, (int, float)) else "—"
            mat_disp = MAT_CN.get(mat, mat or "—")
            wtip = f"墙体 {_wid}\\n厚度：{t_disp}\\n材质：{mat_disp}"
            wdet = {"title": f"墙体 {_wid}", "rows": [
                ("ID", _wid),
                ("厚度", t_disp),
                ("材质", mat_disp),
                ("材质来源", wp.get("materialSource", "—")),
                ("来源图层", wp.get("sourceLayer", "—")),
            ]}
            parts.append(
                f'<g class="layer_wall" {info_attr({"id": _wid, "tip": wtip, "detail": wdet, "kind": "wall"}, floor=floor, coll="walls", pid=_wid, store="geometry", key="id")}>'
                f'<path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )

        # 3. 窗户段
        for wn in geom.get("windowSegments", []):
            c = wn["geometry"]["coordinates"]
            x1, y1 = tosvg(c[0][0], c[0][1])
            x2, y2 = tosvg(c[1][0], c[1][1])
            _wnid = wn.get("id", "")
            _wnlen = (wn.get("properties") or {}).get("length_m", "")
            _wntip = f"窗户段 {_wnid}\\n长度：{_wnlen} m"
            _wndet = {"title": f"窗户段 {_wnid}", "rows": [
                ("类型", "窗"),
                ("长度", f"{_wnlen} m"),
                ("楼层", f"{fk}F"),
            ]}
            _wnattr = info_attr({"tip": _wntip, "detail": _wndet, "kind": "window"})
            parts.append(f'<g class="layer_window" {_wnattr}>'
                         f'<path d="M {x1} {y1} L {x2} {y2}"/></g>\n')

        # 4. 楼梯
        for st in geom.get("stairs", []):
            ring = st["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            label_s = st["properties"].get("label", "")
            code_s = st["properties"].get("code")
            cent = st["properties"].get("centroid")
            linked = code_s in cf_codes if code_s else False
            tip = f"楼梯：{label_s}" + ("（跨层连通 1F↔2F）" if linked else "（本层独有）")
            det = {"title": label_s or "楼梯", "rows": [
                ("设施编号", link_obj(st["id"])),
                ("类型", "楼梯间"),
                ("井道编号", code_s or "（图纸未标注）"),
                ("跨层连通", "是 · 1F↔2F" if linked else "否 · 仅本层"),
                ("无障碍", "否（视障禁用）"),
            ]}
            _sc = st["properties"].get("centroid") or [0, 0]
            _stid = _nearest_topo(_sc)
            if _stid:
                det["topoId"] = _stid
            parts.append(
                f'<g class="layer_stairs" data-mid="{st["id"]}" {info_attr({"tip": tip, "detail": det}, floor=floor, coll="stairs", pid=st["id"], store="geometry", key="id")}>'
                f'<polygon points="{pts}" fill="#FFCCBC" stroke="#E64A19" stroke-width="0.8"/></g>\n'
            )
            if label_s and cent:
                sx_s, sy_s = tosvg(cent[0], cent[1])
                parts.append(
                    f'<g class="layer_stairs" {info_attr({"tip": tip, "detail": det})}>'
                    f'<text x="{sx_s}" y="{sy_s}" '
                    f'font-size="5" text-anchor="middle" fill="#BF360C">{label_s}</text></g>\n'
                )

        # 5. 电梯
        for ev in geom.get("elevators", []):
            ring = ev["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            label_e = ev["properties"].get("label", "")
            code_e = ev["properties"].get("code")
            cent = ev["properties"].get("centroid")
            linked = code_e in cf_codes if code_e else False
            tip = f"电梯：{label_e}" + ("（跨层连通 1F↔2F）" if linked else "（本层独有）")
            det = {"title": label_e or "电梯", "rows": [
                ("设施编号", link_obj(ev["id"])),
                ("类型", "电梯间"),
                ("井道编号", code_e or "（图纸未标注）"),
                ("跨层连通", "是 · 1F↔2F" if linked else "否 · 仅本层"),
                ("无障碍", "是"),
            ]}
            _ec = ev["properties"].get("centroid") or [0, 0]
            _eid = _nearest_topo(_ec)
            if _eid:
                det["topoId"] = _eid
            parts.append(
                f'<g class="layer_elevator" data-mid="{ev["id"]}" {info_attr({"tip": tip, "detail": det}, floor=floor, coll="elevators", pid=ev["id"], store="geometry", key="id")}>'
                f'<polygon points="{pts}" fill="#F8BBD0" stroke="#C2185B" stroke-width="0.8"/></g>\n'
            )
            if label_e and cent:
                sx_s, sy_s = tosvg(cent[0], cent[1])
                parts.append(
                    f'<g class="layer_elevator" {info_attr({"tip": tip, "detail": det})}>'
                    f'<text x="{sx_s}" y="{sy_s}" '
                    f'font-size="5" text-anchor="middle" fill="#880E4F">{label_e}</text></g>\n'
                )

        # 5b. 电梯门（需求⑱：电梯井外墙窗户识别为电梯门）
        for evd in geom.get("elevatorDoors", []):
            c = evd["geometry"]["coordinates"]
            sx, sy = tosvg(c[0], c[1])
            p = evd["properties"]
            axis = p.get("axis") or [c, c]
            ax0, ay0 = tosvg(axis[0][0], axis[0][1])
            ax1, ay1 = tosvg(axis[1][0], axis[1][1])
            # 需求⑳：归属一律用元素 ID（elevatorId / rooms），label 仅作展示辅助
            _elev_id = p.get("elevatorId") or (p.get("rooms") or [None])[0]
            _elev_lbl = p.get("elevatorLabel") or _elev_id
            det = {"title": f"电梯门（{_elev_id or '?'}）", "rows": [
                ("门编号", link_obj(evd["id"])),
                ("类型", "电梯门"),
                ("所属电梯", link_obj(_elev_id) if _elev_id else "—"),
                ("电梯编号", _elev_lbl or "—"),
                ("门宽", f'{p.get("width_m", 0):.2f} m'),
                ("无障碍", "是"),
            ]}
            _ec = c
            _eid = _nearest_topo(_ec)
            if _eid:
                det["topoId"] = _eid
            _elev_tip = f"电梯门（{_elev_id or '?'}）"
            parts.append(
                f'<g class="layer_elevator_door" data-mid="{evd["id"]}" '
                f'{info_attr({"tip": _elev_tip, "detail": det})}>'
                f'<line x1="{ax0}" y1="{ay0}" x2="{ax1}" y2="{ay1}" '
                f'stroke="#AD1457" stroke-width="1.6" stroke-dasharray="2.5,1.5"/>'
                f'<circle cx="{sx}" cy="{sy}" r="1.8" fill="#F8BBD0" '
                f'stroke="#AD1457" stroke-width="0.6"/></g>\n'
            )

        # 6. 柱
        for col in geom.get("columns", []):
            ring = col["geometry"]["coordinates"][0]
            pts = " ".join(f"{tosvg(p[0], p[1])[0]},{tosvg(p[0], p[1])[1]}" for p in ring)
            _colid = col.get("id", "")
            _coltip = f"柱 {_colid}"
            _coldet = {"title": f"柱 {_colid}", "rows": [
                ("类型", "结构柱"),
                ("楼层", f"{fk}F"),
            ]}
            # 开放度信息（按柱 id 关联 open_column_wraps.json；无记录时仅追加占位行）
            _ocd = col_open.get(_colid)
            if _ocd is None:
                _coldet["rows"].append(("开放度 openness", "—（未分析）"))
            else:
                _reason_cn = {"openness_low": "开放度不足", "rays_low": "有效射线不足"}
                if _ocd.get("_status", "") == "open":
                    _judge = "开放柱（信标候选）"
                else:
                    _judge = _reason_cn.get(_ocd.get("reason", ""),
                                            _ocd.get("reason") or "—")
                _coldet["rows"] += [
                    ("开放度 openness", f"{float(_ocd.get('openness', 0.0)):.3f}"),
                    ("开放扇区 nOpen", str(int(_ocd.get("nOpen", 0)))),
                    ("封闭比 closedRatio", f"{float(_ocd.get('closedRatio', 0.0)):.3f}"),
                    ("判定", _judge),
                ]
            _colattr = info_attr({"tip": _coltip, "detail": _coldet, "kind": "column"},
                                 floor=floor, coll="columns", pid=_colid, store="geometry", key="id")
            parts.append(f'<g class="layer_column" {_colattr}>'
                         f'<polygon points="{pts}"/></g>\n')

        # 7. 门（swing=普通门 / opening=门洞 / fire=防火门）
        # 每类门单独一个图层类名（layer_door_swing / _opening / _fire），
        # 使图层面板可分别开关；同时保留基类 layer_door 供样式复用。
        for dr in geom.get("doors", []):
            c = dr["geometry"]["coordinates"]
            sx, sy = tosvg(c[0], c[1])
            p = dr["properties"]
            dtype = p.get("doorType", "swing")
            w = float(p.get("width_m", 0.9))
            dname = DOOR_TYPE_CN.get(dtype, dtype)
            # 新属性：开启方向 / 合页侧 / 子类
            od = p.get("openDirection")
            OD_CN = {"inward": "内开", "outward": "外开", "none": "无（门洞）"}
            hs = p.get("hingeSide")
            HS_CN = {"left": "左铰链", "right": "右铰链"}
            od_disp = OD_CN.get(od, od or "—")
            hs_disp = HS_CN.get(hs, hs or "—")
            if od in ("inward", "outward") and hs:
                od_full = f"{od_disp} · {hs_disp}"
            else:
                od_full = od_disp
            sub = p.get("doorSubType") or "—"
            wa = p.get("wheelchairAccessible")
            wa_disp = "是" if wa else ("否" if wa is False else "—")
            swing_room = p.get("swingIntoRoom") or "—"
            surv = p.get("surveyRequired") or []
            surv_disp = "、".join(surv) if surv else "无"
            # 防火门常开/常闭
            fire_open = p.get("isNormallyOpen")
            fire_label = "常开" if fire_open else ("常闭" if fire_open is False else "")
            tip = f"{dname}\\n宽度：{w:.2f}m\\n开启：{od_full}"
            if fire_label:
                tip = f"{dname}（{fire_label}）\\n宽度：{w:.2f}m\\n开启：{od_full}"
            _rows = [
                       ("门编号", link_obj(dr.get("id") or p.get("id") or "—")),
                       ("类型", f"{dname}（{dtype}）"),
                   ]
            if fire_label:
                _rows.append(("常开/常闭", fire_label))
            _rows += [
                       ("子类", sub),
                       ("开启方向", od_full),
                       ("摆向房间", link_obj(swing_room) if swing_room and swing_room != "—" else "—"),
                       ("宽度", f"{w:.2f} m"),
                       ("轮椅可达", wa_disp),
                       ("归属房间", [link_obj(rid) for rid in p.get("rooms", [])] if p.get("rooms") else "—"),
                       ("来源图层", p.get("sourceLayer", "—")),
                       ("待现场核实", surv_disp),
                   ]
            det = {"title": dname, "rows": _rows}
            # 关联拓扑门节点（TD）：房间匹配优先（语义最稳），坐标最近兜底；
            # 超过阈值(8/15m) 不关联，避免把门错配到不相关的远距拓扑门（数据层几何门/拓扑门关联缺失）。
            _dt = p.get("doorType")
            _drooms = set(p.get("rooms") or [])
            _dc = dr["geometry"]["coordinates"]
            def _td_dist(n):
                return ((n["coordinates"][0] - _dc[0]) ** 2 +
                        (n["coordinates"][1] - _dc[1]) ** 2) ** 0.5
            _tdid = None
            # 1) 门型 + 房间完全一致
            _c1 = [n for n in _td_nodes
                   if n.get("doorType") == _dt and set(n.get("rooms") or []) == _drooms]
            if _c1:
                _tdid = min(_c1, key=_td_dist)["id"]
            else:
                # 2) 房间完全一致（门型可不同，距离<15m）—— 处理门型标注不一致
                _c2 = [n for n in _td_nodes
                       if set(n.get("rooms") or []) == _drooms and _td_dist(n) < 15]
                if _c2:
                    _tdid = min(_c2, key=_td_dist)["id"]
                else:
                    # 3) 门型一致 + 房间重叠（<8m）
                    _c3 = [n for n in _td_nodes
                           if n.get("doorType") == _dt and
                           (set(n.get("rooms") or []) & _drooms) and _td_dist(n) < 8]
                    if _c3:
                        _tdid = min(_c3, key=_td_dist)["id"]
                    else:
                        # 4) 坐标最近兜底（<8m）
                        _c4 = [n for n in _td_nodes if _td_dist(n) < 8]
                        if _c4:
                            _tdid = min(_c4, key=_td_dist)["id"]
            if _tdid:
                det["topoId"] = _tdid
            attr = info_attr({"tip": tip, "detail": det, "id": dr.get("id", p.get("id", "")), "kind": "door"},
                             floor=floor, coll="doors", pid=dr.get("id", p.get("id", "")), store="geometry", key="id")
            dcls = f'layer_door layer_door_{dtype if dtype in ("swing", "fire", "opening") else "swing"}'
            if dtype == "fire":
                s = max(3.0, w * SCALE * 0.22)
                is_open = p.get("isNormallyOpen")
                fire_color = "#FF5722" if is_open else "#8B0000"  # 常开橙红 / 常闭暗红
                parts.append(
                    f'<g class="{dcls}" data-mid="{dr["id"]}" {attr}>'
                    f'<rect x="{float(sx)-s/2:.1f}" y="{float(sy)-s/2:.1f}" width="{s:.1f}" height="{s:.1f}" '
                    f'fill="{fire_color}" opacity="0.9"/></g>\n'
                )
            elif dtype == "opening":
                s = max(3.2, w * SCALE * 0.24)
                diamond = (f"{float(sx)},{float(sy)-s:.1f} {float(sx)+s:.1f},{float(sy)} "
                           f"{float(sx)},{float(sy)+s:.1f} {float(sx)-s:.1f},{float(sy)}")
                parts.append(
                    f'<g class="{dcls}" data-mid="{dr["id"]}" {attr}>'
                    f'<polygon points="{diamond}" fill="#1E8449" opacity="0.9"/></g>\n'
                )
            else:
                r = max(2.2, w * SCALE * 0.16)
                parts.append(
                    f'<g class="{dcls}" data-mid="{dr["id"]}" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="{r:.1f}" fill="#2196F3" opacity="0.85"/></g>\n'
                )

        # 8. 拓扑节点
        node_map = {n["id"]: n for n in topo.get("nodes", [])}
        for n in topo.get("nodes", []):
            cx, cy = n["coordinates"]
            sx, sy = tosvg(cx, cy)
            ntype = n.get("type", "doorway")
            label_n = n.get("label", "")
            nid = n["id"]
            # 详情
            rows = [("节点ID", link_obj(nid)), ("类型", ntype)]
            _rl = n.get("riskLevel")
            if isinstance(_rl, (int, float)):
                rows.append(("风险等级", f"{_rl:g}"))
            # 反向关联：拓扑节点 → 对应地图要素ID（需求①：选中拓扑节点展示其地图元素）
            if ntype == "room":
                rows.append(("房间", n.get("label", "—")))
                _mrid = n.get("roomId")
                rows.append(("对应房间", link_obj(_mrid) if (_mrid and _mrid in _room_by_id) else "—"))
            elif ntype == "doorway":
                _mdid = _nearest_door_for_td(n)
                rows.append(("对应门", link_obj(_mdid) if _mdid else "—"))
            elif ntype == "facility":
                rows.append(("设施类型", n.get("facilityType", "—")))
                rows.append(("视障可达", "是" if n.get("blindAccessible") else "否"))
                rows.append(("轮椅可达", "是" if n.get("wheelchairAccessible") else "否"))
                _mfid = _fac_by_code.get(n.get("label"))
                rows.append(("对应设施", link_obj(_mfid) if _mfid else "—"))
            elif ntype == "facility_entrance":
                rows.append(("设施类型", n.get("facilityType", "—")))
                rows.append(("对应要素", "—"))
            elif ntype == "intersection":
                _rt = n.get("roomType", "")
                _rt_cn = {"corridor": "走道/走廊", "lobby": "门厅/大厅",
                          "activity": "活动空间", "atrium": "中庭"}.get(_rt, _rt or "开放空间")
                rows.append(("空间类型", _rt_cn))
                rows.append(("对应要素", "—"))
            if label_n:
                rows.append(("标签", label_n))
            det = {"title": label_n or ntype, "rows": rows}
            _type_disp = label_n or ntype
            if ntype == "intersection":
                _rt = n.get("roomType", "")
                _type_disp = {"corridor": "走道/走廊", "lobby": "门厅/大厅",
                              "activity": "活动空间", "atrium": "中庭"}.get(_rt, "开放空间")
            tip = f"拓扑节点：{label_n or ntype}\\n类型：{_type_disp}"
            attr = info_attr({"tip": tip, "detail": det, "id": nid, "kind": "node"},
                             floor=floor, coll="nodes", pid=nid, store="topology", key="id")

            if ntype == "room":
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="3" fill="{NODE_COLORS["room"]}" opacity="0.85"/></g>\n'
                )
            elif ntype == "doorway":
                # 门显示完整编号（楼层-类型-序号），替代类型名"普通门/防火门/门洞"
                door_no = nid
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="2.4" fill="{NODE_COLORS["doorway"]}" opacity="0.85"/>'
                    f'<text x="{float(sx)+3:.1f}" y="{float(sy)+2:.1f}" font-size="4.2" '
                    f'fill="{NODE_COLORS["doorway"]}">{door_no}</text></g>\n'
                )
            elif ntype == "intersection":
                s = 3.2
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<rect x="{float(sx)-s:.1f}" y="{float(sy)-s:.1f}" width="{s*2:.1f}" height="{s*2:.1f}" '
                    f'fill="{NODE_COLORS["intersection"]}" opacity="0.85"/></g>\n'
                )
            elif ntype == "facility":
                color = FACILITY_COLORS.get(n.get("facilityType", "staircase"), "#8E44AD")
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="4.2" fill="{color}" opacity="0.9"/></g>\n'
                )
                if label_n:
                    parts.append(
                        f'<g class="layer_topo_node" {attr}><text x="{float(sx)+6:.1f}" y="{float(sy)+3:.1f}" '
                        f'font-size="5" fill="{color}">{label_n}</text></g>\n'
                    )
            elif ntype == "facility_entrance":
                s = 4.5
                tri = (f"{float(sx)},{float(sy)-s:.1f} {float(sx)-s:.1f},{float(sy)+s:.1f} "
                       f"{float(sx)+s:.1f},{float(sy)+s:.1f}")
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<polygon points="{tri}" fill="{NODE_COLORS["facility_entrance"]}" opacity="0.9"/></g>\n'
                )
                if label_n:
                    parts.append(
                        f'<g class="layer_topo_node" {attr}><text x="{float(sx)+6:.1f}" y="{float(sy)+3:.1f}" '
                        f'font-size="5" fill="{NODE_COLORS["facility_entrance"]}">{label_n}</text></g>\n'
                    )
            else:
                parts.append(
                    f'<g class="layer_topo_node" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="3" fill="#7F8C8D" opacity="0.8"/></g>\n'
                )

        # 9. 拓扑边
        # 骨架模式下 TI↔TI 连接边可达数十万条（早期全量渲染让 HTML 数百 MB，
        # 已改为沿骨架段邻接，F1 53 万→2069 条）。TI↔TI 边独立成
        # layer_topo_edge_titi 图层（默认隐藏，由图层面板开关控制），
        # 走廊连通主视觉仍由 layer_skeleton（青色 polyline）展示。
        n_titi = 0
        for e in topo.get("edges", []):
            n1 = node_map.get(e.get("from"))
            n2 = node_map.get(e.get("to"))
            if not n1 or not n2:
                continue
            is_titi = (n1.get("type") == "intersection"
                       and n2.get("type") == "intersection")
            x1, y1 = tosvg(n1["coordinates"][0], n1["coordinates"][1])
            x2, y2 = tosvg(n2["coordinates"][0], n2["coordinates"][1])
            det = {"title": f"导航边 {e.get('id','')}", "rows": [
                ("起始", e.get("from", "—")),
                ("终点", e.get("to", "—")),
                ("距离", f"{e.get('distance',0):.2f} m"),
                ("预估时间", f"{e.get('estimatedTime',0):.1f} s"),
                ("可达等级", e.get("accessibilityLevel", "—")),
                ("风险等级", e.get("riskLevel", "—")),
                ("可步行", "是" if e.get("walkable") else "否"),
                ("轮椅", "是" if e.get("wheelchairAccessible") else "否"),
                ("视障", "是" if e.get("blindAccessible") else "否"),
            ]}
            tip = f"导航边\\n距离 {e.get('distance',0):.1f}m · 视障 {('是' if e.get('blindAccessible') else '否')}"
            attr = info_attr({"tip": tip, "detail": det, "from": e.get("from", ""),
                              "to": e.get("to", ""), "id": e.get("id", ""),
                              "kind": "edge"},
                             floor=floor, coll="edges", pid=e.get("id", ""), store="topology", key="id")
            cls = "layer_topo_edge_titi" if is_titi else "layer_topo_edge"
            parts.append(
                f'<g class="{cls}" {attr}><path d="M {x1} {y1} L {x2} {y2}"/></g>\n'
            )
            if is_titi:
                n_titi += 1

        # 10. 风险 / 坡道 / 盲道 / 材质（若存在）
        for r in acc.get("riskNodes", []):
            cx, cy = r["coordinates"]
            sx, sy = tosvg(cx, cy)
            rtype = r.get("type", r.get("riskType", "stair_entrance"))
            rlabel = r.get("label", "")
            tip = f"风险点：{rlabel or rtype}"
            det = {"title": rlabel or "风险点", "rows": [("类型", rtype), ("描述", r.get("description", "—"))]}
            attr = info_attr({"tip": tip, "detail": det, "kind": "risk"})
            parts.append(
                f'<g class="layer_risk" {attr}>'
                f'<circle cx="{sx}" cy="{sy}" r="5" fill="#F44336" opacity="0.6"/>'
                f'<text x="{sx}" y="{float(sy)+1.6:.1f}" font-size="3.5" text-anchor="middle" fill="#fff">!</text></g>\n'
            )
        for rp in acc.get("ramps", []):
            loc = rp.get("location", rp.get("coordinates", [0, 0]))
            sx, sy = tosvg(loc[0], loc[1])
            attr = info_attr({"tip": "坡道", "detail": {"title": "坡道", "rows": [("编号", rp.get("id", "—"))]}, "kind": "ramp"})
            parts.append(
                f'<g class="layer_ramp" {attr}>'
                f'<circle cx="{sx}" cy="{sy}" r="8" fill="none" stroke="#4CAF50" '
                f'stroke-width="1.5" stroke-dasharray="3,2"/></g>\n'
            )
        for tp in acc.get("tactilePaths", []):
            path = tp.get("path", [])
            if len(path) < 2:
                continue
            d_parts = []
            for pt in path:
                sx_pt, sy_pt = tosvg(pt[0], pt[1])
                d_parts.append(f"M {sx_pt} {sy_pt}" if not d_parts else f"L {sx_pt} {sy_pt}")
            attr = info_attr({"tip": "盲道路径", "detail": {"title": "盲道路径", "rows": [("段数", len(path))]}, "kind": "tactile"})
            parts.append(
                f'<g class="layer_tactile" {attr}>'
                f'<path d="{" ".join(d_parts)}" stroke="#FFD600" stroke-width="1.8" fill="none" '
                f'stroke-linecap="round" opacity="0.8"/></g>\n'
            )
        for gmc in acc.get("groundMaterialChanges", []):
            pt = gmc.get("coordinates", [0, 0])
            sx, sy = tosvg(pt[0], pt[1])
            sz = 6
            pts_d = (f"{sx},{float(sy)-sz} {float(sx)+sz*0.7:.1f},{sy} {sx},{float(sy)+sz} {float(sx)-sz*0.7:.1f},{sy}")
            desc = gmc.get("description", gmc.get("id", ""))[:25]
            attr = info_attr({"tip": "地面材质变化", "detail": {"title": "地面材质变化", "rows": [("描述", desc)]}, "kind": "material"})
            parts.append(
                f'<g class="layer_material" {attr}>'
                f'<polygon points="{pts_d}" fill="#9C27B0" opacity="0.75" stroke="#6A1B9A" stroke-width="0.5"/>'
                f'<text x="{float(sx)+9:.1f}" y="{float(sy)+3:.1f}" font-size="4" fill="#6A1B9A">{desc}</text></g>\n'
            )

        # 10.5 建筑外轮廓（实线）—— 基于闭合空间多边形 + 墙体段栅格化，膨胀弥合门洞缺口，
        # 取「外部泛洪的补集」作为建筑实体后追踪最外轮廓；按相对面积阈值过滤小噪声块，用实线绘制。
        outline_polys = building_outline(geom, cell=0.1, wall_hw=1, close_r=14)
        if outline_polys:
            _areas = [_area(p) for p in outline_polys]
            _max_a = max(_areas)
            # 仅保留最大块的 ≥5%（或绝对 ≥150m²），剔除家具/孤立柱簇等小噪声连通块
            _thr = max(150.0, 0.05 * _max_a)
            outline_polys = [p for p, a in zip(outline_polys, _areas) if a >= _thr]
        if outline_polys:
            total_oa = sum(_area(p) for p in outline_polys)
            print(f"  [F{fk}] 建筑外轮廓: {len(outline_polys)} 块, 总面积 ~{total_oa:.0f} m²")
        _boa = sum(_area(p) for p in outline_polys) if outline_polys else 0.0
        _botip = f"建筑外轮廓\\n总面积约 {_boa:.0f} m² · {len(outline_polys)} 块"
        _bodet = {"title": "建筑外轮廓", "rows": [
            ("类型", "建筑外轮廓（含门洞弥合）"),
            ("轮廓块数", f"{len(outline_polys)}"),
            ("总面积", f"{_boa:.0f} m²"),
            ("楼层", f"{fk}F"),
        ]}
        _boattr = info_attr({"tip": _botip, "detail": _bodet, "kind": "outline"})
        for poly in outline_polys:
            pts = " ".join(f"{tosvg(px, py)[0]},{tosvg(px, py)[1]}" for px, py in poly)
            parts.append(
                f'<g class="layer_building_outline" {_boattr}>'
                f'<polygon points="{pts}" fill="none" stroke="#222" '
                f'stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></g>\n'
            )

        # 11-12. 方案相关图层（指纹采集点 + 三点覆盖 + 信标部署点），按模式分组
        # 全局模式（默认显示）与测试路线模式（隐藏，顶部开关切换），两套数据并存
        cov_fk = cov_index.get(str(fk))

        def render_scheme_group(fp_fd, bc_fk, mode):
            """渲染一套方案（指纹+覆盖+信标），包进 <g class="mode-xxx"> 供模式开关显隐。"""
            if not bc_fk:
                return
            hide = ' style="display:none"' if mode == "route" else ""
            parts.append(f'<g class="mode-{mode}" data-floor="{fk}"{hide}>\n')
            # 指纹采集点
            if fp_fd:
                n_fp = 0
                for p in fp_fd.get("points", []):
                    cx, cy = p["coordinates"][0], p["coordinates"][1]
                    sx, sy = tosvg(cx, cy)
                    is_safe = p.get("regionType") == "safe"
                    col = "#FF7043" if is_safe else "#42A5F5"
                    r = 2.4 if is_safe else 1.8
                    prio = p.get("priority", 3)
                    src = p.get("source", "")
                    tip = f"指纹采集点 {p.get('id','')}\\n区域：{'安全节点' if is_safe else '普通'} · 优先级 {prio} · 来源 {src}"
                    det = {"title": f"指纹采集点 {p.get('id','')}", "rows": [
                        ("楼层", f"{p.get('floor','?')}F"),
                        ("区域类型", "安全节点" if is_safe else "普通"),
                        ("采集优先级", str(prio)),
                        ("来源", src),
                    ]}
                    if p.get("nearNodeId"):
                        det["rows"].append(("邻近节点", f"{p['nearNodeId']} ({p.get('nearNodeType','')})"))
                    attr = info_attr({"tip": tip, "detail": det, "kind": "fingerprint", "id": p.get("id", "")})
                    parts.append(
                        f'<g class="layer_fingerprint" {attr}>'
                        f'<circle cx="{sx}" cy="{sy}" r="{r}" fill="{col}" '
                        f'fill-opacity="0.85" stroke="#fff" stroke-width="0.4"/></g>\n'
                    )
                    n_fp += 1
                if n_fp:
                    print(f"  [F{fk}][{mode}] 指纹网格图层: {n_fp} 个点")
            # 三点定位覆盖（对每指纹点按可见信标数着色；贡献点 key 带 mode 前缀避免跨方案 beaconId 冲突）
            if fp_fd and cov_fk is not None:
                n_cov = 0
                bc_id = [(b.get("beaconId", ""), (b["coordinates"][0], b["coordinates"][1])) for b in bc_fk]
                for p in fp_fd.get("points", []):
                    cx, cy = p["coordinates"][0], p["coordinates"][1]
                    sx, sy = tosvg(cx, cy)
                    vis_ids = visible_ids(cx, cy, bc_id, cov_fk)
                    vis = len(vis_ids)
                    if vis == 3:
                        for bid in vis_ids:
                            if bid:
                                beacon_contrib[f"{mode}:{bid}"].append([round(cx, 2), round(cy, 2)])
                    col = COV_COLORS.get(min(vis, 3), "#43A047")
                    ok = vis >= 3
                    tip = f"三点定位覆盖\\n可见信标 {vis} 个 · {'可定位' if ok else '覆盖不足'}"
                    det = {"title": f"指纹点 {p.get('id','')} 覆盖", "rows": [
                        ("楼层", f"{p.get('floor','?')}F"),
                        ("可见信标数", str(vis)),
                        ("三点定位", "可" if ok else "不足（需≥3）"),
                        ("覆盖信标", "、".join(vis_ids) if vis_ids else "—"),
                    ]}
                    attr = info_attr({"tip": tip, "detail": det, "kind": "coverage",
                                      "id": p.get("id", ""), "covBeacons": vis_ids})
                    parts.append(
                        f'<g class="layer_coverage" style="display:none" data-mode="{mode}" {attr}>'
                        f'<circle cx="{sx}" cy="{sy}" r="3.2" fill="{col}" '
                        f'fill-opacity="0.82" stroke="#fff" stroke-width="0.5"/></g>\n'
                    )
                    n_cov += 1
                if n_cov:
                    print(f"  [F{fk}][{mode}] 三点定位覆盖图层: {n_cov} 个点")
            # 信标部署点
            n_bc = 0
            for b in bc_fk:
                cx, cy = b["coordinates"][0], b["coordinates"][1]
                sx, sy = tosvg(cx, cy)
                sem = b.get("semanticTag", "")
                col = BEACON_COLORS.get(sem, "#555555")
                r = 3.2
                bid = b.get("beaconId", "")
                tip = f"信标 {bid}\\n语义：{sem} · {b.get('floor')}F"
                det = {"title": f"信标 {bid}", "rows": [
                    ("信标 ID", bid),
                    ("语义标签", sem),
                    ("UUID", b.get("uuid", "")),
                    ("Major", b.get("major", "")),
                    ("Minor", b.get("minor", "")),
                    ("楼层", f"{b.get('floor')}F"),
                    ("安装位置", b.get("locationDesc", "")),
                    ("安装方式", b.get("mountType", "")),
                    ("吸附偏移", f"{b.get('snapDist_m', 0)} m"),
                    ("类型/方向", "/".join(filter(None, [b.get("subType", ""), b.get("direction", "")])) or "—"),
                    ("发射功率", f"{b.get('txPower')} dBm"),
                    ("广播间隔", f"{b.get('broadcastInterval')} ms"),
                    ("安装高度", f"{b.get('installHeight')} m"),
                    ("电池型号", b.get("batteryModel", "")),
                    ("预期寿命", f"{b.get('expectedLifespan')} 年" if b.get("expectedLifespan") else ""),
                    ("来源节点", b.get("sourceNodeId", "")),
                ]}
                attr = info_attr({"tip": tip, "detail": det, "kind": "beacon", "id": bid, "b": b})
                parts.append(
                    f'<g class="layer_beacon" data-floor="{fk}" data-mode="{mode}" {attr}>'
                    f'<circle cx="{sx}" cy="{sy}" r="{r}" fill="{col}" '
                    f'fill-opacity="0.9" stroke="#ffffff" stroke-width="0.5"/>'
                    f'<text x="{fmt(float(sx) + 4)}" y="{fmt(float(sy) + 1.5)}" '
                    f'font-size="4.5" fill="{col}" opacity="0.95">{bid}</text></g>\n'
                )
                n_bc += 1
            if n_bc:
                print(f"  [F{fk}][{mode}] 信标部署点图层: {n_bc} 个信标")
            parts.append('</g>\n')

        # 全局模式组（默认显示）
        render_scheme_group(fp_floors.get(str(fk)), beacon_floors.get(str(fk)), "global")
        # 测试路线模式组（默认隐藏，顶部开关切换）
        render_scheme_group(fp_floors_routes.get(str(fk)), beacon_floors_routes.get(str(fk)), "route")

        # ===== 实际坐标系（与地图真实位置对齐，非图例）：5m 网格 + 1m 刻度尺
        #       + y=0/x=0 轴高亮 + 原点(0,0)。独立 class，不随图层开关显隐 =====
        parts.append(f'<g class="coord-grid" pointer-events="none">')
        # 地图内容区像素范围
        c_min_px = MARGIN_X
        c_max_px = MARGIN_X + (max_x - min_x) * SCALE
        c_min_py = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - max_y) * SCALE
        c_max_py = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - min_y) * SCALE
        # 1) 5m 网格（浅灰细线）
        x0g = math.ceil(min_x / 5.0) * 5.0
        x1g = math.floor(max_x / 5.0) * 5.0
        xv = x0g
        while xv <= x1g:
            px = MARGIN_X + (xv - min_x) * SCALE
            parts.append(f'<line x1="{px:.1f}" y1="{c_min_py:.1f}" x2="{px:.1f}" y2="{c_max_py:.1f}" stroke="#e6e6e6" stroke-width="0.4"/>')
            xv += 5.0
        y0g = math.ceil(min_y / 5.0) * 5.0
        y1g = math.floor(max_y / 5.0) * 5.0
        yv = y0g
        while yv <= y1g:
            py = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - yv) * SCALE
            parts.append(f'<line x1="{c_min_px:.1f}" y1="{py:.1f}" x2="{c_max_px:.1f}" y2="{py:.1f}" stroke="#e6e6e6" stroke-width="0.4"/>')
            yv += 5.0
        # 2) X 刻度尺（沿内容区底部，1m 短刻度 / 5m 长刻度+坐标值）
        ruler_y = c_max_py + 6
        for xv in range(math.floor(min_x), math.ceil(max_x) + 1):
            px = MARGIN_X + (xv - min_x) * SCALE
            if xv % 5 == 0:
                parts.append(f'<line x1="{px:.1f}" y1="{ruler_y}" x2="{px:.1f}" y2="{ruler_y + 6}" stroke="#666" stroke-width="0.8"/>')
                parts.append(f'<text x="{px:.1f}" y="{ruler_y + 13}" font-size="5.5" text-anchor="middle" fill="#555">{xv}</text>')
            else:
                parts.append(f'<line x1="{px:.1f}" y1="{ruler_y}" x2="{px:.1f}" y2="{ruler_y + 3}" stroke="#999" stroke-width="0.5"/>')
        # 3) Y 刻度尺（沿内容区左侧，1m 短刻度 / 5m 长刻度+坐标值）
        ruler_x = c_min_px - 8
        for yv in range(math.floor(min_y), math.ceil(max_y) + 1):
            py = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - yv) * SCALE
            if yv % 5 == 0:
                parts.append(f'<line x1="{ruler_x - 6}" y1="{py:.1f}" x2="{ruler_x}" y2="{py:.1f}" stroke="#666" stroke-width="0.8"/>')
                parts.append(f'<text x="{ruler_x - 7}" y="{py + 2:.1f}" font-size="5.5" text-anchor="end" fill="#555">{yv}</text>')
            else:
                parts.append(f'<line x1="{ruler_x - 3}" y1="{py:.1f}" x2="{ruler_x}" y2="{py:.1f}" stroke="#999" stroke-width="0.5"/>')
        # 4) 轴高亮：y=0（X 轴）与 x=0（Y 轴），位于可视范围时红色虚线
        gy0 = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - 0.0) * SCALE
        gx0 = MARGIN_X + (0.0 - min_x) * SCALE
        if min_y <= 0 <= max_y:
            parts.append(f'<line x1="{c_min_px:.1f}" y1="{gy0:.1f}" x2="{c_max_px:.1f}" y2="{gy0:.1f}" stroke="#C62828" stroke-width="0.9" stroke-dasharray="6,3"/>')
            parts.append(f'<text x="{c_min_px + 3:.1f}" y="{gy0 - 3:.1f}" font-size="6" fill="#C62828">y=0（X 轴）</text>')
        if min_x <= 0 <= max_x:
            parts.append(f'<line x1="{gx0:.1f}" y1="{c_min_py:.1f}" x2="{gx0:.1f}" y2="{c_max_py:.1f}" stroke="#C62828" stroke-width="0.9" stroke-dasharray="6,3"/>')
            parts.append(f'<text x="{gx0 + 2:.1f}" y="{c_min_py + 10}" font-size="6" fill="#C62828">x=0（Y 轴）</text>')
        # 5) 原点 (0,0)（y=0 与 x=0 交点，位于地图右外侧）
        ox_px = MARGIN_X + (0.0 - min_x) * SCALE
        oy_px = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - 0.0) * SCALE
        parts.append(f'<circle cx="{ox_px:.1f}" cy="{oy_px:.1f}" r="2.6" fill="#C62828"/>')
        parts.append(f'<text x="{ox_px - 3:.1f}" y="{oy_px - 4:.1f}" font-size="6.5" fill="#C62828" text-anchor="end">原点(0,0)</text>')
        parts.append('</g>\n')

        # 楼层分隔线
        if i < len(sorted_floors) - 1:
            sep_y = (i + 1) * svh_per_floor
            parts.append(
                f'<line x1="0" y1="{sep_y}" x2="{svw}" y2="{sep_y}" '
                f'stroke="#e0e0e0" stroke-width="1" stroke-dasharray="4,2"/>\n'
            )

    # ---------------- 跨层连接线（含端点提示详情） ----------------
    for e in cf:
        from_info = node_lookup.get(e.get("from", ""))
        to_info = node_lookup.get(e.get("to", ""))
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
            line_color, text_color, etype_label = "#E53935", "#C62828", "楼梯连接"
        elif etype == "elevator":
            line_color, text_color, etype_label = "#1E88E5", "#1565C0", "电梯连接"
        else:
            line_color, text_color, etype_label = "#9C27B0", "#7B1FA2", etype
        mid_x = (sx1 + sx2) / 2
        mid_y = (sy1 + sy2) / 2
        eid = e.get("id", "")
        code = e.get("code") or ""
        matched_by = "图纸井道编号" if e.get("matchedBy") == "code" else "几何中心距离"
        blind_ok = "✓" if e.get("blindAccessible") else "✗"
        wheel_ok = "✓" if e.get("wheelchairAccessible") else "✗"
        det = {"title": f"跨层连接 {code or eid}", "rows": [
            ("类型", etype_label),
            ("井道编号", code or "（图纸未标注）"),
            ("连接", f"{f1}F ↔ {f2}F"),
            ("配对依据", matched_by),
            ("边 ID", eid),
            ("距离", f"{e.get('distance',0):.2f} m"),
            ("视障可达", "是" if e.get("blindAccessible") else "否"),
            ("轮椅可达", "是" if e.get("wheelchairAccessible") else "否"),
        ]}
        tip = f"跨层：{code or eid} · {etype_label} {f1}F↔{f2}F"
        attr = info_attr({"tip": tip, "detail": det, "kind": "crossfloor"},
                         floor=floor, coll="crossFloorEdges", pid=eid, store="root", key="id")
        # 端点处也标注井道编号：缩放到单层时依然能直接读出是哪个楼梯/电梯井
        endpoint_tags = "".join(
            f'<text x="{fmt(sx + 5)}" y="{fmt(sy - 4)}" font-size="4.5" '
            f'fill="{text_color}" opacity="0.9">{code}</text>'
            for sx, sy in ((sx1, sy1), (sx2, sy2))) if code else ""
        parts.append(
            f'<g class="layer_crossfloor" {attr}>'
            f'<path d="M {fmt(sx1)} {fmt(sy1)} L {fmt(sx2)} {fmt(sy2)}" stroke="{line_color}"/>'
            f'<circle cx="{fmt(sx1)}" cy="{fmt(sy1)}" r="3" fill="{line_color}" opacity="0.7"/>'
            f'<circle cx="{fmt(sx2)}" cy="{fmt(sy2)}" r="3" fill="{line_color}" opacity="0.7"/>'
            f'{endpoint_tags}'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y-10)}" font-size="6" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.8">{code or eid}</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y+4)}" font-size="5" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.72">{etype_label} ({f1}F↔{f2}F)</text>'
            f'<text x="{fmt(mid_x)}" y="{fmt(mid_y+15)}" font-size="4" fill="{text_color}" '
            f'text-anchor="middle" opacity="0.66">盲:{blind_ok} 轮椅:{wheel_ok}</text></g>\n'
        )

    parts.append(
        f'<text x="20" y="{svh-8}" font-size="9" fill="#999">'
        f'跨层连接: {len(cf)} 条 | 建筑: {geo.get("venueName","")} | 版本: {geo.get("version","?")}</text>\n'
    )


    # ---------------- 路径规划图数据（前端 Dijkstra） ----------------
    # C4：路由规则辅助量已由 build_geojson 一次性写入顶层 routeExtras（与
    # route_rules 同源），渲染端直接读取，不再每次渲染重算 O(边×墙)；
    # 旧 GeoJSON 缺少该字段时回退到共享 compute_route_rule_extras 计算。
    _rule_extras = geo.get("routeExtras")
    if _rule_extras is None:
        if compute_route_rule_extras is None:
            raise SystemExit(
                "GeoJSON 缺少 routeExtras 且无法回退计算（需 shapely）；"
                "请用新版管线重新生成 GeoJSON")
        _rule_extras = compute_route_rule_extras(geo)
    _edge_door_type_map = _rule_extras["edge_door_type"]
    _room_best_door = _rule_extras["room_best_door"]
    _wall_crossing_titi = _rule_extras["wall_crossing_titi"]
    _infra_doorway_ids = _rule_extras["infra_doorway_ids"]

    path_nodes = {}
    path_edges = []
    for fi, fk in enumerate(sorted_floors):
        fd = geo["floors"][fk]
        fbase_y = fi * svh_per_floor
        for n in (fd.get("topology") or {}).get("nodes") or []:
            cx, cy = n["coordinates"]
            sx = MARGIN_X + (cx - ox) * SCALE
            sy = fbase_y + FLOOR_TITLE_H + MARGIN_Y + (oy - cy) * SCALE
            nd = {
                "id": n["id"],
                "type": n.get("type"),
                "label": n.get("label") or "",
                "floor": int(fk),
                "x": round(sx, 1),
                "y": round(sy, 1),
                "mx": cx,
                "my": cy,
                "facilityType": n.get("facilityType"),
                "roomType": n.get("roomType"),
                "roomId": n.get("roomId"),
                "doorType": n.get("doorType"),
                "rooms": n.get("rooms") or [],
                "isNormallyOpen": n.get("isNormallyOpen"),
                # 需求⑳：电梯门归属用元素 ID（elevatorId），不用 label
                "elevatorId": n.get("elevatorId"),
            }
            if n.get("type") == "room" and n["id"] in _room_best_door:
                nd["bestDoorType"] = _room_best_door[n["id"]]
            path_nodes[n["id"]] = nd
        for e in (fd.get("topology") or {}).get("edges") or []:
            edt = _edge_door_type_map.get(f"{fk}:{e.get('id')}")
            path_edges.append({
                "id": e.get("id"),
                "from": e.get("from"),
                "to": e.get("to"),
                "distance": float(e.get("distance") or 0),
                "accessibilityLevel": e.get("accessibilityLevel", 0),
                "blindAccessible": e.get("blindAccessible", True),
                "wheelchairAccessible": e.get("wheelchairAccessible", True),
                "crossFloor": False,
                "type": e.get("type"),
                "doorType": edt,
                "wallCrossing": f"{fk}:{e.get('id')}" in _wall_crossing_titi,
            })
    for e in geo.get("crossFloorEdges") or []:
        path_edges.append({
            "id": e.get("id"),
            "from": e.get("from"),
            "to": e.get("to"),
            "distance": float(e.get("distance") or 0),
            "accessibilityLevel": e.get("accessibilityLevel", 0),
            "blindAccessible": e.get("blindAccessible", True),
            "wheelchairAccessible": e.get("wheelchairAccessible", True),
            "crossFloor": True,
            "type": e.get("type"),
            "doorType": None,
            "wallCrossing": False,
        })
    path_graph_js = json.dumps(
        {"nodes": path_nodes, "edges": path_edges,
         "infraDoorwayIds": sorted(_infra_doorway_ids),
         # A2：路由规则序列化注入，前端 Dijkstra 禁止内嵌常量
         "rules": build_path_rules_js()},
        ensure_ascii=False, separators=(",", ":"))
    parts.append(
        f'<script type="application/json" id="path-graph-data">{path_graph_js}</script>\n'
    )
    # 地图要素中心表：要素ID -> [svg像素中心x, y]，供详情面板点击ID后居中定位（需求②）
    map_centers_js = json.dumps(map_centers, ensure_ascii=False, separators=(",", ":"))
    parts.append(
        f'<script type="application/json" id="map-centers-data">{map_centers_js}</script>\n'
    )
    # 完整 GeoJSON 数据：供「拓扑边编辑」在浏览器内增删边后整体写回文件
    full_geojson_js = json.dumps(geo, ensure_ascii=False, separators=(",", ":"))
    parts.append(
        f'<script type="application/json" id="full-geojson-data">{full_geojson_js}</script>\n'
    )
    # 信标关键贡献点表（JS 选中信标时高亮：该点恰靠此信标维持 >=3 可见）
    parts.append(
        f'<script type="application/json" id="beacon-contrib-data">'
        f'{json.dumps(dict(beacon_contrib), ensure_ascii=False, separators=(",", ":"))}</script>\n'
    )

    # ---------------- 图例 + 详情面板 + JS ----------------

    # ---- B1：静态模板（templates/interactive.html + static/app.js）+ 动态部分组装 ----
    svg_body = "".join(parts)   # 动态 SVG 楼层 + 数据注入 scripts
    out_html = (_tpl_html
                .replace("__SVG_BODY__", svg_body)
                .replace("__MAIN_JS__", _app_js
                         .replace("__NFLOORS__", str(len(sorted_floors)))
                         .replace("__PERFLOOR__", str(svh_per_floor))))
    # 头部动态值（模板占位符；先长后短，避免 __N_CF__ 误伤 __N_CF_STAIR__）
    for _k, _v in sorted(_header_values.items(), key=lambda kv: len(kv[0]), reverse=True):
        out_html = out_html.replace(_k, _v)
    # 注入「区域标注」交互脚本（独立 <script>，含坐标反变换常量）
    anno_script = build_anno_script(min_x, max_y, svh_per_floor, sorted_floors)
    out_html = out_html.replace("</body></html>", anno_script + "\n</body></html>")
    # 注入「人工部署信标」交互脚本（独立 <script>，紧随 anno_script；含 DEPLOY_GEOX/DEPLOY_DEFAULTS）
    deploy_script = build_deploy_script(min_x, max_y, svh_per_floor, sorted_floors, deploy_defaults)
    out_html = out_html.replace("</body></html>", deploy_script + "\n</body></html>")

    with open(_args.out, "w", encoding="utf-8") as f:
        f.write(out_html)

    print(f"已生成: {_args.out}")
    print(f"  SVG: {svw} × {svh} px | 每层 {svh_per_floor} px")
    print(f"  坐标范围: x[{min_x:.1f}, {max_x:.1f}], y[{min_y:.1f}, {max_y:.1f}]")
    print(f"  楼层: {len(sorted_floors)} 层 | 跨层连接: {len(cf)} 条")


if __name__ == "__main__":
    main()
