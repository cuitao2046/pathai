// utils/sampler.js — 采样会话：与生产 scan-window 同口径（截断 + 中位数），
// 定长停留后逐信标产出 {n, rssiMedian, rssiMin, rssiMax, rssiStd, truthDistanceM}。
// 纯逻辑、不依赖 wx，可直接用 node:test 覆盖（避免"标定一套、线上另一套"）。

const RSSI_MIN_DBM = -100
const RSSI_MAX_DBM = -20
const WINDOW_MS = 2000

function clampRssi(rssi) {
  if (typeof rssi !== 'number' || !Number.isFinite(rssi)) return null
  return Math.min(RSSI_MAX_DBM, Math.max(RSSI_MIN_DBM, rssi))
}

// 与生产 scan-window.ts 一致：偶数个取中间两值平均后取整（API-05 rssi 为整数）。
function medianInt(values) {
  const sorted = values.slice().sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 === 1
    ? sorted[mid]
    : Math.round((sorted[mid - 1] + sorted[mid]) / 2)
}

function beaconKey(uuid, major, minor) {
  return `${String(uuid || '').trim().toLowerCase()}|${major}|${minor}`
}

function buildCatalog(beacons) {
  const map = new Map()
  for (const b of beacons || []) {
    map.set(beaconKey(b.uuid, b.major, b.minor), b)
  }
  return map
}

// 真值距离：标定点真值坐标与台账信标坐标的平面欧氏距离（单位 m）。
function truthDistance(point, beacon) {
  const a = point && point.truth
  if (!a || a.length !== 2) throw new Error('标定点缺少真值坐标')
  return Math.hypot(a[0] - beacon.x, a[1] - beacon.y)
}

function stdDev(values) {
  if (values.length === 0) return 0
  const mean = values.reduce((s, v) => s + v, 0) / values.length
  const variance = values.reduce((s, v) => s + (v - mean) * (v - mean), 0) / values.length
  return Math.sqrt(variance)
}

class SessionSampler {
  /**
   * @param {{catalog: Map, point: object, windowMs?: number, startedAtMs?: number}} opts
   *   catalog: buildCatalog(BEACONS) 的结果
   */
  constructor(opts) {
    this.catalog = opts.catalog
    this.point = opts.point
    this.windowMs = opts.windowMs || WINDOW_MS
    this.startedAtMs = opts.startedAtMs || Date.now()
    this.byBeacon = new Map() // beaconId -> number[]
    this.unknownKeys = new Set()
  }

  /** 推入一条扫描样本；未知三元组忽略（不虚构 beaconId）。 */
  push(sample) {
    const rssi = clampRssi(sample && sample.rssi)
    if (rssi === null) return false
    const key = beaconKey(sample.uuid, sample.major, sample.minor)
    const beacon = this.catalog.get(key)
    if (!beacon) {
      this.unknownKeys.add(key)
      return false
    }
    const list = this.byBeacon.get(beacon.beaconId)
    if (list) list.push(rssi)
    else this.byBeacon.set(beacon.beaconId, [rssi])
    return true
  }

  /** 当前实时表（按 RSSI 中位数从强到弱）。 */
  snapshot() {
    const rows = []
    for (const [beaconId, values] of this.byBeacon) {
      const beacon = this._beaconById(beaconId)
      rows.push(this._row(beacon, values))
    }
    return rows.sort((a, b) => b.rssiMedian - a.rssiMedian)
  }

  /** 结束本次停留，产出可直接写入 static_samples.json 的会话对象。 */
  finish(opts) {
    const durationS = (opts && opts.durationS) || Math.max(0, Math.round((Date.now() - this.startedAtMs) / 1000))
    return {
      startedAt: new Date(this.startedAtMs).toISOString(),
      durationS,
      observations: this.snapshot()
    }
  }

  get unknownCount() { return this.unknownKeys.size }

  _beaconById(beaconId) {
    for (const b of this.catalog.values()) {
      if (b.beaconId === beaconId) return b
    }
    throw new Error(`台账缺少信标 ${beaconId}`)
  }

  _row(beacon, values) {
    return {
      beaconId: beacon.beaconId,
      n: values.length,
      rssiMedian: medianInt(values),
      rssiMin: Math.min(...values),
      rssiMax: Math.max(...values),
      rssiStd: +stdDev(values).toFixed(1),
      truthDistanceM: +truthDistance(this.point, beacon).toFixed(2)
    }
  }
}

module.exports = {
  RSSI_MIN_DBM,
  RSSI_MAX_DBM,
  WINDOW_MS,
  clampRssi,
  medianInt,
  beaconKey,
  buildCatalog,
  truthDistance,
  stdDev,
  SessionSampler
}
