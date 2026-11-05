import {
  NAV_ARROW_H_RPX,
  NAV_ARROW_INNER_SCALE,
  NAV_ARROW_POINTS,
  NAV_ARROW_W_RPX,
  apexAngleDeg,
  arrowClipPath,
} from '../core/a11y/nav-arrow-shape';

/**
 * 箭头形态约束：等边三角形"三个角等大、看不出前后"，本形状必须是
 * 单尖顶 + 宽头 + 窄身，且关于中轴对称；同时锁定描边缩放与 clip-path 输出。
 */

describe('nav-arrow-shape 几何', () => {
  it('尖端位于正上方中轴（rotate(0) = 朝上）', () => {
    expect(NAV_ARROW_POINTS[0]).toEqual([50, 0]);
  });

  it('顶点关于 x=50% 严格对称（避免左右歪斜造成误读）', () => {
    const mins = NAV_ARROW_POINTS.map(([x]) => x);
    expect(Math.min(...mins)).toBe(100 - Math.max(...mins));
    for (const [x, y] of NAV_ARROW_POINTS) {
      expect(NAV_ARROW_POINTS.some(([mx, my]) => mx === 100 - x && my === y)).toBe(true);
    }
  });

  it('尖顶角明显锐于等边三角形（≥35° 且 <55°）', () => {
    const angle = apexAngleDeg();
    expect(angle).toBeGreaterThan(35);
    expect(angle).toBeLessThan(55);
  });

  it('箭头宽度至少是箭身宽度的 2.5 倍（头/尾可辨）', () => {
    const shoulderWidth = NAV_ARROW_POINTS[1][0] - NAV_ARROW_POINTS[6][0];
    const stemWidth = NAV_ARROW_POINTS[2][0] - NAV_ARROW_POINTS[5][0];
    expect(stemWidth).toBeGreaterThan(0);
    expect(shoulderWidth / stemWidth).toBeGreaterThanOrEqual(2.5);
  });

  it('箭身延伸到包围盒尾部，旋转时不会被误认成第二个尖顶', () => {
    expect(NAV_ARROW_POINTS[3][1]).toBeGreaterThanOrEqual(90);
    expect(NAV_ARROW_POINTS[3][1]).toBe(NAV_ARROW_POINTS[4][1]);
  });

  it('clip-path 输出闭合多边形且包含全部顶点', () => {
    const path = arrowClipPath();
    expect(path.startsWith('polygon(')).toBe(true);
    expect(path.endsWith(')')).toBe(true);
    for (const [x, y] of NAV_ARROW_POINTS) {
      expect(path).toContain(`${x}% ${y}%`);
    }
    expect(path.split(',').length).toBe(NAV_ARROW_POINTS.length);
  });

  it('内层缩放留出描边，且不吞掉尖顶（0.85–0.95）', () => {
    expect(NAV_ARROW_INNER_SCALE).toBeGreaterThanOrEqual(0.85);
    expect(NAV_ARROW_INNER_SCALE).toBeLessThanOrEqual(0.95);
  });

  it('包围盒适配 520rpx 圆盘（不撑破覆盖层）', () => {
    expect(NAV_ARROW_W_RPX).toBeLessThanOrEqual(520);
    expect(NAV_ARROW_H_RPX).toBeLessThanOrEqual(520);
    expect(NAV_ARROW_H_RPX).toBeGreaterThan(NAV_ARROW_W_RPX);
  });
});
