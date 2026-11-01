App({
  onError(message: string) {
    // 全局错误兜底：仅记录日志便于联调排查，不向用户暴露技术细节
    console.error('[app] 未捕获错误:', message);
  },

  onUnhandledRejection(res) {
    console.error('[app] 未处理的 Promise 拒绝:', res.reason);
  },
});
