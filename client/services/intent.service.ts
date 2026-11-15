/**
 * 意图服务（docs/24 §8 API-04）：POST /api/v1/intents/parse。
 * 请求携带 siteId/mapVersion（取自已加载地图）；服务端做受控意图解析，
 * 外部模型原始响应不透传。
 */
import type { ApiClient } from '../core/http/api-client';
import type {
  IntentLocationPayload,
  IntentParseRequest,
  IntentParseResponse,
} from '../typings/api';
import { ApiClientError } from '../core/http/errors';

/** 地图服务暴露的最小切片（便于测试注入）。 */
export interface MapVersionProvider {
  siteId: string;
  requireVersion(): string;
}

export type IntentTarget =
  | 'location'
  | 'surroundings'
  | 'start'
  | 'cancel'
  | 'end'
  | 'repeat'
  | null;

/**
 * 动作优先分发：新字段 action 存在时以动作为准；否则回退旧 intent 字段，
 * 保证旧响应/旧客户端行为不变（spec REQ-VOICE-12）。
 * - query_current_location → 只答"我在哪"；
 * - describe_surroundings → 现场定位后描述周边要素；
 * - start_navigation → 无目的地的「开始导航」语音命令（复用近期预览或问目的地）；
 * - cancel_navigation / end_navigation / repeat_instruction → 客户端执行导航控制。
 */
export function resolveIntentTarget(
  result: Pick<IntentParseResponse, 'intent' | 'action'>,
): IntentTarget {
  switch (result.action?.name) {
    case 'query_current_location':
      return 'location';
    case 'describe_surroundings':
      return 'surroundings';
    case 'start_navigation':
      return 'start';
    case 'cancel_navigation':
      return 'cancel';
    case 'end_navigation':
      return 'end';
    case 'repeat_instruction':
      return 'repeat';
    case 'speak_answer':
    case 'navigate_to_place':
      // 直答：答案已在本轮 responseText，无需后续；导航：由 destination 渲染确认卡
      return null;
    default:
      break;
  }
  switch (result.intent) {
    case 'query_location':
      return 'location';
    case 'cancel':
      return 'cancel';
    case 'end_navigation':
      return 'end';
    case 'repeat_instruction':
      return 'repeat';
    default:
      return null;
  }
}

export class IntentService {
  constructor(
    private readonly client: ApiClient,
    private readonly map: MapVersionProvider,
  ) {}

  async parse(
    text: string,
    sessionId?: string,
    location?: IntentLocationPayload | null,
  ): Promise<IntentParseResponse> {
    const body: IntentParseRequest = {
      siteId: this.map.siteId,
      mapVersion: this.map.requireVersion(),
      text,
      sessionId: sessionId ?? null,
      locale: 'zh-CN',
      location: location ?? null,
    };
    const resp = await this.client.request<IntentParseResponse>({
      method: 'POST',
      path: '/api/v1/intents/parse',
      body,
    });
    const data = resp.data;
    if (data === null || typeof data.intentId !== 'string') {
      throw new ApiClientError({
        code: 'HTTP_ERROR',
        message: '意图响应格式异常',
        statusCode: resp.statusCode,
        requestId: resp.requestId,
      });
    }
    return data;
  }
}
