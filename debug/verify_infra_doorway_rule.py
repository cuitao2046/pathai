# -*- coding: utf-8 -*-
"""规则 5 验证：归属全部为 infrastructure 的门（纯管井门）不出现在导航路径。

后端全量扫描 + 前端 Node 实测（PATH_GRAPH.infraDoorwayIds 剔除）。
"""
import json, re, subprocess, tempfile, os, sys, itertools

NODE = r"C:\Users\xinni\.workbuddy\binaries\node\versions\22.22.2\node.exe"
GEO = "result/school_building_01_map_v9.geojson"
HTML = "result/floor_layout_v9_interactive.html"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.topology.route_rules import load_geojson

g = load_geojson(GEO)
infra_tds = g.infra_doorway_ids
rooms = [nid for nid, n in g.nodes.items() if n["type"] == "room"]

# ---- 后端全量 ----
print("=== 后端 RouteGraph 全量扫描 ===")
print("纯管井门 infra_doorway_ids:", sorted(infra_tds))
viol_b = reach_b = 0
for s, e in itertools.combinations(rooms, 2):
    for mode in ("normal", "blind"):
        sp = g.shortest_path(s, e, mode)
        if sp is None:
            continue
        reach_b += 1
        if any(nid in infra_tds for nid in sp["path"]):
            viol_b += 1
print(f"后端: 可达路径 {reach_b} 条, 经过纯管井门 {viol_b} 条")

# ---- 前端 ----
html = open(HTML, encoding="utf-8").read()
pg = json.loads(re.search(
    r'<script type="application/json" id="path-graph-data">(.*?)</script>',
    html, re.S).group(1))
print("PATH_GRAPH.infraDoorwayIds:", pg.get("infraDoorwayIds"))
script = None
for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
    if "function dijkstra" in b and "function edgeAllowed" in b:
        script = b
        break

harness = """
function _stubEl(){ return { setAttribute(){}, appendChild(){}, classList:{add(){},remove(){},contains(){return false;}}, addEventListener(){}, getAttribute(){return null;}, style:{}, innerHTML:"", textContent:"{}" }; }
global.document = { getElementById:function(){return _stubEl();}, querySelector:function(){return null;}, querySelectorAll:function(){return [];}, createElement:function(){return _stubEl();}, createElementNS:function(){return _stubEl();}, addEventListener:function(){} };
global.wrapper = { addEventListener:function(){} };
"""
harness += script + "\n"
harness += """
PATH_GRAPH = __PG__;
var INFRA = {};
(PATH_GRAPH.infraDoorwayIds||[]).forEach(function(id){ INFRA[id]=1; });
var rooms = Object.keys(PATH_GRAPH.nodes).filter(function(id){ return PATH_GRAPH.nodes[id].type==='room'; });
var fails = 0, total = 0, reach = 0;
for (var i=0;i<rooms.length;i++) for (var j=i+1;j<rooms.length;j++) {
  ['normal','blind'].forEach(function(mode){
    total++;
    var r = dijkstra(rooms[i], rooms[j], mode);
    if (!r) return;
    reach++;
    for (var k=0;k<r.nodes.length;k++) if (INFRA[r.nodes[k]]) { fails++; console.log('FAIL: '+rooms[i]+'->'+rooms[j]+' 经过 '+r.nodes[k]); }
  });
}
console.log('前端: '+total+' 组合, 可达 '+reach+', 经过纯管井门 '+fails);
console.log(fails===0 ? 'PASS' : 'FAIL');
"""
harness = harness.replace("__PG__", json.dumps(pg))
jsfile = os.path.join(tempfile.gettempdir(), "infra_check.js")
open(jsfile, "w", encoding="utf-8").write(harness)
out = subprocess.run([NODE, jsfile], capture_output=True, text=True)
print(out.stdout.strip())
if out.stderr:
    print("STDERR:", out.stderr[:500])

backend_ok = viol_b == 0
frontend_ok = (out.returncode == 0 and "FAIL" not in out.stdout and "PASS" in out.stdout)
print(f"\n后端无违例: {backend_ok} | 前端无违例: {frontend_ok}")
sys.exit(0 if (backend_ok and frontend_ok) else 1)
