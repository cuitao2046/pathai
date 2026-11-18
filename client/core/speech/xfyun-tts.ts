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
import { createLogger } from '../../utils/logger';
import { sha256 } from '../../utils/sha256';
import type { WssCredentialProvider } from '../../utils/wss-url';

const log = createLogger('xfyun-tts');

/** 默认发音人（讯飞文档默认值；x6 系列需控制台另行开通权限）。 */
const DEFAULT_VCN = 'x5_lingxiaoxuan_flow';
/** 默认语速（讯飞 speed 0~100，50 为默认值）。 */
const DEFAULT_SPEED = 50;
/** 预生成缓存上限：按最近使用淘汰，避免本地文件无限增长。 */
const MAX_PREPARED_FILES = 32;
/** 合成完成等待兜底超时（正常毫秒级返回，防服务端半开连接悬挂）。 */
const SYNTH_TIMEOUT_MS = 30_000;
/** wss 握手超时（对齐 ASR 引擎）。 */
const OPEN_TIMEOUT_MS = 8000;

/** 一次性合成请求参数（docs：super smart-tts）。 */
function buildRequestFrame(appId: string, text: string, vcn: string, speed: number): string {
  return JSON.stringify({
    header: { app_id: appId, status: 2 },
    parameter: {
      tts: {
        vcn,
        speed: clampSpeed(speed),
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

/** 语速限制在讯飞合法区间 0~100；非法值回落默认，防止请求被拒绝。 */
function clampSpeed(value: number): number {
  if (!Number.isFinite(value)) {
    return DEFAULT_SPEED;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
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
  /** 可选：缓存淘汰时删除旧文件（单测假件可不实现）。 */
  unlink?(opt: {
    filePath: string;
    success?: () => void;
    fail?: (err: { errMsg?: string }) => void;
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
  /**
   * 语速 provider（讯飞 speed 0~100，默认 50）。
   * 每句合成前读取一次，设置页改档后下一句即生效。
   */
  speed?: () => number;
}

function wxConnect(url: string): SocketLike {
  return wx.connectSocket({ url }) as unknown as SocketLike;
}

export class XfyunTts implements TtsEngine {
  private readonly credential: WssCredentialProvider;
  private readonly connect: (url: string) => SocketLike;
  private readonly fs: () => FileWriteLike;
  private readonly audioFactory: () => AudioPlayerLike;
  /** 测试注入的固定文件路径；生产环境按文本/语速哈希分文件缓存。 */
  private readonly fixedFilePath: string | null;
  private readonly vcn: string;
  private readonly speed: () => number;
  private session: SynthSession | null = null;
  /** 预生成缓存：cacheKey → 本地音频路径（按最近使用淘汰）。 */
  private readonly prepared = new Map<string, string>();
  /** 正在预生成的文本：cacheKey → 结果路径（speak 命中同一文本时等待它，避免重复合成）。 */
  private readonly preparing = new Map<string, Promise<string | null>>();

  constructor(deps: XfyunTtsDeps) {
    this.credential = deps.credential;
    this.connect = deps.connect ?? wxConnect;
    const fs = deps.fs;
    this.fs = fs !== undefined ? () => fs : () => wx.getFileSystemManager() as unknown as FileWriteLike;
    this.audioFactory = deps.audioFactory ?? (() => wx.createInnerAudioContext() as unknown as AudioPlayerLike);
    this.fixedFilePath = deps.filePath ?? null;
    this.vcn = deps.vcn ?? DEFAULT_VCN;
    this.speed = deps.speed ?? (() => DEFAULT_SPEED);
  }

  async speak(text: string): Promise<void> {
    if (text.trim().length === 0) {
      return;
    }
    const key = this.cacheKey(text);
    const session: SynthSession = {
      aborted: false,
      socket: null,
      player: null,
      abortSynth: null,
    };
    this.session = session;
    try {
      // 预生成缓存命中：直接播放本地文件，不再建立合成连接
      const preparedPath = this.prepared.get(key);
      if (preparedPath !== undefined) {
        this.prepared.delete(key);
        this.prepared.set(key, preparedPath); // LRU：命中移到队尾
        await this.play(session, preparedPath);
        return;
      }
      // 同一文本正在后台预生成：等它完成，避免重复合成
      const preparing = this.preparing.get(key);
      if (preparing !== undefined) {
        const path = await preparing;
        if (session.aborted) {
          return;
        }
        if (path !== null) {
          await this.play(session, path);
          return;
        }
      }
      const cred = await this.credential();
      if (session.aborted) {
        return;
      }
      const bytes = await this.synthesize(session, cred.url, cred.appId, text);
      if (session.aborted || bytes === null) {
        return;
      }
      const path = await this.writeFileTo(bytes, this.filePathFor(key));
      if (session.aborted) {
        return;
      }
      this.rememberPrepared(key, path);
      await this.play(session, path);
    } catch (err) {
      if (session.aborted) {
        return; // stop() 打断导致的失败静默结束，交还队列
      }
      log.error('合成/播报失败:', err instanceof Error ? err.message : err);
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

  /**
   * 后台预生成：把文本合成到按「文本 + 发音人 + 语速」命名的本地文件。
   * 失败静默（不影响主链路）；同一文本已在缓存/生成中时直接返回。
   */
  async prepare(text: string): Promise<void> {
    if (text.trim().length === 0) {
      return;
    }
    const key = this.cacheKey(text);
    if (this.prepared.has(key) || this.preparing.has(key)) {
      return;
    }
    const task = this.synthesizeToCache(text, key).catch((err) => {
      log.warn('预生成失败（忽略）', err instanceof Error ? err.message : err);
      return null;
    });
    this.preparing.set(key, task);
    try {
      await task;
    } finally {
      this.preparing.delete(key);
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
      let opened = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      let openTimer: ReturnType<typeof setTimeout> | null = null;
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
        if (openTimer !== null) {
          clearTimeout(openTimer);
          openTimer = null;
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
      // 微信 SocketTask 在 readyState≠OPEN 时 send 直接 fail（真机踩坑）：
      // 必须等 onOpen 后再发请求帧；握手超时兜底。
      openTimer = setTimeout(() => {
        finish(null, new Error('讯飞合成连接超时'));
      }, OPEN_TIMEOUT_MS);

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
      socket.onOpen(() => {
        if (settled) {
          return;
        }
        opened = true;
        if (openTimer !== null) {
          clearTimeout(openTimer);
          openTimer = null;
        }
        socket.send({
          data: buildRequestFrame(appId, text, this.vcn, this.speed()),
          fail: (err) => finish(null, new Error(err.errMsg ?? '语音合成请求发送失败')),
        });
      });
      socket.onClose(() => {
        if (!opened) {
          finish(null, new Error('讯飞合成连接失败'));
          return;
        }
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
    });
  }

  /** 预生成实现：合成后写入按 key 命名的文件，供 speak 直接播放。 */
  private async synthesizeToCache(text: string, key: string): Promise<string | null> {
    const session: SynthSession = {
      aborted: false,
      socket: null,
      player: null,
      abortSynth: null,
    };
    try {
      const cred = await this.credential();
      const bytes = await this.synthesize(session, cred.url, cred.appId, text);
      if (bytes === null) {
        return null;
      }
      const path = await this.writeFileTo(bytes, this.filePathFor(key));
      this.rememberPrepared(key, path);
      return path;
    } finally {
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

  /** 缓存键：文本 + 发音人 + 语速；任一变化都重新合成。 */
  private cacheKey(text: string): string {
    return sha256(`${this.vcn}|${clampSpeed(this.speed())}|${text}`);
  }

  private filePathFor(key: string): string {
    if (this.fixedFilePath !== null) {
      return this.fixedFilePath;
    }
    return `${wx.env.USER_DATA_PATH}/xfyun-tts-${key.slice(0, 16)}.mp3`;
  }

  /** 写入缓存并做 LRU 淘汰（旧文件删除失败静默）。 */
  private rememberPrepared(key: string, path: string): void {
    this.prepared.delete(key);
    this.prepared.set(key, path);
    while (this.prepared.size > MAX_PREPARED_FILES) {
      const oldestKey = this.prepared.keys().next().value as string | undefined;
      if (oldestKey === undefined) {
        break;
      }
      const oldPath = this.prepared.get(oldestKey);
      this.prepared.delete(oldestKey);
      if (oldPath !== undefined && oldPath !== path) {
        this.removeFile(oldPath);
      }
    }
  }

  private removeFile(path: string): void {
    const fs = this.fs();
    if (fs.unlink === undefined) {
      return;
    }
    try {
      fs.unlink({ filePath: path, fail: () => undefined });
    } catch {
      // 删除失败不影响播放
    }
  }

  private writeFileTo(bytes: Uint8Array, filePath: string): Promise<string> {
    return new Promise<string>((resolve, reject) => {
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
          log.info('播报结束');
          resolve();
        } else {
          log.error('播放失败:', err.message);
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
