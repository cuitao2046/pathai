import {
  FP_POINT_COLORS,
  LOD_1M_MIN_SPACING_PX,
  filterVisiblePoints,
  floorCounts,
  isPointVisible,
  pointStyle,
  routeText,
  type GridPointLike,
  type FilterMode,
} from '../core/collection/fingerprint-visibility';

const BASE: GridPointLike = { isBase2m: true, collectable: false, routeTags: [] };
const SPLIT: GridPointLike = { isBase2m: false, collectable: false, routeTags: [] };
const SPLIT_COLLECTABLE: GridPointLike = { isBase2m: false, collectable: true, routeTags: [] };
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
    const pts = [BASE, SPLIT, SPLIT_COLLECTABLE, ROUTE_1M, ROUTE_1M_COLLECTABLE];
    const byMode = (mode: FilterMode) =>
      filterVisiblePoints(pts, LOW_SCALE, mode).map((p) => pts.indexOf(p));

    // 全部：低缩放下普通 1m 点（含可采集的）被 LOD 隐藏
    expect(byMode('all')).toEqual([0, 3, 4]);
    // 仅可采集：目标点集整体豁免 LOD，否则现场看不到也点不中
    expect(byMode('collectable')).toEqual([2, 4]);
    // 仅路线点：不受 LOD 限制，仅看 routeTags
    expect(byMode('route')).toEqual([3, 4]);
  });

  test('仅可采集档在整楼缩放下也展示全部目标点（F2 71 个全部可见）', () => {
    let visible = 0;
    for (let i = 0; i < 71; i += 1) {
      const p: GridPointLike = { isBase2m: i % 3 === 0, collectable: true, routeTags: [] };
      if (isPointVisible(p, LOW_SCALE, 'collectable')) {
        visible += 1;
      }
    }
    expect(visible).toBe(71);
  });
});

describe('pointStyle（填充=可采集，描边=路线优先）', () => {
  test('未采集的路线可采集点：蓝色实心 + 橙色描边', () => {
    const s = pointStyle(false, true, true);
    expect(s.fill).toBe(FP_POINT_COLORS.collectable);
    expect(s.stroke).toBe(FP_POINT_COLORS.route);
    expect(s.sizePx).toBe(8);
  });

  test('未采集的路线不可采集点：灰色空心 + 橙色描边（不误报为可采）', () => {
    const s = pointStyle(false, true, false);
    expect(s.fill).toBeNull();
    expect(s.stroke).toBe(FP_POINT_COLORS.route);
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

describe('floorCounts（本层可采集 / 总 / 已采）', () => {
  test('只统计本层，可采集与已采各自独立计数', () => {
    const pts = [
      { fpId: 'a', isBase2m: true, collectable: true, routeTags: [] },
      { fpId: 'b', isBase2m: false, collectable: false, routeTags: [] },
      { fpId: 'c', isBase2m: true, collectable: true, routeTags: ['R2'] },
    ];
    const counts = floorCounts(pts, new Map([['a', 3], ['b', 1]]));
    expect(counts).toEqual({ total: 3, collectable: 2, collected: 2 });
  });

  test('空楼层返回全零（不抛错）', () => {
    expect(floorCounts([], new Map())).toEqual({ total: 0, collectable: 0, collected: 0 });
  });
});
