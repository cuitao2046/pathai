import fitz, json, sys, math
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

PDFS = [(P.PDF_F1, '1'), (P.PDF_F2, '2')]
for path, fno in PDFS:
    doc = fitz.open(path)
    page = doc[0]
    on = P.get_default_on_layers(doc)
    print(f'\n===== F{fno} =====  DOOR_FIRE in default-on layers: {"DOOR_FIRE" in on}')
    items = P.extract_layer_items(page, {'DOOR_FIRE'})
    fire = items.get('DOOR_FIRE', {'lines':[], 'quads':[], 'curves':[]})
    nl, nq, nc = len(fire['lines']), len(fire['quads']), len(fire['curves'])
    print(f'DOOR_FIRE raw: lines={nl} quads={nq} curves={nc}')

    # replicate near_wall filter exactly as parser does (needs all_segs)
    # build struct+furn segs
    active = [l for l in
              {P.LAYER_WALL, P.LAYER_WINDOW, P.LAYER_DOOR_FIRE, P.LAYER_STAIR,
               P.LAYER_ELEVATOR, *P.LAYER_COLUMNS, *P.LAYERS_STRUCT, *P.LAYERS_FURNITURE}
              if l in on]
    allitems = P.extract_layer_items(page, set(active))
    struct_segs = []
    for ln in P.LAYERS_STRUCT:
        li = allitems.get(ln)
        if li: struct_segs.extend(P.wall_segments(li))
    struct_segs, _ = P.merge_collinear(struct_segs, record_gaps=True)
    furn_segs = []
    for ln in P.LAYERS_FURNITURE:
        li = allitems.get(ln)
        if li: furn_segs.extend(P.wall_segments(li))
    furn_segs = P.merge_collinear(furn_segs)
    all_segs = struct_segs + furn_segs

    def near_wall(bz, tol=6.0):
        p1, p4 = bz[0], bz[3]
        for a,b in all_segs:
            if P.point_to_seg_dist(p1,a,b)[0] < tol: return True
            if P.point_to_seg_dist(p4,a,b)[0] < tol: return True
        return False
    fire_arcs = [bz for bz in fire['curves'] if near_wall(bz)]
    print(f'DOOR_FIRE curves near wall (-> would become fire doors): {len(fire_arcs)}')

    # Examine lines: length distribution + how many lie across a wall gap (door leaf candidate)
    def seg_len(a,b): return math.hypot(a[0]-b[0], a[1]-b[1])
    lens = sorted(seg_len(a,b) for a,b in fire['lines'])
    print(f'DOOR_FIRE lines length pt: min={lens[0]:.1f} max={lens[-1]:.1f} median={lens[len(lens)//2]:.1f}  (count={len(lens)})')
    # count lines whose midpoint is within 6pt of a wall seg (likely door leaf on wall)
    near_lines = 0
    for a,b in fire['lines']:
        mid = ((a[0]+b[0])/2, (a[1]+b[1])/2)
        if any(P.point_to_seg_dist(mid, s1,s2)[0] < 6.0 for s1,s2 in all_segs):
            near_lines += 1
    print(f'DOOR_FIRE lines near a wall seg: {near_lines}/{nl}')
    # quads: area
    if nq:
        areas = []
        for q in fire['quads']:
            xs=[p[0] for p in q]; ys=[p[1] for p in q]
            w=max(xs)-min(xs); h=max(ys)-min(ys)
            areas.append(max(w,h))
        print(f'DOOR_FIRE quads max-span pt: {[round(a,1) for a in areas[:20]]}')
    doc.close()

# GeoJSON fire door counts
gj = json.load(open(P.OUT_GEOJSON, encoding='utf-8'))
print('\n----- GeoJSON fire doors actually generated -----')
for fno in ('1','2'):
    doors = gj['floors'][fno]['geometry']['doors']
    fire = [d for d in doors if d.get('doorType')=='fire']
    print(f'F{fno}: total doors={len(doors)}  fire={len(fire)}')
