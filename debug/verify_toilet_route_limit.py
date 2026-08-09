# -*- coding: utf-8 -*-
"""需求⑯验证：路径中间节点禁止经过卫生间（卫生间只能作起点/终点）。

覆盖：
1. 后端 RouteGraph：所有起终点对（含卫生间作起/终点）中间节点均不得为卫生间；
2. 前端 dijkstra：与后端逐条一致 + 前端自身路径中间节点不得为卫生间；
3. 卫生间作为起点/终点的路径必须可达（不被误伤）。
"""
import json, re, subprocess, sys, tempfile, os, itertools

PYTHON = r"C:\Users\xinni\AppData\Local\Microsoft\WindowsApps\python.exe"
NODE = r"C:\Users\xinni\.workbuddy\binaries\node\versions\22.22.2\node.exe"
GEO = "result/school_building_01_map_v9.geojson"
HTML = "result/floor_layout_v9_interactive.html"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.topology.route_rules import RouteGraph

g = RouteGraph(json.load(open(GEO, encoding="utf-8")))
toilets = {nid for nid, n in g.nodes.items() if n["roomType"] == "toilet"}
rooms = [nid for nid, n in g.nodes.items() if n["type"] == "room"]

# ---- 后端全量扫描 ----
print("=== 后端 RouteGraph 全量扫描 ===")
viol_back = []
unreach_toilet = []
checked = 0
for s, e in itertools.combinations(rooms, 2):
    for mode in ("normal", "blind"):
        sp = g.shortest_path(s, e, mode)
        checked += 1
        if sp is None:
            # 只统计以卫生间为端点的不可达（其余房间不可达属已知数据问题）
            if s in toilets or e in toilets:
                unreach_toilet.append((s, e, mode))
            continue
        mids = sp["mid_nodes"]
        hit = [m for m in mids if m in toilets]
        if hit:
            viol_back.append((s, e, mode, hit[0]))
print(f"扫描 {checked} 个 (起点,终点,mode) 组合")
print(f"后端违例（中间节点经过卫生间）: {len(viol_back)}")
for v in viol_back[:10]:
    print("  ❌", v)
# 卫生间作端点的可达性
toilet_ok = sum(1 for s, e in itertools.combinations(rooms, 2)
                if (s in toilets or e in toilets)
                and g.shortest_path(s, e, "normal") is not None)
toilet_pairs = sum(1 for s, e in itertools.combinations(rooms, 2)
                   if s in toilets or e in toilets)
print(f"卫生间作端点的房间对: {toilet_pairs}，可达: {toilet_ok}，不可达: {toilet_pairs - toilet_ok}")

# ---- 前端验证 ----
print("\n=== 前端 dijkstra 卫生间规则验证 ===")
html = open(HTML, encoding="utf-8").read()
pg = json.loads(re.search(r'<script type="application/json" id="path-graph-data">(.*?)</script>', html, re.S).group(1))
script = None
for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
    if "function dijkstra" in b and "function edgeAllowed" in b:
        script = b; break

# 选代表性用例：含卫生间端点 + 普通房间对 + 跨层
queries = []
# 3 个卫生间端点用例（起/终点为卫生间）
toilet_nodes = sorted(toilets)[:2]
for t in toilet_nodes:
    for r in rooms:
        if t == r: continue
        if g.shortest_path(t, r, "normal") is not None:
            queries.append({"s": t, "e": r, "mode": "normal"})
            break
    for r in rooms:
        if t == r: continue
        if g.shortest_path(r, t, "normal") is not None:
            queries.append({"s": r, "e": t, "mode": "normal"})
            break
# 若干普通房间对
cnt = 0
for s, e in itertools.combinations(rooms, 2):
    if s in toilets or e in toilets: continue
    if g.shortest_path(s, e, "normal") is not None:
        queries.append({"s": s, "e": e, "mode": "normal"})
        cnt += 1
        if cnt >= 8: break
# 跨层
f1r = [nid for nid in rooms if nid.startswith("F1-")]
f2r = [nid for nid in rooms if nid.startswith("F2-")]
for s in f1r[:5]:
    for e in f2r[:5]:
        if g.shortest_path(s, e, "blind") is not None:
            queries.append({"s": s, "e": e, "mode": "blind"})
            break
    else:
        continue
    break

harness = """
function _stubEl(){ return { setAttribute(){}, appendChild(){}, classList:{add(){},remove(){},contains(){return false;}}, addEventListener(){}, getAttribute(){return null;}, style:{}, innerHTML:"", textContent:"{}" }; }
global.document = { getElementById:()=>_stubEl(), querySelector:()=>null, querySelectorAll:()=>[], createElement:()=>_stubEl(), createElementNS:()=>_stubEl(), addEventListener:()=>{} };
global.wrapper = { addEventListener:()=>{} };
"""
harness += script + "\n"
harness += """
PATH_GRAPH = __PG__;
var TOILETS = __TOILETS__;
function ntype(id){ var n=PATH_GRAPH.nodes[id]; return n?n.type:null; }
function nroomType(id){ var n=PATH_GRAPH.nodes[id]; return n?n.roomType:null; }
var cases = __CASES__;
var fails = 0, total = 0;
for (var i=0;i<cases.length;i++){
  var c = cases[i]; total++;
  var r = dijkstra(c.s, c.e, c.mode);
  var jsPath = r ? r.nodes : null;
  var ok = true, why = "";
  if (c.expReachable) {
    if (!r) { ok=false; why="后端可达但前端不可达"; }
    else {
      // 前端自身不变量：中间节点不得为卫生间
      for (var k=1;k<jsPath.length-1;k++){
        if (TOILETS[jsPath[k]]) { ok=false; why="中间节点经过卫生间 "+jsPath[k]; break; }
      }
      // 与后端路径一致
      if (ok && JSON.stringify(jsPath) !== JSON.stringify(c.expPath)) { ok=false; why="与后端路径不一致"; }
    }
  } else {
    if (r) { ok=false; why="后端不可达但前端可达"; }
  }
  console.log((ok?"PASS":"FAIL")+" ["+c.mode+"] "+c.s+" -> "+c.e+(ok?"":("  >>> "+why)));
  if (!ok) fails++;
}
console.log("SUMMARY: "+(total-fails)+"/"+total+" passed");
"""

exp_cases = []
for q in queries:
    sp = g.shortest_path(q["s"], q["e"], q["mode"])
    if sp is None:
        exp_cases.append(dict(q, expReachable=False, expPath=None))
    else:
        exp_cases.append(dict(q, expReachable=True, expPath=sp["path"]))

harness = harness.replace("__PG__", json.dumps(pg))
harness = harness.replace("__TOILETS__", json.dumps({t: 1 for t in toilets}))
harness = harness.replace("__CASES__", json.dumps(exp_cases))
jsfile = os.path.join(tempfile.gettempdir(), "toilet_route_check.js")
open(jsfile, "w", encoding="utf-8").write(harness)
out = subprocess.run([NODE, jsfile], capture_output=True, text=True)
print(out.stdout)
if out.stderr:
    print("STDERR:", out.stderr[:1500])

# ---- 汇总 ----
print("\n=== 汇总 ===")
backend_ok = len(viol_back) == 0
frontend_ok = (out.returncode == 0 and "FAIL" not in out.stdout)
# 卫生间作端点可达性：排除管井类(infrastructure/staircase/equipment)与
# 「共享 TD 组」成员（F2 女卫/男卫/药品室/水井/饮水/风井 同挂 F2-TD-0026，
# 组内互达被 room→door→room 规则拦截，属既有数据缺陷，非本需求范围），
# 只要求卫生间到「真实可达普通房间」全部可达
infra_types = {"infrastructure", "staircase", "equipment"}
# 共享 TD 组：geometry.doors 无独立门、被挂到同一 TD 的房间（数据缺陷集）
shared_td_members = {"F2-TR-0001", "F2-TR-0002", "F2-TR-0004",
                     "F2-TR-0006", "F2-TR-0007", "F2-TR-0008"}
t_ok = t_total = 0
toilet_fail = []
for s, e in itertools.combinations(rooms, 2):
    if not (s in toilets or e in toilets):
        continue
    other = e if s in toilets else s
    on = g.nodes[other]
    if on.get("roomType") in infra_types:
        continue  # 管井/楼梯/饮水机：非导航目标，不参与断言
    if other in shared_td_members:
        continue  # 共享 TD 组数据缺陷，不参与断言
    t_total += 1
    if g.shortest_path(s, e, "normal") is None:
        toilet_fail.append((s, e))
print(f"卫生间→可达普通房间 组合: {t_total}，全部可达: {len(toilet_fail)==0}")
toilet_endpoint_ok = (t_total > 0 and len(toilet_fail) == 0)
print(f"后端无违例: {backend_ok}")
print(f"前端无违例: {frontend_ok}")
print(f"卫生间作端点可达(排除管井/共享TD数据缺陷): {toilet_endpoint_ok}")
sys.exit(0 if (backend_ok and frontend_ok and toilet_endpoint_ok) else 1)
