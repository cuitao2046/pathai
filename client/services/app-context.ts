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
import { SherpaOnnxAsrEngine } from '../core/asr/sherpa-onnx-asr';
import type { TtsEngine } from '../core/speech/tts-engine';
import { MockAsrEngine } from '../core/asr/mock-asr';
import { MockTtsEngine } from '../core/speech/mock-tts';
import { WechatsiTts } from '../core/speech/wechatsi-tts';
import { getJson, setJson } from '../utils/kv';

/** 数据源覆盖的持久化 key（设置页写入，启动时读取）。 */
export const DATA_SOURCE_KEY = 'cfg:dataSource';

function resolveDataSource(): DataSource {
  const stored = getJson<DataSource>(DATA_SOURCE_KEY);
  return stored === 'mock' || stored === 'live' ? stored : DEFAULT_DATA_SOURCE;
}

class AppContext {
  readonly dataSource: DataSource = resolveDataSource();
  readonly http = createHttpStack(API_BASE_URL);
  readonly map = new MapService({ client: this.http.client, siteId: DEFAULT_SITE_ID });

  private readonly ttsInstance: TtsEngine;
  private readonly asrInstance: AsrEngine;

  constructor() {
    if (this.dataSource === 'mock') {
      // 开发者工具：无 iBeacon/插件环境不确定，先用 Mock 引擎联调业务闭环
      this.ttsInstance = new MockTtsEngine();
      this.asrInstance = new MockAsrEngine();
    } else {
      // live：端侧 Sherpa-Onnx WASM（个人主体无法添加同声传译插件）。
      // 预热：提前下载模型+初始化 wasm，首按 PTT 前就绪；失败留到 start 重试。
      const sherpa = new SherpaOnnxAsrEngine();
      sherpa.warmup();
      this.asrInstance = sherpa;
      // TTS 仍走同声传译（插件缺失时由 SpeechQueue 降级震动+文本，FE-04）
      this.ttsInstance = new WechatsiTts();
    }
  }

  get tts(): TtsEngine {
    return this.ttsInstance;
  }

  get asr(): AsrEngine {
    return this.asrInstance;
  }

  /** 调试：注入 mock 识别文本（live 模式忽略）。 */
  setMockAsrText(text: string): void {
    if (this.asrInstance instanceof MockAsrEngine) {
      this.asrInstance.nextText = text;
    }
  }

  /** 设置页：写入数据源覆盖（重启小程序后生效）。 */
  setDataSourceOverride(source: DataSource): void {
    setJson(DATA_SOURCE_KEY, source);
  }
}

export const appContext = new AppContext();
