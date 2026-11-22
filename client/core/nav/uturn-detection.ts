/**
 * 反向掉头检测（T9 时效实验）。
 *
 * 判定口径（与现场实验方案一致）：
 * - 起点段：位置沿路线投影始终回弹到 0（route progress 夹紧），用「距起点实际距离」衡量反向里程；
 * - 途中段：progress = 路线总长 − 剩余距离，记录历史最大 progress，回退里程 = max − 当前；
 * - 反向判定：最近 2–3 个位置中取位移 ≥1.5m 的一段求运动方位角，
 *   与路线在最近折线点的前进方位角相差 ≥120°；
 * - 连续 2 次定位更新满足反向且回退 ≥5m 才触发，避免单拍噪声误报；
 * - 触发后只有「重新向前 ≥2m 且方位角 <60° 连续 2 次」或换路线（reset）才复位。
 *
 * 纯逻辑、零 wx 依赖，便于单测。
 */
import type { GeoPosition } from '../../typings/api';
import { bearingBetween, shortestDelta } from '../a11y/nav-arrow-angle';
import { remainingRouteDistanceM } from './turn-haptics';

export interface UturnPosition {
  x: number;
  y: number;
  floorId?: string | null;
}

export interface UturnConfig {
  /** 反向里程阈值（米），T9 固定 5m。 */
  triggerDistanceM: number;
  /** 运动方向与路线前进方向的夹角阈值（度）。 */
  reverseAngleDeg: number;
  /** 连续满足条件的定位更新次数。 */
  confirmUpdates: number;
  /** 参与运动方位角计算的历史位置数量。 */
  movementWindow: number;
  /** 运动方位角所需的最小位移（米）。 */
  minDisplacementM: number;
  /** 重新向前多少米后允许复位。 */
  resetForwardM: number;
  /** 复位时允许的方位角偏差（度）。 */
  resetAngleDeg: number;
  /** 路线前进方位角取段的最小长度（米），过滤退化折线。 */
  minSegmentM: number;
}

export const UTURN_DEFAULT_CONFIG: UturnConfig = {
  triggerDistanceM: 5,
  reverseAngleDeg: 120,
  confirmUpdates: 2,
  movementWindow: 3,
  minDisplacementM: 1.5,
  resetForwardM: 2,
  resetAngleDeg: 60,
  minSegmentM: 0.5,
};

export type UturnMode = 'start' | 'mid_route';

export interface UturnTrigger {
  mode: UturnMode;
  /** 反向（回退）里程，米。 */
  reverseDistanceM: number;
  /** 运动方向与路线前进方向的夹角，度。 */
  angleDeg: number;
  /** 当前沿路线进度，米。 */
  progressM: number;
}

export class UturnDetector {
  private readonly cfg: UturnConfig;

  private history: UturnPosition[] = [];

  private reverseStreak = 0;

  private forwardStreak = 0;

  private maxProgressM = 0;

  private triggered = false;

  constructor(config: Partial<UturnConfig> = {}) {
    this.cfg = { ...UTURN_DEFAULT_CONFIG, ...config };
  }

  /** 换路线 / 新会话：清空历史与触发标记。 */
  reset(): void {
    this.history = [];
    this.reverseStreak = 0;
    this.forwardStreak = 0;
    this.maxProgressM = 0;
    this.triggered = false;
  }

  /**
   * 喂入一次可信定位。返回触发结果（仅首次满足条件时返回非 null）。
   * 位置/路线缺失或跨层时返回 null，不改变状态。
   */
  update(position: UturnPosition | null, polyline: readonly GeoPosition[] | null | undefined): UturnTrigger | null {
    if (position === null || !polyline || polyline.length < 2) {
      return null;
    }
    const floorId = position.floorId ?? polyline[0].floorId ?? null;
    if (floorId === null) {
      return null;
    }
    const start = polyline.find((point) => (point.floorId ?? floorId) === floorId);
    if (!start) {
      return null;
    }
    const remaining = remainingRouteDistanceM(polyline, { ...position, floorId });
    if (remaining === null) {
      return null;
    }
    const progressM = Math.max(0, totalLengthM(polyline) - remaining);
    const reverseDistanceM = progressM <= 0.5
      ? Math.hypot(position.x - start.x, position.y - start.y)
      : Math.max(0, this.maxProgressM - progressM);
    const angleDeg = this.reverseAngleDeg(position, polyline, floorId);
    this.maxProgressM = Math.max(this.maxProgressM, progressM);

    if (this.triggered) {
      this.trackReset(angleDeg, reverseDistanceM);
      this.remember(position);
      return null;
    }

    const reversed = angleDeg !== null && angleDeg >= this.cfg.reverseAngleDeg;
    if (reversed && reverseDistanceM >= this.cfg.triggerDistanceM) {
      this.reverseStreak += 1;
      if (this.reverseStreak >= this.cfg.confirmUpdates) {
        this.triggered = true;
        this.remember(position);
        return {
          mode: progressM <= 0.5 ? 'start' : 'mid_route',
          reverseDistanceM: round1(reverseDistanceM),
          angleDeg: round1(angleDeg ?? 0),
          progressM: round1(progressM),
        };
      }
    } else {
      this.reverseStreak = 0;
    }
    this.remember(position);
    return null;
  }

  private trackReset(angleDeg: number | null, reverseDistanceM: number): void {
    const advancing = angleDeg !== null && angleDeg < this.cfg.resetAngleDeg;
    this.forwardStreak = advancing && reverseDistanceM <= this.cfg.triggerDistanceM - this.cfg.resetForwardM
      ? this.forwardStreak + 1 : 0;
    if (this.forwardStreak >= this.cfg.confirmUpdates) {
      this.triggered = false;
      this.reverseStreak = 0;
      this.forwardStreak = 0;
    }
  }

  private remember(position: UturnPosition): void {
    this.history.push({ x: position.x, y: position.y, floorId: position.floorId });
    if (this.history.length > this.cfg.movementWindow) {
      this.history.shift();
    }
  }

  /** 运动方位角与路线前进方位角的绝对夹角；样本不足时返回 null。 */
  private reverseAngleDeg(
    position: UturnPosition,
    polyline: readonly GeoPosition[],
    floorId: string,
  ): number | null {
    const forward = forwardBearing(polyline, position, floorId, this.cfg.minSegmentM);
    if (forward === null) {
      return null;
    }
    // 取「最近一个位移足够」的历史点：掉头方向由最近一步决定，
    // 用最旧点会把「先向前再折返」误判成仍在向前。
    for (let i = this.history.length - 1; i >= 0; i -= 1) {
      const from = this.history[i];
      if ((from.floorId ?? floorId) !== floorId) {
        continue;
      }
      if (Math.hypot(position.x - from.x, position.y - from.y) < this.cfg.minDisplacementM) {
        continue;
      }
      const movement = bearingBetween(from, position);
      return Math.abs(shortestDelta(forward, movement));
    }
    return null;
  }
}

function totalLengthM(polyline: readonly GeoPosition[]): number {
  let total = 0;
  for (let i = 1; i < polyline.length; i += 1) {
    total += Math.hypot(polyline[i].x - polyline[i - 1].x, polyline[i].y - polyline[i - 1].y);
  }
  return total;
}

/**
 * 路线在当前位置的前进方位角：取最近折线顶点，再向后找第一段 ≥minSegmentM 的同层线段；
 * 找不到退化段时回退到最近点的上一条有效线段。
 */
function forwardBearing(
  polyline: readonly GeoPosition[],
  position: { x: number; y: number },
  floorId: string,
  minSegmentM: number,
): number | null {
  let nearest = -1;
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < polyline.length; i += 1) {
    if ((polyline[i].floorId ?? floorId) !== floorId) {
      continue;
    }
    const distance = (polyline[i].x - position.x) ** 2 + (polyline[i].y - position.y) ** 2;
    if (distance < best) {
      best = distance;
      nearest = i;
    }
  }
  if (nearest < 0) {
    return null;
  }
  const origin = polyline[nearest];
  for (let i = nearest + 1; i < polyline.length; i += 1) {
    const point = polyline[i];
    if ((point.floorId ?? floorId) !== floorId) {
      break;
    }
    if (Math.hypot(point.x - origin.x, point.y - origin.y) >= minSegmentM) {
      return bearingBetween(origin, point);
    }
  }
  for (let i = nearest - 1; i >= 0; i -= 1) {
    const point = polyline[i];
    if ((point.floorId ?? floorId) !== floorId) {
      break;
    }
    if (Math.hypot(point.x - origin.x, point.y - origin.y) >= minSegmentM) {
      return bearingBetween(point, origin);
    }
  }
  return null;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}
