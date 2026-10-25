/**
 * 客户端本地 ID 生成：uuid v4。
 *
 * 用途：Idempotency-Key、本地事件关联。仅要求唯一性，不要求不可预测性
 * （小程序基础库无同步加密随机源），故基于 Math.random 实现。
 */
export function uuidV4(): string {
  let out = '';
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) {
      out += '-';
    } else if (i === 14) {
      out += '4';
    } else {
      const r = Math.floor(Math.random() * 16);
      const v = i === 19 ? (r & 0x3) | 0x8 : r;
      out += v.toString(16);
    }
  }
  return out;
}
