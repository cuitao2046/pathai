/**
 * wx iBeacon 实扫封装（真机专用）：openBluetoothAdapter →
 * startBeaconDiscovery({uuids}) → onBeaconUpdate。
 * 开发者工具不支持 iBeacon，联调用 MockBeaconSource（C8 真机清单覆盖差异）。
 * 安卓需系统定位服务开关打开（无需 wx.getLocation 权限）。
 */
import { nowMs } from '../../utils/clock';
import { createLogger } from '../../utils/logger';
import type { BeaconSourceLike } from './beacon-source';
import type { BeaconSample } from './scan-window';

const log = createLogger('ble');

type SampleHandler = (sample: BeaconSample) => void;

function openAdapter(): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.openBluetoothAdapter({
      success: () => resolve(),
      fail: (err) => reject(new Error(err.errMsg ?? '蓝牙初始化失败')),
    });
  });
}

function startDiscovery(uuids: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    wx.startBeaconDiscovery({
      uuids,
      success: () => resolve(),
      fail: (err) => reject(new Error(err.errMsg ?? '启动信标扫描失败')),
    });
  });
}

export class LiveBeaconScanner implements BeaconSourceLike {
  private readonly uuids: string[];
  private handler: SampleHandler | null = null;
  private running = false;

  constructor(uuids: string[]) {
    this.uuids = uuids;
  }

  async start(handler: SampleHandler): Promise<void> {
    if (this.running) {
      return;
    }
    await openAdapter();
    await startDiscovery(this.uuids);
    this.handler = handler;
    wx.onBeaconUpdate(this.onUpdate);
    this.running = true;
    log.info('iBeacon 扫描已启动', `${this.uuids.length} uuid`);
  }

  stop(): void {
    if (!this.running) {
      return;
    }
    this.running = false;
    this.handler = null;
    wx.offBeaconUpdate();
    wx.stopBeaconDiscovery({});
    wx.closeBluetoothAdapter({});
    log.info('iBeacon 扫描已停止');
  }

  private onUpdate: WechatMiniprogram.OnBeaconUpdateCallback = (res) => {
    const handler = this.handler;
    if (handler === null) {
      return;
    }
    const at = nowMs();
    for (const beacon of res.beacons) {
      if (
        typeof beacon.uuid !== 'string' ||
        typeof beacon.major !== 'number' ||
        typeof beacon.minor !== 'number' ||
        typeof beacon.rssi !== 'number'
      ) {
        continue;
      }
      handler({
        uuid: beacon.uuid,
        major: beacon.major,
        minor: beacon.minor,
        rssi: beacon.rssi,
        observedAtMs: at,
      });
    }
  };
}
