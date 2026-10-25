/**
 * 地图数据文件缓存（wx FileSystemManager，用户目录）。
 *
 * 为什么不用 storage：wx.setStorageSync 单 key 上限 1MB，4.6MB 真实地图包
 * 写入必抛异常（被 kv 层降级为进程内存，冷启动即丢失）→ 每次启动重复下载。
 * 用户目录文件无 1MB 限制。
 *
 * 一致性约定：写入方（map-service）保证 raw 已通过 sha256Async 与服务端
 * dataSha256 比对后才写入；读取方以 JSON.parse 失败兜底（写入中断的半截
 * 文件视为无缓存，触发重新下载）。
 */

/** 缓存文件路径（用户数据根目录，按站点隔离）。 */
function cachePath(siteId: string): string {
  return `${wx.env.USER_DATA_PATH}/map-cache-${siteId}.json`;
}

/** 读取缓存；文件不存在/读取失败返回 null。 */
export function readMapCache(siteId: string): Promise<string | null> {
  return new Promise((resolve) => {
    try {
      wx.getFileSystemManager().readFile({
        filePath: cachePath(siteId),
        encoding: 'utf8',
        success: (res) => resolve(typeof res.data === 'string' && res.data.length > 0 ? res.data : null),
        fail: () => resolve(null),
      });
    } catch {
      resolve(null); // 环境不支持文件系统（如测试桩缺省）视为无缓存
    }
  });
}

/** 覆盖写入缓存；失败抛错交调用方记录（下次启动重试）。 */
export function writeMapCache(siteId: string, raw: string): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      wx.getFileSystemManager().writeFile({
        filePath: cachePath(siteId),
        encoding: 'utf8',
        data: raw,
        success: () => resolve(),
        fail: (err) => reject(new Error(err.errMsg ?? '地图缓存写入失败')),
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error('地图缓存写入失败'));
    }
  });
}
