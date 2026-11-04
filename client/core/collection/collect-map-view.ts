/**
 * 采集页共享地图画布控制器（静态标定/网格指纹复用）：
 * - 底图渲染与 pages/map 同源（floor-geometry 预处理 + FloorMapViewport），
 *   手势（单指平移/双指缩放/单击/长按）就地实现，不改动导航地图页；
 * - 点位/定位图层由页面通过 drawOverlay 回调自绘；
 * - 楼层几何按需预处理并缓存（进入页面只处理当前层，切层再算）。
 */
import type { MapBeaconEntry, MapFloorData } from '../../typings/api';
import {
  MAP_BACKGROUND_COLOR,
  flatBounds,
  flattenRoomFills,
  flattenWalls,
  floorPlaceLabels,
  floorsSortedByLevel,
  type PlaceLabel,
  type RoomFillGroup,
} from '../map/floor-geometry';
import { FloorMapViewport } from '../map/floor-map-viewport';

type Canvas2d = WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D;

interface CanvasQueryFields {
  node?: unknown;
  width?: number;
  height?: number;
  left?: number;
  top?: number;
}

/** 页面最小切片（createSelectorQuery 需页面实例）。 */
export interface CollectMapPageLike {
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

/** 地图数据依赖（页面在 ensureLoaded 后传入）。 */
export interface CollectMapDeps {
  floors: Record<string, MapFloorData>;
  places: ReadonlyArray<{ placeId: string; name: string; floorId: string; nodeId: string }>;
  beacons: ReadonlyArray<MapBeaconEntry>;
}

export interface CollectMapCallbacks {
  onReady(): void;
  /** 单击（非拖动/长按）：world 为米制坐标，screen 为画布 CSS 像素。 */
  onTap(world: { x: number; y: number }, screen: { x: number; y: number }): void;
  onLongPress(world: { x: number; y: number }): void;
  onFloorChanged(floorId: string): void;
  /** 底图之上的叠加图层（ctx 已 setTransform(dpr)，坐标为 CSS 像素）。 */
  drawOverlay(ctx: Canvas2d, vp: FloorMapViewport): void;
}

interface GestureState {
  mode: 'pan' | 'zoom' | null;
  lastX: number;
  lastY: number;
  baseDist: number;
  lastMidX: number;
  lastMidY: number;
  /** 单击判定：起点/时刻/累计位移。 */
  startX: number;
  startY: number;
  startAtMs: number;
  moved: boolean;
  longPressFired: boolean;
}

const TAP_SLOP_PX = 10;
const TAP_MAX_MS = 400;

/** 可点按缩放控件的单步倍率（上下限由 FloorMapViewport 统一夹紧）。 */
const ZOOM_STEP_FACTOR = 1.6;

export class CollectMapView {
  private readonly canvasId: string;
  private readonly deps: CollectMapDeps;
  private readonly cb: CollectMapCallbacks;

  private canvas: WechatMiniprogram.Canvas | null = null;
  private ctx: Canvas2d | null = null;
  private viewport: FloorMapViewport | null = null;
  private dpr = 1;
  private size = { w: 0, h: 0 };
  private rect = { left: 0, top: 0 };
  private dirty = false;
  private rafPending = false;
  private gesture: GestureState = {
    mode: null,
    lastX: 0,
    lastY: 0,
    baseDist: 0,
    lastMidX: 0,
    lastMidY: 0,
    startX: 0,
    startY: 0,
    startAtMs: 0,
    moved: false,
    longPressFired: false,
  };

  /** 楼层几何按需预处理缓存。 */
  private segs = new Map<string, Float32Array>();
  private fills = new Map<string, RoomFillGroup[]>();
  private labels = new Map<string, PlaceLabel[]>();

  constructor(
    private readonly page: CollectMapPageLike,
    opts: { canvasId: string; deps: CollectMapDeps; callbacks: CollectMapCallbacks },
  ) {
    this.canvasId = opts.canvasId;
    this.deps = opts.deps;
    this.cb = opts.callbacks;
  }

  get vp(): FloorMapViewport | null {
    return this.viewport;
  }

  get floorId(): string {
    return this.viewport?.floorId ?? '';
  }

  /** 楼层列表（按 level 升序，供页面楼层按钮渲染）。 */
  get floorList(): Array<{ floorId: string; displayName: string; level: number }> {
    return floorsSortedByLevel(this.deps.floors);
  }

  /** 查询 canvas 节点并初始化尺寸（onReady 时调用一次）。 */
  init(): void {
    const query = this.page.createSelectorQuery();
    query
      .select(`#${this.canvasId}`)
      .fields({ node: true, size: true, rect: true })
      .exec((res: Array<CanvasQueryFields | undefined>) => {
        const fields = res[0];
        const node = fields?.node as WechatMiniprogram.Canvas | undefined;
        if (
          node === undefined ||
          typeof fields?.width !== 'number' ||
          typeof fields?.height !== 'number'
        ) {
          return;
        }
        const info = wx.getWindowInfo();
        this.dpr = info.pixelRatio > 0 ? info.pixelRatio : 2;
        this.size = { w: fields.width, h: fields.height };
        node.width = Math.round(fields.width * this.dpr);
        node.height = Math.round(fields.height * this.dpr);
        this.rect = { left: fields.left ?? 0, top: fields.top ?? 0 };
        this.canvas = node;
        this.ctx = node.getContext('2d');
        const first = this.floorList[0];
        const firstSegs = first !== undefined ? this.segmentsFor(first.floorId) : undefined;
        if (first !== undefined && firstSegs !== undefined) {
          this.viewport = new FloorMapViewport(
            flatBounds(firstSegs),
            this.size.w,
            this.size.h,
            first.floorId,
          );
          this.cb.onFloorChanged(first.floorId);
        }
        this.cb.onReady();
        this.requestDraw();
      });
  }

  /** 切换楼层：全图适配居中（与导航地图切层行为一致）。 */
  setFloor(floorId: string): void {
    const vp = this.viewport;
    if (vp === null || floorId === vp.floorId) {
      return;
    }
    const segs = this.segmentsFor(floorId);
    if (segs === undefined) {
      return;
    }
    vp.setFloor(floorId, flatBounds(segs));
    vp.fit();
    this.cb.onFloorChanged(floorId);
    this.requestDraw();
  }

  fit(): void {
    this.viewport?.fit();
    this.requestDraw();
  }

  /** 单指点按放大：以画布中心为锚点；到达上限后 scale/offset 不变。 */
  zoomIn(): void {
    this.zoomBy(ZOOM_STEP_FACTOR);
  }

  /** 单指点按缩小：以画布中心为锚点；到达 fitScale 后 scale/offset 不变。 */
  zoomOut(): void {
    this.zoomBy(1 / ZOOM_STEP_FACTOR);
  }

  private zoomBy(factor: number): void {
    const vp = this.viewport;
    if (vp === null) {
      return;
    }
    vp.zoomAt(this.size.w / 2, this.size.h / 2, factor);
    this.requestDraw();
  }

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
  }

  destroy(): void {
    this.canvas = null;
    this.ctx = null;
    this.viewport = null;
  }

  // ------------------------------------------------------------------
  // 手势（WXML 事件转发）
  // ------------------------------------------------------------------

  private toCanvasPoint(touch: { clientX: number; clientY: number }): { x: number; y: number } {
    return { x: touch.clientX - this.rect.left, y: touch.clientY - this.rect.top };
  }

  onTouchStart(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    const g = this.gesture;
    g.longPressFired = false;
    if (ts.length === 1) {
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = {
        mode: 'pan',
        lastX: p.x,
        lastY: p.y,
        baseDist: 0,
        lastMidX: 0,
        lastMidY: 0,
        startX: p.x,
        startY: p.y,
        startAtMs: Date.now(),
        moved: false,
        longPressFired: false,
      };
    } else if (ts.length >= 2) {
      const a = this.toCanvasPoint(ts[0]);
      const b = this.toCanvasPoint(ts[1]);
      this.gesture = {
        mode: 'zoom',
        lastX: 0,
        lastY: 0,
        baseDist: Math.hypot(a.x - b.x, a.y - b.y),
        lastMidX: (a.x + b.x) / 2,
        lastMidY: (a.y + b.y) / 2,
        startX: 0,
        startY: 0,
        startAtMs: 0,
        moved: true,
        longPressFired: false,
      };
    }
  }

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
      if (Math.hypot(p.x - g.startX, p.y - g.startY) > TAP_SLOP_PX) {
        g.moved = true;
      }
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
  }

  onTouchEnd(e: WechatMiniprogram.TouchEvent): void {
    const ts = e.touches;
    const g = this.gesture;
    if (ts.length === 1) {
      // 双指抬一根 → 剩余单指继续平移
      const p = this.toCanvasPoint(ts[0]);
      this.gesture = {
        mode: 'pan',
        lastX: p.x,
        lastY: p.y,
        baseDist: 0,
        lastMidX: 0,
        lastMidY: 0,
        startX: p.x,
        startY: p.y,
        startAtMs: 0,
        moved: true,
        longPressFired: g.longPressFired,
      };
    } else if (ts.length === 0) {
      // 单击判定：位移小、时长短、未触发长按
      if (
        g.mode === 'pan' &&
        !g.moved &&
        !g.longPressFired &&
        g.startAtMs > 0 &&
        Date.now() - g.startAtMs <= TAP_MAX_MS
      ) {
        this.fireTap(g.startX, g.startY);
      }
      g.mode = null;
    }
  }

  onTouchCancel(e: WechatMiniprogram.TouchEvent): void {
    this.onTouchEnd(e);
  }

  /** WXML bindlongpress 转发（canvas 上长按 → 补标等）。 */
  onLongPressEvent(e: WechatMiniprogram.TouchEvent): void {
    const touch = e.touches[0];
    if (touch === undefined) {
      return;
    }
    const p = this.toCanvasPoint(touch);
    this.gesture.longPressFired = true;
    const world = this.viewport?.fromScreen(p.x, p.y);
    if (world !== undefined) {
      this.cb.onLongPress(world);
    }
  }

  private fireTap(sx: number, sy: number): void {
    const vp = this.viewport;
    if (vp === null) {
      return;
    }
    this.cb.onTap(vp.fromScreen(sx, sy), { x: sx, y: sy });
  }

  // ------------------------------------------------------------------
  // 绘制：底图（与 pages/map 同源）+ 页面叠加层
  // ------------------------------------------------------------------

  private segmentsFor(floorId: string): Float32Array | undefined {
    let segs = this.segs.get(floorId);
    if (segs === undefined) {
      segs = flattenWalls(this.deps.floors, floorId);
      this.segs.set(floorId, segs);
    }
    return segs;
  }

  private draw(): void {
    const ctx = this.ctx;
    const vp = this.viewport;
    if (ctx === null || vp === null) {
      return;
    }
    const { w, h } = this.size;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = MAP_BACKGROUND_COLOR;
    ctx.fillRect(0, 0, w, h);

    // 房间面按类型着色
    let fills = this.fills.get(vp.floorId);
    if (fills === undefined) {
      fills = flattenRoomFills(this.deps.floors, vp.floorId);
      this.fills.set(vp.floorId, fills);
    }
    const { scale, offsetX, offsetY } = vp;
    for (const group of fills) {
      ctx.beginPath();
      for (const ring of group.rings) {
        for (let i = 0; i + 1 < ring.length; i += 2) {
          const sx = ring[i] * scale + offsetX;
          const sy = -ring[i + 1] * scale + offsetY;
          if (i === 0) {
            ctx.moveTo(sx, sy);
          } else {
            ctx.lineTo(sx, sy);
          }
        }
        ctx.closePath();
      }
      ctx.fillStyle = group.color;
      ctx.fill();
    }

    // 墙体单 path 一次 stroke
    const segs = this.segmentsFor(vp.floorId);
    if (segs !== undefined) {
      ctx.beginPath();
      for (let i = 0; i + 3 < segs.length; i += 4) {
        ctx.moveTo(segs[i] * scale + offsetX, -segs[i + 1] * scale + offsetY);
        ctx.lineTo(segs[i + 2] * scale + offsetX, -segs[i + 3] * scale + offsetY);
      }
      ctx.strokeStyle = '#aeb7c4';
      ctx.lineWidth = Math.max(1, 0.12 * scale);
      ctx.stroke();
    }

    // 信标（当前层）：现场找点参照
    ctx.fillStyle = '#059669';
    for (const beacon of this.deps.beacons) {
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

    // 空间语义标注（贪心防重叠，与导航地图同策略）
    let labels = this.labels.get(vp.floorId);
    if (labels === undefined) {
      labels = floorPlaceLabels(this.deps.floors, this.deps.places, vp.floorId);
      this.labels.set(vp.floorId, labels);
    }
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
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.92)';
      ctx.strokeText(label.text, sx, sy);
      ctx.fillStyle = '#374151';
      ctx.fillText(label.text, sx, sy);
    }

    this.cb.drawOverlay(ctx, vp);
  }
}
