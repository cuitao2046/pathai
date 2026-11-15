import { isDevtoolsRuntime } from '../services/runtime-platform';

describe('运行宿主判定', () => {
  afterEach(() => {
    delete (globalThis as unknown as { wx?: unknown }).wx;
  });

  test('platform = devtools 视为模拟器', () => {
    (globalThis as unknown as { wx: unknown }).wx = { getDeviceInfo: () => ({ platform: 'devtools' }) };
    expect(isDevtoolsRuntime()).toBe(true);
  });

  test('真机（ios/android）不走模拟器路径', () => {
    (globalThis as unknown as { wx: unknown }).wx = { getDeviceInfo: () => ({ platform: 'ios' }) };
    expect(isDevtoolsRuntime()).toBe(false);
  });

  test('缺少 getDeviceInfo 时退回 getSystemInfoSync', () => {
    (globalThis as unknown as { wx: unknown }).wx = { getSystemInfoSync: () => ({ platform: 'devtools' }) };
    expect(isDevtoolsRuntime()).toBe(true);
  });

  test('运行环境异常时按真机处理，不误用回环地址', () => {
    (globalThis as unknown as { wx: unknown }).wx = {
      getDeviceInfo: () => { throw new Error('unsupported'); },
      getSystemInfoSync: () => { throw new Error('unsupported'); },
    };
    expect(isDevtoolsRuntime()).toBe(false);
  });
});
