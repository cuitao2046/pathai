/**
 * InstructionTracker 测试（C7）：去重、过期判定、重播。
 */
import { InstructionTracker } from '../core/nav/instruction-tracker';
import { resetNowSource, setNowSource } from '../utils/clock';
import type { NavigationInstruction } from '../typings/api';

function instr(overrides: Partial<NavigationInstruction>): NavigationInstruction {
  return {
    instructionId: 'i1',
    instructionSequence: 1,
    action: 'continue',
    text: '沿走廊直行。',
    priority: 'normal',
    trigger: { type: 'immediate' },
    expiresAt: null,
    ...overrides,
  };
}

describe('InstructionTracker 指令跟踪', () => {
  beforeEach(() => {
    setNowSource(() => 1_000_000);
  });

  afterEach(() => {
    resetNowSource();
  });

  it('新指令返回 true，同 instructionId 重复返回 false', () => {
    const tracker = new InstructionTracker();
    expect(tracker.observe(instr({}))).toBe(true);
    expect(tracker.observe(instr({}))).toBe(false);
    expect(tracker.observe(instr({ instructionId: 'i2', instructionSequence: 2 }))).toBe(true);
  });

  it('observe(null) 返回 false 且不清空当前指令', () => {
    const tracker = new InstructionTracker();
    tracker.observe(instr({}));
    expect(tracker.observe(null)).toBe(false);
    expect(tracker.currentInstruction?.instructionId).toBe('i1');
  });

  it('expiresAt 缺省/null 视为不过期', () => {
    const tracker = new InstructionTracker();
    tracker.observe(instr({ expiresAt: undefined }));
    expect(tracker.isCurrentValid(999_999_999)).toBe(true);
    tracker.observe(instr({ instructionId: 'i2', expiresAt: null }));
    expect(tracker.isCurrentValid(999_999_999)).toBe(true);
  });

  it('expiresAt 未过期有效、已过期无效', () => {
    const tracker = new InstructionTracker();
    tracker.observe(instr({ expiresAt: '1970-01-01T00:16:40.000Z' })); // 1_000_000ms
    expect(tracker.isCurrentValid(999_999)).toBe(true);
    expect(tracker.isCurrentValid(1_000_000)).toBe(false);
  });

  it('replayText 只重播当前有效指令；过期返回 null', () => {
    const tracker = new InstructionTracker();
    expect(tracker.replayText()).toBeNull();
    tracker.observe(instr({ text: '沿走廊直行。' }));
    expect(tracker.replayText()).toBe('沿走廊直行。');
    tracker.observe(instr({ instructionId: 'i2', text: '前方左转。', expiresAt: '1970-01-01T00:16:40.000Z' }));
    expect(tracker.replayText(999_999)).toBe('前方左转。');
    expect(tracker.replayText(1_000_001)).toBeNull();
  });

  it('reset 清空去重表与当前指令', () => {
    const tracker = new InstructionTracker();
    tracker.observe(instr({}));
    tracker.reset();
    expect(tracker.currentInstruction).toBeNull();
    expect(tracker.observe(instr({}))).toBe(true); // 去重表已清
  });
});
