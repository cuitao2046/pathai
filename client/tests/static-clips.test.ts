import { staticInstructionClip } from '../core/speech/static-clips';
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
];

describe('固定导航文案 → 本地离线音频', () => {
  it('固定转向文案命中本地片段', () => {
    for (const [text, clip] of EXPECTED) {
      expect(staticInstructionClip(text)).toBe(clip);
    }
  });

  it('动态文案继续走 TTS（不命中本地片段）', () => {
    expect(staticInstructionClip('请沿当前通道直行 5 米。')).toBeNull();
    expect(staticInstructionClip('请进入电梯，前往 2 楼。')).toBeNull();
    expect(staticInstructionClip('已到达 2 楼，请退出电梯。')).toBeNull();
  });

  it('映射到的音频文件都随包存在', () => {
    for (const [, clip] of EXPECTED) {
      const file = join(__dirname, '..', 'assets', 'voice', `${clip}.mp3`);
      expect(existsSync(file)).toBe(true);
    }
  });
});
