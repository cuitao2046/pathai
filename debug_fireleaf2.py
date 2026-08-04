import fitz, sys, math
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

doc = fitz.open(P.PDF_F1); page = doc[0]
on = P.get_default_on_layers(doc)
active = [l for l in
          {P.LAYER_WALL,P.LAYER_WINDOW,P.LAYER_DOOR_FIRE,P.LAYER_STAIR,P.LAYER_ELEVATOR,
           *P.LAYER_COLUMNS,*P.LAYERS_STRUCT,*P.LAYERS_FURNITURE} if l in on]
ai = P.extract_layer_items(page, set(active))
struct=[]; 
for ln in P.LAYERS_STRUCT:
    li=ai.get(ln)
    if li: struct.extend(P.wall_segments(li))
struct,_=P.merge_collinear(struct, record_gaps=True)
furn=[]; 
for ln in P.LAYERS_FURNITURE:
    li=ai.get(ln)
    if li: furn.extend(P.wall_segments(li))
furn=P.merge_collinear(furn)
all_segs=struct+furn
_, all_gaps = P.merge_collinear(all_segs, record_gaps=True)
fire = ai.get('DOOR_FIRE', {'quads':[], 'lines':[]})

def near(p, tol=6.0):
    return min(P.point_to_seg_dist(p, s1, s2)[0] for s1, s2 in all_segs) < tol
def near_gap(p, tol=12.0):
    return min(P.point_to_seg_dist(p, g["left"], g["right"])[0] for g in all_gaps) < tol

for i,q in enumerate(fire['quads']):
    xs=[p[0] for p in q]; ys=[p[1] for p in q]
    w=max(xs)-min(xs); h=max(ys)-min(ys)
    long=max(w,h); short=min(w,h)
    cx=(min(xs)+max(xs))/2; cy=(min(ys)+max(ys))/2
    center=(cx,cy)
    cfar = not near(center)
    if w>=h:
        a=(min(xs),(min(ys)+max(ys))/2); b=(max(xs),(min(ys)+max(ys))/2)
    else:
        a=((min(xs)+max(xs))/2,min(ys)); b=((min(xs)+max(xs))/2,max(ys))
    ends = near(a) or near(b)
    ng = near_gap(center)
    # gap distance
    gd = min(P.point_to_seg_dist(center, g["left"], g["right"])[0] for g in all_gaps)
    print(f'q{i}: long={long:5.1f} aspect={long/short:5.1f} centerFar={cfar} endsNearWall={ends} nearGap={ng} gapDist={gd:5.1f}')
