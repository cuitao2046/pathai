// app.js — 全局状态：场地配置 + 采集会话元数据
// FP 网格来自 data/fingerprint_grid.js（AUTO-GENERATED，由 result/fingerprint_grid*.json 转换）
const fpGrid = require('./data/fingerprint_grid.js')

App({
  globalData: {
    venueId: fpGrid.venueId,
    venueName: fpGrid.venueName,
    gridVersion: fpGrid.version,

    // iBeacon 统一 uuid（取自 result/ble_deployment.json：全部信标共用同一 uuid）
    // major 1/2 分别对应 F1/F2（与信标部署 major 映射一致）
    beaconUuid: 'B9407F30-F5F8-466E-AFF9-25556B57FE6D',
    majorByFloor: { 1: 1, 2: 2 },

    grid: fpGrid,

    // 采集会话（持久化于 utils/fpstore.saveSession）
    session: {
      operator: '',
      dataset: 'route', // 'route' (路线网格647点) | 'full' (全楼网格1434点)
      venueId: fpGrid.venueId,
      venueName: fpGrid.venueName,
      beaconUuid: 'B9407F30-F5F8-466E-AFF9-25556B57FE6D',
      device: null
    }
  },

  onLaunch() {
    // 采集设备信息（写入导出 JSON，便于溯源）
    try {
      const dev = (wx.getDeviceInfo && wx.getDeviceInfo()) || {}
      const base = (wx.getAppBaseInfo && wx.getAppBaseInfo()) || {}
      this.globalData.session.device = {
        model: dev.model || '',
        system: dev.system || '',
        platform: dev.platform || '',
        version: base.version || ''
      }
    } catch (e) {
      this.globalData.session.device = null
    }
  }
})
