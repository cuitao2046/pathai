/**
 * 版本列表 / 回滚确认的文案：入库记录数（recordCount）与点位向量数（fpCount）
 * 是两个口径——服务端每条记录一条向量聚合前的记录数 vs 按 fpId 聚合后的向量数，
 * 现场必须同时看到，不能只显示 recordCount。
 *
 * 明细被保留策略裁剪（detailsPruned）的旧版本只留摘要行，点位数可能为 0 或不可用，
 * 此时明确写「明细已裁剪」，不再展示点位数字。
 */
import type { FingerprintLibraryVersionSummary } from '../../typings/api';

/** 版本号里的 14 位时间戳：fpl-20261110115454835010-33f40c → 20261110115454。 */
export function versionTimeLabel(version: string): string {
  return version.slice(4, 18);
}

/** 双口径数字：`595 条记录 / 594 个点位`；已裁剪版本不显示点位数字。 */
export function versionCountsText(item: FingerprintLibraryVersionSummary): string {
  if (item.detailsPruned) {
    return `${item.recordCount} 条记录 · 明细已裁剪`;
  }
  return `${item.recordCount} 条记录 / ${item.fpCount} 个点位`;
}

/** 版本列表单行文案；当前 active 版本追加「当前」。 */
export function versionListLabel(item: FingerprintLibraryVersionSummary): string {
  return `${versionTimeLabel(item.version)} · ${versionCountsText(item)}${
    item.active ? ' · 当前' : ''
  }`;
}

/** 回滚确认正文（列表里已裁剪的版本不可选，这里仅兜底）。 */
export function versionRollbackConfirmText(item: FingerprintLibraryVersionSummary): string {
  if (item.detailsPruned) {
    return `版本 ${item.version} 的明细已被保留策略裁剪（${item.recordCount} 条记录），无法回滚。`;
  }
  return `回滚到 ${item.version}（${item.recordCount} 条记录 / ${item.fpCount} 个点位）？\n当前 active 指针会切回该版本，历史版本不被改写。`;
}
