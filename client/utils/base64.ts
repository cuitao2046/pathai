/**
 * base64 与 UTF-8 编解码（讯飞 WebSocket 协议收发用）。
 *
 * - 纯函数实现，不依赖 wx.* 内置接口（引擎单测可直接注入运行）；
 * - UTF-8 编码复用 utils/sha256.ts 的 utf8Bytes，解码在此自实现；
 * - 用途：ASR 识别结果文本（base64→UTF-8 JSON）、TTS 音频分片（bytes↔base64）。
 */
import { utf8Bytes } from './sha256';

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/** 字节序列 → base64（标准字母表，含 '=' 填充）。 */
export function bytesToBase64(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += ALPHABET[(n >> 18) & 63] + ALPHABET[(n >> 12) & 63] + ALPHABET[(n >> 6) & 63] + ALPHABET[n & 63];
  }
  const remain = bytes.length - i;
  if (remain === 1) {
    const n = bytes[i] << 16;
    out += ALPHABET[(n >> 18) & 63] + ALPHABET[(n >> 12) & 63] + '==';
  } else if (remain === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    out += ALPHABET[(n >> 18) & 63] + ALPHABET[(n >> 12) & 63] + ALPHABET[(n >> 6) & 63] + '=';
  }
  return out;
}

const REVERSE = (() => {
  const table = new Int16Array(128).fill(-1);
  for (let i = 0; i < ALPHABET.length; i += 1) {
    table[ALPHABET.charCodeAt(i)] = i;
  }
  return table;
})();

/** base64 → 字节序列（容忍空白；非法字符抛错）。 */
export function base64ToBytes(b64: string): Uint8Array {
  const clean = b64.replace(/[^\w+/=]/g, '');
  const pad = clean.endsWith('==') ? 2 : clean.endsWith('=') ? 1 : 0;
  const out = new Uint8Array((clean.length / 4) * 3 - pad);
  let outIdx = 0;
  for (let i = 0; i < clean.length; i += 4) {
    const c0 = REVERSE[clean.charCodeAt(i)] ?? -1;
    const c1 = REVERSE[clean.charCodeAt(i + 1)] ?? -1;
    const c2 = clean[i + 2] === '=' ? 0 : REVERSE[clean.charCodeAt(i + 2)] ?? -1;
    const c3 = clean[i + 3] === '=' ? 0 : REVERSE[clean.charCodeAt(i + 3)] ?? -1;
    if (c0 < 0 || c1 < 0 || c2 < 0 || c3 < 0) {
      throw new Error('非法 base64 字符');
    }
    const n = (c0 << 18) | (c1 << 12) | (c2 << 6) | c3;
    if (outIdx < out.length) out[outIdx++] = (n >> 16) & 0xff;
    if (outIdx < out.length) out[outIdx++] = (n >> 8) & 0xff;
    if (outIdx < out.length) out[outIdx++] = n & 0xff;
  }
  return out;
}

/** 字节序列 → UTF-8 文本（严格校验，遇非法序列抛错）。 */
export function bytesToUtf8(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  while (i < bytes.length) {
    const b0 = bytes[i];
    let codePoint: number;
    let extra: number;
    if (b0 < 0x80) {
      codePoint = b0;
      extra = 0;
    } else if ((b0 & 0xe0) === 0xc0) {
      codePoint = b0 & 0x1f;
      extra = 1;
    } else if ((b0 & 0xf0) === 0xe0) {
      codePoint = b0 & 0x0f;
      extra = 2;
    } else if ((b0 & 0xf8) === 0xf0) {
      codePoint = b0 & 0x07;
      extra = 3;
    } else {
      throw new Error('非法 UTF-8 序列');
    }
    if (extra > 0 && i + extra >= bytes.length) {
      throw new Error('UTF-8 序列被截断');
    }
    for (let k = 1; k <= extra; k += 1) {
      const bk = bytes[i + k];
      if (bk === undefined || (bk & 0xc0) !== 0x80) {
        throw new Error('非法 UTF-8 续字节');
      }
      codePoint = (codePoint << 6) | (bk & 0x3f);
    }
    if (codePoint > 0xffff) {
      // 代理对：4 字节码点 → 两个 UTF-16 单元
      const cp = codePoint - 0x10000;
      out += String.fromCharCode(0xd800 + (cp >> 10), 0xdc00 + (cp & 0x3ff));
    } else {
      out += String.fromCharCode(codePoint);
    }
    i += extra + 1;
  }
  return out;
}

/** UTF-8 文本 → base64（TTS 请求文本、构造测试向量用）。 */
export function utf8ToBase64(text: string): string {
  return bytesToBase64(utf8Bytes(text));
}

/** base64 → UTF-8 文本（ASR 识别结果 payload.result.text 解码）。 */
export function base64ToUtf8(b64: string): string {
  return bytesToUtf8(base64ToBytes(b64));
}
