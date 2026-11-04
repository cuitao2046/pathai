import {
  HeadingSource,
  type CompassReading,
  type HeadingApi,
  type HeadingMode,
  type MotionReading,
} from '../core/a11y/heading-source';

/** 传感器管线：罗盘优先、失败/超时降级到运动、双失败不可用、生命周期停止。 */

function makeApi(opts: { compassFails?: boolean; motionFails?: boolean } = {}) {
  const state = {
    compassStart: 0,
    compassStop: 0,
    motionStart: 0,
    motionStop: 0,
    compassCb: null as ((r: CompassReading) => void) | null,
    motionCb: null as ((r: MotionReading) => void) | null,
  };
  const api: HeadingApi = {
    startCompass: (o) => {
      state.compassStart += 1;
      if (opts.compassFails) {
        o.fail?.(new Error('compass unsupported'));
      } else {
        o.success?.();
      }
    },
    stopCompass: () => {
      state.compassStop += 1;
    },
    onCompassChange: (cb) => {
      state.compassCb = cb;
    },
    startDeviceMotionListening: (o) => {
      state.motionStart += 1;
      if (opts.motionFails) {
        o.fail?.(new Error('motion unsupported'));
      } else {
        o.success?.();
      }
    },
    stopDeviceMotionListening: () => {
      state.motionStop += 1;
    },
    onDeviceMotionChange: (cb) => {
      state.motionCb = cb;
    },
  };
  return { api, state };
}

function makeSource(api: HeadingApi, timeoutMs = 1500) {
  const headings: Array<{ deg: number; mode: HeadingMode }> = [];
  const modes: HeadingMode[] = [];
  const unavailable = jest.fn();
  const source = new HeadingSource({
    api,
    timeoutMs,
    onHeading: (deg, mode) => headings.push({ deg, mode }),
    onModeChange: (mode) => modes.push(mode),
    onUnavailable: unavailable,
  });
  return { source, headings, modes, unavailable };
}

describe('HeadingSource', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('罗盘有回调时使用绝对方向（compass）', () => {
    const { api, state } = makeApi();
    const { source, headings } = makeSource(api);
    source.start();
    expect(state.compassStart).toBe(1);
    state.compassCb?.({ direction: 90 });
    expect(headings).toEqual([{ deg: 90, mode: 'compass' }]);
    expect(source.currentMode).toBe('compass');
    source.stop();
  });

  it('罗盘方向为负/超 360 时归一化', () => {
    const { api, state } = makeApi();
    const { source, headings } = makeSource(api);
    source.start();
    state.compassCb?.({ direction: -20 });
    expect(headings[0].deg).toBe(340);
    source.stop();
  });

  it('罗盘启动失败 → 立即降级运动相对基准', () => {
    const { api, state } = makeApi({ compassFails: true });
    const { source, headings, modes } = makeSource(api);
    source.start();
    expect(source.currentMode).toBe('motion-relative');
    expect(modes).toContain('motion-relative');
    expect(state.motionStart).toBe(1);
    state.motionCb?.({ alpha: 10 });
    state.motionCb?.({ alpha: 40 });
    expect(headings).toEqual([
      { deg: 0, mode: 'motion-relative' },
      { deg: 30, mode: 'motion-relative' },
    ]);
    source.stop();
  });

  it('罗盘 1500ms 无回调 → 超时降级到运动', () => {
    const { api, state } = makeApi();
    const { source, modes } = makeSource(api, 1500);
    source.start();
    jest.advanceTimersByTime(1500);
    expect(source.currentMode).toBe('motion-relative');
    expect(modes).toContain('motion-relative');
    expect(state.compassStop).toBe(1);
    expect(state.motionStart).toBe(1);
    source.stop();
  });

  it('运动也无回调 → 不可用并只提示一次', () => {
    const { api } = makeApi({ compassFails: true });
    const { source, unavailable } = makeSource(api, 1500);
    source.start();
    jest.advanceTimersByTime(1500);
    expect(source.currentMode).toBe('unavailable');
    expect(source.isRunning).toBe(false);
    expect(unavailable).toHaveBeenCalledTimes(1);
  });

  it('罗盘与运动都启动失败 → 立即不可用', () => {
    const { api } = makeApi({ compassFails: true, motionFails: true });
    const { source, unavailable } = makeSource(api);
    source.start();
    expect(source.currentMode).toBe('unavailable');
    expect(unavailable).toHaveBeenCalledTimes(1);
  });

  it('stop() 停止两类监听，且之后回调不再触发', () => {
    const { api, state } = makeApi();
    const { source, headings } = makeSource(api);
    source.start();
    state.compassCb?.({ direction: 45 });
    source.stop();
    expect(state.compassStop).toBe(1);
    expect(state.motionStop).toBe(1);
    state.compassCb?.({ direction: 200 });
    state.motionCb?.({ alpha: 100 });
    expect(headings).toHaveLength(1);
    expect(source.isRunning).toBe(false);
  });

  it('重复 start() 不重复启动', () => {
    const { api, state } = makeApi();
    const { source } = makeSource(api);
    source.start();
    source.start();
    expect(state.compassStart).toBe(1);
    source.stop();
  });
});
