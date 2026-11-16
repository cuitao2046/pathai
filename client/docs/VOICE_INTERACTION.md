# Aegis 旁白语音交互适配

## 使用

微信开发者工具导入 client，沿用原 project.config.json、服务地址和讯飞配置。
设置 → 无障碍模式 → 开启「旁白语音交互」，返回地图即时生效并持久保存。
该设置独立于「视障模式」（箭头/地图展示）和手机系统旁白，不会开启系统旁白。
默认关闭以保留原交互。不根据 tap/touch 的形态推测系统是否开启旁白。

- 关闭：原有底部按住说话、松开提交；视障模式且全屏开关开启时，空白区域长按 0.5 秒录音。
- 开启：聚焦原生「开始语音输入」按钮，双击激活开始；同一按钮变为「结束语音输入」，再次激活结束。不使用触摸开始/结束来录音。
- 两种模式录音最长 15 秒，结束后等待识别最多 30 秒；取消/后台/卸载不提交。
- 旁白模式提供「取消语音输入」，识别、意图理解阶段也可取消。理解阶段取消不撤销 HTTP 请求，但忽略其迟到结果。
- 麦克风真正就绪才短振；准备阶段也可再次激活结束。不在准备阶段提示已经可以说话。

## 分层

地图页：Touch Adapter（原有全屏手势、底部触摸）/ Accessibility Adapter（原生 button 的 bindtap）。
VoiceInteraction：两套入口共用 PushToTalk；负责模式切换取消、录音上限、识别超时、后台清理。
PushToTalk：ASR 生命周期、错误文案、会话代次隔离；复用现有讯飞/Mock ASR、PCM Recorder。
地图页 handleFinal → IntentService → 原有候选选择/目的地确认 → NavSessionController。
SpeechQueue：录音前 interrupt；旁白模式输出可访问文本，普通模式继续应用 TTS。

## 固定文案与本地音频

固定方向文案优先走本地离线音频（`client/assets/voice/`），动态文案继续走 TTS：

| 文案 | 本地文件 |
| --- | --- |
| 即将左转。 | `ready2left-turn.mp3` |
| 即将右转。 | `ready2right-turn.mp3` |
| 左转。 | `left-turn.mp3` |
| 右转。 | `right-turn.mp3` |
| 稍向左转。 | `little-left.mp3` |
| 稍向右转。 | `little-right.mp3` |
| 掉头。 | `u-turn.mp3` |

映射见 `client/core/speech/static-clips.ts`；当前后端只生成“即将左转/右转”，其余三组为后续动作预留。
动态文案（直行 X 米、进入电梯前往 X 楼、到达 X 楼退出电梯、路线概要、规划到 X 的路线、方向偏离约 X 度等）继续走 TTS。

已补齐的固定提示音：

| 文案/用途 | 本地文件 |
| --- | --- |
| 已到达目的地附近，请留意周围环境。 | `nav-arrived-near.mp3` |
| 定位不足/信号不稳定的安全暂停长文案 | `nav-relocalizing.mp3` |
| 导航连接中断的安全暂停提示 | `nav-offline.mp3` |
| 导航会话已失效 | `nav-session-invalid.mp3` |
| 地图已更新，请重新发起导航 | `nav-map-updated.mp3` |
| 是否现在开始导航的确认询问 | `preview-confirm.mp3` |
| 导航已取消 | `nav-cancelled.mp3` |
| 确认超时，导航已取消 | `preview-timeout.mp3` |
| 无法开始导航，请重试 | `nav-start-failed.mp3` |
| 结束导航失败，请重试 | `nav-end-failed.mp3` |
| 方向已对准，请直行 | `heading-aligned.mp3` |
| 当前设备无法提供方向 | `heading-unavailable.mp3` |

仍缺少本地音频的静态文案（建议补文件后再接入）：

- `nav-arrived.mp3`：“已到达目的地。”（当前 `nav-arrived-near` 只覆盖“附近”版本）
- `preview-confirm-alt.mp3`：“路线概要已播报完毕……”或“是否开始导航？说‘开始导航’，或点击屏幕中的‘开始导航’按钮。”
- `nav-relocalizing-short.mp3`：“正在重新定位，请原地稍候。”（服务端未下发指令时的短兜底）

## 完整状态及转移

录音状态与导航状态正交，导航进行中也能语音查询/更改目的地。

| 当前状态 | 事件 | 下一状态 / 动作 |
| --- | --- | --- |
| 地图未就绪/加载失败 | 任意语音开始事件 | 禁止录音；保留加载/重试入口 |
| idle / error | 普通按下或全屏长按成立 | recording（准备麦克风）；中断应用播放 |
| idle / error | 旁白模式激活 | recording（准备麦克风）；中断应用播放 |
| recording（准备） | ASR onReady | recording（就绪）；短振和更新可访问状态 |
| recording | 普通松手 / 旁白再次激活 / 15 秒上限 | recognizing；停止录音收尾 |
| recognizing | 重复激活 / 触摸 | 忽略；主语音按钮禁用 |
| recognizing | 最终非空文本 | ASR 回 idle；页面进入 understanding（voiceParsing） |
| recognizing | 最终空白 | idle；“没听清，请再说一遍” |
| recognizing | 30 秒超时 | cancel → idle；“语音识别超时，请重试” |
| 准备/录音/识别 | 权限/引擎错误 | error；中文提示；允许重试 |
| 准备/录音/识别 | 取消/后台/卸载/交互模式切换 | idle；引擎 cancel；清计时器；隔离旧回调 |
| understanding | 识别/录音开始 | 拒绝并行输入，主按钮禁用 |
| understanding | 解析成功 | 沿用现有候选卡、目的地确认卡或查询回复 |
| understanding | 解析失败 | 展示错误反馈，恢复语音入口 |
| understanding | 取消/后台/卸载 | 清忙状态并使旧请求结果失效 |
| 候选选择 | 激活某个候选 | 沿用现有目的地确认流程 |
| 目的地确认/路线预览 | 开始导航 | 沿用现有 NavSessionController 状态机 |
| 导航中 | 新语音输入 | 同一录音状态机；原有导航服务继续管理路线 |
| 导航中 | 到达/结束 | 沿用现有到达/结束反馈 |

识别故障只展示面向用户的中文文案，原始错误进日志。会话取消立刻增加 generation，旧 partial、onReady、final、reject 均不能影响新会话。

## 播报与平台边界

旁白模式：停止应用 TTS 与启动本地提示音，反馈以 voiceStatus、reply、导航 HUD 和动态按钮 aria-label 呈现；普通模式沿用原 TTS。录音抢占清空队列，录音中到达的应用播报不出声，避免录入自身声音。
原生 button 保留同一节点，动态名称而非替换按钮，不主动抢焦点、不绑定系统双击或双指手势。

注意：小程序对 ARIA 的支持是平台相关的，aria-live/动态 aria-label 是否主动朗读，必须在目标微信版本和设备上验证。本实现提供可聚焦文本及 aria-live 渐进增强，**不保证所有设备自动播出每次状态变化**。目前不调用未经验证的系统朗读 API，也不假定即时完成的文本适配器代表屏幕阅读器已读完。若设备不主动朗读，用户可滑动聚焦状态文本；导航自动连续播报需求需在目标设备验收后再定制平台播报通道。手机旁白正在朗读时应用也不能控制其音频结束时间，用户听到就绪提示后再说话。

## 改动文件

- 新增 core/asr/voice-interaction.ts：统一交互入口及计时器。
- 新增 core/a11y/voice-mode.ts：独立、持久化的显式开关。
- 修改 core/asr/push-to-talk.ts：旧回调隔离、同步停止错误和取消容错。
- 修改 core/speech/speech-queue.ts：interrupt 清队列及停止当前播放。
- 修改 pages/map/map.ts / map.wxml / map.wxss：按钮语义、状态展示、取消、忙状态、读屏输出适配及生命周期。
- 修改 pages/settings/settings.ts / settings.wxml：开关、操作说明、写入失败反馈。
- 新增 tests/voice-interaction.test.ts：普通/旁白路由、超时、旧回调、切换/后台、stop 异常。

## 自动验证

TypeScript：`node node_modules/typescript/bin/tsc --noEmit` 通过。
相关语音回归：5 个测试套件、80 项测试通过（voice-interaction、push-to-talk、speech-queue、xfyun-asr-contract、hold-to-talk-gesture）。
全量：55 套通过、9 套失败、1 套跳过；483 项通过、1 项跳过。9 套失败均为附件缺少 `../backend/maps_data/school-building-01/2026-10-25.1.json`，未伪造地图 fixture。
附件 node_modules 为 Windows 平台，npm run typecheck 的前置 esbuild 在当前 Linux 环境无法运行；本次直接运行已有 TypeScript 和 Jest 验证，未改动依赖锁。发布包排除 node_modules；本机执行 npm ci 后运行 npm run typecheck / npm test。

## 真机验收（尚未执行）

分别测试 iOS VoiceOver、Android TalkBack，以及二者关闭状态：

1. 开关保存后重进仍保持；普通视觉/视障视觉模式均可独立使用旁白开关。
2. 单指滑动能聚焦语音按钮；开始/准备/就绪/结束名称正确；同一按钮焦点稳定。
3. 包括 touch 与 tap 同时投递的设备：旁白双击只开始一次，再双击只停止一次。
4. 普通长按录音、松开识别，全屏排除区域、地图手势与原行为一致。
5. 首次授权、拒绝授权、快速结束、无音频、断网、超时均可恢复重试。
6. 录音或识别中取消、切后台、返回设置；迟到结果不能创建导航或覆盖新会话。
7. 识别完成后理解期间取消；迟到回复不展示、不启动导航。
8. 播报中开始录音：应用音频立即停；旁白模式无应用 TTS 叠音。确认状态、候选、路线、转弯、到达在系统读屏下实际可听/可访问。
9. 连续重复输入及 15 秒自动收尾；每轮录音不重复占用麦克风。
