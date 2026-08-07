import json, math
with open('result/fingerprint_grid.json') as f:
    d = json.load(f)
pts = d['floors']['1']['points']
print(f"Total F1 fingerprint points: {len(pts)}")
center = (-45.5, -16.5)
for r in [3, 5, 8, 10]:
    n = sum(1 for p in pts if math.dist((p['coordinates'][0], p['coordinates'][1]), center) <= r)
    print(f"Within {r}m of {center}: {n}")
print()
print("Closest 20 points:")
pts_sorted = sorted(pts, key=lambda p: math.dist((p['coordinates'][0], p['coordinates'][1]), center))[:20]
for p in pts_sorted:
    d = math.dist((p['coordinates'][0], p['coordinates'][1]), center)
    print(f"  {p['id']} {p.get('priority','normal')} ({p['coordinates'][0]:.2f},{p['coordinates'][1]:.2f}) dist={d:.2f}m source={p.get('source')} type={p.get('roomType')}")
