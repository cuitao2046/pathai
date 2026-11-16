import { staticClipForText, staticInstructionClip } from '../core/speech/static-clips';
import type { LocalVoiceName } from '../core/speech/local-voice';

declare const require: (name: string) => any;
declare const __dirname: string;
const { existsSync } = require('fs');
const { join } = require('path');

const EXPECTED: Array<[string, LocalVoiceName]> = [
  ['即将左转。', 'ready2left-turn'],
  ['即将右转。', 'ready2right-turn'],
  ['即将左转', 'ready2left-turn'],
  ['左转。', 'left-turn'],
  ['右转。', 'right-turn'],
  ['稍向左转。', 'little-left'],
  ['稍向右转。', 'little-right'],
  ['掉头。', 'u-turn'],
  ['前方路口请左转。', 'ready2left-turn'],
  ['前方路口请右转。', 'ready2right-turn'],
  ['已到达目的地附近，请留意周围环境。', 'nav-arrived-near'],
  ['导航连接中断，导航已暂停，请停下并留意周围环境。', 'nav-offline'],
  ['导航会话已失效，请重新发起导航。', 'nav-session-invalid'],
  ['地图已更新，请重新发起导航。', 'nav-map-updated'],
  ['是否现在开始导航？说“开始导航”开始，说“取消”放弃。', 'preview-confirm'],
  ['已取消导航。如需导航，请重新说出目的地。', 'nav-cancelled'],
  ['已超时未确认，导航已取消。如需导航，请重新说出目的地。', 'preview-timeout'],
  ['方向已对准，请直行。', 'heading-aligned'],
  ['当前设备无法提供方向，请改用语音指令导航。', 'heading-unavailable'],
  ['无法开始导航，请重试。', 'nav-start-failed'],
  ['结束导航失败，请重试。', 'nav-end-failed'],
];

describe('固定导航文案 → 本地离线音频', () => {
  it('固定文案命中本地片段', () => {
    for (const [text, clip] of EXPECTED) {
      expect(staticClipForText(text)).toBe(clip);
      expect(staticInstructionClip(text)).toBe(clip);
    }
  });

  it('动态文案继续走 TTS（不命中本地片段）', () => {
    expect(staticClipForText('请沿当前通道直行 5 米。')).toBeNull();
    expect(staticClipForText('请进入电梯，前往 2 楼。')).toBeNull();
    expect(staticClipForText('已到达 2 楼，请退出电梯。')).toBeNull();
    expect(staticClipForText('已到达目的地。')).toBeNull();
  });

  it('映射到的音频文件都随包存在', () => {
    const clips = new Set<LocalVoiceName>(EXPECTED.map(([, clip]) => clip));
    clips.add('nav-relocalizing');
    for (const clip of clips) {
      const file = join(__dirname, '..', 'assets', 'voice', `${clip}.mp3`);
      expect(existsSync(file)).toBe(true);
    }
  });
});
