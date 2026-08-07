import json
from shapely.geometry import shape, Point, LineString
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.patches import Circle
from matplotlib.patches import Polygon as MplPolygon

d = json.load(open('result/school_building_01_map_v9.geojson'))
f1 = d['floors']['1']

fig, ax = plt.subplots(figsize=(14, 14))

# plot walkable regions (green)
for f in f1['walkable_regions']['features']:
    poly = shape(f['geometry'])
    if poly.is_empty:
        continue
    for p in (poly.geoms if poly.geom_type == 'MultiPolygon' else [poly]):
        x, y = p.exterior.xy
        ax.fill(x, y, color='#A5D6A7', alpha=0.5, zorder=1)

# plot rooms
for r in f1['geometry']['rooms']:
    p = r['properties']
    poly = shape(r['geometry'])
    rt = p.get('roomType', 'room')
    color = {'classroom': '#FFF9C4', 'corridor': '#E3F2FD', 'lobby': '#FFF3E0',
             'staircase': '#FFCDD2', 'elevator_hall': '#F8BBD0',
             'activity': '#F8BBD0', 'atrium': '#F3E5F5'}.get(rt, '#FAFAFA')
    x, y = poly.exterior.xy
    ax.fill(x, y, color=color, alpha=0.3, zorder=2)
    ax.plot(x, y, color='#555', linewidth=0.8, zorder=3)
    label = p.get('label') or p.get('roomId') or ''
    if label in ('合班教室', '走道') or p.get('roomId') in ('F1-RM-0050', 'F1-CR-0048'):
        cx, cy = poly.centroid.x, poly.centroid.y
        ax.text(cx, cy, label or p.get('roomId', ''), fontsize=9, ha='center', zorder=10)

# plot walls
for w in f1['geometry']['walls']:
    c = w['geometry']['coordinates']
    ax.plot([c[0][0], c[1][0]], [c[0][1], c[1][1]], color='black', linewidth=0.6, zorder=4)

# plot skeleton
for feat in f1['skeleton']['features']:
    g = feat.get('geometry')
    if g and g.get('type') == 'LineString':
        coords = g['coordinates']
        ax.plot([p[0] for p in coords], [p[1] for p in coords], color='#00ACC1', linewidth=1.2, zorder=5)

# plot fingerprint points
fp_path = 'result/fingerprint_grid.json'
fp_data = []
if __import__('os').path.exists(fp_path):
    fp_data = json.load(open(fp_path)).get('floors', {}).get('1', {}).get('points', [])
if fp_data:
    pts_normal = [(p['coordinates'][0], p['coordinates'][1]) for p in fp_data if p.get('priority') != 'safe']
    pts_safe = [(p['coordinates'][0], p['coordinates'][1]) for p in fp_data if p.get('priority') == 'safe']
    if pts_normal:
        ax.scatter(*zip(*pts_normal), c='#42A5F5', s=12, zorder=6, alpha=0.85)
    if pts_safe:
        ax.scatter(*zip(*pts_safe), c='#FF7043', s=18, zorder=6, alpha=0.9)

# red circle approx at corrected location from screenshot
red = Circle((-45.5, -16.5), 3, fill=False, edgecolor='red', linewidth=3, zorder=11)
ax.add_patch(red)

# also show the corrected center as a small red cross
ax.plot(-45.5, -16.5, marker='+', color='red', markersize=12, markeredgewidth=2, zorder=12)

ax.set_xlim(-49, -37)
ax.set_ylim(-24, -12)
ax.set_aspect('equal')
ax.set_title('F1 红圈区域现状（绿=walkable，黄=classroom，蓝=corridor，青=骨架，蓝点=指纹，红点=安全点，红圈=用户标注）')
plt.tight_layout()
plt.savefig('debug/red_circle_current_state.png', dpi=180)
print('saved debug/red_circle_current_state.png')
