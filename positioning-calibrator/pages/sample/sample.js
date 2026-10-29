// pages/sample/sample.js — 定长停留采样（默认 90s，可选 60/90/120）
const ref = require('../../data/reference_beacons.js')
const beacon = require('../../utils/beacon.js')
const samplerMod = require('../../utils/sampler.js')
const pointsMod = require('../../utils/points.js')
const cstore = require('../../utils/cstore.js')

const DURATION_OPTIONS = [60, 90, 120]
const TRAFFIC_OPTIONS = [
  { label: '高峰', value: 'peak' },
  { label: '低谷', value: 'offpeak' }
]
const CATALOG = samplerMod.buildCatalog(ref.BEACONS)

Page({
  data: {
    points: [],
    pointIndex: 0,
    currentPoint: null,
    durations: DURATION_OPTIONS,
    durationIndex: 1,
    trafficLabels: TRAFFIC_OPTIONS.map((t) => t.label),
    trafficIndex: 0,
    sampling: false,
    remaining: DURATION_OPTIONS[1],
    observations: [],
    unknownCount: 0,
    statusText: '选择标定点后开始采样',
    lastSaved: ''
  },

  sampler: null,
  timer: null,
  startedAtMs: 0,

  onShow() {
    const points = pointsMod.listPoints()
    const index = Math.min(this.data.pointIndex, Math.max(0, points.length - 1))
    this.setData({ points, pointIndex: index, currentPoint: points[index] || null })
    if (points.length === 0) {
      this.setData({ statusText: '还没有标定点，请先到「标定点管理」新增' })
    }
  },

  onLoad() {
    // 保底：首次进入也刷新一次
    this.onShow()
  },

  onPointChange(e) {
    const index = Number(e.detail.value)
    this.setData({
      pointIndex: index,
      currentPoint: this.data.points[index] || null
    })
  },

  onDurationChange(e) {
    this.setData({ durationIndex: Number(e.detail.value) })
  },

  onTrafficChange(e) {
    this.setData({ trafficIndex: Number(e.detail.value) })
  },

  onStart() {
    if (this.data.sampling) return
    const point = this.data.currentPoint
    if (!point) {
      wx.showToast({ title: '请先选择标定点', icon: 'none' })
      return
    }
    const durationS = this.data.durations[this.data.durationIndex]
    this.startedAtMs = Date.now()
    this.sampler = new samplerMod.SessionSampler({
      catalog: CATALOG,
      point,
      startedAtMs: this.startedAtMs
    })
    this.setData({
      sampling: true,
      remaining: durationS,
      observations: [],
      unknownCount: 0,
      lastSaved: '',
      statusText: `采样中：${point.pointId}，请停留在标记点`
    })

    beacon.start(
      ref.UUIDS,
      (samples) => {
        if (!this.sampler) return
        for (const s of samples) this.sampler.push(s)
      },
      () => this.setData({ statusText: '蓝牙服务不可用，请检查系统蓝牙' })
    ).catch((err) => {
      this.sampler = null
      if (this.timer) clearInterval(this.timer)
      this.timer = null
      this.setData({ sampling: false, statusText: '开始扫描失败' })
      wx.showModal({
        title: '无法开始扫描',
        content: (err && err.errMsg) || '请开启蓝牙后重试',
        showCancel: false
      })
    })

    this.timer = setInterval(() => this.tick(), 1000)
  },

  tick() {
    if (!this.data.sampling || !this.sampler) return
    const durationS = this.data.durations[this.data.durationIndex]
    const elapsedS = (Date.now() - this.startedAtMs) / 1000
    const remaining = Math.max(0, Math.ceil(durationS - elapsedS))
    const observations = this.sampler.snapshot()
    this.setData({
      remaining,
      observations,
      unknownCount: this.sampler.unknownCount
    })
    if (remaining <= 0) this.finish(true)
  },

  onStop() { this.finish(false) },

  finish(auto) {
    if (!this.data.sampling || !this.sampler) return
    const point = this.data.currentPoint
    const durationS = this.data.durations[this.data.durationIndex]
    const elapsedS = Math.max(0, Math.round((Date.now() - this.startedAtMs) / 1000))
    clearInterval(this.timer)
    this.timer = null
    beacon.stop()

    const session = this.sampler.finish({ durationS: Math.min(durationS, elapsedS) })
    this.sampler = null
    const traffic = TRAFFIC_OPTIONS[this.data.trafficIndex]
    const observed = session.observations.length
    if (observed > 0) {
      cstore.addSession(point.pointId, {
        startedAt: session.startedAt,
        durationS: session.durationS,
        trafficWindow: traffic.value,
        observations: session.observations
      })
    }
    this.setData({
      sampling: false,
      remaining: 0,
      observations: session.observations,
      statusText: auto ? '本次采样完成' : '已手动结束',
      lastSaved: observed > 0
        ? `已保存 ${point.pointId} 第 ${cstore.sessionsFor(point.pointId).length} 次（${traffic.label}，${observed} 个信标）`
        : '本次没有可解析信标，未保存'
    })
    if (observed === 0) {
      wx.showToast({ title: '未解析到信标', icon: 'none' })
    } else if (observed < 3) {
      wx.showToast({ title: '可解析信标不足 3 个', icon: 'none' })
    } else {
      wx.showToast({ title: '采样已保存', icon: 'success' })
    }
    this.onShow()
  },

  onUnload() {
    if (this.timer) clearInterval(this.timer)
    this.timer = null
    if (this.data.sampling) {
      // 意外退出：停留足够久才落盘，避免把误触计入样本（不触发 setData）
      const elapsedS = Math.round((Date.now() - this.startedAtMs) / 1000)
      if (elapsedS >= 30 && this.sampler) this.saveOnUnload(elapsedS)
    }
    beacon.stop()
    this.sampler = null
  },

  /** 页面卸载时静默保存（不走 setData/onShow）。 */
  saveOnUnload(elapsedS) {
    const durationS = this.data.durations[this.data.durationIndex]
    const session = this.sampler.finish({ durationS: Math.min(durationS, elapsedS) })
    if (session.observations.length > 0) {
      const traffic = TRAFFIC_OPTIONS[this.data.trafficIndex]
      cstore.addSession(this.data.currentPoint.pointId, {
        startedAt: session.startedAt,
        durationS: session.durationS,
        trafficWindow: traffic.value,
        observations: session.observations
      })
    }
  }
})
