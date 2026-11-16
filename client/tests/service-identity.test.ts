import { isDeviceId, parseServiceIdentity } from '../core/discovery/service-identity';

const DEVICE = 'srv-' + 'a'.repeat(32);

function body(overrides: Record<string, unknown> = {}) {
  return {
    status: 'ok',
    service: 'my-backend',
    device_id: DEVICE,
    version: '0.1.0',
    api_version: 'v1',
    ...overrides,
  };
}

describe('服务身份校验', () => {
  test('合法 /healthz 响应解析出设备身份与版本元数据', () => {
    expect(parseServiceIdentity(200, body())).toEqual({
      service: 'my-backend',
      deviceId: DEVICE,
      version: '0.1.0',
      apiVersion: 'v1',
    });
  });

  test('version / api_version 缺失不影响身份判定', () => {
    expect(parseServiceIdentity(200, body({ version: undefined, api_version: undefined }))).toEqual({
      service: 'my-backend',
      deviceId: DEVICE,
      version: null,
      apiVersion: null,
    });
  });

  test('非 200、服务名不符、device_id 非法均不认作目标服务', () => {
    expect(parseServiceIdentity(500, body())).toBeNull();
    expect(parseServiceIdentity(200, body({ status: 'not-ready' }))).toBeNull();
    expect(parseServiceIdentity(200, body({ service: 'other-service' }))).toBeNull();
    expect(parseServiceIdentity(200, body({ device_id: '' }))).toBeNull();
    expect(parseServiceIdentity(200, body({ device_id: '192.168.1.44' }))).toBeNull();
    expect(parseServiceIdentity(200, null)).toBeNull();
  });

  test('device_id 使用稳定格式', () => {
    expect(isDeviceId(DEVICE)).toBe(true);
    expect(isDeviceId('srv-1234')).toBe(false);
    expect(isDeviceId(undefined)).toBe(false);
  });
});
