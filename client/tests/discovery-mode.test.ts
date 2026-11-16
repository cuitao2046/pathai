import { getDiscoveryMode, setDiscoveryMode } from '../services/discovery-mode';

describe('服务发现方式三态设置', () => {
  const storage = new Map<string, string>();

  beforeEach(() => {
    storage.clear();
    (globalThis as unknown as { wx: unknown }).wx = {
      getStorageSync: (key: string) => storage.get(key),
      setStorageSync: (key: string, value: string) => storage.set(key, value),
      removeStorageSync: (key: string) => storage.delete(key),
    };
  });

  test('默认自动', () => {
    expect(getDiscoveryMode()).toBe('auto');
  });

  test('可持久化仅广播 / 仅扫描', () => {
    expect(setDiscoveryMode('broadcast')).toBe(true);
    expect(getDiscoveryMode()).toBe('broadcast');
    expect(setDiscoveryMode('scan')).toBe(true);
    expect(getDiscoveryMode()).toBe('scan');
  });

  test('损坏或未知值回落到自动', () => {
    storage.set('pathai:cfg:discovery-mode', 'unknown');
    expect(getDiscoveryMode()).toBe('auto');
  });
});
