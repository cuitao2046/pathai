import {
  FP_POINT_COLORS,
  LOD_1M_MIN_SPACING_PX,
  filterVisiblePoints,
  isPointVisible,
  pointStyle,
  routeText,
  type GridPointLike,
  type FilterMode,
} from '../core/collection/fingerprint-visibility';

const BASE: GridPointLike = { isBase2m: true, collectable: false, routeTags: [] };
const SPLIT: GridPointLike = { isBase2m: false, collectable: false, routeTags: [] };
const ROUTE_1M: GridPointLike = { isBase2m: false, collectable: false, routeTags: ['R2'] };
const ROUTE_1M_COLLECTABLE: GridPointLike = { isBase2m: false, collectable: true, routeTags: ['R1'] };

const LOW_SCALE = 2; // 整楼适配：1m 普通点被 LOD 隐藏

describe('isPointVisible / LOD 与筛选', () => {
  test('低缩放隐藏普通 1m 点，但路线 1m 点始终绘制', () => {
    expect(isPointVisible(SPLIT, LOW_SCALE, 'all')).toBe(false);
    expect(isPointVisible(ROUTE_1M, LOW_SCALE, 'all')).toBe(true);
    expect(isPointVisible(BASE, LOW_SCALE, 'all')).toBe(true);
  });

  test('放大到 8px/m 后普通 1m 点显示', () => {
    expect(isPointVisible(SPLIT, LOD_1M_MIN_SPACING_PX - 0.01, 'all')).toBe(false);
    expect(isPointVisible(SPLIT, LOD_1M_MIN_SPACING_PX, 'all')).toBe(true);
  });

  test('筛选档位：全部 / 仅可采集 / 仅路线点', () => {
    const pts = [BASE, SPLIT, ROUTE_1M, ROUTE_1M_COLLECTABLE];
    const byMode = (mode: FilterMode) =>
      filterVisiblePoints(pts, LOW_SCALE, mode).map((p) => pts.indexOf(p));

    expect(byMode('all')).toEqual([0, 2, 3]);
    // 仅可采集：路线点也须满足可采集；普通 1m 仍受 LOD 限制
    expect(byMode('collectable')).toEqual([3]);
    // 仅路线点：不受 LOD 限制，仅看 routeTags
    expect(byMode('route')).toEqual([2, 3]);
  });
});

describe('pointStyle（已采集路线点保留标记）', () => {
  test('未采集路线点：橙色实心 + 白色描边', () => {
    const s = pointStyle(false, true, false);
    expect(s.fill).toBe(FP_POINT_COLORS.route);
    expect(s.stroke).toBe(FP_POINT_COLORS.routeStroke);
    expect(s.sizePx).toBe(8);
  });

  test('已采集路线点：绿色实心 + 橙色描边', () => {
    const s = pointStyle(true, true, true);
    expect(s.fill).toBe(FP_POINT_COLORS.collected);
    expect(s.stroke).toBe(FP_POINT_COLORS.route);
    expect(s.strokeWidth).toBe(2);
  });

  test('已采集普通点：纯绿，无额外描边', () => {
    const s = pointStyle(true, false, true);
    expect(s.fill).toBe(FP_POINT_COLORS.collected);
    expect(s.stroke).toBeNull();
  });

  test('可采集 / 不可采集样式可区分', () => {
    const ok = pointStyle(false, false, true);
    const blind = pointStyle(false, false, false);
    expect(ok.fill).toBe(FP_POINT_COLORS.collectable);
    expect(ok.stroke).toBeNull();
    expect(blind.fill).toBeNull();
    expect(blind.stroke).toBe(FP_POINT_COLORS.unavailable);
  });
});

describe('routeText', () => {
  test('路线点提示优先采集，非路线点为占位符', () => {
    expect(routeText(['R1', 'R2'])).toBe('R1 / R2 · 优先采集');
    expect(routeText([])).toBe('—');
  });
});
