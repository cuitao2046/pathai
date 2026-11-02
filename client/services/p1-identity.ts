import { API_BASE_URL } from '../config/env';
import { isP1Endpoint, isP1Identifier, isRecord } from '../core/discovery/p1-auth';
import { P1Candidate, P1Resolver, P1Resolution } from '../core/discovery/p1-resolver';
import {
  activationRequest, parseP1Address, parseP1Offer, parseP1Pairing, P1Pairing, validActivation, validP1Probe,
} from '../core/discovery/p1-protocol';
import { getString, remove, setString } from '../utils/kv';

const PAIRING_KEY = 'discovery:p1-pairing';
const VERIFIED_KEY = 'discovery:p1-last-address';
const TIMEOUT_MS = 1500;
const DISCOVERY_MS = 2500;
const resolver = new P1Resolver();
let pairingGeneration = 0;
let pairingStorageFailed = false;

function verifiedKey(pairing: P1Pairing): string {
  return `${VERIFIED_KEY}:${pairing.deviceId}:${pairing.pairId}`;
}

function randomNonce(): Promise<string | null> {
  if (typeof wx.getUserCryptoManager !== 'function') return Promise.resolve(null);
  return new Promise((resolve) => {
    let done = false;
    const complete = (result: string | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => complete(null), TIMEOUT_MS);
    try {
      wx.getUserCryptoManager().getRandomValues({ length: 16,
        success: ({ randomValues }) => complete(randomValues.byteLength === 16
          ? Array.from(new Uint8Array(randomValues), (byte) => byte.toString(16).padStart(2, '0')).join('') : null),
        fail: () => complete(null),
      });
    } catch { complete(null); }
  });
}

function expectedPort(): number {
  return Number(/:(\d+)$/.exec(API_BASE_URL)?.[1] ?? 0);
}

function validEndpoint(url: string): boolean {
  return isP1Endpoint(url, expectedPort());
}

function requestJson(url: string, method: 'GET' | 'POST', data?: Record<string, unknown>): Promise<unknown | null> {
  return new Promise((resolve) => {
    let done = false;
    let task: WechatMiniprogram.RequestTask | undefined;
    const finish = (value: unknown | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (value === null) task?.abort();
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), TIMEOUT_MS + 100);
    try {
      task = wx.request({ url, method, ...(data === undefined ? {} : { data }), timeout: TIMEOUT_MS,
        ...(method === 'POST' ? { header: { 'Content-Type': 'application/json' } } : {}),
        success: ({ statusCode, data: body }) => finish(statusCode === 200 ? body : null),
        fail: () => finish(null),
      });
    } catch { finish(null); }
  });
}

export function getP1Pairing(): P1Pairing | null {
  if (pairingStorageFailed) return null;
  const raw = getString(PAIRING_KEY);
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || !isP1Identifier(value.deviceId, 'srv') ||
      !isP1Identifier(value.pairId, 'pair') || typeof value.key !== 'string' ||
      typeof value.endpointHint !== 'string' || !validEndpoint(value.endpointHint)) return null;
    const valid = parseP1Pairing(JSON.stringify({
      kind: 'local-service-pairing', version: 1, serviceName: 'my-backend', deviceId: value.deviceId,
      pairId: value.pairId, pairingKey: value.key,
      endpointHint: { ipv4: /^http:\/\/([^:]+)/.exec(value.endpointHint)?.[1], port: expectedPort() },
      expiresAt: 2147483647,
    }), 0);
    return valid;
  } catch { return null; }
}

export function forgetP1Pairing(): void {
  const pairing = getP1Pairing();
  pairingGeneration++;
  resolver.invalidate();
  remove(PAIRING_KEY);
  remove(VERIFIED_KEY);
  if (pairing) remove(verifiedKey(pairing));
  pairingStorageFailed = false;
}

export async function activateP1Pairing(raw: string): Promise<boolean> {
  const generation = ++pairingGeneration;
  const pairing = parseP1Pairing(raw, Date.now());
  if (!pairing || !validEndpoint(pairing.endpointHint)) return false;
  const nonce = await randomNonce();
  if (!nonce) return false;
  const response = await requestJson(`${pairing.endpointHint}/local/v1/pair/activate`, 'POST',
    activationRequest(pairing, nonce));
  if (generation !== pairingGeneration || !validActivation(response, pairing, nonce)) return false;
  const previous = getString(PAIRING_KEY);
  try {
    wx.setStorageSync(`pathai:${PAIRING_KEY}`, JSON.stringify(pairing));
  } catch {
    if (previous !== null) {
      try { wx.setStorageSync(`pathai:${PAIRING_KEY}`, previous); } catch { return false; }
    } else {
      try { wx.removeStorageSync(`pathai:${PAIRING_KEY}`); } catch { return false; }
    }
    pairingStorageFailed = true;
    return false;
  }
  pairingStorageFailed = false;
  resolver.invalidate();
  return true;
}

export async function verifyP1Candidate(candidate: P1Candidate, pairing: P1Pairing): Promise<string | null> {
  if (!validEndpoint(candidate.endpoint)) return null;
  const challenge = await randomNonce();
  if (!challenge) return null;
  const response = await requestJson(`${candidate.endpoint}/local/v1/probe?pairId=${pairing.pairId}&challenge=${challenge}`, 'GET');
  if (!validP1Probe(response, pairing, challenge, candidate.bootId)) return null;
  return (response as Record<string, unknown>).bootId as string;
}

export async function discoverP1Pairing(pairing: P1Pairing): Promise<P1Candidate[]> {
  if (typeof wx.createUDPSocket !== 'function') return [];
  const requestId = await randomNonce();
  if (!requestId) return [];
  return new Promise((resolve) => {
    let socket: WechatMiniprogram.UDPSocket;
    try { socket = wx.createUDPSocket(); } catch { resolve([]); return; }
    let done = false;
    let sent = false;
    const candidates: P1Candidate[] = [];
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { socket.close(); } catch { resolve(candidates); return; }
      resolve(candidates);
    };
    const timer = setTimeout(finish, DISCOVERY_MS);
    socket.onMessage(({ message, remoteInfo }) => {
      if (done || message.byteLength > 1200) return;
      try {
        const raw = Array.from(new Uint8Array(message), (byte) => String.fromCharCode(byte)).join('');
        const offer = parseP1Offer(raw, remoteInfo.address, pairing, requestId);
        if (offer && validEndpoint(offer.endpoint) &&
          !candidates.some((candidate) => candidate.endpoint === offer.endpoint)) {
          candidates.push(offer);
          if (candidates.length >= 16) finish();
        }
      } catch { return; }
    });
    socket.onError(finish);
    socket.onListening(() => {
      if (sent || done) return;
      sent = true;
      try {
        socket.send({ address: '255.255.255.255', port: 39001, setBroadcast: true,
          message: JSON.stringify({ protocol: 'local-service-discovery', version: 1, type: 'DISCOVER',
            serviceName: 'my-backend', requestId, deviceId: pairing.deviceId, pairId: pairing.pairId }) });
      } catch { finish(); }
    });
    try { socket.bind(); } catch { finish(); }
  });
}

export function invalidateP1Address(): void {
  resolver.invalidate();
}

export async function resolveP1Address(): Promise<P1Resolution> {
  const pairing = getP1Pairing();
  if (!pairing) return null;
  const remembered = getString(verifiedKey(pairing));
  const cached = remembered && validEndpoint(remembered) ? remembered : API_BASE_URL;
  if (!validEndpoint(cached)) return null;
  return resolver.resolve(pairing, { endpoint: cached }, () => discoverP1Pairing(pairing), verifyP1Candidate,
    (result) => setString(verifiedKey(pairing), result.endpoint));
}

export async function verifyP1ScannedAddress(raw: string): Promise<boolean> {
  const pairing = getP1Pairing();
  if (!pairing) return false;
  const address = parseP1Address(raw, pairing);
  if (!address) return false;
  const generation = pairingGeneration;
  const bootId = await verifyP1Candidate({ endpoint: address }, pairing);
  if (!bootId || generation !== pairingGeneration) return false;
  resolver.invalidate();
  try {
    wx.setStorageSync(`pathai:${verifiedKey(pairing)}`, address);
    return true;
  } catch { return false; }
}
