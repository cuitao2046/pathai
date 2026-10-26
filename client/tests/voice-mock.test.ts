/**
 * resolveVoiceMock：语音交互（ASR+TTS）独立 mock 开关解析——
 * 显式存储优先，未设置过则跟随数据源。
 */
import { resolveVoiceMock } from '../services/voice-mock';

describe('resolveVoiceMock', () => {
  test('显式设置优先于数据源（mock 数据源下可关掉语音 mock 走讯飞）', () => {
    expect(resolveVoiceMock(true, 'mock')).toBe(true);
    expect(resolveVoiceMock(true, 'live')).toBe(true);
    expect(resolveVoiceMock(false, 'mock')).toBe(false); // 用户要求的组合：mock定位 + 真实讯飞语音
    expect(resolveVoiceMock(false, 'live')).toBe(false);
  });

  test('未设置过则跟随数据源（mock→开，live→关）', () => {
    expect(resolveVoiceMock(null, 'mock')).toBe(true);
    expect(resolveVoiceMock(null, 'live')).toBe(false);
  });
});
