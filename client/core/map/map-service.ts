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
import { createSha256Stream, type Sha256Stream } from '../../utils/sha256';
import { createLogger } from '../../utils/logger';
import { logPerfPhase, startPerfTrace } from '../../utils/perf-trace';
import { getJson, getString, remove, setJson, setString } from '../../utils/kv';
import {
  appendMapPartial,
  commitMapPartial,
  readMapCache,
  readMapPartialSize,
  readMapPartialSlice,
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
import { resolveBaseUrl, type BaseUrlProvider } from '../http/base-url';
import type { ApiClient } from '../http/api-client';
import { BeaconRegistry } from '../ble/beacon-registry';

const log = createLogger('map');

const SUMMARY_KEY = (siteId: string) => `map:summary:${siteId}`;
const SUMMARY_ETAG_KEY = (siteId: string) => `map:summary-etag:${siteId}`;
const DOWNLOAD_KEY = (siteId: string) => `map:download:${siteId}`;

/**
 * 已落盘半成品的分片哈希读块（1MiB）：按片读 + 立即喂哈希，避免整包 6MB 文本读回。
 * 与下载分片同为 1MiB，单片同步哈希量有界。
 */
const PARTIAL_HASH_READ_BYTES = 1024 * 1024;

export interface MapServiceOptions {
  client: ApiClient;
  siteId: string;
  /**
   * 业务根地址来源（默认编译期配置）。联调重写下载源时必须与
   * 登录/业务请求使用同一地址，避免分片下载打到旧 IP。
   */
  baseUrl?: BaseUrlProvider;
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
  /** 后台静默下载任务（手动重载前必须先等它结束，避免并发写同一半成品）。 */
  private backgroundTask: Promise<void> | null = null;
  private listeners = new Set<(event: MapServiceEvent) => void>();
  private downloadState: MapDownloadState | null = null;
  private downloading = false;
  /** storage 写入失败告警（设置页展示；null=正常）。 */
  private storageWarningValue: string | null = null;

  constructor(private readonly opts: MapServiceOptions) {}

  /** 最近一次本地持久化失败说明；正常情况下为 null。 */
  get storageWarning(): string | null {
    return this.storageWarningValue;
  }

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
    this.backgroundRefresh(fresh, resp.etag);
  }

  /** 后台更新：下载新版本 → 转正文件缓存 → 刷新摘要/ETag（下次启动配对生效）。 */
  private backgroundRefresh(fresh: MapVersionResponse, etag: string | null): void {
    const task = this.download(fresh, etag, false)
      .then(() => {
        log.info('地图新版本已缓存，下次启动生效', fresh.mapVersion);
      })
      .catch((err) => {
        // 不影响本会话（仍用已就绪版本）；保留半成品，下次启动自动续传
        log.warn('地图后台更新失败，下次启动重试', err instanceof Error ? err.message : String(err));
      });
    this.backgroundTask = task;
    void task.finally(() => {
      if (this.backgroundTask === task) {
        this.backgroundTask = null;
      }
    });
  }

  /**
   * 手动重新加载地图（设置页「重新加载地图」）。
   * 与 load() 的差别：不带 If-None-Match（强制拿最新摘要）、不做后台静默更新，
   * 数据有变化时前台下载并在本次会话立即生效；失败原样抛给调用方展示原因。
   */
  async reload(): Promise<void> {
    if (this.inflight !== null) {
      try {
        await this.inflight;
      } catch {
        // 上次自动加载失败不影响这次手动重载
      }
    }
    if (this.backgroundTask !== null) {
      await this.backgroundTask; // 已在此内部吞掉失败
    }
    const run = this.reloadInternal().finally(() => {
      if (this.inflight === run) {
        this.inflight = null;
      }
    });
    this.inflight = run;
    return run;
  }

  private async reloadInternal(): Promise<void> {
    const siteId = this.opts.siteId;
    const trace = startPerfTrace('map', 'map-reload');
    const resp = await this.opts.client.request<MapVersionResponse>({
      method: 'GET',
      path: `/api/v1/sites/${siteId}/map`,
    });

    if (resp.statusCode === 304) {
      // 未带 If-None-Match 仍返回 304（代理/缓存层）：沿用当前已就绪版本
      trace.mark('summary-304', 1000);
      if (this.summary === null) {
        throw mapUnavailable('摘要命中 304 但本地无可用地图');
      }
      this.emitReady('cache', false);
      return;
    }

    const fresh = resp.data;
    if (fresh === null || typeof fresh.mapVersion !== 'string') {
      throw mapUnavailable('摘要响应格式异常');
    }
    trace.mark('summary', 1000);

    // 服务端数据未变：复用本地缓存重新配对，不重复下载 6MB
    const cachedSummary = getJson<MapVersionResponse>(SUMMARY_KEY(siteId));
    const cachedRaw = await readMapCache(siteId);
    if (
      cachedSummary !== null &&
      cachedRaw !== null &&
      cachedSummary.dataSha256 === fresh.dataSha256
    ) {
      try {
        this.adopt(cachedRaw, fresh);
        this.persistMeta(fresh, resp.etag);
        trace.mark('cache-reused', 1000);
        this.emitReady('cache', false);
        log.info('手动重载：服务端数据未变，已复用本地缓存', fresh.mapVersion);
        return;
      } catch (err) {
        log.warn(
          '手动重载：本地缓存不可用，改为重新下载',
          err instanceof Error ? err.message : String(err),
        );
      }
    }

    trace.mark('need-download', 1000);
    const raw = await this.download(fresh, resp.etag, true);
    this.adopt(raw, fresh);
    trace.mark('applied', 3000);
    this.emitReady('download', false);
    log.info('手动重载：已下载并立即生效', fresh.mapVersion);
  }

  /**
   * 持久化摘要/ETag。写入失败必须可见：摘要丢失会让下次启动退化为整包下载，
   * 这正是「每次启动重复下载」的成因之一，不能静默降级。
   */
  private persistMeta(summary: MapVersionResponse, etag: string | null): void {
    const okSummary = setJson(SUMMARY_KEY(this.opts.siteId), summary);
    const okEtag = etag === null ? true : setString(SUMMARY_ETAG_KEY(this.opts.siteId), etag);
    if (!okSummary || !okEtag) {
      this.warnStorage('地图摘要写入 storage 失败（本次会话可用，重启后可能重复下载）');
    }
  }

  /** 记录并按需升级 storage 失败告警（同一原因只打一次 error，避免刷屏）。 */
  private warnStorage(message: string): void {
    if (this.storageWarningValue === message) {
      return;
    }
    this.storageWarningValue = message;
    log.error(`${message}（storage=${storageUsageText()}）`);
  }

  /** 下载进度状态落盘；失败按同一套告警暴露。 */
  private persistDownloadState(): void {
    if (this.downloadState === null) {
      return;
    }
    if (!setJson(DOWNLOAD_KEY(this.opts.siteId), this.downloadState)) {
      this.warnStorage('地图下载进度写入 storage 失败（中断后续传起点可能丢失）');
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
    const startedAtMs = nowMs();

    let offset = 0;
    let total: number | null = null;
    /** 半成品实际落盘大小（存在且与目标版本匹配时）。 */
    let fileSize = 0;
    const saved = getJson<MapDownloadState>(DOWNLOAD_KEY(siteId));
    if (saved !== null && saved.mapVersion === version && saved.dataSha256 === summary.dataSha256) {
      fileSize = await readMapPartialSize(siteId, version);
      // appendFile 总是追加到文件末尾，实际文件大小才是真实续传偏移；
      // 状态记录可能落后于文件（写完分片、未落状态即被回收）。
      offset = fileSize;
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
    this.persistDownloadState();
    this.emit({ type: 'download-start', mapVersion: version, foreground });

    const trace = startPerfTrace('map', 'map-download', { startedAtMs });
    let hasher = createSha256Stream();
    let resetTried = false;
    try {
      // 半成品已完整（上次在「校验/转正」阶段中断）：直接校验现有文件并转正。
      // 旧实现此时会 resetPartial 后整包重下 6MB —— 这是「每次启动重复下载」的直接原因。
      if (total !== null && fileSize >= total) {
        // 用实际文件大小参与哈希：文件被多写/截断时摘要必然不符，走丢弃重下
        await this.feedPartialToHasher(hasher, siteId, version, fileSize);
        trace.mark('reuse-digest', 3000);
        if (hasher.digestHex() === summary.dataSha256) {
          await commitMapPartial(siteId, version);
          this.logDigestStats(hasher, trace.traceId);
          this.clearDownloadState();
          this.persistMeta(summary, etag);
          const cached = await readMapCache(siteId);
          if (cached === null) {
            throw mapUnavailable('地图缓存读取失败');
          }
          trace.mark('reuse-ready', 1000);
          log.info('完整半成品校验通过并转正', version);
          return cached;
        }
        log.warn('完整半成品摘要不符，丢弃后整包重下', version);
        await this.resetPartial(version);
        offset = 0;
        total = null;
        hasher = createSha256Stream();
      } else if (offset > 0) {
        // 续传：先把已落盘字节按片补算进哈希，再继续下载剩余分片
        await this.feedPartialToHasher(hasher, siteId, version, offset);
      }

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
          // 416 = 续传偏移越界：无论偏移是否 >0（首片也可能因服务端版本变化而越界），
          // 都清空半成品并从头重下一次；网络中断必须原样抛出，由下次启动续传。
          if (
            !resetTried &&
            err instanceof ApiClientError &&
            err.statusCode === 416
          ) {
            resetTried = true;
            await this.resetPartial(version);
            offset = 0;
            total = null;
            hasher = createSha256Stream(); // 半成品已清空：哈希状态同步归零
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
            hasher = createSha256Stream();
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
            hasher = createSha256Stream();
          }
          await appendMapPartial(siteId, version, body, offset);
          offset = body.byteLength;
          total = body.byteLength;
        } else {
          throw mapUnavailable(`地图数据响应状态异常：${resp.statusCode}`);
        }

        // 边下边算：写入成功后立即把本片字节喂给流式哈希，不再在下载结束后读回全文重算
        hasher.updateSync(new Uint8Array(body));

        this.patchProgress(offset, total);
        if (total !== null && offset >= total) {
          break;
        }
        if (total === null) {
          throw mapUnavailable('地图分片响应缺少总长度');
        }
      }

      // 下载过程中已按片完成全量哈希，这里只做收尾比对；校验对象是服务端原始字节，
      // 仍与后端 dataSha256 全量比对（不走 size/ETag 弱校验）。
      trace.mark('chunks-done', 3000);
      const digest = hasher.digestHex();
      const stats = hasher.stats();
      this.logDigestStats(hasher, trace.traceId);
      log.info(
        '地图分片下载完成',
        version,
        `下载=${nowMs() - startedAtMs}ms`,
        `哈希CPU=${stats.syncMs}ms`,
        `字节=${stats.bytes}`,
      );
      if (digest !== summary.dataSha256) {
        await removeMapPartial(siteId, version);
        throw mapUnavailable('地图数据摘要校验失败');
      }
      const commitStartAtMs = nowMs();
      // 校验通过后直接把半成品 rename 为正式缓存，不再另写 4.6MB staging 文件
      await commitMapPartial(siteId, version);
      trace.mark('commit-done', 1000);
      log.info('地图校验与转正耗时', `转正=${nowMs() - commitStartAtMs}ms`);
      this.clearDownloadState();
      this.persistMeta(summary, etag);
      const raw = await readMapCache(siteId);
      if (raw === null) {
        throw mapUnavailable('地图缓存读取失败');
      }
      trace.mark('ready', 3000);
      return raw;
    } finally {
      this.downloading = false;
      this.downloadState = null; // 中断/失败时回落到持久化状态供设置页展示
    }
  }

  /** 把半成品 [0,length) 字节按片读入流式哈希（单片 1MiB，同步量有界）。 */
  private async feedPartialToHasher(
    hasher: Sha256Stream,
    siteId: string,
    version: string,
    length: number,
  ): Promise<void> {
    let pos = 0;
    while (pos < length) {
      const want = Math.min(PARTIAL_HASH_READ_BYTES, length - pos);
      const piece = await readMapPartialSlice(siteId, version, pos, want);
      if (piece === null || piece.length === 0) {
        throw mapUnavailable('地图半成品分片读取失败');
      }
      hasher.updateSync(piece);
      pos += piece.length;
    }
  }

  /** 摘要耗时拆分：同步压缩 CPU 与 macrotask 让出等待分别记录，便于复查回归。 */
  private logDigestStats(hasher: Sha256Stream, traceId: string): void {
    const stats = hasher.stats();
    logPerfPhase('map', 'map-digest', 'hash-cpu', stats.syncMs, { thresholdMs: 1000, traceId });
    logPerfPhase('map', 'map-digest', 'hash-yield-wait', stats.yieldWaitMs, {
      thresholdMs: 1000,
      traceId,
    });
    logPerfPhase('map', 'map-digest', 'hash-bytes', stats.bytes, {
      thresholdMs: Number.MAX_SAFE_INTEGER,
      traceId,
    });
  }

  private dataUrlFor(summary: MapVersionResponse): string {
    const useRewrite = this.opts.rewriteDataUrlToLocalApi ?? DEBUG_REWRITE_DATA_URL;
    const base = resolveBaseUrl(this.opts.baseUrl ?? API_BASE_URL);
    return useRewrite
      ? `${base}/api/v1/sites/${this.opts.siteId}/map/data?version=${encodeURIComponent(summary.mapVersion)}`
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
    this.persistDownloadState();
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

/** storage 用量（写失败时辅助判断是否写满）；取不到返回「未知」。 */
function storageUsageText(): string {
  try {
    if (typeof wx.getStorageInfoSync !== 'function') {
      return '未知';
    }
    const info = wx.getStorageInfoSync();
    return `${info.currentSize}KB/${info.limitSize}KB`;
  } catch {
    return '未知';
  }
}
