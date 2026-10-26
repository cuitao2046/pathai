# 讯飞在线语音（ASR+TTS）替换 Sherpa 端侧方案

## Context

端侧 Sherpa-Onnx WASM（13.15MB）无法压进微信单分包 2048KB 硬限（brotli 后仍 2281KB；备选 wasm 均因缺配套 glue 或无在线识别器不可用），离线路线判定不可行。按用户决策改为**讯飞开放平台在线 WebSocket**：

- ASR：语音听写大模型（中英）V2.0，`wss://iat.cn-huabei-1.xf-yun.com/v1`
- TTS：超拟人语音合成，`wss://{host}/v1/private/{实例id}`（完整地址取自讯飞控制台）
- 个人主体无 WechatSI 插件，TTS 一并换讯飞（顺带修复 live TTS 永远降级的问题）

已拍板的三个决策：

1. **HMAC-SHA256 签名由后端签发**：密钥只存 `PATHAI_XFYUN_*` 环境变量，后端出接口返回签好的 wss URL，小程序 `wx.connectSocket` 直连讯飞。
2. **Sherpa 端侧代码彻底删除**（2048KB 超限问题随之消失）。
3. 用户**已有讯飞密钥**（两服务均已开通），开发期即可真机联调。

## 讯飞接口关键事实（文档已核实，直接采信）

- 签名：URL query 携带 `host/date/authorization`；date 为 RFC1123 GMT（服务端校验 ±300s）；`signature_origin = "host: {host}\ndate: {date}\nGET {path} HTTP/1.1"`，HMAC-SHA256(APISecret) → base64 → 拼 `authorization_origin` → 再 base64 → urlencode。
- ASR 发帧：JSON；`header{app_id,status:0/1/2}`；**首帧必须含 `parameter.iat{domain:"spark_asr_v2",language:"zh_cn",accent:"mandarin",dwa:"wpgs",result{encoding:"utf8",compress:"raw",format:"json"}}`**，后续帧仅 header+payload；`payload.audio{encoding:"raw",sample_rate:16000,channels:1,bit_depth:16,seq,status,audio:base64}`；建议节奏 1024B/40ms；音频≤60s（我们录音上限 30s）。
- ASR 收帧：`payload.result.text` base64 → UTF-8 → JSON `{ws:[{cw:[{w}]}],ls,sn}`；`dwa=wpgs` 时有 `pgs`（"apd"追加 / "rpl"替换）+ `rg`（被替换 sn 区间 [起,止]）；结束帧 `header.status==2`。
- TTS 一次性合成：`header{app_id,status:2}`、`parameter.tts{vcn,speed/volume/pitch:50,bgs/reg/rdn/rhy:0,audio{encoding:"lame",sample_rate:24000,channels:1,bit_depth:16,frame_size:0}}`、`payload.text{encoding:"utf8",compress:"raw",format:"plain",status:2,seq:0,text}`；返回 `payload.audio.audio` base64 mp3 分片至 `header.status==2`。vcn 默认 `x5_lingxiaoxuan_flow`。

## 一、后端改动

### 新文件

- `backend/pathai_backend/xfyun.py`：签名工具。
  - `sign_wss_url(*, host, path, api_key, api_secret, now=None) -> str`——`now` 可注入以便测试固定 date；date 用 `email.utils.format_datetime(..., usegmt=True)`。
  - `build_iat_url(settings, now=None)`（host/path 固定）、`build_tts_url(settings, now=None)`（host+path 从 `PATHAI_XFYUN_TTS_URL` 解析）。
- `backend/pathai_backend/routers/voice.py`：
  - `GET /api/v1/voice/asr-url`、`GET /api/v1/voice/tts-url`，`dependencies=[Depends(auth.get_current_user)]`（对齐 map.py 先例）。
  - 响应 `{"url": "<含签名完整 wss URL>", "expiresIn": 240}`（服务端 date ±300s，留 60s 余量）。
  - 缺任一讯飞密钥 → `ApiError(503, "XFYUN_NOT_CONFIGURED", retryable=False)`（对齐 auth.py"请求时报错而非启动失败"的模式，本地无密钥也能起服务）。

### 修改

- `backend/pathai_backend/config.py`：Settings 新增字段（`repr=False`）+ `from_env` 读取：
  - `PATHAI_XFYUN_APPID`（两服务共用 appid）
  - `PATHAI_XFYUN_IAT_API_KEY` / `PATHAI_XFYUN_IAT_API_SECRET`
  - `PATHAI_XFYUN_TTS_API_KEY` / `PATHAI_XFYUN_TTS_API_SECRET`
  - `PATHAI_XFYUN_TTS_URL`（形如 `wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/xxx`）
- `backend/pathai_backend/app.py`：删 `assets_router` import+注册行，加 `voice_router`。
- 无额外依赖（`hmac/hashlib/base64/urllib.parse` 标准库）。

### 后端测试（新 `backend/tests/test_voice_url.py`）

- 签名正确性：注入固定 `now`，按文档公式手工重算 authorization 断言全等；URL 含 `host= date= authorization=` 且 urlencode 正确。
- TTS：签名 origin 含 `PATHAI_XFYUN_TTS_URL` 解析出的 path。
- 缺密钥 → 503 `XFYUN_NOT_CONFIGURED` 标准错误信封；无 token → 401；有效 token → 200。

## 二、客户端改动

**实现前先读参照物**：`client/core/asr/sherpa-onnx-asr.ts`（deps 注入结构、pending 会话、cancel 先摘除再 teardown、finalize 等 recorder.stop 的模式全部沿用）、`client/core/asr/pcm-recorder.ts`（RecorderLike 接口不动）。

### 新文件

1. `client/utils/base64.ts`：`utf8ToBase64`（复用 `utils/sha256.ts` 的 `utf8Bytes`，不改 sha256.ts）、`base64ToUtf8`（自写 UTF-8 解码）；二进制侧用内置 `wx.arrayBufferToBase64` / `wx.base64ToArrayBuffer`。
2. `client/utils/wss-url.ts`：`createWssUrlProvider(client, path, ttlMs=240_000)`——经 `ApiClient.request({method:'GET',path})` 取签名 URL（自带 Bearer+401 重登重放），按 service 缓存 4 分钟，过期前刷新。
3. `client/core/asr/xfyun-asr.ts`：`XfyunAsrEngine implements AsrEngine`
   - 构造 `deps?: { getUrl: () => Promise<string>; recorder?: RecorderLike; connect?: (url) => SocketLike }`（connect 默认包 `wx.connectSocket`，单测注入假 socket task）。
   - `start`：重复会话同步抛错 → 取 URL → 连接（8s 超时）→ onOpen 发首帧（含 parameter.iat + 首个音频帧）→ `recorder.start({onFrame})`，帧经**发送队列按 40ms 节流**发出（seq 递增、status 0→1）；Float32 帧转 Int16 LE 字节（纯函数 `floatToInt16Bytes`，`samples[i]*32768` clamp）→ base64。
   - 收帧：`header.code!=0` → failSession；`payload.result.text` 解码 → `applyWpgs(buf, msg)` 纯函数累积（**导出以便单测**：按 sn 存；`apd` 同 sn 追加、`rpl` 清 `rg[0]..rg[1]` 中非当前 sn 再写入当前 sn；全文=按 sn 排序拼接）→ 有变化回吐 `onPartial`。
   - `stop()`：`recorder.stop()`（等 onStop，尾帧入队）→ 队列刷完 → 发末帧 `status:2` 空 audio → 等 `header.status==2` 响应 settle 最终全文；**30s 录音自动停时不主动 finalize**（与 sherpa 一致：等 PTT 松手再收尾）；末帧响应 10s 超时 → 正常 Error reject。
   - `cancel()`：先摘 pending → 关 socket + `recorder.cancel()` → `AsrCancelledError` reject。
   - 无 warmup。
4. `client/core/speech/xfyun-tts.ts`：`XfyunTts implements TtsEngine`
   - `deps?: { getUrl; connect?; audioFactory?; fs? }`（默认 `wx.createInnerAudioContext` / `wx.getFileSystemManager`，单测注入假件）。
   - `speak`：取 URL → 连接 → 发单帧（status:2）→ 收 `payload.audio.audio` 分片 `wx.base64ToArrayBuffer` 拼接 → `header.status==2` → 合并写 `wx.env.USER_DATA_PATH/xfyun-tts.mp3`（固定名，播前清旧）→ InnerAudioContext 播放（照 `wechatsi-tts.ts` 的 settle 模式：onEnded/onStop→resolve、onError→reject）。失败正常 reject，SpeechQueue FE-04 降级已有。
   - `stop()`：`audio?.stop()`（onStop→resolve 播完语义）+ 关 socket + 清临时文件。

### 修改

- `client/services/app-context.ts`：live 分支 `new XfyunAsrEngine({getUrl: asrUrlProvider})` + `new XfyunTts({getUrl: ttsUrlProvider})`（provider 用 `this.http.client` 构建），删 Sherpa/Wechatsi 导入与 `warmup()`；mock 分支不动。
- `client/config/env.ts`：删 `ASR_MODEL_VERSION`。
- `client/tests/setup-wx.ts`：补 `wx.connectSocket`/`wx.arrayBufferToBase64`/`wx.base64ToArrayBuffer`/`wx.getFileSystemManager`/`wx.env.USER_DATA_PATH` 所需桩（能走 deps 注入的不依赖全局桩）。
- `pages/index/index.ts` 等页面**零改动**（AsrEngine/TtsEngine 契约未变，实现时确认）。

## 三、删除清单

- `client/subpackages/asr/`（整目录）+ `client/app.json` 的 `subpackages` 声明
- `client/core/asr/sherpa-onnx-asr.ts`、`sherpa-runtime.ts`
- `client/utils/model-store.ts`
- `client/core/speech/wechatsi-tts.ts`、`client/core/asr/wechatsi-asr.ts`（后者现已是无引用孤儿）、`client/utils/plugin-loader.ts`（仅被这两个文件使用）
- `client/config/env.ts` 的 `ASR_MODEL_VERSION`
- `client/tests/sherpa-asr-contract.test.ts`（**保留** `pcm-recorder.test.ts`、`sha256.test.ts`、`map-service.test.ts`——sha256.ts 被 map-service 依赖，不动）
- `scripts/patch-asr-glue.js`、`scripts/asr-node-smoke.js`（含我在本会话对 smoke 的临时改动）
- 后端：`backend/pathai_backend/routers/assets.py` 整文件 + `app.py` 两行（已确认无测试引用它）
- `.gitignore` 的 `/backend/static/asr/` 行
- 清残留验证：`grep -ri "sherpa|model-store|ASR_MODEL_VERSION|Wechatsi|plugin-loader" client backend scripts`（`.trae/documents`、`docs/24`、`specs/` 为历史存档**不改**）

## 四、测试计划

- 客户端 jest 新增：
  - `client/tests/xfyun-asr-contract.test.ts`（组织方式照抄 sherpa 版：FakeRecorder + FakeSocketTask 注入）：重复 start 同步抛错；首帧含 parameter.iat；40ms 队列与 status 0→1→2；`header.status==2` settle 最终文本；stop 等末响应；cancel→AsrCancelledError 且关 socket；getUrl 失败/连接超时 reject；onPartial 触发。
  - `applyWpgs` 用例（用文档示例 JSON 构造）：普通累加、apd、rpl 跨段替换、ls。
  - `client/tests/xfyun-tts.test.ts`：分片拼接→writeFile→onEnded resolve；stop 经 onStop resolve；status==2 前错误 reject。
  - `client/tests/wss-url.test.ts`：缓存命中/过期重取/401 重登重放。
  - `client/tests/base64.test.ts`：UTF-8 往返（中文、emoji）。
- 后端 pytest：`backend/tests/test_voice_url.py`（见上），回归 `pytest backend/tests`（当前 202 项基数）。
- 全量：`npx tsc --noEmit`（client）+ `npx jest` + `pytest backend/tests`。

## 五、实现顺序（分步提交）

1. 后端 `xfyun.py` + Settings 字段 + `voice.py` + app.py 注册 + pytest（独立可验）。
2. 客户端 `utils/base64.ts`、`utils/wss-url.ts` + jest。
3. `XfyunAsrEngine` + 契约测试（先读 sherpa-onnx-asr.ts）。
4. `XfyunTts` + 测试。
5. app-context/env 装配切换 + 页面确认零改动 → jest 全量。
6. 删除 Sherpa/Wechatsi 全清单 + app.json + gitignore + scripts + 后端 assets → grep 清残留 → jest+pytest+tsc 全量回归。
7. docs/25 更新（见验收）。

## 六、风险与真机注意事项

- **socket 合法域名**：小程序后台「开发设置→服务器域名→socket合法域名」需加 `wss://iat.cn-huabei-1.xf-yun.com` 与 `wss://cbm01.cn-huabei-1.xf-yun.com`；开发期勾「不校验合法域名」/真机开「开发调试」。真机连不上优先查这里。
- **首帧结构**：parameter.iat 必须在首帧，漏发直接报错。
- **发送节奏**：recorder 1KB 帧 ≈64ms，40ms 队列节流；stop 时尾帧可能突发，队列天然限速。
- **字节序**：PCM 小端 Int16；Float32→Int16 转换用 clamp×32768 纯函数。
- **date ±300s**：签名由后端实时生成（用后端时钟，与手机时钟无关），客户端 URL 缓存 ≤4 分钟。
- **TTS 临时文件**：固定文件名，每次合成前覆盖，stop/播完可 unlink。
- **竞态**：30s 自动停、PTT 松手、cancel 三方——沿用 sherpa 的"先摘 pending 再 teardown"模式。

## 七、验收（docs/25 重写要点）

`docs/25-第二阶段真机联调检查清单.md`：
- §1.1 后端启动命令补 `PATHAI_XFYUN_*` 环境变量示例（注释掉的占位）。
- §1.2 删除 WechatSI 插件⚠️块（个人主体限制已不适用），改为讯飞依赖说明（在线、需 socket 域名）。
- §1.5 重写为讯飞验收：① 后端配好密钥 → 启动；② 设置页切 live → 重启；③ PTT 说「带我去资源教室」→ 中间结果（wpgs partial）+ 最终文本 + 意图闭环；④ TTS 播报真实出声（不再震动降级）；⑤ 断网时 PTT/TTS 报错且 FE-04 降级正常；⑥ 30s 录满自动收尾出文本。
- §2.2 WechatSI 小节改为 socket 域名配置核对项。
- docs/24、specs/ 为第一阶段历史契约存档，不改。

完成后全量回归：`npx tsc --noEmit` + `npx jest` + `pytest backend/tests`，分步提交（约 6 笔）。
