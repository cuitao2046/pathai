/**
 * 讯飞超拟人语音合成 TTS 引擎（TtsEngine 契约实现，live 数据源）。
 *
 * 链路（wss 直连讯飞，签名 URL 由后端签发、凭据 provider 缓存 ≤4min）：
 *  speak → 取凭据 → connect → 发单帧（header.status=2 一次性合成，lame=mp3/24k）→
 *          收 payload.audio.audio 分片（base64）至 header.status==2（或连接关闭）→
 *          合并写本地临时文件（固定名，覆盖旧文件）→ InnerAudioContext 播放 →
 *          onEnded 后 resolve；
 *  stop  → 播放中：audio.stop()（onStop 视为播完，交还队列控制权）；
 *          合成/写文件中：置 aborted + 关 socket，speak 静默 resolve；
 *  失败一律 reject → SpeechQueue FE-04 降级（震动+屏幕文本）已有。
 *
 * 依赖注入（单测用假件，不碰 wx/网络）：credential、connect、fs、audioFactory。
 */
import type { TtsEngine } from './tts-engine';
import { base64ToBytes, utf8ToBase64 } from '../../utils/base64';
import type { WssCredentialProvider } from '../../utils/wss-url';

/** 默认发音人（讯飞文档默认值；x6 系列需控制台另行开通权限）。 */
const DEFAULT_VCN = 'x5_lingxiaoxuan_flow';
/** 合成完成等待兜底超时（正常毫秒级返回，防服务端半开连接悬挂）。 */
const SYNTH_TIMEOUT_MS = 30_000;
const TMP_FILE_NAME = 'xfyun-tts.mp3';

/** 一次性合成请求参数（docs：super smart-tts）。 */
function buildRequestFrame(appId: string, text: string, vcn: string): string {
  return JSON.stringify({
    header: { app_id: appId, status: 2 },
    parameter: {
      tts: {
        vcn,
        speed: 50,
        volume: 50,
        pitch: 50,
        bgs: 0,
        reg: 0,
        rdn: 0,
        rhy: 0,
        audio: {
          encoding: 'lame',
          sample_rate: 24000,
          channels: 1,
          bit_depth: 16,
          frame_size: 0,
        },
      },
    },
    payload: {
      // 实测：网关强校验 text 为 base64(UTF-8)（明文会被 10163 拒绝，文档未标明）
      text: {
        encoding: 'utf8',
        compress: 'raw',
        format: 'plain',
        status: 2,
        seq: 0,
        text: utf8ToBase64(text),
      },
    },
  });
}

function concatBytes(chunks: Uint8Array[]): Uint8Array {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

/** InnerAudioContext 的最小面（便于单测注入假播放器）。 */
export interface AudioPlayerLike {
  src: string;
  onEnded(cb: () => void): void;
  onStop(cb: () => void): void;
  onError(cb: (err: { errMsg?: string }) => void): void;
  play(): void;
  stop(): void;
  destroy(): void;
}

/** FileSystemManager.writeFile 的最小面。 */
export interface FileWriteLike {
  writeFile(opt: {
    filePath: string;
    data: ArrayBuffer;
    success: () => void;
    fail: (err: { errMsg?: string }) => void;
  }): void;
}

/** WebSocket 连接面（与 xfyun-asr 的 SocketLike 结构一致）。 */
export interface SocketLike {
  send(opt: { data: string; fail?: (err: { errMsg?: string }) => void }): void;
  close(opt?: { code?: number; reason?: string }): void;
  onOpen(cb: () => void): void;
  onMessage(cb: (res: { data: unknown }) => void): void;
  onClose(cb: () => void): void;
  onError(cb: (err: { errMsg?: string }) => void): void;
}

interface SynthSession {
  aborted: boolean;
  socket: SocketLike | null;
  player: AudioPlayerLike | null;
  /** 合成阶段中止钩子（stop 在合成/传输期间被调用时立即结束该阶段）。 */
  abortSynth: (() => void) | null;
}

export interface XfyunTtsDeps {
  /** 签名凭据 provider（与 ASR 共用 createWssUrlProvider，路径用 /tts-url）。 */
  credential: WssCredentialProvider;
  /** 建立 WebSocket（默认 wx.connectSocket；单测注入假 socket）。 */
  connect?: (url: string) => SocketLike;
  /** 临时文件写入（默认 wx.getFileSystemManager()）。 */
  fs?: FileWriteLike;
  /** 播放器工厂（默认 wx.createInnerAudioContext()）。 */
  audioFactory?: () => AudioPlayerLike;
  /** 合成临时文件路径（默认 wx.env.USER_DATA_PATH/xfyun-tts.mp3）。 */
  filePath?: string;
  /** 发音人（默认讯飞 x5_lingxiaoxuan_flow）。 */
  vcn?: string;
}

function wxConnect(url: string): SocketLike {
  return wx.connectSocket({ url }) as unknown as SocketLike;
}

export class XfyunTts implements TtsEngine {
  private readonly credential: WssCredentialProvider;
  private readonly connect: (url: string) => SocketLike;
  private readonly fs: () => FileWriteLike;
  private readonly audioFactory: () => AudioPlayerLike;
  private readonly filePath: () => string;
  private readonly vcn: string;
  private session: SynthSession | null = null;

  constructor(deps: XfyunTtsDeps) {
    this.credential = deps.credential;
    this.connect = deps.connect ?? wxConnect;
    const fs = deps.fs;
    this.fs = fs !== undefined ? () => fs : () => wx.getFileSystemManager() as unknown as FileWriteLike;
    this.audioFactory = deps.audioFactory ?? (() => wx.createInnerAudioContext() as unknown as AudioPlayerLike);
    const filePath = deps.filePath;
    this.filePath =
      filePath !== undefined
        ? () => filePath
        : () => `${wx.env.USER_DATA_PATH}/${TMP_FILE_NAME}`;
    this.vcn = deps.vcn ?? DEFAULT_VCN;
  }

  async speak(text: string): Promise<void> {
    if (text.trim().length === 0) {
      return;
    }
    const session: SynthSession = {
      aborted: false,
      socket: null,
      player: null,
      abortSynth: null,
    };
    this.session = session;
    try {
      const cred = await this.credential();
      if (session.aborted) {
        return;
      }
      const bytes = await this.synthesize(session, cred.url, cred.appId, text);
      if (session.aborted || bytes === null) {
        return;
      }
      const path = await this.writeTempFile(bytes);
      if (session.aborted) {
        return;
      }
      await this.play(session, path);
    } catch (err) {
      if (session.aborted) {
        return; // stop() 打断导致的失败静默结束，交还队列
      }
      throw err instanceof Error ? err : new Error('语音合成失败');
    } finally {
      if (this.session === session) {
        this.session = null;
      }
      if (session.socket !== null) {
        try {
          session.socket.close({ code: 1000 });
        } catch {
          // 已关闭则忽略
        }
        session.socket = null;
      }
    }
  }

  stop(): void {
    const session = this.session;
    if (session === null) {
      return;
    }
    if (session.player !== null) {
      session.player.stop(); // onStop → play settle（视为播完）
      return;
    }
    session.aborted = true;
    session.abortSynth?.();
    if (session.socket !== null) {
      try {
        session.socket.close({ code: 1000 });
      } catch {
        // 已关闭则忽略
      }
      session.socket = null;
    }
  }

  // ------------------------------------------------------------------

  private synthesize(
    session: SynthSession,
    url: string,
    appId: string,
    text: string,
  ): Promise<Uint8Array | null> {
    return new Promise<Uint8Array | null>((resolve, reject) => {
      const chunks: Uint8Array[] = [];
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      const finish = (bytes: Uint8Array | null, err?: Error): void => {
        if (settled) {
          return;
        }
        settled = true;
        session.abortSynth = null;
        if (timer !== null) {
          clearTimeout(timer);
          timer = null;
        }
        const socket = session.socket;
        session.socket = null;
        if (socket !== null) {
          try {
            socket.close({ code: 1000 });
          } catch {
            // 已关闭则忽略
          }
        }
        if (err !== undefined) {
          reject(err);
        } else {
          resolve(bytes);
        }
      };
      session.abortSynth = () => finish(null);

      timer = setTimeout(() => {
        finish(null, new Error('语音合成超时'));
      }, SYNTH_TIMEOUT_MS);

      const socket = this.connect(url);
      session.socket = socket;

      socket.onMessage((res) => {
        if (settled) {
          return;
        }
        if (typeof res.data !== 'string') {
          finish(null, new Error('语音合成响应格式异常'));
          return;
        }
        let msg: {
          header?: { code?: number; message?: string; status?: number };
          payload?: { audio?: { audio?: string; status?: number } };
        };
        try {
          msg = JSON.parse(res.data);
        } catch {
          finish(null, new Error('语音合成响应解析失败'));
          return;
        }
        const header = msg.header;
        if (header === undefined || typeof header.code !== 'number') {
          finish(null, new Error('语音合成响应缺少协议头'));
          return;
        }
        if (header.code !== 0) {
          finish(null, new Error(header.message ?? `讯飞服务错误（code=${header.code}）`));
          return;
        }
        const audio = msg.payload?.audio?.audio;
        if (typeof audio === 'string' && audio.length > 0) {
          chunks.push(base64ToBytes(audio));
        }
        const done = header.status === 2 || msg.payload?.audio?.status === 2;
        if (done) {
          if (chunks.length === 0) {
            finish(null, new Error('语音合成未返回音频数据'));
          } else {
            finish(concatBytes(chunks));
          }
        }
      });
      socket.onClose(() => {
        // 服务端发完即关（未携带 status==2 收尾帧）：有分片即视为完成
        if (chunks.length > 0) {
          finish(concatBytes(chunks));
        } else {
          finish(null, new Error('语音合成连接已关闭'));
        }
      });
      socket.onError((err) => {
        finish(null, new Error(err.errMsg ?? '语音合成连接失败'));
      });
      socket.send({
        data: buildRequestFrame(appId, text, this.vcn),
        fail: (err) => finish(null, new Error(err.errMsg ?? '语音合成请求发送失败')),
      });
    });
  }

  private writeTempFile(bytes: Uint8Array): Promise<string> {
    return new Promise<string>((resolve, reject) => {
      const filePath = this.filePath();
      this.fs().writeFile({
        filePath,
        data: toArrayBuffer(bytes),
        success: () => resolve(filePath),
        fail: (err) => reject(new Error(err.errMsg ?? '合成音频写入失败')),
      });
    });
  }

  private play(session: SynthSession, src: string): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const player = this.audioFactory();
      session.player = player;
      let settled = false;
      const settle = (err: Error | null): void => {
        if (settled) {
          return;
        }
        settled = true;
        player.destroy();
        if (session.player === player) {
          session.player = null;
        }
        if (err === null) {
          resolve();
        } else {
          reject(err);
        }
      };
      player.onEnded(() => settle(null));
      player.onStop(() => settle(null)); // stop() 视为播完，交还队列控制权
      player.onError((err) => settle(new Error(err.errMsg ?? 'TTS 音频播放失败')));
      player.src = src;
      player.play();
    });
  }
}
