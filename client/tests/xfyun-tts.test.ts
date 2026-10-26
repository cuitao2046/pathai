/**
 * XfyunTts 引擎单测（注入假 credential/socket/fs/player，不碰 wx 与网络）。
 * 契约对齐 core/speech/tts-engine.ts：speak 播完 resolve、stop 打断并 resolve、
 * 失败 reject（SpeechQueue FE-04 降级在 speech-queue 测试覆盖）。
 */
import type { AudioPlayerLike, FileWriteLike, SocketLike } from '../core/speech/xfyun-tts';
import { XfyunTts } from '../core/speech/xfyun-tts';
import { bytesToBase64, utf8ToBase64 } from '../utils/base64';

jest.useFakeTimers({ doNotFake: ['setImmediate'] });

const CRED = { url: 'wss://cbm01.test/v1/private/demo', appId: 'app-x' };
const TMP_PATH = 'wxfile://usr/xfyun-tts.mp3';

const flush = async (): Promise<void> => {
  for (let i = 0; i < 30; i += 1) {
    await Promise.resolve();
  }
};

class FakeSocket implements SocketLike {
  sent: string[] = [];
  /** 微信 SocketTask 语义：CONNECTING 时 send 直接 fail（真机踩坑，必须模拟）。 */
  readyState: 'CONNECTING' | 'OPEN' | 'CLOSED' = 'CONNECTING';
  private openCb: (() => void) | null = null;
  private messageCb: ((res: { data: unknown }) => void) | null = null;
  private closeCb: (() => void) | null = null;
  private errorCb: ((err: { errMsg?: string }) => void) | null = null;

  send(opt: { data: string; fail?: (err: { errMsg?: string }) => void }): void {
    if (this.readyState !== 'OPEN') {
      opt.fail?.({ errMsg: 'SocketTask.send:fail SocketTask.readyState is not OPEN' });
      return;
    }
    this.sent.push(opt.data);
  }
  close(): void {
    this.readyState = 'CLOSED';
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
    this.readyState = 'OPEN';
    this.openCb?.();
  }
  emitMessage(msg: unknown): void {
    this.messageCb?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) });
  }
  emitClose(): void {
    this.readyState = 'CLOSED';
    this.closeCb?.();
  }
  emitError(errMsg: string): void {
    this.errorCb?.({ errMsg });
  }
}

class FakePlayer implements AudioPlayerLike {
  src = '';
  playCount = 0;
  stopCount = 0;
  destroyed = false;
  private endedCb: (() => void) | null = null;
  private stopCb: (() => void) | null = null;
  private errorCb: ((err: { errMsg?: string }) => void) | null = null;

  onEnded(cb: () => void): void {
    this.endedCb = cb;
  }
  onStop(cb: () => void): void {
    this.stopCb = cb;
  }
  onError(cb: (err: { errMsg?: string }) => void): void {
    this.errorCb = cb;
  }
  play(): void {
    this.playCount += 1;
  }
  stop(): void {
    this.stopCount += 1;
    this.stopCb?.(); // 真机：stop → onStop 回调
  }
  destroy(): void {
    this.destroyed = true;
  }

  emitEnded(): void {
    this.endedCb?.();
  }
  emitError(errMsg: string): void {
    this.errorCb?.({ errMsg });
  }
}

class FakeFs implements FileWriteLike {
  writes: { filePath: string; data: Uint8Array }[] = [];
  failNext = false;

  writeFile(opt: {
    filePath: string;
    data: ArrayBuffer;
    success: () => void;
    fail: (err: { errMsg?: string }) => void;
  }): void {
    if (this.failNext) {
      this.failNext = false;
      opt.fail({ errMsg: 'writeFile:fail no space' });
      return;
    }
    this.writes.push({ filePath: opt.filePath, data: new Uint8Array(opt.data) });
    opt.success();
  }
}

function makeTts(overrides: {
  socket?: FakeSocket;
  player?: FakePlayer;
  fs?: FakeFs;
  credential?: () => Promise<{ url: string; appId: string }>;
} = {}): { tts: XfyunTts; socket: FakeSocket; player: FakePlayer; fs: FakeFs } {
  const socket = overrides.socket ?? new FakeSocket();
  const player = overrides.player ?? new FakePlayer();
  const fs = overrides.fs ?? new FakeFs();
  const tts = new XfyunTts({
    credential: overrides.credential ?? (() => Promise.resolve(CRED)),
    connect: () => socket,
    fs,
    audioFactory: () => player,
    filePath: TMP_PATH,
  });
  return { tts, socket, player, fs };
}

function audioMessage(b64: string, opts: { final?: boolean } = {}): unknown {
  const status = opts.final ? 2 : 1;
  return {
    header: { code: 0, message: 'success', sid: 'tts-test', status },
    payload: {
      audio: {
        encoding: 'lame',
        sample_rate: 24000,
        channels: 1,
        bit_depth: 16,
        status,
        seq: 0,
        frame_size: 0,
        audio: b64,
      },
    },
  };
}

describe('XfyunTts', () => {
  afterEach(() => {
    jest.clearAllTimers();
  });

  test('全链路：onOpen 后才发单帧请求 → 收分片 → 合并写临时文件 → 播放 → resolve', async () => {
    const { tts, socket, player, fs } = makeTts();
    const done = tts.speak('前方左转到达资源教室');

    await flush();
    // 防回归：CONNECTING 状态绝不 send（真机 SocketTask.send:fail readyState not OPEN）
    expect(socket.sent).toHaveLength(0);
    socket.emitOpen();
    await flush();

    expect(socket.sent).toHaveLength(1);
    const req = JSON.parse(socket.sent[0]);
    expect(req.header).toEqual({ app_id: 'app-x', status: 2 }); // 一次性合成
    expect(req.parameter.tts.vcn).toBe('x5_lingxiaoxuan_flow');
    expect(req.parameter.tts.audio).toEqual({
      encoding: 'lame',
      sample_rate: 24000,
      channels: 1,
      bit_depth: 16,
      frame_size: 0,
    });
    expect(req.payload.text).toMatchObject({
      encoding: 'utf8',
      compress: 'raw',
      format: 'plain',
      status: 2,
      seq: 0,
      // 实测网关强校验：text 必须是 base64(UTF-8)，明文会被 10163 拒绝
      text: utf8ToBase64('前方左转到达资源教室'),
    });

    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([1, 2, 3]))));
    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([4, 5, 6])), { final: true }));
    await flush();

    expect(fs.writes).toHaveLength(1);
    expect(fs.writes[0].filePath).toBe(TMP_PATH);
    expect(Array.from(fs.writes[0].data)).toEqual([1, 2, 3, 4, 5, 6]); // 分片按序合并

    expect(player.playCount).toBe(1);
    expect(player.src).toBe(TMP_PATH);
    expect(socket.readyState).toBe('CLOSED'); // 收到 status==2 即关闭连接

    player.emitEnded();
    await expect(done).resolves.toBeUndefined();
    expect(player.destroyed).toBe(true);
  });

  test('播放中 stop → onStop 视为播完，speak resolve', async () => {
    const { tts, socket, player } = makeTts();
    const done = tts.speak('已到达目的地');
    await flush();
    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([9])), { final: true }));
    await flush();

    tts.stop();
    expect(player.stopCount).toBe(1);
    await expect(done).resolves.toBeUndefined();
    expect(player.destroyed).toBe(true);
  });

  test('合成中 stop → 静默 resolve：不写文件不播放，连接关闭', async () => {
    const { tts, socket, player, fs } = makeTts();
    const done = tts.speak('播报中途被打断');
    await flush();

    tts.stop();
    await expect(done).resolves.toBeUndefined();
    expect(socket.readyState).toBe('CLOSED');
    expect(fs.writes).toHaveLength(0);
    expect(player.playCount).toBe(0);
  });

  test('服务端业务错误（header.code!=0）reject', async () => {
    const { tts, socket } = makeTts();
    const done = tts.speak('错误分支');
    await flush();
    socket.emitMessage({ header: { code: 10113, message: '发音人未授权', status: 1 } });
    await expect(done).rejects.toThrow('发音人未授权');
    expect(socket.readyState).toBe('CLOSED');
  });

  test('status==2 但无音频数据 → reject', async () => {
    const { tts, socket } = makeTts();
    const done = tts.speak('空结果');
    await flush();
    socket.emitMessage({ header: { code: 0, message: 'success', status: 2 } });
    await expect(done).rejects.toThrow('未返回音频数据');
  });

  test('服务端未发收尾帧直接关闭：已有分片即完成并播放', async () => {
    const { tts, socket, player } = makeTts();
    const done = tts.speak('关连接分支');
    await flush();
    socket.emitOpen();
    await flush();
    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([7, 8]))));
    socket.emitClose();
    await flush();
    expect(player.playCount).toBe(1);
    player.emitEnded();
    await expect(done).resolves.toBeUndefined();
  });

  test('连接失败 reject（errMsg 透出）', async () => {
    const { tts, socket } = makeTts();
    const done = tts.speak('连接失败');
    await flush();
    socket.emitError('connectSocket:fail fail');
    await expect(done).rejects.toThrow('connectSocket:fail fail');
  });

  test('握手超时：8s 未 onOpen → reject 连接超时（不悬挂播报）', async () => {
    const { tts, socket } = makeTts();
    const done = tts.speak('超时分支');
    await flush();
    expect(socket.sent).toHaveLength(0);

    jest.advanceTimersByTime(8000);
    await expect(done).rejects.toThrow('讯飞合成连接超时');
    expect(socket.readyState).toBe('CLOSED');
  });

  test('临时文件写入失败 reject', async () => {
    const fs = new FakeFs();
    fs.failNext = true;
    const { tts, socket } = makeTts({ fs });
    const done = tts.speak('写文件失败');
    await flush();
    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([1])), { final: true }));
    await expect(done).rejects.toThrow('writeFile:fail no space');
  });

  test('播放失败 reject（交由 SpeechQueue 降级）', async () => {
    const { tts, socket, player } = makeTts();
    const done = tts.speak('播放失败');
    await flush();
    socket.emitMessage(audioMessage(bytesToBase64(new Uint8Array([1])), { final: true }));
    await flush();
    player.emitError('decode error');
    await expect(done).rejects.toThrow('decode error');
  });

  test('凭据获取失败 reject', async () => {
    const { tts } = makeTts({ credential: () => Promise.reject(new Error('拿签名失败')) });
    await expect(tts.speak('任意')).rejects.toThrow('拿签名失败');
  });

  test('空文本直接返回，不连接不合成', async () => {
    const { tts, socket } = makeTts();
    await expect(tts.speak('   ')).resolves.toBeUndefined();
    await flush();
    expect(socket.sent).toHaveLength(0);
  });
});
