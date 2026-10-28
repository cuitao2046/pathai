/**
 * MapService：首次下载与校验、304 短路 + 数据 sha 命中免下载、
 * 摘要不符拒绝入库、联调重写下载源、未加载守卫。
 * mock 数据使用真实地图包 2026-10-25.1（56 地点 / 61 信标）。
 */
import { MapService } from '../core/map/map-service';
import { createHttpStack } from '../core/http/api-client';
import { sha256 } from '../utils/sha256';
import { getJson, getString, remove } from '../utils/kv';
import { wxStub } from './setup-wx';
import { REAL_MAP, REAL_UUID } from './fixtures/real-map';
import { API_BASE_URL } from '../config/env';

// 与生产重写逻辑同源（map-service 用 env.ts 的 API_BASE_URL 拼 dataUrl）
const BASE = API_BASE_URL;
const SITE = 'school-building-01';
const SUMMARY_URL = `${BASE}/api/v1/sites/${SITE}/map`;
const REWRITE_DATA_URL = `${BASE}/api/v1/sites/${SITE}/map/data?version=${REAL_MAP.mapVersion}`;
const CDN_URL = 'https://cdn.example.com/maps/school.json';
const LOGIN_URL = `${BASE}/api/v1/auth/wechat`;

const PKG = REAL_MAP;
const RAW = JSON.stringify(PKG);
const SHA = sha256(RAW);

const SUMMARY = {
  siteId: SITE,
  mapVersion: REAL_MAP.mapVersion,
  publishedAt: REAL_MAP.publishedAt,
  coordinateSystem: { unit: 'm', originDescription: '场馆局部坐标', headingReference: 'map_north' },
  floors: [
    { floorId: '1', displayName: '一楼', level: 1 },
    { floorId: '2', displayName: '二楼', level: 2 },
  ],
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
  wxStub.__files.clear();
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
  // kv 模块级内存降级缓存跨测试存活，清除地图相关键
  remove(`map:summary:${SITE}`);
  remove(`map:summary-etag:${SITE}`);
  remove('auth:token');
  summaryServed = 0;
  dataServed = 0;
  dataBody = RAW;
  installResponder();
});

const CACHE_FILE = `${wxStub.env.USER_DATA_PATH}/map-cache-${SITE}.json`;

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
  expect(service.requireVersion()).toBe(REAL_MAP.mapVersion);
  expect(summaryServed).toBe(1);
  expect(dataServed).toBe(1);
  // 数据走服务端 dataUrl（未开重写）
  const dataCall = wxStub.__requestLog.find((r) => r.url === CDN_URL);
  expect(dataCall).toBeDefined();
  // ETag 入库供下次 304 短路
  expect(getString(`map:summary-etag:${SITE}`)).toBe('etag-1');
  // 注册表可用（真实三元组）
  expect(service.beaconRegistry.resolve(REAL_UUID.toLowerCase(), 1, 10101)).toBe('BK-01-001');
  expect(service.places.length).toBe(REAL_MAP.places.length);
  // 数据本体落文件缓存（storage 单 key 1MB 上限放不下 4.6MB 地图包）
  expect(wxStub.__fileText(CACHE_FILE)).toBe(RAW);
});

test('二次加载：304 复用缓存摘要 + 数据 sha 命中不再下载', async () => {
  const first = newService();
  await first.ensureLoaded();
  expect(dataServed).toBe(1);

  const second = newService();
  await second.ensureLoaded();
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
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
  // 校验失败发生在写缓存之前：文件不落地
  expect(wxStub.__files.has(CACHE_FILE)).toBe(false);
  expect(service.isLoaded).toBe(false);
});

test('联调开关：下载源重写为 API 同源且校验不关闭', async () => {
  const service = newService({ rewrite: true });
  await service.ensureLoaded();
  const dataCall = wxStub.__requestLog.find((r) => r.url === REWRITE_DATA_URL);
  expect(dataCall).toBeDefined();
  expect(wxStub.__requestLog.find((r) => r.url === CDN_URL)).toBeUndefined();
  expect(service.requireVersion()).toBe(REAL_MAP.mapVersion);

  // 重写模式同样执行摘要校验
  dataBody = 'tampered';
  wxStub.__files.clear();
  remove(`map:summary:${SITE}`);
  remove(`map:summary-etag:${SITE}`);
  const bad = newService({ rewrite: true });
  await expect(bad.ensureLoaded()).rejects.toMatchObject({ code: 'MAP_UNAVAILABLE' });
});

test('requirePlace 命中与未命中', async () => {
  const service = newService();
  await service.ensureLoaded();
  expect(service.requirePlace('F1-RM-0003').name).toBe('资源教室');
  expect(() => service.requirePlace('P-NONE')).toThrowError();
});

test('服务端新版本：本会话沿用缓存版，后台异步覆盖本地缓存', async () => {
  // 首次加载（旧版）：文件缓存 + 摘要 + ETag 落地
  const first = newService();
  await first.ensureLoaded();
  expect(wxStub.__fileText(CACHE_FILE)).toBe(RAW);

  // 服务端发布新版本：新摘要（etag-2）+ 新数据
  const NEW_RAW = JSON.stringify({ ...PKG, mapVersion: '2026-10-26.1' });
  const NEW_SHA = sha256(NEW_RAW);
  dataBody = NEW_RAW;
  wxStub.__requestResponder = (o) => {
    if (o.url === LOGIN_URL) {
      o.success({ statusCode: 200, header: {}, data: { accessToken: 'tok-1', tokenType: 'Bearer', expiresIn: 7200, userId: 'anon_1' } });
      return;
    }
    if (o.url === SUMMARY_URL) {
      summaryServed += 1;
      o.success({
        statusCode: 200,
        header: { ETag: 'etag-2' },
        data: { ...SUMMARY, mapVersion: '2026-10-26.1', dataSha256: NEW_SHA },
      });
      return;
    }
    if (o.url === CDN_URL || o.url === REWRITE_DATA_URL) {
      dataServed += 1;
      o.success({ statusCode: 200, header: { ETag: 'etag-2' }, data: dataBody });
      return;
    }
    o.success({ statusCode: 404, header: {}, data: {} });
  };

  const second = newService();
  await second.ensureLoaded();
  // 本会话不热替换：仍用已就绪的缓存版本（避免导航会话中途换图）
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
  expect(second.places.length).toBe(REAL_MAP.places.length);

  // 后台下载完成后：文件被新版本覆盖，摘要/ETag 同步刷新（下次启动配对生效）
  for (let i = 0; i < 50 && wxStub.__fileText(CACHE_FILE) !== NEW_RAW; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  expect(wxStub.__fileText(CACHE_FILE)).toBe(NEW_RAW);
  expect(getJson<{ mapVersion: string }>(`map:summary:${SITE}`)?.mapVersion).toBe('2026-10-26.1');
  expect(getString(`map:summary-etag:${SITE}`)).toBe('etag-2');
  // 本会话版本保持不变
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
});

test('并发 ensureLoaded 单飞：只加载一次', async () => {
  const service = newService();
  await Promise.all([service.ensureLoaded(), service.ensureLoaded()]);
  expect(summaryServed).toBe(1);
  expect(dataServed).toBe(1);
});
