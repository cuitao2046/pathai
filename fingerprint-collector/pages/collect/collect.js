// pages/collect/collect.js — 在指定 FP 点扫描 iBeacon 并采集指纹
const app = getApp()
const fpstore = require('../../utils/fpstore.js')
const beacon = require('../../utils/beacon.js')
const zones = require('../../utils/zones.js')

Page({
  data: {
    fp: null,
    scanning: false,
    beacons: [],
    sampleCount: 0,
    errMsg: '',
    lastUpdate: '',
    // 分区采集上下文
    zones: [],
    zoneRange: ['无分区（绝对坐标）'],  // picker 选项
    zoneIndex: 0,          // 0 = 无分区（全局）
    currentZone: null,     // 选中的分区对象（含 anchor）
    relPreview: null       // [relX, relY] 预览
  },

  onLoad(q) {
    const id = decodeURIComponent(q.fpId || '')
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    const ds = g.grid.datasets[s.dataset] || g.grid.datasets.route
    const fp = ds.points.find((p) => p.id === id) || null
    const existing = fpstore.recordsFor(id)
    const zlist = zones.loadZones()
    const zoneRange = ['无分区（绝对坐标）'].concat(
      zlist.map((z) => z.name + '（F' + z.floor + '）')
    )
    this.setData({
      fp: fp,
      sampleCount: existing.length,
      errMsg: fp ? '' : '未在当前数据集找到该点',
      zones: zlist,
      zoneRange: zoneRange,
      zoneIndex: 0,
      currentZone: null,
      relPreview: null
    })
    wx.setNavigationBarTitle({ title: fp ? ('采集 ' + fp.id) : '采集' })
  },

  // 选择活动分区（0 = 无分区）
  onZoneChange(e) {
    const idx = parseInt(e.detail.value, 10)
    const z = idx > 0 ? this.data.zones[idx - 1] : null
    let relPreview = null
    if (z && this.data.fp) relPreview = zones.relativeCoords(this.data.fp.coordinates, z)
    this.setData({ zoneIndex: idx, currentZone: z, relPreview: relPreview })
  },

  zonePickerRange() {
    return ['无分区（绝对坐标）'].concat(
      this.data.zones.map((z) => z.name + '（F' + z.floor + '）')
    )
  },

  toggleScan() {
    if (this.data.scanning) return this.stopScan()
    const uuid = app.globalData.beaconUuid
    beacon.start([uuid],
      (bs) => { this.setData({ beacons: bs, lastUpdate: this._now() }) },
      (err) => { this.setData({ errMsg: '蓝牙服务异常：' + ((err && err.errMsg) || '请开启蓝牙') }) }
    ).then(() => {
      this.setData({ scanning: true, errMsg: '' })
    }).catch((e) => {
      this.setData({ scanning: false, errMsg: '无法开始扫描：' + ((e && e.errMsg) || '请确认已开启蓝牙并授权') })
    })
  },

  stopScan() {
    beacon.stop().then(() => this.setData({ scanning: false }))
  },

  capture() {
    const fp = this.data.fp
    if (!fp) return
    if (!this.data.beacons.length) {
      wx.showToast({ title: '未检测到信标', icon: 'none' })
      return
    }
    // 传入当前活动分区：有则写入 zoneId/anchor/relCoordinates，无则仅绝对坐标
    const rec = fpstore.addSample(fp, this.data.beacons, { zone: this.data.currentZone || undefined })
    const cnt = fpstore.recordsFor(fp.id).length
    this.setData({ sampleCount: cnt })
    const tag = this.data.currentZone ? (' · 区' + this.data.currentZone.zoneId) : ''
    wx.showToast({ title: '已采集 #' + rec.captureIndex + tag, icon: 'success' })
  },

  goBack() {
    this.stopScan()
    wx.navigateBack()
  },

  onUnload() { this.stopScan() },
  onHide() { /* 保留扫描，便于切后台仍采集；如需省电可在此 stopScan */ },

  _now() {
    const d = new Date()
    const p = (n) => (n < 10 ? '0' + n : '' + n)
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds())
  }
})
