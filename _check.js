
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
  var delta = e.deltaY > 0 ? 0.9 : 1.1;
  var rect = wrapper.getBoundingClientRect();
  var mx = e.clientX - rect.left, my = e.clientY - rect.top;
  var ns = Math.max(0.15, Math.min(20, scale * delta));
  var ratio = ns / scale;
  translateX = mx - ratio * (mx - translateX);
  translateY = my - ratio * (my - translateY);
  scale = ns; applyTransform(); setZoomInfo();
}, { passive: false });

wrapper.addEventListener('mousedown', function(e) {
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

// ---- 点击查看详情 / 高亮 ----
function clearHighlight() { document.querySelectorAll('.selected').forEach(function(el){ el.classList.remove('selected'); }); }
function showDetail(d) {
  var box = document.getElementById('detail');
  var h = '<h4>' + (d.title || '详情') + '</h4>';
  (d.rows || []).forEach(function(r) {
    h += '<div class="row"><span>' + r[0] + '</span><span>' + r[1] + '</span></div>';
  });
  box.innerHTML = h;
}
wrapper.addEventListener('click', function(e) {
  var t = e.target.closest('[data-info]');
  if (!t) return;
  var info = t.getAttribute('data-info');
  var d; try { d = JSON.parse(info); } catch (err) { return; }
  clearHighlight(); t.classList.add('selected');
  showDetail(d.detail || { title: d.tip || '详情', rows: [] });
  // 拓扑节点 → 高亮相连边
  if (d.kind === 'node' && d.id) {
    document.querySelectorAll('.layer_topo_edge').forEach(function(g) {
      var f = g.getAttribute('data-info');
      if (!f) return;
      try { var ed = JSON.parse(f); if (ed.from === d.id || ed.to === d.id) g.classList.add('selected'); } catch(e){}
    });
  }
});

// ---- 图层开关 ----
var allLayers = ['room','wall','window','stairs','elevator','column',
  'door','topo_node','topo_edge','crossfloor','risk','ramp','tactile','material'];
function toggleLayer(name) {
  document.querySelectorAll('.layer_' + name).forEach(function(el){ el.style.display = el.style.display === 'none' ? '' : 'none'; });
}
function setAll(v) {
  allLayers.forEach(function(n){
    document.querySelectorAll('.layer_' + n).forEach(function(el){ el.style.display = v ? '' : 'none'; });
  });
  document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb){ cb.checked = v; });
}

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
buildFloorJump(2, 929);
applyTransform(); setZoomInfo();
