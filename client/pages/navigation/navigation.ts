/**
 * 导航页（C7）：定位管线 → 会话控制器 → 语音播报的运行时编排。
 * - 启动：地图就绪 → 待命采集 → 首个可信定位作为起点 → 创建会话 → 导航档采集；
 * - 播报：页面级 SpeechQueue（与首页同模式），TTS 失败走屏幕文本降级（FE-04）；
 * - 生命周期：onHide 停采集、onShow 查询重对齐恢复（FE-05）、onUnload 上报取消；
 * - 结束：双击结束按钮（500ms 内）防误触；到达判定权在服务端，客户端从不发送 arrived。
 */
import { DEFAULT_DATA_SOURCE } from '../../config/env';
import type { BeaconSourceLike } from '../../core/ble/beacon-source';
import { LiveBeaconScanner } from '../../core/ble/beacon-scanner';
import { MockBeaconSource } from '../../core/ble/mock-beacon-source';
import { describeError } from '../../core/http/error-messages';
import { ApiClientError } from '../../core/http/errors';
import {
  NavSessionController,
  type NavPipelineControl,
  type NavState,
} from '../../core/nav/nav-session-controller';
import { LocationPipeline } from '../../core/position/location-pipeline';
import { SequenceCounter } from '../../core/position/sequence-counter';
import { SpeechQueue } from '../../core/speech/speech-queue';
import { appContext } from '../../services/app-context';
import { vibrateLong, vibrateShort } from '../../utils/haptic';
import { createLogger } from '../../utils/logger';
import type { PlaceCandidate, PositionEstimateResponse } from '../../typings/api';

const log = createLogger('navigation');

const STATE_TEXT: Record<NavState, string> = {
  idle: '正在定位…',
  active: '导航中',
  recovering: '信号弱，正在重新定位，请原地稍候',
  arrived: '已到达目的地',
  ended: '导航结束',
};

const DOUBLE_TAP_WINDOW_MS = 500;

Page({
  data: {
    destinationName: '',
    state: 'idle' as NavState,
    stateText: STATE_TEXT.idle,
    instruction: '正在确定您的位置…',
    fallbackText: '',
    ended: false,
    isMockSource: DEFAULT_DATA_SOURCE === 'mock',
  },

  speech: null as SpeechQueue | null,
  controller: null as NavSessionController | null,
  pipeline: null as LocationPipeline | null,
  destination: null as PlaceCandidate | null,
  lastTapAt: 0,
  sessionEnded: false,

  onLoad(query: Record<string, string | undefined>) {
    this.destination = {
      placeId: decodeURIComponent(query.placeId ?? ''),
      name: decodeURIComponent(query.name ?? '目的地'),
      floorId: decodeURIComponent(query.floorId ?? ''),
    };
    this.setData({ destinationName: this.destination.name });
    this.initSpeech();
    void this.bootstrap();
  },

  onHide() {
    this.controller?.handleBackground(); // 停采集，恢复交给 onShow
  },

  onShow() {
    const controller = this.controller;
    if (controller !== null && controller.sessionId !== null && !this.sessionEnded) {
      void controller.handleForeground();
    }
  },

  onUnload() {
    const controller = this.controller;
    if (controller !== null && !this.sessionEnded && controller.sessionId !== null) {
      controller.endSession('user_cancelled').catch(() => undefined);
    }
    this.pipeline?.stop();
  },

  // ------------------------------------------------------------------
  // 启动
  // ------------------------------------------------------------------

  initSpeech() {
    const appendFallback = (text: string) => {
      const next =
        this.data.fallbackText.length > 0 ? `${this.data.fallbackText}\n${text}` : text;
      this.setData({ fallbackText: next });
    };
    this.speech = new SpeechQueue({
      engine: appContext.tts,
      onUnavailable: (lastText) => {
        log.warn('TTS 不可用，降级文本展示', lastText);
        appendFallback('语音播报不可用，已切换为文字提示');
      },
      onTextFallback: appendFallback,
    });
  },

  /** mock 轨迹：以地图包首个信标为锚点的短点列，保证解算可靠。 */
  makeMockSource(): BeaconSourceLike {
    const beacons = appContext.map.beacons.filter((b) => typeof b.uuid === 'string');
    const anchor = beacons[0];
    const path = anchor
      ? [
          { x: anchor.x, y: anchor.y },
          { x: anchor.x + 1, y: anchor.y + 1 },
        ]
      : [
          { x: 0, y: 0 },
          { x: 1, y: 1 },
        ];
    return new MockBeaconSource({ beacons: appContext.map.beacons, floorId: anchor?.floorId ?? '1', path });
  },

  async bootstrap() {
    if (this.speech === null) {
      return;
    }
    try {
      await appContext.map.ensureLoaded();
    } catch (err) {
      const message =
        err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请返回重试';
      log.warn('地图加载失败，导航无法开始', err);
      this.setData({ stateText: message, instruction: message });
      return;
    }

    const source: BeaconSourceLike =
      DEFAULT_DATA_SOURCE === 'mock'
        ? this.makeMockSource()
        : new LiveBeaconScanner(appContext.map.beaconRegistry.scanUuids);

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
        this.setData({ state, stateText: STATE_TEXT[state] });
        if (state === 'recovering') {
          vibrateLong(); // 震动模式：脱离路线需要引起注意
        }
      },
      onRoute: (route) => {
        const text = route.segments[0]?.text;
        if (text !== undefined) {
          this.setData({ instruction: text });
        }
      },
      onEnded: (info) => {
        this.sessionEnded = true;
        pipeline.stop();
        if (!info.byArrival && info.reason !== null) {
          this.setData({
            instruction: '导航已结束',
            stateText: info.reason === 'user_cancelled' ? '已取消导航' : STATE_TEXT.ended,
          });
        }
      },
    });

    try {
      await pipeline.start('standby');
    } catch (err) {
      log.warn('定位采集启动失败', err);
      this.setData({ stateText: '定位启动失败，请检查蓝牙后重试', instruction: '定位启动失败，请检查蓝牙后重试' });
    }
  },

  // ------------------------------------------------------------------
  // 定位 → 会话
  // ------------------------------------------------------------------

  handleEstimate(estimate: PositionEstimateResponse) {
    const controller = this.controller;
    if (controller === null || this.sessionEnded) {
      return;
    }
    if (controller.sessionId === null) {
      void this.createSession(estimate); // 首个可信估计作为起点
      return;
    }
    controller.handleEstimate(estimate);
  },

  async createSession(estimate: PositionEstimateResponse) {
    const controller = this.controller;
    const position = estimate.position;
    const destination = this.destination;
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
      this.setData({ stateText: STATE_TEXT.active });
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '无法开始导航，请重试';
      log.warn('会话创建失败', err);
      this.setData({ instruction: message });
      this.speech?.enqueue(message, 'normal');
    }
  },

  onBackHome() {
    wx.navigateBack({ delta: 1 });
  },

  // ------------------------------------------------------------------
  // 双击结束（防误触）
  // ------------------------------------------------------------------

  onEndTap() {
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
        this.setData({ stateText: STATE_TEXT.active });
      });
      return;
    }
    this.lastTapAt = now;
    const hint = '再按一次结束导航';
    this.setData({ stateText: hint });
    this.speech?.enqueue(hint, 'normal');
    setTimeout(() => {
      if (this.lastTapAt !== 0 && !this.sessionEnded) {
        this.setData({ stateText: STATE_TEXT[this.data.state as NavState] });
      }
    }, DOUBLE_TAP_WINDOW_MS);
  },
});
