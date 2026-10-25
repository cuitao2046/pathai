/**
 * NavStateMachine 状态迁移表全覆盖（C7）。
 */
import { NavStateMachine } from '../core/nav/nav-state-machine';

describe('NavStateMachine 状态迁移表', () => {
  it('idle + SESSION_CREATED(active) → active，无副作用', () => {
    const m = new NavStateMachine();
    expect(m.handle({ type: 'SESSION_CREATED', status: 'active' })).toEqual({});
    expect(m.state).toBe('active');
  });

  it('idle 时 UPDATE_RESULT/BACKGROUND/END_ACK 均被忽略', () => {
    const m = new NavStateMachine();
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' })).toEqual({});
    expect(m.state).toBe('idle');
    expect(m.handle({ type: 'BACKGROUND' })).toEqual({});
    expect(m.handle({ type: 'END_ACK' })).toEqual({});
    expect(m.state).toBe('idle');
  });

  it('active + recovering → recovering：clearNormal + stopPipeline', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' })).toEqual({
      clearNormal: true,
      stopPipeline: true,
    });
    expect(m.state).toBe('recovering');
  });

  it('recovering + active → active：startPipeline（重新定位成功恢复）', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    m.handle({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' });
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'active', event: 'reroute' })).toEqual({
      startPipeline: true,
    });
    expect(m.state).toBe('active');
  });

  it('active + active（instruction）→ active，无副作用', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'active', event: 'instruction' })).toEqual({});
    expect(m.state).toBe('active');
  });

  it('active + arrived → arrived：stopPipeline；终态拒再迁移', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'arrived', event: 'arrived' })).toEqual({
      stopPipeline: true,
    });
    expect(m.state).toBe('arrived');
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'active', event: 'instruction' })).toEqual({});
    expect(m.state).toBe('arrived');
    expect(m.handle({ type: 'BACKGROUND' })).toEqual({});
    expect(m.handle({ type: 'RESUMED' })).toEqual({});
    expect(m.state).toBe('arrived');
  });

  it('active + ended（服务端判定）→ ended：stopPipeline', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'ended', event: 'instruction' })).toEqual({
      stopPipeline: true,
    });
    expect(m.state).toBe('ended');
  });

  it('active + BACKGROUND → 停采集；RESUMED(active) → 重启采集', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'BACKGROUND' })).toEqual({ stopPipeline: true });
    expect(m.state).toBe('active');
    expect(m.handle({ type: 'RESUMED' })).toEqual({ startPipeline: true });
    expect(m.state).toBe('active');
  });

  it('recovering + BACKGROUND + RESUMED → 不自动重启采集（等服务端更新）', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    m.handle({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' });
    m.handle({ type: 'BACKGROUND' });
    expect(m.handle({ type: 'RESUMED' })).toEqual({});
    expect(m.state).toBe('recovering');
  });

  it('未切后台时 RESUMED 为幂等空操作', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'RESUMED' })).toEqual({});
    expect(m.state).toBe('active');
  });

  it('active + RESUME_FAILED（会话失效/版本不一致）→ ended', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'RESUME_FAILED' })).toEqual({ stopPipeline: true });
    expect(m.state).toBe('ended');
  });

  it('active + END_ACK → ended：stopPipeline', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'END_ACK' })).toEqual({ stopPipeline: true });
    expect(m.state).toBe('ended');
    expect(m.handle({ type: 'UPDATE_RESULT', status: 'active', event: 'instruction' })).toEqual({});
    expect(m.state).toBe('ended');
  });

  it('QUERY_STATUS 终态收敛（前台校验发现服务端已到达/结束）', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'QUERY_STATUS', status: 'arrived' })).toEqual({ stopPipeline: true });
    expect(m.state).toBe('arrived');
  });

  it('QUERY_STATUS 非 active/recovering 状态同步（recovering 保持等待）', () => {
    const m = new NavStateMachine();
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.handle({ type: 'QUERY_STATUS', status: 'recovering' })).toEqual({});
    expect(m.state).toBe('recovering');
  });

  it('isTerminal：idle/active/recovering 为假，arrived/ended 为真', () => {
    const m = new NavStateMachine();
    expect(m.isTerminal).toBe(false);
    m.handle({ type: 'SESSION_CREATED', status: 'active' });
    expect(m.isTerminal).toBe(false);
    m.handle({ type: 'UPDATE_RESULT', status: 'recovering', event: 'relocalizing' });
    expect(m.isTerminal).toBe(false);
    m.handle({ type: 'UPDATE_RESULT', status: 'active', event: 'reroute' });
    m.handle({ type: 'END_ACK' });
    expect(m.isTerminal).toBe(true);
  });
});
