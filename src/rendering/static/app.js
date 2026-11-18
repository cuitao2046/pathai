var svg = document.getElementById('main-svg');
var wrapper = document.getElementById('svg-wrapper');
var container = document.getElementById('svg-container');
var tip = document.getElementById('tooltip');
var scale = 1, translateX = 0, translateY = 0;
var isDragging = false, startX = 0, startY = 0;

function applyTransform() {
  svg.style.transform = 'translate(' + translateX + 'px,' + translateY + 'px) scale(' + scale + ')';
  svg.style.transformOrigin = '0 0';
  document.querySelector('.zoom-info') || (function(){}());
}
function setZoomInfo() {
  var z = document.querySelector('.zoom-info');
  if (z) z.textContent = '缩放: ' + Math.round(scale * 100) + '%';
}
function zoomIn() { scale = Math.min(scale * 1.3, 20); applyTransform(); setZoomInfo(); }
function zoomOut() { scale = Math.max(scale / 1.3, 0.15); applyTransform(); setZoomInfo(); }
function resetView() { scale = 1; translateX = 0; translateY = 0; applyTransform(); setZoomInfo(); }

wrapper.addEventListener('wheel', function(e) {
  e.preventDefault();
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  if (e.ctrlKey) {
    // 触控板双指捏合 / Ctrl+滚轮：围绕光标平滑缩放
    var factor = Math.exp(-e.deltaY * 0.012);
    var ns = Math.max(0.15, Math.min(20, scale * factor));
    var ratio = ns / scale;
    translateX = mx - ratio * (mx - translateX);
    translateY = my - ratio * (my - translateY);
    scale = ns; applyTransform(); setZoomInfo();
  } else if (e.deltaMode === 1) {
    // 鼠标滚轮（行模式）：保持原缩放行为（围绕光标）
    var delta = e.deltaY > 0 ? 0.9 : 1.1;
    var ns2 = Math.max(0.15, Math.min(20, scale * delta));
    var ratio2 = ns2 / scale;
    translateX = mx - ratio2 * (mx - translateX);
    translateY = my - ratio2 * (my - translateY);
    scale = ns2; applyTransform(); setZoomInfo();
  } else {
    // 触控板双指滑动（像素模式平滑滚动）：平移画布（跟手）
    translateX += e.deltaX;
    translateY += e.deltaY;
    applyTransform();
  }
}, { passive: false });

// Safari 旧式触控板手势（gesture 事件）：捏合缩放兼容
var lastGestureScale = 1;
wrapper.addEventListener('gesturestart', function(e) { e.preventDefault(); lastGestureScale = 1; });
wrapper.addEventListener('gesturechange', function(e) {
  e.preventDefault();
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  var factor = e.scale / lastGestureScale;
  lastGestureScale = e.scale;
  var ns = Math.max(0.15, Math.min(20, scale * factor));
  var ratio = ns / scale;
  translateX = mx - ratio * (mx - translateX);
  translateY = my - ratio * (my - translateY);
  scale = ns; applyTransform(); setZoomInfo();
});

wrapper.addEventListener('mousedown', function(e) {
  if (window.annoMode) { startAnnoDraw(e); return; }
  isDragging = true; startX = e.clientX - translateX; startY = e.clientY - translateY;
  wrapper.classList.add('grabbing');
});
document.addEventListener('mousemove', function(e) {
  if (!isDragging) return;
  translateX = e.clientX - startX; translateY = e.clientY - startY;
  applyTransform();
});
document.addEventListener('mouseup', function() { isDragging = false; wrapper.classList.remove('grabbing'); });

// ---- 悬停提示 ----
wrapper.addEventListener('mousemove', function(e) {
  var t = e.target;
  if (t && t.closest) t = t.closest('[data-info]');   // 命中层(.hit)/子元素向上找 data-info
  var info = t.getAttribute && t.getAttribute('data-info');
  if (!info) { tip.style.display = 'none'; return; }
  var d; try { d = JSON.parse(info); } catch (err) { tip.style.display = 'none'; return; }
  tip.textContent = (d.tip || '').replace(/\\n/g, '\n');
  tip.style.display = 'block';
  var cr = container.getBoundingClientRect();
  var x = e.clientX - cr.left + 14, y = e.clientY - cr.top + 14;
  if (x + 290 > cr.width) x = e.clientX - cr.left - 290;
  if (y + 80 > cr.height) y = cr.height - 90;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
});
wrapper.addEventListener('mouseleave', function() { tip.style.display = 'none'; });

// ---- 点击任意要素：按当前状态切换选中，并动态调整关联状态 ----
// 关联状态包括：拓扑图层展示（选中拓扑节点时自动显示）、选中详情面板等
var DETAIL_PLACEHOLDER = '<h4>点击任意要素查看详情</h4><div style="color:#999;font-size:12px">悬停查看提示，点击锁定详情；再次点击同一要素可取消选中。点击拓扑节点会高亮其<b style="color:#FFC107">相连边</b>与<b style="color:#00BCD4">直接可达节点</b>（青色）。</div>';
function clearHighlight() {
  document.querySelectorAll('.selected').forEach(function(el){ el.classList.remove('selected'); });
  document.querySelectorAll('.neighbor').forEach(function(el){ el.classList.remove('neighbor'); });
  document.querySelectorAll('.hz-selected').forEach(function(el){ el.classList.remove('hz-selected'); });
  var ring = document.getElementById('path-flash-ring');
  if (ring) ring.innerHTML = '';
  clearBeaconContrib();
  clearCoverageBeacons();
}
function resetDetail() { currentDetail = null; document.getElementById('detail').innerHTML = DETAIL_PLACEHOLDER; }
// 按节点 id 找到对应的拓扑节点组并加高亮 class
function markNode(id, cls) {
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try { var nd = JSON.parse(f); if (nd.id === id) g.classList.add(cls); } catch(e){}
  });
}
// 联动：确保某图层可见（并同步勾选框状态）
function ensureLayer(name, checked) {
  document.querySelectorAll('.layer_' + name).forEach(function(el){ el.style.display = checked ? '' : 'none'; });
  var cb = document.querySelector('#layerControls input[onchange*="' + name + '"]');
  if (cb) cb.checked = checked;
}
function renderCell(v) {
  // 可点击 ID 链接对象 {_l: id, t: text} → 点击后居中定位到对应元素
  if (v && typeof v === 'object') {
    if (v._l) {
      return '<a href="javascript:void(0)" class="id-link" data-mid="' + rpEsc(v._l) + '">'
           + rpEsc(v.t != null ? v.t : v._l) + '</a>';
    }
    if (Array.isArray(v)) { return v.map(renderCell).join('、'); }
  }
  return rpEsc(String(v));
}
var currentDetail = null;
function showDetail(d) {
  currentDetail = d;
  var box = document.getElementById('detail');
  var title = d.title || '详情';
  // 若已知节点ID，在标题右上方追加拓扑节点ID，方便截图核对
  var titleId = '';
  if (d.id) titleId = ' <span style="font-size:12px;color:#666;font-weight:400">(' + rpEsc(d.id) + ')</span>';
  var h = '<h4>' + title + titleId + '</h4>';
  (d.rows || []).forEach(function(r) {
    h += '<div class="row"><span>' + rpEsc(String(r[0])) + '</span><span>' + renderCell(r[1]) + '</span></div>';
  });
  // 拓扑节点关联行：点击可居中定位到对应拓扑节点（统一走 data-mid → centerById）
  if (d.topoId) {
    h += '<div class="row"><span>拓扑节点</span><span>' + renderCell({_l: d.topoId, t: d.topoId}) + '</span></div>';
  }
  // 编辑入口：仅当该元素携带编辑定位元数据
  var meta = getEditMeta(d);
  if (meta) {
    h += '<div class="edit-bar"><button onclick="enterEditMode()">[编辑]</button>'
       + '<span class="edit-hint">保存将写回 GeoJSON</span></div>';
  }
  box.innerHTML = h;
}

// ---- 详情编辑：可编辑除 ID 外的所有字段并写回 GeoJSON ----
function getEditMeta(d) {
  if (!d) return null;
  var m = {};
  ['_edit_floor','_edit_coll','_edit_pid','_edit_store','_edit_key'].forEach(function(k){
    if (d[k] !== undefined) m[k.replace('_edit_','')] = d[k];
  });
  return Object.keys(m).length ? m : null;
}
function findEditTarget(meta) {
  var floor = String(meta.floor);
  var arr = (meta.store === 'root') ? FULL_DATA[meta.coll]
                                   : FULL_DATA.floors[floor][meta.store][meta.coll];
  if (!arr) return null;
  for (var i=0;i<arr.length;i++) {
    if (String(arr[i][meta.key]) === String(meta.pid)) return arr[i];
  }
  return null;
}
function collectEditable(obj, store) {
  var keys = [];
  if (store === 'geometry' && obj.properties) {
    Object.keys(obj).forEach(function(k){
      if (['id','geometry','properties','type'].indexOf(k) >= 0) return;
      keys.push({label:k, where:'obj'});
    });
    Object.keys(obj.properties).forEach(function(k){
      if (k === 'id') return;
      keys.push({label:k, where:'prop'});
    });
  } else {
    Object.keys(obj).forEach(function(k){ if (k === 'id') return; keys.push({label:k, where:'obj'}); });
  }
  return keys;
}
function valToInput(id, raw, where) {
  var safeId = 'edit_' + String(id).replace(/[^a-zA-Z0-9_]/g,'_');
  var label = rpEsc(String(id));
  if (typeof raw === 'boolean') {
    return '<div class="edit-row" data-key="'+rpEsc(String(id))+'" data-where="'+where+'"><label>'+label+'</label>'
      + '<input type="checkbox" id="'+safeId+'" '+(raw?'checked':'')+' data-type="bool"></div>';
  }
  if (typeof raw === 'number') {
    return '<div class="edit-row" data-key="'+rpEsc(String(id))+'" data-where="'+where+'"><label>'+label+'</label>'
      + '<input type="number" id="'+safeId+'" value="'+rpEsc(String(raw))+'" data-type="number"></div>';
  }
  if (Array.isArray(raw) || (raw !== null && typeof raw === 'object')) {
    return '<div class="edit-row" data-key="'+rpEsc(String(id))+'" data-where="'+where+'"><label>'+label+'</label>'
      + '<textarea id="'+safeId+'" data-type="json" rows="2">'+rpEsc(JSON.stringify(raw))+'</textarea></div>';
  }
  return '<div class="edit-row" data-key="'+rpEsc(String(id))+'" data-where="'+where+'"><label>'+label+'</label>'
    + '<input type="text" id="'+safeId+'" value="'+rpEsc(raw==null?'':String(raw))+'" data-type="string"></div>';
}
function enterEditMode() {
  var meta = getEditMeta(currentDetail);
  if (!meta) return;
  var obj = findEditTarget(meta);
  if (!obj) { alert('未找到可编辑目标元素'); return; }
  var keys = collectEditable(obj, meta.store);
  var h = '<h4>编辑：' + rpEsc(currentDetail.title || '元素')
        + ' <span style="font-size:12px;color:#666">(' + rpEsc(String(meta.pid)) + ')</span></h4>';
  h += '<div class="edit-note">ID 不可编辑，其余字段均可修改</div>';
  if (!keys.length) h += '<div class="edit-note">该元素无可编辑字段</div>';
  keys.forEach(function(k){
    var raw = (k.where === 'prop') ? obj.properties[k.label] : obj[k.label];
    h += valToInput(k.label, raw, k.where);
  });
  h += '<div class="edit-bar"><button onclick="saveEdit()">[保存]</button>'
     + '<button onclick="cancelEdit()">[取消]</button></div>';
  document.getElementById('detail').innerHTML = h;
}
function applyEdit(obj, meta) {
  var ok = true, errs = [];
  document.querySelectorAll('#detail .edit-row').forEach(function(row){
    var key = row.getAttribute('data-key');
    var where = row.getAttribute('data-where');
    var input = row.querySelector('input,textarea');
    if (!input) return;
    var t = input.getAttribute('data-type');
    var val;
    try {
      if (t === 'bool') val = input.checked;
      else if (t === 'number') { val = parseFloat(input.value); if (isNaN(val)) val = input.value; }
      else if (t === 'json') val = JSON.parse(input.value);
      else val = input.value;
      if (where === 'prop') obj.properties[key] = val; else obj[key] = val;
    } catch(e) { ok = false; errs.push(key); }
  });
  return {ok: ok, errs: errs};
}
function saveEdit() {
  var meta = getEditMeta(currentDetail);
  if (!meta) return;
  var obj = findEditTarget(meta);
  if (!obj) { alert('未找到可编辑目标元素'); return; }
  var res = applyEdit(obj, meta);
  if (!res.ok) { alert('以下字段解析失败，未保存：' + res.errs.join(', ')); return; }
  saveGeojson();
  showDetail(currentDetail);
  alert('已保存并写回 GeoJSON：' + meta.store + '/' + meta.coll + ' #' + meta.pid);
}
function cancelEdit() { showDetail(currentDetail); }
// 单击选中延迟抑制：双击（拓扑边加边）时取消挂起的单击选中，避免详情面板闪烁；
// 拓扑边点击即时响应（不受双击抑制影响，双击边无操作）。
var clickTimer = null;
// 选中元素详情注入「质心坐标」行（米制 CAD 坐标）：
// 收集被点击 <g> 内所有几何的采样点(SVG 用户空间)取平均 -> svg2geo 反算。
// 支持: polygon/polyline 顶点、line 两端、rect 中心、circle 圆心，
// 覆盖房间/走廊/可通行区/楼梯/电梯(面)、电梯门/骨架(线)、防火门(矩形)、
// 普通门/拓扑节点(圆)等全部拓扑要素。
function injectCentroid(g, d){
  if(!d || !d.detail || !d.detail.rows || d.detail._hasCentroid) return;
  var pts = [];
  g.querySelectorAll('polygon, polyline').forEach(function(sh){
    var ps = (sh.getAttribute('points') || '').split(/\s+/);
    ps.forEach(function(pair){
      var m = pair.split(',');
      if(m.length === 2 && isFinite(parseFloat(m[0]))) pts.push([parseFloat(m[0]), parseFloat(m[1])]);
    });
  });
  g.querySelectorAll('line').forEach(function(sh){
    var x1 = parseFloat(sh.getAttribute('x1')), y1 = parseFloat(sh.getAttribute('y1'));
    var x2 = parseFloat(sh.getAttribute('x2')), y2 = parseFloat(sh.getAttribute('y2'));
    if(isFinite(x1) && isFinite(y1)) pts.push([x1, y1]);
    if(isFinite(x2) && isFinite(y2)) pts.push([x2, y2]);
  });
  g.querySelectorAll('rect').forEach(function(sh){
    var x = parseFloat(sh.getAttribute('x')), y = parseFloat(sh.getAttribute('y'));
    var w = parseFloat(sh.getAttribute('width')), h = parseFloat(sh.getAttribute('height'));
    if(isFinite(x) && isFinite(y) && isFinite(w) && isFinite(h)) pts.push([x + w / 2, y + h / 2]);
  });
  g.querySelectorAll('circle').forEach(function(sh){
    var cx = parseFloat(sh.getAttribute('cx')), cy = parseFloat(sh.getAttribute('cy'));
    if(isFinite(cx) && isFinite(cy)) pts.push([cx, cy]);
  });
  if(pts.length === 0) return;
  var sx = 0, sy = 0;
  pts.forEach(function(p){ sx += p[0]; sy += p[1]; });
  var cg = svg2geo(sx / pts.length, sy / pts.length);
  d.detail._hasCentroid = true;
  d.detail.rows = [['质心坐标', '(' + cg.x.toFixed(2) + ', ' + cg.y.toFixed(2) + ')']].concat(d.detail.rows);
}
// ---- 选中信标：高亮其「关键贡献点」（该 1m 网格点恰靠此信标维持 >=3 可见，移除即 <3）----
var BEACON_CONTRIB = null;
function loadBeaconContrib(){
  if (BEACON_CONTRIB !== null) return BEACON_CONTRIB;
  var el = document.getElementById('beacon-contrib-data');
  try { BEACON_CONTRIB = el ? JSON.parse(el.textContent) : {}; } catch(e) { BEACON_CONTRIB = {}; }
  return BEACON_CONTRIB;
}
function clearBeaconContrib(){
  document.querySelectorAll('.beacon-contrib').forEach(function(el){
    if (el.parentNode) el.parentNode.removeChild(el);
  });
}
function showBeaconContrib(t){
  clearBeaconContrib();
  var d2 = null; try { d2 = JSON.parse(t.getAttribute('data-info')); } catch(e) { return; }
  if (d2.kind !== 'beacon' || !d2.id) return;
  var mode = t.getAttribute('data-mode') || 'global';
  var m = loadBeaconContrib()[mode + ':' + d2.id];
  if (!m || !m.length) return;
  var fk = t.getAttribute('data-floor');
  var fi = GEOX.floorKeys.indexOf(String(fk));
  if (fi < 0) return;
  var NS = 'http://www.w3.org/2000/svg';
  var g = document.createElementNS(NS, 'g');
  g.setAttribute('class', 'beacon-contrib');
  m.forEach(function(c){
    var sx = GEOX.marginX + (c[0] - GEOX.ox) * GEOX.scale;
    var sy = fi * GEOX.perFloor + GEOX.titleH + GEOX.marginY + (GEOX.oy - c[1]) * GEOX.scale;
    var cEl = document.createElementNS(NS, 'circle');
    cEl.setAttribute('cx', sx); cEl.setAttribute('cy', sy);
    cEl.setAttribute('r', 4.2); cEl.setAttribute('fill', 'none');
    cEl.setAttribute('stroke', '#2E7D32'); cEl.setAttribute('stroke-width', 1.6);
    g.appendChild(cEl);
  });
  svg.appendChild(g);
}
// ---- 选中三点覆盖点：高亮覆盖它的信标（同模式 .layer_beacon 中 id 命中者）----
function clearCoverageBeacons(){
  document.querySelectorAll('.beacon-hit').forEach(function(el){ el.classList.remove('beacon-hit'); });
}
function showCoverageBeacons(t, d){
  clearCoverageBeacons();
  var mode = t.getAttribute('data-mode') || 'global';
  var list = d.covBeacons || [];
  list.forEach(function(bid){
    document.querySelectorAll('.layer_beacon[data-mode="' + mode + '"]').forEach(function(g){
      var f = g.getAttribute('data-info');
      if (!f) return;
      try { var bd = JSON.parse(f); if (bd.id === bid) g.classList.add('beacon-hit'); } catch(e){}
    });
  });
}
wrapper.addEventListener('click', function(e) {
  if (window.annoMode) return;   // 标注模式下拖拽框选，不触发要素选中
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try { d = JSON.parse(info); } catch (err) { return; }
  // 危险层图元：选中/取消 + 危险详情（档位/类型/理由/影响边）
  if ((d.kind === 'hazardEdge' || d.kind === 'hazardZone') && window.showHazardDetail) {
    if (t.classList.contains('hz-selected')) { clearHighlight(); resetDetail(); return; }
    clearHighlight(); t.classList.add('hz-selected');
    window.showHazardDetail(d);
    return;
  }
  injectCentroid(t, d);   // 选中即注入质心坐标（无需其它分支单独处理）
  // 拓扑边：即时选中/取消 + 详情 + 记录选中（启用删除按钮）
  if (d.kind === 'edge' && d.id) {
    if (t.classList.contains('selected')) {
      clearHighlight(); resetDetail();
      selectedEdgeId = null; selectedEdgeEl = null; updateDeleteBtn();
      return;
    }
    clearHighlight(); t.classList.add('selected');
    showDetail(d.detail || { title: d.tip || '详情', rows: [] });
    selectedEdgeId = d.id; selectedEdgeEl = t; updateDeleteBtn();
    return;
  }
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
  clickTimer = setTimeout(function() {
    clickTimer = null;
    // 已选中 → 再次点击取消选中，并还原关联状态
    if (t.classList.contains('selected')) {
      clearHighlight(); clearBeaconContrib(); resetDetail();
      return;
    }
    // 未选中 → 切换为选中：先还原其它，再建立本要素的关联状态
    clearHighlight(); t.classList.add('selected');
    showDetail(d.detail || { title: d.tip || '详情', rows: [] });
    // 信标 → 高亮其关键贡献点（绿色轮廓圈，见 showBeaconContrib）
    if (d.kind === 'beacon') showBeaconContrib(t);
    // 三点覆盖点 → 高亮覆盖它的信标（黄色描边，见 showCoverageBeacons）
    if (d.kind === 'coverage') showCoverageBeacons(t, d);
    // 拓扑节点 → 联动拓扑图层展示，并高亮相连边 + 直接可达节点
    if (d.kind === 'node' && d.id) {
      ensureLayer('topo_node', true);
      ensureLayer('topo_edge', true);
      var nbCount = 0;
      document.querySelectorAll('.layer_topo_edge, .layer_topo_edge_titi').forEach(function(g) {
        var f = g.getAttribute('data-info');
        if (!f) return;
        try {
          var ed = JSON.parse(f);
          if (ed.from === d.id || ed.to === d.id) {
            g.classList.add('selected');                 // 高亮相连边
            var nb = (ed.from === d.id) ? ed.to : ed.from; // 直接可达节点 id
            if (nb) { markNode(nb, 'neighbor'); nbCount++; } // 高亮直接可达节点
          }
        } catch(e){}
      });
      // 在详情面板补充「直接可达」统计
      var stat = document.createElement('div');
      stat.className = 'row';
      stat.innerHTML = '<span>直接可达节点</span><span>' + nbCount + ' 个</span>';
      document.getElementById('detail').appendChild(stat);
    }
  }, 250);
});

// ---- 图层开关 ----
var allLayers = ['room','infrastructure','corridor','lobby','activity','atrium','lobby_elevator','lobby_stair','walkable','skeleton','skeleton_node','wall','window','stairs','elevator','elevator_door','column','building_outline',
  'door_swing','door_opening','door_fire',
  'topo_node','topo_edge','topo_edge_titi','crossfloor','risk','ramp','tactile','material','fingerprint','beacon','coverage','hazard'];
// ---- 方案模式开关（全局 / 测试路线）：切换信标部署/指纹采集/三点覆盖三套数据 ----
var CUR_MODE = 'route';
function setMode(m) {
  CUR_MODE = m;
  document.querySelectorAll('.mode-global').forEach(function(el){ el.style.display = (m === 'global') ? '' : 'none'; });
  document.querySelectorAll('.mode-route').forEach(function(el){ el.style.display = (m === 'route') ? '' : 'none'; });
  var bg = document.getElementById('mode-btn-global'), br = document.getElementById('mode-btn-route');
  if (bg) bg.classList.toggle('active', m === 'global');
  if (br) br.classList.toggle('active', m === 'route');
  clearBeaconContrib();
}
// 显示状态严格跟随勾选框：勾选=显示，取消=隐藏（避免「勾选反而隐藏」的倒挂）
function toggleLayer(name, checked) {
  document.querySelectorAll('.layer_' + name).forEach(function(el){ el.style.display = checked ? '' : 'none'; });
}
function setAll(v) {
  allLayers.forEach(function(n){
    document.querySelectorAll('.layer_' + n).forEach(function(el){ el.style.display = v ? '' : 'none'; });
  });
  document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb){ cb.checked = v; });
}
// ---- 补装信标仿真（5 枚未安装）：仅叠加显示虚拟信标与「因补装新增可采」点，不改数据 ----
function toggleSimBeacons(on) {
  document.body.classList.toggle('show-sim-beacons', !!on);
}

// ---- 导出所选图层为独立 SVG 图片 ----
// 导出的是「当前在图层面板勾选的图层」：先取消不需要的图层再点导出即可。
// 关键：页面 SVG 的描边/填充全部来自 <head> 的外部 <style>，脱离 HTML 后会丢失样式，
// 因此必须把该 <style> 嵌入到克隆出的 SVG 内部，导出的文件才能正常上色。
function exportSelectedSVG() {
  // 1. 收集当前勾选（=所选）图层
  var selected = allLayers.filter(function(n){
    var cb = document.querySelector('#layerControls input[onchange*="' + n + '"]');
    return cb ? cb.checked : true;
  });
  if (selected.length === 0) { alert('请先在上方勾选至少一个图层再导出。'); return; }

  // 2. 克隆主 SVG，去掉缩放/平移的 style 变换，得到全幅原始坐标
  var clone = svg.cloneNode(true);
  clone.removeAttribute('style');
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');

  // 3. 仅保留所选图层，删除未选图层对应的 <g>
  var groups = clone.querySelectorAll('[class*="layer_"]');
  groups.forEach(function(g){
    var cls = g.getAttribute('class') || '';
    var keep = selected.some(function(n){ return cls.indexOf('layer_' + n) !== -1; });
    if (!keep) g.parentNode.removeChild(g);
  });

  // 4. 嵌入页面 <style>（保留颜色/描边），加白底背景，并剔除交互用 data-info 减体积
  var pageStyle = document.querySelector('style');
  if (pageStyle) {
    var styleEl = document.createElementNS('http://www.w3.org/2000/svg', 'style');
    styleEl.textContent = pageStyle.textContent;
    clone.insertBefore(styleEl, clone.firstChild);
  }
  var rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  rect.setAttribute('x', 0); rect.setAttribute('y', 0);
  rect.setAttribute('width', clone.getAttribute('width'));
  rect.setAttribute('height', clone.getAttribute('height'));
  rect.setAttribute('fill', '#ffffff');
  clone.insertBefore(rect, clone.firstChild.nextSibling);
  clone.querySelectorAll('[data-info]').forEach(function(el){ el.removeAttribute('data-info'); });

  // 5. 序列化并触发下载
  var data = new XMLSerializer().serializeToString(clone);
  var xmlDecl = '<?xml version="1.0" encoding="UTF-8"?>';
  if (data.indexOf(xmlDecl) !== 0) data = xmlDecl + data;
  var blob = new Blob([data], {type: 'image/svg+xml;charset=utf-8'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = 'PathAI_所选图层_楼层图.svg';
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  setTimeout(function(){ URL.revokeObjectURL(url); }, 1500);
  console.log('已导出所选图层 SVG，包含图层：', selected.join(', '));
}

// 初始化：按勾选框实际状态同步各图层可见性（未勾选图层初始应隐藏）
allLayers.forEach(function(n){
  var cb = document.querySelector('#layerControls input[onchange*="' + n + '"]');
  var show = cb ? cb.checked : true;
  document.querySelectorAll('.layer_' + n).forEach(function(el){ el.style.display = show ? '' : 'none'; });
});

// ---- 楼层跳转 ----
function buildFloorJump(total, perFloor) {
  var box = document.getElementById('floor-jump');
  var names = {1:'1F 首层'};
  for (var i=0;i<total;i++) {
    var f = i+1;
    var b = document.createElement('button');
    b.className = 'floor-btn' + (i===0?' active':'');
    b.textContent = names[f] || (f+'F');
    b.onclick = (function(idx){ return function(){ jumpFloor(idx, total, perFloor, this); }; })(i);
    box.appendChild(b);
  }
}
function jumpFloor(idx, total, perFloor, btn) {
  document.querySelectorAll('.floor-btn').forEach(function(b){ b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  var targetY = -(idx * perFloor) + 8;
  var minY = container.clientHeight - svg.clientHeight * scale;
  if (targetY > 0) targetY = 0;
  if (targetY < minY) targetY = minY;
  translateY = targetY; applyTransform(); setZoomInfo();
}

// ---- 路径规划：图上选点 + Dijkstra 最优路径 ----
var pathMode = false;
var pathStart = null, pathEnd = null;
var PATH_GRAPH = null;
(function(){
  var el = document.getElementById('path-graph-data');
  if (el) { try { PATH_GRAPH = JSON.parse(el.textContent); } catch(e) { console.warn(e); } }
})();
// 地图要素中心表（要素ID -> [svg像素中心x, y]），供详情面板点击ID居中定位
var MAP_CENTERS = {};
(function(){
  var el = document.getElementById('map-centers-data');
  if (el) { try { MAP_CENTERS = JSON.parse(el.textContent); } catch(e) { console.warn(e); } }
})();

// ---- 危险层数据（hazards_data，内联于 #hazard-data）----
// 选路语义对齐后端 backend/pathai_backend/hazard_layer.py + nav_service.plan_route：
//   block  → 进 edge_allowed 硬禁行（四层回退都不放行）
//   avoid  → 边权加 penaltyM（同边多条取最大；缺省/非正数按 60m）
//   notice → 不参与选路，只随路线提示
// 普通模式完全不读危险层；非普通模式按 appliesTo 命中当前人群才生效。
var HAZARD_DATA = null;
(function(){
  var el = document.getElementById('hazard-data');
  if (el) { try { HAZARD_DATA = JSON.parse(el.textContent); } catch(e) { console.warn('[hazard] 内联数据解析失败', e); } }
})();
var HAZARD_TYPE_CN = {
  construction: '施工', stairs: '楼梯/台阶', revolving_door: '旋转门',
  wet_floor: '地面湿滑', crowd: '人流拥挤', glass_door: '玻璃门',
  obstacle: '临时障碍', other: '其他'
};
var HAZARD_PEOPLE_CN = { blind: '视障', wheelchair: '轮椅', normal: '普通' };
var HAZARD_LEVEL_LABEL = { block: '禁行', avoid: '避开', notice: '提示' };
// 与 backend hazard_layer.DEFAULT_PENALTY_M 保持一致
var HAZARD_DEFAULT_PENALTY_M = 60.0;

function hazardParseTime(v) {
  if (!v) return null;
  var t = Date.parse(v);
  return isNaN(t) ? null : t;
}
// 生效判定：status=active 且 activeFrom ≤ now ≤ activeUntil（缺省视为不限）
function hazardActive(h, nowMs) {
  if (!h || h.status !== 'active') return false;
  var from = hazardParseTime(h.activeFrom), until = hazardParseTime(h.activeUntil);
  if (from != null && nowMs < from) return false;
  if (until != null && nowMs > until) return false;
  return true;
}
// appliesTo 缺省视为视障（与 backend hazard_layer._parse_zone 一致）
function hazardAppliesTo(h, mode) {
  var list = h && h.appliesTo;
  if (!list || !list.length) return mode === 'blind';
  return list.indexOf(mode) >= 0;
}
// 指定模式「当前时刻」生效的危险（含 notice，供路线面板展示）
function activeHazards(mode) {
  if (mode === 'normal') return [];
  var now = Date.now();
  return ((HAZARD_DATA && HAZARD_DATA.hazards) || []).filter(function(h){
    return hazardActive(h, now) && hazardAppliesTo(h, mode);
  });
}
var _HAZ_INDEX = {};
function hazardIndex(mode) {
  if (_HAZ_INDEX[mode]) return _HAZ_INDEX[mode];
  var idx = { block: {}, avoid: {}, notice: {} };
  activeHazards(mode).forEach(function(h){
    var lvl = (h.level === 'block' || h.level === 'avoid' || h.level === 'notice') ? h.level : 'notice';
    (h.edgeIds || []).forEach(function(eid){
      (idx[lvl][eid] || (idx[lvl][eid] = [])).push(h);
    });
  });
  _HAZ_INDEX[mode] = idx;
  return idx;
}
// 同边多条 avoid 取最大 penaltyM；缺省/非正数按 60m
function hazardAvoidPenalty(eid, mode) {
  var list = hazardIndex(mode).avoid[eid];
  if (!list || !list.length) return 0;
  var mx = 0;
  list.forEach(function(h){
    var p = Number(h.penaltyM);
    if (!isFinite(p) || p <= 0) p = HAZARD_DEFAULT_PENALTY_M;
    if (p > mx) mx = p;
  });
  return mx;
}

function togglePathMode() {
  pathMode = !pathMode;
  var btn = document.getElementById('btn-path-mode');
  if (btn) btn.classList.toggle('active', pathMode);
  var hint = document.getElementById('path-hint');
  if (pathMode) {
    ensureLayer('topo_node', true);
    ensureLayer('topo_edge', true);
    if (hint) hint.textContent = '请点击起点拓扑节点…';
    pathStart = pathEnd = null;
    clearPathVisual();
    resetRoutePanel();
  } else {
    if (hint) hint.textContent = '开启后依次点击两个拓扑节点（起点→终点）';
  }
}

function clearPathVisual() {
  document.querySelectorAll('.path-start,.path-end,.path-via,.path-node-flash').forEach(function(el){
    el.classList.remove('path-start','path-end','path-via','path-node-flash');
  });
  _pathFlashNodeId = null;
  var ring = document.getElementById('path-flash-ring');
  if (ring) ring.innerHTML = '';
  var g = document.getElementById('path-route-layer');
  if (g) g.innerHTML = '';
}

function clearPath() {
  pathStart = pathEnd = null;
  clearPathVisual();
  var r = document.getElementById('path-result');
  if (r) r.textContent = '';
  var h = document.getElementById('path-hint');
  if (h) h.textContent = pathMode ? '请点击起点拓扑节点…' : '开启后依次点击两个拓扑节点（起点→终点）';
  resetRoutePanel();
}

function edgeAllowed(e, mode) {
  if (mode === 'blind') {
    if (e.blindAccessible === false) return false;
    if (Number(e.accessibilityLevel) === 999) return false;
    // 规则 2：盲模式跨层必须走电梯，禁用楼梯跨层边
    if (e.crossFloor && e.type === 'staircase') return false;
  }
  if (mode === 'wheelchair') {
    if (e.wheelchairAccessible === false) return false;
    // accessibilityLevel=999 是视障结构等级，不隐含轮椅不可用。
  }
  return true;
}

// 门类型边权惩罚（米），越小越优先。
// A2：唯一来源 constants.py，由 build_path_rules_js 序列化注入，禁止内嵌常量。
function doorPenalty(dt) {
  var R = (PATH_GRAPH && PATH_GRAPH.rules) || {};
  var P = R.doorPenalty || {};
  var def = (R.doorDefaultPenalty != null) ? R.doorDefaultPenalty : 9;
  return (dt in P) ? P[dt] : def;
}

// 常闭防火门判定：isNormallyOpen === false 的 fire 门
function isClosedFireDoor(node) {
  return node && node.type === 'doorway' && node.doorType === 'fire' && node.isNormallyOpen === false;
}

function isSameFloor(s, e) {
  var ns = PATH_GRAPH.nodes[s], ne = PATH_GRAPH.nodes[e];
  return !!(ns && ne && ns.floor === ne.floor);
}

// 门类型边权（仅在 room<->door 边施加，避免每扇门重复惩罚）
// 常开防火门无惩罚（与 swing 平等）
function edgeWeight(e, nodes) {
  var w = Number(e.distance) || 0;
  var dt = e.doorType;
  if (dt) {
    var a = nodes[e.from], b = nodes[e.to];
    if ((a && a.type === 'room') || (b && b.type === 'room')) {
      // 常开防火门：无惩罚（与普通门平等对待）
      if (dt === 'fire') {
        var dn = (a && a.type === 'doorway') ? a : ((b && b.type === 'doorway') ? b : null);
        if (dn && dn.isNormallyOpen) return w;
      }
      w += doorPenalty(dt);
    }
  }
  return w;
}

// 规则 3：禁止 room->door->room 穿透（门必须连接公共空间）
function doorPassThroughBlocked(u, prev, nb, nodes) {
  var nu = nodes[u];
  if (!nu || nu.type !== 'doorway') return false;
  if (u === prev || u === nb) return false;
  var np = nodes[prev], nnb = nodes[nb];
  if (np && nnb && np.type === 'room' && nnb.type === 'room') return true;
  return false;
}

// 构造受限邻接表（对齐 route_rules._build_adjacency）
// hazMode：'off' = 不带危险约束（基线）；其它 = 按当前模式执行危险层规则
function buildPathAdj(mode, doorFilter, allowWall, hazMode) {
  var nodes = PATH_GRAPH.nodes;
  var infraTds = PATH_GRAPH.infraDoorwayIds || [];
  var infraTdSet = {};
  infraTds.forEach(function(id){ infraTdSet[id] = 1; });
  // 危险层只在非普通模式生效；block 在构造邻接表时硬剔除（所有回退层共享此过滤）
  var hz = (hazMode === 'off') ? null : hazardIndex(mode);
  var adj = {};
  (PATH_GRAPH.edges || []).forEach(function(e) {
    if (!edgeAllowed(e, mode)) return;
    // 危险层 block：硬禁行
    if (hz && hz.block[e.id]) return;
    // 规则 5：剔除连接「纯管井门」的边（导航路径不经过风井/水井门）
    if (infraTdSet[e.from] || infraTdSet[e.to]) return;
    // 常闭防火门不可通行
    if (isClosedFireDoor(nodes[e.from]) || isClosedFireDoor(nodes[e.to])) return;
    // 规则 3：剔除穿墙 TI<->TI 边（默认不穿墙；桥边回退时 allowWall=true 重新纳入）
    if (!allowWall && e.wallCrossing) return;
    var a = e.from, b = e.to;
    if (!nodes[a] || !nodes[b]) return;
    // 规则 3：房间只可使用优先级不低于自身最佳门的门
    // 常开防火门与普通门平等对待（penalty=0）
    if (doorFilter) {
      var ta = nodes[a].type, tb = nodes[b].type;
      if (ta === 'room' && tb === 'doorway') {
        var best = nodes[a].bestDoorType;
        if (best != null) {
          var edgeP = (nodes[b].doorType === 'fire' && nodes[b].isNormallyOpen) ? 0 : doorPenalty(e.doorType);
          if (edgeP > doorPenalty(best)) return;
        }
      }
      if (tb === 'room' && ta === 'doorway') {
        var best2 = nodes[b].bestDoorType;
        if (best2 != null) {
          var edgeP2 = (nodes[a].doorType === 'fire' && nodes[a].isNormallyOpen) ? 0 : doorPenalty(e.doorType);
          if (edgeP2 > doorPenalty(best2)) return;
        }
      }
    }
    var w = edgeWeight(e, nodes);
    // 危险层 avoid：边权加 penaltyM（同边多条取最大）；notice 不影响边权
    if (hz) w += hazardAvoidPenalty(e.id, mode);
    if (!adj[a]) adj[a] = [];
    if (!adj[b]) adj[b] = [];
    adj[a].push({ to: b, w: w, id: e.id });
    adj[b].push({ to: a, w: w, id: e.id });
  });
  return adj;
}

// 核心 Dijkstra（应用规则 1 中间节点白名单 + 规则 3 门穿透防护）
function dijkstraCore(startId, endId, mode, adj, midTypesOverride) {
  var nodes = PATH_GRAPH.nodes;
  // 规则 1：同层禁 facility 中转；跨层允许 facility 中转（电梯/楼梯用于跨层）
  // A2：唯一来源 constants.py，由 build_path_rules_js 序列化注入，禁止内嵌常量。
  // 第四层跨层绕行回退：midTypesOverride 传入 midTypesCrossFloor，放行 facility 中转。
  var _rules = (PATH_GRAPH && PATH_GRAPH.rules) || {};
  var _midList = midTypesOverride || (isSameFloor(startId, endId)
    ? (_rules.midTypesSameFloor || [])
    : (_rules.midTypesCrossFloor || []));
  var MID_TYPES = {};
  _midList.forEach(function(t){ MID_TYPES[t] = 1; });
  var dist = {}, prev = {}, prevEdge = {};
  Object.keys(nodes).forEach(function(id){ dist[id] = Infinity; });
  dist[startId] = 0;
  var pq = [[0, startId]]; // [d, id] simple list
  while (pq.length) {
    pq.sort(function(a,b){ return a[0]-b[0]; });
    var cur = pq.shift();
    var d = cur[0], u = cur[1];
    if (d !== dist[u]) continue;
    if (u === endId) break;
    // 中间节点白名单：房间(room)禁止中转；同层额外禁 facility 中转
    if (u !== startId && u !== endId) {
      var _ut = nodes[u] && nodes[u].type;
      // 规则 4：卫生间禁止作为中间节点（只能作起终点）
      if (nodes[u] && nodes[u].roomType === 'toilet') continue;
      if (!MID_TYPES[_ut]) continue;
    }
    var nbrs = adj[u] || [];
    for (var i = 0; i < nbrs.length; i++) {
      var nb = nbrs[i];
      // 规则 3：门不得作为两房间直连通道
      var prevU = (prev[u] != null) ? prev[u] : u;
      if (doorPassThroughBlocked(u, prevU, nb.to, nodes)) continue;
      var nd = d + nb.w;
      if (nd < dist[nb.to]) {
        dist[nb.to] = nd;
        prev[nb.to] = u;
        prevEdge[nb.to] = nb.id;
        pq.push([nd, nb.to]);
      }
    }
  }
  if (dist[endId] === Infinity) return null;
  var path = [];
  var edgeIds = [];
  for (var at = endId; at; at = prev[at]) {
    path.push(at);
    if (prevEdge[at]) edgeIds.push(prevEdge[at]);
    if (at === startId) break;
  }
  path.reverse();
  edgeIds.reverse();
  // 距离对齐 route_rules：保留 2 位小数（含门类型边权惩罚）
  return { nodes: path, edges: edgeIds, distance: Math.round(dist[endId] * 100) / 100 };
}

function dijkstra(startId, endId, mode, hazMode) {
  if (!PATH_GRAPH) return null;
  // 四层回退（对齐 route_rules.shortest_path）：
  // 1) 仅用最佳门 + 不穿墙 TI<->TI 边；
  // 2) 若不可达（最佳门未接入路网）回退允许所有门；
  // 3) 仍不可达（穿墙边是桥边）回退纳入穿墙边保连通；
  // 4) 仍不可达（同层两翼被盲不可达走廊隔断）回退放宽中间节点白名单为
  //    midTypesCrossFloor，允许经电梯跨层绕行（如 F2 东翼→F1→F2 西翼）。
  // hazMode='off'：计算「无危险约束」的基线路线（用于判定已避开/必须经过），
  // 普通模式始终按 off（不读危险层）。
  var _hz = (mode === 'normal') ? 'off' : (hazMode || 'enforce');
  var _rules = (PATH_GRAPH && PATH_GRAPH.rules) || {};
  var sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, false, _hz));
  var note = null;
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, false, false, _hz));
    if (sp) note = 'door_fallback';
  }
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, true, _hz));
    if (sp) note = 'wall_fallback';
  }
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, false, _hz),
                      _rules.midTypesCrossFloor || []);
    if (sp) note = 'cross_roundtrip';
  }
  if (sp && note) sp.note = note;
  return sp;
}

function markNodeClass(id, cls) {
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {
      var nd = JSON.parse(f);
      if (nd.id === id) g.classList.add(cls);
    } catch(e) {}
  });
}

function drawPath(result) {
  clearPathVisual();
  if (!result || !result.nodes.length) return;
  var svg = document.getElementById('main-svg');
  var layer = document.getElementById('path-route-layer');
  if (!layer) {
    layer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    layer.setAttribute('id', 'path-route-layer');
    layer.setAttribute('class', 'layer_path_route');
    svg.appendChild(layer);
  }
  layer.innerHTML = '';
  var pts = [];
  result.nodes.forEach(function(id, idx) {
    var n = PATH_GRAPH.nodes[id];
    if (!n) return;
    pts.push(n.x + ',' + n.y);
    if (idx === 0) markNodeClass(id, 'path-start');
    else if (idx === result.nodes.length - 1) markNodeClass(id, 'path-end');
    else markNodeClass(id, 'path-via');
  });
  if (pts.length >= 2) {
    var pathEl = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    pathEl.setAttribute('points', pts.join(' '));
    pathEl.setAttribute('fill', 'none');
    pathEl.setAttribute('stroke', '#E91E63');
    pathEl.setAttribute('stroke-width', '3.2');
    pathEl.setAttribute('stroke-linecap', 'round');
    pathEl.setAttribute('stroke-linejoin', 'round');
    pathEl.setAttribute('opacity', '0.95');
    layer.appendChild(pathEl);
  }
  // 桥边回退：把路径中「穿墙走廊边」（wallCrossing）以红色虚线 + 标签叠加标出，
  // 让回退保连通的路段在图上直观可见（仅在 wall_fallback 时出现）
  if (result.note === 'wall_fallback') {
    var wcEmap = rpEdgeMap();
    result.edges.forEach(function(eid) {
      var we = wcEmap[eid];
      if (!we || !we.wallCrossing) return;
      var wa = PATH_GRAPH.nodes[we.from], wb = PATH_GRAPH.nodes[we.to];
      if (!wa || !wb) return;
      var seg = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      seg.setAttribute('x1', wa.x); seg.setAttribute('y1', wa.y);
      seg.setAttribute('x2', wb.x); seg.setAttribute('y2', wb.y);
      seg.setAttribute('stroke', '#FF5722');
      seg.setAttribute('stroke-width', '5.5');
      seg.setAttribute('stroke-dasharray', '11 7');
      seg.setAttribute('stroke-linecap', 'round');
      seg.setAttribute('opacity', '0.95');
      seg.setAttribute('class', 'path-wall-cross');
      layer.appendChild(seg);
      var wmidX = (wa.x + wb.x) / 2, wmidY = (wa.y + wb.y) / 2;
      var lab = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      lab.setAttribute('x', wmidX); lab.setAttribute('y', wmidY - 6);
      lab.setAttribute('text-anchor', 'middle');
      lab.setAttribute('font-size', '9');
      lab.setAttribute('font-weight', 'bold');
      lab.setAttribute('fill', '#D32F2F');
      lab.setAttribute('class', 'path-wall-cross');
      lab.textContent = '穿墙边';
      layer.appendChild(lab);
    });
  }
  // 高亮对应拓扑边
  document.querySelectorAll('.layer_topo_edge, .layer_topo_edge_titi').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {
      var ed = JSON.parse(f);
      if (result.edges.indexOf(ed.id) >= 0) g.classList.add('selected');
    } catch(e) {}
  });
}

// ---- 右栏：路径规划「完整路径列表」 ----
var RP_TYPE_META = {
  room: { name: '房间', color: '#E67E22' },
  doorway: { name: '门口', color: '#C0392B' },
  intersection: { name: '交叉口', color: '#27AE60' },
  facility: { name: '设施', color: '#8E44AD' },
  facility_entrance: { name: '设施接入', color: '#16A085' }
};
var RP_FAC_NAME = { staircase: '楼梯', elevator: '电梯', entrance: '出入口', escalator: '扶梯' };
var RP_DOOR_NAME = { swing: '普通门', fire: '防火门', opening: '门洞' };
var RP_MODE_NAME = { normal: '普通', blind: '视障', wheelchair: '轮椅' };
var RP_EMPTY_HTML = '尚未规划路径。<br>点上方「选点导航」后依次点击起点、终点拓扑节点，这里会列出完整途经节点清单（可点击定位）。';
var _rpEdgeMap = null;

function rpEdgeMap() {
  if (_rpEdgeMap) return _rpEdgeMap;
  _rpEdgeMap = {};
  ((PATH_GRAPH && PATH_GRAPH.edges) || []).forEach(function(e) { _rpEdgeMap[e.id] = e; });
  return _rpEdgeMap;
}

function rpEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function rpSetMode(mode) {
  var el = document.getElementById('rp-mode');
  if (el) el.textContent = RP_MODE_NAME[mode] || mode;
}

function resetRoutePanel() {
  var listEl = document.getElementById('route-steps');
  if (listEl) listEl.innerHTML = '';
  var hzEl = document.getElementById('route-hazards');
  if (hzEl) hzEl.innerHTML = '';
  var sumEl = document.getElementById('route-summary');
  if (sumEl) {
    sumEl.className = 'rp-empty';
    sumEl.innerHTML = RP_EMPTY_HTML;
  }
}

// ---- 路径结果中的危险关系：onRoute（真正途经）/ avoided（已避开）/ notice（仅提示）/ blocking（必须经过） ----
// 对齐后端 nav_service._route_hazards / _blocking_hazards：
// 基线 = 同一模式、不带危险约束的路线；只判「基线会经过但最终路线不经过」为已避开。
function routeHazardInfo(result, mode, startId, endId) {
  var out = { enabled: mode !== 'normal', active: 0, onRoute: [], avoided: [], notice: [], blocking: [], baseline: null };
  if (mode === 'normal') return out;
  var active = activeHazards(mode);
  out.active = active.length;
  if (!active.length) return out;
  var baseline = dijkstra(startId, endId, mode, 'off');
  out.baseline = baseline;
  var baseEdges = {};
  if (baseline) (baseline.edges || []).forEach(function(id){ baseEdges[id] = 1; });
  if (!result) {
    // 不可达：基线路线上被 block 拦住的危险即为「必须经过」
    out.blocking = active.filter(function(h){
      return h.level === 'block' && (h.edgeIds || []).some(function(e){ return baseEdges[e]; });
    });
    return out;
  }
  var routeEdges = {};
  (result.edges || []).forEach(function(id){ routeEdges[id] = 1; });
  active.forEach(function(h){
    var edges = h.edgeIds || [];
    var on = edges.some(function(e){ return routeEdges[e]; });
    if (on) {
      // notice 只作提示；block/avoid 属于「真正途经、已计入选路」
      (h.level === 'notice' ? out.notice : out.onRoute).push(h);
      return;
    }
    // 已避开：基线路线上有、最终路线没有；notice 不参与选路，不列为避开
    if (h.level !== 'notice' && edges.some(function(e){ return baseEdges[e]; })) out.avoided.push(h);
  });
  return out;
}

function hazardItemHtml(h) {
  var lvl = (h.level === 'block' || h.level === 'avoid' || h.level === 'notice') ? h.level : 'notice';
  var typeTxt = HAZARD_TYPE_CN[h.type] || h.type || '其他';
  var meta = (h.edgeIds || []).join('、');
  if (h.penaltyM != null) meta += ' · 绕行惩罚 ' + h.penaltyM + ' m';
  return '<div class="rp-hz-item"><span class="rp-hz-badge ' + lvl + '">' +
    rpEsc(HAZARD_LEVEL_LABEL[lvl]) + '</span>' + rpEsc(typeTxt) + '：' + rpEsc(h.reason || '') +
    '<div class="rp-hz-meta">' + rpEsc(meta) + '</div></div>';
}
function hazardSectionHtml(cls, title, list, tip) {
  if (!list || !list.length) return '';
  return '<div class="rp-hz-sec ' + cls + '"><div class="rp-hz-title">' + rpEsc(title) + '</div>' +
    list.map(hazardItemHtml).join('') +
    (tip ? '<div class="rp-hz-tip">' + rpEsc(tip) + '</div>' : '') + '</div>';
}
function renderRouteHazards(hz, mode, startId, endId) {
  var box = document.getElementById('route-hazards');
  if (!box) return;
  box.innerHTML = '';
  if (mode === 'normal') {
    box.innerHTML = '<div class="rp-hz-note">普通模式不启用危险层避让（与后端一致）。切到「视障」可查看危险层选路结果。</div>';
    return;
  }
  if (!hz || !hz.active) {
    box.innerHTML = '<div class="rp-hz-note">当前时间窗内无适用于「' + rpEsc(RP_MODE_NAME[mode] || mode) + '」的生效危险。</div>';
    return;
  }
  var html = '';
  if (hz.blocking && hz.blocking.length) {
    var reasons = [];
    hz.blocking.forEach(function(h){
      var t = h.reason || HAZARD_TYPE_CN[h.type] || '危险';
      if (reasons.indexOf(t) < 0) reasons.push(t);
    });
    var dest = (PATH_GRAPH.nodes[endId] || {}).label || endId;
    html += '<div class="rp-hz-sec rp-hz-blocked"><div class="rp-hz-title">前往「' +
      rpEsc(dest) + '」必须经过危险区域：' + rpEsc(reasons.join('、')) + '</div>' +
      '<div class="rp-hz-tip">这些 block 边是基线路线的必经边，无法绕开；未给出穿过危险区域的路线。</div>' +
      hz.blocking.map(hazardItemHtml).join('') + '</div>';
  }
  html += hazardSectionHtml('rp-hz-avoided', '已为您避开（不在当前路线中）', hz.avoided,
    '以下危险边已通过绕行排除，不会出现在路线上。');
  html += hazardSectionHtml('rp-hz-on', '途经危险（已计入选路）', hz.onRoute,
    '当前路线确实经过这些边：block 为绕不开的必经危险，avoid 为加权后仍最优的选择。');
  html += hazardSectionHtml('rp-hz-notice', '沿途提示（不影响选路）', hz.notice,
    'notice 档只做提示，不参与选路。');
  if (!html) {
    box.innerHTML = '<div class="rp-hz-note">本次路线未命中生效危险。</div>';
    return;
  }
  box.innerHTML = html;
}

// 把视图平移到某个节点（保持当前缩放），用于在右栏点击列表项时定位
function focusRouteNode(id) {
  var n = PATH_GRAPH && PATH_GRAPH.nodes[id];
  if (!n) return;
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - n.x * scale;
  translateY = rect.height / 2 - n.y * scale;
  applyTransform();
}
// 路径列表中点击节点 → 平移到该节点 + 图上醒目高亮（需求⑬）：
// 清除上一次的闪烁标记，节点本身加 path-node-flash（放大+橙色描边+光晕），
// 并在其位置叠加一个脉冲圆环动画标记，确保视觉上非常明显。
var _pathFlashNodeId = null;
function focusPathNode(id) {
  var n = PATH_GRAPH && PATH_GRAPH.nodes[id];
  if (!n) return;
  // 清除上一次高亮
  if (_pathFlashNodeId) {
    document.querySelectorAll('.layer_topo_node.path-node-flash').forEach(function(g) {
      g.classList.remove('path-node-flash');
    });
    _pathFlashNodeId = null;
  }
  // 平移到节点
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - n.x * scale;
  translateY = rect.height / 2 - n.y * scale;
  applyTransform();
  // 节点本身高亮（确保拓扑节点图层可见）
  ensureLayer('topo_node', true);
  var found = false;
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {
    var f = g.getAttribute('data-info');
    if (!f) return;
    try {
      var nd = JSON.parse(f);
      if (nd.id === id) { g.classList.add('path-node-flash'); found = true; }
    } catch(e) {}
  });
  _pathFlashNodeId = id;
  // 叠加脉冲圆环标记（独立于节点 SVG，避免样式冲突）
  var svg = document.getElementById('main-svg');
  var ring = document.getElementById('path-flash-ring');
  if (!ring) {
    ring = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    ring.setAttribute('id', 'path-flash-ring');
    svg.appendChild(ring);
  }
  ring.innerHTML = '';
  var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('cx', n.x); c.setAttribute('cy', n.y); c.setAttribute('r', 6);
  c.setAttribute('class', 'pulse');
  ring.appendChild(c);
  var c2 = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c2.setAttribute('cx', n.x); c2.setAttribute('cy', n.y); c2.setAttribute('r', 7);
  c2.setAttribute('fill', 'rgba(255,87,34,0.28)');
  ring.appendChild(c2);
  // 若节点已加载但其坐标与 PATH_GRAPH 一致，ring 与其自然重合
}
// 详情面板点击「拓扑节点」链接 → 居中定位 + 醒目高亮该拓扑节点（需求⑮）
function focusTopoNode(id) {
  var n = PATH_GRAPH && PATH_GRAPH.nodes[id];
  if (!n) { alert('未找到拓扑节点 ' + id); return; }
  if (scale < 2.5) scale = 2.5;
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - n.x * scale;
  translateY = rect.height / 2 - n.y * scale;
  applyTransform(); setZoomInfo();
  ensureLayer('topo_node', true); ensureLayer('topo_edge', true);
  document.querySelectorAll('.layer_topo_node').forEach(function(g) {
    var f = g.getAttribute('data-info'); if (!f) return;
    try { if (JSON.parse(f).id === id) g.classList.add('selected'); } catch (e) {}
  });
  // 叠加脉冲圆环标记，确保醒目
  flashAt(n.x, n.y, id);
}
// 点击详情面板中任一 ID 链接（data-mid）→ 居中定位 + 醒目高亮对应元素：
// 拓扑节点走 PATH_GRAPH，地图要素走 MAP_CENTERS（svg 像素中心）。
// 高亮 = selected 描边 + 脉冲圆环标记（与路径节点点击一致，需求⑬/⑮）。
function centerById(id) {
  var n = PATH_GRAPH && PATH_GRAPH.nodes[id];
  if (n) { focusTopoNode(id); return; }
  var c = (typeof MAP_CENTERS !== 'undefined') && MAP_CENTERS[id];
  if (!c) { alert('未找到要素 ' + id); return; }
  if (scale < 2.5) scale = 2.5;
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - c[0] * scale;
  translateY = rect.height / 2 - c[1] * scale;
  applyTransform(); setZoomInfo();
  highlightById(id);
  flashAt(c[0], c[1], id);
}
// 在 svg 坐标 (x,y) 叠加脉冲圆环标记（醒目高亮辅助）
function flashAt(x, y, id) {
  var svg = document.getElementById('main-svg');
  var ring = document.getElementById('path-flash-ring');
  if (!ring) {
    ring = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    ring.setAttribute('id', 'path-flash-ring');
    svg.appendChild(ring);
  }
  ring.innerHTML = '';
  var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', 6);
  c.setAttribute('class', 'pulse');
  ring.appendChild(c);
  var c2 = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c2.setAttribute('cx', x); c2.setAttribute('cy', y); c2.setAttribute('r', 7);
  c2.setAttribute('fill', 'rgba(255,87,34,0.28)');
  ring.appendChild(c2);
}
function highlightById(id) {
  clearHighlight();
  var els = document.querySelectorAll('[data-mid="' + rpEsc(id) + '"]');
  if (!els.length) els = document.querySelectorAll('[data-roomid="' + rpEsc(id) + '"]');
  els.forEach(function(g) {
    g.classList.add('selected');
    var cls = (g.getAttribute('class') || '');
    var m = cls.match(/layer_([A-Za-z0-9_]+)/);
    if (m) ensureLayer(m[1], true);
  });
  var old = document.getElementById('detail-located');
  if (old) old.remove();
  var stat = document.createElement('div');
  stat.id = 'detail-located'; stat.className = 'row';
  stat.innerHTML = '<span>已居中定位</span><span>' + rpEsc(id) + '</span>';
  document.getElementById('detail').appendChild(stat);
}
document.getElementById('detail').addEventListener('click', function(e) {
  var a = e.target.closest('[data-mid]');
  if (!a) return;
  centerById(a.getAttribute('data-mid'));
});

function renderRouteList(result, mode, startId, endId) {
  rpSetMode(mode);
  var sumEl = document.getElementById('route-summary');
  var listEl = document.getElementById('route-steps');
  if (!sumEl || !listEl) return;
  listEl.innerHTML = '';
  var sNode = (PATH_GRAPH.nodes[startId] || {});
  var eNode = (PATH_GRAPH.nodes[endId] || {});
  if (!result) {
    sumEl.className = 'rp-empty';
    sumEl.innerHTML = '<b style="color:#C62828">不可达</b><br>' +
      rpEsc(sNode.label || startId) + ' → ' + rpEsc(eNode.label || endId) +
      '<br>当前模式（' + rpEsc(RP_MODE_NAME[mode] || mode) + '）下无满足导航规则的连通路径。';
    return;
  }
  // 逐段几何距离（取自拓扑边 distance，不含门型惩罚）
  var em = rpEdgeMap();
  var segs = [], geo = 0, nXf = 0;
  for (var i = 0; i < result.edges.length; i++) {
    var ed = em[result.edges[i]] || {};
    var dd = Number(ed.distance) || 0;
    geo += dd;
    segs.push({ d: dd, cum: geo, e: ed });
    if (ed.crossFloor) nXf++;
  }
  var nDoor = 0;
  result.nodes.forEach(function(id) {
    var n = PATH_GRAPH.nodes[id];
    if (n && n.type === 'doorway') nDoor++;
  });
  // ---- 摘要 ----
  var noteHtml = '';
  if (result.note === 'door_fallback') {
    noteHtml = '<div class="rp-note">门回退：房间最佳门未接入路网，已放宽为可用任意附属门。</div>';
  } else if (result.note === 'wall_fallback') {
    noteHtml = '<div class="rp-note">桥边回退：两端仅靠穿墙走廊边连通，为保证可达而保留（可通行区数据待修复项）。</div>';
  } else if (result.note === 'cross_roundtrip') {
    noteHtml = '<div class="rp-note">跨层绕行：同层走廊对视障不可达，路线经电梯下至其他楼层、穿过该楼层后返回。</div>';
  }
  var costHtml = '';
  if (Math.abs(result.distance - geo) > 0.05) {
    costHtml = '<div class="rp-kv"><span>规划代价(含门型惩罚)</span><b>' + result.distance.toFixed(2) + '</b></div>';
  }
  sumEl.className = '';
  sumEl.innerHTML =
    '<div class="rp-od">' + rpEsc(sNode.label || startId) + ' → ' + rpEsc(eNode.label || endId) + '</div>' +
    '<div class="rp-kv"><span>总长度</span><b>' + geo.toFixed(1) + ' m</b></div>' +
    '<div class="rp-kv"><span>途经节点</span><b>' + result.nodes.length + ' 个</b></div>' +
    '<div class="rp-kv"><span>经过门</span><b>' + nDoor + ' 扇</b></div>' +
    (nXf ? '<div class="rp-kv"><span>跨层段</span><b>' + nXf + ' 段</b></div>' : '') +
    costHtml + noteHtml;
  // ---- 逐节点清单 ----
  var last = result.nodes.length - 1;
  var html = '';
  result.nodes.forEach(function(id, idx) {
    var n = PATH_GRAPH.nodes[id] || {};
    var meta = RP_TYPE_META[n.type] || { name: n.type || '节点', color: '#607D8B' };
    var color = (idx === 0) ? '#2E7D32' : (idx === last ? '#C62828' : meta.color);
    var name = n.label || '';
    if (!name) {
      if (n.type === 'doorway') name = RP_DOOR_NAME[n.doorType] || '门口';
      else if (n.facilityType) name = RP_FAC_NAME[n.facilityType] || meta.name;
      else name = meta.name;
    }
    var bits = [];
    if (n.type === 'doorway' && n.doorType) bits.push(RP_DOOR_NAME[n.doorType] || n.doorType);
    if (n.facilityType) bits.push(RP_FAC_NAME[n.facilityType] || n.facilityType);
    bits.push('F' + (n.floor == null ? '?' : n.floor));
    bits.push(rpEsc(id));
    var segHtml;
    if (idx === 0) {
      segHtml = '<span class="rp-cum">起点</span>';
    } else {
      var sg = segs[idx - 1] || { d: 0, cum: 0, e: {} };
      var xf = '';
      if (sg.e.crossFloor) {
        var fname = RP_FAC_NAME[sg.e.type] || '跨层';
        xf = '<span class="rp-xf">跨层·' + rpEsc(fname) + '</span><br>';
      }
      segHtml = xf + '+' + sg.d.toFixed(1) + ' m<span class="rp-cum">Σ ' + sg.cum.toFixed(1) + ' m</span>';
    }
    html += '<li data-nid="' + rpEsc(id) + '" title="点击在图上定位该节点">' +
      '<span class="rp-idx" style="background:' + color + '">' + (idx + 1) + '</span>' +
      '<span class="rp-body">' +
        '<span class="rp-name">' + rpEsc(name) + '</span>' +
        '<span class="rp-meta"><span class="rp-tag" style="background:' + meta.color + '">' +
          rpEsc(meta.name) + '</span>' + bits.join(' · ') + '</span>' +
      '</span>' +
      '<span class="rp-seg">' + segHtml + '</span>' +
      '</li>';
  });
  listEl.innerHTML = html;
  Array.prototype.forEach.call(listEl.children, function(li) {
    li.addEventListener('click', function() {
      Array.prototype.forEach.call(listEl.children, function(x) { x.classList.remove('active'); });
      li.classList.add('active');
      focusPathNode(li.getAttribute('data-nid'));
    });
  });
}

function recomputePathIfReady() {
  var mode = (document.getElementById('path-mode-select') || {}).value || 'normal';
  rpSetMode(mode);
  if (pathStart && pathEnd) {
    runPath(pathStart, pathEnd);
  }
}

function runPath(startId, endId) {
  var mode = (document.getElementById('path-mode-select') || {}).value || 'normal';
  var result = dijkstra(startId, endId, mode);
  // 危险关系（视障/轮椅等非普通模式）：onRoute / avoided / notice / blocking
  var hz = routeHazardInfo(result, mode, startId, endId);
  // 供自动化/排查读取：当前路线与危险关系
  window.LAST_ROUTE = result;
  window.LAST_ROUTE_HAZARDS = hz;
  var out = document.getElementById('path-result');
  var hint = document.getElementById('path-hint');
  if (!result) {
    var blockedTxt = '';
    if (hz.blocking && hz.blocking.length) {
      var reasons = [];
      hz.blocking.forEach(function(h){
        var t = h.reason || HAZARD_TYPE_CN[h.type] || '危险';
        if (reasons.indexOf(t) < 0) reasons.push(t);
      });
      blockedTxt = '：必须经过危险区域（' + reasons.join('、') + '）';
    }
    if (out) out.textContent = '不可达' + (blockedTxt || '（当前模式下无连通路径）');
    clearPathVisual();
    markNodeClass(startId, 'path-start');
    markNodeClass(endId, 'path-end');
    renderRouteList(null, mode, startId, endId);
    renderRouteHazards(hz, mode, startId, endId);
    return;
  }
  drawPath(result);
  renderRouteList(result, mode, startId, endId);
  renderRouteHazards(hz, mode, startId, endId);
  var sn = PATH_GRAPH.nodes[startId] || {};
  var en = PATH_GRAPH.nodes[endId] || {};
  if (out) {
    var noteTxt = '';
    if (result.note === 'door_fallback') noteTxt = '（门回退：最佳门未接入路网）';
    else if (result.note === 'wall_fallback') noteTxt = '（桥边回退：穿墙走廊边为保连通保留）';
    else if (result.note === 'cross_roundtrip') noteTxt = '（跨层绕行：经电梯下至其他楼层后返回）';
    out.textContent = '路径 ' + result.nodes.length + ' 节点 · ' +
      result.distance.toFixed(1) + ' m · ' +
      (sn.label || startId) + ' → ' + (en.label || endId) + noteTxt;
  }
  if (hint) {
    hint.textContent = result.note === 'wall_fallback'
      ? '红色虚线为「桥边回退」保留的穿墙走廊边（可通行区数据待修复）'
      : '可继续点选新的起点，或点「清除路径」';
  }
}

// 挂到原有节点点击逻辑：pathMode 下优先选点
var _origPathClickInstalled = false;
function installPathClick() {
  if (_origPathClickInstalled) return;
  _origPathClickInstalled = true;
  wrapper.addEventListener('click', function(e) {
    if (!pathMode) return;
    var t = e.target.closest('[data-info]');
    if (!t) return;
    var info = t.getAttribute('data-info');
    var d; try { d = JSON.parse(info); } catch (err) { return; }
    if (d.kind !== 'node' || !d.id) return;
    e.stopPropagation();
    if (!pathStart || (pathStart && pathEnd)) {
      pathStart = d.id;
      pathEnd = null;
      clearPathVisual();
      markNodeClass(pathStart, 'path-start');
      var h = document.getElementById('path-hint');
      if (h) h.textContent = '已选起点，请点击终点…';
      var r = document.getElementById('path-result');
      if (r) r.textContent = '';
      resetRoutePanel();
      return;
    }
    if (d.id === pathStart) return;
    pathEnd = d.id;
    runPath(pathStart, pathEnd);
  }, true); // capture 优先于详情点击
}
installPathClick();

// ---- 拓扑边编辑：双击节点加边 / 选中边删除 / 保存 GeoJSON ----
// 增删边直接修改嵌入的完整 GeoJSON（#full-geojson-data）并在图上实时反映，
// 点「保存 GeoJSON」用 File System Access API 写回文件（不支持则下载完整文件）。
var FULL_DATA = null;
(function(){
  var el = document.getElementById('full-geojson-data');
  if (el) { try { FULL_DATA = JSON.parse(el.textContent); } catch(e) { console.warn('full geojson parse failed', e); } }
})();
var edgePick = [];            // 双击选点：首个节点 id
var selectedEdgeId = null;    // 当前单击选中的拓扑边 id
var selectedEdgeEl = null;    // 对应的 SVG 元素
function edgeHint(msg){ var h = document.getElementById('edge-hint'); if (h) h.textContent = msg; }
function edgeStatus(msg){ var s = document.getElementById('edge-list'); if (s) s.textContent = msg; }
function nextEdgeId(fa, fb){
  if (fa === fb) {
    var max = 0;
    ((FULL_DATA.floors[String(fa)].topology || {}).edges || []).forEach(function(e){
      var m = /F\d+-TE-(\d+)/.exec(e.id || ''); if (m) max = Math.max(max, parseInt(m[1], 10));
    });
    return 'F' + fa + '-TE-' + ('0000' + (max + 1)).slice(-4);
  }
  var max = 0;
  (FULL_DATA.crossFloorEdges || []).forEach(function(e){
    var m = /FX-XE-(\d+)/.exec(e.id || ''); if (m) max = Math.max(max, parseInt(m[1], 10));
  });
  return 'FX-XE-' + ('0000' + (max + 1)).slice(-4);
}
function edgeExists(a, b){
  for (var fk in FULL_DATA.floors) {
    var es = (FULL_DATA.floors[fk].topology || {}).edges || [];
    for (var i = 0; i < es.length; i++)
      if ((es[i].from === a && es[i].to === b) || (es[i].from === b && es[i].to === a)) return true;
  }
  var xs = FULL_DATA.crossFloorEdges || [];
  for (var j = 0; j < xs.length; j++)
    if ((xs[j].from === a && xs[j].to === b) || (xs[j].from === b && xs[j].to === a)) return true;
  return false;
}
function drawEdgeElement(edge){
  var a = PATH_GRAPH.nodes[edge.from], b = PATH_GRAPH.nodes[edge.to];
  if (!a || !b) return null;
  var det = {title: '导航边 ' + edge.id, rows: [
    ['起始', {_l: edge.from, t: edge.from}], ['终点', {_l: edge.to, t: edge.to}],
    ['距离', edge.distance.toFixed(2) + ' m'],
    ['预估时间', (edge.estimatedTime || 0).toFixed(1) + ' s'],
    ['可达等级', edge.accessibilityLevel], ['风险等级', edge.riskLevel],
    ['可步行', '是'], ['轮椅', '是'], ['视障', '是']
  ]};
  var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  g.setAttribute('class', 'layer_topo_edge');
  g.setAttribute('data-info', JSON.stringify({tip: '导航边 ' + edge.id + ' 距离 ' + edge.distance.toFixed(1) + 'm', detail: det, from: edge.from, to: edge.to, id: edge.id, kind: 'edge'}));
  var p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  p.setAttribute('d', 'M ' + a.x + ' ' + a.y + ' L ' + b.x + ' ' + b.y);
  g.appendChild(p);
  svg.appendChild(g);
  return g;
}
// 双击拓扑节点：第一次选起点，第二次选终点自动加边
wrapper.addEventListener('dblclick', function(e) {
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try { d = JSON.parse(info); } catch (err) { return; }
  if (d.kind !== 'node' || !d.id) return;
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; } // 取消挂起的单击选中
  e.preventDefault(); e.stopPropagation();
  ensureLayer('topo_node', true); ensureLayer('topo_edge', true);
  var nd = PATH_GRAPH.nodes[d.id];
  if (!nd) { edgeHint('节点不在图中'); return; }
  if (edgePick.length === 0) {
    edgePick.push(d.id);
    markNodeClass(d.id, 'path-start');
    edgeHint('已选第一个节点（' + (nd.label || d.id) + '），请双击第二个节点…');
    return;
  }
  var a = edgePick[0];
  edgePick = [];
  clearPathVisual();
  if (d.id === a) { edgeHint('两个节点相同，请重新双击选择'); return; }
  if (edgeExists(a, d.id)) { edgeHint('这两个节点已存在拓扑边，请重新选择'); return; }
  var na = PATH_GRAPH.nodes[a];
  var distM = Math.sqrt((na.mx - nd.mx) * (na.mx - nd.mx) + (na.my - nd.my) * (na.my - nd.my));
  var edge = {
    id: nextEdgeId(na.floor, nd.floor),
    from: a, to: d.id,
    distance: Math.round(distM * 100) / 100,
    estimatedTime: Math.round(distM / 0.8 * 10) / 10,
    accessibilityLevel: 0, riskLevel: 0.5,
    walkable: true, wheelchairAccessible: true, blindAccessible: true,
    manual: true
  };
  if (na.floor === nd.floor) {
    FULL_DATA.floors[String(na.floor)].topology.edges.push(edge);
  } else {
    edge.fromFloor = na.floor; edge.toFloor = nd.floor;
    edge.type = 'manual'; edge.matchedBy = 'manual';
    if (!FULL_DATA.crossFloorEdges) FULL_DATA.crossFloorEdges = [];
    FULL_DATA.crossFloorEdges.push(edge);
  }
  PATH_GRAPH.edges.push({id: edge.id, from: edge.from, to: edge.to, distance: edge.distance, accessibilityLevel: 0, blindAccessible: true, wheelchairAccessible: true, manual: true});
  drawEdgeElement(edge);
  edgeStatus('已添加拓扑边 ' + edge.id + '（' + edge.distance.toFixed(1) + ' m）· 待保存');
  edgeHint('可继续双击两个节点加边，或点「保存 GeoJSON」写回文件');
}, true);
function updateDeleteBtn(){
  var btn = document.getElementById('btn-del-edge');
  if (btn) btn.disabled = !selectedEdgeId;
}
function deleteSelectedEdge(){
  if (!selectedEdgeId) { alert('请先单击选中一条拓扑边'); return; }
  if (!confirm('确定删除拓扑边 ' + selectedEdgeId + ' ？')) return;
  var removed = false;
  for (var fk in FULL_DATA.floors) {
    var es = (FULL_DATA.floors[fk].topology || {}).edges || [];
    for (var i = es.length - 1; i >= 0; i--)
      if (es[i].id === selectedEdgeId) { es.splice(i, 1); removed = true; }
  }
  var xs = FULL_DATA.crossFloorEdges || [];
  for (var j = xs.length - 1; j >= 0; j--)
    if (xs[j].id === selectedEdgeId) { xs.splice(j, 1); removed = true; }
  for (var k = PATH_GRAPH.edges.length - 1; k >= 0; k--)
    if (PATH_GRAPH.edges[k].id === selectedEdgeId) PATH_GRAPH.edges.splice(k, 1);
  if (selectedEdgeEl && selectedEdgeEl.parentNode) selectedEdgeEl.parentNode.removeChild(selectedEdgeEl);
  var del = selectedEdgeId;
  selectedEdgeId = null; selectedEdgeEl = null;
  updateDeleteBtn();
  clearHighlight(); resetDetail();
  edgeStatus(removed ? ('已删除拓扑边 ' + del + ' · 待保存') : ('未找到边 ' + del));
}
// 保存：完整 GeoJSON 写回文件（File System Access API；不支持则下载完整文件）
async function saveGeojson(){
  if (!FULL_DATA) { alert('完整数据未加载，无法保存'); return; }
  var json = JSON.stringify(FULL_DATA, null, 2);
  if (window.showSaveFilePicker) {
    try {
      var handle = await window.showSaveFilePicker({
        suggestedName: 'school_building_01_map_v9.geojson',
        types: [{description: 'GeoJSON', accept: {'application/json': ['.geojson', '.json']}}]
      });
      var w = await handle.createWritable();
      await w.write(json);
      await w.close();
      edgeStatus('已保存 GeoJSON ✔ 建议重渲染 HTML');
      return;
    } catch (err) {
      if (err && err.name === 'AbortError') return;
    }
  }
  var blob = new Blob([json], {type: 'application/json'});
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url; a.download = 'school_building_01_map_v9.geojson'; a.click();
  setTimeout(function(){ URL.revokeObjectURL(url); }, 2000);
  edgeStatus('已下载完整 GeoJSON（请放到 result/ 目录）');
}
updateDeleteBtn();


buildFloorJump(__NFLOORS__, __PERFLOOR__);
applyTransform(); setZoomInfo();

// ===== 危险层（hazards_data）：在现有渲染图上叠加 block / avoid / notice =====
// 数据来源：backend/hazards_data/school-building-01.json，由 render_interactive.py
// 内联为 #hazard-data。页面通过 file:// 打开，禁止 fetch，运行时只读内联 JSON。
// 与地图包的关系：地图包只给几何/拓扑；本层只覆盖运营危险（施工/湿滑/人流等）。
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';
  var LEVEL_STYLE = {
    block:  { color: '#dc2626', width: 4.5, dash: null,  label: 'block 禁行' },
    avoid:  { color: '#ea580c', width: 3.5, dash: '7 5', label: 'avoid 尽量避开' },
    notice: { color: '#7c3aed', width: 3,   dash: '3 4', label: 'notice 仅提示' }
  };
  var RANK = { block: 3, avoid: 2, notice: 1 };
  var FLOOR_CN = { '1': '1F', '2': '2F' };

  var HAZ_BY_ID = {};   // hazardId -> hazard
  var EDGE_LEN = {};    // edgeId -> 米

  function floorLabel(f) { return FLOOR_CN[String(f)] || (String(f) + 'F'); }
  function edgeFloorText(info) {
    if (info.kind === 'crossfloor') return '1F ↔ 2F（跨层边）';
    return info.floor ? floorLabel(info.floor) : '—';
  }
  function fmtTime(v) {
    if (!v) return '';
    var d = new Date(v);
    if (isNaN(d.getTime())) return String(v);
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
      + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  function windowText(h) {
    if (!h.activeFrom && !h.activeUntil) return '长期有效（未设时间窗）';
    return (h.activeFrom ? fmtTime(h.activeFrom) : '立即') + ' 至 '
      + (h.activeUntil ? fmtTime(h.activeUntil) : '长期');
  }
  function appliesText(list) {
    if (!list || !list.length) return '视障';
    return list.map(function (x) { return HAZARD_PEOPLE_CN[x] || x; }).join('、');
  }
  function lengthText(h) {
    var total = 0, n = 0, missing = 0;
    (h.edgeIds || []).forEach(function (eid) {
      var m = EDGE_LEN[eid];
      if (m == null) { missing++; } else { total += m; n++; }
    });
    if (!n) return '未定位' + (missing ? '（' + missing + ' 条边）' : '');
    return '合计 ' + total.toFixed(2) + ' m（' + n + ' 条边'
      + (missing ? '，' + missing + ' 条未定位' : '') + '）';
  }
  function pathEndpoints(d) {
    var re = /[ML]\s*([-\d.]+)[ ,]+([-\d.]+)/g, m, pts = [];
    while ((m = re.exec(d))) pts.push([parseFloat(m[1]), parseFloat(m[2])]);
    return pts;
  }
  // 跨层连接在当前页是一根横跨 1F/2F 的示意线；按危险所在楼层取该端端点画标记
  function crossEndpoint(d, floorId) {
    var pts = pathEndpoints(d);
    if (!pts.length) return null;
    var g = window.GEOX;
    var fi = g ? g.floorKeys.indexOf(String(floorId)) : -1;
    if (!g || fi < 0) return pts[0];
    for (var i = 0; i < pts.length; i++) {
      if (Math.floor(pts[i][1] / g.perFloor) === fi) return pts[i];
    }
    return pts[0];
  }

  function strictest(list) {
    return list.reduce(function (a, h) {
      return (RANK[h.level] || 0) > (RANK[a.level] || 0) ? h : a;
    }, list[0]);
  }

  // 复用页面 GEOX（米制 → SVG 用户坐标），仅 area/point 使用
  function geoToSvg(x, y, floorId) {
    var g = window.GEOX;
    if (!g) return [x, y];
    var fi = g.floorKeys.indexOf(String(floorId));
    if (fi < 0) fi = 0;
    return [
      g.marginX + (x - g.ox) * g.scale,
      fi * g.perFloor + g.titleH + g.marginY + (g.oy - y) * g.scale
    ];
  }

  // 建立 edgeId -> 已有拓扑边几何 的索引（只扫边类图层，避免全量遍历）
  function buildEdgeIndex() {
    var idx = {};
    var sel = '#main-svg .layer_topo_edge, #main-svg .layer_topo_edge_titi, #main-svg .layer_crossfloor';
    document.querySelectorAll(sel).forEach(function (g) {
      var raw;
      try { raw = JSON.parse(g.getAttribute('data-info')); } catch (e) { return; }
      if (!raw) return;
      var meta = raw.detail || {};
      // 拓扑边 id 在顶层；跨层边（crossfloor）的 id 与楼层元数据在 detail 里
      var id = raw.id || raw._edit_pid || meta._edit_pid;
      if (!id) return;
      var floorNum = raw._edit_floor != null ? raw._edit_floor : meta._edit_floor;
      var p = g.querySelector('path');
      if (!p) return;
      var d = p.getAttribute('d');
      if (!d) return;
      var len = null;
      var rows = (raw.detail && raw.detail.rows) || [];
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        if (r && (r[0] === '距离' || r[0] === '长度')) {
          var m = parseFloat(r[1]);
          if (isFinite(m)) len = m;
          break;
        }
      }
      idx[id] = {
        id: id, d: d, len: len, kind: raw.kind,
        floor: floorNum != null ? String(floorNum) : '',
        el: g
      };
    });
    return idx;
  }

  function buildEdgeInfo(eid, info, list) {
    var top = strictest(list);
    var st = LEVEL_STYLE[top.level] || LEVEL_STYLE.notice;
    var lenTxt = info.len != null ? info.len.toFixed(2) + ' m' : '长度未知';
    var lines = ['危险边 ' + eid + ' · '
      + (list.length > 1 ? list.length + ' 条危险（最严 ' + st.label + '）' : st.label)];
    list.forEach(function (h) {
      lines.push('· ' + (HAZARD_TYPE_CN[h.type] || h.type) + '：' + (h.reason || ''));
    });
    lines.push('影响边长度 ' + lenTxt + ' · ' + edgeFloorText(info));
    if (info.kind === 'crossfloor') {
      lines.push('跨层边：图上在 ' + floorLabel(top.floorId) + ' 端以菱形标注');
    }
    var rows = [
      ['楼层', edgeFloorText(info)],
      ['本边长度', lenTxt],
      ['危险条数', list.length + ' 条']
    ];
    if (info.kind === 'crossfloor') {
      rows.push(['标注端', floorLabel(top.floorId) + '（跨层示意图仅标该端）']);
    }
    return {
      tip: lines.join('\\n'),
      detail: {
        title: '危险边 ' + eid,
        rows: rows
      },
      kind: 'hazardEdge',
      id: 'HZ-EDGE:' + eid,
      edgeId: eid,
      hazardIds: list.map(function (h) { return h.hazardId; })
    };
  }

  function buildZoneInfo(h) {
    var st = LEVEL_STYLE[h.level] || LEVEL_STYLE.notice;
    var label = h.targetType === 'area' ? '危险区域' : '危险点位';
    return {
      tip: [label + ' ' + h.hazardId + ' · ' + st.label,
        '· ' + (HAZARD_TYPE_CN[h.type] || h.type) + '：' + (h.reason || ''),
        '影响边 ' + (h.edgeIds || []).length + ' 条 · ' + lengthText(h)].join('\\n'),
      detail: { title: label + ' ' + h.hazardId, rows: [] },
      kind: 'hazardZone',
      id: 'HZ-ZONE:' + h.hazardId,
      hazardIds: [h.hazardId]
    };
  }

  function showHazardDetail(d) {
    var box = document.getElementById('detail');
    if (!box) return;
    function row(label, value) {
      return '<div class="row"><span>' + rpEsc(label) + '</span><span>' + rpEsc(value) + '</span></div>';
    }
    var h = '<h4>' + rpEsc((d.detail && d.detail.title) || '危险详情') + '</h4>';
    ((d.detail && d.detail.rows) || []).forEach(function (r) { h += row(r[0], r[1]); });
    var ids = d.hazardIds || [];
    ids.forEach(function (hid, i) {
      var hz = HAZ_BY_ID[hid];
      if (!hz) return;
      var st = LEVEL_STYLE[hz.level] || LEVEL_STYLE.notice;
      h += '<div class="hz-block">';
      if (ids.length > 1) h += '<div class="hz-block-title">危险 ' + (i + 1) + ' / ' + ids.length + '</div>';
      h += '<div class="row"><span>档位</span><span><span class="hz-badge ' + rpEsc(hz.level) + '">'
        + rpEsc(st.label) + '</span></span></div>';
      h += row('类型', HAZARD_TYPE_CN[hz.type] || hz.type || '其他');
      h += row('理由', hz.reason || '');
      h += row('目标形态', hz.targetType === 'area' ? '区域 area'
        : hz.targetType === 'point' ? '点位 point' : '边 edge');
      h += row('影响边', (hz.edgeIds || []).join('、'));
      h += row('影响长度', lengthText(hz));
      h += row('生效时间', windowText(hz));
      h += row('适用人群', appliesText(hz.appliesTo));
      h += row('视障可通行', hz.blindAccessible ? '是' : '否');
      if (hz.penaltyM != null) h += row('绕行惩罚', hz.penaltyM + ' m');
      h += row('状态/来源', (hz.status || '—') + ' / ' + (hz.source || '—'));
      h += row('危险 ID', hz.hazardId + (hz.note ? '（' + hz.note + '）' : ''));
      h += '</div>';
    });
    box.innerHTML = h;
    currentDetail = d;
  }

  function build() {
    var dataEl = document.getElementById('hazard-data');
    var svg = document.getElementById('main-svg');
    if (!dataEl || !svg) return;

    var DATA = HAZARD_DATA;
    if (!DATA) {
      try { DATA = JSON.parse(dataEl.textContent); }
      catch (e) { console.warn('[hazard] 内联数据解析失败', e); return; }
    }

    var all = DATA.hazards || [];
    all.forEach(function (h) { if (h && h.hazardId) HAZ_BY_ID[h.hazardId] = h; });
    // 只有 active 参与展示；pending/resolved/rejected 不当作现行危险
    var hazards = all.filter(function (h) { return h && h.status === 'active'; });

    var edgeIdx = buildEdgeIndex();
    Object.keys(edgeIdx).forEach(function (id) {
      if (edgeIdx[id].len != null) EDGE_LEN[id] = edgeIdx[id].len;
    });

    var byEdge = {};
    hazards.forEach(function (h) {
      (h.edgeIds || []).forEach(function (eid) {
        (byEdge[eid] || (byEdge[eid] = [])).push(h);
      });
    });
    var edgeIds = Object.keys(byEdge);
    // 轻微危险先画，严重危险后画，重叠时最严档位在最上层
    edgeIds.sort(function (a, b) {
      return (RANK[strictest(byEdge[a]).level] || 0) - (RANK[strictest(byEdge[b]).level] || 0);
    });

    var layer = document.createElementNS(NS, 'g');
    layer.setAttribute('id', 'hazard-layer');
    layer.setAttribute('class', 'layer_hazard');

    var missing = [];
    edgeIds.forEach(function (eid) {
      var info = edgeIdx[eid];
      if (!info) { missing.push(eid); return; }
      var list = byEdge[eid];
      var top = strictest(list);
      var st = LEVEL_STYLE[top.level] || LEVEL_STYLE.notice;

      var item = document.createElementNS(NS, 'g');
      item.setAttribute('class', 'hazard-item hz-edge');
      if (info.floor) item.setAttribute('data-floor', info.floor);
      item.setAttribute('data-info', JSON.stringify(buildEdgeInfo(eid, info, list)));

      if (info.kind === 'crossfloor') {
        var ep = crossEndpoint(info.d, top.floorId);
        if (!ep) { return; }
        var x = ep[0], y = ep[1];
        var dia = document.createElementNS(NS, 'path');
        dia.setAttribute('class', 'hz-cross-vis');
        dia.setAttribute('d', 'M ' + x + ' ' + (y - 8) + ' L ' + (x + 8) + ' ' + y
          + ' L ' + x + ' ' + (y + 8) + ' L ' + (x - 8) + ' ' + y + ' Z');
        dia.setAttribute('fill', st.color);
        dia.setAttribute('fill-opacity', '0.85');
        dia.setAttribute('stroke', st.color);
        dia.setAttribute('stroke-width', '1.5');
        var chit = document.createElementNS(NS, 'circle');
        chit.setAttribute('class', 'hz-cross-hit');
        chit.setAttribute('cx', x);
        chit.setAttribute('cy', y);
        chit.setAttribute('r', '12');
        item.appendChild(dia);
        item.appendChild(chit);
        layer.appendChild(item);
        return;
      }

      var vis = document.createElementNS(NS, 'path');
      vis.setAttribute('class', 'hz-vis');
      vis.setAttribute('d', info.d);
      vis.setAttribute('stroke', st.color);
      vis.setAttribute('stroke-width', st.width);
      if (st.dash) vis.setAttribute('stroke-dasharray', st.dash);

      var hit = document.createElementNS(NS, 'path');
      hit.setAttribute('class', 'hz-hit');
      hit.setAttribute('d', info.d);

      item.appendChild(vis);
      item.appendChild(hit);
      layer.appendChild(item);
    });

    // area / point 展示形态：数据里有就按区域/点画；边线仍由上面的 edgeIds 叠加
    hazards.forEach(function (h) {
      if (h.targetType !== 'area' && h.targetType !== 'point') return;
      if (!h.geometry || !h.geometry.coordinates) return;
      var st = LEVEL_STYLE[h.level] || LEVEL_STYLE.notice;
      var item = document.createElementNS(NS, 'g');
      item.setAttribute('class', 'hazard-item hz-zone');
      item.setAttribute('data-floor', String(h.floorId || ''));
      item.setAttribute('data-info', JSON.stringify(buildZoneInfo(h)));

      if (h.targetType === 'area' && h.geometry.type === 'Polygon') {
        var ring = h.geometry.coordinates[0] || [];
        var pts = ring.map(function (p) {
          var s = geoToSvg(p[0], p[1], h.floorId);
          return s[0].toFixed(2) + ',' + s[1].toFixed(2);
        }).join(' ');
        var poly = document.createElementNS(NS, 'polygon');
        poly.setAttribute('class', 'hz-area');
        poly.setAttribute('points', pts);
        poly.setAttribute('fill', st.color);
        poly.setAttribute('fill-opacity', '0.18');
        poly.setAttribute('stroke', st.color);
        poly.setAttribute('stroke-width', '1.6');
        if (st.dash) poly.setAttribute('stroke-dasharray', st.dash);
        item.appendChild(poly);
      } else if (h.targetType === 'point') {
        var pt = h.geometry.type === 'LineString' ? h.geometry.coordinates[0]
          : (h.geometry.type === 'Point' ? h.geometry.coordinates : h.geometry.coordinates[0]);
        var sp = geoToSvg(pt[0], pt[1], h.floorId);
        var c = document.createElementNS(NS, 'circle');
        c.setAttribute('class', 'hz-point');
        c.setAttribute('cx', sp[0].toFixed(2));
        c.setAttribute('cy', sp[1].toFixed(2));
        c.setAttribute('r', '7');
        c.setAttribute('fill', st.color);
        c.setAttribute('fill-opacity', '0.22');
        c.setAttribute('stroke', st.color);
        c.setAttribute('stroke-width', '2');
        item.appendChild(c);
        var dot = document.createElementNS(NS, 'circle');
        dot.setAttribute('class', 'hz-point-dot');
        dot.setAttribute('cx', sp[0].toFixed(2));
        dot.setAttribute('cy', sp[1].toFixed(2));
        dot.setAttribute('r', '2.5');
        dot.setAttribute('fill', st.color);
        item.appendChild(dot);
      }
      layer.appendChild(item);
    });

    svg.appendChild(layer);

    // 与图层勾选框同步初始状态（默认勾选=显示）
    var cb = document.querySelector('#layerControls input[onchange*="hazard"]');
    layer.style.display = (cb && !cb.checked) ? 'none' : '';

    var stat = document.getElementById('hazard-stat');
    if (stat) {
      stat.textContent = '已加载 ' + hazards.length + ' 条生效危险 · 覆盖 '
        + edgeIds.length + ' 条边 · '
        + (missing.length ? missing.length + ' 条边未定位' : '边全部定位')
        + ' · 数据版本 ' + (DATA.hazardVersion || '—');
    }
    if (missing.length) console.warn('[hazard] 以下 edgeId 在本页 SVG 中未定位：', missing);
    window.HAZARD_DEBUG = {
      data: DATA, byEdge: byEdge, edgeIdx: edgeIdx, missing: missing,
      hazards: hazards, layer: layer
    };
    window.showHazardDetail = showHazardDetail;
  }

  // GEOX 由页面尾部的区域标注脚本定义；等 DOM 解析完成后再构建危险层
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();

