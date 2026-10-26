/**
 * SherpaOnnxAsrEngine 契约单测（注入假 runtime/假 recorder，不碰 wx 与 wasm）。
 * 契约对齐 core/asr/asr-engine.ts 与 tests/push-to-talk.test.ts 的既有语义：
 * start/stop settle 最终文本、重复 start 同步抛错、cancel → AsrCancelledError、
 * runtime 故障 reject、onPartial 节流。
 */
import { AsrCancelledError } from '../core/asr/asr-engine';
import { SherpaOnnxAsrEngine } from '../core/asr/sherpa-onnx-asr';
import type { RecorderLike, PcmFrameHandlers } from '../core/asr/pcm-recorder';
import type { AsrRuntime, OnlineRecognizerLike, OnlineStreamLike } from '../core/asr/sherpa-runtime';

/** 排空微任务链（beginSession/finalize 均为 async）。 */
const flush = async (): Promise<void> => {
  for (let i = 0; i < 30; i += 1) {
    await Promise.resolve();
  }
};

class FakeStream implements OnlineStreamLike {
  accepted: number[] = [];
  constructor(private readonly recognizer: FakeRecognizer) {}
  acceptWaveform(_sampleRate: number, samples: Float32Array): void {
    this.accepted.push(samples.length);
    this.recognizer.pendingFrames += 1; // 喂一帧 → 产生一个待解码窗口
  }
}

class FakeRecognizer implements OnlineRecognizerLike {
  text = '去资源教室';
  decodeCount = 0;
  /** 待解码窗口数：isReady 必须随 decode 递减，否则 while(isReady) 死循环。 */
  pendingFrames = 0;
  stream: FakeStream = new FakeStream(this);

  createStream(): OnlineStreamLike {
    this.pendingFrames = 0;
    this.stream = new FakeStream(this);
    return this.stream;
  }

  isReady(): boolean {
    return this.pendingFrames > 0;
  }

  decode(): void {
    this.pendingFrames -= 1;
    this.decodeCount += 1;
  }

  getResult(): { text: string } {
    return { text: this.text };
  }

  isEndpoint(): boolean {
    return false;
  }

  reset(): void {}
}

class FakeRecorder implements RecorderLike {
  started = false;
  stopCount = 0;
  cancelCount = 0;
  stopImpl: () => Promise<void> = () => Promise.resolve();
  private handlers: PcmFrameHandlers | null = null;

  start(handlers: PcmFrameHandlers): void {
    this.started = true;
    this.handlers = handlers;
  }

  stop(): Promise<void> {
    this.stopCount += 1;
    return this.stopImpl();
  }

  cancel(): void {
    this.cancelCount += 1;
    this.started = false;
  }

  emit(samples: Float32Array): void {
    this.handlers?.onFrame(samples);
  }
}

function samplesOf(n: number): Float32Array {
  return new Float32Array(n).fill(0.1);
}

function makeEngine(recognizer: FakeRecognizer, recorder: FakeRecorder, runtime?: () => Promise<AsrRuntime>) {
  return new SherpaOnnxAsrEngine({
    runtime: runtime ?? (() => Promise.resolve({ recognizer })),
    recorder,
  });
}

describe('SherpaOnnxAsrEngine 契约', () => {
  test('start → 帧 → stop 以最终文本 resolve，全程喂帧解码', async () => {
    const recognizer = new FakeRecognizer();
    const recorder = new FakeRecorder();
    const engine = makeEngine(recognizer, recorder);
    const partials: string[] = [];

    const session = engine.start({ onPartial: (t) => partials.push(t) });
    await flush();
    expect(recorder.started).toBe(true);

    recorder.emit(samplesOf(20000)); // > 0.5s → 触发 partial
    await flush();
    expect(partials).toEqual(['去资源教室']);
    expect(recognizer.decodeCount).toBeGreaterThan(0);

    engine.stop();
    await expect(session).resolves.toBe('去资源教室');
    expect(recorder.stopCount).toBe(1);
  });

  test('重复 start 同步抛错（同一时刻仅一个会话）', async () => {
    const recognizer = new FakeRecognizer();
    const recorder = new FakeRecorder();
    const engine = makeEngine(recognizer, recorder);

    const first = engine.start({});
    await flush();
    expect(() => engine.start({})).toThrow('ASR 会话进行中');
    engine.stop();
    await first;

    // 会话结束后可再次开始
    const second = engine.start({});
    await flush();
    engine.stop();
    await expect(second).resolves.toBe('去资源教室');
  });

  test('cancel → AsrCancelledError、停录、迟到回调不再 settle', async () => {
    const recognizer = new FakeRecognizer();
    const recorder = new FakeRecorder();
    const engine = makeEngine(recognizer, recorder);

    const session = engine.start({});
    await flush();
    engine.cancel();
    await expect(session).rejects.toBeInstanceOf(AsrCancelledError);
    expect(recorder.cancelCount).toBe(1);

    // 取消后的帧与 stop 不得再影响已结束会话，也不得抛错
    recorder.emit(samplesOf(20000));
    engine.stop();
    engine.cancel();
    await flush();
    expect(recorder.stopCount).toBe(0);
  });

  test('runtime 故障（模型下载失败等）以 Error reject', async () => {
    const recorder = new FakeRecorder();
    const engine = new SherpaOnnxAsrEngine({
      runtime: () => Promise.reject(new Error('模型下载失败')),
      recorder,
    });
    const session = engine.start({});
    await expect(session).rejects.toThrow('模型下载失败');
    expect(recorder.started).toBe(false);
  });

  test('onPartial 节流：累计 0.5s 采样才发，且文本变化才重发', async () => {
    const recognizer = new FakeRecognizer();
    recognizer.text = '';
    const recorder = new FakeRecorder();
    const engine = makeEngine(recognizer, recorder);
    const partials: string[] = [];

    const session = engine.start({ onPartial: (t) => partials.push(t) });
    await flush();

    recorder.emit(samplesOf(7999)); // 未到阈值
    await flush();
    expect(partials).toHaveLength(0);

    recorder.emit(samplesOf(1)); // 达 8000（0.5s×16k）
    await flush();
    expect(partials).toHaveLength(0); // 文本为空不发

    recognizer.text = '去资源';
    recorder.emit(samplesOf(8000));
    await flush();
    expect(partials).toEqual(['去资源']);

    recorder.emit(samplesOf(8000)); // 文本未变，不重发
    await flush();
    expect(partials).toEqual(['去资源']);

    engine.stop();
    await expect(session).resolves.toBe('去资源');
  });

  test('runtime 就绪前已松手：resolve 空文本且不启动录音', async () => {
    let release!: (v: { recognizer: FakeRecognizer }) => void;
    const deferred = new Promise<{ recognizer: FakeRecognizer }>((resolve) => {
      release = resolve;
    });
    const recognizer = new FakeRecognizer();
    const recorder = new FakeRecorder();
    const engine = makeEngine(recognizer, recorder, () => deferred);

    const session = engine.start({});
    await flush();
    engine.stop(); // 运行时还没好就松手
    expect(recorder.started).toBe(false);

    release({ recognizer });
    await expect(session).resolves.toBe('');
    expect(recorder.started).toBe(false);
  });

  test('录音器 stop 失败以 Error reject', async () => {
    const recognizer = new FakeRecognizer();
    const recorder = new FakeRecorder();
    recorder.stopImpl = () => Promise.reject(new Error('录音中断'));
    const engine = makeEngine(recognizer, recorder);

    const session = engine.start({});
    await flush();
    engine.stop();
    await expect(session).rejects.toThrow('录音中断');
  });
});
