/**
 * 应用级装配（模块级单例）：HTTP 栈、地图服务与语音引擎按环境配置选择 mock/live。
 * 页面共享同一实例；SpeechQueue/PTT 按页面生命周期各自创建（页面互斥前台）。
 * 数据源可在设置页覆盖（写 kv，重启小程序后生效）。
 */
import {
  API_BASE_URL,
  DEFAULT_DATA_SOURCE,
  DEFAULT_SITE_ID,
  type DataSource,
} from '../config/env';
import { createHttpStack } from '../core/http/api-client';
import { MapService } from '../core/map/map-service';
import type { AsrEngine } from '../core/asr/asr-engine';
import { XfyunAsrEngine } from '../core/asr/xfyun-asr';
import type { TtsEngine } from '../core/speech/tts-engine';
import { MockAsrEngine } from '../core/asr/mock-asr';
import { MockTtsEngine } from '../core/speech/mock-tts';
import { XfyunTts } from '../core/speech/xfyun-tts';
import {
  LocalVoice,
  type LocalVoiceClipHandle,
} from '../core/speech/local-voice';
import { createWssUrlProvider } from '../utils/wss-url';
import { createLogger } from '../utils/logger';
import { getString, getJson, setJson, setString } from '../utils/kv';
import { resolveVoiceMock, VOICE_MOCK_KEY } from './voice-mock';

const log = createLogger('app');

/** 数据源覆盖的持久化 key（设置页写入，启动时读取）。 */
export const DATA_SOURCE_KEY = 'cfg:dataSource';

/** mock 模拟识别文本的持久化 key（设置页写入，重启后 MockAsrEngine 自动恢复）。 */
export const MOCK_ASR_TEXT_KEY = 'cfg:mockAsrText';

function resolveDataSource(): DataSource {
  const stored = getJson<DataSource>(DATA_SOURCE_KEY);
  return stored === 'mock' || stored === 'live' ? stored : DEFAULT_DATA_SOURCE;
}

/** 本地提示音句柄（InnerAudioContext 适配；文件缺失由 onError 静默跳过）。 */
function createWxAudio(src: string): LocalVoiceClipHandle {
  const audio = wx.createInnerAudioContext();
  audio.src = src;
  // 提示音是启动安全提示：静音开关打开时也要能听到
  audio.obeyMuteSwitch = false;
  return {
    play: () => audio.play(),
    stop: () => audio.stop(),
    destroy: () => audio.destroy(),
    onEnded: (listener) => audio.onEnded(listener),
    onError: (listener) => audio.onError(() => listener()),
  };
}

class AppContext {
  readonly dataSource: DataSource = resolveDataSource();
  /** 语音交互（ASR+TTS）是否用 mock 引擎：独立开关，未设置过则跟随数据源。 */
  readonly voiceMock: boolean = resolveVoiceMock(getJson<boolean>(VOICE_MOCK_KEY), this.dataSource);
  readonly http = createHttpStack(API_BASE_URL);
  readonly map = new MapService({ client: this.http.client, siteId: DEFAULT_SITE_ID });
  /** 启动提示音（本地音频，离线可播；按住说话可打断）。 */
  readonly voicePrompts = new LocalVoice({ createAudio: createWxAudio });

  private readonly ttsInstance: TtsEngine;
  private readonly asrInstance: AsrEngine;

  constructor() {
    log.info('数据源 =', this.dataSource, '语音mock =', this.voiceMock);
    if (this.voiceMock) {
      // 语音 mock：Mock 引擎联调业务闭环（模拟文本在设置页配置，本地持久化）
      this.ttsInstance = new MockTtsEngine();
      const mockAsr = new MockAsrEngine();
      const storedText = getString(MOCK_ASR_TEXT_KEY);
      if (storedText !== null && storedText.length > 0) {
        mockAsr.nextText = storedText; // 恢复上次设置页配置的模拟识别文本
      }
      this.asrInstance = mockAsr;
    } else {
      // 语音真实调用讯飞：wss 直连（签名 URL 由后端签发，密钥留在服务端，
      // 凭据按 provider 缓存 ≤4min）。失败由 PTT/SpeechQueue 既有错误路径承接。
      this.asrInstance = new XfyunAsrEngine({
        credential: createWssUrlProvider(this.http.client, '/api/v1/voice/asr-url'),
      });
      this.ttsInstance = new XfyunTts({
        credential: createWssUrlProvider(this.http.client, '/api/v1/voice/tts-url'),
      });
    }
  }

  get tts(): TtsEngine {
    return this.ttsInstance;
  }

  get asr(): AsrEngine {
    return this.asrInstance;
  }

  /** 调试：注入 mock 识别文本（写引擎 + 本地缓存，重启后 mock 引擎自动恢复）。 */
  setMockAsrText(text: string): void {
    setString(MOCK_ASR_TEXT_KEY, text);
    if (this.asrInstance instanceof MockAsrEngine) {
      this.asrInstance.nextText = text;
    }
  }

  /** 读取已缓存的 mock 识别文本（未设置过返回空串）。 */
  getMockAsrText(): string {
    return getString(MOCK_ASR_TEXT_KEY) ?? '';
  }

  /** 设置页：写入数据源覆盖（重启小程序后生效）。 */
  setDataSourceOverride(source: DataSource): void {
    setJson(DATA_SOURCE_KEY, source);
  }

  /** 设置页：写入语音 mock 开关（重启小程序后生效）。 */
  setVoiceMockSetting(on: boolean): void {
    setJson(VOICE_MOCK_KEY, on);
  }

  /** 设置页回显：语音 mock 的最新设置值（未设置过则跟随数据源）。 */
  getVoiceMockSetting(): boolean {
    return resolveVoiceMock(getJson<boolean>(VOICE_MOCK_KEY), this.dataSource);
  }
}

export const appContext = new AppContext();
