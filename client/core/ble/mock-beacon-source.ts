/**
 * Mock 信标源：沿给定点列轨迹匀速行走，按对数路径损耗模型
 * rssi = txPower − 10·n·log10(d)（n=2，与后端解算互逆）合成观测。
 * 只注入地图包已登记三元组；走与 live 完全相同的聚合/节流路径。
 *
 * 模拟导航（类高德）：进入导航后由调用方 `updatePath(route.polyline)`
 * 把行走轨迹切换为规划路线（1.5m/s，见 core/map/mock-position.ts），
 * 信标按**路径点所在楼层**动态切换发射组——跨层（电梯段）自动换层采样，
 * 走完末点驻留（持续观测直到服务端判定到达）。
 */
import { nowMs } from '../../utils/clock';
import type { MapBeaconEntry } from '../../typings/api';
import type { BeaconSourceLike } from './beacon-source';
import type { BeaconSample } from './scan-window';

const PATH_LOSS_N = 2.0;
/** 距离下限（米）：避免 d 过小时 RSSI 反超 txPower 的非物理值。 */
const MIN_DISTANCE_M = 0.5;
const DEFAULT_TX_POWER_DBM = -59;

export interface MockWalkOptions {
  beacons: MapBeaconEntry[];
  /** 默认楼层：路径点未显式携带 floorId 时使用。 */
  floorId: string;
  /** 行走点列（地图坐标系，米；可带 floorId 表示跨层）；走完驻留终点。 */
  path: MockWalkPoint[];
  /** 采样周期（毫秒），默认 500。 */
  tickMs?: number;
  /** 行走速度（米/秒），默认 1.0；模拟导航传 1.5。 */
  speedMps?: number;
}

export interface MockWalkPoint {
  x: number;
  y: number;
  floorId?: string;
}

interface NormalizedPoint {
  x: number;
  y: number;
  floorId: string;
}

export class MockBeaconSource implements BeaconSourceLike {
  private readonly beaconsByFloor: Map<string, MapBeaconEntry[]>;
  private readonly tickMs: number;
  private readonly stepM: number;
  private path: NormalizedPoint[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private handler: ((sample: BeaconSample) => void) | null = null;
  private segment = 0;
  private segmentDone = 0;
  private walked = false;
  private lastPos: NormalizedPoint;

  constructor(opts: MockWalkOptions) {
    // 按楼层预分组（只保留三元组齐全的信标：未登记三元组注册表也不解析）
    this.beaconsByFloor = new Map();
    for (const beacon of opts.beacons) {
      if (
        typeof beacon.uuid !== 'string' ||
        typeof beacon.major !== 'number' ||
        typeof beacon.minor !== 'number'
      ) {
        continue;
      }
      const group = this.beaconsByFloor.get(beacon.floorId);
      if (group === undefined) {
        this.beaconsByFloor.set(beacon.floorId, [beacon]);
      } else {
        group.push(beacon);
      }
    }
    this.tickMs = opts.tickMs ?? 500;
    this.stepM = ((opts.speedMps ?? 1.0) * this.tickMs) / 1000;
    this.path = normalizePath(opts.path, opts.floorId);
    if (this.path.length === 0) {
      throw new Error('MockBeaconSource 需要非空轨迹');
    }
    this.lastPos = { ...this.path[0] };
  }

  /** 当前模拟位置（含楼层）；未开始行走时为路径首点。 */
  get currentPosition(): Readonly<NormalizedPoint> {
    return this.lastPos;
  }

  /**
   * 切换行走轨迹（模拟导航：onRoute 拿到规划路线后调用）。
   * 从新路径首点重新起步（调用方保证首点衔接当前估算位置）。
   */
  updatePath(points: MockWalkPoint[]): void {
    const normalized = normalizePath(points, this.path[0].floorId);
    if (normalized.length === 0) {
      throw new Error('MockBeaconSource.updatePath 需要非空轨迹');
    }
    this.path = normalized;
    this.segment = 0;
    this.segmentDone = 0;
    this.walked = false;
    this.lastPos = { ...normalized[0] };
  }

  start(handler: (sample: BeaconSample) => void): Promise<void> {
    this.handler = handler;
    const loop = (): void => {
      this.emitTick();
      this.timer = setTimeout(loop, this.tickMs);
    };
    this.timer = setTimeout(loop, this.tickMs);
    return Promise.resolve();
  }

  stop(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.handler = null;
  }

  private emitTick(): void {
    const handler = this.handler;
    if (handler === null) {
      return;
    }
    const pos = this.advance();
    this.lastPos = pos;
    const at = nowMs();
    // 按当前所在楼层发射（跨层后自动切换信标组）
    for (const beacon of this.beaconsByFloor.get(pos.floorId) ?? []) {
      const distance = Math.max(
        Math.hypot(beacon.x - pos.x, beacon.y - pos.y),
        MIN_DISTANCE_M,
      );
      const rssi = Math.round(
        (beacon.txPower ?? DEFAULT_TX_POWER_DBM) -
          10 * PATH_LOSS_N * Math.log10(distance),
      );
      handler({
        uuid: beacon.uuid as string,
        major: beacon.major as number,
        minor: beacon.minor as number,
        rssi,
        observedAtMs: at,
      });
    }
  }

  /** 沿点列推进一个 tick 的距离；返回当前位置（含楼层）。 */
  private advance(): NormalizedPoint {
    const last = this.path[this.path.length - 1];
    if (this.walked) {
      return last;
    }
    let remaining = this.stepM;
    while (remaining > 0 && this.segment < this.path.length - 1) {
      const from = this.path[this.segment];
      const to = this.path[this.segment + 1];
      const segLen = Math.hypot(to.x - from.x, to.y - from.y);
      const rest = segLen - this.segmentDone;
      if (remaining < rest) {
        this.segmentDone += remaining;
        remaining = 0;
      } else {
        remaining -= rest;
        this.segment += 1;
        this.segmentDone = 0;
      }
    }
    if (this.segment >= this.path.length - 1) {
      this.walked = true;
      return last;
    }
    const from = this.path[this.segment];
    const to = this.path[this.segment + 1];
    const segLen = Math.hypot(to.x - from.x, to.y - from.y);
    const ratio = segLen === 0 ? 0 : this.segmentDone / segLen;
    return {
      x: from.x + (to.x - from.x) * ratio,
      y: from.y + (to.y - from.y) * ratio,
      // 段内插值恒属起点侧楼层（跨层在段边界瞬移，模拟电梯换层）
      floorId: from.floorId,
    };
  }
}

function normalizePath(points: MockWalkPoint[], defaultFloorId: string): NormalizedPoint[] {
  return points.map((p) => ({ x: p.x, y: p.y, floorId: p.floorId ?? defaultFloorId }));
}
