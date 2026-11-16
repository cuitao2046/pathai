import { scanForService } from '../core/discovery/health-scan';
import type { EndpointProbeResult } from '../core/discovery/service-identity';

const DEVICE_A = 'srv-' + 'a'.repeat(32);
const DEVICE_B = 'srv-' + 'b'.repeat(32);

function identified(deviceId = DEVICE_A): EndpointProbeResult {
  return {
    kind: 'identified',
    identity: { service: 'my-backend', deviceId, version: '0.1.0', apiVersion: 'v1' },
  };
}

describe('有界并发 HTTP 扫描池', () => {
  test('命中服务即停止派发新目标，并记录探测统计', async () => {
    const probe = jest.fn(async (url: string): Promise<EndpointProbeResult> =>
      url.includes('192.168.1.3') ? identified() : { kind: 'unreachable' });
    const result = await scanForService(['192.168.1.1', '192.168.1.2', '192.168.1.3', '192.168.1.4'], {
      port: 8000,
      concurrency: 1,
      probe,
    });
    expect(result.match?.url).toBe('http://192.168.1.3:8000');
    expect(result.attempted).toBe(3);
    expect(result.responded).toBe(1);
    expect(probe).toHaveBeenCalledTimes(3);
  });

  test('并发数不超过配置上限', async () => {
    let active = 0;
    let peak = 0;
    const probe = jest.fn(async (): Promise<EndpointProbeResult> => {
      active++;
      peak = Math.max(peak, active);
      await new Promise(resolve => setTimeout(resolve, 1));
      active--;
      return { kind: 'unreachable' };
    });
    const targets = Array.from({ length: 20 }, (_, index) => `192.168.1.${index + 1}`);
    await scanForService(targets, { port: 8000, concurrency: 3, probe });
    expect(peak).toBeLessThanOrEqual(3);
    expect(probe).toHaveBeenCalledTimes(20);
  });

  test('已知 device_id 时忽略其它服务端响应', async () => {
    const probe = jest.fn(async (url: string): Promise<EndpointProbeResult> =>
      url.endsWith('.2:8000') ? identified(DEVICE_B) : { kind: 'unidentified' });
    const result = await scanForService(['192.168.1.1', '192.168.1.2'], {
      port: 8000,
      concurrency: 2,
      knownDeviceId: DEVICE_A,
      probe,
    });
    expect(result.match).toBeNull();
    expect(result.responded).toBe(2);
    expect(result.matched).toBe(0);
  });

  test('全部请求失败时 responded 为 0，用于区分“扫完无命中”', async () => {
    const result = await scanForService(['192.168.1.1', '192.168.1.2'], {
      port: 8000,
      concurrency: 2,
      probe: async () => ({ kind: 'unreachable' }),
    });
    expect(result).toEqual({ match: null, attempted: 2, responded: 0, matched: 0 });
  });
});
