import fitz, json, math, sys
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

def seg_len(a,b): return math.hypot(a[0]-b[0], a[1]-b[1])
def dist_to_segs(p, segs, tol):
    return min(P.point_to_seg_dist(p, s1, s2)[0] for s1,s2 in segs) < tol

gj = json.load(open(P.OUT_GEOJSON, encoding='utf-8'))
gen_fire = {}  # fno -> list of (x,y) pt centers (already in PDF pt? geojson is meters; convert back)
# geojson coords are meters: xm = (xpt-OX)*S ; invert: xpt = xm/S + OX ; ypt = OY - ym/S
for fno in ('1','2'):
    centers = []
    for d in gj['floors'][fno]['geometry']['doors']:
        if d['properties']['doorType'] != 'fire': continue
        xm, ym = d['geometry']['coordinates']
        xpt = xm / P.SCALE + P.ORIGIN_X
        ypt = P.ORIGIN_Y - ym / P.SCALE
        centers.append((xpt, ypt))
    gen_fire[fno] = centers

for path, fno in [(P.PDF_F1,'1'), (P.PDF_F2,'2')]:
    doc = fitz.open(path); page = doc[0]
    on = P.get_default_on_layers(doc)
    active = [l for l in
              {P.LAYER_WALL,P.LAYER_WINDOW,P.LAYER_DOOR_FIRE,P.LAYER_STAIR,P.LAYER_ELEVATOR,
               *P.LAYER_COLUMNS,*P.LAYERS_STRUCT,*P.LAYERS_FURNITURE} if l in on]
    ai = P.extract_layer_items(page, set(active))
    struct_segs=[]; 
    for ln in P.LAYERS_STRUCT:
        li=ai.get(ln)
        if li: struct_segs.extend(P.wall_segments(li))
    struct_segs,_=P.merge_collinear(struct_segs, record_gaps=True)
    furn=[]; 
    for ln in P.LAYERS_FURNITURE:
        li=ai.get(ln)
        if li: furn.extend(P.wall_segments(li))
    furn=P.merge_collinear(furn)
    all_segs=struct_segs+furn
    fire=ai.get('DOOR_FIRE',{'lines':[],'quads':[],'curves':[]})

    # door-leaf candidate lines: endpoints near wall (<=6pt) AND midpoint far from wall (>6pt) => bridges a gap
    # AND length in plausible door range 8..45 pt (~0.42..2.4m)
    leaf=0; leaf_by_len={}
    leaf_lines=[]
    for a,b in fire['lines']:
        L=seg_len(a,b)
        if L<8 or L>45: continue
        if dist_to_segs(a,all_segs,6.0) and dist_to_segs(b,all_segs,6.0):
            mid=((a[0]+b[0])/2,(a[1]+b[1])/2)
            if not dist_to_segs(mid,all_segs,6.0):
                leaf+=1; leaf_lines.append(((a[0]+b[0])/2,(a[1]+b[1])/2))
    print(f'\nF{fno}: DOOR_FIRE lines={len(fire["lines"])}, door-leaf candidates(spans gap, 8-45pt)={leaf}')

    # compare to generated fire doors
    gen=gen_fire[fno]
    print(f'F{fno}: generated fire doors={len(gen)}')
    # how many generated fire doors are NOT near any leaf line (i.e., came from arcs only)? and vice versa
    # nearest leaf line to each generated fire door
    missing_from_gen=0  # leaf lines with no nearby generated fire door
    for c in leaf_lines:
        near=any(math.hypot(c[0]-g[0],c[1]-g[1])<10.0 for g in gen)
        if not near: missing_from_gen+=1
    print(f'F{fno}: door-leaf candidates NOT covered by a generated fire door (<10pt): {missing_from_gen}')
    # quads as fire doors
    if fire['quads']:
        qspans=[max(max(p[0] for p in q)-min(p[0] for p in q), max(p[1] for p in q)-min(p[1] for p in q)) for q in fire['quads']]
        print(f'F{fno}: DOOR_FIRE quads={len(fire["quads"])}, spans(pt)={[round(s,1) for s in qspans]}')
    doc.close()
