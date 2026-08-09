# -*- coding: utf-8 -*-
import re, json

HTML = "result/floor_layout_v9_interactive.html"
SEARCH_LAYERS = ['room','corridor','lobby','activity','atrium','lobby_elevator','lobby_stair',
  'door_swing','door_fire','door_opening','topo_node','stairs','elevator','risk','ramp','tactile','material','crossfloor']

html = open(HTML, encoding="utf-8").read().splitlines()
elems = []
for line in html:
    if "data-info='" not in line: continue
    cls = re.search(r'class="([^"]*)"', line)
    if not cls: continue
    c = cls.group(1)
    layer = None
    for L in SEARCH_LAYERS:
        if ("layer_"+L) in c:
            layer = L; break
    if not layer: continue
    di = re.search(r"data-info='(.*?)'", line, re.S)
    if not di: continue
    rid = re.search(r'data-roomid="([^"]*)"', line)
    try:
        raw = json.loads(di.group(1).replace("\\'", "'"))
    except Exception:
        continue
    elems.append((layer, raw, rid.group(1) if rid else ""))

print("可搜索要素总数(按行解析):", len(elems))

def build_text(raw, rid):
    parts = []
    if raw.get("id"): parts.append(raw["id"])
    if raw.get("kind"): parts.append(raw["kind"])
    if raw.get("tip"): parts.append(raw["tip"])
    title = (raw.get("detail") or {}).get("title") or ""
    if title: parts.append(title)
    for r in (raw.get("detail") or {}).get("rows") or []:
        if r and r[1] is not None: parts.append(str(r[1]))
    if rid: parts.append(rid)
    return " ".join(parts)

index = [(layer, raw, rid, build_text(raw, rid)) for layer, raw, rid in elems]

def score(q, text):
    if not q: return -1
    text=(text or "").lower(); q=q.lower().strip()
    if not q: return -1
    p=text.find(q)
    if p>=0: return 1000-p-len(text)*0.05
    ti=0; gaps=0; last=-1; matched=0
    for ch in q:
        found=-1
        for j in range(ti,len(text)):
            if text[j]==ch: found=j; break
        if found<0: return -1
        if last>=0: gaps+=(found-last-1)
        last=found; ti=found+1; matched+=1
    if matched<len(q): return -1
    return 400-gaps-matched

def run(q, top=4):
    res=[]
    for layer, raw, rid, text in index:
        s=score(q, text)
        if s>0: res.append((s, layer, raw.get("id",""), rid, (raw.get("detail") or {}).get("title","")))
    res.sort(key=lambda x:-x[0])
    print(f"\n查询: {q} -> 命中 {len(res)}，前{top}:")
    for s,layer,eid,rid,title in res[:top]:
        print(f"  score={s:.1f} layer={layer} id={eid} roomid={rid} title={title}")

for q in ["音乐教室","F1-RM-0015","F1-TD-0018","F1-D-0018","普通门","楼梯","卫生间","电梯"]:
    run(q)
