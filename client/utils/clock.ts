/**
 * 统一时钟入口：业务代码一律经 nowMs/nowSec 取时间，测试可注入假时间源。
 * 单位约定：_ms 毫秒；_sec 秒（后端契约时间为 epoch 秒 / ISO 字符串）。
 */
let source: () => number = () => Date.now();

export function nowMs(): number {
  return source();
}

export function nowSec(): number {
  return Math.floor(source() / 1000);
}

/** 注入自定义时间源（仅测试使用）。 */
export function setNowSource(next: () => number): void {
  source = next;
}

/** 恢复真实时间源。 */
export function resetNowSource(): void {
  source = () => Date.now();
}
