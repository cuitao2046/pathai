/**
 * SpeechQueue 优先级/打断/过期/降级行为测试（C4，docs/24 FE-04）。
 * ControlledTts：speak 挂起直至测试手动 complete/fail；
 * stop() 按引擎契约使当前 speak 以 resolve 结束。
 */
import { SpeechQueue } from '../core/speech/speech-queue';
import type { TtsEngine } from '../core/speech/tts-engine';
import { resetNowSource, setNowSource } from '../utils/clock';
import { wxStub } from './setup-wx';

interface PendingSpeak {
  text: string;
  resolve: () => void;
  reject: (reason?: unknown) => void;
}

/** 手动受控引擎：speak 进入 pending 队列，由测试逐个 settle。 */
class ControlledTts implements TtsEngine {
  pending: PendingSpeak[] = [];
  completed: string[] = [];
  stopCount = 0;

  speak(text: string): Promise<void> {
    return new Promise((resolve, reject) => {
      this.pending.push({ text, resolve, reject });
    });
  }

  /** 当前播报正常播完。 */
  completeCurrent(): void {
    const item = this.pending.shift();
    if (item) {
      this.completed.push(item.text);
      item.resolve();
    }
  }

  /** 当前播报失败（模拟引擎异常）。 */
  failCurrent(): void {
    this.pending.shift()?.reject(new Error('tts failure'));
  }

  stop(): void {
    this.stopCount += 1;
    this.completeCurrent();
  }
}

/** 刷新微任务队列（pump 链在 promise then 中推进）。 */
const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

describe('SpeechQueue 播报队列', () => {
  let engine: ControlledTts;

  beforeEach(() => {
    engine = new ControlledTts();
    wxStub.__vibrationLog.length = 0;
    setNowSource(() => 1_000_000);
  });

  afterEach(() => {
    resetNowSource();
  });

  function makeQueue(options: {
    onUnavailable?: (t: string) => void;
    onTextFallback?: (t: string) => void;
  } = {}): SpeechQueue {
    return new SpeechQueue({
      engine,
      onUnavailable: options.onUnavailable ?? jest.fn(),
      onTextFallback: options.onTextFallback,
    });
  }

  it('normal 优先级按 FIFO 顺序播报', async () => {
    const queue = makeQueue();
    queue.enqueue('提示一');
    queue.enqueue('提示二');
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['提示一']);
    engine.completeCurrent();
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['提示二']);
    engine.completeCurrent();
    await tick();
    expect(engine.pending).toHaveLength(0);
    expect(engine.completed).toEqual(['提示一', '提示二']);
  });

  it('turn 打断正在播的 normal 并丢弃待播 normal', async () => {
    const queue = makeQueue();
    queue.enqueue('提示一');
    queue.enqueue('提示二');
    await tick();
    queue.enqueue('前方左转', 'turn');
    await tick();
    expect(engine.stopCount).toBe(1);
    expect(engine.completed).toEqual(['提示一']);
    expect(engine.pending.map((p) => p.text)).toEqual(['前方左转']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['提示一', '前方左转']);
  });

  it('turn 不打断正在播的 turn', async () => {
    const queue = makeQueue();
    queue.enqueue('转向甲', 'turn');
    await tick();
    queue.enqueue('转向乙', 'turn');
    await tick();
    expect(engine.stopCount).toBe(0);
    expect(engine.pending.map((p) => p.text)).toEqual(['转向甲']);
    engine.completeCurrent();
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['转向乙']);
  });

  it('safety 清空待播低优先级并打断正在播的 normal', async () => {
    const queue = makeQueue();
    queue.enqueue('提示一');
    queue.enqueue('提示二');
    await tick();
    queue.enqueue('注意台阶', 'safety');
    await tick();
    expect(engine.stopCount).toBe(1);
    expect(engine.pending.map((p) => p.text)).toEqual(['注意台阶']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['提示一', '注意台阶']);
  });

  it('safety 打断正在播的 turn', async () => {
    const queue = makeQueue();
    queue.enqueue('前方右转', 'turn');
    await tick();
    queue.enqueue('危险请停下', 'safety');
    await tick();
    expect(engine.completed).toEqual(['前方右转']);
    expect(engine.pending.map((p) => p.text)).toEqual(['危险请停下']);
  });

  it('正在播 safety 时新 safety 排队等待，不打断', async () => {
    const queue = makeQueue();
    queue.enqueue('警告一', 'safety');
    await tick();
    queue.enqueue('警告二', 'safety');
    await tick();
    expect(engine.stopCount).toBe(0);
    expect(engine.pending.map((p) => p.text)).toEqual(['警告一']);
    engine.completeCurrent();
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['警告二']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['警告一', '警告二']);
  });

  it('队列按 safety > turn > normal 优先级出队，safety 到来清空低优先级', async () => {
    const queue = makeQueue();
    queue.enqueue('转向甲', 'turn');
    await tick();
    queue.enqueue('提示一');
    queue.enqueue('转向乙', 'turn'); // 不打断 turn
    await tick();
    queue.enqueue('警告甲', 'safety'); // 打断转向甲，清空待播 提示一/转向乙
    await tick();
    queue.enqueue('转向丙', 'turn'); // safety 播报中，排队
    queue.enqueue('警告乙', 'safety'); // 排队，且清空待播 转向丙
    engine.completeCurrent(); // 警告甲完成
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['警告乙']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['转向甲', '警告甲', '警告乙']);
    expect(engine.pending).toHaveLength(0);
  });

  it('过期项出队时被丢弃，不播报', async () => {
    let now = 1_000_000;
    setNowSource(() => now);
    const queue = makeQueue();
    queue.enqueue('即将过期的提示', 'normal', now + 1_000);
    queue.enqueue('已过期的提示', 'normal', now - 1);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['即将过期的提示']);
    expect(engine.pending).toHaveLength(0);

    queue.enqueue('入队即过期的提示', 'normal', now - 1);
    await tick();
    expect(engine.pending).toHaveLength(0);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['即将过期的提示']);
  });

  it('clearNormal 只清除待播 normal，保留 safety', async () => {
    const queue = makeQueue();
    queue.enqueue('转向甲', 'turn');
    await tick();
    queue.enqueue('提示一');
    queue.enqueue('警告甲', 'safety'); // 打断转向甲并清掉提示一，警告甲入队
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['警告甲']); // 警告甲已在播
    queue.enqueue('提示二'); // 排队
    queue.clearNormal(); // 清掉提示二
    engine.completeCurrent(); // 警告甲完成
    await tick();
    expect(engine.completed).toEqual(['转向甲', '警告甲']);
    expect(engine.pending).toHaveLength(0); // 提示二被 clearNormal 移除，不再播报
  });

  it('clearAll 清空全部待播项且不打断当前', async () => {
    const queue = makeQueue();
    queue.enqueue('提示一');
    await tick();
    queue.enqueue('提示二');
    queue.enqueue('转向甲', 'turn'); // 打断提示一并接管队列
    queue.clearAll(); // 清掉待播的转向甲
    await tick();
    expect(engine.completed).toEqual(['提示一']);
    expect(engine.pending).toHaveLength(0); // 转向甲被 clearAll 清除，不再播报
  });

  it('引擎失败：onUnavailable 恰一次、长震动，后续播报走文本降级', async () => {
    const onUnavailable = jest.fn();
    const onTextFallback = jest.fn();
    const queue = makeQueue({ onUnavailable, onTextFallback });
    queue.enqueue('提示一');
    await tick();
    engine.failCurrent();
    await tick();
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(onUnavailable).toHaveBeenCalledWith('提示一');
    expect(wxStub.__vibrationLog).toEqual(['long']);

    queue.enqueue('提示二'); // 引擎已失效：不再尝试语音
    await tick();
    expect(engine.pending).toHaveLength(0);
    expect(onTextFallback).toHaveBeenCalledWith('提示二');
    expect(wxStub.__vibrationLog).toEqual(['long', 'short']);
  });

  it('引擎失败时待播文本逐条走文本降级', async () => {
    const onTextFallback = jest.fn();
    const queue = makeQueue({ onTextFallback });
    queue.enqueue('提示一');
    await tick();
    queue.enqueue('提示二');
    engine.failCurrent();
    await tick();
    expect(onTextFallback).toHaveBeenCalledTimes(1);
    expect(onTextFallback).toHaveBeenCalledWith('提示二');
  });

  it('未提供 onTextFallback 时降级路径不抛错', async () => {
    const onUnavailable = jest.fn();
    const queue = makeQueue({ onUnavailable });
    queue.enqueue('提示一');
    await tick();
    engine.failCurrent();
    await tick();
    queue.enqueue('提示二'); // 不应抛错
    expect(onUnavailable).toHaveBeenCalledTimes(1);
    expect(engine.pending).toHaveLength(0);
  });
});
