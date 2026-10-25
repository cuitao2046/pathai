/**
 * clock：可注入时间源与恢复。
 */
import { nowMs, nowSec, resetNowSource, setNowSource } from '../utils/clock';

afterEach(() => {
  resetNowSource();
});

test('默认使用真实时间', () => {
  const before = Date.now();
  expect(nowMs()).toBeGreaterThanOrEqual(before);
});

test('nowSec 向下取整到秒', () => {
  setNowSource(() => 1750000123456);
  expect(nowSec()).toBe(1750000123);
});

test('注入固定时间源后可恢复', () => {
  setNowSource(() => 42);
  expect(nowMs()).toBe(42);
  resetNowSource();
  expect(nowMs()).toBeGreaterThan(42);
});
