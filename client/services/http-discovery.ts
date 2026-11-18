/**
 * HTTP 单播扫描发现：在部署约定的热点网段上并发探测 `/healthz` 并校验设备身份。
 *
 * 网段是**写死的部署约定**，不是从本机网络信息推导出来的：
 * iPhone 个人热点固定使用 172.20.10.0/24（网关 .1，客户端通常 .2–.14），
 * 因此直接扫描 172.20.10.1–254 共 254 个目标。换热点形态/网段时只改这里。
 *
 * 之所以不再调用 `wx.getLocalIPAddress`：真机上它返回的是蜂窝接口地址
 * （如 10.13.1.172/255.255.255.255），据此推算的网段与热点网段无关。
 */
import {
  SCAN_CONCURRENCY,
  SCAN_MAX_HOSTS,
  SCAN_TIMEOUT_MS,
  SERVICE_PORT,
} from '../config/env';
import type { CancelToken } from '../core/discovery/cancel-token';
import type { EndpointFailureReason } from '../core/discovery/failure';
import { scanForService, type HealthScanMatch } from '../core/discovery/health-scan';
import { createLogger } from '../utils/logger';
import { probeEndpoint } from './p0-candidate';

const log = createLogger('scan-discovery');

/** 部署约定：iPhone 个人热点固定网段（当前联调现场；非通用推导结果）。 */
const HOTSPOT_SUBNET_CIDR = '172.20.10.0/24';
const HOTSPOT_IP_PREFIX = '172.20.10.';
const HOTSPOT_HOST_START = 1;
const HOTSPOT_HOST_END = 254;

export interface ScanDiscoveryResult {
  service: HealthScanMatch | null;
  reason: EndpointFailureReason | null;
  targetCount: number;
  responded: number;
}

/** 固定网段的扫描目标（.1–.254），受统一上限保护。 */
function hotspotTargets(): string[] {
  const targets: string[] = [];
  for (let host = HOTSPOT_HOST_START; host <= HOTSPOT_HOST_END; host += 1) {
    targets.push(`${HOTSPOT_IP_PREFIX}${host}`);
  }
  return targets.slice(0, SCAN_MAX_HOSTS);
}

export async function scanLanForService(
  knownDeviceId: string | null = null,
  cancel?: CancelToken,
): Promise<ScanDiscoveryResult> {
  const startedAt = Date.now();
  const targets = hotspotTargets();
  log.info(`开始扫描 ${HOTSPOT_SUBNET_CIDR}：${targets.length} 个目标，并发 ${SCAN_CONCURRENCY}，`
    + `开始时间=${new Date(startedAt).toISOString()}`);
  let probed = 0;
  const result = await scanForService(targets, {
    port: SERVICE_PORT,
    concurrency: SCAN_CONCURRENCY,
    knownDeviceId,
    cancel,
    probe: async (url, probeCancel) => {
      const probe = await probeEndpoint(url, SCAN_TIMEOUT_MS, probeCancel);
      if (probe.kind !== 'cancelled') {
        probed++;
        if (probed % 64 === 0) {
          log.info(`扫描进度 ${probed}/${targets.length}`);
        }
      }
      return probe;
    },
  });

  if (result.match !== null) {
    log.info('扫描结束：命中服务端', result.match.url,
      `命中数=${result.matched}`,
      `已探测=${result.attempted}`,
      `响应=${result.responded}`,
      `耗时=${Date.now() - startedAt}ms`,
      `结束时间=${new Date().toISOString()}`);
    return {
      service: result.match,
      reason: null,
      targetCount: targets.length,
      responded: result.responded,
    };
  }

  const reason: EndpointFailureReason = result.responded === 0 ? 'scan-requests-failed' : 'scan-no-service';
  log.warn('扫描结束：未发现可用服务', reason,
    `命中数=0`,
    `已探测=${result.attempted}`,
    `响应=${result.responded}`,
    `耗时=${Date.now() - startedAt}ms`,
    `结束时间=${new Date().toISOString()}`);
  return { service: null, reason, targetCount: targets.length, responded: result.responded };
}
