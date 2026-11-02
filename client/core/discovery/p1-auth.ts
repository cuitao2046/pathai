import { canonicalize, hmac, sha256, utf8ToBytes } from './p1-crypto-bundle';
import { isP0CandidateUrl } from './p0-candidate';

export type P1Domain = 'OFFER' | 'PROBE' | 'ACTIVATE';

const IDENTIFIER = /^(srv|pair|boot)-[0-9a-f]{16,32}$/;
const NONCE = /^[0-9a-f]{32}$/;
const KEY = /^[A-Za-z0-9_-]{43}$/;

export function isP1Identifier(value: unknown, kind: 'srv' | 'pair' | 'boot'): value is string {
  return typeof value === 'string' && IDENTIFIER.test(value) && value.startsWith(`${kind}-`);
}

export function isP1Nonce(value: unknown): value is string {
  return typeof value === 'string' && NONCE.test(value);
}

export function decodeP1Key(value: unknown): Uint8Array | null {
  if (typeof value !== 'string' || !KEY.test(value)) return null;
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const bytes = new Uint8Array(32);
  for (let index = 0; index < 32; index++) {
    const start = Math.floor(index * 8 / 6);
    const offset = index * 8 % 6;
    const upper = alphabet.indexOf(value[start]);
    const lower = alphabet.indexOf(value[start + 1]);
    if (upper < 0 || lower < 0) return null;
    bytes[index] = offset === 0 ? ((upper << 2) | (lower >> 4)) & 255 :
      offset === 2 ? ((upper << 4) | (lower >> 2)) & 255 : ((upper << 6) | lower) & 255;
  }
  return encodeP1Bytes(bytes) === value ? bytes : null;
}

export function encodeP1Bytes(bytes: Uint8Array): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  let result = '';
  for (let index = 0; index < bytes.length; index += 3) {
    const block = (bytes[index] << 16) | ((bytes[index + 1] ?? 0) << 8) | (bytes[index + 2] ?? 0);
    result += alphabet[(block >> 18) & 63] + alphabet[(block >> 12) & 63];
    if (index + 1 < bytes.length) result += alphabet[(block >> 6) & 63];
    if (index + 2 < bytes.length) result += alphabet[block & 63];
  }
  return result;
}

export function p1Tag(domain: P1Domain, payload: Record<string, unknown>, key: Uint8Array): string {
  if (key.length !== 32) throw new Error('invalid key length');
  const canonical = canonicalize(payload);
  if (canonical === undefined) throw new Error('invalid JSON');
  return encodeP1Bytes(hmac(sha256, key, utf8ToBytes(`LSD-${domain}-V1\n${canonical}`)));
}

export function verifyP1Tag(domain: P1Domain, message: Record<string, unknown>, key: Uint8Array): boolean {
  const { auth, ...payload } = message;
  if (!isRecord(auth) || Object.keys(auth).length !== 2 || auth.alg !== 'HS256' ||
    typeof auth.tag !== 'string' || !KEY.test(auth.tag)) return false;
  try {
    const expected = p1Tag(domain, payload, key);
    let difference = 0;
    for (let index = 0; index < expected.length; index++) difference |= expected.charCodeAt(index) ^ auth.tag.charCodeAt(index);
    return difference === 0;
  } catch {
    return false;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseP1Json(raw: string, maxBytes = 1200): Record<string, unknown> | null {
  if (utf8ToBytes(raw).length > maxBytes) return null;
  try {
    const stack: Array<{ kind: 'object'; keys: Set<string> } | { kind: 'array' }> = [];
    let position = 0;
    while (position < raw.length) {
      const character = raw[position];
      if (character === '"') {
        const start = position++;
        let closed = false;
        while (position < raw.length) {
          if (raw[position] === '\\') { position += 2; continue; }
          if (raw[position++] === '"') { closed = true; break; }
        }
        if (!closed) return null;
        const text = raw.slice(start, position);
        let next = position;
        while (/\s/.test(raw[next] ?? '')) next++;
        if (raw[next] === ':') {
          const current = stack[stack.length - 1];
          if (!current || current.kind !== 'object') return null;
          const key: string = JSON.parse(text);
          if (current.keys.has(key)) return null;
          current.keys.add(key);
        }
        continue;
      }
      if (character === '{') stack.push({ kind: 'object', keys: new Set() });
      if (character === '[') stack.push({ kind: 'array' });
      if (character === '}' && stack.pop()?.kind !== 'object') return null;
      if (character === ']' && stack.pop()?.kind !== 'array') return null;
      position++;
    }
    if (stack.length !== 0) return null;
    const value: unknown = JSON.parse(raw);
    return isRecord(value) ? value : null;
  } catch {
    return null;
  }
}

export function isP1Endpoint(url: string, port: number): boolean {
  return Number.isInteger(port) && port >= 1 && port <= 65535 && isP0CandidateUrl(url) && url.endsWith(`:${port}`);
}
