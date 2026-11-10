import {
  deriveControls,
  stateAfterRun,
  type CalcState,
} from '../core/calibration/console-state';

const ctx = {
  hasFile: true,
  hasSample: true,
  deployable: true,
  versionsCount: 2,
  hasRollbackTarget: true,
};

function controls(state: CalcState, overrides: Partial<typeof ctx> = {}) {
  return deriveControls(state, { ...ctx, ...overrides });
}

describe('deriveControls', () => {
  it('empty：只能选文件与回滚', () => {
    const c = controls('empty');
    expect(c.canChoose).toBe(true);
    expect(c.canUpload).toBe(false);
    expect(c.canCompute).toBe(false);
    expect(c.canApply).toBe(false);
    expect(c.canRollback).toBe(true);
  });

  it('local_ready：可上传，计算需先有服务端样本', () => {
    expect(controls('local_ready').canUpload).toBe(true);
    expect(controls('local_ready', { hasSample: false }).canCompute).toBe(false);
  });

  it('server_ready：可计算但不可 apply', () => {
    const c = controls('server_ready');
    expect(c.canCompute).toBe(true);
    expect(c.canApply).toBe(false);
    expect(c.canUpload).toBe(false);
  });

  it('computing / uploading / applying：隐藏写操作', () => {
    for (const state of ['computing', 'uploading', 'applying'] as CalcState[]) {
      const c = controls(state);
      expect(c.canChoose).toBe(false);
      expect(c.canUpload).toBe(false);
      expect(c.canCompute).toBe(false);
      expect(c.canApply).toBe(false);
    }
  });

  it('computed：deployable 才可 apply', () => {
    expect(controls('computed').canApply).toBe(true);
    expect(controls('computed', { deployable: false }).canApply).toBe(false);
  });

  it('blocked：阶段一样本 apply 永久禁用', () => {
    expect(controls('blocked', { deployable: false }).canApply).toBe(false);
  });

  it('compute_failed：可重试上传与计算', () => {
    const c = controls('compute_failed');
    expect(c.canUpload).toBe(true);
    expect(c.canCompute).toBe(true);
  });

  it('stale：只能重新计算，不能 apply', () => {
    const c = controls('stale');
    expect(c.canCompute).toBe(true);
    expect(c.canApply).toBe(false);
  });

  it('无历史或回滚目标时禁用回滚', () => {
    expect(controls('applied', { versionsCount: 0 }).canRollback).toBe(false);
    expect(controls('applied', { hasRollbackTarget: false }).canRollback).toBe(false);
  });
});

describe('stateAfterRun', () => {
  it('queued/computing → computing', () => {
    expect(stateAfterRun('queued', true)).toBe('computing');
    expect(stateAfterRun('computing', true)).toBe('computing');
  });

  it('computed 按 deployable 分流', () => {
    expect(stateAfterRun('computed', true)).toBe('computed');
    expect(stateAfterRun('computed', false)).toBe('blocked');
  });

  it('失败、过期、已生效映射', () => {
    expect(stateAfterRun('compute_failed', true)).toBe('compute_failed');
    expect(stateAfterRun('superseded', true)).toBe('stale');
    expect(stateAfterRun('applied', true)).toBe('applied');
  });
});
