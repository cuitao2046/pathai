/**
 * 地图服务：API-02 摘要（ETag/304 短路）+ 地图包下载校验 + 本地文件缓存。
 *
 * 规则（计划 C3 / FE-01）：
 * - 摘要/ETag 持久化（storage 小对象）；数据本体走用户目录文件缓存
 *   （wx storage 单 key 上限 1MB，4.6MB 地图包放不下，写入必失败）；
 * - 启动：本地配对（缓存摘要 + 缓存数据）命中即秒开，随后拉服务端摘要：
 *   304 或 sha 一致 → 不重新拉取；sha 不同 → 后台异步下载覆盖本地缓存，
 *   本会话继续用已就绪版本（避免导航会话中途热换地图），下次启动生效；
 * - 首启（无可用缓存）→ 前台下载，下载字节经 sha256Async 与服务端
 *   dataSha256 比对后才写缓存；
 * - 联调开关重写下载源为 API 同源，sha256 校验不关闭；
 * - 未加载时 require* 一律抛 MAP_UNAVAILABLE（禁止开始导航 FE-01）。
 */
import { API_BASE_URL, DEBUG_REWRITE_DATA_URL } from '../../config/env';
import { sha256Async } from '../../utils/sha256';
import { createLogger } from '../../utils/logger';
import { getJson, getString, setJson, setString } from '../../utils/kv';
import { readMapCache, writeMapCache } from '../../utils/map-cache';
import type {
  MapBeaconEntry,
  MapPackageRoot,
  MapPlaceEntry,
  MapVersionResponse,
} from '../../typings/api';
import { ApiClientError } from '../http/errors';
import type { ApiClient } from '../http/api-client';
import { BeaconRegistry } from '../ble/beacon-registry';

const log = createLogger('map');

const SUMMARY_KEY = (siteId: string) => `map:summary:${siteId}`;
const SUMMARY_ETAG_KEY = (siteId: string) => `map:summary-etag:${siteId}`;

export interface MapServiceOptions {
  client: ApiClient;
  siteId: string;
  /** 联调开关：重写下载源为 API 同源（默认读环境配置）。 */
  rewriteDataUrlToLocalApi?: boolean;
}

export class MapService {
  private summary: MapVersionResponse | null = null;
  private pkg: MapPackageRoot | null = null;
  private registry: BeaconRegistry | null = null;
  private inflight: Promise<void> | null = null;

  constructor(private readonly opts: MapServiceOptions) {}

  get isLoaded(): boolean {
    return this.summary !== null && this.pkg !== null;
  }

  /** 场所 ID（构造时固定）。 */
  get siteId(): string {
    return this.opts.siteId;
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
    const resp = await this.opts.client.request<MapVersionResponse>({
      method: 'GET',
      path: `/api/v1/sites/${siteId}/map`,
      headers: cachedEtag !== null ? { 'If-None-Match': cachedEtag } : undefined,
    });

    if (resp.statusCode === 304) {
      // 服务端数据未变；仅当本地数据文件缺失/损坏时才用缓存摘要重建
      if (!ready) {
        if (cachedSummary === null) {
          throw mapUnavailable('命中 304 但摘要缓存缺失');
        }
        const raw = await this.download(cachedSummary);
        await this.persist(cachedSummary, resp.etag, raw);
        this.adopt(raw, cachedSummary);
      }
      return;
    }

    const fresh = resp.data;
    if (fresh === null || typeof fresh.mapVersion !== 'string') {
      throw mapUnavailable('摘要响应格式异常');
    }

    if (!ready) {
      // 首启/配对失败：没有可用地图，前台下载并入库
      const raw = await this.download(fresh);
      await this.persist(fresh, resp.etag, raw);
      this.adopt(raw, fresh);
      return;
    }

    if (fresh.dataSha256 === cachedSummary!.dataSha256) {
      // 服务端 sha 与本地一致：不重新拉取地图，仅刷新摘要/ETag 持久层
      setJson(SUMMARY_KEY(siteId), fresh);
      if (resp.etag !== null) {
        setString(SUMMARY_ETAG_KEY(siteId), resp.etag);
      }
      log.info('摘要一致，免下载', fresh.mapVersion);
      return;
    }

    // 服务端有新版本：后台异步拉取覆盖本地缓存；本会话不热替换已就绪地图
    void this.backgroundRefresh(fresh, resp.etag);
  }

  /** 后台更新：下载新版本 → 覆盖文件缓存 → 刷新摘要/ETag（下次启动配对生效）。 */
  private async backgroundRefresh(fresh: MapVersionResponse, etag: string | null): Promise<void> {
    try {
      const raw = await this.download(fresh);
      await this.persist(fresh, etag, raw);
      log.info('地图新版本已缓存，下次启动生效', fresh.mapVersion);
    } catch (err) {
      // 不影响本会话（仍用已就绪版本）；下次启动自动重试
      log.warn('地图后台更新失败，下次启动重试', err instanceof Error ? err.message : String(err));
    }
  }

  /** 下载 + sha 校验 + 持久化（数据先写、摘要后写：中断时配对失败走重建，安全降级）。 */
  private async persist(summary: MapVersionResponse, etag: string | null, raw: string): Promise<void> {
    await writeMapCache(this.opts.siteId, raw);
    setJson(SUMMARY_KEY(this.opts.siteId), summary);
    if (etag !== null) {
      setString(SUMMARY_ETAG_KEY(this.opts.siteId), etag);
    }
  }

  private async download(summary: MapVersionResponse): Promise<string> {
    const useRewrite = this.opts.rewriteDataUrlToLocalApi ?? DEBUG_REWRITE_DATA_URL;
    const url = useRewrite
      ? `${API_BASE_URL}/api/v1/sites/${this.opts.siteId}/map/data?version=${encodeURIComponent(summary.mapVersion)}`
      : summary.dataUrl;
    const resp = await this.opts.client.request<string>({
      method: 'GET',
      url,
      dataType: 'text', // 原始字符串，保证 sha256 与服务端字节一致
    });
    const raw = resp.data;
    if (typeof raw !== 'string' || (await sha256Async(raw)) !== summary.dataSha256) {
      throw mapUnavailable('地图数据摘要校验失败');
    }
    return raw;
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

function mapUnavailable(message: string): ApiClientError {
  return new ApiClientError({ code: 'MAP_UNAVAILABLE', message, retryable: true });
}
