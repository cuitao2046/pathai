// utils/cstore.js — 标定会话存储与导出（static_samples.json 契约见 docs/28 §4.3）
const SESSIONS_KEY = 'cal:sessions:v1'
const OPERATOR_KEY = 'cal:operator:v1'
const APP_VERSION = '1.0.0'

const ref = require('../data/reference_beacons.js')
const pointsMod = require('./points.js')

function loadSessions() {
  try { return wx.getStorageSync(SESSIONS_KEY) || [] } catch (e) { return [] }
}
function saveSessions(list) {
  try { wx.setStorageSync(SESSIONS_KEY, list) } catch (e) {}
}

function getOperator() {
  try { return wx.getStorageSync(OPERATOR_KEY) || '' } catch (e) { return '' }
}
function setOperator(name) {
  try { wx.setStorageSync(OPERATOR_KEY, String(name || '')) } catch (e) {}
}

// session: {startedAt, durationS, trafficWindow, observations[]}
function addSession(pointId, session) {
  const list = loadSessions()
  const sessionIndex = list.filter((s) => s.pointId === pointId).length + 1
  const rec = Object.assign({ pointId, sessionIndex }, session)
  list.push(rec)
  saveSessions(list)
  return rec
}

function sessionsFor(pointId) {
  return loadSessions()
    .filter((s) => s.pointId === pointId)
    .sort((a, b) => a.sessionIndex - b.sessionIndex)
}

function removeSessionsFor(pointId) {
  saveSessions(loadSessions().filter((s) => s.pointId !== pointId))
}

function clearAll() {
  saveSessions([])
}

function stats() {
  const points = pointsMod.listPoints()
  const sessions = loadSessions()
  const sampled = new Set(sessions.map((s) => s.pointId))
  let beaconPairs = 0
  for (const s of sessions) beaconPairs += (s.observations || []).length
  return {
    pointCount: points.length,
    sampledPointCount: sampled.size,
    sessionCount: sessions.length,
    beaconPairs
  }
}

// 生成 static_samples.json；warnings 用于导出页提示，不阻塞导出。
function buildExport(meta) {
  meta = meta || {}
  const points = pointsMod.listPoints()
  const sessions = loadSessions()
  const warnings = []
  if (points.length === 0) warnings.push('还没有标定点')
  const outPoints = points.map((p) => {
    const list = sessionsFor(p.pointId)
    if (list.length === 0) warnings.push(`${p.pointId} 还没有采样会话`)
    if (list.length > 0 && list.length < 3) warnings.push(`${p.pointId} 仅 ${list.length} 次，建议每点 3–5 次`)
    return {
      pointId: p.pointId,
      floor: p.floor,
      truth: p.truth,
      scene: p.scene,
      envNote: p.envNote,
      markType: p.markType,
      sessions: list.map((s) => ({
        sessionIndex: s.sessionIndex,
        startedAt: s.startedAt,
        durationS: s.durationS,
        trafficWindow: s.trafficWindow,
        observations: s.observations || []
      }))
    }
  })
  return {
    data: {
      schemaVersion: '1.0',
      type: 'positioning_calibration_static',
      venueId: ref.VENUE_ID,
      operator: meta.operator || getOperator(),
      device: meta.device || null,
      appVersion: meta.appVersion || APP_VERSION,
      beaconLedgerSha256: ref.LEDGER_SHA256,
      mapVersion: ref.MAP_VERSION,
      windowMs: ref.WINDOW_MS,
      exportedAt: new Date().toISOString(),
      points: outPoints
    },
    warnings
  }
}

function exportToFile(meta) {
  const built = buildExport(meta)
  const json = JSON.stringify(built.data, null, 2)
  const fs = wx.getFileSystemManager()
  const path = `${wx.env.USER_DATA_PATH}/static_samples_${Date.now()}.json`
  return new Promise((resolve, reject) => {
    fs.writeFile({
      filePath: path,
      data: json,
      encoding: 'utf8',
      success: () => resolve({ path, json, warnings: built.warnings }),
      fail: (e) => reject(e)
    })
  })
}

module.exports = {
  getOperator,
  setOperator,
  loadSessions,
  addSession,
  sessionsFor,
  removeSessionsFor,
  clearAll,
  stats,
  buildExport,
  exportToFile
}
