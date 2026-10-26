/**
 * 首页：按住说话 → ASR → API-04 意图解析 → 澄清/确认 → 进入导航（C5）。
 * TTS 回复经 SpeechQueue（normal）播报；TTS 失败时走屏幕文本降级（FE-04）。
 */
import { PushToTalk, type PttState } from '../../core/asr/push-to-talk';
import { createBeaconSource } from '../../core/ble/beacon-source-factory';
import { ApiClientError } from '../../core/http/errors';
import { SpeechQueue } from '../../core/speech/speech-queue';
import { IntentService } from '../../services/intent.service';
import { LocationQueryService, UNRESOLVED_TEXT } from '../../services/location-query.service';
import { appContext } from '../../services/app-context';
import { createLogger } from '../../utils/logger';
import type { IntentParseResponse, PlaceCandidate } from '../../typings/api';
import { describeError } from '../../core/http/error-messages';

const log = createLogger('index');

const STATE_TEXT: Record<PttState, string> = {
  idle: '按住说话',
  recording: '松开结束',
  recognizing: '识别中…',
  error: '识别失败，请重试',
};

Page({
  data: {
    mapReady: false,
    loadError: '',
    pttState: 'idle' as PttState,
    pttStateText: STATE_TEXT.idle,
    partial: '',
    recognized: '',
    reply: '',
    fallbackText: '',
    candidates: [] as PlaceCandidate[],
    destination: null as PlaceCandidate | null,
    isMockSource: appContext.dataSource === 'mock',
    mockText: '带我去图书馆',
  },

  speech: null as SpeechQueue | null,
  ptt: null as PushToTalk | null,
  intents: null as IntentService | null,
  locationQuery: null as LocationQueryService | null,
  parseInFlight: false,
  locationInFlight: false,

  onLoad() {
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

  onHide() {
    this.ptt?.handleAppHide(); // 切后台：取消识别，不提交
  },

  onGoSettings() {
    log.info('onGoSettings tap');
    wx.navigateTo({
      url: '/pages/settings/settings',
      fail: (err) => {
        // 真机排查：tap 已触发但跳转失败时，vConsole 可见具体原因
        log.warn('navigateTo settings 失败', JSON.stringify(err));
      },
    });
  },

  // ------------------------------------------------------------------
  // 初始化
  // ------------------------------------------------------------------

  initSpeech() {
    const appendFallback = (text: string) => {
      const next = this.data.fallbackText.length > 0
        ? `${this.data.fallbackText}\n${text}`
        : text;
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

  initPtt() {
    this.ptt = new PushToTalk({
      engine: appContext.asr,
      onStateChange: (state) => {
        this.setData({ pttState: state, pttStateText: STATE_TEXT[state] });
      },
      onPartial: (text) => this.setData({ partial: text }),
      onFinal: (text) => void this.handleFinal(text),
      onError: (message) => this.setData({ reply: message }),
    });
  },

  async bootstrap() {
    try {
      await appContext.map.ensureLoaded();
      this.setData({ mapReady: true, loadError: '' });
      log.info('首页就绪', appContext.map.requireVersion());
    } catch (err) {
      const message = err instanceof ApiClientError ? describeError(err.code) : '地图加载失败，请检查网络';
      log.warn('地图加载失败', err);
      this.setData({ mapReady: false, loadError: message });
    }
  },

  // ------------------------------------------------------------------
  // 按住说话
  // ------------------------------------------------------------------

  onPttStart() {
    if (!this.data.mapReady) {
      return;
    }
    this.ptt?.begin();
  },

  onPttEnd() {
    this.ptt?.end();
  },

  onPttCancel() {
    this.ptt?.cancel();
  },

  onMockTextInput(event: WechatMiniprogram.Input) {
    appContext.setMockAsrText(event.detail.value);
  },

  // ------------------------------------------------------------------
  // 意图闭环
  // ------------------------------------------------------------------

  async handleFinal(text: string) {
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

  applyIntent(result: IntentParseResponse) {
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
  async answerLocation() {
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
      // query() 设计上不抛错，此处为兜底（如地图未就绪抛 MAP_UNAVAILABLE）
      log.warn('位置查询异常', err);
      this.setData({ reply: UNRESOLVED_TEXT });
      this.speech?.enqueue(UNRESOLVED_TEXT, 'normal');
    } finally {
      this.locationInFlight = false;
    }
  },

  /** 澄清候选点选：以候选名重新解析，取得目的地与话术。 */
  onPickCandidate(event: WechatMiniprogram.TouchEvent) {
    const name = event.currentTarget.dataset.name;
    if (typeof name === 'string' && name.length > 0) {
      void this.handleFinal(name);
    }
  },

  onStartNav() {
    const destination = this.data.destination;
    if (destination === null) {
      return;
    }
    const query = `placeId=${encodeURIComponent(destination.placeId)}&name=${encodeURIComponent(destination.name)}&floorId=${encodeURIComponent(destination.floorId)}`;
    wx.navigateTo({ url: `/pages/navigation/navigation?${query}` });
  },
});
