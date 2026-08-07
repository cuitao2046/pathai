import json, math, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from shapely.geometry import Point, Polygon, LineString
from shapely.strtree import STRtree

GEO = os.path.join(os.path.dirname(__file__), '..', 'result', 'school_building_01_map_v9.geojson')
with open(GEO, encoding='utf-8') as f:
    g = json.load(f)

floor = g['floors']['1']
center = (-42.0, -17.0)
radius = 4.0
pt = Point(center)
print(f"Verification region: center={center}, radius={radius}m")
print()

walks = []
for feat in floor['walkable_regions']['features']:
    coords = feat['geometry']['coordinates'][0]
    poly = Polygon(coords)
    walks.append((feat.get('id','?'), poly))
print(f"Walkable regions: {len(walks)}")
covered = [name for name,p in walks if p.contains(pt) or p.distance(pt) < 0.01]
print(f"Point inside walkable: {bool(covered)}  ({covered})")
print()

for r in floor['semantic']['rooms']:
    poly = None
    if r.get('coords_m'):
        poly = Polygon(r['coords_m'])
    if poly and (poly.contains(pt) or poly.distance(pt) < 0.01):
        print(f"Point inside room: {r['id']} type={r.get('type')} name={r.get('name')}")
        print(f"  room bounds: {poly.bounds}")

skel = floor['skeleton']
segments = []
for feat in skel['features']:
    c = feat['geometry']['coordinates']
    seg = LineString([c[0], c[1]])
    segments.append(seg)
tree = STRtree(segments)
near = tree.query(pt.buffer(radius))
print(f"Skeleton segments within {radius}m: {len(near)}")
for idx in near[:5]:
    feat = skel['features'][idx]
    print(f"  seg {idx}: {feat.get('id','?')} endpoints {feat['geometry']['coordinates']}")
print()

fp_all = []
for n in floor['topology']['nodes']:
    if n['type'] in {'junction','terminal','doorway','intersection'}:
        fp_all.append(n)

# debug: list all nodes near region
print("All topology nodes within 4m of (-42,-17):")
for n in fp_all:
    d = math.dist((n['coordinates'][0],n['coordinates'][1]), (-42,-17))
    if d <= 4.0:
        print(f"  {n['id']} type={n['type']} room={n.get('roomName','')} {n.get('roomType','')} ({n['coordinates'][0]:.2f},{n['coordinates'][1]:.2f})")
near_fp = [n for n in fp_all if math.dist((n['coordinates'][0],n['coordinates'][1]), center) <= radius]
print(f"Fingerprint/anchor points within {radius}m: {len(near_fp)}")
for n in near_fp[:10]:
    print(f"  {n['id']} {n['type']} {n.get('roomName','')} ({n['coordinates'][0]:.2f},{n['coordinates'][1]:.2f})")

candidates = [(-42,-17), (-41,-16), (-43,-18), (-40,-15), (-44,-18), (-39,-18), (-45,-16), (-45,-17), (-45,-18), (-45,-19), (-46,-17), (-43,-16)]
print()
print("Candidate point coverage:")
print("  x       y       walkable_dist  skeleton_dist  fp_count  room")
for x,y in candidates:
    p = Point(x,y)
    wd = min(p.distance(wpoly) for _,wpoly in walks)
    sd = min(p.distance(seg) for seg in segments) if segments else -1
    fpc = sum(1 for n in fp_all if math.dist((n['coordinates'][0],n['coordinates'][1]), (x,y)) <= 3.0)
    rm = []
    for r in floor['semantic']['rooms']:
        if r.get('coords_m'):
            po = Polygon(r['coords_m'])
            if po.contains(p):
                rm.append(r['id'])
    print(f"  {x:6.1f}  {y:6.1f}  {wd:10.2f}   {sd:10.2f}   {fpc:6d}    {','.join(rm) or 'none'}")
