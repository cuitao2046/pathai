/**
 * 采集页选点命中判定（纯逻辑，零 wx 依赖）。
 *
 * 坐标口径：屏幕坐标 = 画布 CSS 像素（与 FloorMapViewport.toScreen 同口径）。
 * 命中半径固定为屏幕像素，因此缩放/平移只改变投影，不改变「手点在哪、选中哪颗点」的
 * 对应关系：放大后同样半径对应的世界范围按 scale 收窄，相邻点位更易区分，选点更精确。
 */

export interface CollectPointXY {
  x: number;
  y: number;
}

/** 只依赖 toScreen 的投影器（可直接传 FloorMapViewport）。 */
export interface ScreenProjector {
  toScreen(x: number, y: number): { sx: number; sy: number };
}

export interface ScreenPick<T> {
  point: T;
  /** 点位与点击点的屏幕距离（CSS 像素）。 */
  distPx: number;
}

/**
 * 在屏幕空间做最近点命中：
 * - 先把每个候选点用当前视口投影为屏幕坐标，再与点击点比距离；
 * - 超过 `radiusPx` 的候选不参与；多个候选取屏幕距离最近者；
 * - `tap` 必须是画布相对坐标（与 toScreen 输出同一坐标系）。
 */
export function pickPointOnScreen<T>(
  points: ReadonlyArray<T>,
  pointXY: (point: T) => CollectPointXY,
  projector: ScreenProjector,
  tap: { x: number; y: number },
  radiusPx: number,
): ScreenPick<T> | null {
  let best: ScreenPick<T> | null = null;
  for (const point of points) {
    const { x, y } = pointXY(point);
    const { sx, sy } = projector.toScreen(x, y);
    const distPx = Math.hypot(sx - tap.x, sy - tap.y);
    if (distPx > radiusPx) {
      continue;
    }
    if (best === null || distPx < best.distPx) {
      best = { point, distPx };
    }
  }
  return best;
}
