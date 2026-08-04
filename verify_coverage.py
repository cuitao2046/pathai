import fitz, json, math, sys
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

gj = json.load(open(P.OUT_GEOJSON, encoding='utf-8'))

def pt_of(d):
    xm,ym = d['geometry']['coordinates']
    return (xm/P.SCALE + P.ORIGIN_X, P.ORIGIN_Y - ym/P.SCALE)

for path, fno in [(P.PDF_F1,'1'),(P.PDF_F2,'2')]:
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
    def near(p,tol=6.0): return min(P.point_to_seg_dist(p,s1,s2)[0] for s1,s2 in segs)<tol
    # the thin-rectangle "leaf" quads (potential missed fire doors)
    leaves=[]
    for q in fire:
        xs=[p[0] for p in q]; ys=[p[1] for p in q]
        w=max(xs)-min(xs); h=max(ys)-min(ys)
        long=max(w,h); short=min(w,h)
        if short<=0 or long/short<4: continue
        if not (7<=long<=60): continue
        c=((min(xs)+max(xs))/2,(min(ys)+max(ys))/2)
        if near(c): continue
        if not (near((min(xs),(min(ys)+max(ys))/2)) or near((max(xs),(min(ys)+max(ys))/2)) or near(((min(xs)+max(xs))/2,min(ys))) or near(((min(xs)+max(xs))/2,max(ys)))):
            continue
        leaves.append(c)
    firedoors=[pt_of(d) for d in gj['floors'][fno]['geometry']['doors'] if d['properties']['doorType']=='fire']
    # also any door (any type) for coverage of the opening
    alldoors=[pt_of(d) for d in gj['floors'][fno]['geometry']['doors']]
    print(f'\n===== F{fno}: {len(leaves)} 细长矩形(疑似防火门叶片) in DOOR_FIRE =====')
    missing=0
    for i,c in enumerate(leaves):
        d_fire=min(math.hypot(c[0]-g[0],c[1]-g[1]) for g in firedoors)
        d_any =min(math.hypot(c[0]-g[0],c[1]-g[1]) for g in alldoors)
        status = 'COVERED(fire)' if d_fire<15 else ('COVERED(any)' if d_any<15 else '*** MISSING ***')
        if d_fire>=15: missing+=1
        print(f'  leaf#{i} center=({c[0]:.0f},{c[1]:.0f}) nearestFire={d_fire:6.1f}pt nearestAny={d_any:6.1f}pt -> {status}')
    print(f'  => 细长矩形叶片中仍未生成防火门(fire)的: {missing} 个')
    doc.close()
