/**
 * 固定导航文案 → 本地离线音频映射。
 *
 * 只有文案完全固定时才走本地音频；带距离/楼层/地点的动态文案继续走 TTS。
 * 服务端转向文案已固定为“即将左转。/即将右转。”；其余“左转/右转/稍向左转/
 * 稍向右转/掉头”为后续动作预留，音频文件已在 `client/assets/voice/`。
 */
import type { LocalVoiceName } from './local-voice';

const STATIC_INSTRUCTION_CLIPS: Record<string, LocalVoiceName> = {
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
};

export function staticInstructionClip(text: string): LocalVoiceName | null {
  const key = text.trim().replace(/[。！!？?]+$/u, '');
  return STATIC_INSTRUCTION_CLIPS[key] ?? null;
}
