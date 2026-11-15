/**
 * 采集页导出样本的文件名规则，计算控制台只认这里定义的两种导出：
 * - 静态标定：static_samples_<13 位毫秒时间戳>.json
 * - 对数等距：logdist_<信标 ID>_<13 位毫秒时间戳>.json
 *   信标 ID 中的非 [A-Za-z0-9_-] 字符在导出时已替换为下划线。
 */
const STATIC_EXPORT_PATTERN = /^static_samples_\d{13}\.json$/i;
const LOGDIST_EXPORT_PATTERN = /^logdist_[A-Za-z0-9_-]+_\d{13}\.json$/i;
const TIMESTAMP_PATTERN = /_(\d{13})\.json$/i;

export function isSampleFileName(name: string): boolean {
  return STATIC_EXPORT_PATTERN.test(name) || LOGDIST_EXPORT_PATTERN.test(name);
}

/** 文件名末尾的 13 位毫秒时间戳；解析不到时返回 null。 */
export function parseExportTimestamp(name: string): number | null {
  const matched = TIMESTAMP_PATTERN.exec(name);
  if (matched === null) {
    return null;
  }
  const ts = Number(matched[1]);
  return Number.isFinite(ts) && ts > 0 ? ts : null;
}

export function sampleKindText(name: string): string {
  return STATIC_EXPORT_PATTERN.test(name) ? '静态标定' : '对数等距';
}
