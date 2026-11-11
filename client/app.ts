/**
 * 启动入口配置：地图页为首屏（自动发现 → 加载地图 → 导航），
 * 设置页与隔离探测页仍保留可进入，便于现场排查。
 *
 * onLaunch 最先执行发布护栏：正式版携带联调配置（假登录/本地数据
 * 改写/明文 HTTP）直接抛错阻断启动，防止误发布（详见 config/env.ts）。
 */
import { assertReleaseSafety } from './config/env';

App({
  onError(message: string) {
    // 全局错误兜底：仅记录日志便于联调排查，不向用户暴露技术细节
    console.error('[app] 未捕获错误:', message);
  },

  onUnhandledRejection(res) {
    console.error('[app] 未处理的 Promise 拒绝:', res.reason);
  },

  onLaunch() {
    assertReleaseSafety();
  },
});
