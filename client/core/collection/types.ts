/**
 * 现场采集（静态标定 + 网格指纹）共享类型。
 * 导出契约与独立工具严格一致：
 * - 静态标定 = positioning-calibrator utils/cstore.js 的 static_samples.json（schemaVersion 1.0）
 * - 网格指纹 = fingerprint-collector utils/fpstore.js 的 ble_fingerprint_collection（schemaVersion 1.2.0）
 * 新增字段必须为可选（PC 侧工具链忽略未知字段），禁止改名/改语义。
 */
import type { ResolvedObservation } from '../ble/scan-window';

/** 静态标定点（预置或现场补标）。 */
export interface CalibrationPointRec {
  pointId: string;
  floorId: string;
  /** 真值坐标（地图局部米制）。 */
  truth: [number, number];
  /** 场景类别（预置点为生成脚本的 scene；补标点为 'custom'）。 */
  scene: string;
  label: string;
  custom: boolean;
  /** 预置点可隐藏（不删除）；自定义点直接删除。 */
  hidden?: boolean;
}

/** 单信标采样统计（static_samples.json observations 契约）。 */
export interface StaticObservation {
  beaconId: string;
  n: number;
  rssiMedian: number;
  rssiMin: number;
  rssiMax: number;
  /** 总体标准差，保留 1 位小数。 */
  rssiStd: number;
  /** 真值点与信标的三维斜距（spec 0.3.3 口径），保留 2 位小数。 */
  truthDistanceM: number;
}

/** 一次倒计时采集 = 一条独立会话（同点多会话互不覆盖）。 */
export interface StaticSessionRec {
  pointId: string;
  sessionIndex: number;
  /** ISO 时刻。 */
  startedAt: string;
  durationS: number;
  /** 人流高峰窗口标记（默认 false；PC 侧分组用）。 */
  trafficWindow: boolean;
  observations: StaticObservation[];
  /** 现场定位数据：采集起止时的定位估计快照（可选，PC 侧忽略未知字段）。 */
  estimateAtStart?: EstimateSnapshot;
  estimateAtEnd?: EstimateSnapshot;
}

/** 定位估计快照（仅可信估计；不可信时缺省）。 */
export interface EstimateSnapshot {
  x: number;
  y: number;
  floorId: string;
  quality: string;
  confidence: number;
}

/** 采集信标观测（ble_fingerprint_collection records[].beacons[] 契约）。 */
export interface FpBeaconObs {
  uuid: string;
  major: number;
  minor: number;
  rssi: number;
  /** 1m 参考值（与后端解算同一 BEACON_RSSI_AT_1M_DBM）。 */
  txPower: number;
  /** 由路径损耗模型反推的距离（米），保留 2 位小数。 */
  accuracy: number;
}

/** 指纹快照记录（同一点可多条，captureIndex 递增）。 */
export interface FpRecord {
  fpId: string;
  floor: number;
  coordinates: [number, number];
  /** 分区功能不引入（spec D5），恒为 null。 */
  relCoordinates: null;
  zoneId: null;
  zoneName: null;
  anchor: null;
  regionType: string;
  captureIndex: number;
  /** ISO 时刻。 */
  collectedAt: string;
  beaconCount: number;
  beacons: FpBeaconObs[];
  /**
   * 采集时的网格/参数版本（spec calibration-console §7.5）。
   * 旧记录没有这两个字段：导出保持缺失，下游按 unknown 处理，不回填。
   */
  gridVersion?: string | null;
  paramsVersion?: string | null;
}

/** 快照输入：2s 窗口逐信标中位数（与生产同口径）。 */
export type SnapshotWindow = ResolvedObservation[];

/**
 * 删除墓碑（ble_fingerprint_collection 1.2.0 deletions[] 契约）。
 * 删除必须显式声明：只有本地真实删除的记录才生成墓碑，绝不按「本批未出现」推断。
 */
export interface FpDeletion {
  fpId: string;
  collectedAt: string;
  /** 删除时记录的内容哈希（服务端校验墓碑与库中记录一致，防止误删）。 */
  contentSha256: string;
  /** 楼层（对账清单驱动删除时由服务端清单回填；导出时可缺省）。 */
  floor?: string;
}
