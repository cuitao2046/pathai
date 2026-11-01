import { parseP0Offer, P0_DISCOVERY_PROTOCOL, P0_DISCOVERY_SERVICE } from '../core/discovery/p0-protocol';

const RANDOM_TIMEOUT_MS = 1500;
const OFFER_TIMEOUT_MS = 3000;
const HTTP_TIMEOUT_MS = 1500;

type P0RandomResult = { requestId: string } | { error: 'random-unavailable' | 'random-failed' | 'random-timeout' };

function requestIdForProbe(): Promise<P0RandomResult> {
  if (typeof wx.getUserCryptoManager !== 'function') return Promise.resolve({ error: 'random-unavailable' });
  return new Promise((resolve) => {
    let completed = false;
    const timer = setTimeout(() => finish({ error: 'random-timeout' }), RANDOM_TIMEOUT_MS);
    function finish(result: P0RandomResult) {
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
            finish({ error: 'random-failed' });
            return;
          }
          finish({ requestId: Array.from(new Uint8Array(randomValues), (value) => value.toString(16).padStart(2, '0')).join('') });
        },
        fail: () => finish({ error: 'random-failed' }),
      });
    } catch {
      finish({ error: 'random-failed' });
    }
  });
}

export type P0ProbeResult = 'udp-and-http' | 'udp-only' | 'timeout' |
  'random-unavailable' | 'random-failed' | 'random-timeout' |
  'udp-unavailable' | 'udp-create-failed' | 'udp-send-failed' | 'udp-error';

export interface P0ProbeDiagnostic {
  result: P0ProbeResult;
  phase: 'binding' | 'listening' | 'requesting' | null;
  errorCode: string | null;
}

function errorCodeFromMessage(message: string): string | null {
  return /(?:errCode|errorCode|code)\s*[:=]\s*(-?\d{1,6})\b/i.exec(message)?.[1] ?? null;
}

export async function runP0LocalProbe(): Promise<P0ProbeDiagnostic> {
  if (typeof wx.createUDPSocket !== 'function') return { result: 'udp-unavailable', phase: null, errorCode: null };
  const random = await requestIdForProbe();
  if ('error' in random) return { result: random.error, phase: null, errorCode: null };
  const requestId = random.requestId;
  return new Promise((resolve) => {
    let socket: WechatMiniprogram.UDPSocket;
    try {
      socket = wx.createUDPSocket();
    } catch {
      resolve({ result: 'udp-create-failed', phase: null, errorCode: null });
      return;
    }
    let finished = false;
    let checking = false;
    let sent = false;
    let phase: P0ProbeDiagnostic['phase'] = 'binding';
    let timer: ReturnType<typeof setTimeout> | null = null;
    let request: WechatMiniprogram.RequestTask | null = null;
    const complete = (result: P0ProbeResult, errorCode: string | null = null) => {
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
        resolve({ result, phase, errorCode });
        return;
      }
      resolve({ result, phase, errorCode });
    };
    socket.onMessage(({ message, remoteInfo }) => {
      if (finished || checking || message.byteLength > 1200) return;
      try {
        const text = String.fromCharCode(...new Uint8Array(message));
        const offer = parseP0Offer(JSON.parse(text) as unknown, remoteInfo.address, requestId);
        if (!offer) return;
        checking = true;
        phase = 'requesting';
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
    socket.onError(({ errMsg }) => complete(checking ? 'udp-only' : 'udp-error', errorCodeFromMessage(errMsg)));
    socket.onListening(() => {
      if (finished || sent) return;
      phase = 'listening';
      sent = true;
      try {
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
        complete('udp-send-failed');
      }
    });
    timer = setTimeout(() => complete('timeout'), OFFER_TIMEOUT_MS);
    try {
      socket.bind();
    } catch {
      complete('udp-send-failed');
    }
  });
}
