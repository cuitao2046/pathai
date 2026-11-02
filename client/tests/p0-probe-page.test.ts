const discover = jest.fn<Promise<string | null>, []>();
const checkCandidate = jest.fn<Promise<boolean>, [string]>();
const getCandidate = jest.fn<string | null, []>();
const saveCandidate = jest.fn<boolean, [string]>();
jest.mock('../services/business-discovery', () => ({ discoverBusinessCandidate: () => discover() }));
jest.mock('../services/p0-candidate', () => ({
  getP0Candidate: () => getCandidate(),
  checkP0Candidate: (url: string) => checkCandidate(url),
  saveP0Candidate: (url: string) => saveCandidate(url),
}));

type ProbePage = {
  data: { busy: boolean; result: string };
  probeGeneration: number;
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
  page.setData = (data) => { Object.assign(page.data, data); };
  discover.mockReset();
  checkCandidate.mockReset();
  getCandidate.mockReset();
  saveCandidate.mockReset();
  getCandidate.mockReturnValue('http://192.168.1.28:8000');
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
    expect(checkCandidate).toHaveBeenCalledWith('http://192.168.1.28:8000');
    expect(discover).toHaveBeenCalledTimes(1);
    finish(true);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(page.data.result).toContain('缓存地址的业务端口 HTTP 可达');
    expect(saveCandidate).not.toHaveBeenCalled();
    expect((globalThis as unknown as { wx: { showModal: jest.Mock } }).wx.showModal).not.toHaveBeenCalled();
  });

  test('缓存不可达则等待发现结果，探测中不重复发起', async () => {
    let finish!: (result: string | null) => void;
    discover.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    checkCandidate.mockResolvedValue(false);
    const pending = page.onProbe();
    await page.onProbe();
    expect(discover).toHaveBeenCalledTimes(1);
    finish('http://192.168.1.44:8000');
    await pending;
    expect(page.data.result).toContain('发现地址的业务端口 HTTP 可达');
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
    let finish!: (result: string | null) => void;
    checkCandidate.mockResolvedValue(false);
    discover.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    const pending = page.onProbe();
    page.onHide();
    finish('http://192.168.1.44:8000');
    await pending;
    expect(page.data.result).toBe('尚未探测');
    expect(saveCandidate).not.toHaveBeenCalled();
    expect((globalThis as unknown as { wx: { showModal: jest.Mock } }).wx.showModal).not.toHaveBeenCalled();
  });
});
