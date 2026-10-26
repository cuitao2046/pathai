/**
 * XfyunAsrEngine 契约单测（注入假 credential/假 recorder/假 socket，不碰 wx 与网络）。
 * 契约对齐 core/asr/asr-engine.ts 与 tests/push-to-talk.test.ts 的既有语义：
 * start/stop settle 最终文本、重复 start 同步抛错、cancel → AsrCancelledError、
 * 凭据/连接/服务端故障 reject、onPartial 回吐、wpgs 增量聚合。
 *
 * 协议帧断言基于讯飞文档（spark_zh_iat_new）：首帧含 parameter.iat、
 * status 0→1→2、seq 递增、1024B/40ms 节流、payload.result.text base64。
 */
import { AsrCancelledError } from '../core/asr/asr-engine';
import type { RecorderLike, PcmFrameHandlers } from '../core/asr/pcm-recorder';
import {
  XfyunAsrEngine,
  applyWpgs,
  floatToInt16Bytes,
  resultChunkText,
  wpgsText,
  type ResultMessage,
  type SocketLike,
  type WpgsBuffer,
} from '../core/asr/xfyun-asr';
import { utf8ToBase64 } from '../utils/base64';

jest.useFakeTimers({ doNotFake: ['setImmediate'] });

/** 排空微任务链（beginSession/finalize 均为 async）。 */
const flush = async (): Promise<void> => {
  for (let i = 0; i < 30; i += 1) {
    await Promise.resolve();
  }
};

const CRED = { url: 'wss://iat.test/v1?sig=1', appId: 'app-x' };

class FakeSocket implements SocketLike {
  sent: string[] = [];
  closed = false;
  failNextSend = false;
  private openCb: (() => void) | null = null;
  private messageCb: ((res: { data: unknown }) => void) | null = null;
  private closeCb: (() => void) | null = null;
  private errorCb: ((err: { errMsg?: string }) => void) | null = null;

  send(opt: { data: string; fail?: (err: { errMsg?: string }) => void }): void {
    if (this.failNextSend) {
      this.failNextSend = false;
      opt.fail?.({ errMsg: 'send:fail socket closed' });
      return;
    }
    this.sent.push(opt.data);
  }

  close(): void {
    this.closed = true;
  }

  onOpen(cb: () => void): void {
    this.openCb = cb;
  }
  onMessage(cb: (res: { data: unknown }) => void): void {
    this.messageCb = cb;
  }
  onClose(cb: () => void): void {
    this.closeCb = cb;
  }
  onError(cb: (err: { errMsg?: string }) => void): void {
    this.errorCb = cb;
  }

  emitOpen(): void {
    this.openCb?.();
  }
  emitMessage(msg: unknown): void {
    this.messageCb?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) });
  }
  emitClose(): void {
    this.closeCb?.();
  }
  emitError(errMsg: string): void {
    this.errorCb?.({ errMsg });
  }

  frames(): { header: { app_id: string; status: number }; parameter?: { iat: { domain: string } }; payload: { audio: { seq: number; status: number; audio: string; sample_rate: number; channels: number; bit_depth: number; encoding: string } } }[] {
    return this.sent.map((s) => JSON.parse(s));
  }
}

class FakeRecorder implements RecorderLike {
  started = false;
  stopCount = 0;
  cancelCount = 0;
  private handlers: PcmFrameHandlers | null = null;

  start(handlers: PcmFrameHandlers): void {
    this.started = true;
    this.handlers = handlers;
  }

  stop(): Promise<void> {
    this.stopCount += 1;
    return Promise.resolve();
  }

  cancel(): void {
    this.cancelCount += 1;
    this.started = false;
  }

  emit(samples: Float32Array): void {
    this.handlers?.onFrame(samples);
  }
}

function samples(n: number): Float32Array {
  return new Float32Array(n).fill(0.25);
}

/** 构造服务端识别结果消息（text 为 base64(JSON)）。 */
function iatMessage(
  inner: Partial<ResultMessage> & { sn: number; words?: string[] },
  opts: { final?: boolean } = {},
): unknown {
  const message: ResultMessage = {
    sn: inner.sn,
    ws: (inner.words ?? []).map((w) => ({ cw: [{ w }] })),
    pgs: inner.pgs,
    rg: inner.rg,
    ls: inner.ls,
  };
  const status = opts.final ? 2 : 1;
  return {
    header: { code: 0, message: 'success', sid: 'iat-test', status },
    payload: {
      result: {
        compress: 'raw',
        encoding: 'utf8',
        format: 'json',
        seq: inner.sn,
        status,
        text: utf8ToBase64(JSON.stringify(message)),
      },
    },
  };
}

function makeEngine(
  recorder: FakeRecorder,
  socket: FakeSocket,
  credential: () => Promise<{ url: string; appId: string }> = () => Promise.resolve(CRED),
): XfyunAsrEngine {
  return new XfyunAsrEngine({ credential, recorder, connect: () => socket });
}

afterEach(() => {
  jest.clearAllTimers();
});

describe('XfyunAsrEngine 契约', () => {
  let recorder: FakeRecorder;
  let socket: FakeSocket;

  beforeEach(() => {
    recorder = new FakeRecorder();
    socket = new FakeSocket();
  });

  test('全链路：开录→连上→40ms 泵帧（首帧含 parameter.iat）→partial→stop→末帧→最终 resolve', async () => {
    const engine = makeEngine(recorder, socket);
    const partials: string[] = [];
    const session = engine.start({ onPartial: (t) => partials.push(t) });
    await flush();
    expect(recorder.started).toBe(true); // 录音先于凭据/连接启动，开口不丢

    // 连接前先攒两帧音频（开口期间的音频入队）
    recorder.emit(samples(512)); // 1024B
    recorder.emit(samples(512));
    socket.emitOpen();
    await flush();

    const frames = socket.frames();
    expect(frames).toHaveLength(1); // 立即发首帧，其余按 40ms 节流
    expect(frames[0].header).toEqual({ app_id: 'app-x', status: 0 });
    expect(frames[0].payload.audio.seq).toBe(0);
    expect(frames[0].payload.audio.encoding).toBe('raw');
    expect(frames[0].payload.audio.sample_rate).toBe(16000);
    expect(frames[0].payload.audio.channels).toBe(1);
    expect(frames[0].payload.audio.bit_depth).toBe(16);
    expect(frames[0].payload.audio.audio.length).toBeGreaterThan(0);
    expect(frames[0].parameter?.iat.domain).toBe('spark_asr_v2');

    jest.advanceTimersByTime(40);
    await flush();
    expect(socket.frames()).toHaveLength(2); // 第二帧按节奏发出
    expect(socket.frames()[1].header.status).toBe(1);
    expect(socket.frames()[1].parameter).toBeUndefined(); // parameter 只随首帧

    // 服务端 wpgs 中间结果 → partial 回吐
    socket.emitMessage(iatMessage({ sn: 1, words: ['带我去'] }));
    await flush();
    expect(partials).toEqual(['带我去']);
    socket.emitMessage(iatMessage({ sn: 2, words: ['资源教室'] }));
    await flush();
    expect(partials).toEqual(['带我去', '带我去资源教室']);

    engine.stop();
    await flush();
    expect(recorder.stopCount).toBe(1);
    const afterStop = socket.frames();
    expect(afterStop[afterStop.length - 1].header.status).toBe(2); // 末帧
    expect(afterStop[afterStop.length - 1].payload.audio.audio).toBe('');
    expect(afterStop[afterStop.length - 1].payload.audio.seq).toBe(2);

    socket.emitMessage(iatMessage({ sn: 3, words: ['。'] }, { final: true }));
    await expect(session).resolves.toBe('带我去资源教室。');
    expect(socket.closed).toBe(true);
  });

  test('重复 start 同步抛错（同一时刻仅一个会话），结束后可再开', async () => {
    const engine = makeEngine(recorder, socket);
    const first = engine.start({});
    await flush();
    expect(() => engine.start({})).toThrow('ASR 会话进行中');

    socket.emitOpen();
    await flush();
    recorder.emit(samples(512));
    engine.stop();
    await flush();
    socket.emitMessage(iatMessage({ sn: 1, words: ['好'] }, { final: true }));
    await expect(first).resolves.toBe('好');

    const second = engine.start({});
    await flush();
    engine.stop();
    await expect(second).resolves.toBe(''); // 快速点按：无音频空文本
  });

  test('cancel → AsrCancelledError、关 socket、停录，迟到消息不再 settle', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    recorder.emit(samples(512));

    engine.cancel();
    await expect(session).rejects.toBeInstanceOf(AsrCancelledError);
    expect(socket.closed).toBe(true);
    expect(recorder.cancelCount).toBe(1);

    socket.emitMessage(iatMessage({ sn: 1, words: ['忽略'] }, { final: true }));
    engine.stop();
    await flush();
    expect(recorder.stopCount).toBe(0); // 会话已摘除，stop 不再生效
  });

  test('凭据获取失败以 Error reject 并释放录音', async () => {
    const engine = makeEngine(recorder, socket, () => Promise.reject(new Error('拿签名失败')));
    const session = engine.start({});
    await expect(session).rejects.toThrow('拿签名失败');
    expect(recorder.cancelCount).toBe(1);
    expect(socket.sent).toHaveLength(0);
  });

  test('连接超时（8s 未 open）以 Error reject', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    jest.advanceTimersByTime(8000);
    await expect(session).rejects.toThrow('讯飞连接超时');
    expect(recorder.cancelCount).toBe(1);
  });

  test('末帧发出后服务端不回最终结果 → 10s 超时 reject', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    recorder.emit(samples(512));
    engine.stop();
    await flush();
    jest.advanceTimersByTime(40); // 队列中的音频帧按节奏泵出
    await flush();
    expect(socket.frames().slice(-1)[0].header.status).toBe(2); // 末帧已发

    jest.advanceTimersByTime(10000);
    await expect(session).rejects.toThrow('识别结果响应超时');
  });

  test('服务端业务错误（header.code!=0）以服务端消息 reject', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    socket.emitMessage({ header: { code: 10105, message: '认证失败', status: 0 } });
    await expect(session).rejects.toThrow('认证失败');
    expect(socket.closed).toBe(true);
    expect(recorder.cancelCount).toBe(1); // 录音中出错必须释放麦克风
  });

  test('连接异常关闭且无最终结果 → reject', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    socket.emitClose();
    await expect(session).rejects.toThrow('讯飞连接已关闭');
  });

  test('音频帧发送失败 → reject', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    socket.failNextSend = true;
    recorder.emit(samples(512));
    jest.advanceTimersByTime(40);
    await expect(session).rejects.toThrow('send:fail socket closed');
  });

  test('快速点按（无音频即松手）→ 空文本收尾且不连服务端', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    engine.stop();
    await expect(session).resolves.toBe('');
    expect(socket.sent).toHaveLength(0);
    expect(recorder.stopCount).toBe(1);
  });

  test('松手晚于连接建立之前：连上后补发开口音频再收尾', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    recorder.emit(samples(512)); // 凭据/握手期间的开口帧
    engine.stop();
    await flush(); // finalize 卡在 waitOpened
    expect(socket.sent).toHaveLength(0);

    socket.emitOpen();
    await flush();
    const frames = socket.frames();
    expect(frames).toHaveLength(2); // 开口帧(status0) + 末帧(status2)
    expect(frames[0].header.status).toBe(0);
    expect(frames[1].header.status).toBe(2);

    socket.emitMessage(iatMessage({ sn: 1, words: ['去资源教室'] }, { final: true }));
    await expect(session).resolves.toBe('去资源教室');
  });

  test('30s 录满自动停（帧停止流动）不触发收尾，等 PTT 松手才 finalize', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    recorder.emit(samples(512));

    let settled = false;
    void session.then(() => (settled = true), () => (settled = true));
    jest.advanceTimersByTime(35_000); // 录音器早已不再产帧，socket 仍空闲
    await flush();
    expect(settled).toBe(false);

    engine.stop();
    await flush();
    socket.emitMessage(iatMessage({ sn: 1, words: ['指令'] }, { final: true }));
    await expect(session).resolves.toBe('指令');
  });

  test('wpgs 增量：rpl 替换后 partial 与最终文本均为修正结果', async () => {
    const engine = makeEngine(recorder, socket);
    const partials: string[] = [];
    const session = engine.start({ onPartial: (t) => partials.push(t) });
    await flush();
    socket.emitOpen();
    await flush();
    recorder.emit(samples(512)); // 会话必须有音频，否则按快速点按空文本收尾
    await flush();
    socket.emitMessage(iatMessage({ sn: 1, words: ['带我去资'] }));
    await flush();
    socket.emitMessage(iatMessage({ sn: 2, words: ['源'] }));
    await flush();
    expect(partials).toEqual(['带我去资', '带我去资源']);

    // 服务端修正：替换第1~2片为完整结果
    socket.emitMessage(iatMessage({ sn: 2, words: ['带我去资源教室'], pgs: 'rpl', rg: [1, 2] }));
    await flush();
    expect(partials[partials.length - 1]).toBe('带我去资源教室');

    engine.stop();
    await flush();
    jest.advanceTimersByTime(40); // 泵出队列音频 → 末帧发出
    await flush();
    socket.emitMessage(iatMessage({ sn: 3, words: ['！'] }, { final: true }));
    await expect(session).resolves.toBe('带我去资源教室！');
  });

  test('服务端在松手前先给最终结果：stop 时直接收尾，不再发末帧', async () => {
    const engine = makeEngine(recorder, socket);
    const session = engine.start({});
    await flush();
    socket.emitOpen();
    await flush();
    recorder.emit(samples(512));
    jest.advanceTimersByTime(40);
    await flush();

    socket.emitMessage(iatMessage({ sn: 1, words: ['你好'] }, { final: true })); // 未 stop → 不 settle
    await flush();
    const sentBefore = socket.sent.length;

    engine.stop();
    await expect(session).resolves.toBe('你好');
    expect(socket.sent.length).toBe(sentBefore); // 未补发末帧
  });
});

describe('applyWpgs / resultChunkText 纯函数', () => {
  test('resultChunkText：多词多段顺序拼接', () => {
    expect(
      resultChunkText({
        sn: 1,
        ws: [{ cw: [{ w: '带' }, { w: '我去' }] }, { cw: [{ w: '图书馆' }] }],
      }),
    ).toBe('带我去图书馆');
    expect(resultChunkText({ sn: 1 })).toBe('');
  });

  test('无 pgs：按 sn 升序聚合全文', () => {
    const buf: WpgsBuffer = {};
    applyWpgs(buf, { sn: 2, ws: [{ cw: [{ w: '世界' }] }] });
    applyWpgs(buf, { sn: 1, ws: [{ cw: [{ w: '你好' }] }] });
    expect(wpgsText(buf)).toBe('你好世界');
  });

  test('pgs=apd：同 sn 追加，不同 sn 写入', () => {
    const buf: WpgsBuffer = {};
    applyWpgs(buf, { sn: 1, ws: [{ cw: [{ w: '你好' }] }] });
    applyWpgs(buf, { sn: 1, pgs: 'apd', ws: [{ cw: [{ w: '世界' }] }] });
    expect(buf[1]).toBe('你好世界');
    applyWpgs(buf, { sn: 2, pgs: 'apd', ws: [{ cw: [{ w: '！' }] }] });
    expect(wpgsText(buf)).toBe('你好世界！');
  });

  test('pgs=rpl：替换 rg 区间（当前 sn 胜出，区间内其余清除）', () => {
    const buf: WpgsBuffer = {};
    applyWpgs(buf, { sn: 1, ws: [{ cw: [{ w: '带我去资' }] }] });
    applyWpgs(buf, { sn: 2, ws: [{ cw: [{ w: '源' }] }] });
    applyWpgs(buf, { sn: 3, ws: [{ cw: [{ w: '教室' }] }] });
    const text = applyWpgs(buf, {
      sn: 3,
      pgs: 'rpl',
      rg: [1, 3],
      ws: [{ cw: [{ w: '去资源教室' }] }],
    });
    expect(text).toBe('去资源教室');
    expect(Object.keys(buf)).toEqual(['3']);
  });

  test('pgs=rpl 且当前 sn 在区间外：清掉区间、写入自身', () => {
    const buf: WpgsBuffer = {};
    applyWpgs(buf, { sn: 1, ws: [{ cw: [{ w: '甲' }] }] });
    applyWpgs(buf, { sn: 2, ws: [{ cw: [{ w: '乙' }] }] });
    const text = applyWpgs(buf, { sn: 5, pgs: 'rpl', rg: [1, 2], ws: [{ cw: [{ w: '丙' }] }] });
    expect(text).toBe('丙');
  });
});

describe('floatToInt16Bytes', () => {
  test('小端 16bit 与钳位', () => {
    expect(Array.from(floatToInt16Bytes(new Float32Array([0])))).toEqual([0, 0]);
    expect(Array.from(floatToInt16Bytes(new Float32Array([1])))).toEqual([0xff, 0x7f]); // 32767 钳位
    expect(Array.from(floatToInt16Bytes(new Float32Array([-1])))).toEqual([0x00, 0x80]); // -32768
    expect(Array.from(floatToInt16Bytes(new Float32Array([5])))).toEqual([0xff, 0x7f]); // 超界钳位
    const half = new DataView(floatToInt16Bytes(new Float32Array([0.5])).buffer);
    expect(half.getInt16(0, true)).toBe(16384);
  });
});
