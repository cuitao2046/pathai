# 楼层地图启动首屏（1F/2F 切换 + 缩放拖动 + mock 位置点）

## Context

用户需求：小程序启动后展示类似导航软件的楼层地图，可选择 1F/2F，初始位置 mock 为 1F 的 (-37,-48)，地图可缩放、移动。**已确认**：新增 map 页设为启动首屏（app.json pages[0]），页内提供入口进入现有语音首页（index 页的 PTT/意图/设置不动）。

现状：项目 canvas 零使用（全新接入）；地图包（4.6MB，本地文件缓存秒开）已含完整 walls/topology/beacons 数据，但 `MapService` 未暴露楼层几何、`typings/api.d.ts` 的 `MapPackageRoot` 未声明 floors 字段；index 页的 `bootstrap()` 是现成的加载/失败范式。

真实数据基线（已抽样验证）：F1 墙 2436 根（全为 2 点 LineString，总长 4513m）/ F2 2006 根；坐标局部米制 F1 x∈[-84.3,13.1]、y∈[-89.4,39.5]（97×129m）；mock 点 (-37,-48) 在合法 1 层区域内。

## 实施步骤（分 3 步提交，提交信息英文、注释中文）

### 步骤 1：类型出口 + 可测核心 + 单测

1. **[typings/api.d.ts](file:///d:/code/pathai/client/typings/api.d.ts)（L249 附近）补渲染子集类型**（该文件是后端 schema 的手工镜像，注明改动原因）：
   - `MapWallFeature`（LineString coordinates + properties.thickness）
   - `MapFloorData`（displayName/level/geometry.walls 客户端渲染子集）
   - `MapPackageRoot` 加必填 `floors: Record<string, MapFloorData>`（仓库内无字面量构造，仅 JSON.parse 强转，必填安全）
2. **[map-service.ts](file:///d:/code/pathai/client/core/map/map-service.ts) 新增出口**：`get floors()`（与 places/beacons getter 同风格，返回 `this.pkg?.floors ?? {}`）
3. **新核心模块** `client/core/map/`（纯逻辑零 wx 依赖，供单测）：
   - `floor-map-viewport.ts` — 类 `FloorMapViewport`：`(bounds, viewW, viewH, padding)` 构造；`fit()`、`toScreen(x,y)`（y 轴翻转：`x*scale+offX, -y*scale+offY`）、`panBy(dx,dy)`（限制 bounds 与视口相交，不许拖丢）、`zoomAt(anchorX, anchorY, factor)`（锚点 world 坐标不变 + scale clamp [fit, fit×8]）、`setFloor(floorId)`（保持 scale/offset，clamp 到新层 minScale）
   - `floor-geometry.ts` — 纯函数 `flattenWalls(floors, floorId)`（预扁平化 Float32Array，避免每帧遍历 Feature）、`floorBounds(...)`、`floorsSortedByLevel(...)`
4. **单测** `client/tests/floor-map-viewport.test.ts`——**按项目约束用真实地图数据**（`tests/fixtures/real-map.ts`）：fit 居中、toScreen 往返、zoomAt 锚点不变、scale clamp、pan 边界、setFloor 保持视口、flattenWalls 段数=真实墙点数

验证：`cd client && npx tsc --noEmit && npx jest`

### 步骤 2：地图页三件套 + 首屏注册

1. **`client/pages/map/`（map.ts/wxml/wxss/json）**：
   - wxml：`view.map-stage`（100vh、overflow:hidden）包 `<canvas type="2d" id="mapCanvas">`，`catchtouchstart/move/end/cancel` 绑在 wrapper（规避 canvas 事件类型差异，clientX/Y 经缓存 rect 换算画布坐标）；覆盖层：加载文案、右侧楼层药丸 `1F/2F`（fixed 竖排、选中态 #2563eb）、右下「回到全图」、底部「进入语音首页」按钮、loadError+重试
   - ts：`onLoad` → `bootstrap()`（照抄 [index.ts](file:///d:/code/pathai/client/pages/index/index.ts) 范式：`ensureLoaded()` → `mapReady`/`describeError` 失败文案）；`onReady` → SelectorQuery 取 canvas 节点+尺寸+rect（`res.node` 一处受控 cast 为唯一类型边界，注明）+ dpr 适配（`setTransform(dpr,0,0,dpr,0,0)` 后按 CSS 像素作画）；两 ready 齐 → `fit + draw`；手势处理（单指 pan、双指距离比 zoomAt+中点 pan、1→2 指重置基线防跳变）；`MOCK_POSITION = {floorId:'1', x:-37, y:-48}` 模块常量
   - 绘制 `draw()`：背景 → 墙体（全部 2 点子路径单 `beginPath` 后**一次 stroke**，`lineWidth=max(1, thickness*scale)`）→ 信标小点 → mock 蓝点（仅 1F）→ 楼层角标用 wxml 覆盖层 `{{floorLabel}}`（非 fillText）
   - 重绘节流：touchmove 只改 viewport 置 dirty，用 `canvas.requestAnimationFrame` 合并每帧最多一次；静止零重绘
2. **[app.json](file:///d:/code/pathai/client/app.json)**：`pages[0]` 注册 `pages/map/map`

验证：tsc + jest + 微信工具**清缓存重编译**（新增文件的 TS 插件已知坑）

### 步骤 3：首屏收尾 + 验收

- 加载/失败/重试态、回语音首页（`wx.navigateTo` 带 fail 日志，照 `onGoSettings` 范式，保留返回栈可回退）、回到全图按钮
- 手动验收清单（微信工具 + 真机）：
  1. 冷启动进 map 页「正在加载地图…」→ 首绘全层
  2. 断网/错误地址重启 → loadError + 重试可用
  3. 1F 显示 (-37,-48) 蓝点，切 2F 蓝点消失、楼层角标切换
  4. 单指拖动、双指缩放流畅，到边界卡住不丢图
  5. 切楼层视口保持，「回到全图」复位
  6. 进语音首页按钮 → index 的 PTT/设置正常，系统返回可回地图
  7. index 的 ensureLoaded 单例秒回，无回归

## 风险与预案

- 类型：仅 SelectorQuery fields 回调 node 处一次受控 cast（Canvas/RenderingContext2D 本身类型齐全，勿整体 any）
- 真机性能：几千 path 单 stroke 预计可行；若卡，预案=每层离屏位图缓存（createOffscreenCanvas）+ AABB 裁剪，首版不做
- 内存：新增扁平化每层约 20KB Float32Array，不复制 Feature 结构

## 验证命令

```powershell
cd d:\code\pathai\client
npx tsc --noEmit
npx jest
# 微信开发者工具：工具→清除缓存→全部清除 → 重新编译 → 按验收清单走
```
