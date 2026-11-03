/**
 * 采集页扫描/定位接线（页面级小件）：
 * - createTeeSource：把同一信标样本流分发给多个消费方
 *   （静态采样器吃原始样本、指纹页聚合器与定位管线各留一份）；
 * - estimateSnapshot：定位估计 → 采集记录快照（仅保留有坐标的估计）。
 */
import type { PositionEstimateResponse } from '../../typings/api';
import type { BeaconSample } from '../ble/scan-window';
import type { BeaconSourceLike } from '../ble/beacon-source';
import type { EstimateSnapshot } from './types';

export function createTeeSource(
  base: BeaconSourceLike,
  taps: Array<(sample: BeaconSample) => void>,
): BeaconSourceLike {
  return {
    async start(handler: (sample: BeaconSample) => void): Promise<void> {
      await base.start((sample) => {
        for (const tap of taps) {
          tap(sample);
        }
        handler(sample);
      });
    },
    stop(): void {
      base.stop();
    },
  };
}

/** 定位估计 → 快照（无坐标/未定位时返回 null，记录里缺省该字段）。 */
export function estimateSnapshot(estimate: PositionEstimateResponse | null): EstimateSnapshot | null {
  if (estimate === null || estimate.position == null) {
    return null;
  }
  return {
    x: estimate.position.x,
    y: estimate.position.y,
    floorId: estimate.position.floorId ?? '',
    quality: estimate.quality,
    confidence: estimate.confidence,
  };
}
