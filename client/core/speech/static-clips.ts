/**
 * 固定导航文案 → 本地离线音频映射。
 *
 * 只有文案完全固定时才走本地音频；带距离/楼层/地点的动态文案继续走 TTS。
 * 匹配时忽略空白、标点与引号，避免服务端/客户端标点差异导致漏命中。
 */
import type { LocalVoiceName } from './local-voice';

const STATIC_CLIPS: Record<string, LocalVoiceName> = {
  // 转向
  即将左转: 'ready2left-turn',
  即将右转: 'ready2right-turn',
  左转: 'left-turn',
  右转: 'right-turn',
  稍向左转: 'little-left',
  稍向右转: 'little-right',
  掉头: 'u-turn',
  // 兼容已创建会话可能仍在播的旧文案。
  前方路口请左转: 'ready2left-turn',
  前方路口请右转: 'ready2right-turn',
  // 到达
  已到达目的地附近请留意周围环境: 'nav-arrived-near',
  // 高置信到达复用同一段离线音频：到达播报必须在弱网/在线 TTS 不可用时仍可靠播出
  已到达目的地: 'nav-arrived-near',
  // 导航异常/状态提示
  导航连接中断导航已暂停请停下并留意周围环境: 'nav-offline',
  导航会话已失效请重新发起导航: 'nav-session-invalid',
  地图已更新请重新发起导航: 'nav-map-updated',
  // 预览/取消
  是否现在开始导航说开始导航开始说取消放弃: 'preview-confirm',
  已取消导航如需导航请重新说出目的地: 'nav-cancelled',
  已超时未确认导航已取消如需导航请重新说出目的地: 'preview-timeout',
  // 方向提示
  方向已对准请直行: 'heading-aligned',
  当前设备无法提供方向请改用语音指令导航: 'heading-unavailable',
  // 动作失败
  无法开始导航请重试: 'nav-start-failed',
  结束导航失败请重试: 'nav-end-failed',
};

function normalize(text: string): string {
  return text.replace(/[\s，。！？、；：“”‘’"'（）()【】\[\]…—\-]/gu, '');
}

/** 固定文案 → 本地音频；动态文案返回 null，继续走 TTS。 */
export function staticClipForText(text: string): LocalVoiceName | null {
  return STATIC_CLIPS[normalize(text)] ?? null;
}

/** 导航指令固定文案（转向/到达）→ 本地音频。 */
export function staticInstructionClip(text: string): LocalVoiceName | null {
  return staticClipForText(text);
}
