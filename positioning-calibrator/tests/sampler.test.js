// node --test positioning-calibrator/tests
const test = require('node:test')
const assert = require('node:assert')
const {
  RSSI_MIN_DBM,
  RSSI_MAX_DBM,
  clampRssi,
  medianInt,
  buildCatalog,
  truthDistance,
  SessionSampler
} = require('../utils/sampler.js')
const ref = require('../data/reference_beacons.js')

const UUID = 'B9407F30-F5F8-466E-AFF9-25556B57FE6D'
const CATALOG = buildCatalog([
  { beaconId: 'B-NEAR', floor: 1, x: 0, y: 3, uuid: UUID, major: 1, minor: 1 },
  { beaconId: 'B-FAR', floor: 1, x: 4, y: 0, uuid: UUID, major: 1, minor: 2 }
])

function push(sampler, rssi, minor) {
  return sampler.push({ uuid: UUID, major: 1, minor, rssi })
}

test('clampRssi 截断到契约区间并丢弃非法值', () => {
  assert.equal(clampRssi(-150), RSSI_MIN_DBM)
  assert.equal(clampRssi(0), RSSI_MAX_DBM)
  assert.equal(clampRssi(-67), -67)
  assert.equal(clampRssi(NaN), null)
  assert.equal(clampRssi(null), null)
})

test('medianInt 与生产 scan-window 一致：偶数取中间均值后取整', () => {
  assert.equal(medianInt([-60, -64, -62]), -62)
  // JS Math.round(-60.5) = -60（向 +∞ 取整）；生产端同样使用 Math.round
  assert.equal(medianInt([-60, -61]), -60)
})

test('truthDistance 为平面欧氏距离（3-4-5）', () => {
  assert.equal(truthDistance({ truth: [0, 0] }, { x: 3, y: 4 }), 5)
  assert.ok(Math.abs(truthDistance({ truth: [0, 0] }, { x: 0, y: 3 }) - 3) < 1e-9)
})

test('truthDistance 与真实台账坐标一致', () => {
  const first = ref.BEACONS[0]
  const point = { truth: [first.x + 3, first.y + 4] }
  assert.ok(Math.abs(truthDistance(point, first) - 5) < 1e-9)
})

test('采样会话按信标聚合并输出中位数/min/max/σ/真值距离', () => {
  const sampler = new SessionSampler({
    catalog: CATALOG,
    point: { truth: [0, 0] },
    startedAtMs: Date.parse('2026-11-01T09:00:00Z')
  })
  assert.equal(push(sampler, -60, 1), true)
  assert.equal(push(sampler, -64, 1), true)
  assert.equal(push(sampler, -62, 1), true)
  push(sampler, -70, 2)

  const rows = sampler.snapshot()
  assert.equal(rows.length, 2)
  const near = rows.find((r) => r.beaconId === 'B-NEAR')
  assert.deepEqual(near, {
    beaconId: 'B-NEAR',
    n: 3,
    rssiMedian: -62,
    rssiMin: -64,
    rssiMax: -60,
    rssiStd: 1.6,
    truthDistanceM: 3
  })

  const session = sampler.finish({ durationS: 90 })
  assert.equal(session.startedAt, '2026-11-01T09:00:00.000Z')
  assert.equal(session.durationS, 90)
  assert.equal(session.observations.length, 2)
})

test('截断后的样本参与统计；未知三元组被忽略且不虚构 beaconId', () => {
  const sampler = new SessionSampler({ catalog: CATALOG, point: { truth: [0, 0] } })
  push(sampler, -150, 1) // 截断为 -100
  push(sampler, -120, 2) // 截断为 -100
  assert.equal(sampler.push({ uuid: UUID, major: 9, minor: 9, rssi: -50 }), false)
  assert.equal(sampler.unknownCount, 1)
  const rows = sampler.snapshot()
  assert.equal(rows.length, 2)
  assert.ok(rows.every((r) => r.rssiMedian === -100))
})

test('参考数据来自真实台账：61 枚、ID/三元组唯一、摘要与地图版本有效', () => {
  assert.equal(ref.VENUE_ID, 'school-building-01')
  assert.equal(ref.BEACONS.length, 61)
  assert.match(ref.MAP_VERSION, /^\d{4}-\d{2}-\d{2}\.\d+$/)
  assert.match(ref.LEDGER_SHA256, /^[0-9a-f]{64}$/)
  assert.equal(new Set(ref.BEACONS.map((b) => b.beaconId)).size, 61)
  assert.equal(new Set(ref.BEACONS.map((b) => `${b.uuid}|${b.major}|${b.minor}`)).size, 61)
  assert.deepEqual(ref.UUIDS, [UUID])
})
