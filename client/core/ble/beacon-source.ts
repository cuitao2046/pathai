/**
 * 信标源抽象：live 实扫（LiveBeaconScanner）与 mock 合成（MockBeaconSource）
 * 同构接入定位管线，聚合/节流路径完全一致。
 */
import type { BeaconSample } from './scan-window';

export interface BeaconSourceLike {
  start(handler: (sample: BeaconSample) => void): Promise<void>;
  stop(): void;
}
