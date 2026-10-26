/**
 * 楼层地图启动首屏：墙体平面图 + 1F/2F 切换 + 单指拖动/双指缩放 + mock 位置点。
 *
 * 视口数学在 core/map/floor-map-viewport.ts（纯逻辑，单测覆盖），本文件只做
 * 页面编排、canvas 绘制与手势采集；地图数据经 MapService 单例（本地文件缓存
 * 秒开，未缓存时前台下载，失败走 loadError + 重试）。
 */
import { appContext } from '../../services/app-context';
import { createLogger } from '../../utils/logger';
import { ApiClientError } from '../../core/http/errors';
import { describeError } from '../../core/http/error-messages';
import { flattenWalls, flatBounds, floorsSortedByLevel, floorPlaceLabels, type PlaceLabel } from '../../core/map/floor-geometry';
import { FloorMapViewport } from '../../core/map/floor-map-viewport';

const log = createLogger('map');

/** mock 初始位置：1 层 (-37,-48)（局部米制，邻近真实信标 BK-01-008）。 */
const MOCK_POSITION = { floorId: '1', x: -37, y: -48 };

type Canvas2d = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

interface FloorItem {
  floorId: string;
  displayName: string;
  level: number;
}

interface GestureState {
  mode: 'pan' | 'zoom' | null;
  lastX: number;
  lastY: number;
  baseDist: number;
  lastMidX: number;
  lastMidY: number;
}

interface CanvasQueryFields {
  node?: unknown;
  width?: number;
  height?: number;
  left?: number;
  top?: number;
}

Page({
  data: {
    loading: true,
    loadError: '',
    floorLabel: '一楼',
    currentFloor: '1',
    floors: [] as FloorItem[],
  },

  canvas: null as WechatMiniprogram.Canvas | null,
  ctx: null as Canvas2d | null,
  viewport: null as FloorMapViewport | null,
  wallSegs: {} as Record<string, Float32Array>,
  placeLabels: {} as Record<string, PlaceLabel[]>,
  floorList: [] as FloorItem[],
  canvasRect: { left: 0, top: 0 },
  canvasReady: false,
  canvasSize: { w: 0, h: 0 },
  dpr: 1,
  dirty: false,
  rafPending: false,
  gesture: {
    mode: null,
    lastX: 0,
    lastY: 0,
    baseDist: 0,
    lastMidX: 0,
    lastMidY: 0,
  } as GestureState,

  onLoad(): void {
    void this.bootstrap();
  },

  onReady(): void {
    this.initCanvas();
  },

  // ------------------------------------------------------------------
  // 数据加载（照 index 页 bootstrap 范式）
  // ------------------------------------------------------------------

  async bootstrap(): Promise<void> {
    this.setData({ loading: true, loadError: '' });
    try {
      await appContext.map.ensureLoaded();
      const floorsData = appContext.map.floors;
      const floors = floorsSortedByLevel(floorsData);
      const segs: Record<string, Float32Array> = {};
      const labels: Record<string, PlaceLabel[]> = {};
      for (const item of floors) {
        segs[item.floorId] = flattenWalls(floorsData, item.floorId);
        // 空间语义标注（教室/办公室等名称）：一次预计算，draw 按层引用
        labels[item.floorId] = floorPlaceLabels(floorsData, appContext.map.places, item.floorId);
      }
      this.wallSegs = segs;
      this.placeLabels = labels;
      this.floorList = floors;
      this.setData({ floors, loading: false, loadError: '' });
      log.info('地图首屏就绪', appContext.map.requireVersion());
      this.tryFirstDraw();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请检查网络';
      log.warn('地图加载失败', err);
      this.setData({ loading: false, loadError: message });
    }
  },

  onRetry(): void {
    void this.bootstrap();
  },

  // ------------------------------------------------------------------
  // canvas 初始化与首绘
  // ------------------------------------------------------------------

  initCanvas(): void {
    const query = this.createSelectorQuery();
    query
      .select('#mapCanvas')
      .fields({ node: true, size: true, rect: true })
      .exec((res: Array<CanvasQueryFields | undefined>) => {
        const fields = res[0];
        // fields 回调为弱类型对象（IAnyObject），node 需一处受控断言
        const node = fields?.node as WechatMiniprogram.Canvas | undefined;
        if (node === undefined || typeof fields?.width !== 'number' || typeof fields?.height !== 'number') {
          log.warn('canvas 节点获取失败');
          return;
        }
        const info = wx.getWindowInfo();
        this.dpr = info.pixelRatio > 0 ? info.pixelRatio : 2;
        this.canvasSize = { w: fields.width, h: fields.height };
        node.width = Math.round(fields.width * this.dpr);
        node.height = Math.round(fields.height * this.dpr);
        this.canvasRect = { left: fields.left ?? 0, top: fields.top ?? 0 };
        this.canvas = node;
        this.ctx = node.getContext('2d');
        this.canvasReady = true;
        log.info('canvas 就绪', `${this.canvasSize.w}x${this.canvasSize.h}`, `dpr=${this.dpr}`);
        this.tryFirstDraw();
      });
  },

  /** 数据与画布两边就绪后建视口并首绘。 */
  tryFirstDraw(): void {
    if (!this.canvasReady || this.data.loading || this.viewport !== null) {
      return;
    }
    const firstFloor = this.floorList[0];
    const segs = firstFloor !== undefined ? this.wallSegs[firstFloor.floorId] : undefined;
    if (firstFloor === undefined || segs === undefined) {
      return;
    }
    this.viewport = new FloorMapViewport(
      flatBounds(segs),
      this.canvasSize.w,
      this.canvasSize.h,
      firstFloor.floorId,
    );
    this.draw();
  },

  // ------------------------------------------------------------------
  // 绘制
  // ------------------------------------------------------------------

  draw(): void {
    const ctx = this.ctx;
    const vp = this.viewport;
    if (ctx === null || vp === null) {
      return;
    }
    const { w, h } = this.canvasSize;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    // 背景
    ctx.fillStyle = '#f5f7fa';
    ctx.fillRect(0, 0, w, h);

    // 墙体：扁平段单 path 一次 stroke（F1 2436 段）
    const segs = this.wallSegs[vp.floorId];
    if (segs !== undefined) {
      const { scale, offsetX, offsetY } = vp;
      ctx.beginPath();
      for (let i = 0; i + 3 < segs.length; i += 4) {
        ctx.moveTo(segs[i] * scale + offsetX, -segs[i + 1] * scale + offsetY);
        ctx.lineTo(segs[i + 2] * scale + offsetX, -segs[i + 3] * scale + offsetY);
      }
      ctx.strokeStyle = '#1f2937';
      ctx.lineWidth = Math.max(1, 0.2 * scale);
      ctx.stroke();
    }

    // 信标（当前层）
    ctx.fillStyle = '#059669';
    for (const beacon of appContext.map.beacons) {
      if (beacon.floorId !== vp.floorId) {
        continue;
      }
      const { sx, sy } = vp.toScreen(beacon.x, beacon.y);
      if (sx < -6 || sy < -6 || sx > w + 6 || sy > h + 6) {
        continue;
      }
      ctx.beginPath();
      ctx.arc(sx, sy, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    // 空间语义标注（教室/办公室等）：固定屏幕字号、白描边压在墙线上、
    // 贪心防重叠（超出视口的跳过；缩得过小时自然只显示互不相交的子集）
    const labels = this.placeLabels[vp.floorId];
    if (labels !== undefined) {
      const FONT = 12;
      ctx.font = `${FONT}px -apple-system, "PingFang SC", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const boxes: Array<[number, number, number, number]> = [];
      for (const label of labels) {
        const { sx, sy } = vp.toScreen(label.x, label.y);
        const halfW = (label.text.length * FONT) / 2;
        if (sx < -halfW - 4 || sy < -10 || sx > w + halfW + 4 || sy > h + 10) {
          continue;
        }
        const box: [number, number, number, number] = [
          sx - halfW - 3,
          sy - FONT / 2 - 3,
          sx + halfW + 3,
          sy + FONT / 2 + 3,
        ];
        const overlaps = boxes.some(
          (r) => !(box[2] < r[0] || box[0] > r[2] || box[3] < r[1] || box[1] > r[3]),
        );
        if (overlaps) {
          continue;
        }
        boxes.push(box);
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(245, 247, 250, 0.95)';
        ctx.strokeText(label.text, sx, sy);
        ctx.fillStyle = '#374151';
        ctx.fillText(label.text, sx, sy);
      }
    }

    // mock 位置点（仅所在楼层）：精度圈 + 蓝点白描边
    if (vp.floorId === MOCK_POSITION.floorId) {
      const { sx, sy } = vp.toScreen(MOCK_POSITION.x, MOCK_POSITION.y);
      ctx.beginPath();
      ctx.arc(sx, sy, 14, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(37, 99, 235, 0.18)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(sx, sy, 7, 0, Math.PI * 2);
      ctx.fillStyle = '#2563eb';
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
    }
  },

  /** 手势期间每帧最多重绘一次；静止零重绘。 */
  requestDraw(): void {
    this.dirty = true;
    if (this.rafPending || this.canvas === null) {
      return;
    }
    this.rafPending = true;
    this.canvas.requestAnimationFrame(() => {
      this.rafPending = false;
      if (this.dirty) {
        this.dirty = false;
        this.draw();
      }
    });
  },

  // ------------------------------------------------------------------
  // 手势：单指平移、双指围绕中点缩放
  // ------------------------------------------------------------------

  toCanvasPoint(touch: { clientX: number; clientY: number }): { x: number; y: number } {
    return { x: touch.clientX - this.canvasRect.left, y: touch.clientY - this.canvasRect.top };
  },

  onTouchStart(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    if (ts.length === 1) {
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    } else if (ts.length >= 2) {
      // 1→2 指切换：重置基线防止缩放跳变
      const a = this.toCanvasPoint(ts[0]);
      const b = this.toCanvasPoint(ts[1]);
      this.gesture = {
        mode: 'zoom',
        lastX: 0,
        lastY: 0,
        baseDist: Math.hypot(a.x - b.x, a.y - b.y),
        lastMidX: (a.x + b.x) / 2,
        lastMidY: (a.y + b.y) / 2,
      };
    }
  },

  onTouchMove(e: WechatMiniprogram.TouchEvent): void {
    const vp = this.viewport;
    if (vp === null) {
      return;
    }
    const g = this.gesture;
    const ts = e.touches;
    if (g.mode === 'pan' && ts.length === 1) {
      const p = this.toCanvasPoint(ts[0]);
      vp.panBy(p.x - g.lastX, p.y - g.lastY);
      g.lastX = p.x;
      g.lastY = p.y;
      this.requestDraw();
    } else if (g.mode === 'zoom' && ts.length >= 2) {
      const a = this.toCanvasPoint(ts[0]);
      const b = this.toCanvasPoint(ts[1]);
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const midX = (a.x + b.x) / 2;
      const midY = (a.y + b.y) / 2;
      if (g.baseDist > 0) {
        vp.zoomAt(midX, midY, dist / g.baseDist);
      }
      vp.panBy(midX - g.lastMidX, midY - g.lastMidY);
      g.baseDist = dist;
      g.lastMidX = midX;
      g.lastMidY = midY;
      this.requestDraw();
    }
  },

  onTouchEnd(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    if (ts.length === 1) {
      // 双指抬一根 → 剩余单指继续平移
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = { mode: 'pan', lastX: p.x, lastY: p.y, baseDist: 0, lastMidX: 0, lastMidY: 0 };
    } else if (ts.length === 0) {
      this.gesture.mode = null;
    }
  },

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    this.onTouchEnd(e);
  },

  // ------------------------------------------------------------------
  // 覆盖层交互
  // ------------------------------------------------------------------

  onSwitchFloor(e: WechatMiniprogram.TouchEvent): void {
    const floorId = String(e.currentTarget.dataset.floor ?? '');
    log.info('tap 切换楼层', floorId, `current=${this.data.currentFloor}`);
    const vp = this.viewport;
    if (floorId === '' || floorId === this.data.currentFloor || vp === null) {
      return;
    }
    const segs = this.wallSegs[floorId];
    if (segs === undefined) {
      return;
    }
    // 保持 scale/offset（同坐标系视觉连续），仅按新层 bounds 夹紧
    vp.setFloor(floorId, flatBounds(segs));
    const item = this.floorList.find((f) => f.floorId === floorId);
    this.setData({ currentFloor: floorId, floorLabel: item?.displayName ?? floorId });
    this.requestDraw();
  },

  onResetView(): void {
    log.info('tap 回到全图');
    this.viewport?.fit();
    this.requestDraw();
  },

  onGoVoiceHome(): void {
    wx.navigateTo({
      url: '/pages/index/index',
      fail: (err) => log.warn('进入语音首页失败', JSON.stringify(err)),
    });
  },
});
