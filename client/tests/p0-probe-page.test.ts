import type { CancelToken } from '../core/discovery/cancel-token';

const discover = jest.fn<Promise<{ url: string; identity: unknown } | null>, [string | null, CancelToken?]>();
const checkCandidate = jest.fn<Promise<boolean>, [string, string | null, CancelToken?]>();
const getCandidate = jest.fn<string | null, []>();
const getDeviceId = jest.fn<string | null, []>();
const saveCandidate = jest.fn<boolean, [string, string?]>();
jest.mock('../services/business-discovery', () => ({
  discoverBusinessCandidate: (deviceId: string | null, cancel?: CancelToken) => discover(deviceId, cancel),
}));
jest.mock('../services/p0-candidate', () => ({
  getP0Candidate: () => getCandidate(),
  getP0DeviceId: () => getDeviceId(),
  checkP0Candidate: (url: string, deviceId: string | null, cancel?: CancelToken) =>
    checkCandidate(url, deviceId, cancel),
  saveP0Candidate: (url: string, deviceId?: string) =>
    deviceId === undefined ? saveCandidate(url) : saveCandidate(url, deviceId),
}));

type ProbePage = {
  data: { busy: boolean; result: string };
  probeGeneration: number;
  probeCancel: { cancel(): void } | null;
  setData: (data: Record<string, unknown>) => void;
  onShow: () => void;
  onHide: () => void;
  onProbe: () => Promise<void>;
};

let page: ProbePage;
(globalThis as unknown as { Page: (definition: ProbePage) => void }).Page = (definition) => { page = definition; };
require('../pages/p0-probe/p0-probe');

beforeEach(() => {
  page.data = { busy: false, result: '尚未探测' };
  page.probeGeneration = 0;
  page.probeCancel = null;
  page.setData = (data) => { Object.assign(page.data, data); };
  discover.mockReset();
  checkCandidate.mockReset();
  getCandidate.mockReset();
  getDeviceId.mockReset();
  saveCandidate.mockReset();
  getCandidate.mockReturnValue('http://192.168.1.28:8000');
  getDeviceId.mockReturnValue(null);
  (globalThis as unknown as { wx: unknown }).wx = { showModal: jest.fn(), login: jest.fn() };
});

describe('P0 隔离探测页', () => {
  test('回前台并行发起候选检查与广播，缓存可达时忽略发现失败', async () => {
    let finish!: (value: boolean) => void;
    checkCandidate.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    discover.mockResolvedValue(null);
    page.onShow();
    await Promise.resolve();
    await Promise.resolve();
    expect(checkCandidate).toHaveBeenCalledWith('http://192.168.1.28:8000', null, expect.anything());
    expect(discover).toHaveBeenCalledTimes(1);
    finish(true);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(page.data.result).toContain('缓存地址的 /healthz 与设备身份校验通过');
    expect(saveCandidate).not.toHaveBeenCalled();
    expect((globalThis as unknown as { wx: { showModal: jest.Mock } }).wx.showModal).not.toHaveBeenCalled();
  });

  test('缓存可达后立即取消仍在进行的广播', async () => {
    let cancelled = false;
    discover.mockImplementation((_deviceId, cancel) => new Promise((resolve) => {
      cancel?.onCancel(() => {
        cancelled = true;
        resolve(null);
      });
    }));
    let finish!: (value: boolean) => void;
    checkCandidate.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    page.onShow();
    await Promise.resolve();
    await Promise.resolve();
    finish(true);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(cancelled).toBe(true);
    expect(page.data.result).toContain('缓存地址的 /healthz 与设备身份校验通过');
  });

  test('缓存不可达则等待发现结果，探测中不重复发起', async () => {
    let finish!: (result: { url: string; identity: unknown } | null) => void;
    discover.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    checkCandidate.mockResolvedValue(false);
    const pending = page.onProbe();
    await page.onProbe();
    expect(discover).toHaveBeenCalledTimes(1);
    finish({ url: 'http://192.168.1.44:8000', identity: {} });
    await pending;
    expect(page.data.result).toContain('发现地址已通过 /healthz 身份校验');
    expect(saveCandidate).toHaveBeenCalledWith('http://192.168.1.44:8000');
  });

  test('双路失败才弹窗，不触发微信登录', async () => {
    checkCandidate.mockResolvedValue(false);
    discover.mockResolvedValue(null);
    await page.onProbe();
    expect(page.data.result).toContain('均不可用');
    const wx = (globalThis as unknown as { wx: { showModal: jest.Mock; login: jest.Mock } }).wx;
    expect(wx.showModal).toHaveBeenCalledTimes(1);
    expect(wx.login).not.toHaveBeenCalled();
  });

  test('页面隐藏后迟到的旧网络结果不能显示或弹窗', async () => {
    let finish!: (result: { url: string; identity: unknown } | null) => void;
    checkCandidate.mockResolvedValue(false);
    discover.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const pending = page.onProbe();
    page.onHide();
    finish({ url: 'http://192.168.1.44:8000', identity: {} });
    await pending;
    expect(page.data.result).toBe('尚未探测');
    expect(saveCandidate).not.toHaveBeenCalled();
    expect((globalThis as unknown as { wx: { showModal: jest.Mock } }).wx.showModal).not.toHaveBeenCalled();
  });
});
