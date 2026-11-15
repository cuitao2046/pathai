import { VoiceInteraction } from '../core/asr/voice-interaction';
import type { AsrHandlers } from '../core/asr/asr-engine';

function make() {
  const sessions: Array<{ resolve: (s: string) => void; reject: (e: Error) => void; handlers: AsrHandlers }> = [];
  const engine = {
    start: jest.fn((handlers: AsrHandlers) => new Promise<string>((resolve, reject) => sessions.push({ resolve, reject, handlers }))),
    stop: jest.fn(), cancel: jest.fn(),
  };
  const final = jest.fn(), error = jest.fn(), partial = jest.fn(), ready = jest.fn();
  const voice = new VoiceInteraction({ engine, onFinal: final, onError: error, onPartial: partial, onRecordingReady: ready, onStateChange: jest.fn() });
  return { engine, sessions, final, error, partial, ready, voice };
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };
beforeEach(() => jest.useFakeTimers());
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

test('普通触摸开始/结束，tap 不会再次开录', async () => {
  const { voice, engine, sessions, final } = make();
  voice.touchStart(); voice.touchEnd(); voice.activate();
  expect(engine.start).toHaveBeenCalledTimes(1);
  expect(engine.stop).toHaveBeenCalledTimes(1);
  sessions[0].resolve('图书馆'); await flush();
  expect(final).toHaveBeenCalledWith('图书馆');
});
test('旁白 touch 事件全部忽略，激活两次开始/结束', () => {
  const { voice, engine } = make(); voice.setScreenReaderMode(true);
  voice.touchStart(); voice.touchEnd(); voice.touchCancel();
  expect(engine.start).not.toHaveBeenCalled();
  voice.activate(); voice.touchEnd();
  expect(voice.currentState).toBe('recording');
  voice.activate(); voice.activate();
  expect(engine.start).toHaveBeenCalledTimes(1);
  expect(voice.currentState).toBe('recognizing');
});
test('录音 15 秒自动收尾，识别 30 秒超时恢复可重试', () => {
  const { voice, engine, error } = make(); voice.setScreenReaderMode(true); voice.activate();
  jest.advanceTimersByTime(15000); expect(engine.stop).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(30000); expect(voice.currentState).toBe('idle');
  expect(error).toHaveBeenCalledWith('语音识别超时，请重试');
  voice.activate(); expect(engine.start).toHaveBeenCalledTimes(2);
});
test('取消后迟到文本/partial/ready/失败均隔离，新会话不被旧会话复位', async () => {
  const { voice, sessions, final, partial, ready, error } = make();
  voice.begin(); voice.cancel(); voice.begin();
  sessions[0].handlers.onPartial?.('旧文本'); sessions[0].handlers.onReady?.();
  sessions[0].resolve('旧目的地'); await flush();
  expect(voice.currentState).toBe('recording');
  expect(final).not.toHaveBeenCalled(); expect(partial).not.toHaveBeenCalled(); expect(ready).not.toHaveBeenCalled();
  voice.cancel(); sessions[1].reject(new Error('迟到错误')); await flush(); expect(error).not.toHaveBeenCalled();
});
test('切换模式及后台立即取消，会话计时器释放', async () => {
  const { voice, sessions, final, engine } = make(); voice.begin(); voice.setScreenReaderMode(true);
  sessions[0].resolve('旧目的地'); await flush(); expect(final).not.toHaveBeenCalled();
  voice.activate(); voice.handleAppHide(); jest.advanceTimersByTime(60000);
  expect(engine.stop).not.toHaveBeenCalled(); expect(voice.currentState).toBe('idle');
});
test('stop 同步异常释放会话且允许重试', () => {
  const { voice, engine, error } = make(); engine.stop.mockImplementation(() => { throw new Error('stop failed'); });
  voice.begin(); voice.end(); expect(voice.currentState).toBe('error'); expect(error).toHaveBeenCalled();
  voice.begin(); expect(engine.start).toHaveBeenCalledTimes(2);
});
