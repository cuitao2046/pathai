// pages/index/index.js
const fpstore = require('../../utils/fpstore.js')
const zones = require('../../utils/zones.js')
const app = getApp()

Page({
  data: {
    venueId: '',
    venueName: '',
    operator: '',
    dataset: 'route',
    datasetLabel: '',
    totalPoints: 0,
    collectedFp: 0,
    sampleCount: 0,
    zoneCount: 0
  },

  onShow() { this.refresh() },

  refresh() {
    const g = app.globalData
    const session = fpstore.loadSession() || g.session
    const ds = g.grid.datasets[session.dataset] || g.grid.datasets.route
    const all = fpstore.allRecords()
    const collectedIds = new Set(all.map((r) => r.fpId))
    const inDs = ds.points.filter((p) => collectedIds.has(p.id)).length
    const st = fpstore.stats()
    this.setData({
      venueId: g.venueId,
      venueName: g.venueName,
      operator: session.operator,
      dataset: session.dataset,
      datasetLabel: ds.label,
      totalPoints: ds.points.length,
      collectedFp: inDs,
      sampleCount: st.recordCount,
      zoneCount: zones.loadZones().length
    })
    app.globalData.session = session
  },

  onOperatorInput(e) {
    const v = e.detail.value
    const s = fpstore.loadSession() || app.globalData.session
    s.operator = v
    fpstore.saveSession(s)
    app.globalData.session = s
    this.setData({ operator: v })
  },

  onDatasetChange(e) {
    const ds = e.currentTarget.dataset.ds
    const s = fpstore.loadSession() || app.globalData.session
    s.dataset = ds
    fpstore.saveSession(s)
    app.globalData.session = s
    this.refresh()
  },

  goPoints() { wx.navigateTo({ url: '/pages/points/points' }) },
  goExport() { wx.navigateTo({ url: '/pages/export/export' }) },
  goZones() { wx.navigateTo({ url: '/pages/zones/zones' }) },

  clearAll() {
    wx.showModal({
      title: '清空所有采集数据',
      content: '将删除本机全部指纹样本，不可恢复。确认？',
      confirmColor: '#dc2626',
      success: (r) => {
        if (r.confirm) {
          fpstore.clearAll()
          this.refresh()
          wx.showToast({ title: '已清空', icon: 'success' })
        }
      }
    })
  }
})
