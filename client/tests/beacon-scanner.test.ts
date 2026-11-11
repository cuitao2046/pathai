/**
 * LiveBeaconScanner / SharedLiveBeaconSource 单测（评审第二批补测）。
 * 覆盖：启动链路、帧过滤（iOS 消失帧/非法帧）、台账命中与样本分发、
 * 多 handler 共享与最后一个释放才停止、启动失败上抛、stop 后竞态隔离。
 * BLE 方法为测试内本地桩（setup-wx 的全局 wx 不含蓝牙面）。
 */
import { LiveBeaconScanner } from '../core/ble/beacon-scanner';
import { SharedLiveBeaconSource } from '../core/ble/beacon-source-factory';
import type { BeaconSample } from '../core/ble/scan-window';
import { wxStub, type WxStub } from './setup-wx';

interface BeaconFrame {
  uuid: string;
  major: number;
  minor: number;
  rssi: number;
  proximity?: number;
  accuracy?: number;
}

interface BleStubState {
  openErr: { errCode: number; errMsg: string } | null;
  adapterAvailable: boolean;
  discoveryErr: { errCode: number; errMsg: string } | null;
  beaconListener: ((res: { beacons: BeaconFrame[] }) => void) | null;
  closeCount: number;
  discoveryUuids: string[][] | null;
}

const ble: BleStubState = {
  openErr: null,
  adapterAvailable: true,
  discoveryErr: null,
  beaconListener: null,
  closeCount: 0,
  discoveryUuids: null,
};

// 给全局 wx 桩扩展蓝牙面（每个测试文件天然隔离）
Object.assign(wxStub as unknown as Record<string, unknown>, {
  openBluetoothAdapter(option: { success(): void; fail(err: { errCode: number; errMsg: string }): void }) {
    if (ble.openErr !== null) {
      option.fail(ble.openErr);
      return;
    }
    option.success();
  },
  getBluetoothAdapterState(option: {
    success(res: { available: boolean; discovering: boolean }): void;
    fail(err: { errMsg: string }): void;
  }) {
    option.success({ available: ble.adapterAvailable, discovering: false });
  },
  onBeaconUpdate(listener: (res: { beacons: BeaconFrame[] }) => void) {
    ble.beaconListener = listener;
  },
  offBeaconUpdate() {
    ble.beaconListener = null;
  },
  onBluetoothAdapterStateChange() {
    // 用例不覆盖蓝牙中途关闭
  },
  offBluetoothAdapterStateChange() {
    // 无状态需要清理
  },
  startBeaconDiscovery(option: {
    uuids: string[];
    success(): void;
    fail(err: { errCode: number; errMsg: string }): void;
  }) {
    if (ble.discoveryUuids === null) {
      ble.discoveryUuids = [];
    }
    ble.discoveryUuids.push(option.uuids);
    if (ble.discoveryErr !== null) {
      option.fail(ble.discoveryErr);
      return;
    }
    option.success();
  },
  stopBeaconDiscovery(option: Record<string, never>) {
    void option;
  },
  closeBluetoothAdapter(option: { fail(): void }) {
    ble.closeCount += 1;
    option.fail();
  },
});

const UUIDS = ['fda50693-a4e2-4fb1-afcf-c6eb07647825'];
const RESOLVER = (uuid: string, major: number, minor: number): string | null => {
  if (uuid.toUpperCase() === UUIDS[0].toUpperCase() && major === 1 && minor === 5) {
    return 'BK-01-005';
  }
  return null;
};

function makeScanner(): LiveBeaconScanner {
  return new LiveBeaconScanner(UUIDS, RESOLVER);
}

function emitFrames(frames: BeaconFrame[]): void {
  ble.beaconListener?.({ beacons: frames });
}

async function startedScanner(): Promise<LiveBeaconScanner> {
  const scanner = makeScanner();
  await scanner.start(() => undefined);
  return scanner;
}

describe('LiveBeaconScanner', () => {
  beforeEach(() => {
    ble.openErr = null;
    ble.adapterAvailable = true;
    ble.discoveryErr = null;
    ble.beaconListener = null;
    ble.closeCount = 0;
    ble.discoveryUuids = null;
  });

  test('启动链路：uuid 白名单下发，状态推进到 scanning', async () => {
    const statuses: string[] = [];
    const scanner = makeScanner();
    scanner.subscribeStatus((s) => statuses.push(s.phase));
    await scanner.start(() => undefined);
    expect(ble.discoveryUuids).toEqual([UUIDS]);
    expect(statuses).toEqual(['idle', 'opening', 'scanning']);
    scanner.stop(); // 清理 8s 诊断计时器
  });

  test('帧解析：iOS 消失帧与非法帧丢弃，命中台账进样本', async () => {
    const scanner = await startedScanner();
    const samples: BeaconSample[] = [];
    scanner.start((s) => samples.push(s)); // running=true，直接挂 handler
    emitFrames([
      { uuid: UUIDS[0], major: 1, minor: 5, rssi: -60 }, // 命中
      { uuid: UUIDS[0], major: 1, minor: 5, rssi: 0, proximity: 0, accuracy: -1 }, // iOS 消失帧
      { uuid: UUIDS[0], major: 1, minor: 5, rssi: Number.NaN }, // 非法 rssi
      { uuid: UUIDS[0], major: Number.NaN, minor: 5, rssi: -70 }, // 非法 major
      { uuid: '', major: 1, minor: 5, rssi: -70 }, // 空 uuid
      { uuid: UUIDS[0], major: 9, minor: 9, rssi: -80 }, // 未命中台账
    ]);
    expect(samples).toHaveLength(2); // 命中帧 + 未命中但合法的帧；消失帧/非法帧全丢
    expect(samples[0]).toMatchObject({ major: 1, minor: 5, rssi: -60 });
    expect(samples[1]).toMatchObject({ major: 9, minor: 9, rssi: -80 });
    const status = { phase: '', matchedCount: 0 };
    scanner.subscribeStatus((s) => {
      status.phase = s.phase;
      status.matchedCount = s.matchedCount;
    });
    expect(status.matchedCount).toBe(1);
    scanner.stop();
  });

  test('多 handler 共享：release 一个不停扫，最后一个释放才关适配器', async () => {
    const scanner = makeScanner();
    const h1 = () => undefined;
    const h2 = () => undefined;
    await scanner.start(h1);
    await scanner.start(h2);
    scanner.release(h1);
    expect(ble.closeCount).toBe(0); // 仍有 handler，扫描继续
    scanner.release(h2);
    expect(ble.closeCount).toBe(1); // 最后一个释放 → 硬停止
  });

  test('启动失败（权限拒绝）：error 状态 + start reject，文案带授权提示', async () => {
    ble.openErr = { errCode: 10001, errMsg: 'openBluetoothAdapter:fail auth deny' };
    const scanner = makeScanner();
    let errorMessage = '';
    scanner.subscribeStatus((s) => {
      if (s.phase === 'error') {
        errorMessage = s.message;
      }
    });
    await expect(scanner.start(() => undefined)).rejects.toThrow('openBluetoothAdapter 失败');
    expect(errorMessage).toContain('未授权蓝牙权限');
    expect(ble.closeCount).toBe(0); // 适配器未打开，不重复关闭
  });

  test('stop 后竞态隔离：迟到帧不再分发', async () => {
    const scanner = await startedScanner();
    const samples: BeaconSample[] = [];
    scanner.start((s) => samples.push(s));
    scanner.stop();
    emitFrames([{ uuid: UUIDS[0], major: 1, minor: 5, rssi: -60 }]);
    expect(samples).toHaveLength(0); // 监听已摘除，迟到的 onBeaconUpdate 不生效
    expect(ble.beaconListener).toBeNull();
  });

  test('蓝牙不可用：适配器状态 available=false 时启动失败', async () => {
    ble.adapterAvailable = false;
    const scanner = makeScanner();
    await expect(scanner.start(() => undefined)).rejects.toThrow('蓝牙适配器不可用');
  });
});

describe('SharedLiveBeaconSource 门面', () => {
  beforeEach(() => {
    ble.openErr = null;
    ble.adapterAvailable = true;
    ble.discoveryErr = null;
    ble.beaconListener = null;
    ble.closeCount = 0;
    ble.discoveryUuids = null;
  });

  test('start/stop 只增删自己的 handler，stop 不影响底层数据', async () => {
    const scanner = makeScanner();
    const source = new SharedLiveBeaconSource(scanner);
    const samples: BeaconSample[] = [];
    await source.start((s) => samples.push(s));
    emitFrames([{ uuid: UUIDS[0], major: 1, minor: 5, rssi: -60 }]);
    expect(samples).toHaveLength(1);
    source.stop();
    expect(ble.closeCount).toBe(1); // 门面 stop 即释放唯一 handler → 底层硬停止
    emitFrames([{ uuid: UUIDS[0], major: 1, minor: 5, rssi: -60 }]);
    expect(samples).toHaveLength(1); // stop 后帧不再进入
  });
});
