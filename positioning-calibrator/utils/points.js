// utils/points.js — 标定点 CRUD（现场手输：ID/楼层/真值坐标/场景/标记方式/环境备注）
const KEY = 'cal:points:v1'

const SCENES = [
  '走廊中段',
  '走廊交叉口',
  '门洞',
  '楼梯口',
  '电梯厅',
  '开阔大厅',
  '靠窗遮挡',
  '信号弱区',
  'NLOS 对照',
  '其他'
]

function _read() {
  try { return wx.getStorageSync(KEY) || [] } catch (e) { return [] }
}
function _write(list) {
  try { wx.setStorageSync(KEY, list) } catch (e) {}
}

function listPoints() {
  return _read().slice().sort((a, b) => a.pointId.localeCompare(b.pointId))
}

function nextPointId() {
  const used = new Set(_read().map((p) => p.pointId))
  for (let i = 1; i <= 99; i++) {
    const id = `CP-${String(i).padStart(2, '0')}`
    if (!used.has(id)) return id
  }
  throw new Error('标定点数量已达上限')
}

function _validate(input) {
  const floor = Number(input.floor)
  if (!Number.isInteger(floor) || floor < 1) throw new Error('楼层必须是 ≥1 的整数')
  const truth = [Number(input.truthX), Number(input.truthY)]
  if (!truth.every((v) => Number.isFinite(v))) throw new Error('真值坐标必须是数字')
  const scene = String(input.scene || '').trim()
  if (!SCENES.includes(scene)) throw new Error('请选择场景类型')
  return {
    floor,
    truth,
    scene,
    envNote: String(input.envNote || '').trim(),
    markType: String(input.markType || '').trim()
  }
}

function addPoint(input) {
  const list = _read()
  const point = Object.assign(
    { pointId: nextPointId(), createdAt: new Date().toISOString() },
    _validate(input)
  )
  list.push(point)
  _write(list)
  return point
}

function updatePoint(pointId, input) {
  const list = _read()
  const index = list.findIndex((p) => p.pointId === pointId)
  if (index < 0) throw new Error(`标定点不存在：${pointId}`)
  list[index] = Object.assign({}, list[index], _validate(input), { updatedAt: new Date().toISOString() })
  _write(list)
  return list[index]
}

function removePoint(pointId) {
  _write(_read().filter((p) => p.pointId !== pointId))
}

function getPoint(pointId) {
  return _read().find((p) => p.pointId === pointId) || null
}

function clearPoints() {
  _write([])
}

module.exports = {
  SCENES,
  listPoints,
  getPoint,
  nextPointId,
  addPoint,
  updatePoint,
  removePoint,
  clearPoints
}
