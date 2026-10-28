/**
 * 地图数据文件缓存（wx FileSystemManager，用户目录）。
 *
 * 为什么不用 storage：wx.setStorageSync 单 key 上限 1MB，4.6MB 真实地图包
 * 写入必抛异常（被 kv 层降级为进程内存，冷启动即丢失）→ 每次启动重复下载。
 * 用户目录文件无 1MB 限制。
 *
 * 断点续传（REQ-MAP）：
 * - 下载中的字节写入半成品 `map-partial-<site>-<version>.json`（二进制追加），
 *   半成品按站点 + 目标版本隔离，目标版本变化即丢弃，不跨版本续传；
 * - 只有完整字节通过 SHA-256 校验后才转正为 `map-cache-<site>.json`：
 *   先写 staging 文件、删除旧缓存在前、rename 在后，尽量避免半截文件顶替正式缓存；
 * - 读取方以 JSON.parse 失败兜底（写入中断的半截文件视为无缓存，触发重新下载）。
 */

/** 正式缓存文件路径（用户数据根目录，按站点隔离）。 */
function cachePath(siteId: string): string {
  return `${wx.env.USER_DATA_PATH}/map-cache-${siteId}.json`;
}

/** 转正中的暂存文件路径（校验通过后 rename 覆盖正式缓存）。 */
function stagingPath(siteId: string): string {
  return `${wx.env.USER_DATA_PATH}/map-next-${siteId}.json`;
}

/** 半成品文件路径（按站点 + 目标版本隔离，支持断点续传）。 */
export function partialPath(siteId: string, mapVersion: string): string {
  return `${wx.env.USER_DATA_PATH}/map-partial-${siteId}-${mapVersion}.json`;
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

/** 校验通过后转正缓存：staging 写入 → 删旧 → rename（失败抛错交调用方记录）。 */
export function commitMapCache(siteId: string, raw: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager();
    const staging = stagingPath(siteId);
    fs.writeFile({
      filePath: staging,
      encoding: 'utf8',
      data: raw,
      success: () => {
        // 目标已存在时部分平台 rename 会失败：先删旧缓存再 rename
        fs.unlink({
          filePath: cachePath(siteId),
          success: () => rename(),
          fail: () => rename(),
        });
        function rename(): void {
          fs.rename({
            oldPath: staging,
            newPath: cachePath(siteId),
            success: () => resolve(),
            fail: (err) => reject(new Error(err.errMsg ?? '地图缓存转正失败')),
          });
        }
      },
      fail: (err) => reject(new Error(err.errMsg ?? '地图缓存写入失败')),
    });
  });
}

/** 半成品当前字节数；不存在返回 0。 */
export function readMapPartialSize(siteId: string, mapVersion: string): Promise<number> {
  return new Promise((resolve) => {
    try {
      wx.getFileSystemManager().stat({
        path: partialPath(siteId, mapVersion),
        success: (res) => {
          const stats = res.stats as unknown as { size?: number };
          resolve(stats.size ?? 0);
        },
        fail: () => resolve(0),
      });
    } catch {
      resolve(0);
    }
  });
}

/** 追加一段下载字节到半成品（二进制，不做 UTF-8 解码，避免分片切断多字节字符）。 */
export function appendMapPartial(
  siteId: string,
  mapVersion: string,
  chunk: ArrayBuffer,
): Promise<void> {
  return new Promise((resolve, reject) => {
    try {
      wx.getFileSystemManager().appendFile({
        filePath: partialPath(siteId, mapVersion),
        data: chunk,
        success: () => resolve(),
        fail: (err) => reject(new Error(err.errMsg ?? '地图半成品写入失败')),
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error('地图半成品写入失败'));
    }
  });
}

/** 读取半成品全部字节（校验完整文件 sha256 用）；不存在返回 null。 */
export function readMapPartialBytes(
  siteId: string,
  mapVersion: string,
): Promise<Uint8Array | null> {
  return new Promise((resolve) => {
    try {
      wx.getFileSystemManager().readFile({
        filePath: partialPath(siteId, mapVersion),
        success: (res) =>
          resolve(res.data instanceof ArrayBuffer ? new Uint8Array(res.data) : null),
        fail: () => resolve(null),
      });
    } catch {
      resolve(null);
    }
  });
}

/** 读取半成品为 UTF-8 文本（完整后转正缓存用）；不存在返回 null。 */
export function readMapPartialText(
  siteId: string,
  mapVersion: string,
): Promise<string | null> {
  return new Promise((resolve) => {
    try {
      wx.getFileSystemManager().readFile({
        filePath: partialPath(siteId, mapVersion),
        encoding: 'utf8',
        success: (res) => resolve(typeof res.data === 'string' ? res.data : null),
        fail: () => resolve(null),
      });
    } catch {
      resolve(null);
    }
  });
}

/** 删除半成品（版本变化、校验失败、转正完成后清理）；失败静默。 */
export function removeMapPartial(siteId: string, mapVersion: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      wx.getFileSystemManager().unlink({
        filePath: partialPath(siteId, mapVersion),
        success: () => resolve(),
        fail: () => resolve(),
      });
    } catch {
      resolve();
    }
  });
}
