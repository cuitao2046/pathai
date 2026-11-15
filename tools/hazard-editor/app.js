/**
 * PathAI 视障危险区标注（静态页面，双击 index.html 即可用）。
 *
 * 职责：
 * - Canvas 渲染地图底图（墙/房间/楼梯/电梯）与拓扑边；
 * - 点选/Shift 多选/框选拓扑边，设置盲人可通行与 block·avoid·notice 档位；
 * - 导入已有危险层继续编辑，导出符合
 *   specs/phase-1-indoor-navigation/hazard-layer.schema.json 的 JSON；
 * - 撤销/本地草稿/楼层切换/缩放平移/hover 详情/清单定位。
 *
 * 数据来自 data/map-data.js（由 build_data.py 从发布包生成），不依赖网络与构建。
 */
(function () {
  'use strict';

  var DATA = window.HAZARD_MAP_DATA;
  if (!DATA || !DATA.floors) {
    document.body.innerHTML =
      '<p style="padding:24px;font:14px sans-serif">缺少 data/map-data.js。' +
      '请先运行 <code>python tools/hazard-editor/build_data.py</code> 生成。</p>';
    return;
  }

  var SITE = DATA.siteId;
  var FLOOR_IDS = Object.keys(DATA.floors).sort(function (a, b) {
    return parseFloat(a) - parseFloat(b);
  });
  var LS_KEY = 'pathai.hazard-editor.v1.' + SITE;
  var LEVELS = ['block', 'avoid', 'notice'];
  var RANK = { block: 3, avoid: 2, notice: 1 };
  var LEVEL_LABEL = { block: 'block 禁行', avoid: 'avoid 尽量避开', notice: 'notice 仅提示' };
  var LEVEL_STYLE = {
    block: { color: '#dc2626', width: 4.5, dash: null },
    avoid: { color: '#ea580c', width: 3.5, dash: [7, 5] },
    notice: { color: '#7c3aed', width: 3, dash: [3, 4] }
  };
  var TYPE_LABEL = {
    construction: '施工/围挡',
    wet_floor: '地面湿滑',
    crowd: '人流密集',
    obstacle: '临时障碍',
    glass_door: '玻璃门',
    revolving_door: '旋转门',
    stairs: '楼梯/台阶',
    other: '其他'
  };

  var els = {
    wrap: document.getElementById('canvas-wrap'),
    canvas: document.getElementById('map'),
    tooltip: document.getElementById('tooltip'),
    status: document.getElementById('status'),
    meta: document.getElementById('map-meta'),
    floorTabs: document.getElementById('floor-tabs'),
    selCount: document.getElementById('sel-count'),
    selSummary: document.getElementById('sel-summary'),
    hazCount: document.getElementById('haz-count'),
    hazList: document.getElementById('haz-list'),
    blind: document.getElementById('f-blind'),
    level: document.getElementById('f-level'),
    type: document.getElementById('f-type'),
    reason: document.getElementById('f-reason'),
    spoken: document.getElementById('f-spoken'),
    penalty: document.getElementById('f-penalty'),
    from: document.getElementById('f-from'),
    until: document.getElementById('f-until'),
    apply: document.getElementById('btn-apply'),
    clearSel: document.getElementById('btn-clear-sel'),
    undo: document.getElementById('btn-undo'),
    importBtn: document.getElementById('btn-import'),
    importFile: document.getElementById('file-import'),
    exportBtn: document.getElementById('btn-export'),
    preview: document.getElementById('export-preview'),
    modePan: document.getElementById('mode-pan'),
    modeBox: document.getElementById('mode-box'),
    zoomIn: document.getElementById('btn-zoom-in'),
    zoomOut: document.getElementById('btn-zoom-out'),
    fit: document.getElementById('btn-fit'),
    clearAll: document.getElementById('btn-clear-all'),
    toggleWalk: document.getElementById('toggle-walk'),
    toggleLabels: document.getElementById('toggle-labels'),
    toggleRisk: document.getElementById('toggle-risk')
  };
  var ctx = els.canvas.getContext('2d');

  // ---------------- 状态 ----------------
  var floorId = FLOOR_IDS[0];
  var hazards = [];
  var undoStack = [];
  var selection = new Set();
  var hazardByEdge = new Map();
  var edgeIndex = new Map();
  var view = { k: 6, tx: 0, ty: 0 };
  var canvasW = 0;
  var canvasH = 0;
  var mode = 'pan';
  var dirty = true;
  var hover = null;
  var boxRect = null;
  var drag = null;
  var statusTimer = null;

  // ---------------- 索引 ----------------
  FLOOR_IDS.forEach(function (fid) {
    var f = DATA.floors[fid];
    f.edges.forEach(function (e) {
      edgeIndex.set(e.i, { kind: 'edge', e: e, floorId: fid });
    });
  });
  (DATA.crossFloorEdges || []).forEach(function (x) {
    edgeIndex.set(x.i, { kind: 'cross', x: x, floorId: String(x.ff) });
  });

  function rebuildHazardIndex() {
    hazardByEdge = new Map();
    hazards.forEach(function (h) {
      (h.edgeIds || []).forEach(function (id) {
        if (!hazardByEdge.has(id)) hazardByEdge.set(id, []);
        hazardByEdge.get(id).push(h);
      });
    });
  }

  function timeValid(h) {
    var now = Date.now();
    if (h.activeFrom) {
      var from = Date.parse(h.activeFrom);
      if (!isNaN(from) && from > now) return false;
    }
    if (h.activeUntil) {
      var until = Date.parse(h.activeUntil);
      if (!isNaN(until) && until < now) return false;
    }
    return true;
  }

  function isEffective(h) {
    return (h.status || 'active') === 'active' && timeValid(h);
  }

  function effectiveHazard(edgeId) {
    var list = hazardByEdge.get(edgeId) || [];
    var best = null;
    for (var i = 0; i < list.length; i++) {
      var h = list[i];
      if (!isEffective(h)) continue;
      if (!best || (RANK[h.level] || 0) > (RANK[best.level] || 0)) best = h;
    }
    return best;
  }

  function anyHazard(edgeId) {
    var list = hazardByEdge.get(edgeId) || [];
    for (var i = 0; i < list.length; i++) {
      if (isEffective(list[i])) return list[i];
    }
    return list[0] || null;
  }

  function mapBlindAccessible(edgeId) {
    var rec = edgeIndex.get(edgeId);
    if (!rec) return true;
    if (rec.kind === 'edge') return rec.e.ba !== false;
    return rec.x.ba !== false;
  }

  function floorOfEdge(edgeId) {
    var rec = edgeIndex.get(edgeId);
    return rec ? rec.floorId : floorId;
  }

  // ---------------- 坐标换算 ----------------
  function sx(cm) { return view.tx + (cm / 100) * view.k; }
  function sy(cm) { return view.ty - (cm / 100) * view.k; }
  function wx(px) { return (px - view.tx) / view.k; }
  function wy(py) { return (view.ty - py) / view.k; }

  function fitView() {
    var b = DATA.floors[floorId].bounds;
    var bw = Math.max(1, (b[2] - b[0]) / 100);
    var bh = Math.max(1, (b[3] - b[1]) / 100);
    var pad = 56;
    var k = Math.min((canvasW - pad * 2) / bw, (canvasH - pad * 2) / bh);
    if (!isFinite(k) || k <= 0) k = 6;
    view.k = k;
    var cx = (b[0] + b[2]) / 200;
    var cy = (b[1] + b[3]) / 200;
    view.tx = canvasW / 2 - cx * k;
    view.ty = canvasH / 2 + cy * k;
    dirty = true;
  }

  function zoomAt(px, py, factor) {
    var before = { x: wx(px), y: wy(py) };
    var k = Math.max(0.5, Math.min(400, view.k * factor));
    view.k = k;
    view.tx = px - before.x * k;
    view.ty = py + before.y * k;
    dirty = true;
    requestDraw();
  }

  function zoomToSelection() {
    var pts = [];
    selection.forEach(function (id) {
      var rec = edgeIndex.get(id);
      if (!rec) return;
      if (rec.kind === 'edge') {
        pts.push(rec.e.a, rec.e.b);
      } else {
        pts.push(rec.x.a);
      }
    });
    if (!pts.length) {
      fitView();
      requestDraw();
      return;
    }
    var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    pts.forEach(function (p) {
      var x = p[0] / 100, y = p[1] / 100;
      minx = Math.min(minx, x); maxx = Math.max(maxx, x);
      miny = Math.min(miny, y); maxy = Math.max(maxy, y);
    });
    var bw = Math.max(6, maxx - minx);
    var bh = Math.max(6, maxy - miny);
    var k = Math.min((canvasW - 160) / bw, (canvasH - 160) / bh);
    view.k = Math.max(1.5, Math.min(80, k));
    var cx = (minx + maxx) / 2, cy = (miny + maxy) / 2;
    view.tx = canvasW / 2 - cx * view.k;
    view.ty = canvasH / 2 + cy * view.k;
    dirty = true;
    requestDraw();
  }

  // ---------------- 绘制 ----------------
  var rafPending = false;
  function requestDraw() {
    if (rafPending) return;
    rafPending = true;
    window.requestAnimationFrame(function () {
      rafPending = false;
      draw();
    });
  }

  function polyPath(flat) {
    ctx.beginPath();
    ctx.moveTo(sx(flat[0]), sy(flat[1]));
    for (var i = 2; i < flat.length; i += 2) {
      ctx.lineTo(sx(flat[i]), sy(flat[i + 1]));
    }
    ctx.closePath();
  }

  function edgePath(e) {
    ctx.beginPath();
    ctx.moveTo(sx(e.a[0]), sy(e.a[1]));
    ctx.lineTo(sx(e.b[0]), sy(e.b[1]));
  }

  function draw() {
    dirty = false;
    ctx.clearRect(0, 0, canvasW, canvasH);
    var f = DATA.floors[floorId];
    if (!f) return;

    // 可通行区
    if (els.toggleWalk.checked) {
      ctx.fillStyle = '#e8f1f8';
      f.walkable.forEach(function (poly) {
        polyPath(poly);
        ctx.fill();
      });
    }

    // 房间
    ctx.lineWidth = 1;
    f.rooms.forEach(function (r) {
      polyPath(r.poly);
      ctx.fillStyle = 'rgba(255,255,255,0.72)';
      ctx.fill();
      ctx.strokeStyle = '#dde3ec';
      ctx.stroke();
    });

    // 楼梯 / 电梯
    f.stairs.forEach(function (s) {
      polyPath(s.poly);
      ctx.fillStyle = 'rgba(254,226,226,0.75)';
      ctx.fill();
      ctx.strokeStyle = '#f0a6a6';
      ctx.stroke();
    });
    f.elevators.forEach(function (s) {
      polyPath(s.poly);
      ctx.fillStyle = 'rgba(219,234,254,0.85)';
      ctx.fill();
      ctx.strokeStyle = '#93b4f5';
      ctx.stroke();
    });

    // 柱
    ctx.fillStyle = '#d7dde6';
    f.columns.forEach(function (poly) {
      polyPath(poly);
      ctx.fill();
    });

    // 门（点）
    if (view.k >= 3) {
      ctx.fillStyle = '#b7791f';
      f.doors.forEach(function (d) {
        var r = d[2] ? 3 : 2;
        ctx.beginPath();
        ctx.arc(sx(d[0]), sy(d[1]), r, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // 墙（按厚度分三档，各用一条 path 批量描边；线宽随缩放变化）
    var buckets = [[15, '#9aa8bb', 0.10], [25, '#8593a8', 0.18], [Infinity, '#6b7a91', 0.34]];
    var groups = [[], [], []];
    f.walls.forEach(function (w) {
      var bi = w[4] <= 15 ? 0 : (w[4] <= 25 ? 1 : 2);
      groups[bi].push(w);
    });
    for (var bi = 0; bi < 3; bi++) {
      if (!groups[bi].length) continue;
      ctx.beginPath();
      groups[bi].forEach(function (w) {
        ctx.moveTo(sx(w[0]), sy(w[1]));
        ctx.lineTo(sx(w[2]), sy(w[3]));
      });
      ctx.strokeStyle = buckets[bi][1];
      ctx.lineWidth = Math.max(1.1, Math.min(24, buckets[bi][2] * view.k));
      ctx.stroke();
    }

    // 结构风险点
    if (els.toggleRisk.checked) {
      f.riskNodes.forEach(function (rn) {
        var color = '#f59e0b';
        if (rn.type === 'stair_entrance') color = '#ef4444';
        else if (rn.type.indexOf('elevator') >= 0) color = '#3b82f6';
        ctx.beginPath();
        ctx.arc(sx(rn.x), sy(rn.y), view.k >= 5 ? 3.2 : 2, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.55;
        ctx.fill();
        ctx.globalAlpha = 1;
      });
    }

    // 拓扑边
    ctx.lineCap = 'round';
    f.edges.forEach(function (e) {
      var h = effectiveHazard(e.i);
      var selected = selection.has(e.i);
      if (e.i === (hover && hover.id)) {
        edgePath(e);
        ctx.setLineDash([]);
        ctx.strokeStyle = 'rgba(255,255,255,0.95)';
        ctx.lineWidth = 8;
        ctx.stroke();
      }
      if (selected) {
        edgePath(e);
        ctx.setLineDash([]);
        ctx.strokeStyle = 'rgba(2,132,199,0.30)';
        ctx.lineWidth = 11;
        ctx.stroke();
      }
      edgePath(e);
      if (h) {
        var st = LEVEL_STYLE[h.level] || LEVEL_STYLE.notice;
        ctx.strokeStyle = st.color;
        ctx.lineWidth = selected ? st.width + 0.8 : st.width;
        ctx.setLineDash(st.dash || []);
      } else {
        ctx.strokeStyle = selected ? '#0369a1' : '#9aa7bb';
        ctx.lineWidth = selected ? 2.6 : 1.4;
        ctx.setLineDash([]);
      }
      ctx.stroke();
    });
    ctx.setLineDash([]);

    // 跨层连接（在本层画起点标记）
    (DATA.crossFloorEdges || []).forEach(function (x) {
      if (String(x.ff) !== floorId) return;
      var h = effectiveHazard(x.i);
      var selected = selection.has(x.i);
      var px = sx(x.a[0]), py = sy(x.a[1]);
      var color = h ? (LEVEL_STYLE[h.level] || LEVEL_STYLE.notice).color : '#7c3aed';
      ctx.beginPath();
      ctx.moveTo(px, py - 7);
      ctx.lineTo(px + 7, py);
      ctx.lineTo(px, py + 7);
      ctx.lineTo(px - 7, py);
      ctx.closePath();
      ctx.fillStyle = h ? color : '#ffffff';
      ctx.strokeStyle = selected ? '#0284c7' : color;
      ctx.lineWidth = selected ? 3 : 2;
      ctx.fill();
      ctx.stroke();
      if (view.k >= 10) {
        ctx.fillStyle = '#6d28d9';
        ctx.font = '10px Consolas, monospace';
        ctx.fillText(x.code || x.i, px + 10, py - 6);
      } else {
        ctx.fillStyle = '#6d28d9';
        ctx.font = '11px "Microsoft YaHei", sans-serif';
        ctx.fillText('↕', px + 9, py + 4);
      }
    });

    // 房间名
    if (els.toggleLabels.checked && view.k >= 6.5) {
      ctx.fillStyle = '#8b98ab';
      ctx.font = '10px "Microsoft YaHei", sans-serif';
      f.rooms.forEach(function (r) {
        if (!r.label) return;
        ctx.fillText(r.label, sx(r.x) - 14, sy(r.y));
      });
    }

    // 框选矩形
    if (boxRect) {
      ctx.fillStyle = 'rgba(2,132,199,0.08)';
      ctx.strokeStyle = '#0284c7';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 3]);
      ctx.fillRect(boxRect.x, boxRect.y, boxRect.w, boxRect.h);
      ctx.strokeRect(boxRect.x, boxRect.y, boxRect.w, boxRect.h);
      ctx.setLineDash([]);
    }
  }

  // ---------------- 尺寸 ----------------
  function resize() {
    var rect = els.wrap.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(1, Math.round(rect.width));
    var h = Math.max(1, Math.round(rect.height));
    var first = canvasW === 0;
    var dx = (w - canvasW) / 2;
    var dy = (h - canvasH) / 2;
    canvasW = w;
    canvasH = h;
    els.canvas.width = Math.round(w * dpr);
    els.canvas.height = Math.round(h * dpr);
    els.canvas.style.width = w + 'px';
    els.canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (first) {
      fitView();
    } else {
      view.tx += dx;
      view.ty += dy;
    }
    dirty = true;
    draw();
  }

  // ---------------- 命中测试 ----------------
  function distToSegment(px, py, x1, y1, x2, y2) {
    var dx = x2 - x1, dy = y2 - y1;
    var len2 = dx * dx + dy * dy;
    var t = len2 === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    var cx = x1 + t * dx, cy = y1 + t * dy;
    return Math.hypot(px - cx, py - cy);
  }

  function pointInRect(px, py, r) {
    return px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h;
  }

  function segSeg(x1, y1, x2, y2, x3, y3, x4, y4) {
    function orient(ax, ay, bx, by, cx, cy) {
      return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    }
    var d1 = orient(x3, y3, x4, y4, x1, y1);
    var d2 = orient(x3, y3, x4, y4, x2, y2);
    var d3 = orient(x1, y1, x2, y2, x3, y3);
    var d4 = orient(x1, y1, x2, y2, x4, y4);
    return ((d1 > 0) !== (d2 > 0)) && ((d3 > 0) !== (d4 > 0));
  }

  function segIntersectsRect(x1, y1, x2, y2, r) {
    if (pointInRect(x1, y1, r) || pointInRect(x2, y2, r)) return true;
    var x3 = r.x, y3 = r.y, x4 = r.x + r.w, y4 = r.y + r.h;
    return (
      segSeg(x1, y1, x2, y2, x3, y3, x4, y3) ||
      segSeg(x1, y1, x2, y2, x4, y3, x4, y4) ||
      segSeg(x1, y1, x2, y2, x4, y4, x3, y4) ||
      segSeg(x1, y1, x2, y2, x3, y4, x3, y3)
    );
  }

  function pickAt(px, py) {
    var best = null;
    var bestD = 9;
    var f = DATA.floors[floorId];
    f.edges.forEach(function (e) {
      var d = distToSegment(px, py, sx(e.a[0]), sy(e.a[1]), sx(e.b[0]), sy(e.b[1]));
      if (d < bestD) {
        bestD = d;
        best = { kind: 'edge', id: e.i };
      }
    });
    (DATA.crossFloorEdges || []).forEach(function (x) {
      if (String(x.ff) !== floorId) return;
      var d = Math.hypot(px - sx(x.a[0]), py - sy(x.a[1]));
      if (d < 11 && (best === null || d < bestD)) {
        bestD = d;
        best = { kind: 'cross', id: x.i };
      }
    });
    return best;
  }

  // ---------------- 选择与表单 ----------------
  function setSelection(ids, additive) {
    if (!additive) selection = new Set();
    ids.forEach(function (id) {
      if (additive && selection.has(id)) selection.delete(id);
      else selection.add(id);
    });
    syncForm();
    renderList();
    dirty = true;
    requestDraw();
  }

  function resetForm() {
    els.level.value = '';
    els.blind.checked = true;
    els.type.value = 'construction';
    els.reason.value = '';
    els.spoken.value = '';
    els.penalty.value = '60';
    els.from.value = '';
    els.until.value = '';
    document.querySelectorAll('.f-apply').forEach(function (cb) {
      cb.checked = cb.value === 'blind';
    });
  }

  function updateSelSummary() {
    els.selCount.textContent = String(selection.size);
    els.apply.disabled = selection.size === 0;
    els.clearSel.disabled = selection.size === 0;
    if (selection.size === 0) {
      els.selSummary.className = 'sel-summary muted';
      els.selSummary.textContent = '点击图上的拓扑边开始标注；框选模式可一次选中多条。';
      resetForm();
      return;
    }
    els.selSummary.className = 'sel-summary';
    var ids = Array.from(selection);
    if (ids.length === 1) {
      var rec = edgeIndex.get(ids[0]);
      if (!rec) {
        els.selSummary.textContent = ids[0] + '（当前地图包中不存在）';
        return;
      }
      var h = anyHazard(ids[0]);
      var mapOk = mapBlindAccessible(ids[0]);
      var len = rec.kind === 'edge' ? rec.e.d : rec.x.d;
      var parts = [];
      parts.push('<div><span class="edge-id">' + esc(ids[0]) + '</span> <span class="tag">' +
        (rec.kind === 'cross' ? '跨层 ' + esc(rec.x.code || '') : '本层边') + '</span></div>');
      parts.push('<div class="muted">长度 ' + len.toFixed(2) + ' m · 地图包 blindAccessible：' +
        (mapOk ? '是' : '否') + '</div>');
      if (h) {
        parts.push('<div>当前标注：<b>' + esc(LEVEL_LABEL[h.level] || h.level) + '</b> · ' +
          esc(TYPE_LABEL[h.type] || h.type) + '</div>');
        if (h.reason) parts.push('<div class="muted">' + esc(h.reason) + '</div>');
        if (!isEffective(h)) parts.push('<div class="muted">（该标注当前未生效：' +
          (h.status !== 'active' ? esc(h.status) : '不在生效时间') + '）</div>');
      } else {
        parts.push('<div class="muted">尚未标注</div>');
      }
      els.selSummary.innerHTML = parts.join('');
    } else {
      var floors = {};
      var known = 0;
      var unknown = 0;
      ids.forEach(function (id) {
        if (!edgeIndex.has(id)) { unknown++; return; }
        known++;
        floors[floorOfEdge(id)] = true;
      });
      var idsPreview = ids.slice(0, 4).join('、') + (ids.length > 4 ? ' …' : '');
      els.selSummary.innerHTML =
        '<div>已选 <b>' + known + '</b> 条边' +
        (Object.keys(floors).length === 1 ? '（' + esc(Object.keys(floors)[0]) + 'F）' : '') +
        (unknown ? '，另有 ' + unknown + ' 条不在当前地图' : '') + '</div>' +
        '<div class="muted">' + esc(idsPreview) + '</div>';
    }
  }

  function syncForm() {
    updateSelSummary();
    if (selection.size === 0) return;
    var id = Array.from(selection)[0];
    var h = anyHazard(id);
    els.level.value = h ? h.level : '';
    els.blind.checked = h ? h.blindAccessible !== false : mapBlindAccessible(id);
    els.type.value = h ? h.type : 'construction';
    els.reason.value = h ? (h.reason || '') : '';
    els.spoken.value = h ? (h.spokenText || '') : '';
    els.penalty.value = String(h && h.penaltyM ? h.penaltyM : 60);
    els.from.value = h ? toLocalInput(h.activeFrom) : '';
    els.until.value = h ? toLocalInput(h.activeUntil) : '';
    var applies = h && Array.isArray(h.appliesTo) ? h.appliesTo : ['blind'];
    document.querySelectorAll('.f-apply').forEach(function (cb) {
      cb.checked = applies.indexOf(cb.value) >= 0;
    });
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function nowISO() {
    return new Date().toISOString();
  }

  function toISO(localValue) {
    if (!localValue) return null;
    var d = new Date(localValue);
    return isNaN(d.getTime()) ? null : d.toISOString();
  }

  function toLocalInput(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d.getTime())) return '';
    var p = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) +
      'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function nextHazardId() {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, '0'); };
    var prefix = 'HZ-' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-';
    var max = 0;
    hazards.forEach(function (h) {
      if (typeof h.hazardId === 'string' && h.hazardId.indexOf(prefix) === 0) {
        var n = parseInt(h.hazardId.slice(prefix.length), 10);
        if (n > max) max = n;
      }
    });
    return prefix + String(max + 1).padStart(4, '0');
  }

  function getApplies() {
    var out = [];
    document.querySelectorAll('.f-apply').forEach(function (cb) {
      if (cb.checked) out.push(cb.value);
    });
    return out.length ? out : ['blind'];
  }

  // ---------------- 修改危险层 ----------------
  function pushUndo() {
    undoStack.push(JSON.stringify(hazards));
    if (undoStack.length > 50) undoStack.shift();
    els.undo.disabled = undoStack.length === 0;
  }

  function afterMutate(message, warn) {
    rebuildHazardIndex();
    persist();
    renderList();
    syncForm();
    els.hazCount.textContent = String(hazards.length);
    dirty = true;
    requestDraw();
    if (message) flash(message, warn);
  }

  function removeEdgesFromHazards(ids) {
    var set = new Set(ids);
    var now = nowISO();
    hazards = hazards.map(function (h) {
      var edgeIds = (h.edgeIds || []).filter(function (id) { return !set.has(id); });
      if (edgeIds.length === (h.edgeIds || []).length) return h;
      var copy = Object.assign({}, h, {
        edgeIds: edgeIds,
        updatedAt: now,
        revision: (h.revision || 1) + 1
      });
      return copy;
    }).filter(function (h) { return h.edgeIds && h.edgeIds.length > 0; });
  }

  function findExactHazard(ids) {
    var set = new Set(ids);
    for (var i = 0; i < hazards.length; i++) {
      var h = hazards[i];
      if (!h.edgeIds || h.edgeIds.length !== ids.length) continue;
      var same = h.edgeIds.every(function (id) { return set.has(id); });
      if (same) return h;
    }
    return null;
  }

  function applyToSelection() {
    if (selection.size === 0) {
      flash('请先选中至少一条边', true);
      return;
    }
    var level = els.level.value;
    var ids = Array.from(selection).filter(function (id) { return edgeIndex.has(id); });
    if (!ids.length) {
      flash('选中的边不在当前地图包中，无法标注', true);
      return;
    }
    var reason = els.reason.value.trim();
    if (level && !reason) {
      flash('请填写理由文案（用于清单与语音播报）', true);
      els.reason.focus();
      return;
    }
    // 前景/背景时间校验
    var activeFrom = toISO(els.from.value);
    var activeUntil = toISO(els.until.value);
    if (activeFrom && activeUntil && Date.parse(activeUntil) <= Date.parse(activeFrom)) {
      flash('失效时间必须晚于生效时间', true);
      return;
    }

    // 防止误操作把更严格的标注覆盖掉（例如框选时顺带选中了楼梯 block 边）
    var stricter = ids.filter(function (id) {
      var cur = effectiveHazard(id);
      if (!cur) return false;
      if (!level) return true;
      return (RANK[cur.level] || 0) > (RANK[level] || 0);
    });
    if (stricter.length) {
      var msg = level
        ? ('选中边中有 ' + stricter.length + ' 条已有更严格的标注（block > avoid > notice）。' +
          '应用“' + LEVEL_LABEL[level] + '”会覆盖它们，是否继续？')
        : ('选中边中有 ' + stricter.length + ' 条已有标注，清除后会恢复地图包原值。是否继续？');
      if (!window.confirm(msg)) return;
    }

    pushUndo();
    var now = nowISO();
    var existing = level ? findExactHazard(ids) : null;

    if (existing) {
      // 同一批边原地更新，保留 hazardId 与创建时间
      existing.level = level;
      existing.type = els.type.value;
      existing.appliesTo = getApplies();
      existing.reason = reason;
      existing.spokenText = els.spoken.value.trim() || null;
      existing.blindAccessible = level !== 'block';
      existing.activeFrom = activeFrom;
      existing.activeUntil = activeUntil;
      existing.updatedAt = now;
      existing.revision = (existing.revision || 1) + 1;
      if (level === 'avoid') {
        existing.penaltyM = clampNumber(els.penalty.value, 1, 500, 60);
      } else {
        delete existing.penaltyM;
      }
    } else {
      removeEdgesFromHazards(ids);
      if (level) {
        var hz = {
          hazardId: nextHazardId(),
          siteId: SITE,
          floorId: floorOfEdge(ids[0]),
          targetType: 'edge',
          edgeIds: ids.slice().sort(),
          type: els.type.value,
          level: level,
          appliesTo: getApplies(),
          reason: reason,
          spokenText: els.spoken.value.trim() || null,
          blindAccessible: level !== 'block',
          activeFrom: activeFrom,
          activeUntil: activeUntil,
          status: 'active',
          source: 'admin',
          reportedBy: null,
          note: null,
          createdAt: now,
          updatedAt: now,
          revision: 1
        };
        if (level === 'avoid') hz.penaltyM = clampNumber(els.penalty.value, 1, 500, 60);
        hazards.push(hz);
      }
    }
    afterMutate(level ? ('已标注 ' + ids.length + ' 条边：' + LEVEL_LABEL[level]) : '已清除选中边的标注');
  }

  function clampNumber(v, min, max, fallback) {
    var n = Number(v);
    if (!isFinite(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function clearSelectionHazards() {
    if (!selection.size) return;
    pushUndo();
    removeEdgesFromHazards(Array.from(selection));
    els.level.value = '';
    els.reason.value = '';
    afterMutate('已清除选中边的标注');
  }

  function doUndo() {
    if (!undoStack.length) {
      flash('没有可撤销的操作');
      return;
    }
    hazards = JSON.parse(undoStack.pop());
    els.undo.disabled = undoStack.length === 0;
    afterMutate('已撤销上一步');
  }

  // ---------------- 清单 ----------------
  function renderList() {
    els.hazList.innerHTML = '';
    els.hazCount.textContent = String(hazards.length);
    if (!hazards.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '还没有危险标注。选中边后填写表单并点“应用到选中”。';
      els.hazList.appendChild(empty);
      return;
    }
    var sorted = hazards.slice().sort(function (a, b) {
      var fa = parseFloat(a.floorId) || 0;
      var fb = parseFloat(b.floorId) || 0;
      if (fa !== fb) return fa - fb;
      if ((RANK[b.level] || 0) !== (RANK[a.level] || 0)) return (RANK[b.level] || 0) - (RANK[a.level] || 0);
      return String(a.hazardId).localeCompare(String(b.hazardId));
    });
    sorted.forEach(function (h) {
      var item = document.createElement('div');
      item.className = 'haz-item' + (Array.from(selection).some(function (id) {
        return (h.edgeIds || []).indexOf(id) >= 0;
      }) ? ' active' : '');
      item.addEventListener('click', function (ev) {
        if (ev.target && ev.target.classList.contains('del')) return;
        focusHazard(h.hazardId);
      });

      var line1 = document.createElement('div');
      line1.className = 'line1';
      var chip = document.createElement('span');
      chip.className = 'chip ' + h.level;
      chip.textContent = h.level;
      line1.appendChild(chip);
      var type = document.createElement('span');
      type.textContent = TYPE_LABEL[h.type] || h.type || '未分类';
      line1.appendChild(type);
      var del = document.createElement('button');
      del.className = 'del';
      del.textContent = '删除';
      del.addEventListener('click', function (ev) {
        ev.stopPropagation();
        pushUndo();
        hazards = hazards.filter(function (x) { return x.hazardId !== h.hazardId; });
        afterMutate('已删除危险 ' + h.hazardId);
      });
      line1.appendChild(del);
      item.appendChild(line1);

      var line2 = document.createElement('div');
      line2.className = 'line2';
      line2.textContent = h.reason || '（未填写理由）';
      item.appendChild(line2);

      var line3 = document.createElement('div');
      line3.className = 'line3';
      var meta = [];
      meta.push(String(h.floorId) + 'F');
      meta.push((h.edgeIds || []).length + ' 条边');
      if (h.activeUntil) meta.push('至 ' + h.activeUntil.slice(0, 16).replace('T', ' '));
      if (h.status && h.status !== 'active') meta.push(h.status);
      if (!timeValid(h)) meta.push('不在生效时间');
      if (h.source === 'map_package') meta.push('地图包基线');
      meta.forEach(function (t) {
        var s = document.createElement('span');
        s.textContent = t;
        line3.appendChild(s);
      });
      item.appendChild(line3);

      els.hazList.appendChild(item);
    });
  }

  function focusHazard(hazardId) {
    var h = hazards.find(function (x) { return x.hazardId === hazardId; });
    if (!h) return;
    var fid = String(h.floorId);
    if (DATA.floors[fid] && fid !== floorId) {
      floorId = fid;
      renderFloorTabs();
    }
    selection = new Set((h.edgeIds || []).filter(function (id) { return edgeIndex.has(id); }));
    zoomToSelection();
    syncForm();
    renderList();
    dirty = true;
    requestDraw();
  }

  // ---------------- 楼层 ----------------
  function renderFloorTabs() {
    els.floorTabs.innerHTML = '';
    FLOOR_IDS.forEach(function (fid) {
      var btn = document.createElement('button');
      var name = DATA.floors[fid].displayName || '';
      btn.textContent = fid + 'F';
      btn.title = name;
      btn.className = fid === floorId ? 'active' : '';
      btn.addEventListener('click', function () {
        if (floorId === fid) return;
        floorId = fid;
        selection = new Set();
        boxRect = null;
        renderFloorTabs();
        updateSelSummary();
        renderList();
        fitView();
        dirty = true;
        requestDraw();
      });
      els.floorTabs.appendChild(btn);
    });
  }

  // ---------------- 导入 / 导出 ----------------
  function normalizeImported(list) {
    var warnings = [];
    var out = [];
    list.forEach(function (raw, index) {
      if (!raw || typeof raw !== 'object') {
        warnings.push('第 ' + (index + 1) + ' 条不是对象，已跳过');
        return;
      }
      if (!raw.hazardId || typeof raw.hazardId !== 'string') {
        warnings.push('第 ' + (index + 1) + ' 条缺少 hazardId，已跳过');
        return;
      }
      if (LEVELS.indexOf(raw.level) < 0) {
        warnings.push(raw.hazardId + ' 档位非法，已跳过');
        return;
      }
      if (!Array.isArray(raw.edgeIds) || raw.edgeIds.length === 0) {
        warnings.push(raw.hazardId + ' 缺少 edgeIds，已跳过');
        return;
      }
      var unknown = raw.edgeIds.filter(function (id) { return !edgeIndex.has(id); });
      if (unknown.length) {
        warnings.push(raw.hazardId + ' 有 ' + unknown.length + ' 条边不在当前地图（' +
          unknown.slice(0, 3).join('、') + (unknown.length > 3 ? '…' : '') + '）');
      }
      var h = Object.assign({}, raw);
      h.edgeIds = raw.edgeIds.slice();
      h.appliesTo = Array.isArray(raw.appliesTo) && raw.appliesTo.length ? raw.appliesTo : ['blind'];
      h.blindAccessible = raw.level !== 'block';
      h.status = raw.status || 'active';
      h.source = raw.source || 'import';
      h.revision = raw.revision || 1;
      h.createdAt = raw.createdAt || nowISO();
      h.updatedAt = raw.updatedAt || h.createdAt;
      h.activeFrom = raw.activeFrom || null;
      h.activeUntil = raw.activeUntil || null;
      h.spokenText = raw.spokenText || null;
      h.reportedBy = raw.reportedBy || null;
      h.note = raw.note || null;
      if (h.level === 'avoid' && !h.penaltyM) h.penaltyM = 60;
      if (h.level !== 'avoid') delete h.penaltyM;
      out.push(h);
    });
    return { list: out, warnings: warnings };
  }

  function importFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var obj = JSON.parse(String(reader.result));
        var list = Array.isArray(obj) ? obj : obj.hazards;
        if (!Array.isArray(list)) throw new Error('找不到 hazards 数组');
        var meta = [];
        if (!Array.isArray(obj)) {
          if (obj.siteId && obj.siteId !== SITE) meta.push('siteId=' + obj.siteId + ' 与当前 ' + SITE + ' 不一致');
          if (obj.mapVersion && obj.mapVersion !== DATA.mapVersion) {
            meta.push('mapVersion=' + obj.mapVersion + '，当前地图 ' + DATA.mapVersion);
          }
          if (obj.schemaVersion && obj.schemaVersion !== '1.0.0') {
            meta.push('schemaVersion=' + obj.schemaVersion);
          }
        }
        var result = normalizeImported(list);
        pushUndo();
        hazards = result.list;
        selection = new Set();
        afterMutate('已导入 ' + hazards.length + ' 条危险');
        var allWarn = meta.concat(result.warnings);
        if (allWarn.length) {
          flash('导入提醒：' + allWarn.slice(0, 3).join('；') +
            (allWarn.length > 3 ? ' 等 ' + allWarn.length + ' 项' : ''), true);
        }
      } catch (err) {
        flash('导入失败：' + err.message, true);
      }
    };
    reader.readAsText(file);
  }

  function buildLayer() {
    var now = nowISO();
    var list = hazards.map(function (h) {
      var out = {
        hazardId: h.hazardId,
        siteId: h.siteId || SITE,
        floorId: String(h.floorId),
        targetType: h.targetType || 'edge',
        edgeIds: (h.edgeIds || []).slice(),
        type: h.type || 'other',
        level: h.level,
        appliesTo: (h.appliesTo && h.appliesTo.length) ? h.appliesTo.slice() : ['blind'],
        reason: h.reason || '未填写理由',
        spokenText: h.spokenText || null,
        blindAccessible: h.level !== 'block',
        activeFrom: h.activeFrom || null,
        activeUntil: h.activeUntil || null,
        status: h.status || 'active',
        source: h.source || 'admin',
        reportedBy: h.reportedBy || null,
        note: h.note || null,
        createdAt: h.createdAt || now,
        updatedAt: h.updatedAt || now,
        revision: h.revision || 1
      };
      if (h.level === 'avoid') out.penaltyM = Number(h.penaltyM) || 60;
      if (h.geometry) out.geometry = h.geometry;
      return out;
    });
    var updatedAt = list.reduce(function (m, h) {
      return h.updatedAt > m ? h.updatedAt : m;
    }, now);
    return {
      schemaVersion: '1.0.0',
      hazardVersion: updatedAt + '/' + list.length,
      siteId: SITE,
      mapVersion: DATA.mapVersion,
      generatedAt: now,
      updatedAt: updatedAt,
      generator: 'pathai-hazard-editor/1.0.0',
      notes: '地图数据：' + DATA.sourceFile,
      hazards: list
    };
  }

  function exportLayer() {
    var layer = buildLayer();
    var json = JSON.stringify(layer, null, 2);
    els.preview.textContent = json.length > 40000
      ? json.slice(0, 40000) + '\n…（预览截断，下载文件为完整内容）'
      : json;
    var blob = new Blob([json + '\n'], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = SITE + '.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    flash('已导出 ' + layer.hazards.length + ' 条危险（' + SITE + '.json）');
  }

  // ---------------- 持久化 ----------------
  function persist() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(hazards));
    } catch (err) {
      /* 配额/隐私模式失败不影响编辑与导出 */
    }
  }

  function loadInitial() {
    var initial = Array.isArray(DATA.initialHazards) ? DATA.initialHazards : [];
    try {
      var raw = localStorage.getItem(LS_KEY);
      if (raw) {
        var parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) {
          hazards = parsed;
          flash('已恢复本地草稿：' + hazards.length + ' 条（来源 ' + SITE + '）');
          return;
        }
      }
    } catch (err) {
      /* 忽略损坏草稿 */
    }
    hazards = JSON.parse(JSON.stringify(initial));
  }

  // ---------------- 提示 ----------------
  function flash(msg, warn) {
    els.status.textContent = msg;
    els.status.className = 'status show' + (warn ? ' warn' : '');
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = setTimeout(function () {
      els.status.className = 'status';
      statusTimer = null;
    }, 3600);
  }

  // ---------------- 提示气泡 ----------------
  function showTooltip(px, py, id) {
    var rec = edgeIndex.get(id);
    if (!rec) return;
    var h = effectiveHazard(id);
    var mapOk = mapBlindAccessible(id);
    var html = '<b>' + esc(id) + '</b><br>';
    if (rec.kind === 'edge') {
      html += '长度 ' + rec.e.d.toFixed(2) + ' m · 盲道可达：' + (mapOk ? '是' : '否');
    } else {
      html += '跨层 ' + esc(rec.x.code || '') + '（' + esc(rec.x.ff) + 'F↔' + esc(rec.x.tf) +
        'F）· 盲道可达：' + (mapOk ? '是' : '否');
    }
    if (h) {
      html += '<br><span style="color:#fdba74">' + esc(LEVEL_LABEL[h.level] || h.level) + '</span>' +
        ' · ' + esc(TYPE_LABEL[h.type] || h.type);
      if (h.reason) html += '<br>' + esc(h.reason);
      if (!isEffective(h)) html += '<br>（当前未生效）';
    } else {
      html += '<br>未标注';
    }
    html += '<br><span style="opacity:.7">点击选中 · Shift 多选</span>';
    els.tooltip.innerHTML = html;
    els.tooltip.hidden = false;
    var w = els.tooltip.offsetWidth;
    var left = px + 16;
    if (left + w > canvasW - 8) left = px - w - 16;
    var top = py + 16;
    if (top + els.tooltip.offsetHeight > canvasH - 8) top = py - els.tooltip.offsetHeight - 12;
    els.tooltip.style.left = Math.max(4, left) + 'px';
    els.tooltip.style.top = Math.max(4, top) + 'px';
  }

  function hideTooltip() {
    els.tooltip.hidden = true;
  }

  // ---------------- 交互 ----------------
  function localPos(ev) {
    var r = els.canvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  els.canvas.addEventListener('mousedown', function (ev) {
    if (ev.button !== 0) return;
    var p = localPos(ev);
    hideTooltip();
    if (mode === 'box') {
      boxRect = { x: p.x, y: p.y, w: 0, h: 0 };
      drag = { type: 'box', startX: p.x, startY: p.y, additive: ev.shiftKey, moved: false };
      dirty = true;
      requestDraw();
      return;
    }
    drag = {
      type: 'pan',
      startX: p.x,
      startY: p.y,
      tx: view.tx,
      ty: view.ty,
      moved: false
    };
    els.canvas.classList.add('panning');
  });

  window.addEventListener('mousemove', function (ev) {
    var p = localPos(ev);
    if (drag && drag.type === 'pan') {
      var dx = p.x - drag.startX;
      var dy = p.y - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      if (drag.moved) {
        view.tx = drag.tx + dx;
        view.ty = drag.ty + dy;
        dirty = true;
        requestDraw();
      }
      return;
    }
    if (drag && drag.type === 'box') {
      boxRect.x = Math.min(drag.startX, p.x);
      boxRect.y = Math.min(drag.startY, p.y);
      boxRect.w = Math.abs(p.x - drag.startX);
      boxRect.h = Math.abs(p.y - drag.startY);
      if (boxRect.w + boxRect.h > 4) drag.moved = true;
      dirty = true;
      requestDraw();
      return;
    }
    if (p.x < 0 || p.y < 0 || p.x > canvasW || p.y > canvasH) {
      hideTooltip();
      if (hover) {
        hover = null;
        dirty = true;
        requestDraw();
      }
      return;
    }
    var hit = pickAt(p.x, p.y);
    var hitId = hit ? hit.id : null;
    if (hitId !== (hover && hover.id)) {
      hover = hitId ? { id: hitId } : null;
      dirty = true;
      requestDraw();
    }
    if (hitId) showTooltip(p.x, p.y, hitId);
    else hideTooltip();
  });

  window.addEventListener('mouseup', function (ev) {
    if (!drag) return;
    var p = localPos(ev);
    var d = drag;
    drag = null;
    els.canvas.classList.remove('panning');
    if (d.type === 'pan') {
      if (!d.moved) {
        var hit = pickAt(p.x, p.y);
        if (hit) setSelection([hit.id], ev.shiftKey);
        else if (!ev.shiftKey) setSelection([], false);
      }
      return;
    }
    // 框选
    var rect = boxRect;
    boxRect = null;
    if (!rect || rect.w + rect.h < 5) {
      var hit2 = pickAt(p.x, p.y);
      if (hit2) setSelection([hit2.id], d.additive);
      dirty = true;
      requestDraw();
      return;
    }
    var ids = [];
    var f = DATA.floors[floorId];
    f.edges.forEach(function (e) {
      if (segIntersectsRect(sx(e.a[0]), sy(e.a[1]), sx(e.b[0]), sy(e.b[1]), rect)) {
        ids.push(e.i);
      }
    });
    (DATA.crossFloorEdges || []).forEach(function (x) {
      if (String(x.ff) !== floorId) return;
      if (pointInRect(sx(x.a[0]), sy(x.a[1]), rect)) ids.push(x.i);
    });
    if (!ids.length) {
      if (!d.additive) setSelection([], false);
      flash('框选范围内没有拓扑边');
    } else {
      setSelection(ids, d.additive);
      flash('已选中 ' + selection.size + ' 条边');
    }
    dirty = true;
    requestDraw();
  });

  els.canvas.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var p = localPos(ev);
    var factor = Math.exp(-ev.deltaY * 0.0015);
    zoomAt(p.x, p.y, factor);
  }, { passive: false });

  els.canvas.addEventListener('mouseleave', function () {
    hideTooltip();
    if (hover) {
      hover = null;
      dirty = true;
      requestDraw();
    }
  });

  // ---------------- 控件事件 ----------------
  els.modePan.addEventListener('click', function () {
    mode = 'pan';
    els.modePan.classList.add('active');
    els.modeBox.classList.remove('active');
    els.canvas.classList.remove('boxing');
  });
  els.modeBox.addEventListener('click', function () {
    mode = 'box';
    els.modeBox.classList.add('active');
    els.modePan.classList.remove('active');
    els.canvas.classList.add('boxing');
  });
  els.zoomIn.addEventListener('click', function () { zoomAt(canvasW / 2, canvasH / 2, 1.3); });
  els.zoomOut.addEventListener('click', function () { zoomAt(canvasW / 2, canvasH / 2, 1 / 1.3); });
  els.fit.addEventListener('click', function () { fitView(); requestDraw(); });

  els.toggleWalk.addEventListener('change', function () { dirty = true; requestDraw(); });
  els.toggleLabels.addEventListener('change', function () { dirty = true; requestDraw(); });
  els.toggleRisk.addEventListener('change', function () { dirty = true; requestDraw(); });

  els.blind.addEventListener('change', function () {
    if (!els.blind.checked) {
      els.level.value = 'block';
    } else if (els.level.value === 'block') {
      els.level.value = 'avoid';
    }
  });
  els.level.addEventListener('change', function () {
    if (els.level.value === 'block') els.blind.checked = false;
    else if (els.level.value) els.blind.checked = true;
  });

  els.apply.addEventListener('click', applyToSelection);
  els.clearSel.addEventListener('click', clearSelectionHazards);
  els.undo.addEventListener('click', doUndo);
  els.importBtn.addEventListener('click', function () { els.importFile.value = ''; els.importFile.click(); });
  els.importFile.addEventListener('change', function () {
    if (els.importFile.files && els.importFile.files[0]) importFile(els.importFile.files[0]);
  });
  els.exportBtn.addEventListener('click', exportLayer);
  els.clearAll.addEventListener('click', function () {
    if (!hazards.length) {
      flash('当前没有标注');
      return;
    }
    if (!window.confirm('确定清空全部 ' + hazards.length + ' 条危险标注？可用“撤销”恢复。')) return;
    pushUndo();
    hazards = [];
    selection = new Set();
    afterMutate('已清空全部标注');
  });

  document.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') {
      ev.preventDefault();
      doUndo();
    } else if (ev.key === 'Escape') {
      setSelection([], false);
      hideTooltip();
    }
  });

  if (window.ResizeObserver) {
    new ResizeObserver(function () { resize(); }).observe(els.wrap);
  } else {
    window.addEventListener('resize', resize);
  }

  // ---------------- 启动 ----------------
  els.meta.textContent = '· ' + SITE + ' · 地图 ' + DATA.mapVersion + ' · 数据 ' + DATA.generatedAt.slice(0, 10);
  loadInitial();
  rebuildHazardIndex();
  renderFloorTabs();
  renderList();
  els.hazCount.textContent = String(hazards.length);
  els.undo.disabled = true;
  updateSelSummary();
  resize();

  // 验证/调试入口：控制台可查看当前导出结构（不影响页面交互）
  window.__hazardEditor = {
    buildLayer: buildLayer,
    getHazards: function () { return hazards; },
    getSelection: function () { return Array.from(selection); },
    setFloor: function (fid) { if (DATA.floors[fid]) { floorId = fid; renderFloorTabs(); fitView(); requestDraw(); } },
    // 返回某条边中点在页面坐标系中的位置，便于自动化测试真实点击
    edgePoint: function (id) {
      var rec = edgeIndex.get(id);
      if (!rec) return null;
      var cm = rec.kind === 'edge'
        ? [(rec.e.a[0] + rec.e.b[0]) / 2, (rec.e.a[1] + rec.e.b[1]) / 2]
        : rec.x.a;
      var r = els.canvas.getBoundingClientRect();
      return { x: r.left + sx(cm[0]), y: r.top + sy(cm[1]) };
    }
  };
})();
