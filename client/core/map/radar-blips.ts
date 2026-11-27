/**
 * 扫描信标的雷达波纹数据（纯逻辑，便于单测）。
 *
 * 定位管线每来一帧原始样本就更新该信标的最近 RSSI/时间；页面按固定帧率
 * 把样本换算成“扩散相位 + 衰减”交给 canvas 绘制。超过保持时长没有新样本
 * 的信标自然消散，不复用上一段信号的视觉残留。
 */

/** 一帧扫描样本的最近状态。 */
export interface ScannedBeaconState {
  rssi: number;
  /** 样本到达时刻（毫秒，Date.now）。 */
  atMs: number;
}

/** 雷达波纹绘制结果。 */
export interface RadarBlip {
  beaconId: string;
  /** 当前扩散相位 0~1（0 = 刚从信标点冒出）。 */
  phase: number;
  /** 整体衰减 1~0（1 = 刚扫到，0 = 即将消失）。 */
  fade: number;
  /** 信号强度 0~1（由 RSSI 映射，用于点径与配色）。 */
  strength: number;
}

/** 无新样本后的保持时长：4s 覆盖两个扫描窗口，避免闪烁。 */
export const RADAR_HOLD_MS = 4000;
/** 单圈波纹扩散周期。 */
export const RADAR_CYCLE_MS = 1600;
/** RSSI 映射区间：-95dBm → 0，-50dBm → 1。 */
const RSSI_WEAK = -95;
const RSSI_STRONG = -50;

function clamp01(value: number): number {
  if (value < 0) {
    return 0;
  }
  return value > 1 ? 1 : value;
}

/** RSSI → 0~1 强度（越强越接近 1）。 */
export function radarStrength(rssi: number): number {
  return clamp01((rssi - RSSI_WEAK) / (RSSI_STRONG - RSSI_WEAK));
}

/**
 * 把扫描状态换算成当前时刻的雷达波纹。
 * 已超时的信标不返回（调用方可据此清理存储）。
 */
export function radarBlips(
  scanned: ReadonlyMap<string, ScannedBeaconState>,
  nowMs: number,
): RadarBlip[] {
  const blips: RadarBlip[] = [];
  for (const [beaconId, state] of scanned) {
    const age = nowMs - state.atMs;
    if (age < 0 || age > RADAR_HOLD_MS) {
      continue;
    }
    blips.push({
      beaconId,
      phase: (age % RADAR_CYCLE_MS) / RADAR_CYCLE_MS,
      fade: 1 - age / RADAR_HOLD_MS,
      strength: radarStrength(state.rssi),
    });
  }
  return blips;
}

/** 剔除超过保持时长的样本（原地删除，返回是否有变化）。 */
export function pruneScannedBeacons(
  scanned: Map<string, ScannedBeaconState>,
  nowMs: number,
): boolean {
  let changed = false;
  for (const [beaconId, state] of scanned) {
    if (nowMs - state.atMs > RADAR_HOLD_MS) {
      scanned.delete(beaconId);
      changed = true;
    }
  }
  return changed;
}
