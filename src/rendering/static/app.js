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
  'topo_node','topo_edge','topo_edge_titi','crossfloor','risk','ramp','tactile','material','fingerprint','beacon','coverage'];
// ---- 方案模式开关（全局 / 测试路线）：切换信标部署/指纹采集/三点覆盖三套数据 ----
var CUR_MODE = 'global';
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
    if (Number(e.accessibilityLevel) === 999) return false;
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
function buildPathAdj(mode, doorFilter, allowWall) {
  var nodes = PATH_GRAPH.nodes;
  var infraTds = PATH_GRAPH.infraDoorwayIds || [];
  var infraTdSet = {};
  infraTds.forEach(function(id){ infraTdSet[id] = 1; });
  var adj = {};
  (PATH_GRAPH.edges || []).forEach(function(e) {
    if (!edgeAllowed(e, mode)) return;
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
    if (!adj[a]) adj[a] = [];
    if (!adj[b]) adj[b] = [];
    adj[a].push({ to: b, w: w, id: e.id });
    adj[b].push({ to: a, w: w, id: e.id });
  });
  return adj;
}

// 核心 Dijkstra（应用规则 1 中间节点白名单 + 规则 3 门穿透防护）
function dijkstraCore(startId, endId, mode, adj) {
  var nodes = PATH_GRAPH.nodes;
  // 规则 1：同层禁 facility 中转；跨层允许 facility 中转（电梯/楼梯用于跨层）
  // A2：唯一来源 constants.py，由 build_path_rules_js 序列化注入，禁止内嵌常量。
  var _rules = (PATH_GRAPH && PATH_GRAPH.rules) || {};
  var _midList = isSameFloor(startId, endId)
    ? (_rules.midTypesSameFloor || [])
    : (_rules.midTypesCrossFloor || []);
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

function dijkstra(startId, endId, mode) {
  if (!PATH_GRAPH) return null;
  // 三层回退（对齐 route_rules.shortest_path）：
  // 1) 仅用最佳门 + 不穿墙 TI<->TI 边；
  // 2) 若不可达（最佳门未接入路网）回退允许所有门；
  // 3) 仍不可达（穿墙边是桥边）回退纳入穿墙边保连通。
  var sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, false));
  var note = null;
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, false, false));
    if (sp) note = 'door_fallback';
  }
  if (!sp) {
    sp = dijkstraCore(startId, endId, mode, buildPathAdj(mode, true, true));
    if (sp) note = 'wall_fallback';
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
  var sumEl = document.getElementById('route-summary');
  if (sumEl) {
    sumEl.className = 'rp-empty';
    sumEl.innerHTML = RP_EMPTY_HTML;
  }
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
  var out = document.getElementById('path-result');
  var hint = document.getElementById('path-hint');
  if (!result) {
    if (out) out.textContent = '不可达（当前模式下无连通路径）';
    clearPathVisual();
    markNodeClass(startId, 'path-start');
    markNodeClass(endId, 'path-end');
    renderRouteList(null, mode, startId, endId);
    return;
  }
  drawPath(result);
  renderRouteList(result, mode, startId, endId);
  var sn = PATH_GRAPH.nodes[startId] || {};
  var en = PATH_GRAPH.nodes[endId] || {};
  if (out) {
    var noteTxt = '';
    if (result.note === 'door_fallback') noteTxt = '（门回退：最佳门未接入路网）';
    else if (result.note === 'wall_fallback') noteTxt = '（桥边回退：穿墙走廊边为保连通保留）';
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

