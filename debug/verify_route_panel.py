# -*- coding: utf-8 -*-
"""右栏「路径规划完整路径列表」渲染验证。

在 Node 中以最小 DOM 桩执行前端 renderRouteList()，用真实 PATH_GRAPH 数据校验：
  1. 列表项数 == 路径节点数，且 data-nid 顺序与 dijkstra 结果一致；
  2. 累计距离单调不减，末项累计 ≈ 各段 distance 之和；
  3. 摘要含起终点、总长度、途经节点数；
  4. 跨层盲模式路线在列表中标出「跨层·电梯」；
  5. 不可达时摘要显示「不可达」；
  6. resetRoutePanel() 能清空列表并恢复占位文案；
  7. 点击列表项触发 focusRouteNode（视图平移）。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

NODE = r"C:\Users\xinni\.workbuddy\binaries\node\versions\22.22.2\node.exe"
HTML = "result/floor_layout_v9_interactive.html"

html = open(HTML, encoding="utf-8").read()
m = re.search(r'<script type="application/json" id="path-graph-data">(.*?)</script>', html, re.S)
pg = json.loads(m.group(1))
script = None
for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S):
    if "function renderRouteList" in b and "function dijkstra" in b:
        script = b
        break
if not script:
    print("FAIL: 未在生成的 HTML 中找到含 renderRouteList 的脚本块")
    sys.exit(1)

harness = r"""
// ---------- 最小 DOM 桩 ----------
var _registry = {};
function _mkClassList(){
  var s = {};
  return { add:function(c){ s[c]=1; }, remove:function(c){ delete s[c]; },
           contains:function(c){ return !!s[c]; }, _set:s };
}
function _mkLi(attrs){
  var nid = (/data-nid="([^"]*)"/.exec(attrs) || [])[1] || null;
  var handlers = [];
  return {
    _nid: nid, _handlers: handlers,
    classList: _mkClassList(),
    addEventListener: function(ev, fn){ if (ev === 'click') handlers.push(fn); },
    getAttribute: function(k){ return k === 'data-nid' ? nid : null; },
    click: function(){ handlers.forEach(function(f){ f(); }); }
  };
}
function _mkEl(id){
  var el = {
    // textContent 用 '{}'：页面脚本会 JSON.parse(#path-graph-data / #full-geojson-data)，
    // 给空串会在 stderr 刷出误导性的 SyntaxError（虽被 catch）
    id: id, _html: '', className: '', textContent: '{}', children: [],
    classList: _mkClassList(), addEventListener: function(){},
    getAttribute: function(){ return null; }, setAttribute: function(){},
    appendChild: function(){}, style: {},
    getBoundingClientRect: function(){ return { width: 800, height: 600, left: 0, top: 0 }; }
  };
  Object.defineProperty(el, 'innerHTML', {
    get: function(){ return el._html; },
    set: function(v){
      el._html = v;
      el.children = [];
      var re = /<li ([^>]*)>/g, mm;
      while ((mm = re.exec(v))) el.children.push(_mkLi(mm[1]));
    }
  });
  return el;
}
// 只对右栏三个目标元素做「有状态」缓存桩，其余沿用一次性简单桩
// （与 verify_frontend_parity.py 的桩保持一致，避免污染路由逻辑）
var _TRACKED = { 'route-summary': 1, 'route-steps': 1, 'rp-mode': 1 };
function _get(id){
  if (!_TRACKED[id]) return _mkEl('_' + id);
  if (!_registry[id]) _registry[id] = _mkEl(id);
  return _registry[id];
}
global.document = {
  getElementById: _get,
  querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; },
  createElement: function(){ return _mkEl('x'); },
  createElementNS: function(){ return _mkEl('x'); },
  addEventListener: function(){}
};
var _focus = { calls: 0 };
global.wrapper = {
  addEventListener: function(){},
  getBoundingClientRect: function(){ return { width: 800, height: 600, left: 0, top: 0 }; }
};
global.scale = 1; global.translateX = 0; global.translateY = 0;
global.applyTransform = function(){ _focus.calls++; };
global.ensureLayer = function(){};
"""

harness += script + "\n"

harness += r"""
// ---------- 测试驱动 ----------
PATH_GRAPH = __PG__;
var fails = 0, total = 0;
function check(name, cond, detail){
  total++;
  if (!cond) fails++;
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (cond ? '' : '  >>> ' + (detail || '')));
}
function ids(t){ return Object.keys(PATH_GRAPH.nodes).filter(function(k){ return PATH_GRAPH.nodes[k].type === t; }); }
function byFloor(list, f){ return list.filter(function(k){ return PATH_GRAPH.nodes[k].floor === f; }); }

var rooms1 = byFloor(ids('room'), 1), rooms2 = byFloor(ids('room'), 2);

// 注意：部分房间（如无门/门洞卫生间）在规则约束下本就不可达，
// 直接取 rooms[0]/rooms[1] 会得到 null 路径而让断言变成空转。
// 这里主动搜索一对真实可达、且节点数足够的房间。
function findPair(a, b, mode, minLen){
  for (var i = 0; i < a.length; i++) {
    for (var j = 0; j < b.length; j++) {
      if (a[i] === b[j]) continue;
      var rr = dijkstra(a[i], b[j], mode);
      if (rr && rr.nodes.length >= (minLen || 4)) return { s: a[i], e: b[j], r: rr };
    }
  }
  return null;
}
var sum = document.getElementById('route-summary');
var list = document.getElementById('route-steps');
var modeEl = document.getElementById('rp-mode');

// ===== 用例 A：同层 room -> room（normal） =====
var pairA = findPair(rooms1, rooms1, 'normal', 5);
check('A0 找到同层可达房间对', !!pairA);
if (!pairA) { console.log('SUMMARY: ' + (total - fails) + '/' + total + ' passed'); process.exit(1); }
var s = pairA.s, e = pairA.e, r = pairA.r;
console.log('    (同层用例: ' + s + ' -> ' + e + ', ' + r.nodes.length + ' 节点)');
renderRouteList(r, 'normal', s, e);
check('A1 列表项数 == 路径节点数',
      list.children.length === r.nodes.length,
      list.children.length + ' vs ' + r.nodes.length);
var seqOk = r.nodes.every(function(id, i){ return list.children[i].getAttribute('data-nid') === id; });
check('A2 列表 data-nid 顺序与路径一致', seqOk);
check('A3 模式徽章为「普通」', modeEl.textContent === '普通', modeEl.textContent);
check('A4 摘要含起终点与总长度',
      sum.innerHTML.indexOf('→') >= 0 && sum.innerHTML.indexOf('总长度') >= 0 &&
      sum.innerHTML.indexOf('途经节点') >= 0);
check('A5 摘要脱离占位态', sum.className === '', 'className=' + sum.className);
// 累计距离单调不减 + 末项累计 == 各段 distance 之和
var cums = [], mm2, reCum = /Σ ([0-9.]+) m/g;
while ((mm2 = reCum.exec(list._html))) cums.push(parseFloat(mm2[1]));
var mono = cums.every(function(v, i){ return i === 0 || v >= cums[i-1] - 1e-9; });
check('A6 累计距离单调不减', mono, JSON.stringify(cums.slice(0, 8)));
var em = {};
PATH_GRAPH.edges.forEach(function(x){ em[x.id] = x; });
var geo = 0;
r.edges.forEach(function(eid){ geo += Number((em[eid] || {}).distance) || 0; });
check('A7 末项累计 ≈ 各段 distance 之和',
      cums.length > 0 && Math.abs(cums[cums.length-1] - geo) < 0.15,
      (cums[cums.length-1]) + ' vs ' + geo.toFixed(2));
check('A8 首项标注「起点」', list._html.indexOf('起点') >= 0);
// ===== 用例 B：点击列表项触发定位 =====
// 注意：脚本内真实的 applyTransform() 会覆盖桩函数，所以直接断言平移量变化
var _tx0 = translateX, _ty0 = translateY;
var _li = list.children[Math.min(2, list.children.length - 1)];
_li.click();
check('B1 点击列表项触发视图定位（平移量改变）',
      translateX !== _tx0 || translateY !== _ty0,
      'tx ' + _tx0 + '->' + translateX + ', ty ' + _ty0 + '->' + translateY);
check('B2 被点击项加 active 类', _li.classList.contains('active'));
// 定位应把节点居中：translate = 视口中心 - 节点坐标 * scale
var _n = PATH_GRAPH.nodes[_li.getAttribute('data-nid')];
check('B3 定位公式把目标节点居中',
      Math.abs(translateX - (400 - _n.x * scale)) < 1e-6 &&
      Math.abs(translateY - (300 - _n.y * scale)) < 1e-6,
      'tx=' + translateX + ' expect=' + (400 - _n.x * scale));

// ===== 用例 C：跨层盲模式必须电梯，并在列表中标注 =====
if (rooms2.length) {
  var pairC = findPair(rooms1, rooms2, 'blind', 5);
  check('C0 找到跨层盲模式可达房间对', !!pairC);
  if (pairC) {
    var s2 = pairC.s, e2 = pairC.e, rb = pairC.r;
    console.log('    (跨层用例: ' + s2 + ' -> ' + e2 + ', ' + rb.nodes.length + ' 节点)');
    renderRouteList(rb, 'blind', s2, e2);
    check('C1 模式徽章为「视障」', modeEl.textContent === '视障', modeEl.textContent);
    check('C2 摘要显示跨层段数', sum.innerHTML.indexOf('跨层段') >= 0);
    check('C3 列表标注「跨层·电梯」', list._html.indexOf('跨层·电梯') >= 0);
    check('C4 列表未出现「跨层·楼梯」', list._html.indexOf('跨层·楼梯') < 0);
    check('C5 列表项数 == 路径节点数', list.children.length === rb.nodes.length);
  }
}

// ===== 用例 D：不可达 =====
renderRouteList(null, 'blind', s, e);
check('D1 不可达时摘要提示「不可达」', sum.innerHTML.indexOf('不可达') >= 0);
check('D2 不可达时列表清空', list.children.length === 0);
check('D3 不可达时回到占位样式', sum.className === 'rp-empty');

// ===== 用例 E：重置 =====
renderRouteList(r, 'normal', s, e);
check('E0 重置前列表非空', list.children.length > 0);
resetRoutePanel();
check('E1 重置后列表清空', list.children.length === 0);
check('E2 重置后恢复占位文案', sum.innerHTML.indexOf('尚未规划路径') >= 0 && sum.className === 'rp-empty');

console.log('SUMMARY: ' + (total - fails) + '/' + total + ' passed');
"""

harness = harness.replace("__PG__", json.dumps(pg))
jsfile = os.path.join(tempfile.gettempdir(), "route_panel_test.js")
with open(jsfile, "w", encoding="utf-8") as f:
    f.write(harness)

out = subprocess.run([NODE, jsfile], capture_output=True, text=True)
print(out.stdout)
if out.stderr.strip():
    print("STDERR:", out.stderr[:1500])
sys.exit(1 if (out.returncode != 0 or "FAIL " in out.stdout) else 0)
