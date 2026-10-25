/**
 * 地图服务：API-02 摘要（ETag/304 短路）+ 地图包数据下载与 sha256 校验。
 *
 * 规则（计划 C3 / FE-01）：
 * - 摘要缓存与 ETag 持久化；刷新时 If-None-Match 命中 304 则复用缓存摘要；
 * - 本地数据摘要与 summary.dataSha256 一致时不重新下载；
 * - 联调开关重写下载源为 API 同源 /map/data，sha256 校验不关闭；
 * - 未加载时 require* 一律抛 MAP_UNAVAILABLE（禁止开始导航 FE-01）。
 */
import { API_BASE_URL, DEBUG_REWRITE_DATA_URL } from '../../config/env';
import { sha256 } from '../../utils/sha256';
import { createLogger } from '../../utils/logger';
import { getJson, getString, setJson, setString } from '../../utils/kv';
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
const DATA_KEY = (siteId: string) => `map:data:${siteId}`;

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
    const storedEtag = getString(SUMMARY_ETAG_KEY(siteId));

    const summaryResp = await this.opts.client.request<MapVersionResponse>({
      method: 'GET',
      path: `/api/v1/sites/${siteId}/map`,
      headers: storedEtag !== null ? { 'If-None-Match': storedEtag } : undefined,
    });

    if (summaryResp.statusCode === 304) {
      const cached = getJson<MapVersionResponse>(SUMMARY_KEY(siteId));
      if (cached === null) {
        throw mapUnavailable('摘要缓存缺失却命中 304');
      }
      this.summary = cached;
      log.info('摘要 304，复用缓存', this.summary.mapVersion);
    } else {
      const summary = summaryResp.data;
      if (summary === null || typeof summary.mapVersion !== 'string') {
        throw mapUnavailable('摘要响应格式异常');
      }
      this.summary = summary;
      setJson(SUMMARY_KEY(siteId), summary);
      if (summaryResp.etag !== null) {
        setString(SUMMARY_ETAG_KEY(siteId), summaryResp.etag);
      }
    }

    const targetSha = this.summary.dataSha256;
    const storedRaw = getString(DATA_KEY(siteId));
    if (storedRaw !== null && sha256(storedRaw) === targetSha) {
      this.adopt(storedRaw);
      return; // 本地数据可用：不再下载
    }

    const raw = await this.download(targetSha);
    setString(DATA_KEY(siteId), raw);
    this.adopt(raw);
  }

  private async download(targetSha: string): Promise<string> {
    const summary = this.summary as MapVersionResponse;
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
    if (typeof raw !== 'string' || sha256(raw) !== targetSha) {
      throw mapUnavailable('地图数据摘要校验失败');
    }
    return raw;
  }

  private adopt(raw: string): void {
    const summary = this.summary as MapVersionResponse;
    let pkg: MapPackageRoot;
    try {
      pkg = JSON.parse(raw) as MapPackageRoot;
    } catch {
      throw mapUnavailable('地图数据 JSON 解析失败');
    }
    if (pkg.siteId !== this.opts.siteId || pkg.mapVersion !== summary.mapVersion) {
      throw mapUnavailable('地图数据与版本摘要不一致');
    }
    this.pkg = pkg;
    this.registry = new BeaconRegistry(pkg.beacons ?? []);
    log.info('地图就绪', pkg.mapVersion, `信标 ${this.registry.resolvableCount}`);
  }
}

function mapUnavailable(message: string): ApiClientError {
  return new ApiClientError({ code: 'MAP_UNAVAILABLE', message, retryable: true });
}
