import {
  HeadingSmoother,
  arrowAngle,
  bearingBetween,
  deviationBand,
  deviationOfArrow,
  deviationStep,
  emaAngle,
  normalizeDeg,
  routeBearing,
  shortestDelta,
} from '../core/a11y/nav-arrow-angle';

/** 方向箭头角度数学：最短弧、0/360 回绕、EMA、静止冻结、偏离分档。 */

describe('normalizeDeg / shortestDelta', () => {
  it('归一化到 [0, 360)', () => {
    expect(normalizeDeg(0)).toBe(0);
    expect(normalizeDeg(360)).toBe(0);
    expect(normalizeDeg(370)).toBe(10);
    expect(normalizeDeg(-10)).toBe(350);
  });

  it('跨越 0°/360° 取最短弧', () => {
    expect(shortestDelta(350, 10)).toBe(20);
    expect(shortestDelta(10, 350)).toBe(-20);
    expect(shortestDelta(0, 90)).toBe(90);
    expect(shortestDelta(90, 0)).toBe(-90);
  });

  it('180° 边界取 +180', () => {
    expect(shortestDelta(0, 180)).toBe(180);
    expect(shortestDelta(180, 0)).toBe(180);
  });
});

describe('arrowAngle / deviationOfArrow', () => {
  it('手机朝向正北且路线朝北 → 箭头朝上（0°）', () => {
    expect(arrowAngle(0, 0)).toBe(0);
  });

  it('路线在东、手机朝北 → 箭头 90°（向右）', () => {
    expect(arrowAngle(90, 0)).toBe(90);
  });

  it('手机朝东（90）而路线朝北（0）→ 箭头 270°（向左）', () => {
    expect(arrowAngle(0, 90)).toBe(270);
  });

  it('偏离量把 350° 视为 10°', () => {
    expect(deviationOfArrow(350)).toBe(10);
    expect(deviationOfArrow(180)).toBe(180);
  });
});

describe('bearingBetween', () => {
  it('屏幕上方=北=0°，右=东=90°，下=南=180°，左=西=270°', () => {
    expect(bearingBetween({ x: 0, y: 0 }, { x: 0, y: -1 })).toBe(0);
    expect(bearingBetween({ x: 0, y: 0 }, { x: 1, y: 0 })).toBe(90);
    expect(bearingBetween({ x: 0, y: 0 }, { x: 0, y: 1 })).toBe(180);
    expect(bearingBetween({ x: 0, y: 0 }, { x: -1, y: 0 })).toBe(270);
  });
});

describe('routeBearing', () => {
  const line = [
    { x: 0, y: 0 },
    { x: 0, y: -100 },
    { x: 100, y: -100 },
  ];

  it('取最近顶点的下一段方向', () => {
    expect(routeBearing(line, { x: 0, y: -20 })).toBe(0);
    expect(routeBearing(line, { x: 0, y: -99 })).toBe(90);
  });

  it('已到末顶点时取最后一段方向（不回退到 0）', () => {
    expect(routeBearing(line, { x: 200, y: -100 })).toBe(90);
  });

  it('少于 2 个点返回 null（无方向）', () => {
    expect(routeBearing([{ x: 0, y: 0 }], { x: 0, y: 0 })).toBeNull();
    expect(routeBearing([], { x: 0, y: 0 })).toBeNull();
  });
});

describe('emaAngle', () => {
  it('α=0.15 按最短弧收敛', () => {
    expect(emaAngle(0, 100)).toBeCloseTo(15, 5);
    expect(emaAngle(100, 0)).toBeCloseTo(85, 5);
  });

  it('跨越 360° 不回绕到 180°', () => {
    // 350 → 10 的最短弧是 +20，α=0.5 → 360 → 0
    expect(emaAngle(350, 10, 0.5)).toBe(0);
  });
});

describe('HeadingSmoother 静止冻结', () => {
  it('首次直接采用原始角度', () => {
    const s = new HeadingSmoother();
    expect(s.update(100, 0)).toBe(100);
  });

  it('静止 1s 内 <3° 抖动 → 冻结显示角度', () => {
    const s = new HeadingSmoother();
    const first = s.update(100, 0);
    s.update(101, 500); // 抖动 1°：仍在平滑
    const frozen = s.update(102, 1000); // 已静止 1s → 冻结
    expect(frozen).toBeCloseTo(100.15, 5); // 仅 500ms 时累积了一次 EMA
    expect(first).toBe(100);
  });

  it('变化 ≥3° 时解除冻结并继续跟随', () => {
    const s = new HeadingSmoother();
    s.update(100, 0);
    s.update(101, 400);
    expect(s.update(105, 1000)).toBeGreaterThan(100.3);
  });

  it('reset 后回到首次采样', () => {
    const s = new HeadingSmoother();
    s.update(50, 0);
    s.reset();
    expect(s.update(200, 100)).toBe(200);
  });
});

describe('偏离分档与播报档位', () => {
  it('14/15 对齐；16/30 接近；31 偏离', () => {
    expect(deviationBand(14)).toBe('aligned');
    expect(deviationBand(15)).toBe('aligned');
    expect(deviationBand(16)).toBe('near');
    expect(deviationBand(30)).toBe('near');
    expect(deviationBand(31)).toBe('off');
  });

  it('播报角度取整到 15° 且最小 15°', () => {
    expect(deviationStep(3)).toBe(15);
    expect(deviationStep(22)).toBe(15);
    expect(deviationStep(23)).toBe(30);
    expect(deviationStep(90)).toBe(90);
  });
});
