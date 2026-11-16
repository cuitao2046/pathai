import { SERVICE_PORT } from '../config/env';
import { BUSINESS_SERVICE, parseBusinessOffer } from '../core/discovery/business-protocol';
import { P0_DISCOVERY_PROTOCOL } from '../core/discovery/p0-protocol';
import type { ServiceIdentity } from '../core/discovery/service-identity';
import { createLogger } from '../utils/logger';
import { probeEndpoint } from './p0-candidate';

const log = createLogger('discovery');

const RANDOM_TIMEOUT_MS = 1500;
const OFFER_TIMEOUT_MS = 3000;
const DISCOVERY_PORT = 39001;

export interface BusinessDiscoveryResult {
  url: string;
  identity: ServiceIdentity;
}

function randomRequestId(): Promise<string | null> {
  if (typeof wx.getUserCryptoManager !== 'function') return Promise.resolve(null);
  return new Promise((resolve) => {
    let done = false;
    const finish = (id: string | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(id);
    };
    const timer = setTimeout(() => finish(null), RANDOM_TIMEOUT_MS);
    try {
      wx.getUserCryptoManager().getRandomValues({ length: 16,
        success: ({ randomValues }) => finish(randomValues.byteLength === 16
          ? Array.from(new Uint8Array(randomValues), (value) => value.toString(16).padStart(2, '0')).join('') : null),
        fail: () => finish(null),
      });
    } catch { finish(null); }
  });
}

export async function discoverBusinessCandidate(
  expectedDeviceId: string | null = null,
): Promise<BusinessDiscoveryResult | null> {
  const startedAt = Date.now();
  const elapsed = () => Date.now() - startedAt;
  if (typeof wx.createUDPSocket !== 'function') {
    log.info('UDP 广播未发起：运行时不支持 createUDPSocket');
    return null;
  }
  const requestId = await randomRequestId();
  if (!requestId) {
    log.info('UDP 广播未发起：无法生成随机 requestId');
    return null;
  }
  log.info(`UDP 广播发现开始：${DISCOVERY_PORT}`, `requestId=${requestId}`);
  return new Promise((resolve) => {
    let socket: WechatMiniprogram.UDPSocket;
    try {
      socket = wx.createUDPSocket();
    } catch (err) {
      // 模拟器内常见：UDPSocket(socketId = 1) is already created（UDP 能力/单例限制）
      log.warn('UDP 发现不可用：createUDPSocket 失败', err);
      resolve(null);
      return;
    }
    let finished = false;
    let sent = false;
    let checking = false;
    /** 收到的 OFFER 条数（含身份校验未通过的）。 */
    let offers = 0;
    const attempted = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const finish = (candidate: BusinessDiscoveryResult | null, reason: string) => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      log.info(
        'UDP 广播结束',
        candidate ? `命中=${candidate.url}` : '未命中',
        `OFFER=${offers}`,
        `原因=${reason}`,
        `耗时=${elapsed()}ms`,
      );
      try { socket.close(); } catch { resolve(candidate); return; }
      resolve(candidate);
    };
    socket.onMessage(({ message, remoteInfo }) => {
      if (finished || checking || message.byteLength > 1200) return;
      try {
        const offer = parseBusinessOffer(JSON.parse(String.fromCharCode(...new Uint8Array(message))) as unknown,
          remoteInfo.address, requestId, SERVICE_PORT);
        if (!offer) return;
        offers += 1;
        log.debug('收到 BUSINESS_OFFER', offer, '来自', remoteInfo.address);
        if (attempted.has(offer) || attempted.size >= 3) return;
        attempted.add(offer);
        checking = true;
        void probeEndpoint(offer).then((result) => {
          checking = false;
          if (result.kind !== 'identified') return;
          // 设备身份以 device_id 为准：已知身份时只接受同一台服务端。
          if (expectedDeviceId !== null && result.identity.deviceId !== expectedDeviceId) {
            log.debug('OFFER 探活通过但设备身份不一致，继续等待', offer,
              `期望=${expectedDeviceId}`, `实际=${result.identity.deviceId}`);
            return;
          }
          finish({ url: offer, identity: result.identity }, '命中');
        }, () => { checking = false; });
      } catch { return; }
    });
    socket.onError(() => {
      if (!checking) {
        log.warn('UDP 发现异步错误：广播不可用或被宿主网络拦截');
        finish(null, 'UDP 异步错误');
      }
    });
    socket.onListening(() => {
      if (finished || sent) return;
      sent = true;
      try {
        socket.send({ address: '255.255.255.255', port: DISCOVERY_PORT, setBroadcast: true,
          message: JSON.stringify({ protocol: P0_DISCOVERY_PROTOCOL, version: 1, type: 'BUSINESS_DISCOVER',
            serviceName: BUSINESS_SERVICE, requestId }) });
        log.debug('已发送 BUSINESS_DISCOVER 广播', '255.255.255.255:39001');
      } catch { finish(null, '广播发送失败'); }
    });
    timer = setTimeout(() => {
      log.warn(`UDP 发现超时：${DISCOVERY_PORT} 端口在 ${OFFER_TIMEOUT_MS}ms 内未收到 BUSINESS_OFFER`);
      finish(null, `${OFFER_TIMEOUT_MS}ms 内未收到有效 BUSINESS_OFFER`);
    }, OFFER_TIMEOUT_MS);
    try { socket.bind(); } catch { finish(null, 'socket.bind 失败'); }
  });
}
