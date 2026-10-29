// pages/export/export.js — 复核并导出 static_samples.json
const ref = require('../../data/reference_beacons.js')
const cstore = require('../../utils/cstore.js')
const app = getApp()

Page({
  data: {
    venueId: ref.VENUE_ID,
    mapVersion: ref.MAP_VERSION,
    stats: {},
    warnings: [],
    preview: '',
    exportedPath: ''
  },

  onShow() { this.refresh() },

  refresh() {
    const st = cstore.stats()
    const built = cstore.buildExport({
      operator: cstore.getOperator(),
      device: app.globalData.device,
      appVersion: app.globalData.appVersion
    })
    const preview = JSON.stringify(built.data, null, 2)
    this.setData({
      stats: st,
      warnings: built.warnings,
      preview: preview.length > 4000
        ? preview.slice(0, 4000) + '\n… (预览截断，完整内容见导出文件)'
        : preview
    })
  },

  copyJson() {
    const built = cstore.buildExport({
      operator: cstore.getOperator(),
      device: app.globalData.device,
      appVersion: app.globalData.appVersion
    })
    wx.setClipboardData({
      data: JSON.stringify(built.data, null, 2),
      success: () => wx.showToast({ title: '已复制' })
    })
  },

  doExport() {
    if (this.data.stats.sessionCount === 0) {
      wx.showToast({ title: '暂无采样数据', icon: 'none' })
      return
    }
    cstore.exportToFile({
      operator: cstore.getOperator(),
      device: app.globalData.device,
      appVersion: app.globalData.appVersion
    }).then((res) => {
      this.setData({ exportedPath: res.path })
      wx.setClipboardData({
        data: res.json,
        success: () => {
          let tip = 'JSON 已复制到剪贴板，并写入文件：\n' + res.path
          if (res.warnings.length) tip += '\n\n注意：\n- ' + res.warnings.join('\n- ')
          tip += '\n\n可在开发者工具「文件系统」导出，或点下方「分享文件」发送到电脑。'
          try {
            if (wx.shareFileMessage) wx.shareFileMessage({ filePath: res.path, fail: () => {} })
          } catch (e) {}
          wx.showModal({ title: '导出成功', content: tip, showCancel: false })
        }
      })
    }).catch((e) => {
      wx.showModal({ title: '导出失败', content: (e && e.errMsg) || '写文件失败', showCancel: false })
    })
  }
})
