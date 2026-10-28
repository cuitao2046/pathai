/**
 * 地图服务：API-02 摘要（ETag/304 短路）+ 地图包分片下载校验 + 本地文件缓存。
 *
 * 规则（计划 C3 / FE-01，REQ-MAP 下载状态）：
 * - 摘要/ETag 持久化（storage 小对象）；数据本体走用户目录文件缓存
 *   （wx storage 单 key 上限 1MB，4.6MB 地图包放不下，写入必失败）；
 * - 启动：本地配对（缓存摘要 + 缓存数据）命中即秒开；随后拉服务端摘要：
 *   304 或 sha 一致 → 不重新拉取；sha 不同 → 后台静默下载覆盖本地缓存，
 *   本会话继续用已就绪版本（避免导航会话中途热换地图），下次启动生效；
 * - 首启（无可用缓存）→ 前台下载；下载按 1MiB 分片（HTTP Range）进行，
 *   已下载字节持久化，中断后下次启动从偏移续传，拼接完整并通过
 *   sha256Async 校验后才转正为正式缓存；
 * - 新旧版本日期差超过 MAP_STALE_DAYS 时标记 stale（语音播「地图过旧」，
 *   旧版仍可用）；
 * - 未加载时 require* 一律抛 MAP_UNAVAILABLE（禁止开始导航 FE-01）。
 */
import {
  API_BASE_URL,
  DEBUG_REWRITE_DATA_URL,
  MAP_DOWNLOAD_CHUNK_BYTES,
  MAP_STALE_DAYS,
} from '../../config/env';
import { nowMs } from '../../utils/clock';
import { sha256Async } from '../../utils/sha256';
import { createLogger } from '../../utils/logger';
import { getJson, getString, remove, setJson, setString } from '../../utils/kv';
import {
  appendMapPartial,
  commitMapCache,
  readMapCache,
  readMapPartialSize,
  readMapPartialText,
  removeMapPartial,
} from '../../utils/map-cache';
import type {
  MapBeaconEntry,
  MapFloorData,
  MapPackageRoot,
  MapPlaceEntry,
  MapVersionResponse,
} from '../../typings/api';
import { ApiClientError } from '../http/errors';
import { getHeader } from '../http/transport';
import type { ApiClient } from '../http/api-client';
import { BeaconRegistry } from '../ble/beacon-registry';

const log = createLogger('map');

const SUMMARY_KEY = (siteId: string) => `map:summary:${siteId}`;
const SUMMARY_ETAG_KEY = (siteId: string) => `map:summary-etag:${siteId}`;
const DOWNLOAD_KEY = (siteId: string) => `map:download:${siteId}`;

export interface MapServiceOptions {
  client: ApiClient;
  siteId: string;
  /** 联调开关：重写下载源为 API 同源（默认读环境配置）。 */
  rewriteDataUrlToLocalApi?: boolean;
}

/** 下载状态（持久化到 storage，重启后可续传）。 */
export interface MapDownloadState {
  mapVersion: string;
  dataSha256: string;
  downloadedBytes: number;
  totalBytes: number | null;
  updatedAtMs: number;
}

/** 供设置页展示的下载进度（未完成时才返回）。 */
export interface MapDownloadProgress extends MapDownloadState {
  /** 本次会话是否正在下载（false=中断/等待下次启动续传）。 */
  active: boolean;
}

/** 启动播报/进度所需事件（页面编排播报顺序，服务本身不出声）。 */
export type MapServiceEvent =
  | { type: 'download-start'; mapVersion: string; foreground: boolean }
  | { type: 'download-progress'; state: MapDownloadState }
  | { type: 'ready'; mapVersion: string; source: 'cache' | 'download'; stale: boolean };

export class MapService {
  private summary: MapVersionResponse | null = null;
  private pkg: MapPackageRoot | null = null;
  private registry: BeaconRegistry | null = null;
  private inflight: Promise<void> | null = null;
  private listeners = new Set<(event: MapServiceEvent) => void>();
  private downloadState: MapDownloadState | null = null;
  private downloading = false;

  constructor(private readonly opts: MapServiceOptions) {}

  get isLoaded(): boolean {
    return this.summary !== null && this.pkg !== null;
  }

  /** 场所 ID（构造时固定）。 */
  get siteId(): string {
    return this.opts.siteId;
  }

  /** 订阅下载/就绪事件；返回取消订阅函数。 */
  subscribe(listener: (event: MapServiceEvent) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** 未完成的地图下载（含上次启动中断的续传任务）；无则 null。 */
  get pendingDownload(): MapDownloadProgress | null {
    const state =
      this.downloadState ?? getJson<MapDownloadState>(DOWNLOAD_KEY(this.opts.siteId));
    if (state === null) {
      return null;
    }
    if (state.totalBytes !== null && state.downloadedBytes >= state.totalBytes) {
      return null;
    }
    return { ...state, active: this.downloading };
  }

  /** 幂等加载：并发调用共享同一次加载（单飞）。 */
  async ensureLoaded(): Promise<void> {
    if (this.isLoaded) {
      return;
    }
    if (this.inflight === null) {
      this.inflight = this.load().finally(() => {
        this.inflight = null;
      });
    }
    return this.inflight;
  }

  /** 当前地图版本；未加载抛 MAP_UNAVAILABLE。 */
  requireVersion(): string {
    if (this.summary === null) {
      throw mapUnavailable('地图尚未加载');
    }
    return this.summary.mapVersion;
  }

  get places(): MapPlaceEntry[] {
    return this.pkg?.places ?? [];
  }

  get beacons(): MapBeaconEntry[] {
    return this.pkg?.beacons ?? [];
  }

  /** 楼层几何（墙体等渲染子集）；未加载返回空对象。 */
  get floors(): Readonly<Record<string, MapFloorData>> {
    return this.pkg?.floors ?? {};
  }

  /** 信标三元组解析表；未加载抛 MAP_UNAVAILABLE。 */
  get beaconRegistry(): BeaconRegistry {
    if (this.registry === null) {
      throw mapUnavailable('地图尚未加载');
    }
    return this.registry;
  }

  requirePlace(placeId: string): MapPlaceEntry {
    const place = this.pkg?.places.find((p) => p.placeId === placeId);
    if (place === undefined) {
      throw new ApiClientError({ code: 'PLACE_NOT_FOUND', message: `地点不存在：${placeId}` });
    }
    return place;
  }

  private async load(): Promise<void> {
    const siteId = this.opts.siteId;
    const cachedSummary = getJson<MapVersionResponse>(SUMMARY_KEY(siteId));
    const cachedEtag = getString(SUMMARY_ETAG_KEY(siteId));
    const cachedRaw = await readMapCache(siteId);

    // 1) 本地配对命中（缓存摘要 + 缓存数据自洽）→ 立即可用，本会话沿用该版本
    let ready = false;
    if (cachedSummary !== null && cachedRaw !== null) {
      try {
        this.adopt(cachedRaw, cachedSummary);
        ready = true;
        log.info('地图缓存命中', cachedSummary.mapVersion);
      } catch {
        ready = false; // 损坏/版本不配对 → 视为无缓存
        log.warn('地图缓存配对失败，回退下载');
      }
    }

    // 2) 拉服务端摘要（带 ETag；数据未变时 304 短路）
    let resp;
    try {
      resp = await this.opts.client.request<MapVersionResponse>({
        method: 'GET',
        path: `/api/v1/sites/${siteId}/map`,
        headers: cachedEtag !== null ? { 'If-None-Match': cachedEtag } : undefined,
      });
    } catch (err) {
      if (ready) {
        // 弱网/离线：本地缓存可用即视为加载成功，不停机等摘要
        log.warn('摘要请求失败，沿用本地缓存', err instanceof Error ? err.message : String(err));
        this.emitReady('cache', false);
        return;
      }
      throw err;
    }

    if (resp.statusCode === 304) {
      // 服务端数据未变；仅当本地数据文件缺失/损坏时才用缓存摘要重建
      if (!ready) {
        if (cachedSummary === null) {
          throw mapUnavailable('命中 304 但摘要缓存缺失');
        }
        const raw = await this.download(cachedSummary, resp.etag, true);
        this.adopt(raw, cachedSummary);
        this.emitReady('download', false);
        return;
      }
      this.emitReady('cache', false);
      return;
    }

    const fresh = resp.data;
    if (fresh === null || typeof fresh.mapVersion !== 'string') {
      throw mapUnavailable('摘要响应格式异常');
    }

    if (!ready) {
      // 首启/配对失败：没有可用地图，前台下载并入库（支持断点续传）
      const raw = await this.download(fresh, resp.etag, true);
      this.adopt(raw, fresh);
      this.emitReady('download', false);
      return;
    }

    // 3) 已有可用缓存：判定新旧版本日期差（过旧仅提示，旧版继续可用）
    const stale = isVersionStale(this.summary!.mapVersion, fresh.mapVersion);
    this.emitReady('cache', stale);

    if (fresh.dataSha256 === cachedSummary!.dataSha256) {
      // 服务端 sha 与本地一致：不重新拉取地图，仅刷新摘要/ETag 持久层
      this.persistMeta(fresh, resp.etag);
      log.info('摘要一致，免下载', fresh.mapVersion);
      return;
    }

    // 服务端有新版本：后台静默下载（含续传半成品）；本会话不热替换已就绪地图
    void this.backgroundRefresh(fresh, resp.etag);
  }

  /** 后台更新：下载新版本 → 转正文件缓存 → 刷新摘要/ETag（下次启动配对生效）。 */
  private async backgroundRefresh(fresh: MapVersionResponse, etag: string | null): Promise<void> {
    try {
      await this.download(fresh, etag, false);
      log.info('地图新版本已缓存，下次启动生效', fresh.mapVersion);
    } catch (err) {
      // 不影响本会话（仍用已就绪版本）；保留半成品，下次启动自动续传
      log.warn('地图后台更新失败，下次启动重试', err instanceof Error ? err.message : String(err));
    }
  }

  private persistMeta(summary: MapVersionResponse, etag: string | null): void {
    setJson(SUMMARY_KEY(this.opts.siteId), summary);
    if (etag !== null) {
      setString(SUMMARY_ETAG_KEY(this.opts.siteId), etag);
    }
  }

  /**
   * 分片下载 + 断点续传 + 全文件 sha256 校验 + 转正缓存。
   * 返回转正后的地图 JSON 文本（调用方 adopt）。
   */
  private async download(
    summary: MapVersionResponse,
    etag: string | null,
    foreground: boolean,
  ): Promise<string> {
    const siteId = this.opts.siteId;
    const version = summary.mapVersion;
    const url = this.dataUrlFor(summary);

    let offset = 0;
    let total: number | null = null;
    const saved = getJson<MapDownloadState>(DOWNLOAD_KEY(siteId));
    if (saved !== null && saved.mapVersion === version && saved.dataSha256 === summary.dataSha256) {
      offset = Math.min(saved.downloadedBytes, await readMapPartialSize(siteId, version));
      total = saved.totalBytes;
    } else {
      if (saved !== null) {
        await removeMapPartial(siteId, saved.mapVersion); // 目标版本变了：丢弃旧半成品
      }
      await removeMapPartial(siteId, version);
    }

    this.downloading = true;
    this.downloadState = {
      mapVersion: version,
      dataSha256: summary.dataSha256,
      downloadedBytes: offset,
      totalBytes: total,
      updatedAtMs: nowMs(),
    };
    // 先落一次状态：首片未完成即中断时，设置页也能展示「未完成」与续传起点
    setJson(DOWNLOAD_KEY(siteId), this.downloadState);
    this.emit({ type: 'download-start', mapVersion: version, foreground });

    let resetTried = false;
    try {
      for (;;) {
        // 始终用 Range 请求（含首片）：服务端按片返回 206，客户端才能记录进度与续传；
        // 若服务端不支持 Range（返回 200 整包），下方按整包兜底。
        const headers = {
          Range: `bytes=${offset}-${offset + MAP_DOWNLOAD_CHUNK_BYTES - 1}`,
        };
        let resp;
        try {
          resp = await this.opts.client.request<ArrayBuffer>({
            method: 'GET',
            url,
            responseType: 'arraybuffer',
            headers,
          });
        } catch (err) {
          // 仅当服务端明确判定半成品越界（416）时才清空重下；
          // 网络中断必须原样抛出，由下次启动按已下载偏移续传。
          if (
            offset > 0 &&
            !resetTried &&
            err instanceof ApiClientError &&
            err.statusCode === 416
          ) {
            resetTried = true;
            await this.resetPartial(version);
            offset = 0;
            continue;
          }
          throw err;
        }

        const body = resp.data;
        if (!(body instanceof ArrayBuffer)) {
          throw mapUnavailable('地图数据响应格式异常');
        }

        if (resp.statusCode === 206) {
          const range = parseContentRange(getHeader(resp.header, 'Content-Range'));
          if (range === null) {
            throw mapUnavailable('地图分片响应缺少 Content-Range');
          }
          if (range.start !== offset) {
            if (resetTried) {
              throw mapUnavailable('地图分片偏移不一致');
            }
            resetTried = true;
            await this.resetPartial(version);
            offset = 0;
            continue;
          }
          if (range.total !== null) {
            total = range.total;
          }
          await appendMapPartial(siteId, version, body, offset);
          offset = range.end + 1;
        } else if (resp.statusCode === 200) {
          // 服务端未实现 Range（或本次从 0 开始）：整包写入，覆盖半成品
          if (offset > 0) {
            await removeMapPartial(siteId, version);
            offset = 0;
          }
          await appendMapPartial(siteId, version, body, offset);
          offset = body.byteLength;
          total = body.byteLength;
        } else {
          throw mapUnavailable(`地图数据响应状态异常：${resp.statusCode}`);
        }

        this.patchProgress(offset, total);
        if (total !== null && offset >= total) {
          break;
        }
        if (total === null) {
          throw mapUnavailable('地图分片响应缺少总长度');
        }
      }

      // 完整性校验对象是完整文件（分片拼接后的文本），与服务端 dataSha256 必须一致。
      // 用文本读取而非二进制 ArrayBuffer：真机/工具 readFile 不带 encoding 时的返回值
      // 未必能被宿主环境的 instanceof ArrayBuffer 命中，文本读取是已验证可靠的路径；
      // sha256Async 内部按 UTF-8 编码后分片哈希，JSON 文本往返编码字节一致。
      const raw = await readMapPartialText(siteId, version);
      if (raw === null || raw.length === 0) {
        throw mapUnavailable('地图半成品读取失败');
      }
      const digest = await sha256Async(raw);
      if (digest !== summary.dataSha256) {
        await removeMapPartial(siteId, version);
        throw mapUnavailable('地图数据摘要校验失败');
      }
      await commitMapCache(siteId, raw);
      await removeMapPartial(siteId, version);
      this.clearDownloadState();
      this.persistMeta(summary, etag);
      return raw;
    } finally {
      this.downloading = false;
      this.downloadState = null; // 中断/失败时回落到持久化状态供设置页展示
    }
  }

  private dataUrlFor(summary: MapVersionResponse): string {
    const useRewrite = this.opts.rewriteDataUrlToLocalApi ?? DEBUG_REWRITE_DATA_URL;
    return useRewrite
      ? `${API_BASE_URL}/api/v1/sites/${this.opts.siteId}/map/data?version=${encodeURIComponent(summary.mapVersion)}`
      : summary.dataUrl;
  }

  private async resetPartial(version: string): Promise<void> {
    await removeMapPartial(this.opts.siteId, version);
    this.patchProgress(0, null);
  }

  private patchProgress(downloadedBytes: number, totalBytes: number | null): void {
    if (this.downloadState === null) {
      return;
    }
    this.downloadState = { ...this.downloadState, downloadedBytes, totalBytes, updatedAtMs: nowMs() };
    setJson(DOWNLOAD_KEY(this.opts.siteId), this.downloadState);
    this.emit({ type: 'download-progress', state: this.downloadState });
  }

  private clearDownloadState(): void {
    this.downloadState = null;
    remove(DOWNLOAD_KEY(this.opts.siteId));
  }

  private emitReady(source: 'cache' | 'download', stale: boolean): void {
    this.emit({ type: 'ready', mapVersion: this.summary!.mapVersion, source, stale });
  }

  private emit(event: MapServiceEvent): void {
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch (err) {
        log.warn('地图事件回调异常', err instanceof Error ? err.message : String(err));
      }
    }
  }

  /** 采纳一份自洽的数据+摘要；版本不符抛错交调用方降级。 */
  private adopt(raw: string, summary: MapVersionResponse): void {
    let pkg: MapPackageRoot;
    try {
      pkg = JSON.parse(raw) as MapPackageRoot;
    } catch {
      throw mapUnavailable('地图数据 JSON 解析失败');
    }
    if (pkg.siteId !== this.opts.siteId || pkg.mapVersion !== summary.mapVersion) {
      throw mapUnavailable('地图数据与版本摘要不一致');
    }
    this.summary = summary;
    this.pkg = pkg;
    this.registry = new BeaconRegistry(pkg.beacons ?? []);
    log.info('地图就绪', pkg.mapVersion, `信标 ${this.registry.resolvableCount}`);
  }
}

/** `Content-Range: bytes start-end/total` 解析；缺失/`*` 总长度返回 null 字段。 */
export function parseContentRange(
  header: string | null,
): { start: number; end: number; total: number | null } | null {
  if (header === null) {
    return null;
  }
  const match = /^bytes\s+(\d+)-(\d+)\/(\d+|\*)$/.exec(header.trim());
  if (match === null) {
    return null;
  }
  return {
    start: Number(match[1]),
    end: Number(match[2]),
    total: match[3] === '*' ? null : Number(match[3]),
  };
}

/** 版本日期（`YYYY-MM-DD.N` 的前缀）→ UTC 毫秒；无法解析返回 null。 */
function versionDate(version: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(version);
  if (match === null) {
    return null;
  }
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

/** 服务端最新版本日期 − 本地版本日期 > 阈值（默认 7 天）判为过旧。 */
export function isVersionStale(
  localVersion: string,
  serverVersion: string,
  maxDays: number = MAP_STALE_DAYS,
): boolean {
  const local = versionDate(localVersion);
  const server = versionDate(serverVersion);
  if (local === null || server === null) {
    return false;
  }
  return (server - local) / 86_400_000 > maxDays;
}

function mapUnavailable(message: string): ApiClientError {
  return new ApiClientError({ code: 'MAP_UNAVAILABLE', message, retryable: true });
}
