/**
 * 采集页地图交互单测：
 * - 屏幕命中判定随视口缩放/平移正确变换（放大后不偏移、命中世界范围收窄）；
 * - scale 到达上下限后继续缩放不漂移。
 * 数据：真实地图包 2026-10-25.1 的 1 层墙体 + 真实标定点/指纹网格点。
 */
import { FloorMapViewport } from '../core/map/floor-map-viewport';
import { flattenWalls, flatBounds } from '../core/map/floor-geometry';
import { pickPointOnScreen } from '../core/collection/hit-test';
import { PRESET_CALIBRATION_POINTS } from '../data/calibration_points';
import { FINGERPRINT_GRID } from '../data/fingerprint_grid';
import { REAL_MAP } from './fixtures/real-map';

const SEGS1 = flattenWalls(REAL_MAP.floors, '1');
const BOUNDS1 = flatBounds(SEGS1);
const VIEW_W = 375;
const VIEW_H = 667;

const CAL_F1 = PRESET_CALIBRATION_POINTS.filter((p) => p.floorId === '1');
const calXY = (p: (typeof CAL_F1)[number]) => ({ x: p.truth[0], y: p.truth[1] });
const GRID_F1 = FINGERPRINT_GRID.filter((p) => p.floorId === '1');
const gridXY = (p: (typeof GRID_F1)[number]) => ({ x: p.x, y: p.y });

const STATIC_HIT_RADIUS_PX = 24;
const FP_HIT_RADIUS_PX = 16;

function makeViewport(): FloorMapViewport {
  return new FloorMapViewport(BOUNDS1, VIEW_W, VIEW_H, '1');
}

/** 视口投影结果（sx/sy）→ 点击点（x/y）。 */
function tapAt(p: { sx: number; sy: number }): { x: number; y: number } {
  return { x: p.sx, y: p.sy };
}

describe('pickPointOnScreen（真实标定点）', () => {
  test('fit 视图下点在点位上 → 选中该点，屏幕距离为 0', () => {
    const vp = makeViewport();
    const target = CAL_F1.find((p) => p.pointId === 'CAL-F1-01')!;
    const screen = vp.toScreen(target.truth[0], target.truth[1]);
    const hit = pickPointOnScreen(CAL_F1, calXY, vp, tapAt(screen), STATIC_HIT_RADIUS_PX);
    expect(hit?.point.pointId).toBe('CAL-F1-01');
    expect(hit!.distPx).toBeCloseTo(0, 9);
  });

  test('放大后同一世界偏移超出屏幕命中半径 → 不再命中（选点范围按 scale 收窄）', () => {
    const vp = makeViewport();
    const target = CAL_F1.find((p) => p.pointId === 'CAL-F1-01')!;
    // 距目标 4 m（fit 视图约 14 px，落在 24 px 命中半径内）
    const tapWorld = { x: target.truth[0] + 4, y: target.truth[1] };
    const fitScreen = vp.toScreen(tapWorld.x, tapWorld.y);
    const fitHit = pickPointOnScreen(CAL_F1, calXY, vp, tapAt(fitScreen), STATIC_HIT_RADIUS_PX);
    expect(fitHit?.point.pointId).toBe('CAL-F1-01');
    expect(fitHit!.distPx).toBeCloseTo(4 * vp.fitScale, 6);
    expect(fitHit!.distPx).toBeLessThanOrEqual(STATIC_HIT_RADIUS_PX);

    // 以目标点自身为锚点放大 8×：同一世界点变成约 112 px，超出命中半径
    const anchor = vp.toScreen(target.truth[0], target.truth[1]);
    vp.zoomAt(anchor.sx, anchor.sy, 8);
    const zoomScreen = vp.toScreen(tapWorld.x, tapWorld.y);
    expect(pickPointOnScreen(CAL_F1, calXY, vp, tapAt(zoomScreen), STATIC_HIT_RADIUS_PX)).toBeNull();

    // 命中半径对应的世界范围按放大倍数收窄
    expect(STATIC_HIT_RADIUS_PX / vp.scale).toBeCloseTo(STATIC_HIT_RADIUS_PX / vp.fitScale / 8, 9);
  });

  test('缩放+平移后按当前投影点选：命中目标且与 fromScreen 自洽', () => {
    const vp = makeViewport();
    vp.zoomAt(200, 300, 3);
    vp.panBy(40, -60);
    const target = CAL_F1.find((p) => p.pointId === 'CAL-F1-02')!;
    const screen = vp.toScreen(target.truth[0], target.truth[1]);
    const hit = pickPointOnScreen(CAL_F1, calXY, vp, tapAt(screen), STATIC_HIT_RADIUS_PX);
    expect(hit?.point.pointId).toBe('CAL-F1-02');
    expect(hit!.distPx).toBeCloseTo(0, 9);

    // 屏幕点反算世界坐标与点位一致（证明选点用的是同一套视口变换）
    const back = vp.fromScreen(screen.sx, screen.sy);
    expect(back.x).toBeCloseTo(target.truth[0], 9);
    expect(back.y).toBeCloseTo(target.truth[1], 9);
  });

  test('命中半径边界：半径内命中、半径外不命中', () => {
    const vp = makeViewport();
    const target = CAL_F1.find((p) => p.pointId === 'CAL-F1-15')!;
    const inside = (STATIC_HIT_RADIUS_PX - 0.1) / vp.scale;
    const outside = (STATIC_HIT_RADIUS_PX + 0.1) / vp.scale;
    // 向 -x 偏移，避开邻近点位
    const hit = pickPointOnScreen(
      CAL_F1,
      calXY,
      vp,
      tapAt(vp.toScreen(target.truth[0] - inside, target.truth[1])),
      STATIC_HIT_RADIUS_PX,
    );
    expect(hit?.point.pointId).toBe('CAL-F1-15');
    expect(
      pickPointOnScreen(
        CAL_F1,
        calXY,
        vp,
        tapAt(vp.toScreen(target.truth[0] - outside, target.truth[1])),
        STATIC_HIT_RADIUS_PX,
      ),
    ).toBeNull();
  });
});

describe('pickPointOnScreen（真实指纹网格 2 m 间距）', () => {
  test('放大后相邻 2 m 网格点可分别精确选中', () => {
    const vp = makeViewport();
    // v2 编号由坐标派生，不再有固定序号；动态找一对同排相距 2m 的 2m 底网点
    let pair: [typeof GRID_F1[number], typeof GRID_F1[number]] | null = null;
    for (const p of GRID_F1) {
      if (!p.isBase2m) continue;
      const q = GRID_F1.find((r) => r.isBase2m && r.row === p.row && r.col === p.col + 2);
      if (q !== undefined) {
        pair = [p, q];
        break;
      }
    }
    expect(pair).not.toBeNull();
    const [a, b] = pair!;

    vp.zoomAt(VIEW_W / 2, VIEW_H / 2, 4);
    const hitA = pickPointOnScreen(
      GRID_F1,
      gridXY,
      vp,
      tapAt(vp.toScreen(a.x, a.y)),
      FP_HIT_RADIUS_PX,
    );
    const hitB = pickPointOnScreen(
      GRID_F1,
      gridXY,
      vp,
      tapAt(vp.toScreen(b.x, b.y)),
      FP_HIT_RADIUS_PX,
    );
    expect(hitA?.point.fpId).toBe(a.fpId);
    expect(hitB?.point.fpId).toBe(b.fpId);
    // fit 视图下相邻 2 m 点间距（约 7 px）小于命中半径 → 邻点也在命中圈内；
    // 放大 4× 后间距（约 28 px）大于命中半径 → 点 A 的命中圈不再包含 B，选点更精确
    expect(2 * vp.fitScale).toBeLessThan(FP_HIT_RADIUS_PX);
    expect(2 * vp.scale).toBeGreaterThan(FP_HIT_RADIUS_PX);
  });
});

describe('缩放上下限不漂移', () => {
  test('到达最大/最小 scale 后继续缩放：scale 与 offset 不变', () => {
    const vp = makeViewport();
    vp.zoomAt(VIEW_W / 2, VIEW_H / 2, 1e6);
    const maxSnap = { scale: vp.scale, x: vp.offsetX, y: vp.offsetY };
    expect(vp.scale).toBeCloseTo(vp.maxScale, 9);
    vp.zoomAt(VIEW_W / 2, VIEW_H / 2, 1.6);
    expect(vp.scale).toBeCloseTo(maxSnap.scale, 9);
    expect(vp.offsetX).toBeCloseTo(maxSnap.x, 9);
    expect(vp.offsetY).toBeCloseTo(maxSnap.y, 9);

    vp.fit();
    vp.zoomAt(VIEW_W / 2, VIEW_H / 2, 1e-6);
    const minSnap = { scale: vp.scale, x: vp.offsetX, y: vp.offsetY };
    expect(vp.scale).toBeCloseTo(vp.minScale, 9);
    vp.zoomAt(VIEW_W / 2, VIEW_H / 2, 1 / 1.6);
    expect(vp.scale).toBeCloseTo(minSnap.scale, 9);
    expect(vp.offsetX).toBeCloseTo(minSnap.x, 9);
    expect(vp.offsetY).toBeCloseTo(minSnap.y, 9);
  });
});
