/**
 * sherpa-onnx wasm 胶水/封装 → CommonJS 转换脚本（小程序与 Node 通用桥接前置）。
 *
 * 源产物来自 ModelScope studio `k2-fsa/web-assembly-asr-sherpa-onnx-zh-en`
 * （result/wasm-space，浏览器全局变量风格、非 MODULARIZE）。三处手术：
 *  1) `var Module=typeof Module!="undefined"?Module:{}` → 读取
 *     `globalThis.__SHERPA_MODULE__`（调用方先预置 locateFile/getPreloadedPackage/
 *     onRuntimeInitialized，Node 与小程序同一份产物通用）
 *  2) 内联 .data 包元数据（原 space preload 了 190MB demo 模型）→ 替换为
 *     ASR 模型清单（路径 /asr/*）；配合 getPreloadedPackage 返回的模型
 *     buffer，模型在 preRun 阶段自动写入 MEMFS——绕开 FS 未导出的限制
 *  3) 尾部追加 module.exports
 *
 * 用法：node scripts/patch-asr-glue.js
 */
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..');
const SPACE_DIR = path.join(REPO_ROOT, 'result', 'wasm-space');
const OUT_DIR = path.join(SPACE_DIR, 'patched');
/** 模型三文件（候选 A：streaming zipformer2 CTC zh int8），顺序即拼接顺序。 */
const MODEL_FILES = ['model.int8.onnx', 'bbpe.model', 'tokens.txt'];

function buildMetadata(sizeOf) {
  const files = [];
  let offset = 0;
  for (const name of MODEL_FILES) {
    const size = sizeOf(name);
    // 直接放根路径：loadPackage 写文件不会创建子目录（/asr 会 ENOENT）
    files.push({ filename: `/${name}`, start: offset, end: offset + size });
    offset += size;
  }
  return { files, total: offset };
}

function patchGlue(modelSizes) {
  const src = fs.readFileSync(path.join(SPACE_DIR, 'sherpa-onnx-wasm-main-asr.js'), 'utf8');

  // 手术 1：Module 来源注入
  const HEAD = 'var Module=typeof Module!="undefined"?Module:{};';
  if (!src.includes(HEAD)) {
    throw new Error('glue 头部形态与预期不符，未找到 Module 定义');
  }
  let out = src.replace(HEAD, 'var Module=globalThis.__SHERPA_MODULE__||{};');

  // 手术 2：.data 元数据替换为 ASR 模型清单
  const metaRe = /loadPackage\(\{files:\[.*?\],remote_package_size:\d+\}\)/;
  if (!metaRe.test(out)) {
    throw new Error('未找到内联 loadPackage 元数据');
  }
  const { files, total } = buildMetadata((n) => modelSizes[n]);
  const newMeta =
    'loadPackage({files:' +
    JSON.stringify(files) +
    ',remote_package_size:' +
    total +
    '})';
  out = out.replace(metaRe, newMeta);

  // 手术 3：尾部导出
  out += '\nmodule.exports={getModule:function(){return Module;}};\n';
  return out;
}

function patchWrapper() {
  const src = fs.readFileSync(path.join(SPACE_DIR, 'sherpa-onnx-asr.js'), 'utf8');
  // 封装函数均以 Module 为参数，无全局依赖；仅追加导出
  return (
    src +
    '\nmodule.exports={createOnlineRecognizer:createOnlineRecognizer,OfflineRecognizer:OfflineRecognizer};\n'
  );
}

function main() {
  const modelSizes = {};
  for (const name of MODEL_FILES) {
    const p = path.join(REPO_ROOT, 'result', 'asr-models', 'zh-ctc-small', name);
    modelSizes[name] = fs.statSync(p).size;
  }
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(path.join(OUT_DIR, 'sherpa-glue.cjs.js'), patchGlue(modelSizes));
  fs.writeFileSync(path.join(OUT_DIR, 'sherpa-wrappers.cjs.js'), patchWrapper());
  const { files, total } = buildMetadata((n) => modelSizes[n]);
  console.log('patched ->', OUT_DIR);
  for (const f of files) console.log(`  ${f.filename}  [${f.start}, ${f.end})`);
  console.log(`  remote_package_size = ${total}`);
}

main();
