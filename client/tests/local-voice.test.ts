/**
 * 本地提示音播放器（REQ-MAP）：串行播放、失败静默跳过、按住说话可打断。
 * 音频句柄用假件注入，不碰 wx / 网络。
 */
import { LocalVoice, type LocalVoiceClipHandle } from '../core/speech/local-voice';

interface FakeClip {
  src: string;
  played: number;
  stopped: number;
  destroyed: number;
  onEnded: (() => void) | null;
  onError: (() => void) | null;
}

function createHarness() {
  const clips: FakeClip[] = [];
  const voice = new LocalVoice({
    createAudio: (src): LocalVoiceClipHandle => {
      const clip: FakeClip = {
        src,
        played: 0,
        stopped: 0,
        destroyed: 0,
        onEnded: null,
        onError: null,
      };
      clips.push(clip);
      return {
        play: () => {
          clip.played += 1;
        },
        stop: () => {
          clip.stopped += 1;
        },
        destroy: () => {
          clip.destroyed += 1;
        },
        onEnded: (listener) => {
          clip.onEnded = listener;
        },
        onError: (listener) => {
          clip.onError = listener;
        },
      };
    },
    srcOf: (name) => `/assets/voice/${name}.mp3`,
  });
  return { voice, clips };
}

test('按调用顺序串行播放，上一段结束后才开始下一段', async () => {
  const { voice, clips } = createHarness();
  const first = voice.play('map-loading');
  const second = voice.play('app-started');
  await Promise.resolve();

  expect(clips.length).toBe(1);
  expect(clips[0].src).toBe('/assets/voice/map-loading.mp3');
  expect(clips[0].played).toBe(1);

  clips[0].onEnded?.();
  await first;
  await Promise.resolve();

  expect(clips.length).toBe(2);
  expect(clips[1].src).toBe('/assets/voice/app-started.mp3');
  clips[1].onEnded?.();
  await second;
});

test('音频缺失/解码失败：静默跳过并继续下一段', async () => {
  const { voice, clips } = createHarness();
  const first = voice.play('map-load-failed');
  const second = voice.play('app-started');
  await Promise.resolve();

  clips[0].onError?.();
  await first;
  await Promise.resolve();
  expect(clips.length).toBe(2);
  expect(clips[1].src).toBe('/assets/voice/app-started.mp3');
  clips[1].onEnded?.();
  await second;
});

test('stop 立即打断当前提示音并清空待播队列', async () => {
  const { voice, clips } = createHarness();
  const first = voice.play('map-stale');
  const second = voice.play('app-started');
  await Promise.resolve();
  expect(clips.length).toBe(1);

  voice.stop();
  await first;
  await second;

  expect(clips[0].stopped).toBe(1);
  expect(clips[0].destroyed).toBe(1);
  expect(clips.length).toBe(1); // 队列被清空，第二段不再播放
});
