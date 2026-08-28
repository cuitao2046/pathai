// pages/points/points.js — 选择 FP 采集点
const app = getApp()
const fpstore = require('../../utils/fpstore.js')

Page({
  data: {
    datasetLabel: '',
    total: 0,
    floorFilter: 'all',
    keyword: '',
    filtered: [],
    collectedSet: {}
  },

  onShow() { this.refresh() },

  refresh() {
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    const ds = g.grid.datasets[s.dataset] || g.grid.datasets.route
    const recs = fpstore.allRecords()
    const cs = {}
    recs.forEach((r) => { cs[r.fpId] = (cs[r.fpId] || 0) + 1 })
    this.setData({
      datasetLabel: ds.label,
      total: ds.points.length,
      points: ds.points,
      collectedSet: cs
    })
    this.applyFilter()
  },

  applyFilter() {
    const f = this.data.floorFilter
    const kw = (this.data.keyword || '').trim().toLowerCase()
    const cs = this.data.collectedSet
    let out = this.data.points || []
    if (f !== 'all') out = out.filter((p) => String(p.floor) === String(f))
    if (kw) out = out.filter((p) =>
      p.id.toLowerCase().includes(kw) ||
      (p.nearNodeId || '').toLowerCase().includes(kw))
    // 已采集点置顶
    out = out.slice().sort((a, b) => ((cs[b.id] ? 1 : 0) - (cs[a.id] ? 1 : 0)))
    this.setData({ filtered: out })
  },

  onFloor(e) { this.setData({ floorFilter: e.currentTarget.dataset.f }); this.applyFilter() },
  onSearch(e) { this.setData({ keyword: e.detail.value }); this.applyFilter() },

  tapPoint(e) {
    const id = e.currentTarget.dataset.id
    wx.navigateTo({ url: '/pages/collect/collect?fpId=' + encodeURIComponent(id) })
  }
})
