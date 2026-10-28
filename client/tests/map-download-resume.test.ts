/**
 * 地图下载状态与断点续传（REQ-MAP）：中断续传、版本切换丢弃半成品、
 * 过旧判定与静默后台下载、无缓存失败语义、Content-Range 解析。
 * mock 数据使用真实地图包 2026-10-25.1。
 */
import {
  MapService,
  isVersionStale,
  parseContentRange,
  type MapServiceEvent,
} from '../core/map/map-service';
import { createHttpStack } from '../core/http/api-client';
import { sha256, utf8Bytes } from '../utils/sha256';
import { getJson, remove, setJson } from '../utils/kv';
import { partialPath, appendMapPartial } from '../utils/map-cache';
import { wxStub } from './setup-wx';
import { REAL_MAP } from './fixtures/real-map';
import { API_BASE_URL, MAP_DOWNLOAD_CHUNK_BYTES } from '../config/env';

const SITE = 'school-building-01';
const BASE = API_BASE_URL;
const SUMMARY_URL = `${BASE}/api/v1/sites/${SITE}/map`;
const DATA_URL_PREFIX = `${BASE}/api/v1/sites/${SITE}/map/data`;
const LOGIN_URL = `${BASE}/api/v1/auth/wechat`;
const CACHE_FILE = `${wxStub.env.USER_DATA_PATH}/map-cache-${SITE}.json`;
const DOWNLOAD_KEY = `map:download:${SITE}`;

const RAW = JSON.stringify(REAL_MAP);
const BYTES = utf8Bytes(RAW);
const TOTAL = BYTES.length;
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
  dataUrl: 'https://cdn.example.com/maps/school.json',
  dataSha256: SHA,
};

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

interface ServerOptions {
  /** 第 N 个数据请求返回网络失败（从 1 计数）。 */
  failChunk?: number;
  version?: string;
  raw?: string;
  sha?: string;
  /** 忽略 Range，始终整包 200（模拟未实现断点续传的服务端）。 */
  ignoreRange?: boolean;
  /** 前 N 个数据请求返回 416（模拟服务端判定 Range 越界）。 */
  fail416Chunks?: number;
}

/** 模拟支持 Range 的地图服务端；返回每个数据请求的 Range 头（便于断言续传偏移）。 */
function installServer(options: ServerOptions = {}): { ranges: string[] } {
  const version = options.version ?? REAL_MAP.mapVersion;
  const raw = options.raw ?? RAW;
  const bytes = raw === RAW ? BYTES : utf8Bytes(raw);
  const sha = options.sha ?? (raw === RAW ? SHA : sha256(raw));
  const ranges: string[] = [];
  let chunkCount = 0;

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
      o.success({
        statusCode: 200,
        header: { ETag: 'etag-1' },
        data: { ...SUMMARY, mapVersion: version, dataSha256: sha },
      });
      return;
    }
    if (o.url.startsWith(DATA_URL_PREFIX)) {
      chunkCount += 1;
      const range = o.header?.Range ?? null;
      ranges.push(range ?? 'none');
      if (options.failChunk !== undefined && chunkCount === options.failChunk) {
        o.fail({ errMsg: 'request:fail network' });
        return;
      }
      if (options.ignoreRange === true || range === null) {
        o.success({ statusCode: 200, header: { ETag: 'etag-1' }, data: toArrayBuffer(bytes) });
        return;
      }
      if (options.fail416Chunks !== undefined && chunkCount <= options.fail416Chunks) {
        o.success({ statusCode: 416, header: { 'Content-Range': `bytes */${bytes.length}` }, data: {} });
        return;
      }
      const match = /^bytes=(\d+)-(\d+)$/.exec(range);
      if (match === null) {
        o.success({ statusCode: 416, header: {}, data: {} });
        return;
      }
      const start = Number(match[1]);
      if (start >= bytes.length) {
        // 与真实服务端一致：起点超出文件长度即 416
        o.success({ statusCode: 416, header: { 'Content-Range': `bytes */${bytes.length}` }, data: {} });
        return;
      }
      const end = Math.min(Number(match[2]), bytes.length - 1);
      o.success({
        statusCode: 206,
        header: {
          ETag: 'etag-1',
          'Content-Range': `bytes ${start}-${end}/${bytes.length}`,
        },
        data: toArrayBuffer(bytes.slice(start, end + 1)),
      });
      return;
    }
    o.success({ statusCode: 404, header: {}, data: {} });
  };
  return { ranges };
}

function newService(): MapService {
  const { client } = createHttpStack(BASE);
  return new MapService({ client, siteId: SITE, rewriteDataUrlToLocalApi: true });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

beforeEach(() => {
  wxStub.__storage.clear();
  wxStub.__files.clear();
  wxStub.__requestLog.length = 0;
  wxStub.__requestResponder = null;
  remove(`map:summary:${SITE}`);
  remove(`map:summary-etag:${SITE}`);
  remove(DOWNLOAD_KEY);
  remove('auth:token');
});

test('分片下载中断：状态持久化，重启后从已下载偏移续传', async () => {
  installServer({ failChunk: 2 });
  const first = newService();
  await expect(first.ensureLoaded()).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
  expect(first.isLoaded).toBe(false);

  // 中断后：半成品已落盘，状态可查询（供设置页展示百分比）
  const pending = first.pendingDownload;
  expect(pending?.downloadedBytes).toBe(MAP_DOWNLOAD_CHUNK_BYTES);
  expect(pending?.totalBytes).toBe(TOTAL);
  expect(pending?.active).toBe(false);
  expect(wxStub.__files.has(partialPath(SITE, REAL_MAP.mapVersion))).toBe(true);
  expect(wxStub.__files.has(CACHE_FILE)).toBe(false);

  // 恢复网络后重新启动：首片直接从已下载偏移续传，不整包重下
  const server = installServer();
  const second = newService();
  await second.ensureLoaded();
  expect(server.ranges[0]).toBe(
    `bytes=${MAP_DOWNLOAD_CHUNK_BYTES}-${MAP_DOWNLOAD_CHUNK_BYTES * 2 - 1}`,
  );
  expect(server.ranges.some((r) => r.startsWith('bytes=0-'))).toBe(false);
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
  // 完整校验通过后才转正缓存，并清理半成品与下载状态
  expect(wxStub.__fileText(CACHE_FILE)).toBe(RAW);
  expect(wxStub.__files.has(partialPath(SITE, REAL_MAP.mapVersion))).toBe(false);
  expect(getJson(DOWNLOAD_KEY)).toBeNull();
  expect(second.pendingDownload).toBeNull();
}, 30000);

test('目标版本变化：丢弃旧版本半成品，从 0 重新下载', async () => {
  installServer({ failChunk: 2 });
  const first = newService();
  await expect(first.ensureLoaded()).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
  expect(wxStub.__files.has(partialPath(SITE, REAL_MAP.mapVersion))).toBe(true);

  const nextVersion = '2026-11-20.1';
  const nextRaw = JSON.stringify({ ...REAL_MAP, mapVersion: nextVersion });
  const server = installServer({ version: nextVersion, raw: nextRaw });
  const second = newService();
  await second.ensureLoaded();

  expect(server.ranges[0]).toBe(`bytes=0-${MAP_DOWNLOAD_CHUNK_BYTES - 1}`);
  expect(wxStub.__files.has(partialPath(SITE, REAL_MAP.mapVersion))).toBe(false);
  expect(second.requireVersion()).toBe(nextVersion);
  expect(wxStub.__fileText(CACHE_FILE)).toBe(nextRaw);
}, 30000);

test('缓存过旧：ready 带 stale 且新版后台静默下载（非前台）', async () => {
  installServer(); // 先落地 2026-10-25.1 缓存
  const first = newService();
  await first.ensureLoaded();

  const nextVersion = '2026-11-20.1';
  const nextRaw = JSON.stringify({ ...REAL_MAP, mapVersion: nextVersion });
  installServer({ version: nextVersion, raw: nextRaw });

  const second = newService();
  const events: MapServiceEvent[] = [];
  second.subscribe((event) => events.push(event));
  await second.ensureLoaded();

  // 本会话继续用旧版（不热替换），并标记 stale
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
  expect(events.find((e) => e.type === 'ready')).toMatchObject({
    source: 'cache',
    stale: true,
  });
  expect(events.find((e) => e.type === 'download-start')).toMatchObject({
    foreground: false,
  });

  for (let i = 0; i < 80 && wxStub.__fileText(CACHE_FILE) !== nextRaw; i += 1) {
    await sleep(10);
  }
  expect(wxStub.__fileText(CACHE_FILE)).toBe(nextRaw);
  expect(second.requireVersion()).toBe(REAL_MAP.mapVersion);
}, 30000);

test('无缓存且下载失败：前台下载事件 + 保留状态（不落正式缓存）', async () => {
  installServer({ failChunk: 1 });
  const service = newService();
  const events: MapServiceEvent[] = [];
  service.subscribe((event) => events.push(event));
  await expect(service.ensureLoaded()).rejects.toMatchObject({ code: 'NETWORK_ERROR' });

  expect(events.find((e) => e.type === 'download-start')).toMatchObject({
    foreground: true,
  });
  expect(service.isLoaded).toBe(false);
  expect(service.pendingDownload?.active).toBe(false);
  expect(wxStub.__files.has(CACHE_FILE)).toBe(false);
});

test('服务端不支持 Range：按整包 200 兜底写入并校验通过', async () => {
  installServer({ ignoreRange: true });
  const service = newService();
  await service.ensureLoaded();
  expect(service.requireVersion()).toBe(REAL_MAP.mapVersion);
  expect(wxStub.__fileText(CACHE_FILE)).toBe(RAW);
  expect(wxStub.__files.has(partialPath(SITE, REAL_MAP.mapVersion))).toBe(false);
}, 30000);

test('半成品写入契约：offset=0 需建档（真机 appendFile 不自动创建文件）', async () => {
  const path = partialPath(SITE, REAL_MAP.mapVersion);
  await appendMapPartial(SITE, REAL_MAP.mapVersion, toArrayBuffer(BYTES.slice(0, 16)), 0);
  expect(wxStub.__files.has(path)).toBe(true);
  await appendMapPartial(SITE, REAL_MAP.mapVersion, toArrayBuffer(BYTES.slice(16, 32)), 16);
  expect(wxStub.__files.get(path)?.length).toBe(32);
});

test('续传状态已满：不发越界 Range，清空后整包重下（真机 416 坑）', async () => {
  setJson(DOWNLOAD_KEY, {
    mapVersion: REAL_MAP.mapVersion,
    dataSha256: SHA,
    downloadedBytes: TOTAL,
    totalBytes: TOTAL,
    updatedAtMs: 1,
  });
  wxStub.__files.set(partialPath(SITE, REAL_MAP.mapVersion), BYTES.slice());

  const server = installServer();
  const service = newService();
  await service.ensureLoaded();

  expect(server.ranges[0]).toBe(`bytes=0-${MAP_DOWNLOAD_CHUNK_BYTES - 1}`);
  expect(server.ranges.some((r) => r.startsWith(`bytes=${TOTAL}-`))).toBe(false);
  expect(service.requireVersion()).toBe(REAL_MAP.mapVersion);
  expect(wxStub.__fileText(CACHE_FILE)).toBe(RAW);
  expect(getJson(DOWNLOAD_KEY)).toBeNull();
}, 30000);

test('首片即 416：清空半成品并重试一次，不把 416 抛给用户', async () => {
  const server = installServer({ fail416Chunks: 1 });
  const service = newService();
  await service.ensureLoaded();

  expect(server.ranges[0]).toBe(`bytes=0-${MAP_DOWNLOAD_CHUNK_BYTES - 1}`);
  expect(server.ranges[1]).toBe(`bytes=0-${MAP_DOWNLOAD_CHUNK_BYTES - 1}`);
  expect(service.requireVersion()).toBe(REAL_MAP.mapVersion);
}, 30000);

test('过旧判定按 mapVersion 日期差（默认 7 天）', () => {
  expect(isVersionStale('2026-10-20.1', '2026-10-27.1')).toBe(false); // 恰好 7 天不算过旧
  expect(isVersionStale('2026-10-20.1', '2026-10-28.1')).toBe(true);
  expect(isVersionStale('2026-10-27.3', '2026-10-25.1')).toBe(false); // 旧于服务端版本不倒判
  expect(isVersionStale('bad-version', '2026-10-28.1')).toBe(false);
});

test('Content-Range 解析', () => {
  expect(parseContentRange('bytes 1024-2047/4096')).toEqual({
    start: 1024,
    end: 2047,
    total: 4096,
  });
  expect(parseContentRange('bytes 0-0/*')).toEqual({ start: 0, end: 0, total: null });
  expect(parseContentRange(null)).toBeNull();
  expect(parseContentRange('items 0-1/2')).toBeNull();
});
