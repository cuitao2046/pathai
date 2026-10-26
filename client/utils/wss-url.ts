/**
 * 讯飞签名 URL 获取与缓存（ASR/TTS 各自一个 provider）。
 *
 * 后端 GET /api/v1/voice/{asr,tts}-url 签发的 URL 内嵌 RFC1123 date，
 * 讯飞握手时校验与服务端偏差 ±300s，故缓存须短于响应 expiresIn（240s）；
 * 经 ApiClient 请求，自带 Bearer 注入与 401 重登后重放一次。
 */
import type { ApiClient } from '../core/http/api-client';

export type WssUrlProvider = () => Promise<string>;

interface VoiceUrlResponse {
  url: string;
  expiresIn: number;
}

/**
 * @param client 已装配的 API 客户端
 * @param path   签发端点（/api/v1/voice/asr-url 或 /tts-url）
 * @param ttlMs  本地缓存上限（默认 240s，与后端 expiresIn 一致，取两者较小值）
 * @param now    时钟注入（单测）
 */
export function createWssUrlProvider(
  client: ApiClient,
  path: string,
  ttlMs = 240_000,
  now: () => number = Date.now,
): WssUrlProvider {
  let cached: { url: string; expiresAt: number } | null = null;
  let inflight: Promise<string> | null = null;

  return () => {
    const t = now();
    if (cached !== null && t < cached.expiresAt) {
      return Promise.resolve(cached.url);
    }
    if (inflight !== null) {
      return inflight;
    }
    inflight = client
      .request<VoiceUrlResponse>({ method: 'GET', path })
      .then((resp) => {
        const url = resp.data?.url;
        if (typeof url !== 'string' || url.length === 0) {
          throw new Error('签名 URL 响应缺少 url 字段');
        }
        const expiresInMs = (resp.data?.expiresIn ?? 240) * 1000;
        cached = { url, expiresAt: now() + Math.min(expiresInMs, ttlMs) };
        return url;
      })
      .finally(() => {
        inflight = null;
      });
    return inflight;
  };
}
