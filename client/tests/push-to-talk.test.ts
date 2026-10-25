/**
 * PushToTalk 按住说话状态机测试（C5，docs/24 FE-02）。
 * 用 MockAsrEngine 驱动完整生命周期；stop() 异步送达最终文本（与插件 onStop 一致）。
 */
import { MockAsrEngine } from '../core/asr/mock-asr';
import { PushToTalk, type PttState } from '../core/asr/push-to-talk';
import type { AsrEngine } from '../core/asr/asr-engine';

/** 刷新微任务与 setTimeout(0)（mock stop 的异步 settle）。 */
const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

/** start 即抛错的引擎（模拟插件缺失）。 */
class ThrowingStartEngine implements AsrEngine {
  start(): Promise<string> {
    throw new Error('插件未加载');
  }
  stop(): void {}
  cancel(): void {}
}

describe('PushToTalk 按住说话状态机', () => {
  function make(engine?: AsrEngine) {
    const asr = engine ?? new MockAsrEngine();
    const states: PttState[] = [];
    const finals: string[] = [];
    const partials: string[] = [];
    const errors: string[] = [];
    const ptt = new PushToTalk({
      engine: asr,
      onStateChange: (s) => states.push(s),
      onPartial: (t) => partials.push(t),
      onFinal: (t) => finals.push(t),
      onError: (m) => errors.push(m),
    });
    return { asr, states, finals, partials, errors, ptt };
  }

  it('完整生命周期：begin → recording，end → recognizing，最终文本回 idle 并提交', async () => {
    const { ptt, asr, states, finals, partials } = make();
    (asr as MockAsrEngine).nextText = '带我去图书馆';
    ptt.begin();
    expect(ptt.currentState).toBe('recording');
    (asr as MockAsrEngine).emitPartial('带我去');
    expect(partials).toEqual(['带我去']);
    ptt.end();
    expect(ptt.currentState).toBe('recognizing');
    await tick();
    expect(ptt.currentState).toBe('idle');
    expect(finals).toEqual(['带我去图书馆']);
    expect(states).toEqual(['recording', 'recognizing', 'idle']);
  });

  it('touchcancel 取消：不提交最终文本，回 idle', async () => {
    const { ptt, asr, finals } = make();
    ptt.begin();
    ptt.cancel();
    await tick();
    expect((asr as MockAsrEngine).cancelCount).toBe(1);
    expect(finals).toEqual([]);
    expect(ptt.currentState).toBe('idle');
  });

  it('recognizing 阶段取消同样不提交', async () => {
    const { ptt, finals } = make();
    ptt.begin();
    ptt.end();
    ptt.cancel(); // stop 的异步 settle 之前取消
    await tick();
    expect(finals).toEqual([]);
    expect(ptt.currentState).toBe('idle');
  });

  it('recording 中重复 begin 被忽略', () => {
    const { ptt, asr } = make();
    ptt.begin();
    ptt.begin();
    expect((asr as MockAsrEngine).startCount).toBe(1);
  });

  it('idle 时 end/cancel 为空操作', () => {
    const { ptt, asr } = make();
    ptt.end();
    ptt.cancel();
    expect((asr as MockAsrEngine).stopCount).toBe(0);
    expect((asr as MockAsrEngine).cancelCount).toBe(0);
  });

  it('引擎 start 同步抛错 → error 状态并回调 onError', () => {
    const { ptt, errors } = make(new ThrowingStartEngine());
    ptt.begin();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['插件未加载']);
  });

  it('引擎会话 reject → error 状态，不提交', async () => {
    const { ptt, asr, finals, errors } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('配额不足'));
    await tick();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['配额不足']);
    expect(finals).toEqual([]);
  });

  it('error 状态可重新 begin 并正常完成', async () => {
    const { ptt, asr, finals } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('x'));
    await tick();
    expect(ptt.currentState).toBe('error');
    (asr as MockAsrEngine).nextText = '去教室';
    ptt.begin();
    expect(ptt.currentState).toBe('recording');
    ptt.end();
    await tick();
    expect(finals).toEqual(['去教室']);
    expect(ptt.currentState).toBe('idle');
  });

  it('空白识别文本不触发 onFinal', async () => {
    const { ptt, asr, finals } = make();
    (asr as MockAsrEngine).nextText = '   ';
    ptt.begin();
    ptt.end();
    await tick();
    expect(finals).toEqual([]);
    expect(ptt.currentState).toBe('idle');
  });

  it('handleAppHide 取消当前会话', async () => {
    const { ptt, asr, finals } = make();
    ptt.begin();
    ptt.handleAppHide();
    await tick();
    expect((asr as MockAsrEngine).cancelCount).toBe(1);
    expect(finals).toEqual([]);
    expect(ptt.currentState).toBe('idle');
  });
});
