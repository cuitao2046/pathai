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
import { PushToTalk, type PttState } from '../../core/asr/push-to-talk';
import { createBeaconSource } from '../../core/ble/beacon-source-factory';
import type { BeaconSourceLike } from '../../core/ble/beacon-source';
import { MockBeaconSource } from '../../core/ble/mock-beacon-source';
import { MOCK_POSITION, MOCK_WALK_SPEED_MPS } from '../../core/map/mock-position';
import { SpeechQueue } from '../../core/speech/speech-queue';
import { IntentService } from '../../services/intent.service';
import { LocationQueryService, UNRESOLVED_TEXT } from '../../services/location-query.service';
import {
  NavSessionController,
  type NavPipelineControl,
  type NavState,
} from '../../core/nav/nav-session-controller';
import { LocationPipeline } from '../../core/position/location-pipeline';
import { SequenceCounter } from '../../core/position/sequence-counter';
import { buildRouteOverview } from '../../core/nav/route-overview';
import { vibrateLong, vibrateShort } from '../../utils/haptic';
import type {
  IntentParseResponse,
  PlaceCandidate,
  PositionEstimateResponse,
  RouteInfo,
} from '../../typings/api';

const log = createLogger('map');

/** PTT 状态文案（自原语音首页平移，REQ-PAGE 单页化）。 */
const PTT_TEXT: Record<PttState, string> = {
  idle: '按住说话',
  recording: '松开结束',
  recognizing: '识别中…',
  error: '识别失败，请重试',
};

/** 导航态文案（自原导航页平移）。 */
const NAV_TEXT: Record<NavState, string> = {
  idle: '正在定位…',
  active: '导航中',
  recovering: '信号弱，正在重新定位，请原地稍候',
  arrived: '已到达目的地',
  ended: '导航结束',
};

const DOUBLE_TAP_WINDOW_MS = 500;

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
    mapReady: false,
    floorLabel: '一楼',
    currentFloor: '1',
    floors: [] as FloorItem[],
    // 意图/语音（REQ-PAGE：下半屏热区 + 居中浮层）
    pttState: 'idle' as PttState,
    pttStateText: PTT_TEXT.idle,
    partial: '',
    recognized: '',
    reply: '',
    fallbackText: '',
    candidates: [] as PlaceCandidate[],
    destination: null as PlaceCandidate | null,
    isMockSource: appContext.dataSource === 'mock',
    // 导航态（REQ-PAGE：导航融合进地图页，原独立导航页退役）
    navActive: false,
    state: 'idle' as NavState,
    stateText: NAV_TEXT.idle,
    instruction: '',
    destinationName: '',
    ended: false,
  },

  canvas: null as WechatMiniprogram.Canvas | null,
  speech: null as SpeechQueue | null,
  ptt: null as PushToTalk | null,
  intents: null as IntentService | null,
  locationQuery: null as LocationQueryService | null,
  controller: null as NavSessionController | null,
  pipeline: null as LocationPipeline | null,
  /** 当前导航的观测源（mock 时为 MockBeaconSource，路线就绪后切换行走轨迹）。 */
  navSource: null as BeaconSourceLike | null,
  navDestination: null as PlaceCandidate | null,
  routeForDisplay: null as RouteInfo | null,
  /** 导航态最近一次可信位置（unreliable/lost 不更新 → 恢复态不推进显示）。 */
  navPosition: null as { floorId: string; x: number; y: number } | null,
  overviewAnnounced: false,
  lastTapAt: 0,
  sessionEnded: false,
  parseInFlight: false,
  locationInFlight: false,
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
    this.initSpeech();
    this.initPtt();
    this.intents = new IntentService(appContext.http.client, appContext.map);
    this.locationQuery = new LocationQueryService({
      client: appContext.http.client,
      map: appContext.map,
      makeSource: () => createBeaconSource(appContext.dataSource, appContext.map),
      floorName: (floorId) => (floorId ? appContext.map.floors[floorId]?.displayName : undefined),
    });
    void this.bootstrap();
  },

  onHide(): void {
    this.ptt?.handleAppHide(); // 切后台：取消识别，不提交
    this.controller?.handleBackground(); // 导航中：停采集，恢复交给 onShow
  },

  onShow(): void {
    const controller = this.controller;
    if (controller !== null && controller.sessionId !== null && !this.sessionEnded) {
      void controller.handleForeground();
    }
  },

  onUnload(): void {
    const controller = this.controller;
    if (controller !== null && !this.sessionEnded && controller.sessionId !== null) {
      controller.endSession('user_cancelled').catch(() => undefined);
    }
    this.pipeline?.stop();
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
      this.setData({ floors, loading: false, loadError: '', mapReady: true });
      log.info('地图首屏就绪', appContext.map.requireVersion());
      this.tryFirstDraw();
      // 启动集中申请权限：地图就绪后再弹，避免与加载叠加
      this.requestStartupPermissions();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请检查网络';
      log.warn('地图加载失败', err);
      this.setData({ loading: false, loadError: message, mapReady: false });
    }
  },

  /**
   * 启动授权：麦克风（按住说话/同声传译插件）、蓝牙（iBeacon 实扫）。
   * wx.authorize 仅首次弹窗；用户此前拒绝过则直接 fail（记录日志，
   * 需在设置页手动开启），一律不阻塞启动。
   */
  requestStartupPermissions(): void {
    for (const scope of ['scope.record', 'scope.bluetooth']) {
      wx.authorize({
        scope,
        success: () => log.info('授权通过', scope),
        fail: (err) => log.info('授权未通过（拒绝过或不支持）', scope, err.errMsg ?? ''),
      });
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

    // 导航态：当前楼层路线全貌 + 标记（REQ-NAV 展示层；route 由 onRoute 存入）
    const route = this.routeForDisplay;
    if (route !== null && this.data.navActive && route.polyline.length >= 2) {
      // polyline 按楼层切段（floorId null 沿用前一点楼层，桥接跨层内的空洞）
      const routeSegs: Array<{ floorId: string; pts: Array<{ x: number; y: number }> }> = [];
      let lastFloor: string | null = null;
      for (const pt of route.polyline) {
        const fid: string | null = pt.floorId ?? lastFloor;
        if (fid === null) {
          continue;
        }
        lastFloor = fid;
        const tail = routeSegs[routeSegs.length - 1];
        if (tail !== undefined && tail.floorId === fid) {
          tail.pts.push({ x: pt.x, y: pt.y });
        } else {
          routeSegs.push({ floorId: fid, pts: [{ x: pt.x, y: pt.y }] });
        }
      }

      // 当前层整条路线高亮（宽底衬 + 主线，非只显示前方一段）
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (const seg of routeSegs) {
        if (seg.floorId !== vp.floorId || seg.pts.length < 2) {
          continue;
        }
        ctx.beginPath();
        const head = vp.toScreen(seg.pts[0].x, seg.pts[0].y);
        ctx.moveTo(head.sx, head.sy);
        for (let i = 1; i < seg.pts.length; i += 1) {
          const pt = vp.toScreen(seg.pts[i].x, seg.pts[i].y);
          ctx.lineTo(pt.sx, pt.sy);
        }
        ctx.strokeStyle = 'rgba(37, 99, 235, 0.22)';
        ctx.lineWidth = Math.max(10, 1.0 * vp.scale);
        ctx.stroke();
        ctx.strokeStyle = '#2563eb';
        ctx.lineWidth = Math.max(5, 0.5 * vp.scale);
        ctx.stroke();
      }

      // 楼层切换点（相邻点楼层变化处、属本层）：橙色空心圈
      for (let i = 1; i < route.polyline.length; i += 1) {
        const prevFloor = route.polyline[i - 1].floorId;
        const cur = route.polyline[i];
        if (
          prevFloor === null || prevFloor === undefined ||
          cur.floorId === null || cur.floorId === undefined ||
          prevFloor === cur.floorId ||
          cur.floorId !== vp.floorId
        ) {
          continue;
        }
        const { sx, sy } = vp.toScreen(cur.x, cur.y);
        ctx.beginPath();
        ctx.arc(sx, sy, 9, 0, Math.PI * 2);
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 4;
        ctx.stroke();
      }

      // 转向点：decision_point 指令按 nodeId 查本层拓扑节点坐标 → 白边橙菱形
      const nodes = appContext.map.floors[vp.floorId]?.topology.nodes ?? [];
      for (const seg of route.segments) {
        const nodeId = seg.trigger.nodeId;
        if (seg.trigger.type !== 'decision_point' || nodeId === null || nodeId === undefined) {
          continue;
        }
        const node = nodes.find((n) => n.id === nodeId);
        if (node === undefined) {
          continue;
        }
        const { sx, sy } = vp.toScreen(node.coordinates[0], node.coordinates[1]);
        ctx.save();
        ctx.translate(sx, sy);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = '#f59e0b';
        ctx.fillRect(-6, -6, 12, 12);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.strokeRect(-6, -6, 12, 12);
        ctx.restore();
      }

      // 终点标记（本层）：红底白圈 + 「终」
      const lastPt = route.polyline[route.polyline.length - 1];
      if (lastPt !== undefined && (lastPt.floorId ?? lastFloor) === vp.floorId) {
        const { sx, sy } = vp.toScreen(lastPt.x, lastPt.y);
        ctx.beginPath();
        ctx.arc(sx, sy, 11, 0, Math.PI * 2);
        ctx.fillStyle = '#dc2626';
        ctx.fill();
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#ffffff';
        ctx.stroke();
        ctx.font = '12px -apple-system, "PingFang SC", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#ffffff';
        ctx.fillText('终', sx, sy);
      }
    }

    // 位置点：导航态画最近可信位置（无估计则不画，不伪造）；待命态保留 mock 锚点
    const displayPos = this.data.navActive ? this.navPosition : MOCK_POSITION;
    if (displayPos !== null && displayPos.floorId === vp.floorId) {
      const { sx, sy } = vp.toScreen(displayPos.x, displayPos.y);
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

  onGoSettings(): void {
    log.info('onGoSettings tap');
    wx.navigateTo({
      url: '/pages/settings/settings',
      fail: (err) => log.warn('navigateTo settings 失败', JSON.stringify(err)),
    });
  },

  // ------------------------------------------------------------------
  // 按住说话 → 意图（REQ-PAGE 下半屏热区 / 居中浮层）
  // ------------------------------------------------------------------

  initSpeech(): void {
    const appendFallback = (text: string) => {
      const next =
        this.data.fallbackText.length > 0 ? `${this.data.fallbackText}\n${text}` : text;
      this.setData({ fallbackText: next });
    };
    this.speech = new SpeechQueue({
      engine: appContext.tts,
      onUnavailable: (lastText) => {
        log.warn('TTS 不可用，降级文本展示', lastText);
        this.setData({ reply: lastText });
        appendFallback('语音播报不可用，已切换为文字提示');
      },
      onTextFallback: appendFallback,
    });
  },

  initPtt(): void {
    this.ptt = new PushToTalk({
      engine: appContext.asr,
      onStateChange: (state) => {
        this.setData({ pttState: state, pttStateText: PTT_TEXT[state] });
      },
      onPartial: (text) => this.setData({ partial: text }),
      onFinal: (text) => void this.handleFinal(text),
      onError: (message) => this.setData({ reply: message }),
    });
  },

  onPttStart(): void {
    if (this.data.loading || this.data.loadError !== '' || !this.data.mapReady) {
      return; // 地图未就绪不受理语音（原首页 mapReady 门）
    }
    this.ptt?.begin();
  },

  onPttEnd(): void {
    this.ptt?.end();
  },

  onPttCancel(): void {
    this.ptt?.cancel();
  },

  async handleFinal(text: string): Promise<void> {
    if (this.parseInFlight || this.intents === null) {
      return;
    }
    this.parseInFlight = true;
    this.setData({ recognized: text, partial: '', reply: '正在理解…' });
    try {
      const result = await this.intents.parse(text);
      this.applyIntent(result);
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '网络异常，请重试';
      log.warn('意图解析失败', err);
      this.setData({ reply: message });
      this.speech?.enqueue(message, 'normal');
    } finally {
      this.parseInFlight = false;
    }
  },

  applyIntent(result: IntentParseResponse): void {
    this.setData({
      reply: result.responseText,
      candidates: result.needsClarification ? result.candidates : [],
      destination: result.destination ?? null,
    });
    this.speech?.enqueue(result.responseText, 'normal');
    if (result.intent === 'query_location') {
      // responseText 是过渡话术（"请稍等，正在查询…"），随后补播真实位置
      void this.answerLocation();
    }
  },

  /** 「我在哪儿」：单次定位 → 按质量策略播报（FE-01，失败播安全文案）。 */
  async answerLocation(): Promise<void> {
    if (this.locationInFlight || this.locationQuery === null) {
      return;
    }
    this.locationInFlight = true;
    try {
      const text = await this.locationQuery.query();
      if (text === null || !this.data.mapReady) {
        return; // 并发被忽略 / 页面已不可用
      }
      this.setData({ reply: text });
      this.speech?.enqueue(text, 'normal');
    } catch (err) {
      log.warn('位置查询异常', err);
      this.setData({ reply: UNRESOLVED_TEXT });
      this.speech?.enqueue(UNRESOLVED_TEXT, 'normal');
    } finally {
      this.locationInFlight = false;
    }
  },

  /** 澄清候选点选：以候选名重新解析，取得目的地与话术。 */
  onPickCandidate(event: WechatMiniprogram.TouchEvent): void {
    const name = event.currentTarget.dataset.name;
    if (typeof name === 'string' && name.length > 0) {
      void this.handleFinal(name);
    }
  },

  /** 确认目的地 → 进入导航态（同页融合，原 navigateTo 导航页已退役）。 */
  onStartNav(): void {
    const destination = this.data.destination;
    if (destination === null || this.data.navActive) {
      return;
    }
    this.navDestination = destination;
    this.sessionEnded = false;
    this.overviewAnnounced = false;
    this.routeForDisplay = null;
    this.navPosition = null;
    this.lastTapAt = 0;
    // 收起意图居中卡，切换到导航 HUD
    this.setData({
      navActive: true,
      ended: false,
      state: 'idle' as NavState,
      stateText: NAV_TEXT.idle,
      instruction: '正在确定您的位置…',
      destinationName: destination.name,
      destination: null,
      candidates: [],
      reply: '',
      partial: '',
      recognized: '',
    });
    void this.initNavigation();
  },

  /**
   * 导航编排（自原导航页 bootstrap 平移）：定位管线 + 会话控制器。
   * SpeechQueue 复用页面级实例（onLoad 已建）。
   */
  async initNavigation(): Promise<void> {
    if (this.speech === null) {
      return;
    }
    try {
      await appContext.map.ensureLoaded();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请重试';
      log.warn('导航前地图加载失败', err);
      this.setData({ stateText: message, instruction: message });
      return;
    }

    const source = createBeaconSource(appContext.dataSource, appContext.map);
    this.navSource = source;
    const pipeline = new LocationPipeline({
      client: appContext.http.client,
      map: appContext.map,
      source,
      sequence: new SequenceCounter(),
      onEstimate: (estimate) => this.handleEstimate(estimate),
      onUnusable: (err) => {
        log.warn('定位不可用', err.code);
        vibrateShort();
      },
    });
    this.pipeline = pipeline;

    const pipelineControl: NavPipelineControl = {
      stop: () => pipeline.stop(),
      startNavigating: async () => {
        if (pipeline.isRunning) {
          pipeline.setMode('navigating');
        } else {
          await pipeline.start('navigating');
        }
      },
    };
    this.controller = new NavSessionController({
      client: appContext.http.client,
      map: appContext.map,
      speech: this.speech,
      pipeline: pipelineControl,
      onStateChange: (state) => {
        this.setData({ state, stateText: NAV_TEXT[state] });
        if (state === 'recovering') {
          vibrateLong(); // 震动模式：脱离路线需要引起注意
        }
      },
      onRoute: (route) => {
        // onRoute 先于 controller 播报首条指令（createSession 内顺序）：
        // 概要先进队列，形成「概要 → 首条指令」的既定顺序（REQ-NAV）
        if (!this.overviewAnnounced && this.navDestination !== null) {
          this.overviewAnnounced = true;
          const overview = buildRouteOverview(
            route,
            this.navDestination.name,
            (floorId) => appContext.map.floors[floorId]?.displayName,
          );
          this.speech?.enqueue(overview, 'normal');
          log.info('路径概要:', overview);
        }
        const text = route.segments[0]?.text;
        if (text !== undefined) {
          this.setData({ instruction: text });
        }
        // mock 模拟导航（类高德）：路线就绪后让 mock 用户沿规划路线
        // 以 1.5m/s 行走，沿途按所在楼层采集信标观测（reroute 同样换新路线）
        if (this.navSource instanceof MockBeaconSource && route.polyline.length > 0) {
          this.navSource.updatePath(
            route.polyline.map((p) => ({ x: p.x, y: p.y, floorId: p.floorId ?? undefined })),
          );
          log.info(
            `模拟导航：沿路线行走 ${route.polyline.length} 点 @${MOCK_WALK_SPEED_MPS}m/s`,
          );
        }
        this.routeForDisplay = route;
        this.requestDraw(); // 导航视觉：重绘路线（REQ-NAV 展示层）
      },
      onEnded: (info) => {
        this.sessionEnded = true;
        pipeline.stop();
        if (!info.byArrival && info.reason !== null) {
          this.setData({
            ended: true,
            instruction: '导航已结束',
            stateText: info.reason === 'user_cancelled' ? '已取消导航' : NAV_TEXT.ended,
          });
        } else {
          this.setData({ ended: true }); // 到达/恢复失效：按钮切为「关闭」
        }
      },
    });

    try {
      await pipeline.start('standby');
    } catch (err) {
      log.warn('定位采集启动失败', err);
      this.setData({
        stateText: '定位启动失败，请检查蓝牙后重试',
        instruction: '定位启动失败，请检查蓝牙后重试',
      });
    }
  },

  /** 定位 → 会话（首个可信估计作为起点）；可信位置驱动显示跟随。 */
  handleEstimate(estimate: PositionEstimateResponse): void {
    const position = estimate.position;
    if (position !== undefined && position !== null) {
      // pipeline 仅把 reliable/degraded 交给本回调 → 恢复态(unreliable)天然不推进显示
      this.navPosition = {
        floorId: position.floorId ?? this.navPosition?.floorId ?? this.floorList[0]?.floorId ?? '1',
        x: position.x,
        y: position.y,
      };
      this.followPosition();
    }
    const controller = this.controller;
    if (controller === null || this.sessionEnded) {
      return;
    }
    if (controller.sessionId === null) {
      void this.createSession(estimate);
      return;
    }
    controller.handleEstimate(estimate);
  },

  /** 类高德跟随：楼层自动切换 + 视口对准当前位置（REQ-NAV 展示层）。 */
  followPosition(): void {
    const vp = this.viewport;
    const pos = this.navPosition;
    if (vp === null || pos === null || this.canvasSize.w === 0) {
      return;
    }
    if (pos.floorId !== vp.floorId) {
      const segs = this.wallSegs[pos.floorId];
      if (segs !== undefined) {
        vp.setFloor(pos.floorId, flatBounds(segs));
        const item = this.floorList.find((f) => f.floorId === pos.floorId);
        this.setData({ currentFloor: pos.floorId, floorLabel: item?.displayName ?? pos.floorId });
      }
    }
    // 每次可信估计把视口中心对准位置（导航态低频：standby 8s / 导航 2.5s）
    const { sx, sy } = vp.toScreen(pos.x, pos.y);
    vp.panBy(this.canvasSize.w / 2 - sx, this.canvasSize.h / 2 - sy);
    this.requestDraw();
  },

  async createSession(estimate: PositionEstimateResponse): Promise<void> {
    const controller = this.controller;
    const position = estimate.position;
    const destination = this.navDestination;
    if (controller === null || position === undefined || position === null || destination === null) {
      return;
    }
    try {
      await controller.createSession(destination, {
        x: position.x,
        y: position.y,
        floorId: position.floorId ?? null,
        confidence: estimate.confidence,
        observedAt: estimate.observedAt,
      });
      this.setData({ stateText: NAV_TEXT.active });
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '无法开始导航，请重试';
      log.warn('会话创建失败', err);
      this.setData({ instruction: message });
      this.speech?.enqueue(message, 'normal');
    }
  },

  /** 双击结束导航（防误触，自原导航页平移）。 */
  onEndTap(): void {
    const controller = this.controller;
    if (controller === null || this.sessionEnded || controller.sessionId === null) {
      return;
    }
    const now = Date.now();
    if (now - this.lastTapAt <= DOUBLE_TAP_WINDOW_MS) {
      this.lastTapAt = 0;
      this.setData({ stateText: '正在结束导航…' });
      controller.endSession('user_finished').catch((err) => {
        log.warn('结束会话失败', err);
        this.setData({ stateText: NAV_TEXT.active });
      });
      return;
    }
    this.lastTapAt = now;
    const hint = '再按一次结束导航';
    this.setData({ stateText: hint });
    this.speech?.enqueue(hint, 'normal');
    setTimeout(() => {
      if (this.lastTapAt !== 0 && !this.sessionEnded) {
        this.setData({ stateText: NAV_TEXT[this.data.state as NavState] });
      }
    }, DOUBLE_TAP_WINDOW_MS);
  },

  /** 导航结束后关闭 HUD，回到待命地图。 */
  onDismissNav(): void {
    this.navDestination = null;
    this.routeForDisplay = null;
    this.navPosition = null;
    this.navSource = null;
    this.pipeline?.stop();
    this.pipeline = null;
    this.controller = null;
    this.setData({
      navActive: false,
      ended: false,
      instruction: '',
      destinationName: '',
      state: 'idle' as NavState,
      stateText: NAV_TEXT.idle,
    });
    this.requestDraw();
  },
});
