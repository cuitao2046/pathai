import { HoldToTalkGesture, HOLD_TO_TALK_DEFAULTS } from '../core/a11y/hold-to-talk-gesture';

/** 全屏按住说话状态机：阈值边界与分支（specs/nav-accessibility-ux §4.1–4.3）。 */

function start(g: HoldToTalkGesture, x = 100, y = 100, excluded = false, now = 0) {
  return g.onStart({ x, y, pointCount: 1, excluded, now });
}

describe('HoldToTalkGesture 基本状态', () => {
  it('默认参数为 500ms / 10px / 15s', () => {
    expect(HOLD_TO_TALK_DEFAULTS).toEqual({ holdMs: 500, movePx: 10, maxHoldMs: 15000 });
  });

  it('单指按下进入 pending，抬起不触发语音', () => {
    const g = new HoldToTalkGesture();
    expect(start(g)).toBe('pending');
    expect(g.currentPhase).toBe('pending');
    expect(g.onEnd(0)).toBe('none');
    expect(g.currentPhase).toBe('idle');
  });

  it('双指按下立即进入 zoom（优先级最高）', () => {
    const g = new HoldToTalkGesture();
    expect(g.onStart({ x: 100, y: 100, pointCount: 2, excluded: false, now: 0 })).toBe('zoom');
    expect(g.currentPhase).toBe('zoom');
  });

  it('排除区按下不进入 pending，而是立即 pan', () => {
    const g = new HoldToTalkGesture();
    expect(start(g, 100, 100, true)).toBe('pan-start');
    expect(g.currentPhase).toBe('pan');
    expect(g.onTick(600)).toBe('none'); // 排除区不允许转语音
  });
});

describe('HoldToTalkGesture 500ms 长按边界', () => {
  it('499ms 未达阈值', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 1000);
    expect(g.onTick(1499)).toBe('none');
  });

  it('恰好 500ms 进入语音', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 1000);
    expect(g.onTick(1500)).toBe('voice-start');
    expect(g.currentPhase).toBe('voice');
  });

  it('501ms 进入语音', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 1000);
    expect(g.onTick(1501)).toBe('voice-start');
  });

  it('语音态抬手 = voice-end（等价 onPttEnd）', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 0);
    g.onTick(500);
    expect(g.onEnd(0)).toBe('voice-end');
    expect(g.currentPhase).toBe('idle');
  });

  it('语音态 touchcancel = voice-cancel（不提交）', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 0);
    g.onTick(600);
    expect(g.onCancel()).toBe('voice-cancel');
    expect(g.currentPhase).toBe('idle');
  });

  it('15s 上限自动结束（>= 15000ms）', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 0);
    g.onTick(500); // 进入语音，计时基准 500
    expect(g.onTick(15499)).toBe('none');
    expect(g.onTick(15500)).toBe('voice-timeout');
  });
});

describe('HoldToTalkGesture 10px 位移死区', () => {
  it('9px 仍在 pending（不拖拽）', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100);
    expect(g.onMove(103, 104, 1)).toBe('none'); // 距离 5px
    expect(g.currentPhase).toBe('pending');
  });

  it('恰好 10px 仍为 pending（阈值严格大于 10）', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100);
    expect(g.onMove(110, 100, 1)).toBe('none');
    expect(g.currentPhase).toBe('pending');
  });

  it('11px 转 pan-start 并取消语音候选', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 0);
    expect(g.onMove(111, 100, 1)).toBe('pan-start');
    expect(g.currentPhase).toBe('pan');
    expect(g.onTick(600)).toBe('none'); // 已转拖拽，不再进入语音
  });

  it('pending 阶段第二根手指落下 → zoom', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100);
    expect(g.onMove(120, 100, 2)).toBe('zoom');
    expect(g.currentPhase).toBe('zoom');
  });

  it('语音态内移动不取消语音', () => {
    const g = new HoldToTalkGesture();
    start(g, 100, 100, false, 0);
    g.onTick(500);
    expect(g.onMove(300, 300, 1)).toBe('none');
    expect(g.currentPhase).toBe('voice');
  });
});
