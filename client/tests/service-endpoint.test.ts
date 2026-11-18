/**
 * service-endpoint：三态发现策略（自动 / 仅广播 / 仅扫描）、设备身份校验、
 * 运行期失效恢复与模拟器回环路径。失败不回退编译期地址。
 */
import type { CancelToken } from '../core/discovery/cancel-token';
import type * as ServiceEndpoint from '../services/service-endpoint';

const DEVICE_A = 'srv-' + 'a'.repeat(32);
const DEVICE_B = 'srv-' + 'b'.repeat(32);

interface CandidateMocks {
  getP0Candidate: jest.Mock;
  getP0DeviceId: jest.Mock;
  probeEndpoint: jest.Mock;
  saveP0Candidate: jest.Mock;
  checkEndpointHealth: jest.Mock;
}

let candidate: CandidateMocks;
let discoverBusinessCandidate: jest.Mock;
let scanLanForService: jest.Mock;
let getDiscoveryMode: jest.Mock;
let isDevtoolsRuntime: jest.Mock;

function identity(deviceId = DEVICE_A) {
  return { service: 'my-backend', deviceId, version: '0.1.0', apiVersion: 'v1' };
}

function identified(deviceId = DEVICE_A) {
  return { kind: 'identified', identity: identity(deviceId) };
}

function scanned(url: string, deviceId = DEVICE_A) {
  return {
    service: { url, identity: identity(deviceId) },
    reason: null,
    targetCount: 10,
    responded: 1,
  };
}

beforeEach(() => {
  jest.resetModules();
  candidate = {
    getP0Candidate: jest.fn(() => null),
    getP0DeviceId: jest.fn(() => null),
    probeEndpoint: jest.fn(async () => identified()),
    saveP0Candidate: jest.fn(() => true),
    checkEndpointHealth: jest.fn(async () => false),
  };
  discoverBusinessCandidate = jest.fn(async () => null);
  scanLanForService = jest.fn(async () => ({
    service: null,
    reason: 'scan-no-service',
    targetCount: 0,
    responded: 0,
  }));
  getDiscoveryMode = jest.fn(() => 'auto');
  isDevtoolsRuntime = jest.fn(() => false);
  jest.doMock('../services/p0-candidate', () => candidate);
  jest.doMock('../services/business-discovery', () => ({ discoverBusinessCandidate }));
  jest.doMock('../services/http-discovery', () => ({ scanLanForService }));
  jest.doMock('../services/discovery-mode', () => ({ getDiscoveryMode }));
  jest.doMock('../services/runtime-platform', () => ({ isDevtoolsRuntime }));
});

function load(): typeof ServiceEndpoint {
  return require('../services/service-endpoint') as typeof ServiceEndpoint;
}

const OLD = 'http://10.1.2.3:8000';
const NEXT = 'http://10.9.9.9:8000';

test('运行期地址失效后重新发现，不再永久复用旧 IP', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  const ep = load();
  await ep.ensureServiceEndpoint();
  candidate.probeEndpoint.mockResolvedValueOnce({ kind: 'unreachable' });
  discoverBusinessCandidate.mockResolvedValue({ url: NEXT, identity: identity() });
  await ep.recoverServiceEndpoint(OLD);
  expect(ep.currentServiceBaseUrl()).toBe(NEXT);
  await ep.recoverServiceEndpoint(OLD); // Late failure from the old generation.
  expect(ep.currentServiceBaseUrl()).toBe(NEXT);
});

test('旧发现任务迟到完成不能覆盖新一轮地址', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  let complete!: (value: unknown) => void;
  candidate.probeEndpoint.mockReturnValueOnce(new Promise(resolve => { complete = resolve; }));
  const ep = load();
  const old = ep.ensureServiceEndpoint();
  ep.invalidateServiceEndpoint();
  candidate.getP0Candidate.mockReturnValue(NEXT);
  candidate.probeEndpoint.mockResolvedValue(identified());
  expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
  complete({ kind: 'identified', identity: identity() });
  expect(await old).toBeNull();
  expect(ep.currentServiceBaseUrl()).toBe(NEXT);
});

test('并发连接故障共享一次恢复发现', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  const ep = load();
  await ep.ensureServiceEndpoint();
  candidate.probeEndpoint.mockResolvedValue({ kind: 'unreachable' });
  discoverBusinessCandidate.mockResolvedValue({ url: NEXT, identity: identity() });
  await Promise.all([ep.recoverServiceEndpoint(OLD), ep.recoverServiceEndpoint(OLD)]);
  expect(discoverBusinessCandidate).toHaveBeenCalledTimes(1);
});

test('未解析时不再回退编译期地址：current 为 null，require 抛 ENDPOINT_UNRESOLVED', () => {
  const ep = load();
  expect(ep.resolvedServiceBaseUrl()).toBeNull();
  expect(ep.currentServiceBaseUrl()).toBeNull();
  expect(() => ep.requireServiceBaseUrl()).toThrow(
    expect.objectContaining({ code: 'ENDPOINT_UNRESOLVED' }),
  );
});

test('缓存可达：直接采用缓存地址并补写设备身份，不发起广播', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBe(OLD);
  expect(candidate.saveP0Candidate).toHaveBeenCalledWith(OLD, DEVICE_A);
  expect(discoverBusinessCandidate).not.toHaveBeenCalled();
  expect(scanLanForService).not.toHaveBeenCalled();
});

test('缓存命中但 device_id 变化：视为换机并重新发现', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  candidate.getP0DeviceId.mockReturnValue(DEVICE_A);
  candidate.probeEndpoint.mockResolvedValue(identified(DEVICE_B));
  discoverBusinessCandidate.mockResolvedValue({ url: NEXT, identity: identity(DEVICE_B) });
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
  expect(discoverBusinessCandidate).toHaveBeenCalledWith(DEVICE_A, expect.anything());
});

describe('三态策略顺序', () => {
  test('自动：缓存失败 → 广播成功，不进入扫描', async () => {
    candidate.getP0Candidate.mockReturnValue(OLD);
    candidate.probeEndpoint.mockResolvedValueOnce({ kind: 'unreachable' });
    discoverBusinessCandidate.mockResolvedValue({ url: NEXT, identity: identity() });
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
    expect(candidate.saveP0Candidate).toHaveBeenCalledWith(NEXT, DEVICE_A);
    expect(scanLanForService).not.toHaveBeenCalled();
  });

  test('自动：广播失败后继续扫描并命中', async () => {
    getDiscoveryMode.mockReturnValue('auto');
    discoverBusinessCandidate.mockResolvedValue(null);
    scanLanForService.mockResolvedValue(scanned(NEXT));
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
    expect(scanLanForService).toHaveBeenCalledWith(null, expect.anything());
    expect(candidate.saveP0Candidate).toHaveBeenCalledWith(NEXT, DEVICE_A);
  });

  test('仅广播：广播失败即结束，不扫描', async () => {
    getDiscoveryMode.mockReturnValue('broadcast');
    discoverBusinessCandidate.mockResolvedValue(null);
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBeNull();
    expect(scanLanForService).not.toHaveBeenCalled();
    expect(ep.lastEndpointFailure()).toBe('lan-service-not-found');
  });

  test('仅扫描：跳过广播，失败原因保留扫描细分原因', async () => {
    getDiscoveryMode.mockReturnValue('scan');
    scanLanForService.mockResolvedValue({
      service: null,
      reason: 'scan-no-service',
      targetCount: 0,
      responded: 0,
    });
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBeNull();
    expect(discoverBusinessCandidate).not.toHaveBeenCalled();
    expect(ep.lastEndpointFailure()).toBe('scan-no-service');
  });

  test('自动全失败：上报扫描的具体失败原因', async () => {
    discoverBusinessCandidate.mockResolvedValue(null);
    scanLanForService.mockResolvedValue({
      service: null,
      reason: 'scan-requests-failed',
      targetCount: 10,
      responded: 0,
    });
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBeNull();
    expect(ep.lastEndpointFailure()).toBe('scan-requests-failed');
  });

  test('扫描命中后保存地址与设备身份', async () => {
    getDiscoveryMode.mockReturnValue('scan');
    scanLanForService.mockResolvedValue(scanned(NEXT, DEVICE_B));
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
    expect(candidate.saveP0Candidate).toHaveBeenCalledWith(NEXT, DEVICE_B);
  });

  test('地址失效时立即中止正在进行的广播', async () => {
    let cancelled = false;
    discoverBusinessCandidate.mockImplementation((_id: unknown, cancel: CancelToken) =>
      new Promise((resolve) => {
        cancel.onCancel(() => {
          cancelled = true;
          resolve(null);
        });
      }));
    const ep = load();
    const pending = ep.ensureServiceEndpoint();
    ep.invalidateServiceEndpoint();
    expect(await pending).toBeNull();
    expect(cancelled).toBe(true);
  });

  test('地址失效时立即中止正在进行的扫描', async () => {
    getDiscoveryMode.mockReturnValue('scan');
    let cancelled = false;
    scanLanForService.mockImplementation((_id: unknown, cancel: CancelToken) =>
      new Promise((resolve) => {
        cancel.onCancel(() => {
          cancelled = true;
          resolve({ service: null, reason: 'scan-requests-failed', targetCount: 254, responded: 0 });
        });
      }));
    const ep = load();
    const pending = ep.ensureServiceEndpoint();
    await new Promise((resolve) => setTimeout(resolve, 0));
    ep.invalidateServiceEndpoint();
    expect(await pending).toBeNull();
    expect(cancelled).toBe(true);
  });
});

test('并发调用共享同一轮解析，只检查/发现一次', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  candidate.probeEndpoint.mockResolvedValue({ kind: 'unreachable' });
  discoverBusinessCandidate.mockResolvedValue({ url: NEXT, identity: identity() });
  const ep = load();
  const [first, second] = await Promise.all([
    ep.ensureServiceEndpoint(),
    ep.ensureServiceEndpoint(),
  ]);
  expect(first).toBe(NEXT);
  expect(second).toBe(NEXT);
  expect(candidate.probeEndpoint).toHaveBeenCalledTimes(1);
  expect(discoverBusinessCandidate).toHaveBeenCalledTimes(1);
});

test('解析成功后再次调用直接返回，不重复检查', async () => {
  candidate.getP0Candidate.mockReturnValue(OLD);
  const ep = load();
  await ep.ensureServiceEndpoint();
  await ep.ensureServiceEndpoint();
  expect(candidate.probeEndpoint).toHaveBeenCalledTimes(1);
});

test('首轮失败后可重试：下一轮重新执行发现', async () => {
  discoverBusinessCandidate
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce({ url: NEXT, identity: identity() });
  scanLanForService.mockResolvedValue({
    service: null,
    reason: 'scan-no-service',
    targetCount: 0,
    responded: 1,
  });
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBeNull();
  expect(await ep.ensureServiceEndpoint()).toBe(NEXT);
});

describe('开发者工具模拟器专用路径', () => {
  test('本机回环可达：直接采用回环地址，不查缓存、不广播、不扫描、不写缓存', async () => {
    isDevtoolsRuntime.mockReturnValue(true);
    candidate.checkEndpointHealth.mockResolvedValue(true);
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBe('http://127.0.0.1:8000');
    expect(candidate.checkEndpointHealth).toHaveBeenCalledWith('http://127.0.0.1:8000');
    expect(candidate.getP0Candidate).not.toHaveBeenCalled();
    expect(candidate.saveP0Candidate).not.toHaveBeenCalled();
    expect(discoverBusinessCandidate).not.toHaveBeenCalled();
    expect(scanLanForService).not.toHaveBeenCalled();
    expect(ep.lastEndpointFailure()).toBeNull();
  });

  test('本机回环不可达：返回 null 并标记模拟器后端不可达，不回退广播/扫描', async () => {
    isDevtoolsRuntime.mockReturnValue(true);
    candidate.checkEndpointHealth.mockResolvedValue(false);
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBeNull();
    expect(ep.lastEndpointFailure()).toBe('devtools-backend-unreachable');
    expect(discoverBusinessCandidate).not.toHaveBeenCalled();
    expect(scanLanForService).not.toHaveBeenCalled();
    expect(ep.resolvedServiceBaseUrl()).toBeNull();
  });

  test('解析成功后再次调用复用结果，不重复探活', async () => {
    isDevtoolsRuntime.mockReturnValue(true);
    candidate.checkEndpointHealth.mockResolvedValue(true);
    const ep = load();
    await ep.ensureServiceEndpoint();
    await ep.ensureServiceEndpoint();
    expect(candidate.checkEndpointHealth).toHaveBeenCalledTimes(1);
  });
});
