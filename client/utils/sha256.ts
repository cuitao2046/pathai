/**
 * SHA-256（FIPS 180-4）零依赖实现。
 *
 * 用途：地图包摘要校验（dataSha256）、API-06 幂等键请求体指纹。
 * 小程序基础库无同步 WebCrypto，故自行实现；正确性由 NIST 已知向量测试保证
 * （tests/sha256.test.ts）。
 *
 * 性能约定：真实地图包为 4.6MB 量级，**必须走 sha256Async**（UTF-8 编码与
 * 分块压缩均按片让出主线程）。真机上同步计算 4.6MB 会阻塞逻辑层几十秒，
 * 表现为「正在加载地图」期间所有点击无响应。同步 sha256 仅用于小文本
 * （测试向量、小字符串指纹）。
 */

/** 轮常量 K（FIPS 180-4 §4.2.3：前 64 个素数立方根小数部分前 32 位）。 */
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotr(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

function isLowSurrogate(code: number): boolean {
  return code >= 0xdc00 && code <= 0xdfff;
}

// ---------------------------------------------------------------------------
// UTF-8 编码（小程序环境无 TextEncoder）
// ---------------------------------------------------------------------------

/** 计算 text 的 UTF-8 字节数（含增补平面代理对 4 字节）。 */
function utf8ByteLength(text: string): number {
  let len = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code < 0x80) {
      len += 1;
    } else if (code < 0x800) {
      len += 2;
    } else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const lo = text.charCodeAt(i + 1);
      if (lo >= 0xdc00 && lo <= 0xdfff) {
        len += 4;
        i += 1;
        continue;
      }
      len += 3; // 孤立高位代理，按 BMP 码点编码
    } else {
      len += 3;
    }
  }
  return len;
}

/** 将 text 的 UTF-8 字节写入 out[pos..]，返回结束位置。 */
function writeUtf8(text: string, out: Uint8Array, pos: number): number {
  let p = pos;
  for (let i = 0; i < text.length; i++) {
    let code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const lo = text.charCodeAt(i + 1);
      if (lo >= 0xdc00 && lo <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (lo - 0xdc00);
        i += 1;
      }
    }
    if (code < 0x80) {
      out[p++] = code;
    } else if (code < 0x800) {
      out[p++] = 0xc0 | (code >> 6);
      out[p++] = 0x80 | (code & 0x3f);
    } else if (code < 0x10000) {
      out[p++] = 0xe0 | (code >> 12);
      out[p++] = 0x80 | ((code >> 6) & 0x3f);
      out[p++] = 0x80 | (code & 0x3f);
    } else {
      out[p++] = 0xf0 | (code >> 18);
      out[p++] = 0x80 | ((code >> 12) & 0x3f);
      out[p++] = 0x80 | ((code >> 6) & 0x3f);
      out[p++] = 0x80 | (code & 0x3f);
    }
  }
  return p;
}

/** UTF-8 编码（预分配一次写入，无中间数组拷贝）。 */
export function utf8Bytes(text: string): Uint8Array {
  const out = new Uint8Array(utf8ByteLength(text));
  writeUtf8(text, out, 0);
  return out;
}

// ---------------------------------------------------------------------------
// 压缩核心
// ---------------------------------------------------------------------------

/** 单块压缩：64 字压缩轮 + 64 轮主循环，原地更新 h。同步/异步共用。 */
function compressBlock(h: Uint32Array, view: DataView, offset: number, w: Uint32Array): void {
  for (let i = 0; i < 16; i++) {
    w[i] = view.getUint32(offset + i * 4);
  }
  for (let i = 16; i < 64; i++) {
    const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
    const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
    w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
  }

  let a = h[0];
  let b = h[1];
  let c = h[2];
  let d = h[3];
  let e = h[4];
  let f = h[5];
  let g = h[6];
  let hh = h[7];

  for (let i = 0; i < 64; i++) {
    const bigS1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
    const ch = (e & f) ^ (~e & g);
    const t1 = (hh + bigS1 + ch + K[i] + w[i]) >>> 0;
    const bigS0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
    const maj = (a & b) ^ (a & c) ^ (b & c);
    const t2 = (bigS0 + maj) >>> 0;

    hh = g;
    g = f;
    f = e;
    e = (d + t1) >>> 0;
    d = c;
    c = b;
    b = a;
    a = (t1 + t2) >>> 0;
  }

  h[0] = (h[0] + a) >>> 0;
  h[1] = (h[1] + b) >>> 0;
  h[2] = (h[2] + c) >>> 0;
  h[3] = (h[3] + d) >>> 0;
  h[4] = (h[4] + e) >>> 0;
  h[5] = (h[5] + f) >>> 0;
  h[6] = (h[6] + g) >>> 0;
  h[7] = (h[7] + hh) >>> 0;
}

function initHash(): Uint32Array {
  return new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
}

/** 预填充（原始数据 + 0x80 + 零 + 64 位大端长度），返回缓冲与视图。 */
function padInput(input: Uint8Array): { buf: Uint8Array; view: DataView } {
  const bitLength = input.length * 8;
  const bitLenHi = Math.floor(bitLength / 0x100000000);
  const bitLenLo = bitLength >>> 0;
  const paddedLen = ((input.length + 9 + 63) & ~63) >>> 0;
  const buf = new Uint8Array(paddedLen);
  buf.set(input);
  buf[input.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(paddedLen - 8, bitLenHi);
  view.setUint32(paddedLen - 4, bitLenLo);
  return { buf, view };
}

function digestToBytes(h: Uint32Array): Uint8Array {
  const out = new Uint8Array(32);
  const outView = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) {
    outView.setUint32(i * 4, h[i]);
  }
  return out;
}

/** 计算 SHA-256 摘要（32 字节）；仅用于小文本，大输入用 sha256BytesAsync。 */
export function sha256Bytes(input: Uint8Array): Uint8Array {
  const h = initHash();
  const { buf, view } = padInput(input);
  const w = new Uint32Array(64);
  for (let offset = 0; offset < buf.length; offset += 64) {
    compressBlock(h, view, offset, w);
  }
  return digestToBytes(h);
}

// ---------------------------------------------------------------------------
// 异步分片版（大文本；每片让出主线程，保持 UI 可交互）
// ---------------------------------------------------------------------------

/** 让出主线程一个宏任务：macrotask 间隙可处理渲染层 tap 等原生事件。 */
function yieldToUi(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, 0);
  });
}

/** 每片压缩块数（1024 块 = 64KB），单片在真机上约数毫秒。 */
const BLOCKS_PER_SLICE = 1024;
/** 每片 UTF-8 编码字符数（512K UTF-16 码元 ≈ 0.5~1.5MB 字节）。 */
const UTF8_CHARS_PER_SLICE = 524288;

/**
 * 异步 UTF-8 编码：预分配后按片写入，片间让出主线程。
 * 切片右边界归一避免把增补平面代理对切开（切成两半会被分别错编为 3 字节）：
 * end 落在低位代理上时右移一位，保证代理对完整归入当前片。
 */
export async function utf8BytesAsync(text: string): Promise<Uint8Array> {
  const out = new Uint8Array(utf8ByteLength(text));
  let pos = 0;
  let start = 0;
  while (start < text.length) {
    let end = Math.min(start + UTF8_CHARS_PER_SLICE, text.length);
    if (end < text.length && isLowSurrogate(text.charCodeAt(end))) {
      end += 1;
    }
    pos = writeUtf8(text.slice(start, end), out, pos);
    start = end;
    if (start < text.length) {
      await yieldToUi();
    }
  }
  return out;
}

/** 异步分块压缩：状态跨片保留，按片让出主线程。 */
export async function sha256BytesAsync(input: Uint8Array): Promise<Uint8Array> {
  const h = initHash();
  const { buf, view } = padInput(input);
  const w = new Uint32Array(64);
  let sinceYield = 0;
  for (let offset = 0; offset < buf.length; offset += 64) {
    compressBlock(h, view, offset, w);
    sinceYield += 1;
    if (sinceYield >= BLOCKS_PER_SLICE) {
      sinceYield = 0;
      await yieldToUi();
    }
  }
  return digestToBytes(h);
}

/** 大文本 → 64 位小写 hex 摘要（分片让出主线程，供地图包等 4.6MB 级输入）。 */
export async function sha256Async(text: string): Promise<string> {
  const bytes = await utf8BytesAsync(text);
  return toHex(await sha256BytesAsync(bytes)); // toHex：摘要→hex，勿用 sha256Hex（会二次哈希）
}

// ---------------------------------------------------------------------------
// 小文本便捷入口
// ---------------------------------------------------------------------------

function toHex(bytes: Uint8Array): string {
  let hex = '';
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, '0');
  }
  return hex;
}

/** 任意字节输入 → 64 位小写 hex 摘要。 */
export function sha256Hex(input: Uint8Array): string {
  return toHex(sha256Bytes(input));
}

/** 字符串输入（UTF-8 编码）→ 64 位小写 hex 摘要；仅用于小文本。 */
export function sha256(text: string): string {
  return sha256Hex(utf8Bytes(text));
}
