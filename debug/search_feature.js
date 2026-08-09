// ===== 语义模糊搜索 + 定位居中 =====
// 该脚本会被注入到交互式 HTML 中（位于主脚本之前），所有依赖的全局函数
// （applyTransform / showDetail / ensureLayer / clearHighlight / toggleLayer /
// rpEsc / allLayers 以及 svg / wrapper / scale / translateX / translateY）
// 均为全局定义，运行时（用户点击）均已就绪。
var SEARCH_LAYERS = ['room', 'corridor', 'lobby', 'activity', 'atrium', 'lobby_elevator', 'lobby_stair',
  'door_swing', 'door_fire', 'door_opening', 'topo_node', 'stairs', 'elevator', 'risk', 'ramp', 'tactile', 'material', 'crossfloor'];
var SEARCH_TYPE_CN = {
  room: '房间', corridor: '走道', lobby: '门厅', activity: '活动区', atrium: '中庭',
  lobby_elevator: '电梯前室', lobby_stair: '楼梯前室',
  door_swing: '普通门', door_fire: '防火门', door_opening: '门洞',
  topo_node: '拓扑节点', stairs: '楼梯', elevator: '电梯', risk: '风险点', ramp: '坡道',
  tactile: '盲道', material: '地面材质', crossfloor: '跨层连接'
};
var SEARCH_INDEX = null;

function srBuildIndex() {
  if (SEARCH_INDEX) return SEARCH_INDEX;
  var idx = [];
  document.querySelectorAll('#main-svg [data-info]').forEach(function (el) {
    var cls = el.getAttribute('class') || '';
    var layer = null;
    for (var i = 0; i < SEARCH_LAYERS.length; i++) {
      if (cls.indexOf('layer_' + SEARCH_LAYERS[i]) !== -1) { layer = SEARCH_LAYERS[i]; break; }
    }
    if (!layer) return;
    var raw;
    try { raw = JSON.parse(el.getAttribute('data-info')); } catch (e) { return; }
    var title = (raw.detail && raw.detail.title) || '';
    var parts = [];
    if (raw.id) parts.push(raw.id);
    if (raw.kind) parts.push(raw.kind);
    if (raw.tip) parts.push(raw.tip);
    if (title) parts.push(title);
    if (raw.detail && raw.detail.rows) {
      raw.detail.rows.forEach(function (r) { if (r && r[1] != null) parts.push(String(r[1])); });
    }
    var rid = el.getAttribute('data-roomid');
    if (rid) parts.push(rid);
    idx.push({
      el: el, layer: layer, text: parts.join(' '),
      disp: title || (raw.id || ''),
      id: raw.id || '', rid: rid || ''
    });
  });
  SEARCH_INDEX = idx;
  return idx;
}

function srFloorFromId(s) {
  s = String(s || '');
  var up = s.toUpperCase();
  var i = up.indexOf('F');
  while (i >= 0) {
    var num = '';
    var j = i + 1;
    while (j < s.length && s[j] >= '0' && s[j] <= '9') { num += s[j]; j++; }
    if (num) return num;
    i = up.indexOf('F', i + 1);
  }
  return '';
}

// 模糊打分：子串优先（高权重），否则逐字符子序列匹配（按间隔惩罚）
function srScore(q, text) {
  if (!q) return -1;
  text = (text || '').toLowerCase();
  q = q.toLowerCase().trim();
  if (!q) return -1;
  var p = text.indexOf(q);
  if (p >= 0) return 1000 - p - text.length * 0.05;
  var ti = 0, gaps = 0, last = -1, matched = 0;
  for (var i = 0; i < q.length; i++) {
    var ch = q[i], found = -1;
    for (var j = ti; j < text.length; j++) { if (text[j] === ch) { found = j; break; } }
    if (found < 0) return -1;
    if (last >= 0) gaps += (found - last - 1);
    last = found; ti = found + 1; matched++;
  }
  if (matched < q.length) return -1;
  return 400 - gaps - matched;
}

function srRun(q, dropdown) {
  var idx = srBuildIndex();
  var scored = [];
  for (var i = 0; i < idx.length; i++) {
    var s = srScore(q, idx[i].text);
    if (s > 0) scored.push({ e: idx[i], s: s });
  }
  scored.sort(function (a, b) { return b.s - a.s; });
  scored = scored.slice(0, 12);
  if (!scored.length) {
    dropdown.innerHTML = '<div class="search-empty">未找到匹配「' + rpEsc(q) + '」的元素</div>';
    dropdown.classList.add('show');
    return;
  }
  var html = '';
  scored.forEach(function (it, k) {
    var e = it.e;
    var fl = srFloorFromId(e.id || e.rid);
    html += '<div class="sr-item" data-k="' + k + '">'
      + '<div class="sr-title">' + rpEsc(e.disp) + '</div>'
      + '<div class="sr-meta"><span class="sr-tag">' + rpEsc(SEARCH_TYPE_CN[e.layer] || e.layer) + '</span>'
      + (e.id ? rpEsc(e.id) + ' · ' : '') + (fl ? ('F' + fl + ' · ') : '') + '匹配度 ' + Math.round(it.s) + '</div>'
      + '</div>';
  });
  dropdown.innerHTML = html;
  dropdown.classList.add('show');
  var items = dropdown.querySelectorAll('.sr-item');
  for (var n = 0; n < items.length; n++) {
    (function (li, entry) {
      li.addEventListener('mousedown', function (ev) {
        ev.preventDefault();
        srLocate(entry.e.el);
        dropdown.classList.remove('show');
        var inp = document.getElementById('search-input');
        if (inp) inp.blur();
      });
    })(items[n], scored[n]);
  }
}

function srEnsureVisible(el) {
  var cls = (el.getAttribute('class') || '');
  for (var i = 0; i < allLayers.length; i++) {
    var name = allLayers[i];
    if (cls.indexOf('layer_' + name) !== -1) {
      var cb = document.querySelector('#layerControls input[onchange*="' + name + '"]');
      if (cb && !cb.checked) { cb.checked = true; toggleLayer(name, true); }
    }
  }
  el.style.display = '';
}

var _srHi = null;
function srClearHi() {
  if (_srHi && _srHi.parentNode) { _srHi.parentNode.removeChild(_srHi); _srHi = null; }
}
function srHighlight(cx, cy) {
  srClearHi();
  var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  var c1 = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c1.setAttribute('cx', cx); c1.setAttribute('cy', cy); c1.setAttribute('r', 11);
  c1.setAttribute('fill', 'none'); c1.setAttribute('stroke', '#FF6D00'); c1.setAttribute('stroke-width', 2);
  c1.setAttribute('stroke-dasharray', '5,3');
  g.appendChild(c1);
  var c2 = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c2.setAttribute('cx', cx); c2.setAttribute('cy', cy); c2.setAttribute('r', 4);
  c2.setAttribute('fill', '#FF6D00'); c2.setAttribute('opacity', 0.55);
  g.appendChild(c2);
  svg.appendChild(g);
  _srHi = g;
  setTimeout(srClearHi, 2600);
}

function srLocate(el) {
  srEnsureVisible(el);
  var b = el.getBBox();
  var cx = b.x + b.width / 2, cy = b.y + b.height / 2;
  if (scale < 2.5) scale = 2.5;
  var rect = wrapper.getBoundingClientRect();
  translateX = rect.width / 2 - cx * scale;
  translateY = rect.height / 2 - cy * scale;
  applyTransform(); setZoomInfo();
  srHighlight(cx, cy);
  clearHighlight();
  el.classList.add('selected');
  try {
    var d = JSON.parse(el.getAttribute('data-info'));
    showDetail(d.detail || { title: d.tip || '详情', rows: [] });
  } catch (e) {}
  if ((el.getAttribute('class') || '').indexOf('layer_topo_node') !== -1) {
    ensureLayer('topo_node', true); ensureLayer('topo_edge', true);
  }
}

(function () {
  var inp = document.getElementById('search-input');
  var dropdown = document.getElementById('search-results');
  if (!inp || !dropdown) return;
  inp.addEventListener('input', function () { srRun(inp.value, dropdown); });
  inp.addEventListener('focus', function () { if (inp.value) srRun(inp.value, dropdown); });
  inp.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      var first = dropdown.querySelector('.sr-item');
      if (first) first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('show'); inp.blur();
    }
  });
  document.addEventListener('click', function (e) {
    if (e.target !== inp && !dropdown.contains(e.target)) dropdown.classList.remove('show');
  });
})();
