/**
 * PathAI 室内导航小程序入口。
 * onLaunch 预热令牌（静默 wx.login → API-01）；切后台恢复在 C7/C8 接入。
 */
import { appContext } from './services/app-context';

App({
  onLaunch() {
    // 预热令牌：提前触发登录，避免首个业务请求串行等待
    void appContext.http.tokens.getAccessToken().catch(() => {
      // 失败由首个业务请求的重登机制兜底
    });
  },

  onError(message: string) {
    // 全局错误兜底：仅记录日志便于联调排查，不向用户暴露技术细节
    console.error('[app] 未捕获错误:', message);
  },

  onUnhandledRejection(res) {
    console.error('[app] 未处理的 Promise 拒绝:', res.reason);
  },
});
