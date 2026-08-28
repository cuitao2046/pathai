// pages/export/export.js — 复核采集记录并导出 JSON（支持按分区多选导出）
const app = getApp()
const fpstore = require('../../utils/fpstore.js')
const zones = require('../../utils/zones.js')

Page({
  data: {
    stats: {},
    zones: [],            // [{zoneId, name, floor, recordCount, fpCount, selected}]
    records: [],
    preview: '',
    exportedPath: '',
    datasetLabel: '',
    operator: '',
    scopeText: '全部'
  },

  onShow() { this.refresh() },

  refresh() {
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    const st = fpstore.stats()
    const stMap = {}
    fpstore.zoneStats().forEach((x) => { if (x.zoneId) stMap[x.zoneId] = x })
    const zlist = zones.loadZones().map((z) => ({
      zoneId: z.zoneId,
      name: z.name,
      floor: z.floor,
      recordCount: (stMap[z.zoneId] || {}).recordCount || 0,
      fpCount: (stMap[z.zoneId] || {}).fpCount || 0,
      selected: false
    }))
    this.setData({
      stats: st,
      zones: zlist,
      records: fpstore.allRecords().slice(-50).reverse(),
      datasetLabel: (g.grid.datasets[s.dataset] || {}).label,
      operator: s.operator,
      scopeText: '全部'
    })
    this.rebuildPreview()
  },

  // 切换某分区的选中态，并重建预览
  toggleZone(e) {
    const zid = e.currentTarget.dataset.id
    const zonesData = this.data.zones.map((z) =>
      z.zoneId === zid ? Object.assign({}, z, { selected: !z.selected }) : z)
    this.setData({ zones: zonesData })
    const sel = zonesData.filter((z) => z.selected)
    this.setData({ scopeText: sel.length ? ('选中 ' + sel.length + ' 个分区') : '全部' })
    this.rebuildPreview()
  },

  rebuildPreview() {
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    const selIds = this.data.zones.filter((z) => z.selected).map((z) => z.zoneId)
    const json = fpstore.buildExport(s, g.globalData.session.device,
      selIds.length ? { zoneIds: selIds } : {})
    const preview = JSON.stringify(json, null, 2)
    this.setData({
      preview: preview.length > 4000 ? (preview.slice(0, 4000) + '\n… (预览截断，完整内容见导出文件)') : preview
    })
  },

  _writeAndShare(selIds) {
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    if (fpstore.allRecords().length === 0) {
      wx.showToast({ title: '暂无数据', icon: 'none' })
      return
    }
    fpstore.exportToFile(s, g.globalData.session.device, selIds ? { zoneIds: selIds } : {})
      .then((res) => {
        this.setData({ exportedPath: res.path })
        wx.setClipboardData({
          data: res.json,
          success: () => {
            let tip = 'JSON 已复制到剪贴板，并已写入文件：\n' + res.path +
              '\n\n可在微信开发者工具「文件系统」导出该文件，或点下方「分享文件」发送到电脑。'
            try { if (wx.shareFileMessage) wx.shareFileMessage({ filePath: res.path, fail: () => {} }) } catch (e) {}
            wx.showModal({ title: '导出成功', content: tip, showCancel: false })
          }
        })
      })
      .catch((e) => {
        wx.showModal({ title: '导出失败', content: (e && e.errMsg) || '写文件失败', showCancel: false })
      })
  },

  // 导出选中分区（多选）；未选则提示
  doExportSelected() {
    const selIds = this.data.zones.filter((z) => z.selected).map((z) => z.zoneId)
    if (!selIds.length) {
      wx.showToast({ title: '请先勾选至少一个分区', icon: 'none' })
      return
    }
    this._writeAndShare(selIds)
  },

  // 导出全部（忽略分区筛选）
  doExportAll() {
    this._writeAndShare(null)
  },

  copyJson() {
    const g = app.globalData
    const s = fpstore.loadSession() || g.session
    const selIds = this.data.zones.filter((z) => z.selected).map((z) => z.zoneId)
    const json = JSON.stringify(fpstore.buildExport(s, g.globalData.session.device,
      selIds.length ? { zoneIds: selIds } : {}), null, 2)
    wx.setClipboardData({ data: json, success: () => wx.showToast({ title: '已复制' }) })
  }
})
