# -*- coding: utf-8 -*-
"""GeoJSON 楼层地图渲染脚本。

读取 parse_cad_pdf.py 生成的楼层 GeoJSON（结构参考
school_building_01_map_v7.geojson：geometry / semantic / topology 三段式），
为每个楼层渲染平面图 PNG。

用法:
    python render_map.py [geojson_path] [--topology]

参数:
    geojson_path   输入 GeoJSON，默认 result/school_building_01_map_v8.geojson
    --topology     额外输出叠加导航拓扑（节点/边）的调试图

输出（与 GeoJSON 同目录，通常为 result/）:
    map_render_f{floor}.png            楼层平面图
    map_render_f{floor}_topology.png   拓扑叠加图（仅 --topology）

坐标系: local_meters（米，Y 轴指北向上，由 CAD pt 坐标经 SCALE 换算并翻转得到）。
"""
import json
import math
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyArrow, Patch, Polygon as MplPolygon, Rectangle
from shapely.geometry import Polygon as ShpPolygon
from shapely.ops import polylabel

# ---------------------------------------------------------------- 配置

# Windows 中文字体，避免标签显示为方框
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

DEFAULT_GEOJSON = r"E:\code\pathai\result\school_building_01_map_v8.geojson"

# 房间类型配色（室内地图常用 pastel 色系）
ROOM_COLORS = {
    "classroom": "#FFF3C4",      # 教室 浅黄
    "office": "#D6E4FF",         # 办公 浅蓝
    "meeting": "#FFE8C8",        # 会议 浅橙
    "toilet": "#CFF0FD",         # 卫生间 浅青
    "corridor": "#EFEFEF",       # 走道 浅灰
    "lobby": "#FFF9E0",          # 门厅 米白
    "staircase": "#E4E4E4",      # 楼梯间 灰
    "elevator_hall": "#E4E4E4",  # 电梯厅 灰
    "storage": "#EAD9FF",        # 储藏 浅紫
    "equipment": "#F5D5C8",      # 设备 浅砖
    "medical": "#FFD6D6",        # 医务 浅红
    "lab": "#D9F2D9",            # 实验室 浅绿
    "reception": "#FFE8F0",      # 接待 浅粉
    "shaft": "#D8D8D8",          # 管井 深灰
    "atrium": "#F7F7F7",         # 中庭 近白
    "room": "#F5F5DC",           # 通用房间 米色
}
DEFAULT_ROOM_COLOR = "#F5F5DC"

WALL_COLOR = "#222222"      # 墙体 近黑
WINDOW_COLOR = "#2E8B8B"    # 窗 青
DOOR_SWING_COLOR = "#1F6FB2"  # 平开门 蓝
DOOR_FIRE_COLOR = "#C0392B"   # 防火门 红
COLUMN_COLOR = "#555555"    # 柱 深灰
STAIR_EDGE = "#777777"

NODE_COLORS = {"room": "#E67E22", "door": "#C0392B", "staircase": "#8E44AD",
               "elevator": "#16A085"}


# ---------------------------------------------------------------- 工具

def _rings(geom):
    """统一处理 Polygon / MultiPolygon，产出 [(外环, [内环...]), ...]"""
    t = geom["type"]
    cs = geom["coordinates"]
    polys = cs if t == "MultiPolygon" else [cs]
    out = []
    for poly in polys:
        if not poly:
            continue
        out.append((poly[0], poly[1:]))
    return out


def _draw_polygon(ax, geom, facecolor, edgecolor, lw, zorder, alpha=1.0):
    for ext, holes in _rings(geom):
        ax.add_patch(MplPolygon(ext, closed=True, facecolor=facecolor,
                                edgecolor=edgecolor, lw=lw, zorder=zorder,
                                alpha=alpha))
        for hole in holes:  # 内环用底色回填，形成"洞"
            ax.add_patch(MplPolygon(hole, closed=True, facecolor="white",
                                    edgecolor=edgecolor, lw=lw * 0.6,
                                    zorder=zorder))


def _poly_area(ext):
    a = 0.0
    for i in range(len(ext) - 1):
        a += ext[i][0] * ext[i + 1][1] - ext[i + 1][0] * ext[i][1]
    return abs(a) / 2.0


def _collect_bounds(floor):
    xs, ys = [], []

    def eat_pts(coords):
        for p in coords:
            xs.append(p[0])
            ys.append(p[1])

    g = floor["geometry"]
    for w in g.get("walls", []):
        eat_pts(w["geometry"]["coordinates"])
    for r in g.get("rooms", []):
        for ext, _ in _rings(r["geometry"]):
            eat_pts(ext)
    for key in ("stairs", "elevators", "columns"):
        for f in g.get(key, []):
            for ext, _ in _rings(f["geometry"]):
                eat_pts(ext)
    for dr in g.get("doors", []):
        eat_pts([dr["geometry"]["coordinates"]])
    return (min(xs), max(xs), min(ys), max(ys)) if xs else (0, 1, 0, 1)


# ---------------------------------------------------------------- 图层绘制

def _label_anchor(ext, fallback):
    """标签锚点：polylabel（内切圆心，保证在多边形内且远离边界）"""
    try:
        poly = ShpPolygon(ext)
        if poly.is_valid and not poly.is_empty:
            pt = polylabel(poly, tolerance=0.1)
            return (pt.x, pt.y)
    except Exception:
        pass
    return fallback


def draw_rooms(ax, rooms, dupp=0.12):
    """房间填充 + 类型着色 + 标签。

    标签字号受面积、房间横向跨度（文本宽）与纵向跨度（字高）约束；
    锚点用 polylabel，避免狭长/异形房间标签贴边或与邻房重叠。
    dupp: data units per point（由 figsize 与坐标范围换算）。
    """
    for r in rooms:
        p = r["properties"]
        rt = p.get("roomType", "room")
        color = ROOM_COLORS.get(rt, DEFAULT_ROOM_COLOR)
        _draw_polygon(ax, r["geometry"], color, "#999999", 0.5, zorder=1)
    for r in rooms:
        p = r["properties"]
        label = p.get("label") or ""
        c = p.get("centroid")
        if not label or not c:
            continue
        ext = _rings(r["geometry"])[0][0]
        xs = [pt[0] for pt in ext]
        ys = [pt[1] for pt in ext]
        x_span = max(xs) - min(xs)
        y_span = max(ys) - min(ys)
        fs = min(12.0, math.sqrt(max(_poly_area(ext), 0.1)) * 1.9)
        # 水平文本：估算宽度不超过横向跨度的 88%
        em = sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in label)
        if em > 0 and x_span > 0:
            fs = min(fs, 0.88 * x_span / (em * dupp))
        # 字高不超过纵向跨度的 55%
        if y_span > 0:
            fs = min(fs, 0.55 * y_span / dupp)
        fs = max(4.0, fs)
        rt = p.get("roomType", "room")
        tc = "#666666" if rt in ("corridor", "atrium") else "#1A1A2E"
        lx, ly = _label_anchor(ext, (c[0], c[1]))
        ax.text(lx, ly, label, ha="center", va="center",
                fontsize=fs, color=tc, zorder=8)


def draw_walls(ax, walls):
    for w in walls:
        cs = w["geometry"]["coordinates"]
        ax.plot([p[0] for p in cs], [p[1] for p in cs],
                color=WALL_COLOR, lw=1.1, solid_capstyle="round", zorder=4)


def draw_windows(ax, windows):
    for wn in windows:
        cs = wn["geometry"]["coordinates"]
        ax.plot([p[0] for p in cs], [p[1] for p in cs],
                color=WINDOW_COLOR, lw=2.2, solid_capstyle="butt", zorder=5)


def draw_doors(ax, doors):
    """门：先画白色圆盘"切开"墙线形成门洞，再画类型标记。

    GeoJSON 中门为 Point + width_m（与 v7 一致，无朝向信息），
    平开门用蓝色圆环、防火门用红色方块区分。
    """
    for dr in doors:
        x, y = dr["geometry"]["coordinates"]
        p = dr["properties"]
        w = max(0.6, float(p.get("width_m") or 0.9))
        # 门洞开口（遮罩墙线）
        ax.add_patch(Circle((x, y), w * 0.55, facecolor="white",
                            edgecolor="none", zorder=6))
        if p.get("doorType") == "fire":
            s = w * 0.42
            ax.add_patch(Rectangle((x - s / 2, y - s / 2), s, s,
                                   facecolor="white", edgecolor=DOOR_FIRE_COLOR,
                                   lw=1.4, zorder=7))
        else:
            ax.add_patch(Circle((x, y), w * 0.30, facecolor="white",
                                edgecolor=DOOR_SWING_COLOR, lw=1.3, zorder=7))


def draw_columns(ax, columns):
    for c in columns:
        _draw_polygon(ax, c["geometry"], COLUMN_COLOR, COLUMN_COLOR, 0.3,
                      zorder=3)


def draw_stairs_elevators(ax, stairs, elevators):
    for st in stairs:
        _draw_polygon(ax, st["geometry"], "#F2F2F2", STAIR_EDGE, 0.8, zorder=2)
        # 踏步示意线：沿多边形短轴方向等距画线（稀疏浅灰，避免糊成黑块）
        ext = _rings(st["geometry"])[0][0]
        xs = [p[0] for p in ext]
        ys = [p[1] for p in ext]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if (x1 - x0) >= (y1 - y0):
            n = max(3, int((y1 - y0) / 0.45))
            for i in range(1, n):
                yy = y0 + (y1 - y0) * i / n
                ax.plot([x0, x1], [yy, yy], color="#C9C9C9", lw=0.45, zorder=2)
        else:
            n = max(3, int((x1 - x0) / 0.45))
            for i in range(1, n):
                xx = x0 + (x1 - x0) * i / n
                ax.plot([xx, xx], [y0, y1], color="#C9C9C9", lw=0.45, zorder=2)
        c = st["properties"].get("centroid")
        lab = st["properties"].get("label", "楼梯")
        if c:
            ax.text(c[0], c[1], lab, ha="center", va="center", fontsize=6.5,
                    color="#555555", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.1", fc="white",
                              ec="none", alpha=0.8))
    for ev in elevators:
        _draw_polygon(ax, ev["geometry"], "#EAF4FF", "#4A90D9", 1.0, zorder=2)
        ext = _rings(ev["geometry"])[0][0]
        xs = [p[0] for p in ext]
        ys = [p[1] for p in ext]
        # 电梯轿厢对角线符号
        ax.plot([min(xs), max(xs)], [min(ys), max(ys)], color="#4A90D9",
                lw=0.7, zorder=3)
        ax.plot([min(xs), max(xs)], [max(ys), min(ys)], color="#4A90D9",
                lw=0.7, zorder=3)
        c = ev["properties"].get("centroid")
        lab = ev["properties"].get("label", "电梯")
        if c:
            ax.text(c[0], c[1], lab, ha="center", va="center", fontsize=6.5,
                    color="#2C6EA8", zorder=8,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              ec="none", alpha=0.75))


def draw_topology(ax, topology):
    """导航拓扑叠加：边为绿色细线，节点按类型着色"""
    for e in topology.get("edges", []):
        geo = e.get("geometry")
        if geo and geo.get("type") == "LineString":
            cs = geo["coordinates"]
            ax.plot([p[0] for p in cs], [p[1] for p in cs],
                    color="#27AE60", lw=0.7, alpha=0.6, zorder=9)
    for n in topology.get("nodes", []):
        x, y = n["coordinates"]
        color = NODE_COLORS.get(n.get("type"), "#7F8C8D")
        ax.plot(x, y, "o", ms=3.5, color=color, zorder=10,
                markeredgecolor="white", markeredgewidth=0.4)


# ---------------------------------------------------------------- 装饰

def draw_decorations(ax, bounds, title, legend_x):
    """指北针、比例尺、标题、图例（图例放在右侧预留带内）"""
    minx, maxx, miny, maxy = bounds
    w = maxx - minx

    ax.set_title(title, fontsize=15, pad=12)

    # 指北针（右上）
    nx, ny = maxx - w * 0.04, maxy - (maxy - miny) * 0.02
    ax.add_patch(FancyArrow(nx, ny - 2.2, 0, 1.8, width=0.12,
                            head_width=0.55, head_length=0.55,
                            fc="#333333", ec="#333333", zorder=12))
    ax.text(nx, ny + 0.1, "N", ha="center", va="bottom", fontsize=10,
            fontweight="bold", color="#333333", zorder=12)

    # 比例尺（左下），取 5/10/20/50 中不超过图宽 20% 的最大值
    sb = max(v for v in (5, 10, 20, 50) if v <= w * 0.2)
    sx, sy = minx + w * 0.03, miny + (maxy - miny) * 0.03
    ax.plot([sx, sx + sb], [sy, sy], color="#333333", lw=2.4, zorder=12,
            solid_capstyle="butt")
    for xx in (sx, sx + sb):
        ax.plot([xx, xx], [sy - 0.3, sy + 0.3], color="#333333", lw=1.2,
                zorder=12)
    ax.text(sx + sb / 2, sy + 0.5, f"{sb} m", ha="center", va="bottom",
            fontsize=8, color="#333333", zorder=12)

    # 图例（右下）
    handles = [
        Line2D([0], [0], color=WALL_COLOR, lw=1.6, label="墙体"),
        Line2D([0], [0], color=WINDOW_COLOR, lw=2.2, label="窗"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="white",
               markeredgecolor=DOOR_SWING_COLOR, markeredgewidth=1.4,
               markersize=8, label="平开门"),
        Line2D([0], [0], marker="s", color="none", markerfacecolor="white",
               markeredgecolor=DOOR_FIRE_COLOR, markeredgewidth=1.4,
               markersize=8, label="防火门"),
        Patch(facecolor=ROOM_COLORS["classroom"], edgecolor="#999999",
              label="教室"),
        Patch(facecolor=ROOM_COLORS["office"], edgecolor="#999999",
              label="办公/功能房间"),
        Patch(facecolor=ROOM_COLORS["corridor"], edgecolor="#999999",
              label="走道"),
        Patch(facecolor=COLUMN_COLOR, edgecolor=COLUMN_COLOR, label="柱"),
    ]
    ax.legend(handles=handles, loc="lower left",
              bbox_to_anchor=(legend_x, miny), bbox_transform=ax.transData,
              fontsize=7.5, framealpha=0.9, edgecolor="#CCCCCC",
              borderpad=0.7, labelspacing=0.45)


# ---------------------------------------------------------------- 主流程

def render_floor(floor_id, floor, venue_name, out_dir, with_topology):
    g = floor["geometry"]
    bounds = _collect_bounds(floor)
    minx, maxx, miny, maxy = bounds
    w, h = maxx - minx, maxy - miny
    pad = max(w, h) * 0.04
    legend_band = max(16.0, w * 0.16)  # 右侧图例预留带（数据单位=米）
    ax_w = w + 2 * pad + legend_band
    ax_h = h + 2 * pad

    # 图纸尺寸：长边约 16 英寸，dpi 150
    scale = 16.0 / max(ax_w, ax_h)
    fig_w = ax_w * scale
    # data units per point：用于把字号(pt)约束换算到数据坐标
    dupp = ((maxx + pad + legend_band) - (minx - pad)) / fig_w / 72.0

    fig, ax = plt.subplots(figsize=(fig_w, ax_h * scale))
    ax.set_aspect("equal")
    ax.set_xlim(minx - pad, maxx + pad + legend_band)
    ax.set_ylim(miny - pad, maxy + pad)
    ax.axis("off")

    draw_rooms(ax, g.get("rooms", []), dupp=dupp)
    draw_columns(ax, g.get("columns", []))
    draw_stairs_elevators(ax, g.get("stairs", []), g.get("elevators", []))
    draw_walls(ax, g.get("walls", []))
    draw_windows(ax, g.get("windowSegments", []))
    draw_doors(ax, g.get("doors", []))

    title = f"{venue_name} · {floor_id}F 平面渲染图"
    draw_decorations(ax, bounds, title, legend_x=maxx + pad * 0.8)

    out = os.path.join(out_dir, f"map_render_f{floor_id}.png")
    fig.savefig(out, dpi=150, bbox_inches="tight",
                facecolor="white")
    print(f"[F{floor_id}] 平面图 -> {out}")
    plt.close(fig)

    if with_topology:
        fig2, ax2 = plt.subplots(figsize=(fig_w, ax_h * scale))
        ax2.set_aspect("equal")
        ax2.set_xlim(minx - pad, maxx + pad + legend_band)
        ax2.set_ylim(miny - pad, maxy + pad)
        ax2.axis("off")
        draw_rooms(ax2, g.get("rooms", []), dupp=dupp)
        draw_walls(ax2, g.get("walls", []))
        draw_doors(ax2, g.get("doors", []))
        draw_topology(ax2, floor.get("topology", {}))
        ax2.set_title(f"{venue_name} · {floor_id}F 导航拓扑", fontsize=15,
                      pad=12)
        out2 = os.path.join(out_dir, f"map_render_f{floor_id}_topology.png")
        fig2.savefig(out2, dpi=150, bbox_inches="tight", facecolor="white")
        print(f"[F{floor_id}] 拓扑图 -> {out2}")
        plt.close(fig2)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    with_topology = "--topology" in sys.argv
    path = args[0] if args else DEFAULT_GEOJSON
    out_dir = os.path.dirname(os.path.abspath(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    venue = data.get("venueName", "楼层地图")

    floors = data["floors"]
    items = floors.items() if isinstance(floors, dict) else enumerate(floors, 1)
    for fid, floor in sorted(items, key=lambda kv: int(kv[0])):
        render_floor(str(fid), floor, venue, out_dir, with_topology)
    print("done.")


if __name__ == "__main__":
    main()
