/**
 * PathAI 室内导航小程序入口。
 * 生命周期编排（令牌预热、切后台恢复）在 C2/C8 里程碑接入。
 */
App({
  onLaunch() {
    // C2：预热令牌管理器（静默 wx.login → API-01）
  },

  onError(message: string) {
    // 全局错误兜底：仅记录日志便于联调排查，不向用户暴露技术细节
    console.error('[app] 未捕获错误:', message);
  },

  onUnhandledRejection(res) {
    console.error('[app] 未处理的 Promise 拒绝:', res.reason);
  },
});
