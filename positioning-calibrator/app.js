// positioning-calibrator — 现场定位参数标定采集小程序
// 只做采集与导出：采样口径与生产客户端一致，不做统计与参数拟合（由 PC 脚本负责）。
App({
  globalData: {
    appVersion: '1.0.0',
    device: null
  },

  onLaunch() {
    try {
      const info = wx.getSystemInfoSync()
      this.globalData.device = {
        model: info.model,
        system: info.system,
        platform: info.platform
      }
    } catch (e) {
      this.globalData.device = null
    }
  }
})
