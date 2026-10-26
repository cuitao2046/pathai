/**
 * FloorMapViewport / floor-geometry 单测：真实地图包 2026-10-25.1 数据驱动
 * （项目约束：mock 必须使用真实地图与信标数据）。
 */
import { FloorMapViewport } from '../core/map/floor-map-viewport';
import { flattenWalls, flatBounds, floorsSortedByLevel, floorPlaceLabels } from '../core/map/floor-geometry';
import { REAL_MAP } from './fixtures/real-map';

const FLOORS = REAL_MAP.floors;
const SEGS1 = flattenWalls(FLOORS, '1');
const BOUNDS1 = flatBounds(SEGS1);
const VIEW_W = 375;
const VIEW_H = 667;

function makeViewport(): FloorMapViewport {
  return new FloorMapViewport(BOUNDS1, VIEW_W, VIEW_H, '1');
}

describe('floor-geometry（真实墙体数据）', () => {
  test('1 层扁平墙段数 = 真实墙坐标段数（2436 根 2 点线段）', () => {
    const walls = FLOORS['1'].geometry.walls;
    const expected = walls.reduce(
      (acc, w) => acc + Math.max(0, w.geometry.coordinates.length - 1),
      0,
    );
    expect(walls.length).toBe(2436);
    expect(expected).toBe(2436); // 全为 2 点 LineString：段数 = 墙根数
    expect(SEGS1.length).toBe(expected * 4);
  });

  test('flatBounds 覆盖真实 1 层坐标范围', () => {
    // 真实基线：F1 x ∈ [-84.3, 13.1]、y ∈ [-89.4, 39.5]
    expect(BOUNDS1.minX).toBeLessThan(-84);
    expect(BOUNDS1.maxX).toBeGreaterThan(13);
    expect(BOUNDS1.minY).toBeLessThan(-89);
    expect(BOUNDS1.maxY).toBeGreaterThan(39);
  });

  test('floorsSortedByLevel 按 level 升序', () => {
    const list = floorsSortedByLevel(FLOORS);
    expect(list.map((f) => f.floorId)).toEqual(['1', '2']);
    expect(list[0].displayName).toBe('一楼');
    expect(list[1].displayName).toBe('二楼');
  });

  test('floorPlaceLabels 每层地点全部解析出坐标（空间语义标注）', () => {
    for (const floorId of ['1', '2']) {
      const expectedCount = REAL_MAP.places.filter((p) => p.floorId === floorId).length;
      expect(expectedCount).toBeGreaterThan(0);
      const labels = floorPlaceLabels(FLOORS, REAL_MAP.places, floorId);
      expect(labels).toHaveLength(expectedCount); // nodeId 坐标全部可解析
    }
    const f1 = floorPlaceLabels(FLOORS, REAL_MAP.places, '1');
    const resource = f1.find((l) => l.text === '资源教室');
    expect(resource).toBeDefined();
    // 真实坐标落在 1 层范围内（x ∈ [-84,13]、y ∈ [-89,40]）
    expect(resource!.x).toBeGreaterThan(-85);
    expect(resource!.x).toBeLessThan(14);
    expect(resource!.y).toBeGreaterThan(-90);
    expect(resource!.y).toBeLessThan(41);
  });
});

describe('FloorMapViewport', () => {
  test('fit 后 bounds 中心映射到视口中心', () => {
    const vp = makeViewport();
    const cx = (BOUNDS1.minX + BOUNDS1.maxX) / 2;
    const cy = (BOUNDS1.minY + BOUNDS1.maxY) / 2;
    const { sx, sy } = vp.toScreen(cx, cy);
    expect(sx).toBeCloseTo(VIEW_W / 2, 6);
    expect(sy).toBeCloseTo(VIEW_H / 2, 6);
    // fit 后整层落在视口内（含 padding）
    expect(vp.toScreen(BOUNDS1.minX, BOUNDS1.maxY).sx).toBeGreaterThanOrEqual(0);
    expect(vp.toScreen(BOUNDS1.maxX, BOUNDS1.minY).sy).toBeLessThanOrEqual(VIEW_H);
  });

  test('toScreen / fromScreen 往返恒等', () => {
    const vp = makeViewport();
    vp.zoomAt(100, 200, 3.5);
    vp.panBy(37, -52);
    const back = vp.fromScreen(vp.toScreen(-40.5, -77.25).sx, vp.toScreen(-40.5, -77.25).sy);
    expect(back.x).toBeCloseTo(-40.5, 9);
    expect(back.y).toBeCloseTo(-77.25, 9);
  });

  test('zoomAt 锚点世界坐标不变', () => {
    const vp = makeViewport();
    const anchor = vp.fromScreen(120, 300);
    vp.zoomAt(120, 300, 2);
    const after = vp.fromScreen(120, 300);
    expect(after.x).toBeCloseTo(anchor.x, 9);
    expect(after.y).toBeCloseTo(anchor.y, 9);
    expect(vp.scale).toBeCloseTo(vp.minScale * 2, 9);
  });

  test('scale 被夹紧在 [fitScale, fitScale×8]', () => {
    const vp = makeViewport();
    const min = vp.minScale;
    vp.zoomAt(0, 0, 0.001); // 远低于下限
    expect(vp.scale).toBeCloseTo(min, 9);
    vp.zoomAt(0, 0, 1e9); // 远高于上限
    expect(vp.scale).toBeCloseTo(vp.maxScale, 9);
    expect(vp.maxScale).toBeCloseTo(min * 8, 9);
  });

  test('极端平移后包围盒仍与视口相交（整层拖不丢）', () => {
    const vp = makeViewport();
    vp.panBy(1e7, 1e7);
    const l = vp.toScreen(BOUNDS1.minX, BOUNDS1.maxY);
    const r = vp.toScreen(BOUNDS1.maxX, BOUNDS1.minY);
    const EPS = 1e-6; // clamp 拉回边缘的浮点误差
    expect(r.sx).toBeGreaterThanOrEqual(-EPS); // 右缘不完全离开左侧
    expect(l.sy).toBeLessThanOrEqual(VIEW_H + EPS); // 下缘不完全离开顶部
    vp.panBy(-1e7, -1e7);
    const l2 = vp.toScreen(BOUNDS1.maxX, BOUNDS1.minY);
    expect(l2.sx).toBeLessThanOrEqual(VIEW_W + EPS);
  });

  test('setFloor 保持 scale，按新层 fitScale 夹紧', () => {
    const vp = makeViewport();
    vp.zoomAt(188, 300, 4); // 放大到 4×fit
    const scaleBefore = vp.scale;
    const offBefore = { x: vp.offsetX, y: vp.offsetY };
    const segs2 = flattenWalls(FLOORS, '2');
    vp.setFloor('2', flatBounds(segs2));
    expect(vp.floorId).toBe('2');
    expect(vp.scale).toBeCloseTo(scaleBefore, 6); // 视口保持
    expect(vp.offsetX).toBe(offBefore.x);
    expect(vp.offsetY).toBe(offBefore.y);

    // 若新层 fitScale 高于当前 scale（新层更小/更集中），scale 被抬到下限
    vp.zoomAt(0, 0, 1e9); // 上限
    vp.setFloor('1', BOUNDS1);
    vp.setFloor('2', flatBounds(segs2));
    expect(vp.scale).toBeGreaterThanOrEqual(vp.minScale - 1e-9);
    expect(vp.scale).toBeLessThanOrEqual(vp.maxScale + 1e-9);
  });

  test('resize 重新适配且居中', () => {
    const vp = makeViewport();
    vp.zoomAt(50, 50, 6);
    vp.resize(812, 375);
    const cx = (BOUNDS1.minX + BOUNDS1.maxX) / 2;
    const cy = (BOUNDS1.minY + BOUNDS1.maxY) / 2;
    expect(vp.toScreen(cx, cy).sx).toBeCloseTo(812 / 2, 6);
    expect(vp.toScreen(cx, cy).sy).toBeCloseTo(375 / 2, 6);
  });
});
