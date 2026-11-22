/**
 * 反向掉头检测单测（T9）：
 * - 起点段反向走 5m、连续两拍才触发；
 * - 正常前进 / 横向移动 / 位移不足不触发；
 * - 途中段回退 5m 触发；
 * - reset 后重新计时。
 */
import { UturnDetector } from '../core/nav/uturn-detection';
import type { GeoPosition } from '../typings/api';

const ROUTE: GeoPosition[] = [
  { x: 0, y: 0, floorId: '1' },
  { x: 100, y: 0, floorId: '1' },
];

function walk(detector: UturnDetector, points: Array<[number, number]>) {
  const results = points.map(([x, y]) => detector.update({ x, y, floorId: '1' }, ROUTE));
  return results.find((result) => result !== null) ?? null;
}

describe('UturnDetector', () => {
  it('起点段反向走满 5m 并连续两拍后触发', () => {
    const detector = new UturnDetector();
    expect(walk(detector, [[0, 0], [-1, 0], [-2, 0], [-3.5, 0]])).toBeNull();
    expect(walk(detector, [[-5.5, 0]])).toBeNull();
    const trigger = walk(detector, [[-6.5, 0]]);
    expect(trigger).not.toBeNull();
    expect(trigger?.mode).toBe('start');
    expect(trigger?.reverseDistanceM).toBeGreaterThanOrEqual(5);
  });

  it('正常前进不触发', () => {
    const detector = new UturnDetector();
    expect(walk(detector, [[0, 0], [5, 0], [10, 0], [15, 0], [20, 0]])).toBeNull();
  });

  it('横向移动（夹角 <120°）不触发', () => {
    const detector = new UturnDetector();
    expect(walk(detector, [[0, 0], [0, -3], [0, -6], [0, -9], [0, -12]])).toBeNull();
  });

  it('位移不足 1.5m 时无法判定方向', () => {
    const detector = new UturnDetector();
    expect(walk(detector, [[0, 0], [-0.5, 0], [-1, 0], [-1.4, 0], [-1.4, 0]])).toBeNull();
  });

  it('途中段回退 5m 触发', () => {
    const detector = new UturnDetector();
    expect(walk(detector, [[0, 0], [5, 0], [10, 0], [15, 0], [20, 0]])).toBeNull();
    expect(walk(detector, [[14, 0]])).toBeNull();
    const trigger = walk(detector, [[13, 0]]);
    expect(trigger?.mode).toBe('mid_route');
    expect(trigger?.progressM).toBe(13);
  });

  it('reset 后重新累计连续次数', () => {
    const detector = new UturnDetector();
    walk(detector, [[0, 0], [-2, 0], [-5, 0]]);
    detector.reset();
    expect(walk(detector, [[-6, 0]])).toBeNull();
    expect(walk(detector, [[-8, 0]])).toBeNull();
    expect(walk(detector, [[-9, 0]])).not.toBeNull();
  });

  it('跨层位置不参与判定', () => {
    const detector = new UturnDetector();
    expect(detector.update({ x: -6, y: 0, floorId: '2' }, ROUTE)).toBeNull();
  });
});
