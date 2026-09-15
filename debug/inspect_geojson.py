# -*- coding: utf-8 -*-
"""临时巡检（第五轮）：门 doorType 分布 + 跨层边摘要。"""
import json
import collections

P = 'E:/code/pathai/result/school_building_01_map_v9.geojson'
OUT = 'E:/code/pathai/debug/_inspect_out5.txt'

d = json.load(open(P, encoding='utf-8'))
lines = []

for fk in ['1', '2']:
    doors = d['floors'][fk]['geometry']['doors']
    lines.append('F%s doors by doorType: %s' % (fk, dict(collections.Counter(
        x['properties'].get('doorType') for x in doors).most_common())))
    lines.append('F%s doors by sourceLayer: %s' % (fk, dict(collections.Counter(
        x['properties'].get('sourceLayer') for x in doors).most_common())))
    w = collections.Counter(round(float(x['properties'].get('width_m') or 0), 2) for x in doors)
    lines.append('F%s door width_m dist: %s' % (fk, dict(w.most_common(8))))
    lines.append('')

lines.append('crossFloorEdges: %d' % len(d['crossFloorEdges']))
lines.append('  by type: %s' % dict(collections.Counter(
    e.get('type') for e in d['crossFloorEdges']).most_common()))
lines.append('  sample: %s' % json.dumps(d['crossFloorEdges'][0], ensure_ascii=False)[:400])
lines.append('')
lines.append('routeExtras keys: %s' % list(d['routeExtras'].keys()))
for k, v in d['routeExtras'].items():
    lines.append('  %s : %s' % (k, (len(v) if isinstance(v, (list, dict)) else v)))

open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
print('written', OUT)
