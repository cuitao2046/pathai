/**
 * service-endpoint：启动地址解析顺序（缓存优先 → 广播发现 → 双路失败返回 null），
 * 解析成功后供登录/业务/地图共用同一根地址，失败不静默回退旧 IP。
 */
import type * as ServiceEndpoint from '../services/service-endpoint';

interface CandidateMocks {
  getP0Candidate: jest.Mock;
  checkP0Candidate: jest.Mock;
  saveP0Candidate: jest.Mock;
  checkEndpointHealth: jest.Mock;
}

let candidate: CandidateMocks;
let discoverBusinessCandidate: jest.Mock;
let isDevtoolsRuntime: jest.Mock;

beforeEach(() => {
  jest.resetModules();
  candidate = {
    getP0Candidate: jest.fn(),
    checkP0Candidate: jest.fn(),
    saveP0Candidate: jest.fn(() => true),
    checkEndpointHealth: jest.fn(),
  };
  discoverBusinessCandidate = jest.fn();
  isDevtoolsRuntime = jest.fn(() => false);
  jest.doMock('../services/p0-candidate', () => candidate);
  jest.doMock('../services/business-discovery', () => ({ discoverBusinessCandidate }));
  jest.doMock('../services/runtime-platform', () => ({ isDevtoolsRuntime }));
});

function load(): typeof ServiceEndpoint {
  return require('../services/service-endpoint') as typeof ServiceEndpoint;
}

test('未解析时不再回退编译期地址：current 为 null，require 抛 ENDPOINT_UNRESOLVED', () => {
  const ep = load();
  expect(ep.resolvedServiceBaseUrl()).toBeNull();
  expect(ep.currentServiceBaseUrl()).toBeNull();
  expect(() => ep.requireServiceBaseUrl()).toThrow(
    expect.objectContaining({ code: 'ENDPOINT_UNRESOLVED' }),
  );
});

test('解析成功后 requireServiceBaseUrl 返回同一地址', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(true);
  const ep = load();
  await ep.ensureServiceEndpoint();
  expect(ep.requireServiceBaseUrl()).toBe('http://10.1.2.3:8000');
});

test('缓存可达：直接采用缓存地址，不再发起广播发现', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(true);
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBe('http://10.1.2.3:8000');
  expect(discoverBusinessCandidate).not.toHaveBeenCalled();
  expect(ep.currentServiceBaseUrl()).toBe('http://10.1.2.3:8000');
});

test('缓存不可达：采用发现地址并写入候选缓存', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(false);
  discoverBusinessCandidate.mockResolvedValue('http://10.9.9.9:8000');
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBe('http://10.9.9.9:8000');
  expect(candidate.saveP0Candidate).toHaveBeenCalledWith('http://10.9.9.9:8000');
  expect(ep.resolvedServiceBaseUrl()).toBe('http://10.9.9.9:8000');
});

test('双路失败：返回 null 且不把旧 IP 当作已解析地址', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(false);
  discoverBusinessCandidate.mockResolvedValue(null);
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBeNull();
  expect(ep.resolvedServiceBaseUrl()).toBeNull();
});

test('并发调用共享同一轮解析，只检查/发现一次', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(false);
  discoverBusinessCandidate.mockResolvedValue('http://10.9.9.9:8000');
  const ep = load();
  const [first, second] = await Promise.all([
    ep.ensureServiceEndpoint(),
    ep.ensureServiceEndpoint(),
  ]);
  expect(first).toBe('http://10.9.9.9:8000');
  expect(second).toBe('http://10.9.9.9:8000');
  expect(candidate.checkP0Candidate).toHaveBeenCalledTimes(1);
  expect(discoverBusinessCandidate).toHaveBeenCalledTimes(1);
});

test('解析成功后再次调用直接返回，不重复检查', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(true);
  const ep = load();
  await ep.ensureServiceEndpoint();
  await ep.ensureServiceEndpoint();
  expect(candidate.checkP0Candidate).toHaveBeenCalledTimes(1);
});

test('首轮失败后可重试：下一轮重新执行发现', async () => {
  candidate.getP0Candidate.mockReturnValue('http://10.1.2.3:8000');
  candidate.checkP0Candidate.mockResolvedValue(false);
  discoverBusinessCandidate.mockResolvedValueOnce(null).mockResolvedValueOnce('http://10.9.9.9:8000');
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBeNull();
  expect(await ep.ensureServiceEndpoint()).toBe('http://10.9.9.9:8000');
});

test('真机双路失败：记录局域网未发现服务，不采用编译期地址', async () => {
  candidate.getP0Candidate.mockReturnValue(null);
  discoverBusinessCandidate.mockResolvedValue(null);
  const ep = load();
  expect(await ep.ensureServiceEndpoint()).toBeNull();
  expect(ep.lastEndpointFailure()).toBe('lan-service-not-found');
  expect(candidate.checkEndpointHealth).not.toHaveBeenCalled();
});

describe('开发者工具模拟器专用路径', () => {
  test('本机回环可达：直接采用 127.0.0.1 + 后端端口，不查缓存、不广播、不写缓存', async () => {
    isDevtoolsRuntime.mockReturnValue(true);
    candidate.checkEndpointHealth.mockResolvedValue(true);
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBe('http://127.0.0.1:8000');
    expect(candidate.checkEndpointHealth).toHaveBeenCalledWith('http://127.0.0.1:8000');
    expect(candidate.getP0Candidate).not.toHaveBeenCalled();
    expect(candidate.checkP0Candidate).not.toHaveBeenCalled();
    expect(candidate.saveP0Candidate).not.toHaveBeenCalled();
    expect(discoverBusinessCandidate).not.toHaveBeenCalled();
    expect(ep.lastEndpointFailure()).toBeNull();
  });

  test('本机回环不可达：返回 null 并标记模拟器后端不可达，不发起 UDP 广播', async () => {
    isDevtoolsRuntime.mockReturnValue(true);
    candidate.checkEndpointHealth.mockResolvedValue(false);
    const ep = load();
    expect(await ep.ensureServiceEndpoint()).toBeNull();
    expect(ep.lastEndpointFailure()).toBe('devtools-backend-unreachable');
    expect(discoverBusinessCandidate).not.toHaveBeenCalled();
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
