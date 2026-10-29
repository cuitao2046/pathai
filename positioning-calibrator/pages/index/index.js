// pages/index/index.js — 标定任务仪表盘
const ref = require('../../data/reference_beacons.js')
const cstore = require('../../utils/cstore.js')

Page({
  data: {
    venueId: ref.VENUE_ID,
    mapVersion: ref.MAP_VERSION,
    ledgerPrefix: ref.LEDGER_SHA256.slice(0, 12),
    beaconCount: ref.BEACONS.length,
    operator: '',
    pointCount: 0,
    sampledPointCount: 0,
    sessionCount: 0,
    beaconPairs: 0
  },

  onShow() {
    const st = cstore.stats()
    this.setData({
      operator: cstore.getOperator(),
      pointCount: st.pointCount,
      sampledPointCount: st.sampledPointCount,
      sessionCount: st.sessionCount,
      beaconPairs: st.beaconPairs
    })
  },

  onOperatorInput(e) {
    const value = e.detail.value
    cstore.setOperator(value)
    this.setData({ operator: value })
  },

  goPoints() { wx.navigateTo({ url: '/pages/points/points' }) },
  goSample() { wx.navigateTo({ url: '/pages/sample/sample' }) },
  goExport() { wx.navigateTo({ url: '/pages/export/export' }) },

  clearSessions() {
    wx.showModal({
      title: '清空采样会话',
      content: '将删除本机全部采样会话（保留标定点），不可恢复。确认？',
      confirmColor: '#dc2626',
      success: (r) => {
        if (r.confirm) {
          cstore.clearAll()
          this.onShow()
          wx.showToast({ title: '已清空', icon: 'success' })
        }
      }
    })
  }
})
