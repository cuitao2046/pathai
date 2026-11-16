/** HTTP 单播扫描：固定热点网段、身份命中与失败原因区分。 */
import type * as HttpDiscovery from '../services/http-discovery';
import type { EndpointProbeResult } from '../core/discovery/service-identity';
import { describeEndpointFailure } from '../core/discovery/failure';

const DEVICE_A = 'srv-' + 'a'.repeat(32);
const DEVICE_B = 'srv-' + 'b'.repeat(32);

let probeEndpoint: jest.Mock;

function identified(deviceId = DEVICE_A): EndpointProbeResult {
  return {
    kind: 'identified',
    identity: { service: 'my-backend', deviceId, version: '0.1.0', apiVersion: 'v1' },
  };
}

beforeEach(() => {
  jest.resetModules();
  probeEndpoint = jest.fn();
  jest.doMock('../services/p0-candidate', () => ({ probeEndpoint }));
});

function load(): typeof HttpDiscovery {
  return require('../services/http-discovery') as typeof HttpDiscovery;
}

/** 只让指定 IP 命中，其余视为请求失败。 */
function hitOnly(ip: string, deviceId = DEVICE_A): void {
  probeEndpoint.mockImplementation(async (url: string): Promise<EndpointProbeResult> => {
    return url === `http://${ip}:8000` ? identified(deviceId) : { kind: 'unreachable' };
  });
}

describe('HTTP 单播扫描发现', () => {
  test('固定扫描 172.20.10.1–254（254 个目标）并命中', async () => {
    hitOnly('172.20.10.4');
    const result = await load().scanLanForService(null);
    expect(result.service?.url).toBe('http://172.20.10.4:8000');
    expect(result.service?.identity.deviceId).toBe(DEVICE_A);
    expect(result.targetCount).toBe(254);
    expect(probeEndpoint).toHaveBeenCalledWith('http://172.20.10.1:8000', expect.anything());
  });

  test('命中后不再派发新目标', async () => {
    hitOnly('172.20.10.4');
    await load().scanLanForService(null);
    expect(probeEndpoint).not.toHaveBeenCalledWith('http://172.20.10.200:8000', expect.anything());
  });

  test('有响应但不是目标服务：scan-no-service', async () => {
    probeEndpoint.mockResolvedValue({ kind: 'unidentified' });
    const result = await load().scanLanForService(null);
    expect(result.reason).toBe('scan-no-service');
    expect(result.responded).toBe(254);
    expect(result.targetCount).toBe(254);
  });

  test('请求全部失败：scan-requests-failed', async () => {
    probeEndpoint.mockResolvedValue({ kind: 'unreachable' });
    const result = await load().scanLanForService(null);
    expect(result.reason).toBe('scan-requests-failed');
    expect(result.responded).toBe(0);
  });

  test('已知 device_id 时只接受同一台服务端', async () => {
    hitOnly('172.20.10.4', DEVICE_B);
    const result = await load().scanLanForService(DEVICE_A);
    expect(result.service).toBeNull();
    expect(result.reason).toBe('scan-no-service');
  });
});

describe('失败原因文案', () => {
  test('扫完无命中：给出固定网段与可执行建议', () => {
    const text = describeEndpointFailure('scan-no-service');
    expect(text).toContain('172.20.10.0/24');
    expect(text).toContain('后端已启动');
    expect(text).toContain('电脑已连接该热点');
  });

  test('请求全失败与扫完无命中是两个独立原因', () => {
    const noService = describeEndpointFailure('scan-no-service');
    const requestsFailed = describeEndpointFailure('scan-requests-failed');
    expect(noService).toContain('未在 172.20.10.0/24 找到服务');
    expect(requestsFailed).not.toBe(noService);
  });
});
