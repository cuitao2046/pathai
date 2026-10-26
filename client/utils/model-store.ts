/**
 * ASR 模型存储（Sherpa-Onnx 端侧识别，首启一次联网、识别期零联网）。
 *
 * - manifest（GET /static/asr/manifest，免鉴权）取服务端版本与各文件 sha256；
 * - wx.downloadFile 直接落用户目录，逐文件 sha256Async（分片不让 UI 卡）校验
 *   通过后才写就绪标记——半截/损坏文件因无就绪标记永不被信任，重下覆盖；
 * - readModelSync() 同步拼接三文件：胶水 getPreloadedPackage 是同步回调，
 *   模型经 .data 注入机制进 MEMFS（胶水未导出 FS）。
 */
import { API_BASE_URL, ASR_MODEL_VERSION } from '../config/env';
import { createLogger } from './logger';
import { getString, setString } from './kv';
import { sha256BytesAsync, sha256Hex } from './sha256';

const log = createLogger('asr-model');

const READY_KEY = 'cfg:asrModelReady';
/** 与后端 routers/assets.py ALLOWED_FILES 一致。 */
const MODEL_FILES = ['model.int8.onnx', 'bbpe.model', 'tokens.txt'] as const;
const MANIFEST_URL = `${API_BASE_URL}/static/asr/manifest`;

interface ModelFileEntry {
  sha256: string;
  bytes: number;
}

interface ModelManifest {
  version: string;
  files: Record<string, ModelFileEntry>;
}

function filePath(name: string): string {
  return `${wx.env.USER_DATA_PATH}/asr-model/${name}`;
}

function exists(path: string): boolean {
  try {
    wx.getFileSystemManager().accessSync(path);
    return true;
  } catch {
    return false;
  }
}

function fetchManifest(): Promise<ModelManifest> {
  return new Promise((resolve, reject) => {
    wx.request({
      url: MANIFEST_URL,
      method: 'GET',
      success: (res) => {
        if (res.statusCode === 200) {
          resolve(res.data as ModelManifest);
        } else {
          reject(new Error(`模型清单请求失败：HTTP ${res.statusCode}`));
        }
      },
      fail: (err) => reject(new Error(err.errMsg ?? '模型清单请求失败')),
    });
  });
}

async function downloadAndVerify(name: string, expect: ModelFileEntry): Promise<void> {
  const target = filePath(name);
  await new Promise<void>((resolve, reject) => {
    const task = wx.downloadFile({
      url: `${API_BASE_URL}/static/asr/${name}`,
      filePath: target,
      success: (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else {
          reject(new Error(`模型下载失败：${name} HTTP ${res.statusCode}`));
        }
      },
      fail: (err) => reject(new Error(err.errMsg ?? `模型下载失败：${name}`)),
    });
    task.onProgressUpdate((p) => {
      log.info('下载中', name, `${p.progress}%`);
    });
  });

  const buf = wx.getFileSystemManager().readFileSync(target);
  const bytes = new Uint8Array(buf as ArrayBuffer);
  const digest = sha256Hex(await sha256BytesAsync(bytes));
  if (digest !== expect.sha256) {
    try {
      wx.getFileSystemManager().unlinkSync(target);
    } catch {
      // 删除失败不阻断：无就绪标记保证该文件不会被信任
    }
    throw new Error(`模型校验失败：${name}（sha256 不匹配）`);
  }
  log.info('校验通过', name, `${expect.bytes}B`);
}

/** 确保模型就绪（幂等）；未就绪时抛错交上层提示「模型未就绪」。 */
export async function ensureAsrModel(): Promise<void> {
  if (getString(READY_KEY) === ASR_MODEL_VERSION && MODEL_FILES.every((f) => exists(filePath(f)))) {
    return;
  }
  const manifest = await fetchManifest();
  if (manifest.version !== ASR_MODEL_VERSION) {
    throw new Error(
      `ASR 模型版本不一致：服务端 ${manifest.version} / 客户端 ${ASR_MODEL_VERSION}`,
    );
  }
  for (const name of MODEL_FILES) {
    const entry = manifest.files[name];
    if (entry === undefined) {
      throw new Error(`模型清单缺少文件：${name}`);
    }
    await downloadAndVerify(name, entry);
  }
  setString(READY_KEY, ASR_MODEL_VERSION);
  log.info('ASR 模型就绪', ASR_MODEL_VERSION);
}

/** 模型是否已就绪（不触发下载）。 */
export function isAsrModelReady(): boolean {
  return getString(READY_KEY) === ASR_MODEL_VERSION && MODEL_FILES.every((f) => exists(filePath(f)));
}

/**
 * 同步读取并拼接三模型文件（getPreloadedPackage 同步约定）。
 * 未就绪抛错；一次性 26MB 读入仅发生在识别器首次创建时。
 */
export function readModelSync(): ArrayBuffer {
  if (!isAsrModelReady()) {
    throw new Error('ASR 模型未就绪，请稍后重试');
  }
  const fs = wx.getFileSystemManager();
  const parts = MODEL_FILES.map(
    (name) => new Uint8Array(fs.readFileSync(filePath(name)) as ArrayBuffer),
  );
  const total = parts.reduce((sum, p) => sum + p.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.byteLength;
  }
  return out.buffer;
}
