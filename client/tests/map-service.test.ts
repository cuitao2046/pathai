/**
 * MapService：首次下载与校验、304 短路 + 数据 sha 命中免下载、
 * 摘要不符拒绝入库、联调重写下载源、未加载守卫。
 */
import { MapService } from '../core/map/map-service';
import { createHttpStack } from '../core/http/api-client';
import { sha256 } from '../utils/sha256';
import { getString, remove } from '../utils/kv';
import { wxStub } from './setup-wx';

const BASE = 'http://localhost:8000';
const SITE = 'school-building-01';
const SUMMARY_URL = `${BASE}/api/v1/sites/${SITE}/map`;
const REWRITE_DATA_URL = `${BASE}/api/v1/sites/${SITE}/map/data?version=v2026.10.1`;
const CDN_URL = 'https://cdn.example.com/maps/school.json';
const LOGIN_URL = `${BASE}/api/v1/auth/wechat`;

const PKG = {
  siteId: SITE,
  mapVersion: 'v2026.10.1',
  publishedAt: '2026-10-01T00:00:00Z',
  places: [
    {
      placeId: 'P-CLASS-1',
      name: '一楼教室',
      spokenName: '一楼教室',
      aliases: ['教室'],
      type: 'classroom',
      floorId: '1',
      nodeId: 'F1-N1',
      accessible: true,
    },
  ],
  beacons: [
    {
      beaconId: 'BK-1',
      floorId: '1',
      x: 5,
      y: 5,
      uuid: 'FDA50693-A4E2-4FB1-AFCF-C6EB07647825',
      major: 1,
      minor: 1,
    },
    {
      beaconId: 'BK-2',
      floorId: '1',
      x: 10,
      y: 10,
      uuid: 'FDA50693-A4E2-4FB1-AFCF-C6EB07647825',
      major: 1,
      minor: 2,
    },
  ],
};
const RAW = JSON.stringify(PKG);
const SHA = sha256(RAW);

const SUMMARY = {
  siteId: SITE,
  mapVersion: 'v2026.10.1',
  publishedAt: '2026-10-01T00:00:00Z',
  coordinateSystem: { unit: 'm', originDescription: '西南角原点', headingReference: '北' },
  floors: [{ floorId: '1', displayName: '一层', level: 1 }],
  dataUrl: CDN_URL,
  dataSha256: SHA,
};

let summaryServed = 0;
let dataServed = 0;
/** data 端点返回内容（测试可改为篡改值）。 */
let dataBody: string = RAW;

function installResponder(): void {
  wxStub.__requestResponder = (o) => {
    if (o.url === LOGIN_URL) {
      o.success({
        statusCode: 200,
        header: {},
        data: { accessToken: 'tok-1', tokenType: 'Bearer', expiresIn: 7200, userId: 'anon_1' },
      });
      return;
    }
    if (o.url === SUMMARY_URL) {
      summaryServed += 1;
      if (o.header?.['If-None-Match'] === 'etag-1') {
        o.success({ statusCode: 304, header: { ETag: 'etag-1' }, data: '' });
      } else {
        o.success({
          statusCode: 200,
          header: { ETag: 'etag-1', 'X-Request-Id': 'req_map' },
          data: SUMMARY,
        });
      }
      return;
    }
    if (o.url === CDN_URL || o.url === REWRITE_DATA_URL) {
      dataServed += 1;
      o.success({ statusCode: 200, header: { ETag: 'etag-1' }, data: dataBody });
      return;
    }
    o.success({ statusCode: 404, header: {}, data: {} });
  };
}

function newService(overrides?: Partial<{ rewrite: boolean }>): MapService {
  const { client } = createHttpStack(BASE);
  return new MapService({
    client,
    siteId: SITE,
    rewriteDataUrlToLocalApi: overrides?.rewrite ?? false,
  });
}

beforeEach(() => {
  wxStub.__storage.clear();
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
  // kv 模块级内存降级缓存跨测试存活，清除地图相关键
  remove(`map:summary:${SITE}`);
  remove(`map:summary-etag:${SITE}`);
  remove(`map:data:${SITE}`);
  remove('auth:token');
  summaryServed = 0;
  dataServed = 0;
  dataBody = RAW;
  installResponder();
});

test('未加载时 require* 守卫抛 MAP_UNAVAILABLE', () => {
  const service = newService();
  expect(() => service.requireVersion()).toThrowError();
  expect(() => service.beaconRegistry).toThrowError();
  expect(service.isLoaded).toBe(false);
});

test('首次加载：下载校验入库，摘要 ETag 持久化', async () => {
  const service = newService();
  await service.ensureLoaded();
  expect(service.isLoaded).toBe(true);
  expect(service.requireVersion()).toBe('v2026.10.1');
  expect(summaryServed).toBe(1);
  expect(dataServed).toBe(1);
  // 数据走服务端 dataUrl（未开重写）
  const dataCall = wxStub.__requestLog.find((r) => r.url === CDN_URL);
  expect(dataCall).toBeDefined();
  // ETag 入库供下次 304 短路
  expect(getString(`map:summary-etag:${SITE}`)).toBe('etag-1');
  // 注册表可用
  expect(service.beaconRegistry.resolve('fda50693-a4e2-4fb1-afcf-c6eb07647825', 1, 2)).toBe('BK-2');
  expect(service.places.length).toBe(1);
});

test('二次加载：304 复用缓存摘要 + 数据 sha 命中不再下载', async () => {
  const first = newService();
  await first.ensureLoaded();
  expect(dataServed).toBe(1);

  const second = newService();
  await second.ensureLoaded();
  expect(second.requireVersion()).toBe('v2026.10.1');
  expect(summaryServed).toBe(2); // 摘要刷新一次（304）
  expect(dataServed).toBe(1); // 数据未重新下载
  const summaryCall = wxStub.__requestLog.filter((r) => r.url === SUMMARY_URL);
  expect(summaryCall[1]?.header?.['If-None-Match']).toBe('etag-1');
});

test('数据摘要不符：拒绝入库并抛 MAP_UNAVAILABLE', async () => {
  dataBody = JSON.stringify({ ...PKG, mapVersion: 'tampered' });
  const service = newService();
  await expect(service.ensureLoaded()).rejects.toMatchObject({
    name: 'ApiClientError',
    code: 'MAP_UNAVAILABLE',
  });
  expect(getString(`map:data:${SITE}`)).toBeNull();
  expect(service.isLoaded).toBe(false);
});

test('联调开关：下载源重写为 API 同源且校验不关闭', async () => {
  const service = newService({ rewrite: true });
  await service.ensureLoaded();
  const dataCall = wxStub.__requestLog.find((r) => r.url === REWRITE_DATA_URL);
  expect(dataCall).toBeDefined();
  expect(wxStub.__requestLog.find((r) => r.url === CDN_URL)).toBeUndefined();
  expect(service.requireVersion()).toBe('v2026.10.1');

  // 重写模式同样执行摘要校验
  dataBody = 'tampered';
  remove(`map:data:${SITE}`);
  const bad = newService({ rewrite: true });
  await expect(bad.ensureLoaded()).rejects.toMatchObject({ code: 'MAP_UNAVAILABLE' });
});

test('requirePlace 命中与未命中', async () => {
  const service = newService();
  await service.ensureLoaded();
  expect(service.requirePlace('P-CLASS-1').name).toBe('一楼教室');
  expect(() => service.requirePlace('P-NONE')).toThrowError();
});

test('并发 ensureLoaded 单飞：只加载一次', async () => {
  const service = newService();
  await Promise.all([service.ensureLoaded(), service.ensureLoaded()]);
  expect(summaryServed).toBe(1);
  expect(dataServed).toBe(1);
});
