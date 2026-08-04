import fitz, json, math, sys
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

def seg_len(a,b): return math.hypot(a[0]-b[0], a[1]-b[1])
def near_any(p, segs, tol):
    return min(P.point_to_seg_dist(p, s1, s2)[0] for s1,s2 in segs) < tol

gj = json.load(open(P.OUT_GEOJSON, encoding='utf-8'))
gen_fire={}
for fno in ('1','2'):
    c=[]
    for d in gj['floors'][fno]['geometry']['doors']:
        if d['properties']['doorType']!='fire': continue
        xm,ym=d['geometry']['coordinates']
        c.append((xm/P.SCALE+P.ORIGIN_X, P.ORIGIN_Y-ym/P.SCALE))
    gen_fire[fno]=c

for path,fno in [(P.PDF_F1,'1'),(P.PDF_F2,'2')]:
    doc=fitz.open(path); page=doc[0]
    on=P.get_default_on_layers(doc)
    active=[l for l in {P.LAYER_WALL,P.LAYER_WINDOW,P.LAYER_DOOR_FIRE,P.LAYER_STAIR,P.LAYER_ELEVATOR,*P.LAYER_COLUMNS,*P.LAYERS_STRUCT,*P.LAYERS_FURNITURE} if l in on]
    ai=P.extract_layer_items(page,set(active))
    segs=[]
    for ln in P.LAYERS_STRUCT:
        li=ai.get(ln)
        if li: segs.extend(P.wall_segments(li))
    segs,_=P.merge_collinear(segs,record_gaps=True)
    fire=ai.get('DOOR_FIRE',{'quads':[]})['quads']
    print(f'\n===== F{fno}: {len(fire)} DOOR_FIRE quads =====')
    for i,q in enumerate(fire):
        xs=[p[0] for p in q]; ys=[p[1] for p in q]
        w=max(xs)-min(xs); h=max(ys)-min(ys)
        cx=(min(xs)+max(xs))/2; cy=(min(ys)+max(ys))/2
        # proximity to generated fire door
        gd = min(math.hypot(cx-g[0],cy-g[1]) for g in gen_fire[fno])
        # does it span a wall gap? endpoints near wall, center far
        corners=[(min(xs),min(ys)),(max(xs),min(ys)),(max(xs),max(ys)),(min(xs),max(ys))]
        ends_near = sum(1 for c in corners if near_any(c,segs,6.0))
        center_far = not near_any((cx,cy),segs,6.0)
        kind='GAP-SPANNING' if (ends_near>=2 and center_far) else 'symbol-like'
        print(f'  q{i}: w={w:5.1f} h={h:5.1f} aspect={max(w,h)/max(min(w,h),0.01):5.1f} center_far={center_far} endsNearWall={ends_near} nearestFireDoor={gd:6.1f}pt -> {kind}')
    doc.close()
