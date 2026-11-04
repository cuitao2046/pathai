# Tasks：导航无障碍交互优化

> 对应 `specs/nav-accessibility-ux/spec.md` **0.2.0**（已拍板）。
> 状态：已实施（T1–T11），自动化验证见 `npx tsc --noEmit` + `npx jest`；真机项见 spec §6。

## 依赖与顺序

```
T1 手势仲裁状态机（纯逻辑 + 单测）
  └─ T2 排除区域采样与命中
       └─ T3 页面接线（canvas 触摸流 → PTT）
            └─ T5 全屏手势设置开关

T4 方向角数学（纯逻辑 + 单测）
  └─ T6 传感器管线（罗盘 + 运动降级）
       └─ T7 箭头覆盖层（WXML + 样式 + 主题）
            └─ T8 对齐/偏离反馈（震动 + SpeechQueue 播报）
                 └─ T9 开关、生命周期与省电
```

## T1｜手势仲裁状态机

- 新建 `client/core/a11y/hold-to-talk-gesture.ts`（纯逻辑，无 wx API）：输入 `touchstart/move/end/cancel`
  事件摘要（时间戳、坐标、手指数、是否在排除区），输出 `pending | pan | voice | idle` 与动作指令。
- 参数：`HOLD_MS=500`、`MOVE_PX=10`（常量集中，便于 Q2/Q3 调整）。
- 单测：499/500/501 ms、9/10/11 px、2 指立即升级、500 ms 后移动保持语音、cancel 复位。

## T2｜排除区域采样

- 新建 `client/pages/map/tap-exclusion.ts`（或复用 `map.ts` 内方法）：
  `refreshExclusions()` 用 `createSelectorQuery().selectAll(...)` 采样热区矩形，外扩 2px；
  `isExcluded(x, y)` 纯函数 + 单测（边界、外扩、空集合退化）。
- 刷新触发点：`onReady`、`floorMenuOpen`/`collectMenuOpen`/`navActive` 变化、
  居中卡片出现、`onResize`；采样失败 → 退化到最小静态集合并 `log.warn`。

## T3｜页面接线（手势 → PushToTalk）

- `map.ts`：`onTouchStart/Move/End/Cancel` 接入 T1；语音态调 `onPttStart/onPttEnd/onPttCancel` 等价路径；
  保留 `mapReady` 门；进入语音态同步 `.ptt-zone` 的 `pttState`（视觉联动）。
- 不改动双指缩放与既有轻触语义；单指 pan 增加 10px 死区。
- 单测/回归：现有手势相关测试保持全绿。

## T4｜方向角数学

- 新建 `client/core/a11y/nav-arrow-angle.ts`：`shortestDelta`、`arrowAngle(routeBearing, heading)`、
  `emaAngle(prev, next, alpha=0.15)`、`isAligned(≤15°)`、`deviationBand(≤15 / 15–30 / >30)`、档位取整。
- 单测：0/360 回绕、180 边界、静止冻结（1 s 内 <3°）、分档边界 14/15/31。

## T5｜全屏手势设置开关

- 设置页新增「全屏按住说话」开关（kv `cfg:fullScreenPtt`、即时生效），默认开（Q9）。
- 首页读取并作为 T1 的总开关；关闭时行为回到 v0.3.7。
- 仅视障模式生效（与 T11 模式门叠加）。

## T6｜传感器管线

- 新建 `client/core/a11y/heading-source.ts`：封装 `wx.startCompass`/`onCompassChange`
  与 `wx.startDeviceMotionListening`/`onDeviceMotionChange`；提供 `start()/stop()/onHeading(cb)`、
  精度判定与三级降级（罗盘 → 运动相对基准 → 不可用）。
- 仅 `navActive` 启动；`onHide`/结束/卸载停止；单测用注入的假 API（不依赖真机）。

## T7｜箭头覆盖层

- `map.wxml` 新增 `.nav-arrow`（`pointer-events:none`、`z-index:20`、导航态 `wx:if`），箭头 + 对齐环；
- `map.wxss` 两套主题（浅底深蓝 / 深底亮黄）、警示态描边；尺寸 `min(70vw,300px)`；
- 角度驱动：`setData({ arrowDeg })` 节流 ≥20fps、变化 <2° 不更新；
- 取色脚本校验对比度（≥7:1，深色 ≥12:1）。

## T8｜对齐/偏离反馈

- 对齐：首次进入 ≤15° 短振 + 语音"方向已对准，请直行"（会话内一次）；
- 偏离 >30° 持续 2 s：红 + 白色描边 + 语音"偏离约 X 度，请向{左/右}转"，节流 ≥5 s，
  走 `SpeechQueue` `normal`；
- 位置不可信：箭头灰 + 降级提示，不播报方向（对齐 `phase-1` REQ-NAV）。

## T9｜开关、生命周期与省电

- 设置页新增「导航方向箭头」开关（默认开）；
- 传感器启停、后台停止、低电量策略；监听计数断言（B7）。

## T10｜验收与文档回写

- 实机验收 A1–A6、B1–B7 记录表（自动化已覆盖 A4/A5 的可测部分与 A6 分支）；
- 已回写 `phase-1` REQ-A11Y 交叉引用并升 0.3.8；
- `specs/field-collection-integration/spec.md` 无影响，不改。

## T11｜无障碍模式（普通 / 视障）

- 设置页第 1 组「无障碍模式」：模式开关（kv `cfg:a11yMode`，默认 `a11y`）+ 两个子开关；
  即时生效（`map.ts#onShow` 回读 → `applyA11ySettings()` → `syncArrowLifecycle()`）。
- 普通模式：不启用 T1 手势（单指按下立即 pan，无 500ms/10px）、不渲染 T7 箭头；
  导航核心（路线/定位/HUD/语音/震动）不变。
- Q11「夜间/强对比」并入模式：配色随系统深浅色（`theme === 'dark'`）。
- 单测：普通模式下 `hold-to-talk-gesture` 不被调用（页面分支由 `isFullScreenPttEnabled()` 守卫，
  属页面级；纯逻辑侧覆盖状态机阈值边界，页面分支以真机/回归确认）。
