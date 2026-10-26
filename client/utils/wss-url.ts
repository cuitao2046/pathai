/**
 * 讯飞签名凭据获取与缓存（ASR/TTS 各自一个 provider）。
 *
 * 后端 GET /api/v1/voice/{asr,tts}-url 签发 {url, appId, expiresIn}：
 * - url 内嵌 RFC1123 date，讯飞握手时校验与服务端偏差 ±300s，
 *   故缓存须短于 expiresIn（240s）；
 * - appId 用于讯飞请求帧 header.app_id（非机密，随签发响应下发）；
 * - 经 ApiClient 请求，自带 Bearer 注入与 401 重登后重放一次。
 */
import type { ApiClient } from '../core/http/api-client';

/** 连接讯飞所需的凭据：签名 URL + 请求帧 app_id。 */
export interface VoiceCredential {
  url: string;
  appId: string;
}

export type WssCredentialProvider = () => Promise<VoiceCredential>;

interface VoiceUrlResponse {
  url: string;
  appId: string;
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
): WssCredentialProvider {
  let cached: { cred: VoiceCredential; expiresAt: number } | null = null;
  let inflight: Promise<VoiceCredential> | null = null;

  return () => {
    const t = now();
    if (cached !== null && t < cached.expiresAt) {
      return Promise.resolve(cached.cred);
    }
    if (inflight !== null) {
      return inflight;
    }
    inflight = client
      .request<VoiceUrlResponse>({ method: 'GET', path })
      .then((resp) => {
        const url = resp.data?.url;
        const appId = resp.data?.appId;
        if (typeof url !== 'string' || url.length === 0) {
          throw new Error('签名 URL 响应缺少 url 字段');
        }
        if (typeof appId !== 'string' || appId.length === 0) {
          throw new Error('签名 URL 响应缺少 appId 字段');
        }
        const cred: VoiceCredential = { url, appId };
        const expiresInMs = (resp.data?.expiresIn ?? 240) * 1000;
        cached = { cred, expiresAt: now() + Math.min(expiresInMs, ttlMs) };
        return cred;
      })
      .finally(() => {
        inflight = null;
      });
    return inflight;
  };
}
