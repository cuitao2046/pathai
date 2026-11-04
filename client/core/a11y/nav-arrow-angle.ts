/**
 * 导航方向箭头的角度数学（纯逻辑，零 wx 依赖）。
 *
 * 坐标约定与地图一致：x 向东（屏幕右）、y 向南（屏幕下），屏幕上方 = 北。
 * 方位角 = 从屏幕上方顺时针为正（0=正上/北，90=右/东）。
 *
 * 箭头指向采用「相对手机朝向」（spec Q1 拍板 B）：
 *   arrowDeg = normalize(routeBearing − heading)
 *   箭头朝上 ⇔ 手机正对路线。
 */

export type DeviationBand = 'aligned' | 'near' | 'off';

/** EMA 平滑系数（spec §5.5，按 5 Hz 计）。 */
export const HEADING_EMA_ALPHA = 0.15;
/** 静止冻结：连续该时长内变化 < STATIC_EPS_DEG 则冻结显示。 */
export const STATIC_FREEZE_MS = 1000;
export const STATIC_EPS_DEG = 3;
/** 偏离警示阈值：>30° 持续 ≥2 s 才进入警示态。 */
export const DEVIATION_ALERT_DEG = 30;
export const ALIGNED_DEG = 15;

/** 归一化到 [0, 360)。 */
export function normalizeDeg(deg: number): number {
  const mod = deg % 360;
  return mod < 0 ? mod + 360 : mod;
}

/** from → to 的最短弧差，范围 (-180, 180]；正值为顺时针。 */
export function shortestDelta(from: number, to: number): number {
  const delta = normalizeDeg(to - from + 180) - 180;
  return delta === -180 ? 180 : delta;
}

/** 相对手机朝向的箭头角度（0=朝上=对准）。 */
export function arrowAngle(routeBearing: number, heading: number): number {
  return normalizeDeg(routeBearing - heading);
}

/** 箭头相对"朝上"的偏离量（0–180）。0° 与 360° 是同一方向。 */
export function deviationOfArrow(arrowDeg: number): number {
  return Math.abs(shortestDelta(0, arrowDeg));
}

/** 从 from 指向 to 的方位角（顺时针为正，屏幕上方=0）。 */
export function bearingBetween(
  from: { x: number; y: number },
  to: { x: number; y: number },
): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  return normalizeDeg((Math.atan2(dx, -dy) * 180) / Math.PI);
}

/**
 * 路线在当前位置的行进方向：取最近折线顶点出发的下一段；
 * 若已到最后一个顶点则取指向该顶点的最后一段。同层点集合由调用方过滤。
 */
export function routeBearing(
  points: ReadonlyArray<{ x: number; y: number }>,
  pos: { x: number; y: number },
): number | null {
  if (points.length < 2) {
    return null;
  }
  let nearest = 0;
  let best = Number.POSITIVE_INFINITY;
  for (let i = 0; i < points.length; i += 1) {
    const d = (points[i].x - pos.x) ** 2 + (points[i].y - pos.y) ** 2;
    if (d < best) {
      best = d;
      nearest = i;
    }
  }
  if (nearest < points.length - 1) {
    return bearingBetween(points[nearest], points[nearest + 1]);
  }
  return bearingBetween(points[points.length - 2], points[points.length - 1]);
}

/** 偏离分档：≤15° 对齐；15–30° 接近；>30° 偏离。 */
export function deviationBand(deviation: number): DeviationBand {
  if (deviation <= ALIGNED_DEG) {
    return 'aligned';
  }
  if (deviation <= DEVIATION_ALERT_DEG) {
    return 'near';
  }
  return 'off';
}

/** 播报档位：取整到 15°，最小 15°。 */
export function deviationStep(deviation: number): number {
  return Math.max(15, Math.round(deviation / 15) * 15);
}

/** 角度一阶低通（走最短弧，避免 0/360 回绕跳变）。 */
export function emaAngle(prev: number, next: number, alpha: number = HEADING_EMA_ALPHA): number {
  return normalizeDeg(prev + shortestDelta(prev, next) * alpha);
}

/**
 * 罗盘平滑 + 静止冻结：静止 1 s 内抖动 < 3° 时保持显示角度不变，
 * 避免半盲用户看到箭头持续微抖（spec §5.5 抖动指标 ±2°）。
 */
export class HeadingSmoother {
  private display: number | null = null;
  private lastRaw: number | null = null;
  private stillSince: number | null = null;

  constructor(private readonly alpha: number = HEADING_EMA_ALPHA) {}

  get current(): number | null {
    return this.display;
  }

  reset(): void {
    this.display = null;
    this.lastRaw = null;
    this.stillSince = null;
  }

  update(angleDeg: number, now: number): number {
    const raw = normalizeDeg(angleDeg);
    if (this.display === null || this.lastRaw === null) {
      this.display = raw;
      this.lastRaw = raw;
      this.stillSince = now;
      return raw;
    }
    const delta = shortestDelta(this.lastRaw, raw);
    if (Math.abs(delta) < STATIC_EPS_DEG) {
      if (this.stillSince === null) {
        this.stillSince = now;
      }
    } else {
      this.stillSince = null;
    }
    this.lastRaw = raw;
    if (this.stillSince !== null && now - this.stillSince >= STATIC_FREEZE_MS) {
      return this.display; // 静止冻结：不跟随 ±3° 内的抖动
    }
    this.display = emaAngle(this.display, raw, this.alpha);
    return this.display;
  }
}
