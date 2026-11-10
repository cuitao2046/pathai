/**
 * 信标源抽象：live 实扫（LiveBeaconScanner）与 mock 合成（MockBeaconSource）
 * 同构接入定位管线，聚合/节流路径完全一致。
 */
import type { BeaconSample } from './scan-window';

/** live 扫描阶段（页面据此显示真实 BLE 状态，不再让界面“看起来正常”）。 */
export type BleScanPhase = 'idle' | 'opening' | 'scanning' | 'error';

export interface BleScanStatus {
  phase: BleScanPhase;
  /** 面向现场的说明；phase=error 时为失败原因（含 errCode/errMsg）。 */
  message: string;
  /** 本轮发现的 iBeacon 设备数（按 uuid/major/minor 去重）。 */
  scannedDeviceCount: number;
  /** 本轮收到的 iBeacon 上报条数（仅供日志/诊断，不上界面）。 */
  rawSamples: number;
  /** 本轮解析出的不同 (uuid, major, minor) 信标数（含 major/minor 未命中台账的，仅供日志）。 */
  deviceCount: number;
  /** 本轮按 uuid+major+minor 命中台账的去重信标数（界面口径）。 */
  matchedCount: number;
  /** 命中台账的信标里 RSSI 最强的那枚（界面可选展示）。 */
  strongestBeaconId: string | null;
  strongestRssi: number | null;
}

export interface BeaconSourceLike {
  start(handler: (sample: BeaconSample) => void): Promise<void>;
  stop(): void;
  /**
   * 订阅 live 扫描状态（mock 源不实现；页面按数据源显示模拟提示）。
   * 返回取消订阅函数。
   */
  subscribeStatus?(listener: (status: BleScanStatus) => void): () => void;
}
