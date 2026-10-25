/**
 * 事件发射器：订阅/退订/异常隔离/清空；uuid 格式与唯一性。
 */
import { EventEmitter } from '../utils/event-emitter';
import { uuidV4 } from '../utils/ids';

describe('EventEmitter', () => {
  test('on/emit/off 与退订函数', () => {
    const bus = new EventEmitter<{ tick: { n: number } }>();
    const seen: number[] = [];
    const off = bus.on('tick', (p) => seen.push(p.n));
    bus.emit('tick', { n: 1 });
    off();
    bus.emit('tick', { n: 2 });
    expect(seen).toEqual([1]);
  });

  test('监听器异常不阻断其他监听器', () => {
    const bus = new EventEmitter<{ hit: string }>();
    const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    const calls: string[] = [];
    bus.on('hit', () => {
      throw new Error('boom');
    });
    bus.on('hit', (p) => calls.push(p));
    bus.emit('hit', 'x');
    expect(calls).toEqual(['x']);
    errSpy.mockRestore();
  });

  test('clear 移除全部监听', () => {
    const bus = new EventEmitter<{ a: number; b: number }>();
    const calls: number[] = [];
    bus.on('a', (p) => calls.push(p));
    bus.on('b', (p) => calls.push(p));
    bus.clear();
    bus.emit('a', 1);
    bus.emit('b', 2);
    expect(calls).toEqual([]);
  });
});

describe('uuidV4', () => {
  test('v4 格式（版本位 4、变体位 8/9/a/b）', () => {
    const id = uuidV4();
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  test('批量生成唯一', () => {
    const ids = new Set(Array.from({ length: 1000 }, () => uuidV4()));
    expect(ids.size).toBe(1000);
  });
});
