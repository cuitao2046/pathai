/**
 * 首页：按住说话 → ASR → API-04 意图解析 → 澄清/确认 → 进入导航（C5）。
 * TTS 回复经 SpeechQueue（normal）播报；TTS 失败时走屏幕文本降级（FE-04）。
 */
import { DEFAULT_DATA_SOURCE } from '../../config/env';
import { PushToTalk, type PttState } from '../../core/asr/push-to-talk';
import { ApiClientError } from '../../core/http/errors';
import { SpeechQueue } from '../../core/speech/speech-queue';
import { IntentService } from '../../services/intent.service';
import { appContext } from '../../services/app-context';
import { createLogger } from '../../utils/logger';
import type { PlaceCandidate } from '../../typings/api';
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
    isMockSource: DEFAULT_DATA_SOURCE === 'mock',
    mockText: '带我去图书馆',
  },

  speech: null as SpeechQueue | null,
  ptt: null as PushToTalk | null,
  intents: null as IntentService | null,
  parseInFlight: false,

  onLoad() {
    this.initSpeech();
    this.initPtt();
    this.intents = new IntentService(appContext.http.client, appContext.map);
    void this.bootstrap();
  },

  onHide() {
    this.ptt?.handleAppHide(); // 切后台：取消识别，不提交
  },

  onGoSettings() {
    wx.navigateTo({ url: '/pages/settings/settings' });
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

  applyIntent(result: {
    needsClarification: boolean;
    destination?: PlaceCandidate | null;
    candidates: PlaceCandidate[];
    responseText: string;
  }) {
    this.setData({
      reply: result.responseText,
      candidates: result.needsClarification ? result.candidates : [],
      destination: result.destination ?? null,
    });
    this.speech?.enqueue(result.responseText, 'normal');
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
