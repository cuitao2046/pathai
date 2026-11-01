import { parseP0Offer, P0_DISCOVERY_PROTOCOL, P0_DISCOVERY_SERVICE } from '../core/discovery/p0-protocol';

const RANDOM_TIMEOUT_MS = 1500;
const OFFER_TIMEOUT_MS = 3000;
const HTTP_TIMEOUT_MS = 1500;

function requestIdForProbe(): Promise<string | null> {
  if (typeof wx.getUserCryptoManager !== 'function') return Promise.resolve(null);
  return new Promise((resolve) => {
    let completed = false;
    const timer = setTimeout(() => finish(null), RANDOM_TIMEOUT_MS);
    function finish(result: string | null) {
      if (completed) return;
      completed = true;
      clearTimeout(timer);
      resolve(result);
    }
    try {
      wx.getUserCryptoManager().getRandomValues({
        length: 16,
        success: ({ randomValues }) => {
          if (randomValues.byteLength !== 16) {
            finish(null);
            return;
          }
          finish(Array.from(new Uint8Array(randomValues), (value) => value.toString(16).padStart(2, '0')).join(''));
        },
        fail: () => finish(null),
      });
    } catch {
      finish(null);
    }
  });
}

export type P0ProbeResult = 'udp-and-http' | 'udp-only' | 'timeout' | 'unsupported';

export async function runP0LocalProbe(): Promise<P0ProbeResult> {
  if (typeof wx.createUDPSocket !== 'function') return Promise.resolve('unsupported');
  const requestId = await requestIdForProbe();
  if (requestId === null) return Promise.resolve('unsupported');
  return new Promise((resolve) => {
    let socket: WechatMiniprogram.UDPSocket;
    try {
      socket = wx.createUDPSocket();
    } catch {
      resolve('unsupported');
      return;
    }
    let finished = false;
    let checking = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let request: WechatMiniprogram.RequestTask | null = null;
    const complete = (result: P0ProbeResult) => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      if (result === 'udp-only') {
        try {
          request?.abort();
        } catch {
          request = null;
        }
      }
      try {
        socket.close();
      } catch {
        resolve(result);
        return;
      }
      resolve(result);
    };
    socket.onMessage(({ message, remoteInfo }) => {
      if (finished || checking || message.byteLength > 1200) return;
      try {
        const text = String.fromCharCode(...new Uint8Array(message));
        const offer = parseP0Offer(JSON.parse(text) as unknown, remoteInfo.address, requestId);
        if (!offer) return;
        checking = true;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => complete('udp-only'), HTTP_TIMEOUT_MS + 100);
        request = wx.request({
          url: `http://${offer.address}:${offer.port}/local/p0/probe`,
          method: 'GET',
          timeout: HTTP_TIMEOUT_MS,
          success: ({ statusCode, data }) => {
            const body = data as { protocol?: unknown; ready?: unknown };
            complete(statusCode === 200 && body?.protocol === P0_DISCOVERY_PROTOCOL && body.ready === true
              ? 'udp-and-http' : 'udp-only');
          },
          fail: () => complete('udp-only'),
        });
      } catch {
        if (checking) complete('udp-only');
        return;
      }
    });
    socket.onError(() => complete(checking ? 'udp-only' : 'unsupported'));
    try {
      socket.bind();
      timer = setTimeout(() => complete('timeout'), OFFER_TIMEOUT_MS);
      socket.send({
        address: '255.255.255.255',
        port: 39001,
        setBroadcast: true,
        message: JSON.stringify({
          protocol: P0_DISCOVERY_PROTOCOL,
          version: 1,
          type: 'DISCOVER',
          serviceName: P0_DISCOVERY_SERVICE,
          requestId,
        }),
      });
    } catch {
      complete('unsupported');
    }
  });
}
