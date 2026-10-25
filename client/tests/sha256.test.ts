/**
 * SHA-256 正确性验证：NIST FIPS 180-2 已知向量 + 字节边界长度 + UTF-8 编码。
 * 参考摘要由 CPython hashlib 生成（2026-10）。
 */
import { sha256, sha256Hex, utf8Bytes } from '../utils/sha256';

describe('sha256 已知向量', () => {
  test('空字符串', () => {
    expect(sha256('')).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
  });

  test('"abc"', () => {
    expect(sha256('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  test('448 位消息（FIPS 示例 2）', () => {
    expect(sha256('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')).toBe(
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    );
  });
});

describe('填充边界长度（b"x" × n，CPython hashlib 参考值）', () => {
  test.each([
    [3, 'cd2eb0837c9b4c962c22d2ff8b5441b7b45805887f051d39bf133b583baf6860'],
    [55, 'd5e285683cd4efc02d021a5c62014694958901005d6f71e89e0989fac77e4072'],
    [56, '04c26261370ee7541549d16dee320c723e3fd14671e66a099afe0a377c16888e'],
    [63, '75220b47218278e656f2013bb8f0c455a25eaf01e86c64924e9d48d89776d6f2'],
    [64, '7ce100971f64e7001e8fe5a51973ecdfe1ced42befe7ee8d5fd6219506b5393c'],
    [65, '9537c5fdf120482f7d58d25e9ed583f52c02b4e304ea814db1633ad565aed7e9'],
    [119, '000b48d4edf0fa7bee3c6236ecd2785baa5db4eeb8bb54341b029e0d9fa5fb0c'],
  ])('长度 %i', (n, expected) => {
    expect(sha256Hex(new Uint8Array(n).fill(0x78))).toBe(expected);
  });

  test('一百万字节 a（NIST 多块向量）', () => {
    expect(sha256('a'.repeat(1000000))).toBe(
      'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0',
    );
  });

  test('一百万字节 x（CPython hashlib 参考值）', () => {
    expect(sha256('x'.repeat(1000000))).toBe(
      '1b977e9f84f1b26b6ed7f68b0498faee2385ea4125bd29adce4a7d9106ba3134',
    );
  });
});

describe('UTF-8 编码', () => {
  test('中文双字节', () => {
    expect([...utf8Bytes('你好')]).toEqual([0xe4, 0xbd, 0xa0, 0xe5, 0xa5, 0xbd]);
    expect(sha256('你好')).toBe(
      '670d9743542cae3ea7ebe36af56bd53648b0a1126162e78d81a32934a711302e',
    );
  });

  test('增补平面代理对', () => {
    // U+1D54A MATHEMATICAL DOUBLE-STRUCK S → f0 9d 95 8a
    expect([...utf8Bytes('\u{1D54A}')]).toEqual([0xf0, 0x9d, 0x95, 0x8a]);
  });
});
