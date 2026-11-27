/**
 * 应用级装配（模块级单例）：HTTP 栈、地图服务与语音引擎按环境配置选择 mock/live。
 * 页面共享同一实例；SpeechQueue/PTT 按页面生命周期各自创建（页面互斥前台）。
 * 数据源可在设置页覆盖（写 kv，重启小程序后生效）。
 */
import {
  DEFAULT_DATA_SOURCE,
  DEFAULT_SITE_ID,
  type DataSource,
} from '../config/env';
import { createHttpStack } from '../core/http/api-client';
import { MapService } from '../core/map/map-service';
import { currentServiceBaseUrl, ensureServiceEndpoint, recoverServiceEndpoint } from './service-endpoint';
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
import {
  resolveTtsSpeedGear,
  ttsSpeedValue,
  TTS_SPEED_KEY,
  type TtsSpeedGear,
} from './tts-speed';
import { getDiscoveryMode, setDiscoveryMode, type DiscoveryMode } from './discovery-mode';
import { TrajectoryRecorder } from '../core/calibration/trajectory-recorder';
import type { CalibrationRecord } from '../core/calibration/trajectory-recorder';

const log = createLogger('app');

/** 数据源覆盖的持久化 key（设置页写入，启动时读取）。 */
export const DATA_SOURCE_KEY = 'cfg:dataSource';

/** mock 模拟识别文本的持久化 key（设置页写入，重启后 MockAsrEngine 自动恢复）。 */
export const MOCK_ASR_TEXT_KEY = 'cfg:mockAsrText';

/**
 * 上次成功加载地图的信标 UUID 清单（去重、小写）。
 * 启动楼层探测在地图加载完成前就要开扫，此时拿不到 beaconRegistry，
 * 因此把 UUID 缓存在 kv，下一次启动即可与地图下载并行扫描。
 */
export const BEACON_UUIDS_KEY = 'cfg:beaconUuids';

/**
 * 启动探测用的信标三元组目录（uuid/major/minor → beaconId/floorId）。
 * 与 UUID 清单一起缓存：地图加载前即可把广播判定为命中/未命中，日志不再误报。
 */
export const BEACON_DIRECTORY_KEY = 'cfg:beaconDirectory';

/** 标定模式开关（设置页写入，重启生效；只记录不改算法）。 */
export const CALIBRATION_MODE_KEY = 'cfg:calibrationMode';

/** 现场采集工具开关（设置页写入；只控制首页「采集」入口可见性，即时生效）。 */
export const FIELD_COLLECTION_TOOL_KEY = 'cfg:fieldCollectionTool';

/** 标定操作人（写入 static/dynamic 导出的 operator 字段）。 */
export const CALIBRATION_OPERATOR_KEY = 'cfg:calibrationOperator';

/** 无障碍模式：'a11y' 视障模式（默认，大箭头 + 全屏按住说话）/ 'normal' 普通模式。 */
export const A11Y_MODE_KEY = 'cfg:a11yMode';

/** 全屏按住说话开关（仅视障模式生效，默认开）。 */
export const FULL_SCREEN_PTT_KEY = 'cfg:fullScreenPtt';

/** 导航方向大箭头开关（仅视障模式生效，默认开）。 */
export const NAV_ARROW_KEY = 'cfg:navArrow';

/**
 * 罗盘朝向偏移（度）：地图北与手机罗盘北的夹角，现场两点法标定后填入。
 * 只对绝对方位（罗盘）生效，运动相对朝向不做修正。
 */
export const HEADING_OFFSET_KEY = 'cfg:headingOffsetDeg';

/** 采集页是否叠加「补装 5 枚虚拟信标」的影响（默认关闭＝v11 真实口径）。 */
export const SIM_BEACON_PREVIEW_KEY = 'cfg:simBeaconPreview';

/**
 * 启用指纹库提升导航定位精度（默认关闭；关闭＝仅蓝牙三点定位）。
 * 读取方：地图页导航管线与「我在哪儿」定位查询。
 */
export const FINGERPRINT_DB_KEY = 'cfg:fingerprintDb';

export type A11yMode = 'a11y' | 'normal';

/** 后端 /api/v1/auth/me 返回的当前微信用户身份与授权。 */
export interface ClientIdentity {
  userId: string;
  /** 授权用户（测试人员）：可见全部设置与现场采集菜单。 */
  isTester: boolean;
  canApplyConfig: boolean;
}

/** 标定模式轨迹缓冲 key。 */
export const CALIBRATION_RECORDS_KEY = 'cal:trajectory:v1';

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
    onPlay: (listener) => audio.onPlay(listener),
    onError: (listener) => audio.onError(() => listener()),
  };
}

class AppContext {
  readonly dataSource: DataSource = resolveDataSource();
  /** 语音交互（ASR+TTS）是否用 mock 引擎：独立开关，未设置过则跟随数据源。 */
  readonly voiceMock: boolean = resolveVoiceMock(getJson<boolean>(VOICE_MOCK_KEY), this.dataSource);
  /** HTTP 栈与地图服务共用同一运行时地址（启动时由 service-endpoint 解析）。 */
  readonly http = createHttpStack(() => currentServiceBaseUrl(), { wechatLogin: true,
    recovery: { prepare: ensureServiceEndpoint, failed: recoverServiceEndpoint },
  });
  readonly map = new MapService({
    client: this.http.client,
    siteId: DEFAULT_SITE_ID,
    baseUrl: () => currentServiceBaseUrl(),
  });
  /** 启动提示音（本地音频，离线可播；按住说话可打断）。 */
  readonly voicePrompts = new LocalVoice({ createAudio: createWxAudio });
  /** 标定模式是否开启（启动时读取；设置页写入后需重启生效）。 */
  readonly calibrationMode: boolean = getJson<boolean>(CALIBRATION_MODE_KEY) === true;
  /** 标定轨迹记录器：仅标定模式开启时由地图页写入。 */
  readonly calibration = new TrajectoryRecorder({
    load: () => getJson<CalibrationRecord[]>(CALIBRATION_RECORDS_KEY) ?? [],
    save: (records) => setJson(CALIBRATION_RECORDS_KEY, records),
  });

  private readonly ttsInstance: TtsEngine;
  private readonly asrInstance: AsrEngine;
  /** 当前用户身份（启动后异步读取；未读取/失败按普通用户处理，不展示授权功能）。 */
  private identity: ClientIdentity | null = null;
  private identityInFlight: Promise<ClientIdentity | null> | null = null;

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
        // 每句合成前读取一次档位：设置页改语速后下一句即生效
        speed: () => ttsSpeedValue(this.getTtsSpeedGear()),
      });
    }
  }

  get tts(): TtsEngine {
    return this.ttsInstance;
  }

  get asr(): AsrEngine {
    return this.asrInstance;
  }

  /**
   * 读取并缓存当前微信用户的身份权限（同一时刻只发一个请求）。
   * 失败返回 null 且保留上次结果；调用方须按“未授权”渲染，不得暴露授权功能。
   */
  async refreshIdentity(): Promise<ClientIdentity | null> {
    if (this.identityInFlight !== null) {
      return this.identityInFlight;
    }
    const task = this.http.client
      .request<ClientIdentity>({ method: 'GET', path: '/api/v1/auth/me' })
      .then((response) => response.data)
      .catch((err) => {
        log.warn('身份权限读取失败', err);
        return null;
      });
    this.identityInFlight = task;
    task.then(
      (identity) => {
        if (identity !== null) {
          this.identity = identity;
        }
        if (this.identityInFlight === task) {
          this.identityInFlight = null;
        }
      },
      () => {
        if (this.identityInFlight === task) {
          this.identityInFlight = null;
        }
      },
    );
    return task;
  }

  /** 当前已知身份（尚未读取或读取失败时为 null）。 */
  getIdentity(): ClientIdentity | null {
    return this.identity;
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

  /** 设置页：写入在线 TTS 语速档位（下一句播报即时生效）。 */
  setTtsSpeedGear(gear: TtsSpeedGear): void {
    setJson(TTS_SPEED_KEY, gear);
  }

  /** TTS 引擎/设置页读取：语速档位（未设置或非法值回落「标准」）。 */
  getTtsSpeedGear(): TtsSpeedGear {
    return resolveTtsSpeedGear(getJson<TtsSpeedGear>(TTS_SPEED_KEY));
  }

  /** 设置页：写入标定模式开关（重启小程序后生效）。 */
  setCalibrationModeSetting(on: boolean): void {
    setJson(CALIBRATION_MODE_KEY, on);
  }

  /** 设置页回显：标定模式的最新设置值（区别于本次启动实际生效的 calibrationMode）。 */
  getCalibrationModeSetting(): boolean {
    return getJson<boolean>(CALIBRATION_MODE_KEY) === true;
  }

  setCalibrationOperator(name: string): void {
    setString(CALIBRATION_OPERATOR_KEY, name);
  }

  getCalibrationOperator(): string {
    return getString(CALIBRATION_OPERATOR_KEY) ?? '';
  }

  /** 设置页：写入现场采集工具开关（即时生效，仅控制入口可见性）。 */
  setFieldCollectionToolSetting(on: boolean): void {
    setJson(FIELD_COLLECTION_TOOL_KEY, on);
  }

  /** 首页/设置页回显：现场采集工具开关（默认关闭）。 */
  getFieldCollectionToolSetting(): boolean {
    return getJson<boolean>(FIELD_COLLECTION_TOOL_KEY) === true;
  }

  /** 设置页：写入「采集页叠加补装信标影响」开关（即时生效；默认关闭）。 */
  setSimBeaconPreviewSetting(on: boolean): void {
    setJson(SIM_BEACON_PREVIEW_KEY, on);
  }

  /** 采集页读取：是否叠加 5 枚未安装虚拟信标的仿真覆盖（默认关闭＝v11 真实口径）。 */
  getSimBeaconPreviewSetting(): boolean {
    return getJson<boolean>(SIM_BEACON_PREVIEW_KEY) === true;
  }

  /** 设置页：写入「启用指纹库提升导航定位精度」（即时生效；默认关闭）。 */
  setFingerprintDbSetting(on: boolean): void {
    setJson(FINGERPRINT_DB_KEY, on);
  }

  /** 定位链路读取：指纹库开关（默认关闭＝仅蓝牙三点定位）。 */
  getFingerprintDbSetting(): boolean {
    return getJson<boolean>(FINGERPRINT_DB_KEY) === true;
  }

  /** 设置页：写入无障碍模式（即时生效；默认「视障模式」）。 */
  setA11yMode(mode: A11yMode): void {
    setJson(A11Y_MODE_KEY, mode);
  }

  /** 首页/设置页回显：无障碍模式（未设置过返回默认 'a11y'）。 */
  getA11yMode(): A11yMode {
    return getJson<A11yMode>(A11Y_MODE_KEY) === 'normal' ? 'normal' : 'a11y';
  }

  /** 设置页：写入服务发现方式（重启小程序生效；默认自动）。 */
  setDiscoveryModeSetting(mode: DiscoveryMode): void {
    setDiscoveryMode(mode);
  }

  /** 设置页回显：服务发现方式（未设置过返回 'auto'）。 */
  getDiscoveryModeSetting(): DiscoveryMode {
    return getDiscoveryMode();
  }

  /** 设置页：写入全屏按住说话开关（仅视障模式生效，即时生效）。 */
  setFullScreenPttSetting(on: boolean): void {
    setJson(FULL_SCREEN_PTT_KEY, on);
  }

  /** 首页/设置页回显：全屏按住说话开关（默认开）。 */
  getFullScreenPttSetting(): boolean {
    return getJson<boolean>(FULL_SCREEN_PTT_KEY) !== false;
  }

  /** 设置页：写入导航方向箭头开关（仅视障模式生效，即时生效）。 */
  setNavArrowSetting(on: boolean): void {
    setJson(NAV_ARROW_KEY, on);
  }

  /** 首页/设置页回显：导航方向箭头开关（默认开）。 */
  getNavArrowSetting(): boolean {
    return getJson<boolean>(NAV_ARROW_KEY) !== false;
  }

  /** 设置页：写入罗盘朝向偏移（度，即时生效；范围 -180~180）。 */
  setHeadingOffsetSetting(deg: number): void {
    setJson(HEADING_OFFSET_KEY, deg);
  }

  /** 朝向读取：罗盘偏移（默认 0；非法值按 0 处理，避免把方向整体带偏）。 */
  getHeadingOffsetSetting(): number {
    const stored = getJson<number>(HEADING_OFFSET_KEY);
    if (typeof stored !== 'number' || !Number.isFinite(stored)) {
      return 0;
    }
    return Math.max(-180, Math.min(180, stored));
  }
}

export const appContext = new AppContext();
