/**
 * Sherpa-Onnx WASM 运行时（小程序侧桥接，Step1 spike 落地）。
 *
 * 链路（Node 侧已由 scripts/asr-node-smoke.js 验证同一套 patch 产物）：
 *  1) ensureAsrModel()：模型就绪（首启下载，此后零联网）；
 *  2) wx.loadSubpackage('asr')：加载 wasm+胶水分包；
 *  3) 预置 globalThis.__SHERPA_MODULE__（patch 后胶水的第一行即读它）：
 *     - instantiateWasm：小程序无 fetch/无 process → 用 WXWebAssembly.instantiate
 *       加载包内 wasm，回传 instance（胶水 receiveInstance 忽略 module 参数）；
 *     - getPreloadedPackage：同步返回模型字节 → preRun 阶段写入 MEMFS
 *       （胶水未导出 FS，.data 注入是唯一写入通道）；
 *  4) require 胶水/封装（分包内 CJS）→ createOnlineRecognizer(zipformer2Ctc)。
 *
 * 识别器为模块级单例：模型与 wasm 常驻内存（约 60MB 峰值），PTT 反复 start/stop
 * 复用同一实例（流按会话 create/reset）。
 */
import { createLogger } from '../../utils/logger';
import { ensureAsrModel, readModelSync } from '../../utils/model-store';

const log = createLogger('sherpa');

/** 小程序运行时模块加载（TS 无 Node 类型，同 fixtures/real-map 先例）。 */
declare const require: (id: string) => unknown;

/** typings 5.2.3 未收录 wx.loadSubpackage（基础 API），本地补充。 */
interface LoadSubpackageOption {
  name: string;
  success?: (res: { resources?: unknown }) => void;
  fail?: (err: { errMsg?: string }) => void;
}
interface WxWithLoadSubpackage {
  loadSubpackage(option: LoadSubpackageOption): unknown;
}

const SUBPACKAGE_ROOT = 'asr';
const SUBPACKAGE_DIR = '/subpackages/asr';
const WASM_PATH = `${SUBPACKAGE_DIR}/sherpa-onnx-wasm-main-asr.wasm`;
const GLUE_PATH = `${SUBPACKAGE_DIR}/sherpa-glue.cjs.js`;
const WRAPPERS_PATH = `${SUBPACKAGE_DIR}/sherpa-wrappers.cjs.js`;

/** 识别会话流（对应封装 OnlineStream 使用面）。 */
export interface OnlineStreamLike {
  acceptWaveform(sampleRate: number, samples: Float32Array): void;
}

/** 在线识别器（对应封装 OnlineRecognizer 使用面）。 */
export interface OnlineRecognizerLike {
  createStream(): OnlineStreamLike;
  isReady(stream: OnlineStreamLike): boolean;
  decode(stream: OnlineStreamLike): void;
  getResult(stream: OnlineStreamLike): { text: string };
  isEndpoint(stream: OnlineStreamLike): boolean;
  reset(stream: OnlineStreamLike): void;
}

interface SherpaWrappers {
  createOnlineRecognizer(moduleObj: object, config?: object): OnlineRecognizerLike;
}

export interface AsrRuntime {
  recognizer: OnlineRecognizerLike;
}

/**
 * zipformer2 CTC 中文 int8 配置（与 scripts/asr-node-smoke.js 同源；
 * MEMFS 路径由 patch 清单决定为根路径）。
 */
const RECOGNIZER_CONFIG = {
  featConfig: { sampleRate: 16000, featureDim: 80 },
  modelConfig: {
    transducer: { encoder: '', decoder: '', joiner: '' },
    paraformer: { encoder: '', decoder: '' },
    zipformer2Ctc: { model: '/model.int8.onnx' },
    nemoCtc: { model: '' },
    toneCtc: { model: '' },
    tokens: '/tokens.txt',
    numThreads: 1,
    provider: 'cpu',
    debug: 0,
    modelType: '',
    modelingUnit: 'cjkchar',
    bpeVocab: '/bbpe.model',
  },
  decodingMethod: 'greedy_search',
  maxActivePaths: 4,
  enableEndpoint: 1,
  rule1MinTrailingSilence: 2.4,
  rule2MinTrailingSilence: 1.2,
  rule3MinUtteranceLength: 20,
  hotwordsFile: '',
  hotwordsScore: 1.5,
  ctcFstDecoderConfig: { graph: '', maxActive: 3000 },
  ruleFsts: '',
  ruleFars: '',
};

function loadSubpackage(root: string): Promise<void> {
  return new Promise((resolve, reject) => {
    (wx as unknown as WxWithLoadSubpackage).loadSubpackage({
      name: root,
      success: () => resolve(),
      fail: (err) => reject(new Error(err.errMsg ?? `分包加载失败：${root}`)),
    });
  });
}

let runtimePromise: Promise<AsrRuntime> | null = null;

/** 获取 ASR 运行时（幂等单例；失败清空缓存以便重试）。 */
export function ensureAsrRuntime(): Promise<AsrRuntime> {
  if (runtimePromise === null) {
    runtimePromise = initRuntime().catch((err) => {
      runtimePromise = null;
      throw err;
    });
  }
  return runtimePromise;
}

async function initRuntime(): Promise<AsrRuntime> {
  await ensureAsrModel();
  await loadSubpackage(SUBPACKAGE_ROOT);

  // patch 后胶水首行：var Module=globalThis.__SHERPA_MODULE__||{}
  // → 运行时全部状态挂回本对象，onRuntimeInitialized/getPreloadedPackage 等同引用
  const moduleObj: Record<string, unknown> = {};
  (globalThis as Record<string, unknown>).__SHERPA_MODULE__ = moduleObj;

  await new Promise<void>((resolve, reject) => {
    moduleObj.locateFile = (p: string) => `${SUBPACKAGE_DIR}/${p}`;
    moduleObj.getPreloadedPackage = () => readModelSync();
    moduleObj.instantiateWasm = (
      imports: object,
      receive: (instance: object) => void,
    ): object => {
      // 小程序无 fetch：直接按包内路径实例化（typings 返回 Instance；
      // 胶水 receiveInstance 只取 instance.exports，忽略 module 参数）
      WXWebAssembly.instantiate(WASM_PATH, imports as WXWebAssembly.Imports)
        .then((instance) => receive(instance))
        .catch((err: unknown) => reject(err instanceof Error ? err : new Error(String(err))));
      return {}; // 告知胶水由本钩子接管实例化
    };
    moduleObj.onRuntimeInitialized = () => {
      log.info('wasm 运行时就绪');
      resolve();
    };
    moduleObj.print = (...args: unknown[]) => log.info('[wasm]', ...args);
    moduleObj.printErr = (...args: unknown[]) => log.warn('[wasm]', ...args);

    try {
      require(GLUE_PATH);
    } catch (err) {
      reject(err instanceof Error ? err : new Error(String(err)));
    }
  });

  const wrappers = require(WRAPPERS_PATH) as SherpaWrappers;
  const recognizer = wrappers.createOnlineRecognizer(moduleObj, RECOGNIZER_CONFIG);
  log.info('在线识别器创建完成（zipformer2 CTC zh int8）');
  return { recognizer };
}
