/**
 * Step0 Node 冒烟：现成 wasm 产物 + patch 后胶水 → 流式识别中文测试音频。
 *
 * 验证链（小程序 Step1 之前先把此链在 Node 跑通，隔离微信环境变量）：
 *  __SHERPA_MODULE__ 预置(locateFile/getPreloadedPackage/onRuntimeInitialized)
 *  → require 胶水(经 patch：读 globalThis 注入、元数据换模型清单)
 *  → 模型经 getPreloadedPackage 进 MEMFS → createOnlineRecognizer(zipformer2Ctc)
 *  → wav(16k) 流式喂帧 → 打印识别文本
 *
 * 用法：node scripts/asr-node-smoke.js [wav路径，默认 test_wavs_0.wav]
 */
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..');
const SPACE_DIR = path.join(REPO_ROOT, 'result', 'wasm-space');
const PATCHED = path.join(SPACE_DIR, 'patched');
const MODEL_DIR = path.join(REPO_ROOT, 'result', 'asr-models', 'zh-ctc-small');
const MODEL_FILES = ['model.int8.onnx', 'bbpe.model', 'tokens.txt'];

/** wav → Float32 PCM（跳 RIFF 头找 data 块；16bit 单声道；任意采样率线性重采样到 16k）。 */
function loadWavPcm16k(file) {
  const buf = fs.readFileSync(file);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error('非 RIFF wav');
  const sampleRate = buf.readUInt32LE(24);
  const channels = buf.readUInt16LE(22);
  let pos = 12;
  let dataStart = -1;
  let dataSize = 0;
  while (pos + 8 <= buf.length) {
    const id = buf.toString('ascii', pos, pos + 4);
    const size = buf.readUInt32LE(pos + 4);
    if (id === 'data') {
      dataStart = pos + 8;
      dataSize = size;
      break;
    }
    pos += 8 + size + (size % 2);
  }
  if (dataStart < 0) throw new Error('未找到 data 块');
  const int16 = new Int16Array(buf.buffer, buf.byteOffset + dataStart, Math.floor(dataSize / 2));
  const mono = new Float32Array(int16.length / channels);
  for (let i = 0; i < mono.length; i += 1) {
    let acc = 0;
    for (let c = 0; c < channels; c += 1) acc += int16[i * channels + c];
    mono[i] = acc / channels / 32768;
  }
  if (sampleRate === 16000) return mono;
  const ratio = sampleRate / 16000;
  const out = new Float32Array(Math.floor(mono.length / ratio));
  for (let i = 0; i < out.length; i += 1) {
    const p = i * ratio;
    const i0 = Math.floor(p);
    const i1 = Math.min(i0 + 1, mono.length - 1);
    out[i] = mono[i0] + (mono[i1] - mono[i0]) * (p - i0);
  }
  return out;
}

async function main() {
  const wavPath = process.argv[2] || path.join(MODEL_DIR, 'test_wavs_0.wav');
  const chunks = MODEL_FILES.map((n) => fs.readFileSync(path.join(MODEL_DIR, n)));
  const modelBuffer = Buffer.concat(chunks);
  const ab = modelBuffer.buffer.slice(
    modelBuffer.byteOffset,
    modelBuffer.byteOffset + modelBuffer.byteLength,
  );

  globalThis.__SHERPA_MODULE__ = {
    locateFile: (p) => path.join(SPACE_DIR, p),
    getPreloadedPackage: () => ab,
    print: (...a) => console.log('[wasm]', ...a),
    printErr: (...a) => console.error('[wasm-err]', ...a),
  };
  const ready = new Promise((resolve) => {
    globalThis.__SHERPA_MODULE__.onRuntimeInitialized = resolve;
  });

  console.log('loading glue…');
  const glue = require(path.join(PATCHED, 'sherpa-glue.cjs.js'));
  await ready;
  console.log('runtime initialized, FS model files:',
    glue.getModule().FS ? Object.keys(glue.getModule().FS.files || {}) : '(FS 未导出，检查 MEMFS 经封装可见性)');

  const { createOnlineRecognizer } = require(path.join(PATCHED, 'sherpa-wrappers.cjs.js'));
  const mod = glue.getModule();
  const recognizer = createOnlineRecognizer(mod, {
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
  });
  console.log('recognizer created');

  const samples = loadWavPcm16k(wavPath);
  console.log(`wav: ${wavPath} samples=${samples.length} (${(samples.length / 16000).toFixed(2)}s)`);
  const t0 = Date.now();
  const stream = recognizer.createStream();
  stream.acceptWaveform(16000, samples);
  while (recognizer.isReady(stream)) recognizer.decode(stream);
  const text = recognizer.getResult(stream).text;
  console.log(`decode in ${Date.now() - t0}ms`);
  console.log('识别结果:', JSON.stringify(text));
  if (!text || text.trim().length === 0) {
    console.error('SMOKE FAIL: 识别文本为空');
    process.exit(1);
  }
  console.log('SMOKE OK');
}

main().catch((err) => {
  console.error('SMOKE FAIL:', err);
  process.exit(1);
});
