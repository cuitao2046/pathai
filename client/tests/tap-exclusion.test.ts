import {
  EXCLUSION_PAD_PX,
  expandRect,
  isPointExcluded,
  toCanvasRect,
  type Rect,
} from '../core/a11y/tap-exclusion';

/** 排除区命中：边界、外扩 2px、空集合与 canvas 坐标换算。 */

describe('isPointExcluded', () => {
  const rect: Rect = { left: 10, top: 20, right: 60, bottom: 70 };

  it('矩形内部命中', () => {
    expect(isPointExcluded(30, 40, [rect], 0)).toBe(true);
  });

  it('矩形外部不命中', () => {
    expect(isPointExcluded(5, 40, [rect], 0)).toBe(false);
    expect(isPointExcluded(30, 80, [rect], 0)).toBe(false);
  });

  it('边界点按命中处理（含左/上边界）', () => {
    expect(isPointExcluded(10, 20, [rect], 0)).toBe(true);
    expect(isPointExcluded(60, 70, [rect], 0)).toBe(true);
  });

  it('默认外扩 2px：紧贴边缘 1px 缝隙也命中', () => {
    expect(EXCLUSION_PAD_PX).toBe(2);
    expect(isPointExcluded(8, 40, [rect])).toBe(true); // left-2
    expect(isPointExcluded(62, 40, [rect])).toBe(true); // right+2
    expect(isPointExcluded(62.5, 40, [rect])).toBe(false); // 超出外扩
  });

  it('空集合不排除任何点', () => {
    expect(isPointExcluded(0, 0, [])).toBe(false);
  });

  it('多个矩形任一命中即可', () => {
    const second: Rect = { left: 200, top: 200, right: 240, bottom: 240 };
    expect(isPointExcluded(220, 220, [rect, second], 0)).toBe(true);
    expect(isPointExcluded(150, 150, [rect, second], 0)).toBe(false);
  });
});

describe('expandRect / toCanvasRect', () => {
  it('外扩后四边各增加 pad', () => {
    expect(expandRect({ left: 0, top: 0, right: 10, bottom: 10 }, 2)).toEqual({
      left: -2,
      top: -2,
      right: 12,
      bottom: 12,
    });
  });

  it('boundingClientRect 换算为 canvas 坐标', () => {
    expect(
      toCanvasRect({ left: 100, top: 200, width: 30, height: 40 }, { left: 20, top: 50 }),
    ).toEqual({ left: 80, top: 150, right: 110, bottom: 190 });
  });
});
