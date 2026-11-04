/**
 * 采集页网格点的可见性 / 筛选 / 样式（纯逻辑，零 wx 依赖，供页面与单测复用）。
 *
 * 规则（spec field-collection-integration §4.1，v0.2.1）：
 * - LOD：相邻 1m 点在屏幕间距 ≥ LOD_1M_MIN_SPACING_PX 时才绘制 1m 普通点；
 * - 路线点（routeTags 非空）不受 LOD 限制，整楼视图也全部绘制；
 * - 筛选三档：all（默认）/ collectable（仅可采集）/ route（仅路线点）；
 * - 已采集点一律绿色；已采集的路线点额外保留橙色描边，避免丢失“路线点”信息。
 */

export type FilterMode = 'all' | 'collectable' | 'route';

/** 1m 点的 LOD 像素间距阈值（真机可调 10px）。 */
export const LOD_1M_MIN_SPACING_PX = 8;

/** 网格点配色（页面顶部常量统一引用，便于后续调整）。 */
export const FP_POINT_COLORS = {
  route: '#F97316',
  routeStroke: '#FFFFFF',
  collectable: 'rgba(37, 99, 235, 0.7)',
  unavailable: 'rgba(148, 163, 184, 0.75)',
  collected: '#16a34a',
  collectedText: '#15803d',
  labelStroke: 'rgba(255, 255, 255, 0.9)',
} as const;

export interface GridPointLike {
  isBase2m: boolean;
  collectable: boolean;
  routeTags: string[];
}

export function isRoutePoint(p: Pick<GridPointLike, 'routeTags'>): boolean {
  return p.routeTags.length > 0;
}

/** 点是否绘制（含 LOD 豁免与筛选档位）。 */
export function isPointVisible(
  p: GridPointLike,
  scalePxPerM: number,
  mode: FilterMode,
  minSpacingPx: number = LOD_1M_MIN_SPACING_PX,
): boolean {
  if (mode === 'route') {
    return isRoutePoint(p);
  }
  if (mode === 'collectable' && !p.collectable) {
    return false;
  }
  // 路线点不受 LOD 限制；普通 1m 点需满足屏幕间距
  return p.isBase2m || isRoutePoint(p) || scalePxPerM >= minSpacingPx;
}

export function filterVisiblePoints<T extends GridPointLike>(
  pts: T[],
  scalePxPerM: number,
  mode: FilterMode,
  minSpacingPx: number = LOD_1M_MIN_SPACING_PX,
): T[] {
  return pts.filter((p) => isPointVisible(p, scalePxPerM, mode, minSpacingPx));
}

export interface PointStyle {
  fill: string | null;
  stroke: string | null;
  strokeWidth: number;
  sizePx: number;
}

/** 绘制样式：已采集 > 路线 > 可采集 > 不可采集（路线点始终保留橙色标记）。 */
export function pointStyle(hasRecord: boolean, isRoute: boolean, collectable: boolean): PointStyle {
  if (hasRecord) {
    return isRoute
      ? { fill: FP_POINT_COLORS.collected, stroke: FP_POINT_COLORS.route, strokeWidth: 2, sizePx: 10 }
      : { fill: FP_POINT_COLORS.collected, stroke: null, strokeWidth: 0, sizePx: 8 };
  }
  if (isRoute) {
    return { fill: FP_POINT_COLORS.route, stroke: FP_POINT_COLORS.routeStroke, strokeWidth: 1.5, sizePx: 8 };
  }
  if (collectable) {
    return { fill: FP_POINT_COLORS.collectable, stroke: null, strokeWidth: 0, sizePx: 4 };
  }
  return { fill: null, stroke: FP_POINT_COLORS.unavailable, strokeWidth: 1, sizePx: 4 };
}

/** 详情卡路线文案：有标签时标注“优先采集”。 */
export function routeText(routeTags: string[]): string {
  return routeTags.length > 0 ? `${routeTags.join(' / ')} · 优先采集` : '—';
}
