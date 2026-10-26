# 端侧 Sherpa-Onnx WASM 语音识别（首启一次联网，使用期完全离线）

## Context

个人主体无法添加「微信同声传译」插件，live 数据源 PTT 不可用。用户拍板换 **Sherpa-Onnx 端侧 WASM**：识别推理在手机本地，**音频永不出端**（不改「后端不处理原始音频」约束）。

**预算门实测结论（Step 0.5 已执行）**：原「模型随包分发、完全零联网」经实测不可行——微信规则主包 ≤2MB、所有分包合计 ≤30MB；候选 A `streaming-zipformer-small-ctc-zh-int8` 模型 **25.37MB**、候选 B `streaming-zipformer-zh-14m` int8 三件 **43.3MB**，加 wasm（预估 5-8MB）与主包均超 30MB，且公开中文模型无 ≤15MB 档。**用户决策：首启一次联网下载模型，之后使用期完全离线**（识别/导航在飞行模式下照常）。

已核实技术事实：
- sherpa-onnx **无官方小程序封装、无预编译 ASR wasm** → 自建：以 `wasm/vad-asr/CMakeLists.txt` 离线导出表为基准（`wasm/asr` 只导出在线函数），去 `--preload-file`、去 `WASM_BIGINT`、加 `MODULARIZE=1` + 导出 `FS`，`INITIAL_MEMORY 512MB→64MB+GROWTH`
- JS 封装 `wasm/asr/sherpa-onnx-asr.js` 有手写 `OfflineRecognizer`；config 传 **MEMFS 文件路径**
- **WXWebAssembly 只加载代码包内 .wasm** → wasm+glue 进**分包**（宽松：wasm ≤25MB 预算内）；模型**运行时下载**到用户目录再 `FS.writeFile` 桥入
- 模型下载源：**hf-mirror.com 已验证可达**（GitHub 直连被重置、HF 域名不可达、ModelScope 未同步目标模型）；开发机预下载解包 → 自家后端静态托管（微信端不解 bz2）
- PTT 按住→松开：**流式模型（候选 A/B 均为流式 zipformer）用 OnlineRecognizer 增量喂帧**，onPartial 有中间结果，贴合现有 PttState

## 分步实施（spike 先行，每步独立可验，失败即止损）

### Step 0：wasm 构建 + Node 冒烟
1. 开发机从 hf-mirror 下载候选 A 解包（已确认文件：`model.int8.onnx` 25.12MB、`bbpe.model` 0.24MB、`tokens.txt`、`test_wavs/*.wav` 夹具）
2. `scripts/build-asr-wasm.sh` 按上述 flags 构建 wasm + 胶水；实测 wasm 体积（分包预算 ≤25MB，宽松）
3. Node 下 require 胶水 + 模型 + 黄金夹具（**用真实导航指令录音样本优先**：「带我去资源教室」等；test_wavs 兜底）→ 吐文本
- **止损**：构建不出 / Node 识别不出 → 报告（含换候选 B 43.3MB 的质量/下载权衡），不碰小程序

### Step 1：小程序最小闭环（核心 spike）
- `client/subpackages/asr/` 放 glue JS + `.wasm`；app.json 加 `subpackages`
- `core/asr/sherpa-runtime.ts`：`wx.loadSubpackage` → require 胶水 → **`Module.instantiateWasm` 钩子注入 `WXWebAssembly.instantiate(分包路径)`** → 静态 PCM 夹具（先用开发机手动落地用户目录的模型）识别出文本
- 验证顺序：① MODULARIZE 产物 require 拿 factory（无 module.exports 则构建脚本追加兜底）→ ② instantiateWasm 钩子先于实例化 → ③ FS+识别
- **止损**：任一环卡死 → 止损报告（卡点+备选：手写 ccall 不先做）

### Step 2：录音器
- `core/asr/pcm-recorder.ts`：`wx.getRecorderManager` format:'PCM'、sampleRate:16000、numberOfChannels:1、frameSize → `onFrameRecorded` 累积（30s 上限）；iOS PCM 是否裸样本**待真机核实**（备选 tempFilePath 剥 RIFF 头）

### Step 3：模型分发（首启一次联网，此后零联网）
- **后端**：`backend/pathai_backend/routers/assets.py` 静态路由 `/static/asr/<file>` 免 Bearer（`wx.downloadFile` 不带 header）；模型三文件放 `backend/static/asr/`（gitignore，开发机下载解包后拷入，**不入 git**——体积大）
- **客户端** `utils/model-store.ts`：`${wx.env.USER_DATA_PATH}/asr-model/` 下载三文件 + `sha256Async` 校验 + 进度回调 + 指数退避重试（复用 map-cache 文件模式）；kv 存已就绪版本号（`cfg:asr-model`）；**失败可重试，不阻塞地图/导航**，PTT 报「模型未就绪」
- 联网校验：`wx.downloadFile` 走 request 域名规则（局域网 IP 需开发调试放行——真机清单已有）

### Step 4：引擎实现与装配
- 新 `core/asr/sherpa-onnx-asr.ts` 实现 `AsrEngine`：start=确保 runtime+模型就绪→启录音→增量喂帧，stop=停录→终解码→resolve（onPartial 有中间结果），cancel=停录+`AsrCancelledError`
- `services/app-context.ts` live 分支 `WechatsiAsrEngine` → `SherpaOnnxAsrEngine`（4-6 行；`asr-engine.ts`/`push-to-talk.ts`/既有状态机测试 **0 行改动**，wechatsi-asr.ts 保留待主体变更回切）
- `config/env.ts` 加模型版本/托管路径常量

### Step 5：真机/工具验收
- 工具：清缓存重编译；首启下载模型（进度/断网重试）→ PTT 录音识别
- 真机 iOS/Android：SIMD（微信 ≥8.0.25、基础库 ≥2.13.0）、内存峰值、识别延迟、30s 截断
- **离线验收：模型就绪后开飞行模式 → PTT 识别→意图→导航全链路照常**（核心目标）

## 单测（纯逻辑注入假 runtime/假下载）
- `tests/sherpa-asr-contract.test.ts`：AsrEngine 契约（start/stop settle、cancel→AsrCancelledError、重复 start 拒绝、stop 后 cancel 不覆盖）——沿 `tests/push-to-talk.test.ts` 注入风格
- `tests/model-store.test.ts`：sha 不符不落盘、半截文件重下、版本 kv、下载失败重试
- `tests/pcm-recorder.test.ts`：帧拼接、Int16 归一化、30s 截断
- 既有 push-to-talk/intent 等测试 0 改动

## 风险与止损
| 风险 | 应对 |
|---|---|
| 小程序胶水桥接无先例 | Step1 三关 spike 先验证，失败出止损报告不进 Step2 |
| 包体积 | 只有 wasm+glue 进分包（≤25MB 预算宽松）；模型不占包 |
| 内存（25MB 模型 + WASM 堆常驻） | INITIAL_MEMORY 64MB+GROWTH；真机峰值不过→换 int8 更小变体再议 |
| iOS WXWebAssembly（Global export 限制） | Step1/Step5 集中验证 |
| CTC 质量（导航域短指令） | Node 冒烟用真实指令样本测，不达标升候选 B（43.3MB 下载） |
| 首启下载失败/弱网 | 指数退避+断点（服务端 Range **待核实**，否则整文件重下）；PTT 明确报「模型未就绪，稍后重试」 |

## 验证命令
```powershell
cd d:\code\pathai\client ; npx tsc --noEmit ; npx jest     # 每步后
cd d:\code\pathai ; python -m pytest backend/tests -q        # 后端动过后
# 工具：清缓存→重新编译→vConsole；Step1 标志=夹具文本打出
# Step5：模型就绪后飞行模式 PTT→意图→导航全链路
```

## 关键文件
- 新增：`client/core/asr/{sherpa-runtime,sherpa-onnx-asr,pcm-recorder}.ts`、`client/utils/model-store.ts`、`client/subpackages/asr/*`、`scripts/build-asr-wasm.sh`、`backend/pathai_backend/routers/assets.py`、`backend/static/asr/*`（不入 git）、3 个新测试
- 改动：`client/app.json`（subpackages）、`client/services/app-context.ts`（live 分支 4-6 行）、`client/config/env.ts`（模型托管路径/版本常量）、`backend/pathai_backend/app.py`（注册 assets 路由）、`.gitignore`（static/asr）
- 复用：`core/asr/asr-engine.ts` 契约、`utils/sha256.ts::sha256Async`、`utils/map-cache.ts` 文件模式、`tests/push-to-talk.test.ts` 注入风格
