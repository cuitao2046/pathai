/**
 * 转弯逼近震动（REQ-A11Y-TURN-HAPTIC）。
 *
 * 与语音分工：语音仍负责「距拐点 8~10m 的提前播报」，本模块只负责**最后几米**的
 * 距离逼近感——按“到当前转弯拐点的剩余沿线距离”分区给节拍，越近越密，
 * 到拐点长振一次即止。
 *
 * 坐标：地图世界坐标 x 东、y 北（y 轴朝上，数学坐标系）；距离一律沿路线 polyline
 * 累计欧氏里程（与后端推进器同口径），左右方向不在这里判定。
 *
 * 平台：iOS 的 `wx.vibrateShort({ type })` 轻重档不生效，强弱感完全由**间隔**表达，
 * 所以这里所有节拍都用同一档短振，禁止按 type 分支；Android 同档也够用。
 */

import type { GeoPosition, MapFloorData } from '../../typings/api';
import { nowMs } from '../../utils/clock';

export interface TurnHapticConfig {
  /** 距拐点超过该值不振（米）。 */
  quietZoneM: number;
  /** 6~4m 档：约 0.8s 一次。 */
  smoothZoneM: number;
  /** 4~2m 档：约 0.4s 一次。 */
  mediumZoneM: number;
  /** 2m 内到该值：约 0.2s 一次。 */
  passZoneM: number;
  smoothIntervalMs: number;
  mediumIntervalMs: number;
  fastIntervalMs: number;
  /** 节拍下限：任何分区都不得低于 0.2s（需求约束）。 */
  minIntervalMs: number;
  /** 外推步行速度（m/s），与 mock 一致；真机可后续改为最近估计速度。 */
  walkSpeedMps: number;
  /**
   * 外推上限（ms）。上报间隔 1s，留 1.5s 余量；停走或上报中断时不再继续"逼近"，
   * 避免用户站着不动却越振越密、甚至误触长振。
   */
  maxExtrapolationMs: number;
  /** 调度粒度（ms）：100ms 足以表达 200ms 下限，又不至于空转太频繁。 */
  tickMs: number;
}

/** 现场可调的默认参数（无设置体系时先用常量）。 */
export const TURN_HAPTIC_CONFIG: TurnHapticConfig = {
  quietZoneM: 6,
  smoothZoneM: 4,
  mediumZoneM: 2,
  passZoneM: 0.5,
  smoothIntervalMs: 800,
  mediumIntervalMs: 400,
  fastIntervalMs: 200,
  minIntervalMs: 200,
  walkSpeedMps: 1.5,
  maxExtrapolationMs: 1500,
  tickMs: 100,
};

export type TurnHapticZone = 'quiet' | 'smooth' | 'medium' | 'fast' | 'passed';

/** 按剩余沿线距离分区：>6 静默；6–4 / 4–2 / ≤2；<0.5（或已越过）为到达。 */
export function zoneForDistance(
  distanceM: number,
  cfg: TurnHapticConfig = TURN_HAPTIC_CONFIG,
): TurnHapticZone {
  if (!Number.isFinite(distanceM) || distanceM < cfg.passZoneM) {
    return 'passed';
  }
  if (distanceM > cfg.quietZoneM) {
    return 'quiet';
  }
  if (distanceM > cfg.smoothZoneM) {
    return 'smooth';
  }
  if (distanceM > cfg.mediumZoneM) {
    return 'medium';
  }
  return 'fast';
}

/** 分区对应节拍间隔；quiet/passed 返回 null。 */
export function intervalForZone(
  zone: TurnHapticZone,
  cfg: TurnHapticConfig = TURN_HAPTIC_CONFIG,
): number | null {
  switch (zone) {
    case 'smooth':
      return Math.max(cfg.smoothIntervalMs, cfg.minIntervalMs);
    case 'medium':
      return Math.max(cfg.mediumIntervalMs, cfg.minIntervalMs);
    case 'fast':
      return Math.max(cfg.fastIntervalMs, cfg.minIntervalMs);
    default:
      return null;
  }
}

export interface TurnHapticDeps {
  vibrateShort: () => void;
  vibrateLong: () => void;
  now?: () => number;
  setTimer?: (handler: () => void, ms: number) => ReturnType<typeof setInterval>;
  clearTimer?: (handle: ReturnType<typeof setInterval>) => void;
}

/**
 * 转弯逼近节拍器：外部只需在“当前指令是转弯”时喂距离，其余交给定时器。
 *
 * 生命周期：
 * - `beginTurn(d)`：新转弯开始（instructionId 变化），复位“已到达”标记；
 * - `update(d)`：同一转弯的位置刷新（1s 一次），两次上报之间按速度外推；
 * - `stop()`：中断（导航结束/到达/偏航/onHide/录音），清定时器且不残留；
 * - 距离 < passZoneM：长振一次并自行停止，后续 update 不再触发（等下一次 beginTurn）。
 */
export class TurnHaptics {
  private readonly cfg: TurnHapticConfig;

  private readonly now: () => number;

  private readonly setTimer: (handler: () => void, ms: number) => ReturnType<typeof setInterval>;

  private readonly clearTimer: (handle: ReturnType<typeof setInterval>) => void;

  private timer: ReturnType<typeof setInterval> | null = null;

  private distanceM: number | null = null;

  private updatedAtMs = 0;

  private lastBeatAtMs: number | null = null;

  /** 已长振过（到达拐点），等下一次 beginTurn 复位。 */
  private done = false;

  constructor(
    private readonly deps: TurnHapticDeps,
    cfg: TurnHapticConfig = TURN_HAPTIC_CONFIG,
  ) {
    this.cfg = cfg;
    this.now = deps.now ?? nowMs;
    this.setTimer = deps.setTimer ?? ((handler, ms) => setInterval(handler, ms));
    this.clearTimer = deps.clearTimer ?? ((handle) => clearInterval(handle));
  }

  /** 新转弯开始：清“已到达”标记与节拍相位，立即按新距离判一次。 */
  beginTurn(distanceM: number | null): void {
    this.done = false;
    this.lastBeatAtMs = null;
    this.update(distanceM);
  }

  /** 位置/距离刷新；distanceM 为 null 表示当前没有可取距离（直接停）。 */
  update(distanceM: number | null): void {
    if (this.done) {
      return;
    }
    if (distanceM === null || !Number.isFinite(distanceM)) {
      this.stop();
      return;
    }
    this.distanceM = Math.max(0, distanceM);
    this.updatedAtMs = this.now();
    if (this.timer === null) {
      this.timer = this.setTimer(() => this.tick(), this.cfg.tickMs);
    }
    this.tick(); // 分区切换立即生效，不等下一个调度拍
  }

  /** 中断/结束：清定时器与状态，后续 update 需要重新喂距离。 */
  stop(): void {
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    this.distanceM = null;
    this.updatedAtMs = 0;
    this.lastBeatAtMs = null;
    this.done = false;
  }

  private tick(): void {
    if (this.distanceM === null) {
      return;
    }
    const now = this.now();
    const remain = this.extrapolatedRemaining(now);
    if (remain < this.cfg.passZoneM) {
      this.deps.vibrateLong();
      this.finish();
      return;
    }
    const zone = zoneForDistance(remain, this.cfg);
    const interval = intervalForZone(zone, this.cfg);
    if (interval === null) {
      return; // quiet 区：不振，但定时器继续跑，等外推进区
    }
    const minGap = Math.max(interval, this.cfg.minIntervalMs);
    if (this.lastBeatAtMs === null || now - this.lastBeatAtMs >= minGap - this.cfg.tickMs / 2) {
      this.deps.vibrateShort();
      this.lastBeatAtMs = now;
    }
  }

  /** 两次上报之间按步行速度外推；超过上限即冻结在最后位置。 */
  private extrapolatedRemaining(now: number): number {
    const base = this.distanceM ?? 0;
    const elapsed = Math.min(Math.max(0, now - this.updatedAtMs), this.cfg.maxExtrapolationMs);
    return Math.max(0, base - (this.cfg.walkSpeedMps * elapsed) / 1000);
  }

  /** 长振后立即停止节拍，且在本转弯内不再重启。 */
  private finish(): void {
    if (this.timer !== null) {
      this.clearTimer(this.timer);
      this.timer = null;
    }
    this.done = true;
    this.distanceM = null;
    this.lastBeatAtMs = null;
  }
}

/** 指令 trigger.nodeId → 拓扑节点坐标（跨楼层遍历，找不到返回 null）。 */
export function resolveTurnNode(
  floors: Record<string, MapFloorData> | undefined,
  nodeId: string | null | undefined,
): { x: number; y: number; floorId: string } | null {
  if (floors === undefined || nodeId === null || nodeId === undefined) {
    return null;
  }
  for (const [floorId, floor] of Object.entries(floors)) {
    const node = floor.topology.nodes.find((n) => n.id === nodeId);
    if (node !== undefined) {
      return { x: node.coordinates[0], y: node.coordinates[1], floorId };
    }
  }
  return null;
}

/**
 * 当前位置到转弯拐点的剩余沿线距离（米）。
 * 不同楼层、缺数据、折线退化时返回 null（调用方应停止节拍）。
 */
export function turnDistanceM(
  polyline: readonly GeoPosition[],
  current: { x: number; y: number; floorId?: string | null } | null,
  turn: { x: number; y: number; floorId: string } | null,
): number | null {
  if (current === null || turn === null || polyline.length < 2) {
    return null;
  }
  const floorId = current.floorId ?? turn.floorId;
  if (floorId !== turn.floorId) {
    return null;
  }
  const cum = polylineCumulative(polyline);
  const s = projectAlong(polyline, cum, floorId, current.x, current.y);
  if (s === null) {
    return null;
  }
  const turnCum = turnCumulative(polyline, cum, floorId, turn.x, turn.y, s);
  if (turnCum === null) {
    return null;
  }
  return turnCum - s;
}

/**
 * 当前位置沿路线到终点的剩余距离（米）。
 * 跨层按折线累计（电梯段取折线两点欧氏距离）；无法投影到同层折线时返回 null。
 */
export function remainingRouteDistanceM(
  polyline: readonly GeoPosition[],
  current: { x: number; y: number; floorId?: string | null } | null,
): number | null {
  if (current === null || polyline.length < 2) {
    return null;
  }
  const floorId = current.floorId;
  if (floorId === null || floorId === undefined) {
    return null;
  }
  const cum = polylineCumulative(polyline);
  const s = projectAlong(polyline, cum, floorId, current.x, current.y);
  if (s === null) {
    return null;
  }
  const total = cum[cum.length - 1];
  return Math.max(0, total - s);
}

function polylineCumulative(polyline: readonly GeoPosition[]): number[] {
  const cum = [0];
  for (let i = 1; i < polyline.length; i += 1) {
    const a = polyline[i - 1];
    const b = polyline[i];
    cum.push(cum[i - 1] + Math.hypot(b.x - a.x, b.y - a.y));
  }
  return cum;
}

/** 投影到同层折线，返回最近线段的沿线里程（过拐点连续，不回弹）。 */
function projectAlong(
  polyline: readonly GeoPosition[],
  cum: readonly number[],
  floorId: string,
  x: number,
  y: number,
): number | null {
  let best: number | null = null;
  let bestDist = Infinity;
  for (let i = 0; i < polyline.length - 1; i += 1) {
    const a = polyline[i];
    const b = polyline[i + 1];
    if ((a.floorId ?? floorId) !== floorId || (b.floorId ?? floorId) !== floorId) {
      continue;
    }
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    const t = len2 <= 1e-12 ? 0 : Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / len2));
    const px = a.x + t * dx;
    const py = a.y + t * dy;
    const dist = Math.hypot(x - px, y - py);
    if (dist < bestDist) {
      bestDist = dist;
      best = cum[i] + t * Math.sqrt(len2);
    }
  }
  return best;
}

/** 拐点在折线上的沿线里程：取当前里程之后的第一个同坐标顶点。 */
function turnCumulative(
  polyline: readonly GeoPosition[],
  cum: readonly number[],
  floorId: string,
  x: number,
  y: number,
  fromM: number,
): number | null {
  let fallback: number | null = null;
  for (let i = 0; i < polyline.length; i += 1) {
    const p = polyline[i];
    if ((p.floorId ?? floorId) !== floorId) {
      continue;
    }
    if (Math.hypot(p.x - x, p.y - y) > 1e-6) {
      continue;
    }
    if (cum[i] >= fromM - 1e-6) {
      return cum[i];
    }
    fallback = cum[i]; // 只在已经走过一遍（绕圈）时才用到
  }
  return fallback;
}
