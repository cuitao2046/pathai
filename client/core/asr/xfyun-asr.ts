/**
 * 讯飞语音听写大模型（中英）V2.0 ASR 引擎（AsrEngine 契约实现，live 数据源）。
 *
 * 链路（wss 直连讯飞，签名 URL 由后端签发、凭据 provider 缓存 ≤4min）：
 *  start → 立即开录（音频先入本地队列，保住开口瞬间）→ 取凭据 → connect →
 *          连上后按 40ms 节流泵帧（帧在发送时构造，保证 appId 已就绪；
 *          首帧含 parameter.iat，status 0→1）→
 *          收 payload.result（dwa=wpgs 增量）回吐 onPartial；
 *  stop  → 停录 → 等连上 → 服务端已给最终结果则直接收尾，否则队列刷完 →
 *          发末帧 status:2 → 等 header.status==2 收最终全文 resolve；
 *  cancel→ 先摘会话 + 停录 + 关 socket → AsrCancelledError reject。
 *
 * 协议要点（docs：spark_zh_iat_new）：
 *  - 音频 PCM 16k/16bit/单声道，1024B ≈64ms/帧，建议间隔 40ms；
 *  - parameter.iat 必须随首帧；payload.audio.seq 从 0 递增；
 *  - wpgs：pgs=apd 追加、pgs=rpl 替换 rg 指定 sn 区间，按 sn 聚合出全文。
 *
 * 依赖注入（单测用假件，不碰 wx/网络）：credential、recorder、connect。
 */
import { AsrCancelledError, type AsrEngine, type AsrHandlers } from './asr-engine';
import { PcmRecorder, type RecorderLike } from './pcm-recorder';
import { base64ToUtf8, bytesToBase64 } from '../../utils/base64';
import { createLogger } from '../../utils/logger';
import type { WssCredentialProvider } from '../../utils/wss-url';

const log = createLogger('xfyun-asr');

/** 讯飞建议发送节奏：1024B 音频 / 40ms。 */
const SEND_INTERVAL_MS = 40;
/** 连接超时（取凭据后的 wss 握手）。 */
const OPEN_TIMEOUT_MS = 8000;
/** 末帧发出后等服务端最终结果的超时。 */
const FINAL_TIMEOUT_MS = 10000;
const SAMPLE_RATE = 16000;

/** 首帧必须携带的服务参数（缺失直接报错）。 */
const IAT_PARAMETER = {
  iat: {
    domain: 'spark_asr_v2',
    language: 'zh_cn',
    accent: 'mandarin',
    dwa: 'wpgs',
    result: { encoding: 'utf8', compress: 'raw', format: 'json' },
  },
};

/** 讯飞 WebSocket 连接的最小面（wx.connectSocket 返回的 SocketTask 结构兼容）。 */
export interface SocketLike {
  send(opt: { data: string; fail?: (err: { errMsg?: string }) => void }): void;
  close(opt?: { code?: number; reason?: string }): void;
  onOpen(cb: () => void): void;
  onMessage(cb: (res: { data: unknown }) => void): void;
  onClose(cb: () => void): void;
  onError(cb: (err: { errMsg?: string }) => void): void;
}

/** Float32 [-1,1] → PCM 16bit 小端字节（讯飞 raw 音频格式）。 */
export function floatToInt16Bytes(samples: Float32Array): Uint8Array {
  const out = new Uint8Array(samples.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const v = Math.max(-32768, Math.min(32767, Math.round(samples[i] * 32768)));
    view.setInt16(i * 2, v, true);
  }
  return out;
}

/** 识别结果片段（payload.result.text base64 解码后的 JSON）。 */
export interface ResultMessage {
  sn: number;
  ls?: boolean;
  pgs?: string;
  rg?: [number, number];
  ws?: { cw?: { w: string }[] }[];
}

/** wpgs 聚合缓冲：sn → 该片文本。 */
export type WpgsBuffer = Record<number, string>;

/** 单片结果文本：ws[].cw[].w 顺序拼接。 */
export function resultChunkText(msg: ResultMessage): string {
  return (msg.ws ?? [])
    .map((word) => (word.cw ?? []).map((c) => c.w).join(''))
    .join('');
}

/** 按 sn 升序聚合全文。 */
export function wpgsText(buf: WpgsBuffer): string {
  return Object.keys(buf)
    .map(Number)
    .sort((a, b) => a - b)
    .map((sn) => buf[sn])
    .join('');
}

/**
 * 把一片 wpgs 结果并入缓冲并返回当前全文（原地修改 buf）。
 * - pgs='rpl'：替换 rg=[from,to] 区间（当前 sn 的新文本胜出，其余清掉）；
 * - pgs='apd'：追加（同 sn 已有内容时拼接，否则写入）；
 * - 无 pgs：直接写入该 sn。
 */
export function applyWpgs(buf: WpgsBuffer, msg: ResultMessage): string {
  const text = resultChunkText(msg);
  if (msg.pgs === 'rpl' && Array.isArray(msg.rg)) {
    const [from, to] = msg.rg;
    for (let sn = from; sn <= to; sn += 1) {
      if (sn !== msg.sn) {
        delete buf[sn];
      }
    }
  }
  if (text.length > 0) {
    if (msg.pgs === 'apd' && buf[msg.sn] !== undefined) {
      buf[msg.sn] += text;
    } else {
      buf[msg.sn] = text;
    }
  }
  return wpgsText(buf);
}

interface QueuedFrame {
  audio: string;
}

interface PendingSession {
  resolve: (text: string) => void;
  reject: (err: Error) => void;
  handlers: AsrHandlers;
  appId: string;
  socket: SocketLike | null;
  opened: boolean;
  /** stop() 已调用（等待收尾）。 */
  stopped: boolean;
  /** 会话已拆除（teardown 后一切回调不再生效）。 */
  dead: boolean;
  /** 已收到服务端 header.status==2 的最终响应。 */
  gotFinal: boolean;
  finalText: string;
  /** 已构造的帧数（下一片的 seq；0 表示从未发过帧）。 */
  seq: number;
  /** 收到的音频帧数（入队即可用，不依赖连接）。 */
  audioFrames: number;
  /** 末帧已发出（此后丢弃录音尾帧）。 */
  finalFrameSent: boolean;
  queue: QueuedFrame[];
  results: WpgsBuffer;
  lastPartial: string;
  openTimer: ReturnType<typeof setTimeout> | null;
  finalTimer: ReturnType<typeof setTimeout> | null;
  pumpTimer: ReturnType<typeof setInterval> | null;
  openWaiters: (() => void)[];
  drainWaiters: (() => void)[];
}

export interface XfyunAsrDeps {
  /** 签名凭据 provider（app-context 用 createWssUrlProvider 构建）。 */
  credential: WssCredentialProvider;
  /** PCM 录音器（默认真实；单测注入假 recorder）。 */
  recorder?: RecorderLike;
  /** 建立 WebSocket（默认 wx.connectSocket；单测注入假 socket）。 */
  connect?: (url: string) => SocketLike;
}

function wxConnect(url: string): SocketLike {
  return wx.connectSocket({ url }) as unknown as SocketLike;
}

/** 构造一帧（发送时调用：seq/首帧状态在发送点确定，appId 此时必然就绪）。 */
function buildFrame(
  session: PendingSession,
  opts: { audio: string; final?: boolean },
): string {
  const status = opts.final ? 2 : session.seq === 0 ? 0 : 1;
  const frame: Record<string, unknown> = {
    header: { app_id: session.appId, status },
    payload: {
      audio: {
        encoding: 'raw',
        sample_rate: SAMPLE_RATE,
        channels: 1,
        bit_depth: 16,
        seq: session.seq,
        status,
        audio: opts.audio,
      },
    },
  };
  if (session.seq === 0) {
    frame.parameter = IAT_PARAMETER; // parameter.iat 必须随首帧
  }
  session.seq += 1;
  return JSON.stringify(frame);
}

export class XfyunAsrEngine implements AsrEngine {
  private readonly credential: WssCredentialProvider;
  private readonly recorder: RecorderLike;
  private readonly connect: (url: string) => SocketLike;
  private pending: PendingSession | null = null;

  constructor(deps: XfyunAsrDeps) {
    this.credential = deps.credential;
    this.recorder = deps.recorder ?? new PcmRecorder();
    this.connect = deps.connect ?? wxConnect;
  }

  start(handlers: AsrHandlers): Promise<string> {
    if (this.pending !== null) {
      throw new Error('ASR 会话进行中'); // 契约：重复 start 同步抛错
    }
    log.info('识别会话开始');
    const session: PendingSession = {
      resolve: () => undefined,
      reject: () => undefined,
      handlers,
      appId: '',
      socket: null,
      opened: false,
      stopped: false,
      dead: false,
      gotFinal: false,
      finalText: '',
      seq: 0,
      audioFrames: 0,
      finalFrameSent: false,
      queue: [],
      results: {},
      lastPartial: '',
      openTimer: null,
      finalTimer: null,
      pumpTimer: null,
      openWaiters: [],
      drainWaiters: [],
    };
    const promise = new Promise<string>((resolve, reject) => {
      session.resolve = resolve;
      session.reject = reject;
    });
    this.pending = session;
    void this.beginSession(session);
    return promise;
  }

  stop(): void {
    const session = this.pending;
    if (session === null || session.stopped) {
      return;
    }
    session.stopped = true;
    void this.finalize(session);
  }

  cancel(): void {
    const session = this.pending;
    if (session === null) {
      return;
    }
    this.pending = null; // 先摘除：其后的回调不再 settle 本会话
    this.recorder.cancel();
    this.teardownSession(session);
    session.reject(new AsrCancelledError());
  }

  // ------------------------------------------------------------------
  // 会话内部
  // ------------------------------------------------------------------

  private async beginSession(session: PendingSession): Promise<void> {
    try {
      // 先开录：音频进本地队列，取凭据/握手期间的开口不丢
      this.recorder.start({ onFrame: (samples) => this.feed(session, samples) });
      const cred = await this.credential();
      if (this.pending !== session) {
        return; // 会话已被 cancel
      }
      log.info('凭据就绪 → 连接', cred.url.split('?')[0]); // 不打印签名参数
      session.appId = cred.appId;
      const socket = this.connect(cred.url);
      session.socket = socket;
      socket.onOpen(() => this.handleOpen(session));
      socket.onMessage((res) => this.handleMessage(session, res.data));
      socket.onClose(() => this.handleClose(session));
      socket.onError((err) =>
        this.failSession(session, new Error(err.errMsg ?? '讯飞连接失败')),
      );
      session.openTimer = setTimeout(() => {
        this.failSession(session, new Error('讯飞连接超时'));
      }, OPEN_TIMEOUT_MS);
      if (session.stopped && session.audioFrames === 0) {
        // 快速点按（无任何音频）：无需连服务端，空文本收尾
        this.pending = null;
        this.teardownSession(session);
        session.resolve('');
      }
    } catch (err) {
      this.failSession(session, err);
    }
  }

  private handleOpen(session: PendingSession): void {
    if (this.pending !== session || session.opened) {
      return;
    }
    session.opened = true;
    log.info('讯飞连接已建立');
    if (session.openTimer !== null) {
      clearTimeout(session.openTimer);
      session.openTimer = null;
    }
    this.pump(session); // 立即先发一帧，不等 40ms
    session.pumpTimer = setInterval(() => this.pump(session), SEND_INTERVAL_MS);
    for (const waiter of session.openWaiters.splice(0)) waiter();
    // stop() 若已在等待（waitOpened），由 waiter 唤醒后自行收尾，此处不重复 finalize
  }

  private pump(session: PendingSession): void {
    if (this.pending !== session || !session.opened || session.socket === null) {
      return;
    }
    if (session.gotFinal) {
      // 服务端已给最终结果：不再发送，队列视为处理完毕
      if (session.pumpTimer !== null) {
        clearInterval(session.pumpTimer);
        session.pumpTimer = null;
      }
      for (const waiter of session.drainWaiters.splice(0)) waiter();
      return;
    }
    if (session.queue.length === 0) {
      for (const waiter of session.drainWaiters.splice(0)) waiter();
      return;
    }
    const queued = session.queue.shift() as QueuedFrame;
    const frame = buildFrame(session, { audio: queued.audio });
    if (session.seq === 1) {
      log.info('首帧已发送（含 parameter.iat）'); // buildFrame 后 seq 已递增到 1
    }
    session.socket.send({
      data: frame,
      fail: (err) => this.failSession(session, new Error(err.errMsg ?? '音频帧发送失败')),
    });
    if (session.queue.length === 0) {
      for (const waiter of session.drainWaiters.splice(0)) waiter();
    }
  }

  private feed(session: PendingSession, samples: Float32Array): void {
    if (this.pending !== session || session.finalFrameSent || samples.length === 0) {
      return;
    }
    session.audioFrames += 1;
    session.queue.push({ audio: bytesToBase64(floatToInt16Bytes(samples)) });
  }

  private handleMessage(session: PendingSession, data: unknown): void {
    if (this.pending !== session) {
      return;
    }
    if (typeof data !== 'string') {
      this.failSession(session, new Error('讯飞响应格式异常'));
      return;
    }
    let msg: {
      header?: { code?: number; message?: string; status?: number };
      payload?: { result?: { text?: string } };
    };
    try {
      msg = JSON.parse(data);
    } catch {
      this.failSession(session, new Error('讯飞响应解析失败'));
      return;
    }
    const header = msg.header;
    if (header === undefined || typeof header.code !== 'number') {
      this.failSession(session, new Error('讯飞响应缺少协议头'));
      return;
    }
    if (header.code !== 0) {
      this.failSession(
        session,
        new Error(header.message ?? `讯飞服务错误（code=${header.code}）`),
      );
      return;
    }
    const resultText = msg.payload?.result?.text;
    if (typeof resultText === 'string' && resultText.length > 0) {
      let parsed: ResultMessage;
      try {
        parsed = JSON.parse(base64ToUtf8(resultText)) as ResultMessage;
      } catch {
        this.failSession(session, new Error('识别结果解码失败'));
        return;
      }
      const text = applyWpgs(session.results, parsed);
      if (text.length > 0 && text !== session.lastPartial) {
        session.lastPartial = text;
        session.handlers.onPartial?.(text);
      }
    }
    if (header.status === 2) {
      session.gotFinal = true;
      session.finalText = wpgsText(session.results);
      this.maybeSettle(session);
    }
  }

  private handleClose(session: PendingSession): void {
    if (this.pending !== session) {
      return;
    }
    if (session.gotFinal) {
      this.maybeSettle(session); // 未 stop 时留待 stop 收尾
      return;
    }
    this.failSession(session, new Error('讯飞连接已关闭'));
  }

  private async finalize(session: PendingSession): Promise<void> {
    session.stopped = true;
    try {
      await this.recorder.stop(); // 等 onStop：尾帧已 onFrame 入队
    } catch (err) {
      this.failSession(session, err);
      return;
    }
    if (this.pending !== session) {
      return; // 收尾期间被 cancel
    }
    if (session.gotFinal) {
      this.maybeSettle(session); // 服务端已先给出最终结果
      return;
    }
    if (session.audioFrames === 0) {
      // 无任何音频帧（快速点按）：空文本收尾，不打扰服务端、不等连接
      this.pending = null;
      this.teardownSession(session);
      session.resolve('');
      return;
    }
    await this.waitOpened(session);
    if (this.pending !== session) {
      return;
    }
    if (session.gotFinal) {
      this.maybeSettle(session); // 等连接期间最终结果到达
      return;
    }
    await this.waitForDrain(session);
    if (this.pending !== session) {
      return;
    }
    if (session.gotFinal) {
      this.maybeSettle(session); // 排队期间最终结果到达
      return;
    }
    const socket = session.socket;
    if (socket === null) {
      this.failSession(session, new Error('连接不可用'));
      return;
    }
    session.finalFrameSent = true;
    socket.send({
      data: buildFrame(session, { audio: '', final: true }),
      fail: (err) => this.failSession(session, new Error(err.errMsg ?? '末帧发送失败')),
    });
    session.finalTimer = setTimeout(() => {
      this.failSession(session, new Error('识别结果响应超时'));
    }, FINAL_TIMEOUT_MS);
    // 最终结果由 handleMessage(status==2) → maybeSettle 收下
  }

  private maybeSettle(session: PendingSession): void {
    if (this.pending !== session || !session.gotFinal || !session.stopped) {
      return;
    }
    log.info('识别完成:', session.finalText || '(空文本)');
    this.pending = null;
    this.teardownSession(session);
    session.resolve(session.finalText);
  }

  private failSession(session: PendingSession, err: unknown): void {
    if (this.pending !== session) {
      return;
    }
    log.error('识别会话失败:', err instanceof Error ? err.message : err);
    this.pending = null;
    this.recorder.cancel(); // 录音中出错必须释放麦克风
    this.teardownSession(session);
    session.reject(err instanceof Error ? err : new Error('语音识别失败'));
  }

  private teardownSession(session: PendingSession): void {
    session.dead = true;
    if (session.openTimer !== null) {
      clearTimeout(session.openTimer);
      session.openTimer = null;
    }
    if (session.finalTimer !== null) {
      clearTimeout(session.finalTimer);
      session.finalTimer = null;
    }
    if (session.pumpTimer !== null) {
      clearInterval(session.pumpTimer);
      session.pumpTimer = null;
    }
    for (const waiter of session.openWaiters.splice(0)) waiter();
    for (const waiter of session.drainWaiters.splice(0)) waiter();
    if (session.socket !== null) {
      try {
        session.socket.close({ code: 1000 });
      } catch {
        // 关闭失败可忽略：会话已终止
      }
      session.socket = null;
    }
  }

  private waitOpened(session: PendingSession): Promise<void> {
    if (session.opened || session.dead) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      session.openWaiters.push(resolve);
    });
  }

  private waitForDrain(session: PendingSession): Promise<void> {
    if (session.queue.length === 0 || session.dead || session.gotFinal) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      session.drainWaiters.push(resolve);
    });
  }
}
