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

/** 指定文本永不 settle 的引擎（模拟播放器无 onEnded/onError，引擎也无响应）。 */
class StallTts implements TtsEngine {
  spoken: string[] = [];
  stopCount = 0;
  hangText: string | null = null;

  speak(text: string): Promise<void> {
    this.spoken.push(text);
    if (text === this.hangText) {
      return new Promise<void>(() => undefined); // 既不 resolve 也不 reject
    }
    return Promise.resolve();
  }

  stop(): void {
    this.stopCount += 1;
    // 坏引擎：stop 也唤不醒挂起的 speak
  }
}

describe('SpeechQueue 播报队列', () => {
  let engine: ControlledTts;

  beforeEach(() => {
    engine = new ControlledTts();
    wxStub.__vibrationLog.length = 0;
    setNowSource(() => 1_000_000);
  });

  afterEach(async () => {
    // 排空队列：让挂起的播报 settle，看门狗定时器随之清除，
    // 否则测试结束后定时器仍会触发（Cannot log after tests are done）。
    for (let i = 0; i < 10; i += 1) {
      const pending = engine.pending.splice(0);
      if (pending.length === 0) {
        break;
      }
      for (const item of pending) {
        item.resolve();
      }
      await tick();
    }
    resetNowSource();
  });

  function makeQueue(options: {
    onUnavailable?: (t: string) => void;
    onTextFallback?: (t: string) => void;
    playClip?: (clip: string) => Promise<void>;
    stopClip?: () => void;
  } = {}): SpeechQueue {
    return new SpeechQueue({
      engine,
      onUnavailable: options.onUnavailable ?? jest.fn(),
      onTextFallback: options.onTextFallback,
      playClip: options.playClip,
      stopClip: options.stopClip,
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

  it('本地音频片段与文本共用 FIFO：片段播完才播后续文本', async () => {
    const played: string[] = [];
    let finishClip: (() => void) | null = null;
    const queue = makeQueue({
      playClip: (clip) => {
        played.push(clip);
        return new Promise<void>((resolve) => {
          finishClip = resolve;
        });
      },
    });

    queue.enqueueClip('nav-start');
    queue.enqueue('沿走廊直行 5 米。');
    await tick();
    expect(played).toEqual(['nav-start']);
    expect(engine.pending).toHaveLength(0); // 文本严格等片段播完

    finishClip!();
    await tick();
    expect(engine.pending.map((p) => p.text)).toEqual(['沿走廊直行 5 米。']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['沿走廊直行 5 米。']);
  });

  it('safety 抢占正在播的本地片段：调用 stopClip 并继续播安全提示', async () => {
    let finishClip: (() => void) | null = null;
    const stopClip = jest.fn(() => finishClip?.());
    const queue = makeQueue({
      playClip: () => new Promise<void>((resolve) => {
        finishClip = resolve;
      }),
      stopClip,
    });

    queue.enqueueClip('nav-start');
    await tick();
    queue.enqueue('前方有障碍，请停下。', 'safety');
    await tick();

    expect(stopClip).toHaveBeenCalledTimes(1);
    expect(engine.pending.map((p) => p.text)).toEqual(['前方有障碍，请停下。']);
  });

  it('本地片段播放器缺失时静默跳过，绝不回退成 TTS 读文件名', async () => {
    const queue = makeQueue();
    queue.enqueueClip('nav-start');
    queue.enqueue('首条指令');
    await tick();

    expect(engine.pending.map((p) => p.text)).toEqual(['首条指令']);
    engine.completeCurrent();
    await tick();
    expect(engine.completed).toEqual(['首条指令']);
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

  it('notifyWhenIdle 在队列彻底播完后回调一次（finished）', async () => {
    const queue = makeQueue();
    queue.enqueue('路线概要');
    const done = jest.fn();
    queue.notifyWhenIdle(done);
    await tick();
    expect(done).not.toHaveBeenCalled(); // 概要还在播，不放行
    engine.completeCurrent();
    await tick();
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith('finished');
  });

  it('notifyWhenIdle 在已安静时异步回调（先 enqueue 再等待的时序）', async () => {
    const queue = makeQueue();
    const done = jest.fn();
    queue.notifyWhenIdle(done);
    expect(done).not.toHaveBeenCalled();
    await tick();
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith('finished');
  });

  it('概要被 turn 抢占时回调 interrupted（不能当作播完）', async () => {
    const queue = makeQueue();
    queue.enqueue('路线概要');
    const done = jest.fn();
    queue.notifyWhenIdle(done);
    await tick();
    queue.enqueue('前方左转', 'turn');
    await tick();
    engine.completeCurrent(); // 转向播完，队列安静
    await tick();
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith('interrupted');
  });

  it('引擎失效清空队列时 notifyWhenIdle 以 interrupted 回调，不卡住导航', async () => {
    const queue = makeQueue();
    queue.enqueue('路线概要');
    queue.enqueue('后续提示');
    const done = jest.fn();
    queue.notifyWhenIdle(done);
    engine.failCurrent();
    await tick();
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith('interrupted');
  });

  it('引擎无响应（speak 永不结束）：看门狗按 interrupted 放行并继续队列', async () => {
    const stall = new StallTts();
    stall.hangText = '路线概要';
    const queue = new SpeechQueue({
      engine: stall,
      onUnavailable: jest.fn(),
      stallTimeoutMs: () => 5,
    });
    queue.enqueue('路线概要', 'normal', null, 'map:overview');
    const done = jest.fn();
    queue.notifyWhenIdle(done);
    await tick();
    expect(done).not.toHaveBeenCalled(); // 概要仍被当作「在播」

    await new Promise((resolve) => setTimeout(resolve, 20)); // 等看门狗触发
    expect(stall.stopCount).toBe(1); // 尽力 stop
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith('stalled'); // 引擎无响应，不能当作播完

    queue.enqueue('沿走廊直行 5 米。', 'turn', null, 'nav:instruction');
    await tick();
    expect(stall.spoken).toEqual(['路线概要', '沿走廊直行 5 米。']); // 队列继续推进
  });

  it('看门狗放手后迟到的 speak 结束不会清空新的当前项', async () => {
    const stall = new StallTts();
    stall.hangText = '路线概要';
    let resolveStalled: (() => void) | null = null;
    stall.speak = (text: string): Promise<void> => {
      stall.spoken.push(text);
      if (text === stall.hangText) {
        return new Promise<void>((resolve) => {
          resolveStalled = resolve;
        });
      }
      return Promise.resolve();
    };
    const queue = new SpeechQueue({
      engine: stall,
      onUnavailable: jest.fn(),
      stallTimeoutMs: () => 5,
    });
    queue.enqueue('路线概要', 'normal', null, 'map:overview');
    await new Promise((resolve) => setTimeout(resolve, 20)); // 看门狗放手

    queue.enqueue('前方右转。', 'turn', null, 'nav:instruction');
    await tick();
    expect(stall.spoken).toEqual(['路线概要', '前方右转。']);

    resolveStalled!(); // 迟到的结束信号
    await tick();
    expect(queue.lastSpokenText).toBe('前方右转。'); // 不被迟到信号污染
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

  it('lastSpokenText 记录最近一次入队文本（「再说一遍」重播源）', () => {
    const queue = makeQueue({});
    expect(queue.lastSpokenText).toBeNull();
    queue.enqueue('导航开始');
    expect(queue.lastSpokenText).toBe('导航开始');
    queue.enqueue('前方左转');
    expect(queue.lastSpokenText).toBe('前方左转');
    // 重播自身也走 enqueue：连续重播仍指向同一条
    queue.enqueue(queue.lastSpokenText ?? '');
    expect(queue.lastSpokenText).toBe('前方左转');
  });
});
