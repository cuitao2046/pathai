import fitz, json, math, sys
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

gj = json.load(open(P.OUT_GEOJSON, encoding='utf-8'))
for path, fno in [(P.PDF_F1,'1'),(P.PDF_F2,'2')]:
    doc=fitz.open(path); page=doc[0]
    on=P.get_default_on_layers(doc)
    active=[l for l in {P.LAYER_WALL,P.LAYER_WINDOW,P.LAYER_DOOR_FIRE,P.LAYER_STAIR,P.LAYER_ELEVATOR,*P.LAYER_COLUMNS,*P.LAYERS_STRUCT,*P.LAYERS_FURNITURE} if l in on]
    ai=P.extract_layer_items(page,set(active))
    segs=[]; 
    for ln in P.LAYERS_STRUCT:
        li=ai.get(ln)
        if li: segs.extend(P.wall_segments(li))
    segs,gaps=P.merge_collinear(segs, record_gaps=True)
    furn=[]; 
    for ln in P.LAYERS_FURNITURE:
        li=ai.get(ln)
        if li: furn.extend(P.wall_segments(li))
    furn=P.merge_collinear(furn)
    allsegs=segs+furn
    # wall gaps as points (midpoint of each gap seg)
    gap_pts=[g["center"] for g in gaps]
    def dmin(p, lst):
        return min(math.hypot(p[0]-q[0],p[1]-q[1]) for q in lst) if lst else 999
    fires=[d for d in gj['floors'][fno]['geometry']['doors'] if d['properties']['doorType']=='fire']
    print(f'\n===== F{fno}: {len(fires)} fire doors =====')
    nearwall=0; neargap=0; farboth=0
    for d in fires:
        xm,ym=d['geometry']['coordinates']
        pt=(xm/P.SCALE+P.ORIGIN_X, P.ORIGIN_Y-ym/P.SCALE)
        dw=dmin(pt,[((a[0]+b[0])/2,(a[1]+b[1])/2) for a,b in allsegs])
        dg=dmin(pt, gap_pts)
        rooms=d['properties'].get('rooms',[])
        tag=''
        if dw<12: nearwall+=1
        if dg<12: neargap+=1
        if dw>=12 and dg>=12: farboth+=1; tag='  <-- FLOATING (no wall, no gap!)'
        print(f"  fire {d['id']}: distToWall={dw:5.1f}pt distToGap={dg:5.1f}pt rooms={rooms}{tag}")
    print(f'  nearWall(<12pt)={nearwall}  nearGap(<12pt)={neargap}  FLOATING={farboth}')
    doc.close()
