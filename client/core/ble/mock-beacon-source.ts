/**
 * Mock 信标源：沿给定点列轨迹匀速行走，按对数路径损耗模型
 * rssi = txPower − 10·n·log10(d)（n=2，与后端解算互逆）合成观测。
 * 只注入地图包已登记三元组；走与 live 完全相同的聚合/节流路径。
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
  floorId: string;
  /** 行走点列（地图坐标系，米）；走完驻留终点。 */
  path: Array<{ x: number; y: number }>;
  /** 采样周期（毫秒），默认 500。 */
  tickMs?: number;
  /** 行走速度（米/秒），默认 1.0。 */
  speedMps?: number;
}

interface WalkPoint {
  x: number;
  y: number;
}

export class MockBeaconSource implements BeaconSourceLike {
  private readonly emitBeacons: MapBeaconEntry[];
  private readonly path: WalkPoint[];
  private readonly tickMs: number;
  private readonly stepM: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private handler: ((sample: BeaconSample) => void) | null = null;
  private segment = 0;
  private segmentDone = 0;
  private walked = false;

  constructor(opts: MockWalkOptions) {
    // 只保留同层且三元组齐全的信标（未登记三元组注册表也不解析）
    this.emitBeacons = opts.beacons.filter(
      (b) =>
        b.floorId === opts.floorId &&
        typeof b.uuid === 'string' &&
        typeof b.major === 'number' &&
        typeof b.minor === 'number',
    );
    if (opts.path.length === 0) {
      throw new Error('MockBeaconSource 需要非空轨迹');
    }
    this.path = opts.path;
    this.tickMs = opts.tickMs ?? 500;
    this.stepM = ((opts.speedMps ?? 1.0) * this.tickMs) / 1000;
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
    const at = nowMs();
    for (const beacon of this.emitBeacons) {
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

  /** 沿点列推进一个 tick 的距离；返回当前位置。 */
  private advance(): WalkPoint {
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
    };
  }
}
