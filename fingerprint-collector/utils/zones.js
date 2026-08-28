// utils/zones.js — 分区（Zone）管理：CRUD + 相对坐标计算
// 分区用于「分区采集」：每个分区有参考锚点元素(anchor)，分区内采集的指纹坐标
// 全部相对该锚点计算（相对坐标 = 采集点绝对坐标 - 锚点绝对坐标）。
// 分区为操作员在采集现场定义，持久化于 storage（不随包发布）。

const STORAGE_KEY = 'fp_zones_v1'

function loadZones() {
  try { return wx.getStorageSync(STORAGE_KEY) || [] } catch (e) { return [] }
}
function saveZones(zones) {
  try { wx.setStorageSync(STORAGE_KEY, zones) } catch (e) {}
}

function nextZoneId(zones) {
  let max = 0
  zones.forEach((z) => {
    const m = /^Z(\d+)$/.exec(z.zoneId || '')
    if (m) max = Math.max(max, parseInt(m[1], 10))
  })
  return 'Z' + String(max + 1).padStart(2, '0')
}

// anchor: 参考锚点元素，兼容两种形态（调用方无需关心）：
//   形态 A（reference_elements 元素）：{id, label, type, floor, abs}
//   形态 B（规范化锚点）：{anchorId, anchorType, anchorLabel, abs}
// 统一规范化为 {anchorId, anchorType('beacon'|'topo'|'custom'), anchorLabel, abs}
function _normalizeAnchor(anchor) {
  if (!anchor || !anchor.abs || anchor.abs.length < 2) {
    throw new Error('分区必须指定参考锚点元素')
  }
  return {
    anchorId: anchor.anchorId || anchor.id,
    anchorType: anchor.anchorType || anchor.type || 'custom',
    anchorLabel: anchor.anchorLabel || anchor.label || anchor.anchorId || anchor.id || '',
    abs: [anchor.abs[0], anchor.abs[1]]
  }
}

function addZone({ name, floor, anchor }) {
  const a = _normalizeAnchor(anchor)
  const zones = loadZones()
  const zone = {
    zoneId: nextZoneId(zones),
    name: (name || '').trim() || '未命名分区',
    floor: floor,
    anchor: a,
    createdAt: new Date().toISOString()
  }
  zones.push(zone)
  saveZones(zones)
  return zone
}

function updateZone(zoneId, patch) {
  const zones = loadZones()
  const z = zones.find((x) => x.zoneId === zoneId)
  if (!z) return null
  if (patch.name !== undefined) z.name = (patch.name || '').trim() || z.name
  if (patch.floor !== undefined) z.floor = patch.floor
  if (patch.anchor !== undefined) {
    z.anchor = _normalizeAnchor(patch.anchor)
  }
  saveZones(zones)
  return z
}

function removeZone(zoneId) {
  const zones = loadZones().filter((z) => z.zoneId !== zoneId)
  saveZones(zones)
}

function getZone(zoneId) {
  return loadZones().find((z) => z.zoneId === zoneId) || null
}

// 相对坐标：采集点绝对坐标 - 锚点绝对坐标（同坐标系，平面偏移）
function relativeCoords(abs, zone) {
  if (!zone || !zone.anchor || !zone.anchor.abs) return null
  return [abs[0] - zone.anchor.abs[0], abs[1] - zone.anchor.abs[1]]
}

module.exports = {
  loadZones, saveZones, nextZoneId, addZone, updateZone, removeZone, getZone, relativeCoords
}
