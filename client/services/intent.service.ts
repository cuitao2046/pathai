/**
 * 意图服务（docs/24 §8 API-04）：POST /api/v1/intents/parse。
 * 请求携带 siteId/mapVersion（取自已加载地图）；服务端做受控意图解析，
 * 外部模型原始响应不透传。
 */
import type { ApiClient } from '../core/http/api-client';
import type { IntentParseRequest, IntentParseResponse } from '../typings/api';
import { ApiClientError } from '../core/http/errors';

/** 地图服务暴露的最小切片（便于测试注入）。 */
export interface MapVersionProvider {
  siteId: string;
  requireVersion(): string;
}

export class IntentService {
  constructor(
    private readonly client: ApiClient,
    private readonly map: MapVersionProvider,
  ) {}

  async parse(text: string, sessionId?: string): Promise<IntentParseResponse> {
    const body: IntentParseRequest = {
      siteId: this.map.siteId,
      mapVersion: this.map.requireVersion(),
      text,
      sessionId: sessionId ?? null,
      locale: 'zh-CN',
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
