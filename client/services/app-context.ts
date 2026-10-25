/**
 * 应用级装配（模块级单例）：HTTP 栈、地图服务与语音引擎按环境配置选择 mock/live。
 * 页面共享同一实例；SpeechQueue/PTT 按页面生命周期各自创建（页面互斥前台）。
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
import type { TtsEngine } from '../core/speech/tts-engine';
import { MockAsrEngine } from '../core/asr/mock-asr';
import { WechatsiAsrEngine } from '../core/asr/wechatsi-asr';
import { MockTtsEngine } from '../core/speech/mock-tts';
import { WechatsiTts } from '../core/speech/wechatsi-tts';

class AppContext {
  readonly dataSource: DataSource = DEFAULT_DATA_SOURCE;
  readonly http = createHttpStack(API_BASE_URL);
  readonly map = new MapService({ client: this.http.client, siteId: DEFAULT_SITE_ID });

  private readonly ttsInstance: TtsEngine;
  private readonly asrInstance: AsrEngine;

  constructor() {
    if (DEFAULT_DATA_SOURCE === 'mock') {
      // 开发者工具：无 iBeacon/插件环境不确定，先用 Mock 引擎联调业务闭环
      this.ttsInstance = new MockTtsEngine();
      this.asrInstance = new MockAsrEngine();
    } else {
      this.ttsInstance = new WechatsiTts();
      this.asrInstance = new WechatsiAsrEngine();
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
}

export const appContext = new AppContext();
