import { API_BASE_URL } from '../config/env';
import { BUSINESS_SERVICE, parseBusinessOffer } from '../core/discovery/business-protocol';
import { P0_DISCOVERY_PROTOCOL } from '../core/discovery/p0-protocol';
import { checkP0Candidate } from './p0-candidate';

const RANDOM_TIMEOUT_MS = 1500;
const OFFER_TIMEOUT_MS = 3000;
const DISCOVERY_PORT = 39001;
const businessPort = Number(/:(\d+)$/.exec(API_BASE_URL)?.[1] ?? 0);

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

export async function discoverBusinessCandidate(): Promise<string | null> {
  if (typeof wx.createUDPSocket !== 'function' || !businessPort) return null;
  const requestId = await randomRequestId();
  if (!requestId) return null;
  return new Promise((resolve) => {
    let socket: WechatMiniprogram.UDPSocket;
    try { socket = wx.createUDPSocket(); } catch { resolve(null); return; }
    let finished = false;
    let sent = false;
    let checking = false;
    const attempted = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const finish = (candidate: string | null) => {
      if (finished) return;
      finished = true;
      if (timer) clearTimeout(timer);
      try { socket.close(); } catch { resolve(candidate); return; }
      resolve(candidate);
    };
    socket.onMessage(({ message, remoteInfo }) => {
      if (finished || checking || message.byteLength > 1200) return;
      try {
        const offer = parseBusinessOffer(JSON.parse(String.fromCharCode(...new Uint8Array(message))) as unknown,
          remoteInfo.address, requestId, businessPort);
        if (!offer || attempted.has(offer) || attempted.size >= 3) return;
        attempted.add(offer);
        checking = true;
        void checkP0Candidate(offer).then((ok) => {
          checking = false;
          if (ok) finish(offer);
        }, () => { checking = false; });
      } catch { return; }
    });
    socket.onError(() => { if (!checking) finish(null); });
    socket.onListening(() => {
      if (finished || sent) return;
      sent = true;
      try {
        socket.send({ address: '255.255.255.255', port: DISCOVERY_PORT, setBroadcast: true,
          message: JSON.stringify({ protocol: P0_DISCOVERY_PROTOCOL, version: 1, type: 'BUSINESS_DISCOVER',
            serviceName: BUSINESS_SERVICE, requestId }) });
      } catch { finish(null); }
    });
    timer = setTimeout(() => finish(null), OFFER_TIMEOUT_MS);
    try { socket.bind(); } catch { finish(null); }
  });
}
