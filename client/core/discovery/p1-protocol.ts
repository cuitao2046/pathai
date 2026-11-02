import {
  decodeP1Key, isP1Endpoint, isP1Identifier, isP1Nonce, isRecord,
  parseP1Json, p1Tag, verifyP1Tag,
} from './p1-auth';

export interface P1Pairing {
  deviceId: string;
  pairId: string;
  key: string;
  endpointHint: string;
}

export interface P1Offer {
  endpoint: string;
  bootId: string;
  port: number;
}

export function parseP1Pairing(raw: string, nowMs: number): P1Pairing | null {
  const value = parseP1Json(raw, 1024);
  if (!value || Object.keys(value).sort().join(',') !==
    'deviceId,endpointHint,expiresAt,kind,pairId,pairingKey,serviceName,version' ||
    value.kind !== 'local-service-pairing' || value.version !== 1 || value.serviceName !== 'my-backend' ||
    !isP1Identifier(value.deviceId, 'srv') || !isP1Identifier(value.pairId, 'pair') ||
    decodeP1Key(value.pairingKey) === null || !isRecord(value.endpointHint) ||
    typeof value.endpointHint.ipv4 !== 'string' || !Number.isInteger(value.endpointHint.port) ||
    typeof value.expiresAt !== 'number' || !Number.isSafeInteger(value.expiresAt) ||
    nowMs >= value.expiresAt * 1000) return null;
  const endpoint = `http://${value.endpointHint.ipv4}:${value.endpointHint.port}`;
  if (!isP1Endpoint(endpoint, value.endpointHint.port as number)) return null;
  return { deviceId: value.deviceId, pairId: value.pairId, key: value.pairingKey as string, endpointHint: endpoint };
}

export function parseP1Address(raw: string, pairing: P1Pairing): string | null {
  const value = parseP1Json(raw, 512);
  if (!value || Object.keys(value).sort().join(',') !==
    'deviceId,endpointHint,kind,serviceName,version' || value.kind !== 'local-service-address' ||
    value.version !== 1 || value.serviceName !== 'my-backend' || value.deviceId !== pairing.deviceId ||
    !isRecord(value.endpointHint) || Object.keys(value.endpointHint).sort().join(',') !== 'ipv4,port' ||
    typeof value.endpointHint.ipv4 !== 'string' || !Number.isInteger(value.endpointHint.port)) return null;
  const endpoint = `http://${value.endpointHint.ipv4}:${value.endpointHint.port}`;
  return isP1Endpoint(endpoint, value.endpointHint.port as number) ? endpoint : null;
}

export function activationRequest(pairing: P1Pairing, nonce: string): Record<string, unknown> {
  const key = decodeP1Key(pairing.key);
  if (!key || !isP1Nonce(nonce)) throw new Error('invalid pairing');
  const payload = {
    protocol: 'local-service-discovery', version: 1, type: 'ACTIVATE', serviceName: 'my-backend',
    deviceId: pairing.deviceId, pairId: pairing.pairId, nonce,
  };
  return { ...payload, auth: { alg: 'HS256', tag: p1Tag('ACTIVATE', payload, key) } };
}

export function validActivation(response: unknown, pairing: P1Pairing, nonce: string): boolean {
  const key = decodeP1Key(pairing.key);
  return key !== null && isRecord(response) &&
    Object.keys(response).sort().join(',') === 'auth,bootId,deviceId,nonce,pairId,protocol,serviceName,type,version' &&
    response.protocol === 'local-service-discovery' && response.version === 1 && response.type === 'ACTIVATED' &&
    response.serviceName === 'my-backend' && response.deviceId === pairing.deviceId &&
    response.pairId === pairing.pairId && response.nonce === nonce &&
    isP1Identifier(response.bootId, 'boot') && verifyP1Tag('ACTIVATE', response, key);
}

export function parseP1Offer(raw: string, source: string, pairing: P1Pairing, requestId: string): P1Offer | null {
  const value = parseP1Json(raw);
  const key = decodeP1Key(pairing.key);
  if (!value || !key || Object.keys(value).sort().join(',') !==
    'apiVersion,auth,bootId,deviceId,pairId,port,protocol,requestId,serviceName,transport,ttlSec,type,version' ||
    value.protocol !== 'local-service-discovery' || value.version !== 1 || value.type !== 'OFFER' ||
    value.serviceName !== 'my-backend' || value.deviceId !== pairing.deviceId ||
    value.pairId !== pairing.pairId || value.requestId !== requestId ||
    !isP1Identifier(value.bootId, 'boot') || value.transport !== 'http' ||
    value.apiVersion !== 1 || value.ttlSec !== 30 || !Number.isInteger(value.port) ||
    !verifyP1Tag('OFFER', value, key)) return null;
  const endpoint = `http://${source}:${value.port}`;
  if (!isP1Endpoint(endpoint, value.port as number)) return null;
  return { endpoint, bootId: value.bootId, port: value.port as number };
}

export function validP1Probe(response: unknown, pairing: P1Pairing, challenge: string, expectedBoot?: string): boolean {
  const key = decodeP1Key(pairing.key);
  return key !== null && isRecord(response) &&
    Object.keys(response).sort().join(',') ===
      'apiVersion,auth,bootId,challenge,deviceId,pairId,protocol,ready,serverTimeMs,serviceName,type,version' &&
    response.protocol === 'local-service-discovery' && response.version === 1 && response.type === 'PROBE' &&
    response.serviceName === 'my-backend' && response.deviceId === pairing.deviceId &&
    response.pairId === pairing.pairId && response.challenge === challenge && response.ready === true &&
    response.apiVersion === 1 && isP1Identifier(response.bootId, 'boot') &&
    (expectedBoot === undefined || response.bootId === expectedBoot) &&
    typeof response.serverTimeMs === 'number' && Number.isSafeInteger(response.serverTimeMs) &&
    verifyP1Tag('PROBE', response, key);
}
