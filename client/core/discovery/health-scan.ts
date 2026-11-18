/**
 * 有界并发扫描池（纯函数）：对目标 IP 逐个探测，命中即中止所有在途探测并停止派发。
 *
 * 通过注入 `probe` 隔离 wx.request，单测可直接用假实现验证并发、命中与取消逻辑。
 */
import { createCancelSource, type CancelToken } from './cancel-token';
import type { EndpointProbe, ServiceIdentity } from './service-identity';

export interface HealthScanOptions {
  port: number;
  concurrency: number;
  probe: EndpointProbe;
  /** 已知设备身份：只接受同一 device_id 的响应（首次发现时为 null）。 */
  knownDeviceId?: string | null;
  /** 外部取消信号：地址失效、页面隐藏或新轮次开始时立即中止在途探测。 */
  cancel?: CancelToken;
}

export interface HealthScanMatch {
  url: string;
  identity: ServiceIdentity;
}

export interface HealthScanResult {
  match: HealthScanMatch | null;
  /** 已派发的探测数（命中后不再派发）。 */
  attempted: number;
  /** 有 HTTP 响应的探测数（含非目标服务响应）。 */
  responded: number;
  matched: number;
}

export async function scanForService(
  targets: readonly string[],
  options: HealthScanOptions,
): Promise<HealthScanResult> {
  const { port, concurrency, probe } = options;
  const knownDeviceId = options.knownDeviceId ?? null;
  let nextIndex = 0;
  let attempted = 0;
  let responded = 0;
  let matched = 0;
  let match: HealthScanMatch | null = null;
  /** 命中或外部取消时统一触发：中止在途探测并让 worker 立即退出。 */
  const stop = createCancelSource();
  const unlink = options.cancel?.onCancel(() => stop.cancel()) ?? null;

  const worker = async (): Promise<void> => {
    while (match === null && !stop.token.aborted) {
      const index = nextIndex++;
      if (index >= targets.length) {
        return;
      }
      attempted++;
      const url = `http://${targets[index]}:${port}`;
      const result = await probe(url, stop.token);
      if (result.kind === 'cancelled') {
        return;
      }
      if (result.kind !== 'unreachable') {
        responded++;
      }
      if (
        result.kind === 'identified' &&
        (knownDeviceId === null || result.identity.deviceId === knownDeviceId)
      ) {
        matched++;
        if (match === null) {
          match = { url, identity: result.identity };
          stop.cancel();
        }
      }
    }
  };

  try {
    const workerCount = Math.min(Math.max(1, concurrency), targets.length);
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    return { match, attempted, responded, matched };
  } finally {
    unlink?.();
  }
}
