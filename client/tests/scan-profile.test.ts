import { detectPhonePlatform, getScanProfile } from '../core/ble/scan-profile';

const deviceInfo = wx as unknown as { getDeviceInfo?: () => { platform: string } };

describe('scan-profile：按机型选择聚合窗口', () => {
  afterEach(() => {
    delete deviceInfo.getDeviceInfo;
  });

  it('无法读取机型时回退 iOS（2s，保持历史行为）', () => {
    expect(detectPhonePlatform()).toBe('ios');
    expect(getScanProfile().windowMs).toBe(2000);
  });

  it('iOS 用 2s 窗口', () => {
    deviceInfo.getDeviceInfo = () => ({ platform: 'ios' });
    expect(detectPhonePlatform()).toBe('ios');
    expect(getScanProfile().windowMs).toBe(2000);
  });

  it('安卓用 1.5s 窗口', () => {
    deviceInfo.getDeviceInfo = () => ({ platform: 'android' });
    expect(detectPhonePlatform()).toBe('android');
    expect(getScanProfile().windowMs).toBe(1500);
  });
});
