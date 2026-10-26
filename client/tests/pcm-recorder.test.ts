/**
 * PcmRecorder 单测（假 RecorderManager 注入，不碰 wx）：
 * 启动参数、帧归一化、stop 等 onStop、cancel 丢帧、录音错误补抛。
 */
import { PcmRecorder, MAX_RECORD_MS, type RecorderManagerLike, type PcmFrameHandlers } from '../core/asr/pcm-recorder';

class FakeRecorderManager implements RecorderManagerLike {
  startOption: Record<string, unknown> | null = null;
  stopCount = 0;
  /** stop() 是否同步派发 onStop（false 模拟微信异步/错误先行）。 */
  autoStopEvent = true;
  private frameCb: ((res: { frameBuffer?: ArrayBuffer }) => void) | null = null;
  private stopCb: ((res: unknown) => void) | null = null;
  private errorCb: ((err: { errMsg?: string }) => void) | null = null;

  start(option: Record<string, unknown>): void {
    this.startOption = option;
  }

  stop(): void {
    this.stopCount += 1;
    if (this.autoStopEvent) {
      this.stopCb?.({}); // 默认模拟微信同步派发 onStop
    }
  }

  onFrameRecorded(listener: (res: { frameBuffer?: ArrayBuffer }) => void): void {
    this.frameCb = listener;
  }

  onStop(listener: (res: unknown) => void): void {
    this.stopCb = listener;
  }

  onError(listener: (err: { errMsg?: string }) => void): void {
    this.errorCb = listener;
  }

  /** 推一帧 Int16 采样（小端字节）。 */
  emitFrame(values: number[]): void {
    const int16 = Int16Array.from(values);
    this.frameCb?.({ frameBuffer: int16.buffer.slice(0) });
  }

  fireError(errMsg: string): void {
    this.errorCb?.({ errMsg });
  }
}

describe('PcmRecorder', () => {
  let fake: FakeRecorderManager;
  let recorder: PcmRecorder;
  let received: Float32Array[];

  const handlers: PcmFrameHandlers = {
    onFrame: (samples) => received.push(samples),
  };

  beforeEach(() => {
    fake = new FakeRecorderManager();
    recorder = new PcmRecorder(fake);
    received = [];
  });

  test('start 参数：PCM 16kHz 单声道 1KB 帧 30s 语音识别音源', () => {
    recorder.start(handlers);
    expect(recorder.isRecording).toBe(true);
    expect(fake.startOption).toMatchObject({
      format: 'PCM',
      sampleRate: 16000,
      numberOfChannels: 1,
      frameSize: 1,
      duration: MAX_RECORD_MS,
      audioSource: 'voice_recognition',
    });
  });

  test('帧 Int16 归一化为 Float32 [-1,1]', () => {
    recorder.start(handlers);
    fake.emitFrame([32767, -32768, 16384, 0]);
    expect(received).toHaveLength(1);
    expect(received[0].length).toBe(4);
    expect(received[0][0]).toBeCloseTo(1, 4);
    expect(received[0][1]).toBe(-1);
    expect(received[0][2]).toBeCloseTo(0.5, 4);
    expect(received[0][3]).toBe(0);
  });

  test('stop 调 manager.stop 并等 onStop 后 resolve，其后不再推帧', async () => {
    recorder.start(handlers);
    fake.emitFrame([1000]);
    await recorder.stop();
    expect(fake.stopCount).toBe(1);
    expect(recorder.isRecording).toBe(false);
    received = [];
    fake.emitFrame([2000]); // 停止后回调（微信一般不再发；防御性丢弃）
    expect(received).toHaveLength(0);
  });

  test('cancel 停录并丢弃后续帧（不 reject 等待者）', async () => {
    recorder.start(handlers);
    recorder.cancel();
    expect(fake.stopCount).toBe(1);
    expect(recorder.isRecording).toBe(false);
    received = [];
    fake.emitFrame([500]);
    expect(received).toHaveLength(0);
    // cancel 后 settle 已清：stop 不会挂起
    await expect(recorder.stop()).resolves.toBeUndefined();
  });

  test('录音错误：onError 挂起的 stop reject，且事后 stop 仍补抛', async () => {
    // 场景 1：stop() 已在等待（onStop 延迟）→ onError 触发 reject
    recorder.start(handlers);
    fake.autoStopEvent = false;
    const pending = recorder.stop();
    fake.fireError('录音中断');
    await expect(pending).rejects.toThrow('录音中断');

    // 场景 2：错误发生在 stop() 之前 → stop 时补抛，不吞成空结果
    fake.autoStopEvent = true;
    recorder.start(handlers);
    fake.fireError('系统占用');
    await expect(recorder.stop()).rejects.toThrow('系统占用');
  });

  test('未启动直接 stop 立即 resolve', async () => {
    await expect(recorder.stop()).resolves.toBeUndefined();
    expect(fake.stopCount).toBe(0);
  });
});
