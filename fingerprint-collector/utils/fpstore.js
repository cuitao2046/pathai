// utils/fpstore.js — 采集记录存储 / 会话 / 导出
// 存储：wx.setStorageSync（运行期持久化，避免小程序被回收丢失）
// 导出：构造规范化 JSON，写 USER_DATA_PATH 文件 + 剪贴板兜底

const STORAGE_KEY = 'fp_records_v1'
const SESSION_KEY = 'fp_session_v1'
const zonesMod = require('./zones.js')

function loadRecords() {
  try { return wx.getStorageSync(STORAGE_KEY) || [] } catch (e) { return [] }
}
function saveRecords(records) {
  try { wx.setStorageSync(STORAGE_KEY, records) } catch (e) {}
}

function loadSession() {
  try { return wx.getStorageSync(SESSION_KEY) || null } catch (e) { return null }
}
function saveSession(s) {
  try { wx.setStorageSync(SESSION_KEY, s) } catch (e) {}
}

// fp: {id, floor, coordinates:[x,y], regionType}
// beacons: 归一化后的信标数组（见 utils/beacon.normalize）
// opts: { capturedAt, zone }
//   zone: 来自 utils/zones.getZone 的分区对象；指定后写入分区信息与相对坐标
//         相对坐标 = fp.coordinates - zone.anchor.abs（同坐标系平面偏移）
// 注意：无论是否分区，绝对坐标 coordinates 始终保留（满足「保留所有采集结果」并可复算）。
function addSample(fp, beacons, opts) {
  opts = opts || {}
  const capturedAt = opts.capturedAt
  const zone = opts.zone || null
  const records = loadRecords()
  const existing = records.filter((r) => r.fpId === fp.id)
  const captureIndex = existing.length + 1
  const rel = (zone && zone.anchor && zone.anchor.abs)
    ? [fp.coordinates[0] - zone.anchor.abs[0], fp.coordinates[1] - zone.anchor.abs[1]]
    : null
  const rec = {
    fpId: fp.id,
    floor: fp.floor,
    coordinates: fp.coordinates,
    relCoordinates: rel,
    zoneId: zone ? zone.zoneId : null,
    zoneName: zone ? zone.name : null,
    anchor: (zone && zone.anchor) ? {
      anchorId: zone.anchor.anchorId,
      anchorType: zone.anchor.anchorType,
      anchorLabel: zone.anchor.anchorLabel,
      abs: zone.anchor.abs
    } : null,
    regionType: fp.regionType || '',
    captureIndex: captureIndex,
    collectedAt: capturedAt || new Date().toISOString(),
    beaconCount: beacons.length,
    beacons: beacons.map((b) => ({
      uuid: b.uuid,
      major: b.major,
      minor: b.minor,
      rssi: b.rssi,
      txPower: b.txPower,
      accuracy: b.accuracy
    }))
  }
  records.push(rec)
  saveRecords(records)
  return rec
}

function recordsFor(fpId) {
  return loadRecords().filter((r) => r.fpId === fpId)
}
function allRecords() { return loadRecords() }
function clearAll() { saveRecords([]) }

// 分区统计：每个分区（含「未分区」）的记录数 / 覆盖 FP 点数
function zoneStats() {
  const recs = loadRecords()
  const zones = zonesMod.loadZones()
  const map = {}
  zones.forEach((z) => { map[z.zoneId] = { zoneId: z.zoneId, name: z.name, recordCount: 0, fpSet: {} } })
  const none = { zoneId: null, name: '（未分区/全局）', recordCount: 0, fpSet: {} }
  for (const r of recs) {
    const key = r.zoneId || null
    const t = key
      ? (map[key] || (map[key] = { zoneId: key, name: key, recordCount: 0, fpSet: {} }))
      : none
    t.recordCount++
    t.fpSet[r.fpId] = true
  }
  const out = Object.values(map)
  if (none.recordCount) out.push(none)
  return out.map((z) => ({
    zoneId: z.zoneId, name: z.name,
    recordCount: z.recordCount, fpCount: Object.keys(z.fpSet).length
  }))
}

// 汇总统计：用于首页/导出页
function stats() {
  const recs = loadRecords()
  const fpSet = {}
  let sampleSum = 0
  for (const r of recs) {
    fpSet[r.fpId] = true
    sampleSum += r.beaconCount
  }
  return {
    recordCount: recs.length,
    fpCount: Object.keys(fpSet).length,
    beaconSampleSum: sampleSum
  }
}

// 构造可直接入库的导出 JSON
// opts: { zoneIds?: string[] } —— 提供则只导出指定分区（多选），否则导出全部
function buildExport(session, device, opts) {
  opts = opts || {}
  const selectedZoneIds = (opts.zoneIds && opts.zoneIds.length) ? opts.zoneIds : null
  let records = loadRecords()
  if (selectedZoneIds) {
    const set = new Set(selectedZoneIds)
    records = records.filter((r) => r.zoneId && set.has(r.zoneId))
  }
  const zonesMeta = selectedZoneIds
    ? zonesMod.loadZones()
        .filter((z) => selectedZoneIds.indexOf(z.zoneId) >= 0)
        .map((z) => ({ zoneId: z.zoneId, name: z.name, floor: z.floor, anchor: z.anchor }))
    : []
  const fps = new Set(records.map((r) => r.fpId))
  return {
    schemaVersion: '1.1.0',
    type: 'ble_fingerprint_collection',
    venueId: session.venueId,
    venueName: session.venueName,
    dataset: session.dataset,
    exportedAt: new Date().toISOString(),
    appVersion: '1.0.0',
    operator: session.operator || '',
    device: device || null,
    beaconConfig: { uuid: session.beaconUuid, majors: [1, 2] },
    exportScope: selectedZoneIds ? 'selected_zones' : 'all',
    zones: zonesMeta,
    recordCount: records.length,
    fpCount: fps.size,
    records: records
  }
}

// 写文件到 USER_DATA_PATH，返回 {path, json}
function exportToFile(session, device, opts) {
  const data = buildExport(session, device, opts)
  const json = JSON.stringify(data, null, 2)
  const fs = wx.getFileSystemManager()
  const path = `${wx.env.USER_DATA_PATH}/fingerprint_export_${Date.now()}.json`
  return new Promise((resolve, reject) => {
    fs.writeFile({
      filePath: path,
      data: json,
      encoding: 'utf8',
      success: () => resolve({ path: path, json: json }),
      fail: (e) => reject(e)
    })
  })
}

module.exports = {
  loadRecords, saveRecords, addSample, recordsFor, allRecords, clearAll,
  loadSession, saveSession, stats, zoneStats, buildExport, exportToFile
}
