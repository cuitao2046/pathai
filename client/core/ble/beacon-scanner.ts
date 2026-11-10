/**
 * wx iBeacon 实扫封装（真机专用）：
 * openBluetoothAdapter → onBeaconUpdate → startBeaconDiscovery({ uuids })。
 *
 * 为什么不用 startBluetoothDevicesDiscovery + advertisData：
 * iOS 上 onBluetoothDeviceFound 对 Apple 0x004C 的 iBeacon 帧 advertisData 为空
 * （真机日志：25 个 BLE 设备、0 帧可解析 iBeacon），自己解析广播在 iOS 上拿不到样本；
 * 而 startBeaconDiscovery 走系统 iBeacon 服务（iOS 为 CoreLocation），
 * 现场已用对数等距页验证能拿到 BK-01-005 的 uuid/major/minor/rssi，安卓同样可用。
 *
 * 生命周期：
 * - 支持多个 handler 共享同一次物理扫描：第一个 handler 启动，最后一个释放才停止；
 * - 任何阶段失败都把 phase=error 上抛（不静默）；
 * - stop() 后允许重新 start()（适配器/监听器重新建立）；
 * - 启动后 8s 一次性诊断：0 帧 / 有帧但三元组对不上台账。
 *
 * 开发者工具不支持 BLE，联调用 MockBeaconSource。
 * 安卓需系统定位服务开关打开（微信搜索信标依赖位置权限）。
 */
import { nowMs } from '../../utils/clock';
import { createLogger } from '../../utils/logger';
import type { BeaconSourceLike, BleScanStatus } from './beacon-source';
import type { BeaconSample } from './scan-window';

const log = createLogger('ble');

type SampleHandler = (sample: BeaconSample) => void;
type StatusListener = (status: BleScanStatus) => void;
/** 三元组 → 台账 beaconId；未命中返回 null。由工厂注入，扫描器不依赖地图模块。 */
type BeaconResolver = (uuid: string, major: number, minor: number) => string | null;

/** 启动后多久没有 iBeacon 就做一次诊断提示（一次性计时，不是轮询）。 */
const NO_SIGNAL_HINT_MS = 8000;
/** 状态上屏节流：onBeaconUpdate 可能高频回调，最多 1s 推一次。 */
const REPORT_INTERVAL_MS = 1000;
/** 信标明细日志最多打印多少个，避免现场信标多时刷屏。 */
const MAX_BEACON_LOGS = 20;

interface AdapterState {
  available: boolean;
  discovering: boolean;
}

function describeWxError(err: unknown, fallback: string): string {
  if (typeof err === 'object' && err !== null) {
    const e = err as { errCode?: number; errMsg?: string };
    const code = e.errCode !== undefined ? `[${e.errCode}]` : '';
    const msg = e.errMsg ?? '';
    // 权限被拒是现场最常见的一类，单独给面向用户的说法（原始 errMsg 仍会带在日志里）
    if (/auth\s*deny|authorize/i.test(msg)) {
      return `${fallback}：未授权蓝牙权限，请在系统设置中允许微信使用蓝牙 ${code}${msg}`.trim();
    }
    if (code !== '' || msg !== '') {
      return `${fallback} ${code}${msg}`.trim();
    }
  }
  return fallback;
}

function openAdapter(): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.openBluetoothAdapter({
      success: () => resolve(),
      fail: (err) => reject(new Error(describeWxError(err, 'openBluetoothAdapter 失败'))),
    });
  });
}

/**
 * 启动 iBeacon 搜索：uuids 是系统级白名单（台账里登记的 UUID，已小写）。
 * ignoreBluetoothAvailable 仅 iOS 生效：iOS 11 起控制面板关蓝牙仍可用 Beacon 服务。
 */
function startBeaconDiscovery(uuids: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.startBeaconDiscovery({
      uuids,
      ignoreBluetoothAvailable: true,
      success: () => resolve(),
      fail: (err) => reject(new Error(describeWxError(err, 'startBeaconDiscovery 失败'))),
    });
  });
}

/** 读取适配器状态；接口失败返回 null（不据此误判蓝牙不可用）。 */
function readAdapterState(): Promise<AdapterState | null> {
  return new Promise((resolve) => {
    wx.getBluetoothAdapterState({
      success: (res) => resolve({ available: res.available, discovering: res.discovering }),
      fail: () => resolve(null),
    });
  });
}

export class LiveBeaconScanner implements BeaconSourceLike {
  private readonly uuids: string[];
  private readonly configuredUuids: Set<string>;
  /**
   * 多页面共享同一个物理扫描：每页注册自己的 handler，
   * 首个 handler 触发 start，最后一个 release 才真正停止。
   */
  private readonly handlers = new Set<SampleHandler>();
  private running = false;
  private starting: Promise<void> | null = null;
  /** 启动/停止竞态令牌：stop() 后旧的 start 流程不得再注册监听。 */
  private token = 0;
  private listeners = new Set<StatusListener>();
  private status: BleScanStatus = {
    phase: 'idle',
    message: '蓝牙未启动',
    scannedDeviceCount: 0,
    rawSamples: 0,
    deviceCount: 0,
    matchedCount: 0,
    strongestBeaconId: null,
    strongestRssi: null,
  };
  private readonly seenTriplets = new Set<string>();
  /** beaconId → 最近一次 RSSI（台账命中的去重集合，界面口径）。 */
  private readonly matchedRssi = new Map<string, number>();
  /** 命中台账的三元组键集合（仅用于日志里区分未命中帧）。 */
  private readonly matchedTriplets = new Set<string>();
  private readonly resolveBeacon: BeaconResolver | undefined;
  private rawSamples = 0;
  private lastStatusEmitMs = 0;
  private noSignalTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(uuids: string[], resolveBeacon?: BeaconResolver) {
    this.uuids = uuids.map((u) => u.trim().toLowerCase());
    this.configuredUuids = new Set(this.uuids);
    this.resolveBeacon = resolveBeacon;
  }

  subscribeStatus(listener: StatusListener): () => void {
    this.listeners.add(listener);
    listener(this.status);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async start(handler: SampleHandler): Promise<void> {
    this.handlers.add(handler);
    if (this.running) {
      return;
    }
    if (this.starting !== null) {
      return this.starting;
    }
    const startPromise = this.doStart();
    this.starting = startPromise;
    try {
      await startPromise;
    } finally {
      if (this.starting === startPromise) {
        this.starting = null;
      }
    }
  }

  /**
   * 释放一个 handler；最后一个释放时停止物理扫描。
   * 页面 onHide/onUnload 只应 release 自己，不能直接 stop 全局扫描。
   */
  release(handler: SampleHandler): void {
    this.handlers.delete(handler);
    if (this.handlers.size === 0 && (this.running || this.starting !== null)) {
      this.stop();
    }
  }

  /** 硬停止（工厂重建共享扫描器时使用）：清空所有 handler 并关闭适配器。 */
  stop(): void {
    this.token += 1;
    const wasActive = this.running || this.status.phase !== 'idle';
    this.running = false;
    this.handlers.clear();
    this.cleanupRuntime();
    try {
      wx.closeBluetoothAdapter({ fail: () => undefined });
    } catch {
      // 旧基础库/适配器未打开时忽略
    }
    this.resetCounters();
    if (wasActive) {
      log.info('iBeacon 扫描已停止');
    }
    this.emit('idle', '蓝牙未启动');
  }

  private async doStart(): Promise<void> {
    const token = ++this.token;
    this.resetCounters();
    this.emit('opening', '正在初始化蓝牙…');

    try {
      await openAdapter();
      if (token !== this.token) {
        return;
      }
      log.info('openBluetoothAdapter ok');

      const state = await readAdapterState();
      if (token !== this.token) {
        return;
      }
      if (state !== null) {
        log.info('适配器状态', `available=${state.available}`, `discovering=${state.discovering}`);
        if (!state.available) {
          throw new Error('蓝牙适配器不可用：请打开手机蓝牙后重试');
        }
      }

      this.bindAdapterStateChange();

      // 先挂监听再启动搜索，避免漏掉首批上报
      wx.onBeaconUpdate(this.onBeaconUpdate);

      await startBeaconDiscovery(this.uuids);
      if (token !== this.token) {
        this.cleanupRuntime();
        return;
      }

      this.running = true;
      log.info(
        'iBeacon 扫描已启动',
        `uuid 白名单 ${this.uuids.length} 个`,
        'onBeaconUpdate 上报（iOS 无需 advertisData）',
      );
      this.emit('scanning', 'iBeacon 扫描中');
      this.armNoSignalHint();
    } catch (err) {
      if (token !== this.token) {
        return;
      }
      this.running = false;
      this.cleanupRuntime();
      const message = err instanceof Error ? err.message : String(err);
      log.warn('iBeacon 扫描启动失败', message);
      this.emit('error', message);
      throw err instanceof Error ? err : new Error(message);
    }
  }

  private resetCounters(): void {
    this.rawSamples = 0;
    this.seenTriplets.clear();
    this.matchedRssi.clear();
    this.matchedTriplets.clear();
    this.lastStatusEmitMs = 0;
  }

  private bindAdapterStateChange(): void {
    const listener: WechatMiniprogram.OnBluetoothAdapterStateChangeCallback = (res) => {
      log.info('适配器状态变化', `available=${res.available}`, `discovering=${res.discovering}`);
      if (!this.running) {
        return;
      }
      if (!res.available) {
        this.emit('error', '蓝牙已关闭或不可用，请打开手机蓝牙后重试');
      }
    };
    wx.onBluetoothAdapterStateChange(listener);
  }

  private armNoSignalHint(): void {
    this.clearNoSignalHint();
    this.noSignalTimer = setTimeout(() => {
      this.noSignalTimer = null;
      if (!this.running) {
        return;
      }
      if (this.seenTriplets.size === 0) {
        log.warn(
          'iBeacon 扫描已启动但 8s 内 0 帧：',
          '请确认信标已通电；UUID 必须与台账一致（白名单已按台账下发）；安卓还需打开系统定位开关',
        );
        return;
      }
      const triples = [...this.seenTriplets].join(' ');
      if (this.matchedRssi.size === 0) {
        log.warn(
          '扫描 8s 诊断：收到 iBeacon 帧但 0 条命中台账',
          `三元组=[${triples}]`,
          '请核对 major/minor 与地图台账',
        );
        return;
      }
      log.info(
        '扫描 8s 诊断：命中台账',
        `信标=[${[...this.matchedRssi.keys()].join(' ')}]`,
        `累计帧数=${this.rawSamples}`,
        `未命中三元组=[${[...this.seenTriplets].filter((k) => !this.matchedTriplets.has(k)).join(' ') || '无'}]`,
      );
    }, NO_SIGNAL_HINT_MS);
  }

  private clearNoSignalHint(): void {
    if (this.noSignalTimer !== null) {
      clearTimeout(this.noSignalTimer);
      this.noSignalTimer = null;
    }
  }

  /** 移除监听并停搜索（不关适配器，关闭动作由 stop 统一做）。 */
  private cleanupRuntime(): void {
    this.clearNoSignalHint();
    try {
      wx.offBeaconUpdate();
    } catch {
      // 旧基础库无此方法
    }
    try {
      wx.offBluetoothAdapterStateChange();
    } catch {
      // 旧基础库无此方法
    }
    try {
      wx.stopBeaconDiscovery({});
    } catch {
      // 未启动时停止会失败，忽略
    }
  }

  private emit(phase: BleScanStatus['phase'], message: string): void {
    let strongestBeaconId: string | null = null;
    let strongestRssi: number | null = null;
    for (const [beaconId, rssi] of this.matchedRssi) {
      if (strongestRssi === null || rssi > strongestRssi) {
        strongestBeaconId = beaconId;
        strongestRssi = rssi;
      }
    }
    this.status = {
      phase,
      message,
      scannedDeviceCount: this.seenTriplets.size,
      rawSamples: this.rawSamples,
      deviceCount: this.seenTriplets.size,
      matchedCount: this.matchedRssi.size,
      strongestBeaconId,
      strongestRssi,
    };
    for (const listener of this.listeners) {
      listener(this.status);
    }
  }

  private onBeaconUpdate: WechatMiniprogram.OnBeaconUpdateCallback = (res) => {
    if (this.handlers.size === 0) {
      return;
    }
    const at = nowMs();
    let frames = 0;
    let addedTriplet = false;

    for (const beacon of res.beacons) {
      if (typeof beacon.uuid !== 'string' || beacon.uuid.trim() === '') {
        continue;
      }
      // iOS 在信号消失时会回一条 proximity=0 / accuracy=-1 / rssi=0 的记录，不能当样本；
      // 只按这三个条件同时成立来判定，避免把某些 iOS 版本正常的 rssi=0 误丢。
      if (beacon.proximity === 0 && beacon.accuracy === -1 && beacon.rssi === 0) {
        continue;
      }
      if (!Number.isFinite(beacon.rssi)) {
        continue;
      }
      if (!Number.isFinite(beacon.major) || !Number.isFinite(beacon.minor)) {
        continue;
      }
      const uuid = beacon.uuid.trim();
      const key = `${uuid.toLowerCase()}|${beacon.major}|${beacon.minor}`;
      frames += 1;
      this.rawSamples += 1;
      const isNewTriplet = !this.seenTriplets.has(key);
      if (isNewTriplet) {
        this.seenTriplets.add(key);
        addedTriplet = true;
      }
      // 命中口径唯一：uuid+major+minor 全部与台账一致；未命中只进日志，不上界面
      const beaconId = this.resolveBeacon?.(uuid, beacon.major, beacon.minor) ?? null;
      if (beaconId !== null) {
        this.matchedRssi.set(beaconId, beacon.rssi);
        this.matchedTriplets.add(key);
      }
      if (isNewTriplet && this.seenTriplets.size <= MAX_BEACON_LOGS) {
        if (beaconId !== null) {
          log.info('iBeacon 广播命中台账', key, `beaconId=${beaconId}`, `RSSI=${beacon.rssi}`);
        } else {
          log.warn(
            'iBeacon 广播未命中台账',
            key,
            `RSSI=${beacon.rssi}`,
            this.configuredUuids.has(uuid.toLowerCase())
              ? 'UUID 在台账内，major/minor 不匹配'
              : 'UUID 不在台账内',
          );
        }
      }
      const sample: BeaconSample = {
        uuid,
        major: beacon.major,
        minor: beacon.minor,
        rssi: beacon.rssi,
        observedAtMs: at,
      };
      for (const handler of this.handlers) {
        handler(sample);
      }
    }

    if (frames > 0 || addedTriplet || at - this.lastStatusEmitMs >= REPORT_INTERVAL_MS) {
      this.lastStatusEmitMs = at;
      this.emit('scanning', 'iBeacon 扫描中');
    }
  };
}
