/**
 * utils/base64.ts：UTF-8 ↔ base64 编解码（讯飞协议收发）。
 * 以 Node Buffer 为对照基准做往返校验。
 */
import {
  base64ToBytes,
  base64ToUtf8,
  bytesToBase64,
  bytesToUtf8,
  utf8ToBase64,
} from '../utils/base64';

/** jest 运行于 Node 环境，本工程 tsconfig 未含 node 类型，本地声明对照用的 Buffer 面。 */
declare const Buffer: {
  from(input: string | Uint8Array, encoding?: string): { toString(encoding: string): string };
};

const SAMPLES = [
  '',
  'a',
  'ab',
  'abc',
  '带我去图书馆',
  '二楼东翼生物工作室 A-201',
  'mixed 中文 and English 123!?，。',
  '🏫🎉 emoji 组合',
];

describe('utf8ToBase64 / base64ToUtf8', () => {
  it('与 Node Buffer 的 utf8→base64 结果一致', () => {
    for (const text of SAMPLES) {
      expect(utf8ToBase64(text)).toBe(Buffer.from(text, 'utf8').toString('base64'));
    }
  });

  it('往返保持原文（含 4 字节 emoji 代理对）', () => {
    for (const text of SAMPLES) {
      expect(base64ToUtf8(utf8ToBase64(text))).toBe(text);
    }
  });
});

describe('bytesToBase64 / base64ToBytes', () => {
  it('覆盖 3 字节分组的三种余数（0/1/2）', () => {
    const cases = [
      new Uint8Array([1, 2, 3, 4, 5, 6]),
      new Uint8Array([0x00, 0xff, 0x7f]),
      new Uint8Array([0xde, 0xad]),
      new Uint8Array([0xbe]),
      new Uint8Array(0),
    ];
    for (const bytes of cases) {
      const b64 = bytesToBase64(bytes);
      expect(b64).toBe(Buffer.from(bytes).toString('base64'));
      expect(Array.from(base64ToBytes(b64))).toEqual(Array.from(bytes));
    }
  });

  it('解码标准向量', () => {
    expect(Array.from(base64ToBytes('AQID'))).toEqual([1, 2, 3]);
    expect(Array.from(base64ToBytes('AQI='))).toEqual([1, 2]);
  });

  it('非法字符抛错', () => {
    expect(() => base64ToBytes('!!not-base64!!')).toThrow('非法 base64');
  });
});

describe('bytesToUtf8', () => {
  it('截断的多字节序列抛错', () => {
    const truncated = utf8ToBase64('图');
    const bytes = base64ToBytes(truncated).slice(0, 2); // 去掉最后一个续字节
    expect(() => bytesToUtf8(bytes)).toThrow();
  });

  it('非法首字节抛错', () => {
    expect(() => bytesToUtf8(new Uint8Array([0x80, 0x41]))).toThrow('非法 UTF-8');
  });
});
