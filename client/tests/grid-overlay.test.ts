import {
  OVERLAY_CACHE_KEY,
  clearOverlayCache,
  mergeOverlay,
  overlayInvalidReason,
  readOverlayCache,
  resolveRuntimeGrid,
  writeOverlayCache,
  type OverlayPayload,
} from '../core/collection/grid-overlay';
import { FINGERPRINT_GRID_VERSION, type FpGridPoint } from '../data/fingerprint_grid';
import type { ApiClient } from '../core/http/api-client';

function point(fpId: string, overrides: Partial<FpGridPoint> = {}): FpGridPoint {
  return {
    fpId,
    floorId: '1',
    x: 1,
    y: 2,
    regionType: 'normal',
    shortId: fpId,
    col: 1,
    row: 2,
    isBase2m: true,
    collectable: true,
    collectable15m: true,
    collectableWithSim: true,
    simAddedBy: [],
    routeTags: [],
    visible10m: 5,
    visible15m: 8,
    nearestBeaconM: 2.4,
    strongestRssi: -70,
    ...overrides,
  };
}

function payload(overrides: Partial<OverlayPayload> = {}): OverlayPayload {
  return {
    schemaVersion: '1.0',
    baseGridVersion: FINGERPRINT_GRID_VERSION,
    gridVersion: 'g-20261109-abcdef',
    paramsVersion: 'loc-20261109-abcd',
    points: [
      {
        fpId: 'A',
        collectable: false,
        collectable15m: true,
        collectableWithSim: false,
        simAddedBy: ['SIM-1'],
        visible10m: 2,
        strongestRssi: -88,
      },
    ],
    ...overrides,
  };
}

function clientReturning(
  responder: (opts: { path?: string; headers?: Record<string, string> }) => {
    statusCode: number;
    data: unknown;
    etag?: string | null;
  },
): ApiClient {
  return {
    request: jest.fn(async (opts: { path?: string; headers?: Record<string, string> }) => {
      const res = responder(opts);
      return {
        statusCode: res.statusCode,
        data: res.data,
        etag: res.etag ?? null,
        requestId: null,
        header: {},
      };
    }),
  } as unknown as ApiClient;
}

beforeEach(() => {
  clearOverlayCache();
});

describe('mergeOverlay', () => {
  it('只覆盖随参数变化的标记字段，保留设计与 LOD 字段', () => {
    const base = [point('A'), point('B', { collectable: false, visible10m: 1 })];
    const { points, applied, unknownPoints } = mergeOverlay(base, payload());

    expect(applied).toBe(1);
    expect(unknownPoints).toBe(0);
    const a = points[0];
    expect(a.collectable).toBe(false);
    expect(a.collectable15m).toBe(true);
    expect(a.collectableWithSim).toBe(false);
    expect(a.simAddedBy).toEqual(['SIM-1']);
    expect(a.visible10m).toBe(2);
    expect(a.strongestRssi).toBe(-88);
    // 设计层字段不变
    expect(a.x).toBe(1);
    expect(a.isBase2m).toBe(true);
    // 未命中的点保持原样
    expect(points[1].collectable).toBe(false);
    expect(points[1].visible10m).toBe(1);
  });

  it('未知 fpId 被忽略并计数', () => {
    const base = [point('A')];
    const { points, unknownPoints } = mergeOverlay(
      base,
      payload({ points: [{ fpId: 'A' }, { fpId: 'NOPE' }, { fpId: 'ALSO-NOPE' }] }),
    );
    expect(points).toHaveLength(1);
    expect(unknownPoints).toBe(2);
  });
});

describe('overlayInvalidReason', () => {
  it('基线不一致严格拒绝', () => {
    expect(overlayInvalidReason(payload({ baseGridVersion: '11.0.0' }), '11.1.0')).toContain(
      '基线不一致',
    );
  });

  it('缺少 points 或未知 schema 拒绝', () => {
    expect(overlayInvalidReason(payload({ points: undefined }), '11.1.0')).toContain('points');
    expect(overlayInvalidReason(payload({ schemaVersion: '9.9' }), '11.1.0')).toContain('schema');
  });
});

describe('resolveRuntimeGrid', () => {
  it('在线合并并写入缓存', async () => {
    const base = [point('A')];
    const client = clientReturning(() => ({
      statusCode: 200,
      data: payload(),
      etag: '"v1"',
    }));
    const grid = await resolveRuntimeGrid(client, base);

    expect(grid.source).toBe('online');
    expect(grid.gridVersion).toBe('g-20261109-abcdef');
    expect(grid.paramsVersion).toBe('loc-20261109-abcd');
    expect(grid.points[0].collectable).toBe(false);
    expect(readOverlayCache()?.etag).toBe('"v1"');
    const storage = (wx as unknown as { __storage: Map<string, string> }).__storage;
    expect(storage.get(`pathai:${OVERLAY_CACHE_KEY}`)).toBeDefined();
  });

  it('304 复用缓存，不再合并网络响应体', async () => {
    const base = [point('A')];
    writeOverlayCache(payload(), '"v1"');
    const client = clientReturning((opts) => {
      expect(opts.headers?.['If-None-Match']).toBe('"v1"');
      return { statusCode: 304, data: '' };
    });
    const grid = await resolveRuntimeGrid(client, base);

    expect(grid.source).toBe('cache');
    expect(grid.points[0].collectable).toBe(false);
  });

  it('网络失败回退缓存并提示离线', async () => {
    const base = [point('A')];
    writeOverlayCache(payload(), '"v1"');
    const client = {
      request: jest.fn(async () => {
        throw new Error('offline');
      }),
    } as unknown as ApiClient;
    const grid = await resolveRuntimeGrid(client, base);

    expect(grid.source).toBe('cache');
    expect(grid.message).toContain('离线');
  });

  it('无缓存且不可用时回退内置快照', async () => {
    const base = [point('A')];
    const client = clientReturning(() => ({
      statusCode: 200,
      data: { available: false, reason: 'no_active_version', baseGridVersion: null },
    }));
    const grid = await resolveRuntimeGrid(client, base);

    expect(grid.source).toBe('builtin');
    expect(grid.points).toEqual(base);
    expect(grid.gridVersion).toBe(FINGERPRINT_GRID_VERSION);
    expect(grid.message).toContain('内置网格快照');
  });

  it('基线不一致时不使用网络覆盖层，回退内置', async () => {
    const base = [point('A')];
    const client = clientReturning(() => ({
      statusCode: 200,
      data: payload({ baseGridVersion: '11.0.0' }),
    }));
    const grid = await resolveRuntimeGrid(client, base);

    expect(grid.source).toBe('builtin');
    expect(grid.message).toContain('基线不一致');
  });
});
