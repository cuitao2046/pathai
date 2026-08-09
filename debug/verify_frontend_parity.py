# -*- coding: utf-8 -*-
"""Task #17 前端/后端 Dijkstra 对齐验证：用真实 PATH_GRAPH 数据在 Node 中执行
前端 dijkstra，并与 Python 后端 route_rules.RouteGraph 的结果逐条比对。"""
import json, re, subprocess, sys, tempfile, os

PYTHON = r"C:\Users\xinni\AppData\Local\Microsoft\WindowsApps\python.exe"
NODE = r"C:\Users\xinni\.workbuddy\binaries\node\versions\22.22.2\node.exe"
GEO = "result/school_building_01_map_v9.geojson"
HTML = "result/floor_layout_v9_interactive.html"

# 1) 后端期望结果
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.topology.route_rules import RouteGraph
g = RouteGraph(json.load(open(GEO, encoding="utf-8")))

# 选代表性查询
rooms_f1 = [nid for nid, n in g.nodes.items() if n["type"] == "room" and n["floor"] == 1]
rooms_f2 = [nid for nid, n in g.nodes.items() if n["type"] == "room" and n["floor"] == 2]
fac_f1 = [nid for nid, n in g.nodes.items() if n["type"] == "facility" and n["floor"] == 1]

def pick_pair(list_a, list_b, mode="normal", min_len=5):
    """挑一对在后端「真实可达」且路径足够长的节点。

    直接取 rooms[0]/rooms[1] 是陷阱：部分房间（无门/门洞卫生间）在规则约束下
    本就不可达，此时前后端同为 None 也会「通过」，断言退化为空转。
    """
    for a in list_a:
        for b in list_b:
            if a == b:
                continue
            sp = g.shortest_path(a, b, mode)
            if sp and len(sp["path"]) >= min_len:
                return a, b
    return None, None

queries = []
sf_a, sf_b = pick_pair(rooms_f1, rooms_f1, "normal")
if sf_a:
    queries.append((sf_a, sf_b, "normal"))                     # 同层 room->room
    queries.append((sf_a, sf_b, "blind"))                      # 同层盲模式
cf_a, cf_b = pick_pair(rooms_f1, rooms_f2, "blind")
if cf_a:
    queries.append((cf_a, cf_b, "normal"))                     # 跨层 normal
    queries.append((cf_a, cf_b, "blind"))                      # 跨层 blind（必须电梯）
    queries.append((cf_a, cf_b, "wheelchair"))                 # 跨层轮椅
fa, fb = pick_pair(rooms_f1, fac_f1, "normal", min_len=4)
if fa:
    queries.append((fa, fb, "normal"))                         # 同层 room->facility（终点设施允许）
# 负例：已知在规则下不可达的房间对（前端也必须判为不可达）
if len(rooms_f1) >= 2 and g.shortest_path(rooms_f1[0], rooms_f1[1], "normal") is None:
    queries.append((rooms_f1[0], rooms_f1[1], "normal"))

expected = []
for s, e, mode in queries:
    sp = g.shortest_path(s, e, mode)
    if sp is None:
        expected.append({"s": s, "e": e, "mode": mode, "reachable": False})
        continue
    expected.append({
        "s": s, "e": e, "mode": mode, "reachable": True,
        "path": sp["path"], "distance": sp["distance"],
        "note": sp.get("door_fallback") and "door_fallback" or (
            sp.get("wall_fallback") and "wall_fallback" or None),
        "used_elevator": sp["used_elevator"], "used_stair": sp["used_stair"],
    })

# 守卫：若可达用例太少，说明选点退化，断言不再有意义
n_reach = sum(1 for x in expected if x["reachable"])
print(f"用例 {len(expected)} 条（可达 {n_reach} / 不可达 {len(expected)-n_reach}）")
if n_reach < 4:
    print(f"FAIL 守卫：可达用例仅 {n_reach} 条，测试会退化为空转")
    sys.exit(1)

# 2) 抽取前端 PATH_GRAPH 与 dijkstra 脚本
html = open(HTML, encoding="utf-8").read()
pg = json.loads(re.search(r'<script type="application/json" id="path-graph-data">(.*?)</script>', html, re.S).group(1))
script = None
for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
    if "function dijkstra" in b and "function edgeAllowed" in b:
        script = b; break

# 3) 构造 Node 测试
harness = """
// ---- DOM 桩 ----
// textContent 用 "{}"：页面脚本会 JSON.parse(#path-graph-data / #full-geojson-data)，
// 给空串会在 stderr 刷出误导性的 SyntaxError（虽被 catch）。
function _stubEl(){ return { setAttribute(){}, appendChild(){}, classList:{add(){},remove(){},contains(){return false;}}, addEventListener(){}, getAttribute(){return null;}, style:{}, innerHTML:"", textContent:"{}" }; }
global.document = { getElementById:()=>_stubEl(), querySelector:()=>null, querySelectorAll:()=>[], createElement:()=>_stubEl(), createElementNS:()=>_stubEl(), addEventListener:()=>{} };
global.wrapper = { addEventListener:()=>{} };
"""
# 注入脚本（末尾的 installPathClick() 调用会被桩吞掉）
harness += script + "\n"
harness += """
// ---- 测试驱动 ----
PATH_GRAPH = __PG__;
function ntype(id){ var n=PATH_GRAPH.nodes[id]; return n?n.type:null; }
function isCrossStair(eid){ for (var i=0;i<PATH_GRAPH.edges.length;i++){ var e=PATH_GRAPH.edges[i]; if(e.id===eid) return (e.crossFloor && e.type==='staircase'); } return false; }
var cases = __CASES__;
var fails = 0, total = 0;
for (var i=0;i<cases.length;i++){
  var c = cases[i]; total++;
  var r = dijkstra(c.s, c.e, c.mode);
  var jsPath = r ? r.nodes : null;
  var jsDist = r ? r.distance : null;
  var jsNote = r ? r.note : null;
  var exp = c.exp;
  var ok = true, why = "";
  // 1) 与后端结果一致
  if (exp.reachable) {
    if (!r) { ok=false; why="后端可达但前端不可达"; }
    else {
      if (JSON.stringify(jsPath) !== JSON.stringify(exp.path)) { ok=false; why="节点序列不一致"; }
      if (Math.abs(jsDist - exp.distance) > 1e-6) { ok=false; why="距离不一致("+jsDist+" vs "+exp.distance+")"; }
      if ((jsNote||null) !== (exp.note||null)) { ok=false; why="回退标记不一致("+jsNote+" vs "+exp.note+")"; }
    }
  } else { if (r) { ok=false; why="后端不可达但前端可达"; } }
  // 2) 规则不变量（前端自身也应满足）
  if (r) {
    var sf = PATH_GRAPH.nodes[c.s].floor === PATH_GRAPH.nodes[c.e].floor;
    // 规则 1：同层路线中间节点不得为 facility
    if (sf) {
      for (var k=1;k<jsPath.length-1;k++){ if (ntype(jsPath[k])==='facility'){ ok=false; why="同层路线含 facility 中转"; break; } }
    }
    // 规则 2：盲模式跨层不得走楼梯跨层边
    if (c.mode==='blind' && !sf) {
      for (var m=0;m<r.edges.length;m++){ if (isCrossStair(r.edges[m])){ ok=false; why="盲模式跨层走了楼梯"; break; } }
    }
    // 规则 3：房间->房间不得 room->doorway->room 直连
    for (var p=0;p+2<jsPath.length;p++){
      if (ntype(jsPath[p])==='room' && ntype(jsPath[p+1])==='doorway' && ntype(jsPath[p+2])==='room'){ ok=false; why="room->door->room 直连穿透"; break; }
    }
  }
  console.log((ok?"PASS":"FAIL")+" ["+c.mode+"] "+c.s+" -> "+c.e+(ok?"":("  >>> "+why)));
  if (!ok) fails++;
}
console.log("SUMMARY: "+(total-fails)+"/"+total+" passed");
"""

cases = [{"s": q[0], "e": q[1], "mode": q[2],
          "exp": next(x for x in expected if x["s"]==q[0] and x["e"]==q[1] and x["mode"]==q[2])}
         for q in queries]
harness = harness.replace("__PG__", json.dumps(pg))
harness = harness.replace("__CASES__", json.dumps(cases))
jsfile = os.path.join(tempfile.gettempdir(), "path_parity.js")
open(jsfile, "w", encoding="utf-8").write(harness)
out = subprocess.run([NODE, jsfile], capture_output=True, text=True)
print(out.stdout)
if out.stderr:
    print("STDERR:", out.stderr[:2000])
sys.exit(1 if (out.returncode != 0 or "FAIL" in out.stdout) else 0)
