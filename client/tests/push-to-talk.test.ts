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
    const empties: number[] = [];
    const ready: number[] = [];
    const ptt = new PushToTalk({
      engine: asr,
      onStateChange: (s) => states.push(s),
      onPartial: (t) => partials.push(t),
      onFinal: (t) => finals.push(t),
      onError: (m) => errors.push(m),
      onEmpty: () => empties.push(Date.now()),
      onRecordingReady: () => ready.push(Date.now()),
    });
    return { asr, states, finals, partials, errors, empties, ready, ptt };
  }

  it('麦克风就绪（onReady）晚于状态切换：震动提示只在真就绪后给一次', async () => {
    const { ptt, ready } = make();
    ptt.begin();
    expect(ready).toEqual([]); // 状态已是 recording，但麦克风还没开
    await tick();
    expect(ready).toHaveLength(1);
  });

  it('就绪前已松手：不再补震动（避免「已结束还提示可以说话」）', async () => {
    const { ptt, ready } = make();
    ptt.begin();
    ptt.end(); // onReady 尚未送达
    await tick();
    expect(ready).toEqual([]);
  });

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

  it('引擎 start 同步抛错 → error 状态并回调 onError（原始错误不外泄）', () => {
    const { ptt, errors } = make(new ThrowingStartEngine());
    ptt.begin();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['录音没有成功，请重试']);
  });

  it('引擎会话 reject → error 状态，不提交', async () => {
    const { ptt, asr, finals, errors } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('配额不足'));
    await tick();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['语音识别失败，请重试']);
    expect(finals).toEqual([]);
  });

  it('麦克风权限被拒 → 播报可操作中文提示，不含原始 errMsg', async () => {
    const { ptt, asr, errors } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('startRecord:fail auth deny'));
    await tick();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['需要麦克风权限，请在设置中开启']);
    expect(errors[0]).not.toContain('errMsg');
    expect(errors[0]).not.toContain('auth deny');
  });

  it('麦克风被占用 → 播报占用提示而非识别失败', async () => {
    const { ptt, asr, errors } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('operateRecorder:fail the recorder is busy'));
    await tick();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['麦克风被占用，请稍后再试']);
  });

  it('开麦超时 → 播报麦克风打开失败，不含原始错误', async () => {
    const { ptt, asr, errors } = make();
    ptt.begin();
    (asr as MockAsrEngine).emitError(new Error('麦克风启动超时'));
    await tick();
    expect(ptt.currentState).toBe('error');
    expect(errors).toEqual(['麦克风打开失败，请重试']);
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

  it('空白识别文本不触发 onFinal，但触发 onEmpty 提示', async () => {
    const { ptt, asr, finals, empties } = make();
    (asr as MockAsrEngine).nextText = '   ';
    ptt.begin();
    ptt.end();
    await tick();
    expect(finals).toEqual([]);
    expect(empties).toHaveLength(1);
    expect(ptt.currentState).toBe('idle');
  });

  it('快速点按无音频（空串）→ 触发 onEmpty，不进入 error', async () => {
    const { ptt, asr, finals, empties } = make();
    (asr as MockAsrEngine).nextText = '';
    ptt.begin();
    ptt.end();
    await tick();
    expect(finals).toEqual([]);
    expect(empties).toHaveLength(1);
    expect(ptt.currentState).toBe('idle');
  });

  it('正常识别不触发 onEmpty', async () => {
    const { ptt, asr, finals, empties } = make();
    (asr as MockAsrEngine).nextText = '去图书馆';
    ptt.begin();
    ptt.end();
    await tick();
    expect(finals).toEqual(['去图书馆']);
    expect(empties).toEqual([]);
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
