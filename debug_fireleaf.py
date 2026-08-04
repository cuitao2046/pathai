import fitz, sys, math
sys.path.insert(0, r'E:\code\pathai\src')
import parse_cad_pdf as P

doc = fitz.open(P.PDF_F1); page = doc[0]
on = P.get_default_on_layers(doc)
active = [l for l in
          {P.LAYER_WALL,P.LAYER_WINDOW,P.LAYER_DOOR_FIRE,P.LAYER_STAIR,P.LAYER_ELEVATOR,
           *P.LAYER_COLUMNS,*P.LAYERS_STRUCT,*P.LAYERS_FURNITURE} if l in on]
ai = P.extract_layer_items(page, set(active))
segs=[]
for ln in P.LAYERS_STRUCT:
    li=ai.get(ln)
    if li: segs.extend(P.wall_segments(li))
segs,_=P.merge_collinear(segs, record_gaps=True)
fire = ai.get('DOOR_FIRE', {'quads':[]})['quads']
print('F1 DOOR_FIRE quads:', len(fire))

def near(p, tol):
    return min(P.point_to_seg_dist(p, s1, s2)[0] for s1,s2 in segs) < tol

for i,q in enumerate(fire):
    xs=[p[0] for p in q]; ys=[p[1] for p in q]
    w=max(xs)-min(xs); h=max(ys)-min(ys)
    long=max(w,h); short=min(w,h)
    cx=(min(xs)+max(xs))/2; cy=(min(ys)+max(ys))/2
    cdist=min(P.point_to_seg_dist((cx,cy),s1,s2)[0] for s1,s2 in segs)
    aspect = long/short if short>0 else 999
    reasons=[]
    if short<=0 or aspect<4: reasons.append('not-thin')
    if long<7 or long>60: reasons.append('len-range')
    if cdist<6.0: reasons.append('center-on-wall')
    if not reasons:
        # check ends
        if w>=h:
            a=(min(xs),(min(ys)+max(ys))/2); b=(max(xs),(min(ys)+max(ys))/2)
        else:
            a=((min(xs)+max(xs))/2,min(ys)); b=((min(xs)+max(ys))/2,max(ys))
        a_on = near(a,6.0); b_on=near(b,6.0)
        if not (a_on and b_on): reasons.append(f'ends-not-on-wall(a={a_on},b={b_on})')
    print(f'q{i}: long={long:5.1f} aspect={aspect:5.1f} centerDist={cdist:5.1f} -> {"LEAF" if not reasons else reasons}')
