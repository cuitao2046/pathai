# Spec：阶段一标定闭环（对数等距测量页 + 定位常量计算控制台）

> 版本 0.1.0（评审稿）
> 日期：2026-11-09
> 状态：待评审；**本轮只定义需求与设计，不改实现代码**
> 变更：初稿。合并「对数等距测量页」与「计算子菜单」两个需求为一份 spec；按 2026-11-09 用户已拍板的三项决定重写：① 计算子菜单**不做鉴权**；② **指纹网格重算正式纳入 apply 后链路**；③ 对数等距页按独立页方案执行（独立 schema、`deployable: false`、单信标、入口为首页采集连体候选第三项、安装高度可调、30/60/120 秒）。
> 关联：
> - `specs/field-collection-integration/spec.md`（静态标定页 / 网格指纹页的既有契约）
> - `docs/33-标定链路说明-采集-拟合-参数落地.md`（采集 → 拟合 → 参数落地）
> - `src/tools/fit_location_params.py`（A/n 拟合、阈值推荐、回放）
> - `src/tools/generate_fingerprint_grid.py`（指纹网格生成器）
> - `backend/pathai_backend/config.py`、`routers/location.py`、`map_repository.py`、`models.py`

---

## 0. 评审摘要

**一句话**：本 spec 把“阶段一单信标对数等距采集 → 拟合 A/n → 阶段二静态标定 → 计算控制台审核 → apply 生效（参数 + 指纹网格）→ 客户端刷新”串成一条可评审、可回滚的闭环；阶段一数据只用于评审与工程决策，不直接进入生产参数。

**用户已拍板、本文不再列为待定**：

| # | 决定 | 日期 |
|---|---|---|
| 1 | 计算子菜单**不做鉴权**：不引入 admin 登录、Bearer token、密码二次确认 | 2026-11-09 |
| 2 | 参数冻结后，**指纹网格需要重算**，并纳入 apply 后链路 | 2026-11-09 |
| 3 | 对数等距页按上一版建议执行：独立页、独立 schema、`deployable: false`、单信标、入口为首页采集连体候选第三项、安装高度可调、30/60/120 秒 | 2026-11-09 |

**关键结论**：

1. 对数等距页是**阶段一专用页**，采集的是“单信标 + 已知水平距离”的 RSSI 样本；它不参与导航定位，也不产生可部署参数。
2. 阶段一拟合结果只用于**评审部署台账、评估覆盖、给阶段二提供基线**；阶段二的静态标定才是生产 A/n 的来源。
3. 计算子菜单是**运维控制台**，不是采集页；它管理“样本 → 计算 → 审核 → apply → 回滚”的完整生命周期。
4. apply 是**参数与网格的成对原子提交**：网格重算失败则参数不生效；不允许“参数已生效、网格稍后补”的半状态。
5. 网格重算使用**实测 A/n**，但墙/门衰减、`-85 dBm` 与 `≥3 信标` 覆盖率口径保持工程常量，不随拟合自动改变；理由见 §7.1。
6. 网格产物到达客户端采用**混合方案**：内置设计层不变，服务端只下发“标定覆盖层”，客户端按 `gridVersion` 拉取、缓存、校验，失败回退内置快照。三种方案对比见 §7.3。

**本轮不做**：不改定位算法本身；不改地图包生产流程；不把 `fingerprint-collector` 独立工具纳入闭环；不做 public 公网部署；不做多租户/多站点权限。

---

## 1. 背景与目标

### 1.1 现状

- 阶段一只有 1 枚信标，无法三边定位；现有静态标定页按“地图点位 + 多信标观测”设计，阶段一直接复用需要绕行（隐藏预置点、外部过滤空会话、单信标回放全 lost）。
- A/n 目前靠“人工跑 `fit_location_params.py` + 手改 `PATHAI_LOCATION_*` 环境变量 + 重启”，没有审核界面、没有版本记录、没有回滚入口。
- 指纹网格由 `generate_fingerprint_grid.py` 离线生成，内置在小程序包里；A/n 变化后网格的 `collectable` 标记会失真，但线上包不会自动更新。
- 客户端内置 `BEACON_RSSI_AT_1M_DBM`（`client/config/env.ts`）只用于 mock 与指纹 `accuracy` 换算，不参与真实定位；apply 不能直接改包内常量。

### 1.2 目标

- 提供一个**阶段一专用**的对数等距测量页，现场按 10 个水平距离档位采集单信标 RSSI，导出独立 schema。
- 提供一个**计算控制台**，让“文件 ready → 上传 → 计算 → 审核 → apply → 回滚”在界面内完成，并明确展示冻结常量与网格影响。
- 参数生效后**不重启服务**，多 worker 在秒级内收敛；客户端能读到新 `paramsVersion` 与新的网格覆盖层。
- 所有 apply 可追溯、可回滚；网格重算与参数生效成对发生。

### 1.3 非目标

- 不做鉴权、登录、token、密码二次确认（用户明确决定，见 §6.9）。
- 不替代阶段二静态标定；阶段一结果不可直接 apply。
- 不改变 `MIN_BEACONS_FOR_TRILATERATION = 3`、定位解算公式、播报与导航交互。
- 不改变地图包发布/回滚流程；地图包仍由现有 admin 地图接口管理。
- 不把 `fingerprint-collector/` 独立小程序改造成在线拉取；它本轮仍走发版路径。

---

## 2. 术语与总体链路

### 2.1 术语

| 术语 | 含义 |
|---|---|
| 水平距离 `h` | 手机在楼层平面内到信标垂直投影点的水平距离（米） |
| 斜距 `d` | `√(h² + (installHeightM − phoneHeightM)²)`；手机高固定 1.2 m |
| A | 对数距离模型的 1 m 参考 RSSI（回归截距） |
| n | 路径损耗指数（回归斜率） |
| 阶段一 | 只部署 1 枚信标，做对数等距采集，产出 A/n 供评审 |
| 阶段二 | 按最终台账全量部署，用静态标定页产出可部署 A/n 与阈值 |
| 冻结常量 | apply 时写入运行时、定位接口立即使用的 A/n/残差阈值/版本号 |
| 网格基线 | 内置在设计层、由 `FINGERPRINT_GRID_VERSION` 标识的坐标与编号体系 |
| 网格覆盖层 | 服务端按新 A/n 重算后，对 `collectable` 等标记的增量覆盖数据 |
| `deployable` | 样本是否允许进入 apply；阶段一恒为 `false` |

### 2.2 总体链路

```
阶段一（单信标）
  对数等距页 → 导出 positioning_calibration_logdist（deployable=false）
    → fit_location_params.py（仅拟合 A/n）
    → 评审：部署台账是否冗余、覆盖是否足够、阶段二布点是否调整

阶段二（全量信标）
  静态标定页 → 导出 positioning_calibration_static（deployable=true）
    → 计算控制台：上传 → 服务端计算（A/n + 阈值 + 拟合质量）
    → 审核：结果、阈值、覆盖影响
    → apply：参数版本 + 指纹网格重算（原子提交）
    → 运行时热生效 + 客户端拉取 paramsVersion / 网格覆盖层
    → 回滚：参数与网格成对回退
```

### 2.3 参数生命周期

```
draft（上传样本）
  → computed（计算完成，含网格预览）
  → applied（参数 + 网格同时生效，生成新的 paramsVersion/gridVersion）
  → superseded（被更新版本取代）
  → rolled_back（成对回退到上一对版本）
```

- 每个 `paramsVersion` 只对应一个 `gridVersion`；不允许单独替换其中一个。
- 版本行不可变；任何“修改”都是新增一行并把 active 指针指向它。

---

## 3. 需求编号

### 3.1 Part A：对数等距测量页

| 编号 | 需求 | 优先级 | 章节 |
|---|---|---|---|
| R-A-01 | 首页采集连体候选新增第三项「对数等距」，进入独立页 `pages/collect/logdist/logdist` | P0 | §5.1 |
| R-A-02 | 页面按 10 个水平距离档位组织，5×2 平铺；档位固定为 0/1/1.5/2/3/4.5/6.5/9/13/18 m | P0 | §5.2 |
| R-A-03 | 安装高度可调，默认 2.5 m；手机高固定 1.2 m；所有档位实时显示对应斜距 | P0 | §5.2 |
| R-A-04 | 自动锁定唯一已解析信标；多信标时必须人工选择；锁定后本次页面会话不再切换 | P0 | §5.5 |
| R-A-05 | 每档支持多次会话采集，倒计时 30/60/120 秒；统计口径与静态标定页一致 | P0 | §5.5 |
| R-A-06 | 本地存储键 `col:logdist:v1`；只持久化“有会话”的档位 | P0 | §5.4 |
| R-A-07 | 导出独立 schema `positioning_calibration_logdist`，`deployable: false` | P0 | §5.6 |
| R-A-08 | `fit_location_params.py` 按 `type` 分派；对数等距样本只产 A/n，不产可部署阈值 | P0 | §5.7 |
| R-A-09 | 明确与阶段二静态标定的数据衔接、安装高度口径、禁止直接 apply | P0 | §5.8 |

### 3.2 Part B：计算控制台

| 编号 | 需求 | 优先级 | 章节 |
|---|---|---|---|
| R-B-01 | 设置页新增「定位常量计算」入口，进入 `pages/calc/calc` | P0 | §6.1 |
| R-B-02 | 展示冻结常量清单、当前生效版本、拟 apply 的新值 | P0 | §6.2 |
| R-B-03 | 展示所需文件与 ready 状态；客户端管“存在”，服务端管“正确” | P0 | §6.3 |
| R-B-04 | 文件不全时上传/计算不可点；文件齐但未上传时仅上传可点；上传成功后计算可点 | P0 | §6.5 |
| R-B-05 | 服务端基于上传样本计算 A/n、阈值、拟合质量与网格预览 | P0 | §6.4、§6.6 |
| R-B-06 | 结果页展示拟合质量、阈值建议、警告、网格可采集点数变化 | P0 | §6.6 |
| R-B-07 | apply 二次确认；参数与网格成对提交；失败不改变线上 | P0 | §6.7、§7.4 |
| R-B-08 | 版本历史、成对回滚、审计记录 | P0 | §6.8 |
| R-B-09 | 不做鉴权，但保留 apply 二次确认、版本 CAS、局域网限定、限流、schema 校验、审计 | P0 | §6.9 |
| R-B-10 | apply 后服务端热生效，客户端刷新 `paramsVersion` 与网格覆盖层 | P0 | §6.7 |
| R-B-11 | 指纹网格重算纳入 apply 链路，不允许半状态 | P0 | §7 |
| R-B-12 | 网格产物采用混合交付：内置设计层 + 运行时覆盖层 + 兜底快照 | P0 | §7.3 |
| R-B-13 | 旧指纹记录不因参数变化失效；新增 `gridVersion`/`paramsVersion` | P0 | §7.5 |
| R-B-14 | 阶段一样本可在计算页查看结果，但 apply 恒不可用 | P1 | §6.5 |

### 3.3 网格重算专项

| 编号 | 需求 | 优先级 | 章节 |
|---|---|---|---|
| R-G-01 | 重算使用 apply 的实测全局 A/n；墙/门衰减与覆盖率阈值保持工程常量 | P0 | §7.1 |
| R-G-02 | 服务端以库方式调用同一网格生成器，禁止复制算法 | P0 | §7.2 |
| R-G-03 | 重算在 staging 完成并校验，成功后与参数同事务提交 | P0 | §7.2、§7.4 |
| R-G-04 | 产物包含 `grid.json`、`overlay.json`、F2 清单、manifest 与输入哈希 | P0 | §7.2 |
| R-G-05 | 客户端按 `gridVersion` 合并覆盖层；基线不匹配时拒绝合并并回退内置快照 | P0 | §7.3 |
| R-G-06 | `fpId` 由坐标派生，A/n 变化不改编号；旧记录继续有效 | P0 | §7.5 |

---

# Part A：对数等距测量页

## 5. 对数等距测量页（`pages/collect/logdist`）

### 5.1 定位与入口（R-A-01）

- 页面是**阶段一专用工具**，不是静态标定页的替代，也不进入导航会话。
- 入口沿用 `specs/field-collection-integration/spec.md` 的「采集」连体候选：
  - 现有两项：静态标定 / 网格指纹；
  - 新增第三项：**对数等距**；
  - 三项与触发键零间隙、1rpx 单线分隔、末项保留右圆角；点按层使用 `<view aria-role="button">`。
- 仍受设置页「现场采集工具」开关控制；开关关闭或导航会话进行中时整颗隐藏。
- 新增页面注册到 `client/app.json` 主包；页面代码与数据资源需控制体积，不引入新的大图资源。

### 5.2 页面结构（R-A-02、R-A-03）

自上而下：

1. **顶部状态条**
   - 左：页面标题「对数等距」；
   - 右：当前锁定信标短号；未锁定时显示「等待信标」。
2. **安装高度条**
   - 显示「安装高度 2.5 m」+ 增减控件；
   - 可调范围建议 2.0–4.0 m，步进 0.1 m；
   - 修改后即时重算所有档位的斜距，并影响后续会话；已采会话保留采集时的 `installHeightM` 快照。
3. **距离档位选择器（5×2）**
   - 10 个档位：`0 / 1 / 1.5 / 2 / 3 / 4.5 / 6.5 / 9 / 13 / 18` m；
   - 每格显示：水平距离、对应斜距、已采会话数角标；
   - 已采档位使用实心样式，未采档位空心样式；
   - 单格建议 130.8rpx × 88rpx、间距 12rpx，与现有采集页按钮体系一致。
4. **底部居中 HUD**
   - 当前档位名 + 倒计时；
   - 采集按钮（开始/结束）；
   - 次数胶囊：本档已完成会话数 / 建议 3 次；
   - 采集期间显示逐信标实时中位数（本页只有 1 枚锁定信标，因此为单行）。
5. **右下按钮列**
   - 清理：清空本页记录（二次确认）；
   - 导出：导出当前全部已采档位。

页面不渲染地图、不显示定位蓝点、不显示估计位置。原因：单信标无法三边定位，显示估计会误导现场。

### 5.3 交互与状态机（R-A-04、R-A-05）

状态：

| 状态 | 进入条件 | 可执行动作 | 退出 |
|---|---|---|---|
| `scanning` | 页面进入，尚未锁定信标 | 等待；可调安装高度 | 解析到信标 → `beacon_pending` 或 `ready` |
| `beacon_pending` | 同时解析到 ≥2 枚信标 | 从候选列表选择 1 枚 | 选择后 → `ready` |
| `ready` | 已锁定 1 枚信标，未采集 | 选档位、开始采集、导出、清理 | 开始 → `capturing` |
| `capturing` | 倒计时进行中 | 结束采集（提前结束需确认） | 倒计时结束/手动结束 → `ready` |
| `paused` | 切后台、锁屏、蓝牙关闭 | 恢复采集 | 回前台/蓝牙恢复 → `capturing` |
| `beacon_lost` | 采集中锁定信标连续 10 s 无样本 | 继续等待/结束本次 | 恢复 → `capturing`；结束 → `ready` |
| `empty` | 无任何已采会话 | 等待/调高度 | 首次采集完成 → `ready` |

交互规则：

- 进入页面即启动 BLE 扫描；离开页面停止扫描；不与导航会话并发。
- **单信标锁定**：
  - 解析到 1 枚 → 自动锁定；
  - 解析到多枚 → 暂停自动锁定，弹出候选列表（短号 + 最近中位数），必须人工选择；选择后本次页面会话不再自动切换；
  - 解析到 0 枚 → 保持 `scanning`，提示「请靠近信标或检查台账」。
- 倒计时默认 60 s，可在页面设置区切换 30/60/120 s；与静态标定页保持同一套倒计时与振动语义。
- 振动：开始短振一次，结束长振一次；暂停/恢复不额外振动。
- 采集期间不因定位质量、可见信标数不足而中断；本页不订阅 `LocationPipeline`。
- 锁定信标消失时只提示，不自动改锁其他信标；避免同一档位混入不同信标的样本。

### 5.4 数据模型与存储（R-A-06）

存储键：`col:logdist:v1`（与静态标定的 `col:static:v1`、指纹的存储键完全隔离）。

```json
{
  "schemaVersion": 1,
  "installHeightM": 2.5,
  "phoneHeightM": 1.2,
  "beacon": {
    "beaconId": "BK-01-007",
    "uuid": "xxxx",
    "major": 1,
    "minor": 7
  },
  "stops": {
    "0": {
      "horizontalDistanceM": 0,
      "sessions": [
        {
          "startedAt": "2026-11-09T09:12:00Z",
          "durationS": 60,
          "captureIndex": 1,
          "installHeightM": 2.5,
          "observations": [
            {
              "beaconId": "BK-01-007",
              "n": 28,
              "rssiMedian": -62,
              "rssiMin": -71,
              "rssiMax": -55,
              "rssiStd": 3.8,
              "truthDistanceM": 1.3
            }
          ]
        }
      ]
    }
  }
}
```

规则：

- **只持久化有会话的档位**；空档位不落盘，避免导出时出现空会话。
- 会话追加制：同一档位多次采集是独立会话，不覆盖、不平均；`captureIndex` 仅展示。
- 每条会话记录采集时的 `installHeightM` 快照；页面级 `installHeightM` 只影响后续会话。
- 锁定信标信息写入页面级 `beacon`；若现场更换信标，必须清理本页记录后重新开始，禁止在同一份记录里混用两个 `beaconId`。
- 采集完成后立即落盘；杀进程不丢已完成会话。
- 清理动作清空 `col:logdist:v1`，需二次确认。

### 5.5 采集逻辑（R-A-05）

- 复用现有 BLE 扫描与统计核心：
  - 2 s 扫描窗口；
  - RSSI 截断 -100 ~ -20 dBm；
  - 逐信标中位数（偶数取中间两值平均后取整）；
  - 输出 `n / rssiMedian / rssiMin / rssiMax / rssiStd`。
- 与静态标定页的差异只有一个：**真值距离不来自地图坐标**，而是由档位水平距离与安装高度直接计算：
  - `d = √(h² + (installHeightM − 1.2)²)`；
  - `h = 0` 时 `d = 1.3 m`（安装 2.5 m）；
  - `h = 18` 时 `d ≈ 18.05 m`。
- 每个档位的斜距在页面上实时显示，避免现场误以为“0 m 就是 0 m 距离”。
- 采样期间只统计锁定信标；其他信标的样本丢弃，不写入记录。
- 若一次会话没有任何锁定信标样本，则不生成会话，提示「未收到样本，请检查信标」。
- 不记录定位估计快照（`estimateAtStart/estimateAtEnd` 不适用于单信标）。

斜距参考表（安装 2.5 m / 手机 1.2 m）：

| 水平 `h` (m) | 斜距 `d` (m) | `log10(d)` |
|---:|---:|---:|
| 0 | 1.30 | 0.114 |
| 1 | 1.64 | 0.215 |
| 1.5 | 1.99 | 0.298 |
| 2 | 2.39 | 0.378 |
| 3 | 3.27 | 0.515 |
| 4.5 | 4.68 | 0.670 |
| 6.5 | 6.63 | 0.822 |
| 9 | 9.09 | 0.959 |
| 13 | 13.07 | 1.116 |
| 18 | 18.05 | 1.256 |

### 5.6 导出 schema（R-A-07）

文件名：`logdist_samples_<timestamp>.json`；写入 `USER_DATA_PATH`，同时提供剪贴板兜底与微信分享。

```json
{
  "schemaVersion": "1.0",
  "type": "positioning_calibration_logdist",
  "deployable": false,
  "exportedAt": "2026-11-09T10:30:00Z",
  "appVersion": "1.0.0",
  "operator": "张三",
  "device": "iPhone 15",
  "mapVersion": "2026-11-08.2",
  "beacon": {
    "beaconId": "BK-01-007",
    "uuid": "xxxx",
    "major": 1,
    "minor": 7
  },
  "installHeightM": 2.5,
  "phoneHeightM": 1.2,
  "model": {
    "form": "log_distance",
    "expression": "RSSI = A - 10 * n * log10(d)"
  },
  "stops": [
    {
      "stopId": "LD-00",
      "horizontalDistanceM": 0,
      "slantDistanceM": 1.3,
      "sessions": [
        {
          "startedAt": "2026-11-09T09:12:00Z",
          "durationS": 60,
          "captureIndex": 1,
          "installHeightM": 2.5,
          "observations": [
            {
              "beaconId": "BK-01-007",
              "n": 28,
              "rssiMedian": -62,
              "rssiMin": -71,
              "rssiMax": -55,
              "rssiStd": 3.8,
              "truthDistanceM": 1.3
            }
          ]
        }
      ]
    }
  ],
  "summary": {
    "stopCount": 10,
    "sessionCount": 30,
    "observationCount": 840
  }
}
```

与静态标定导出的差异：

| 维度 | 静态标定 | 对数等距 |
|---|---|---|
| `type` | `positioning_calibration_static` | `positioning_calibration_logdist` |
| 组织结构 | `points[]`（地图点位） | `stops[]`（水平距离档位） |
| 真值距离来源 | 地图坐标 + 安装高度 | 档位水平距离 + 安装高度 |
| 定位快照 | 有 `estimateAtStart/estimateAtEnd` | 无 |
| 可部署 | `deployable` 由阶段二样本决定 | 恒 `false` |
| 预置空点 | 会导出未采样预置点 | 不导出空档位 |

导出前检查：

- 至少 3 个不同档位有会话，否则提示「档位不足，A/n 拟合不稳定」，但仍允许导出（结果会带警告）。
- 显示「已采档位 / 总会话数 / 总观测数」。
- `mapVersion` 从已加载地图包取；取不到时写 `null` 并在导出页提示。

### 5.7 与 `fit_location_params.py` 的关系（R-A-08）

- 脚本入口增加按 `type` 分派：
  - `positioning_calibration_static` → 现有逻辑：A/n、分楼层分组、回放、阈值推荐、完整参数文档；
  - `positioning_calibration_logdist` → 只做 A/n 拟合，跳过回放与阈值推荐。
- 对数等距拟合：
  - 观测对：每条会话的每个观测 `(d = truthDistanceM, rssi = rssiMedian)`；
  - 模型：`RSSI = A − 10n·log10(d)`；
  - 输出：`A`、`n`、`R²`、`RMSE`、`pairCount`、逐档残差、警告。
- 输出文档类型：`positioning_calibration_logdist_result`，`deployable: false`，明确标注「仅供评审，不可 apply」。
- 敏感性：
  - 默认使用全部档位；
  - 同时输出剔除 `h = 0` 档的结果；
  - 若两者 `A` 差 > 2 dB 或 `n` 差 > 0.3，标记「需复核 0 m 档」。
- 拟合稳定性门槛（不通过则结果带 `warnings`，不阻断导出）：
  - 有效档位数 ≥ 3；
  - 观测对数 ≥ 9；
  - `n` 落在 1.5–6.0；
  - `A` 落在 −80 ~ −30 dBm；
  - `R²` ≥ 0.6。
- CLI 仍支持 `--samples` 与 `--out`；对数等距模式不需要 `--ledger`/`--map-package`，传了也忽略。
- 现有静态标定路径的行为、输出 schema、阈值推荐保持不变。

### 5.8 与阶段二静态标定的衔接（R-A-09）

- **数据不合并**：阶段一与阶段二是两份独立导出、两次独立拟合；不要把对数等距样本混入静态标定样本，否则会改变阶段二阈值推荐的回放口径。
- **安装高度口径必须一致**：两页都使用“实测安装高度”，默认 2.5 m；阶段一页面修改的高度写入每条会话，阶段二静态标定页使用同一信标的安装高度。若现场实测不是 2.5 m，两页都必须改，否则 A/n 会系统性偏移。
- **阶段一结果的使用方式**：
  1. 评审部署台账：用阶段一 A/n 估算“在计划安装位置上，哪些点能收到 ≥3 枚信标、RSSI 是否达到可用阈值”；
  2. 判断冗余信标：覆盖重叠过高的信标可在阶段二部署前移除；
  3. 给阶段二一个基线预期：阶段二拟合出的 A/n 与阶段一差异过大时，优先检查安装高度、信标朝向、墙体材质或台账坐标。
- **阶段一结果不能直接 apply**：
  - 单信标没有多信标回放，`recommend_thresholds` 会退化为兜底值；
  - `fit_location_params.py` 的单信标回放全部 lost，不能据此判断覆盖率；
  - 因此计算控制台对 `deployable: false` 的样本只展示结果，apply 永久禁用。
- **阶段二完成后**：静态标定导出 → 计算控制台 → apply；apply 触发的网格重算使用阶段二的实测 A/n，不使用阶段一的 A/n。

### 5.9 边界与异常

| 场景 | 行为 |
|---|---|
| 页面进入时蓝牙关闭 | 提示开启蓝牙，停留在 `scanning`，不计时 |
| 同时解析到多枚信标 | 必须人工选择；未选择前不能开始采集 |
| 采集中锁定信标消失 | 提示「信标丢失」，倒计时继续；结束后若样本为 0 则不写会话 |
| 采集中切后台/锁屏 | 暂停倒计时；回前台继续；不静默作废 |
| 修改安装高度时已有会话 | 已采会话保留原高度快照；页面显示「高度已变更，建议新档位重新采集」 |
| 存储超限 | 与静态标定一致，导出前提示；不自动删除 |
| 地图包版本缺失 | 导出 `mapVersion: null` + 警告；不阻断导出 |
| 单档位只采 1 次 | 允许；导出摘要提示「建议每档 ≥3 次」 |

---

# Part B：定位常量计算控制台

## 6. 计算控制台（`pages/calc/calc`）

### 6.1 入口与页面结构（R-B-01）

- 设置页新增 section「定位常量计算」：
  - 展示当前 `paramsVersion` / `gridVersion`（只读）；
  - 入口按钮「计算定位常量」→ `pages/calc/calc`。
- 入口不受「现场采集工具」开关控制；它属于运维功能，默认可见（用户已决定不做鉴权）。
- 页面结构自上而下：
  1. **当前生效状态卡**：`paramsVersion`、`gridVersion`、A/n、两个残差阈值、生效时间、操作人。
  2. **冻结常量卡**：拟 apply 的新值与当前值并排对比，差异高亮。
  3. **输入文件卡**：文件列表 + ready 状态 + 文件名 + 来源 + 操作（上传/重新上传）。
  4. **操作区**：上传样本 / 开始计算 / Apply / 回滚。
  5. **计算结果卡**：A/n、R²、RMSE、观测对数、分楼层/分档位质量、阈值建议、警告。
  6. **网格影响卡**：`baseGridVersion`、拟生成 `gridVersion`、各楼层可采集点数变化、新增/移除点数量、预览失败原因。
  7. **版本历史**：最近 N 对 `paramsVersion + gridVersion`，含操作人、时间、样本哈希前 8 位、状态。
- 页面所有状态都从服务端读取；客户端不本地计算 A/n，不本地重算网格。

### 6.2 冻结常量清单（R-B-02）

apply 时冻结并写入运行时的常量：

| 常量 | 环境变量 | 当前默认 | 来源 | apply 是否更新 |
|---|---|---:|---|---|
| `location_rssi_at_1m`（A） | `PATHAI_LOCATION_RSSI_AT_1M` | -71.0 | 拟合截距 | 是 |
| `location_path_loss_n`（n） | `PATHAI_LOCATION_PATH_LOSS_N` | 2.0 | 拟合斜率 | 是 |
| `location_residual_reliable_m` | `PATHAI_LOCATION_RESIDUAL_RELIABLE_M` | 1.5 | `recommend_thresholds` | 是 |
| `location_residual_degraded_m` | `PATHAI_LOCATION_RESIDUAL_DEGRADED_M` | 4.0 | `recommend_thresholds` | 是 |
| `location_params_version` | `PATHAI_LOCATION_PARAMS_VERSION` | `unset` | 服务端生成 | 是 |
| `gridVersion` | 无 | `11.1.0` | 网格重算 | 是（派生） |

关联但不冻结的常量：

| 常量 | 当前值 | 本轮是否随 apply 变化 | 原因 |
|---|---:|---|---|
| `MIN_BEACONS_FOR_TRILATERATION` | 3 | 否 | 定位算法硬约束，不是拟合产物 |
| 墙衰减（partition/brick/concrete） | 8/12/15 dB | 否 | 工程经验值，本轮无实测依据 |
| 门衰减 | 2 dB | 否 | 同上 |
| 可采集 RSSI 阈值 | -85 dBm | 否 | 覆盖率政策口径，需保持跨版本可比 |
| 可采集最少信标数 | 3 | 否 | 与解算约束一致 |
| 手机高度 | 1.2 m | 否 | 现场统一假设 |
| 默认安装高度 | 2.5 m | 否 | 仅缺台账时的兜底 |

> 分楼层 A/n：`fit_location_params.py` 可能产出 `groups[]`，但当前后端解算只有全局 `LocationParams`，没有分楼层参数注入路径。本 spec 规定：**apply 只使用全局 A/n；分楼层组仅展示，不参与 apply 与网格重算**，避免“网格按楼层、解算按全局”的口径分裂。见 D19。

### 6.3 所需文件与 ready 判定（R-B-03）

#### 6.3.1 文件清单

| 文件 | 必需 | 来源 | 客户端判定 | 服务端判定 |
|---|---|---|---|---|
| 标定样本 JSON | 是 | 用户上传（`wx.chooseMessageFile`） | 存在、可解析、`type`/`schemaVersion` 已知、`deployable` 字段存在 | 完整 schema、会话非空、距离/高度合法、信标可解析、`deployable: true` 才可 apply |
| 地图包 | 是 | 按样本 `mapVersion` 从 `MapRepository` 解析 | 样本中 `mapVersion` 非空 | 包存在、可解析、楼层/信标与样本一致 |
| 部署台账 `ble_deployment.json` | 是（网格重算输入） | 服务端版本化基线 | 不判定 | 存在、与 `mapVersion` 对应、信标 ID 可解析 |
| 路线定义 `beacon_routes.json` | 是（网格重算输入） | 服务端版本化基线 | 不判定 | 存在、与 `mapVersion` 对应 |
| 网格基线 `geojson` | 是（网格重算输入） | 服务端版本化基线 | 不判定 | 存在、可解析 |
| 虚拟信标 `simulated_beacons.json` | 否 | 服务端版本化基线 | 不判定 | 存在则校验，缺失则网格无 `collectableWithSim` |
| 操作人 | 是 | 设置页「校准操作人」 | 非空 | 写入审计；不校验身份 |
| 样本哈希 | 派生 | 客户端上传后由服务端计算 | 不判定 | 计算/apply 时校验一致 |

#### 6.3.2 判定分工

- **客户端只管“存在”**：
  - 用户是否选了文件、文件名、大小、是否可解析为 JSON、顶层 `type` 是否认识、`deployable` 是否存在；
  - 不做 A/n 合法性、跨楼层污染、信标存在性等业务判断。
- **服务端只管“正确”**：
  - schema 全量校验；
  - 会话/观测非空；
  - `truthDistanceM` 与 `installHeightM` 自洽；
  - 信标 ID 能在对应地图包/台账中解析；
  - 样本 `mapVersion` 对应的地图包/基线可用；
  - `deployable: false` 的样本可计算但不可 apply；
  - 拟合质量门槛（见 §6.6）不通过时返回 `warnings`/`blocked`。
- ready 状态是“客户端存在 + 服务端正确”的合成结果；服务端不可达时只显示客户端存在性，并标记「未校验」。

#### 6.3.3 ready 状态

| 状态 | 含义 | 上传按钮 | 计算按钮 |
|---|---|---|---|
| `local_missing` | 未选样本文件 | 可点 | 不可点 |
| `local_ready` | 文件可解析，尚未上传 | 可点（重新上传） | 不可点 |
| `uploading` | 上传中 | 不可点 | 不可点 |
| `server_invalid` | 服务端校验失败 | 可点 | 不可点 |
| `server_ready` | 服务端校验通过，可计算 | 可点 | 可点 |
| `server_unreachable` | 服务端不可达 | 可点 | 不可点 |

### 6.4 接口设计（R-B-05）

所有接口挂在 `/api/v1/calibration` 与 `/api/v1/config` 下，**无鉴权**（见 §6.9）。

| 方法 | 路径 | 用途 | 关键返回 |
|---|---|---|---|
| `POST` | `/api/v1/calibration/samples` | 上传样本 JSON | `sampleId`、`hash`、`type`、`deployable`、`mapVersion`、`validation` |
| `GET` | `/api/v1/calibration/readiness?mapVersion=` | 查询文件/基线 ready | 逐项 `ready`、缺失原因、服务端基线版本 |
| `POST` | `/api/v1/calibration/runs` | 基于 `sampleId` 计算 | `runId`、`status`、`result`、`gridPreview` |
| `GET` | `/api/v1/calibration/runs/{runId}` | 查询计算/apply 状态 | `status`、`result`、`gridPreview`、`error` |
| `POST` | `/api/v1/calibration/runs/{runId}/apply` | 成对 apply | 新 `paramsVersion`、`gridVersion`、`appliedAt` |
| `GET` | `/api/v1/calibration/versions` | 版本历史 | 版本对列表、active 标记 |
| `POST` | `/api/v1/calibration/rollback` | 成对回滚 | 回滚后的 `paramsVersion`、`gridVersion` |
| `GET` | `/api/v1/config/location-params` | 客户端读取当前参数 | A/n、阈值、`paramsVersion`、`gridVersion` |
| `GET` | `/api/v1/config/fingerprint-grid/overlay?baseGridVersion=&paramsVersion=` | 客户端读取网格覆盖层 | `baseGridVersion`、`gridVersion`、`points[]`、`etag` |

约定：

- 上传与计算分离；`compute` 是异步 job，客户端轮询 `runs/{runId}`。
- `compute` 同时产出**参数结果 + 网格预览**；只有两者都成功，状态才是 `computed`。
- `apply` 只接受 `computed` 且未 stale 的 run；请求体必须带 `expectedParamsVersion`（CAS）与 `confirm: true`。
- 所有写接口返回 `requestId`，审计可关联。
- `GET /api/v1/config/*` 不得有副作用。
- 接口错误统一返回 `{code, message, details}`；客户端按 `code` 展示可操作提示，不直接透传堆栈。

### 6.5 按钮状态机（R-B-04、R-B-14）

状态：

| 状态 | 进入条件 | 上传 | 计算 | Apply | 回滚 | 页面提示 |
|---|---|---|---|---|---|---|
| `empty` | 未选文件 | 可点 | 不可点 | 不可点 | 可点 | 请上传标定样本 |
| `local_ready` | 文件可解析，未上传 | 可点 | 不可点 | 不可点 | 可点 | 文件已选择，待上传 |
| `uploading` | 上传中 | 不可点 | 不可点 | 不可点 | 可点 | 上传中 |
| `server_invalid` | 服务端校验失败 | 可点 | 不可点 | 不可点 | 可点 | 展示具体校验错误 |
| `server_ready` | 校验通过 | 可点 | 可点 | 不可点 | 可点 | 可以开始计算 |
| `computing` | 计算/网格预览中 | 不可点 | 不可点 | 不可点 | 可点 | 计算中，禁止重复提交 |
| `compute_failed` | 拟合或网格预览失败 | 可点 | 可点（重试） | 不可点 | 可点 | 展示失败环节与日志 ID |
| `computed` | 参数 + 网格预览成功 | 可点（重传后作废本次） | 不可点 | 可点 | 可点 | 请审核后 apply |
| `stale` | 上传新样本/服务端参数被他人更新 | 可点 | 重新计算 | 不可点 | 可点 | 本次结果已过期，请重新计算 |
| `applying` | apply 中 | 不可点 | 不可点 | 不可点 | 不可点 | 正在提交参数与网格 |
| `applied` | apply 成功 | 可点 | 不可点 | 不可点 | 可点 | 新版本已生效 |
| `blocked` | 样本 `deployable: false` | 可点 | 可点（仅看结果） | 永久不可点 | 可点 | 阶段一样本仅用于评审，不可 apply |

规则：

- 上传新文件立即作废旧样本与旧 run（服务端标记 `superseded`）。
- 计算中禁止重复点击；服务端按 `sampleId + inputHash` 幂等，重复请求返回同一 run。
- `computed` 不是“已生效”；只有 `applied` 才改变线上参数。
- `blocked` 下允许查看 A/n 与拟合质量，但不展示网格影响卡（阶段一不触发网格重算）。
- apply 成功后按钮变为「已生效」，历史列表新增一行；回滚按钮始终可用（无历史时禁用）。

### 6.6 结果展示（R-B-06）

参数结果卡：

- A、n、`R²`、`RMSE`、观测对数、档位数/点位数；
- 分楼层质量（仅静态样本）：每层 A/n/观测数/RMSE；
- 阈值建议：`residualReliableM`、`residualDegradedM`、`originMinConfidence`（如脚本输出）；
- 警告列表：
  - 观测对不足；
  - `n`/`A` 越界；
  - 分楼层提升不足，使用全局；
  - 单楼层样本为空；
  - 跨楼层信标污染风险；
  - 安装高度缺失或使用默认值；
  - 样本 `mapVersion` 非当前发布版本。

网格影响卡（仅 `deployable: true`）：

- `baseGridVersion`、拟生成 `gridVersion`；
- 各楼层可采集点数：当前 vs 拟生效；
- 新增可采集点 / 移除可采集点数量；
- 路线点覆盖变化（若基线有路线）；
- 预览失败时展示失败原因与是否可重试。

展示原则：

- 当前值与新值并排，差异用颜色和箭头标出；
- 所有数字保留与脚本输出一致的精度，不在客户端二次取整；
- 任何 `warnings` 都不隐藏；`blocked` 原因必须在首屏可见。

### 6.7 apply 生效语义（R-B-07、R-B-10）

apply 的前置条件：

1. run 状态为 `computed`，且未 stale；
2. 样本 `deployable: true`；
3. 客户端提交的 `expectedParamsVersion` 等于当前 active 版本（CAS）；
4. staging 中的网格产物存在，manifest 输入哈希与当前基线一致；
5. 操作人在设置页已填写（仅记录，不校验身份）。

apply 流程（服务端）：

1. **二次确认**：客户端弹窗展示旧值/新值、样本哈希前 8 位、拟生成 `gridVersion`、各楼层可采集点变化；用户确认后提交。
2. **CAS 检查**：不匹配返回 `409`，客户端提示「线上参数已被他人更新，请刷新后重新计算」。
3. **staging 校验**：校验网格产物完整性、`fpId` 唯一性、楼层齐全、覆盖层 schema、点数在合理范围。
4. **单事务提交**：新增 `location_param_versions` 行、新增 `fingerprint_grid_versions` 行、更新 active 指针、写审计事件；任一失败整体回滚。
5. **运行时热切换**：使运行时参数缓存失效；多 worker 在 TTL（≤2 s）内重新加载；不重启服务。
6. **客户端刷新**：apply 响应返回新版本；客户端清除网格覆盖层缓存，重新拉取 `location-params` 与 `overlay`；采集页顶部版本号更新。
7. **结果**：返回 `paramsVersion`、`gridVersion`、`appliedAt`、`requestId`。

失败语义：

| 失败点 | 线上状态 | 界面 | 可否重试 |
|---|---|---|---|
| CAS 失败 | 不变 | 提示版本冲突，要求刷新 | 刷新后重新计算/apply |
| staging 缺失/校验失败 | 不变 | 提示「网格产物失效，请重新计算」 | 是 |
| 网格重算失败（compute 阶段） | 不变 | `compute_failed` | 是 |
| DB 事务失败 | 不变 | 提示提交失败，展示日志 ID | 是 |
| 运行时缓存刷新失败 | 新版本已落库，旧 worker 最多 2 s 后收敛 | 提示「已生效，部分节点延迟」 | 不需要；观察版本 |
| 客户端离线 | 服务端已生效 | 客户端继续用缓存/内置快照 | 上线后自动刷新 |

**明确不允许**：参数先生效、网格 later 补；或网格生效、参数未生效。任何半状态都视为 apply 失败。

### 6.8 版本与回滚（R-B-08）

- `paramsVersion` 由服务端生成，建议格式 `cal.YYYYMMDD.N`（同日递增），不可复用。
- `gridVersion` 由基线版本与参数版本派生：`<baseGridVersion>+<paramsVersion>`，例如 `11.1.0+cal.20261109.1`。
- `baseGridVersion` 来自内置 `FINGERPRINT_GRID_VERSION`；它变化意味着设计层坐标/编号变化，必须发版，不能由服务端单独改。
- 每个 active 版本对包含：
  - 参数行（A/n/两个阈值/来源 run/样本哈希/操作人/时间）；
  - 网格行（`gridVersion`/`baseGridVersion`/产物路径/manifest/各楼层计数）；
  - active 指针。
- 回滚：
  - 只能回滚到历史版本对，不能只回滚参数或只回滚网格；
  - 回滚同样走 CAS + 单事务 + 审计；
  - 回滚目标必须与当前 `baseGridVersion` 相同；跨基线回滚拒绝，要求先发版。
- 保留策略：
  - 默认保留最近 20 对版本 + 当前 active；
  - 产物文件与版本行一起清理，active 版本永不清理；
  - 审计保留 180 天。

### 6.9 无鉴权决定与最小防护（R-B-09）

> **用户明确决定（2026-11-09）**：计算子菜单不引入 admin 登录、Bearer token、密码二次确认。以下风险与最小防护是如实记录，不是待定项。

风险：

| 风险 | 影响 | 说明 |
|---|---|---|
| 同局域网任何人可上传/计算/apply | 定位参数被篡改，全场定位漂移或不可用 | 无身份校验 |
| 误操作覆盖生产参数 | 现场导航异常 | 没有二次密码，只靠确认弹窗 |
| 无法确认真实操作人 | 审计只能记录自填 `operator` 与来源 IP | 审计可追溯但不能防伪造 |
| 恶意重放 apply | 反复切换版本 | 由 CAS 与限流缓解 |
| 公网暴露 | 任何人可改参数 | 必须靠部署约束，不是代码鉴权 |

最小防护（必须实现）：

1. **apply 二次确认弹窗**：展示旧值/新值、`gridVersion`、样本哈希前 8 位、可采集点变化；默认焦点在「取消」；确认文案明确「将立即影响全场定位」。
2. **版本 CAS**：apply/rollback 必须带 `expectedParamsVersion`；不匹配 `409`；同一 run 重复 apply 幂等。
3. **局域网限定**：`/api/v1/calibration/*` 只接受来自 RFC1918 私网或回环地址的请求；公网来源返回 `403`；部署文档明确要求反代不暴露该前缀。
4. **限流**：
   - apply 每 10 s 最多 1 次；
   - compute 每样本最多 3 次；
   - 上传单文件上限 20 MB；
   - 超限返回 `429`/`413`。
5. **输入白名单**：
   - 只接受已知 `type` 与 `schemaVersion`；
   - A ∈ [−80, −30] dBm，n ∈ [1.5, 6.0]；
   - 两个残差阈值必须 >0 且 `degraded > reliable`；
   - 不满足直接拒绝，不进入 staging。
6. **staging 全量校验后再提交**：网格点数、`fpId` 唯一性、楼层完整性、覆盖层 schema；任一失败不提交。
7. **审计**：复用 `AdminAuditEvent`，记录 `calibration.upload`、`calibration.compute`、`calibration.apply`、`calibration.rollback`；包含 `operator`、来源 IP、`requestId`、样本哈希、前后版本、结果；审计不可由业务接口删除。
8. **成对回滚**：误操作可在一次操作内恢复参数与网格，降低损失。
9. **只读接口无副作用**：`GET /api/v1/config/*` 不触发计算、不写库。

残余风险（用户已接受）：同网段恶意用户可以 apply；本轮不解决身份认证。后续若接入 admin 登录，应把本组接口整体挂到 admin 鉴权与 CSRF 体系下。

### 6.10 与既有链路的关系

- **定位接口**：`routers/location.py` 每次请求通过 `get_settings()` 取参数；apply 后替换 `app.state.settings` 即可热生效。多 worker 由 DB active 版本 + TTL 缓存保证收敛。
- **环境变量**：仍保留为启动兜底；优先级为 **DB active 版本 > 环境变量 > 代码默认值**。启动时若 DB 有 active 版本，覆盖 `Settings`；无则用环境变量。
- **地图包**：不新增上传；按样本 `mapVersion` 从 `MapRepository` 读取。地图包与样本版本不一致时，ready 校验失败并提示。
- **静态标定/指纹页**：导出 schema 不变；指纹页新增 `gridVersion`/`paramsVersion` 字段（向后兼容）。
- **独立工具**：`fingerprint-collector` 不接入本闭环；它仍使用内置网格，需发版才能拿到新标记。
- **客户端配置**：
  - 客户端新增运行时配置缓存，读取 `GET /api/v1/config/location-params`；
  - 指纹采集页的 `rssiAt1m`/`pathLossN` 优先使用运行时配置，`client/config/env.ts` 的 `BEACON_RSSI_AT_1M_DBM` 作为兜底；
  - 每条指纹记录写入 `gridVersion` 与 `paramsVersion`，保证历史口径可追溯；
  - mock 信标仍可用内置常量，不要求联网。

---

## 7. Part B 核心：指纹网格重算

> 本章回答五个问题：重算用什么参数、服务端怎么执行、产物怎么到客户端、与 apply 如何原子、旧记录如何对齐。

### 7.1 重算使用什么参数（R-G-01）

#### 7.1.1 A/n

- **使用 apply 的实测全局 A/n**，不使用规划口径（`A=-52`、`n=3.5`），也不使用阶段一的 A/n。
- 依据：
  - 网格的 `collectable` 是“按生产解算模型预测能否收到 ≥3 枚可用信标”，必须与定位接口使用同一组 A/n；
  - 规划口径是设计假设，阶段二实测后已被替代；继续用规划口径会让网格标记与真实解算脱节；
  - 阶段一只有单信标，不能代表全量部署的多径环境，不能作为生产网格依据。
- 分楼层 A/n 不参与重算（见 D19）；若未来后端支持分楼层参数，再扩展生成器与网格，属于后续版本。

#### 7.1.2 安装高度

- 使用**部署台账中每枚信标的实测安装高度**；
- 台账缺失某信标高度时，回退 2.5 m，并在 manifest 与结果页标记 `warnings`；
- 不使用阶段一页面临时调整的高度覆盖整栋楼。

#### 7.1.3 墙/门衰减与覆盖率阈值

| 参数 | 本轮处理 | 建议与依据 |
|---|---|---|
| 墙衰减 8/12/15 dB | **保持** | 没有本轮实测数据支撑；改动会同时改变覆盖判定与历史可比性。若后续用阶段二穿墙样本拟合，再单独评审 |
| 门衰减 2 dB | **保持** | 同上；门近似为薄墙的经验值 |
| 可采集 RSSI 阈值 -85 dBm | **保持** | 它是“可用性政策”，不是拟合参数；随 A/n 自动改会让版本之间不可比 |
| 最少信标数 3 | **保持** | 与 `MIN_BEACONS_FOR_TRILATERATION` 一致；改了会让网格承诺解算做不到 |
| 手机高度 1.2 m | **保持** | 现场统一假设；阶段一/二都按此计算斜距 |
| 设计层网格 2 m/1 m、原点 | **保持** | 设计层变化必须发版；服务端只重算采集层标记 |

- **敏感性预览（只读，不生效）**：结果页可展示 `-82 / -85 / -88 dBm` 三档下的可采集点数，供评审判断阈值是否合理；apply 仍只写 `-85`。该预览不改变网格产物，只作为决策辅助。
- 结论：**本轮重算只改 A/n 与每信标安装高度的输入，不改墙/门/阈值政策**。

### 7.2 服务端如何执行（R-G-02、R-G-03、R-G-04）

#### 7.2.1 生成器复用

- 以**库方式**调用 `src/tools/generate_fingerprint_grid.py` 的核心函数；禁止在 backend 复制一套网格算法。
- 若现有脚本以 `main()` + 文件读写为主，实施时抽取纯计算核心（输入：地图/台账/路线/虚拟信标/A/n/安装高度；输出：点列表与标记），CLI 与 backend 共用。
- 常量（墙/门/阈值/手机高）从同一模块读取，确保 CLI 与服务端产物一致。

#### 7.2.2 输入

| 输入 | 来源 | 版本约束 |
|---|---|---|
| 地图/GeoJSON | 服务端网格基线目录 | 必须与样本 `mapVersion` 对应 |
| 部署台账 | 服务端版本化基线 `ble_deployment.json` | 与地图包同版本 |
| 路线定义 | 服务端基线 `beacon_routes.json` | 与地图包同版本 |
| 虚拟信标 | 服务端基线 `simulated_beacons.json` | 可选；缺失则无 `collectableWithSim` |
| A/n | apply 候选 run | 实测全局值 |
| 安装高度 | 台账逐信标 | 缺失回退 2.5 m + 警告 |

- 服务端通过 `PATHAI_GRID_BASELINE_DIR` 指向只读基线目录；启动时校验必需文件存在，缺失则 `readiness` 返回 `server_invalid`，apply 不可用。
- 当前 `generate_fingerprint_grid.py` 默认从仓库根 `result/` 读取；部署时不能依赖开发机路径，必须显式配置基线目录。

#### 7.2.3 产物

每次 run 在 staging 目录生成：

```
<calibration_data_dir>/grid/<gridVersion>/
├── grid.json                 # 全量点 + 标记（审计/预览用）
├── overlay.json              # 下发给客户端的覆盖层
├── f2_collectable_points.csv # F2 清单（沿用现有产物）
├── f2_collectable_points.md
└── manifest.json             # 输入哈希、A/n、安装高度来源、计数、生成器版本
```

- `grid.json` 体积约 2.6 MB，**不下发客户端**；只用于审计与预览。
- `overlay.json` 只含客户端需要的标记字段，体积控制在数百 KB 以内（见 §7.3）。
- staging 成功后 run 状态为 `computed`；apply 只做校验与提交，不重新生成（除非 staging 缺失，此时 apply 失败并要求重算）。

#### 7.2.4 耗时与资源

- 网格生成是 CPU 密集的几何计算，量级为**秒级到十秒级**（与点数、墙体相交计算量相关），不涉及外部服务。
- 计算在后台 job 中执行，客户端轮询；超时建议 180 s。
- 同一站点同时只允许 1 个 compute/apply job（互斥），避免并发写 staging 与版本冲突。
- 失败：job 标记 `failed`，保留日志与已生成的部分产物供排查；不写 DB 版本、不改变 active 指针。

#### 7.2.5 失败回退

| 失败 | 处理 |
|---|---|
| 基线文件缺失 | ready 阶段就阻止；apply 不可点 |
| 生成器异常/超时 | run `failed`；参数结果可展示但 `computed` 不成立；apply 不可点 |
| 产物 schema 校验失败 | run `failed`；删除 staging 或标记不可用 |
| 计数异常（如某层 0 点） | 视为失败，要求人工检查地图/台账，不自动提交 |
| 磁盘写入失败 | run `failed`；不提交 |

### 7.3 产物如何到达客户端（R-B-12、R-G-05）

#### 7.3.1 问题

`client/data/fingerprint_grid.ts`（687 KB）与 `fingerprint-collector/data/fingerprint_grid.js`（1.2 MB）都是**打进小程序包**的静态文件。服务端重算不能直接改线上包；必须选择一种交付方式。

#### 7.3.2 三种方案对比

| 维度 | ① 服务端重算后重新发版 | ② 客户端运行时全量拉取网格 | ③ 混合：内置设计层 + 运行时覆盖层（推荐） |
|---|---|---|---|
| 改动量 | 最小：服务端只生成，人工把产物合入客户端再发版 | 最大：网格改为异步数据源，需分片/压缩/缓存/版本/失败重试；LOD 与筛选要适配“点集未就绪” | 中等：新增覆盖层接口、客户端合并模块、缓存与版本校验；内置数据结构不变 |
| 对采集页影响 | 无，发版后生效 | 大：首屏要等网格；离线/弱网体验差；筛选计数可能先空后有 | 小：点对象字段不变，只覆盖 `collectable` 等标记；LOD/筛选逻辑不动 |
| 对 LOD/筛选影响 | 无 | 需要把 `filterVisiblePoints` 的输入从同步常量改为异步点集，加载态与错误态都要处理 | 无：仍先有内置点集，覆盖层只改标记 |
| 离线可用性 | 最好（包内数据） | 最差（无网只剩旧缓存或空） | 好：内置快照始终可用；覆盖层过期仍可采集，只是标记可能旧 |
| 生效时延 | 需发版 + 审核，小时/天级 | 立即 | 立即（下次进页/启动拉取） |
| 主要风险 | 闭环断裂：apply 后线上小程序仍旧网格，参数与标记不一致 | 2.6 MB 全量 JSON 首屏/流量/内存压力；缓存一致性复杂 | 覆盖层与基线版本不匹配；需严格拒绝合并并回退；`fingerprint-collector` 仍不更新 |
| 结论 | 不能作为唯一方案 | 不推荐 v1 | **推荐** |

#### 7.3.3 推荐方案：混合交付

**真源与兜底**：

- 服务端产物是**标定相关标记的唯一真源**；
- 小程序内置 `client/data/fingerprint_grid.ts` 是**设计层坐标 + 兜底快照**；
- 客户端启动/进入采集页时拉取覆盖层；成功则合并，失败则用缓存，再失败用内置快照。

**覆盖层内容**（只覆盖会随 A/n 变化的字段）：

```json
{
  "schemaVersion": "1.0",
  "baseGridVersion": "11.1.0",
  "gridVersion": "11.1.0+cal.20261109.1",
  "paramsVersion": "cal.20261109.1",
  "params": { "a": -52.3, "n": 3.42 },
  "generatedAt": "2026-11-09T10:30:00Z",
  "counts": { "F1": 572, "F2": 22 },
  "points": [
    {
      "fpId": "FP-1-C012R034",
      "collectable": true,
      "collectable15m": false,
      "collectableWithSim": true,
      "simAddedBy": ["SIM-01"],
      "visible10m": 4,
      "strongestRssi": -71
    }
  ]
}
```

- 不包含坐标、`regionType`、`routeTags`、编号规则；这些仍来自内置设计层。
- `points` 默认全量下发；若体积超限，可在后续版本改为 diff（只下发与内置基线不同的点），本轮不做。
- 服务端按 `baseGridVersion + paramsVersion` 生成 ETag；客户端用 `If-None-Match` 条件请求，304 时复用缓存。

**客户端合并规则**：

1. 校验 `baseGridVersion` 等于内置 `FINGERPRINT_GRID_VERSION`；不等则**拒绝合并**，提示「网格基线不一致，请升级小程序」，继续用内置快照。
2. 校验 `schemaVersion` 认识、`points` 为数组；失败则用缓存/内置。
3. 按 `fpId` 合并：
   - 覆盖层存在、内置也存在 → 覆盖标记字段；
   - 覆盖层有、内置无 → 忽略并计数警告（说明基线不一致或产物脏）；
   - 内置有、覆盖层无 → 保留内置标记。
4. 合并结果写入内存；缓存键 `grid:overlay:v1` 保存 `{gridVersion, paramsVersion, etag, fetchedAt, payload}`。
5. 采集页顶部显示 `gridVersion` 与「内置/缓存/在线」来源标识，便于现场判断。

**拉取时机**：

- 小程序启动后异步拉取一次；
- 进入指纹采集页时再检查一次（版本不同则刷新）；
- apply 成功后客户端主动刷新；
- 弱网/失败不阻塞采集页，后台重试；不弹阻断式错误。

**离线与降级**：

- 完全离线：使用缓存覆盖层；无缓存则内置快照；
- 覆盖层过期：仍可使用，页面标注「标记可能不是最新」；
- 服务端回滚后：客户端按新 `paramsVersion` 重新拉取，旧覆盖层按版本键淘汰。

**对独立工具的影响**：

- `fingerprint-collector/data/fingerprint_grid.js` 本轮**不接入运行时覆盖层**，仍随发版更新；
- 如果现场使用独立工具采集，必须确认其内置 `gridVersion` 与当前 `paramsVersion` 匹配，否则导出记录会带旧标记；该工具导出的记录同样应写入 `gridVersion`（后续小改动，不纳入本轮闭环）。

### 7.4 重算与 apply 的原子性（R-B-11）

- **定义**：apply 的成功单位是 `paramsVersion + gridVersion` 一对；不存在只成功一半的合法状态。
- **执行顺序**：
  1. compute 阶段完成拟合 + 网格预览，产物落 staging；
  2. apply 阶段校验 staging 与 CAS；
  3. DB 单事务写参数行、网格行、active 指针、审计；
  4. 提交后热切换运行时缓存；
  5. 客户端刷新。
- **网格失败时**：
  - compute 阶段失败 → `compute_failed`，参数结果可看，apply 不可点；
  - apply 阶段发现 staging 缺失/校验失败 → `apply_failed`，线上仍是旧版本；
  - DB 事务失败 → 全部回滚，线上不变；
  - 任何情况下都不允许“参数先生效、网格 later 补”。
- **界面呈现**：
  - `compute_failed`：红色状态条「计算失败：网格预览未通过」，展示失败环节、日志 ID、重试按钮；
  - `apply_failed`：红色状态条「应用失败：线上参数与网格均未变更」，展示原因与重试按钮；
  - `applied` 但部分节点未刷新：黄色状态条「已生效，部分节点将在 2 秒内收敛」，展示当前/目标版本。
- **手动重试**：
  - `compute_failed` 可直接重试计算；
  - `apply_failed` 若 staging 仍在可直接重试 apply；staging 失效则要求重新计算；
  - 重试不改变 CAS 语义；版本被他人更新时必须重新计算。

### 7.5 网格重算后，已采集指纹记录如何对齐（R-B-13、R-G-06）

- **`fpId` 是否受参数变化影响**：不受。`fpId` 由 `floor + col + row` 坐标派生（`FP-{floor}-C{col:03d}R{row:03d}{suffix}`），A/n 只影响 `collectable` 等标记，不改变坐标与编号。
- **旧记录是否仍可用**：可用。记录中的 `fpId`、`coordinates`、`beacons[]` 不因 A/n 变化而失效；下游可按 `gridVersion` 决定是否纳入训练集。
- **新增字段**：
  - `FpRecord` 增加 `gridVersion` 与 `paramsVersion`（采集时写入当前运行时版本）；
  - 导出 schema 仍为 `ble_fingerprint_collection`，新增字段为可选；建议保持 `schemaVersion: "1.1.0"` 以兼容现有转换脚本，字段缺失时按 `unknown` 处理；若后续需要强约束，再升 `1.2.0`。
- **旧记录（无 `gridVersion`）**：
  - 不回填、不改写；导出时字段为 `null` 或缺失；
  - 入库/训练时标记 `unknown`，不因缺少版本而丢弃；
  - 分析报表可按 `gridVersion` 分组，`unknown` 单列。
- **点变为不可采集后**：
  - 已采记录保留，不删除；
  - 指纹页对“已采但当前不可采”的点显示特殊角标（如灰色实心 + 「已采·当前不可采」）；
  - 导出仍包含这些记录；是否用于训练由下游按版本与覆盖策略决定。
- **点变为可采集后**：无需迁移；新点按新 `gridVersion` 采集即可。
- **设计层变化（`baseGridVersion` 变化）**：
  - 可能改变坐标/编号，`fpId` 可能变化；
  - 本轮不自动迁移；要求作为新数据集处理，或在后续版本提供映射表；
  - 服务端拒绝跨 `baseGridVersion` 的 apply/rollback，避免新旧记录混用。

### 7.6 网格版本、回滚与清理

- `gridVersion = <baseGridVersion>+<paramsVersion>`；它与参数版本一一对应。
- 回滚时参数与网格成对回退；客户端按回滚后的 `paramsVersion` 重新拉取覆盖层。
- 版本清理：保留最近 20 对 + active；删除版本行前先删除 staging/产物目录；审计保留 180 天。
- 覆盖层缓存按 `gridVersion` 键控；客户端只保留最近 2 个版本，避免存储膨胀。

### 7.7 网格重算边界

| 场景 | 行为 |
|---|---|
| 样本 `mapVersion` 对应地图包不存在 | ready 失败，apply 不可用 |
| 服务端基线目录未配置 | ready 失败，提示运维配置 `PATHAI_GRID_BASELINE_DIR` |
| 台账缺少某信标安装高度 | 回退 2.5 m + 警告；不阻断 |
| 路线基线缺失 | 视为 ready 失败（网格重算是 apply 必需环节） |
| 虚拟信标缺失 | 允许；`collectableWithSim` 为空 |
| 生成器版本与内置 `baseGridVersion` 不一致 | apply 拒绝，要求先发版对齐 |
| 网格点数异常（某层 0 点） | 失败，不提交 |
| 覆盖层超过体积上限 | 失败并记录；后续可改 diff 下发，本轮不自动降级 |

---

## 8. 数据模型

### 8.1 服务端表

| 表 | 关键字段 | 说明 |
|---|---|---|
| `calibration_samples` | `sample_id`、`type`、`schema_version`、`deployable`、`map_version`、`file_name`、`size`、`content_hash`、`raw_path`、`uploaded_at`、`operator` | 上传样本元数据；原文件落盘，不入 DB 大字段 |
| `calibration_runs` | `run_id`、`sample_id`、`status`、`input_hash`、`result_json`、`grid_preview_json`、`warnings_json`、`error`、`created_at`、`finished_at` | 计算任务；`status` 覆盖 `queued/running/computed/failed` |
| `location_param_versions` | `params_version`、`rssi_at_1m`、`path_loss_n`、`residual_reliable_m`、`residual_degraded_m`、`source_run_id`、`sample_hash`、`operator`、`created_at`、`status` | 不可变；`status=active/superseded/rolled_back` |
| `fingerprint_grid_versions` | `grid_version`、`base_grid_version`、`params_version`、`manifest_json`、`counts_json`、`artifact_dir`、`created_at`、`status` | 与参数版本一一对应 |
| `calibration_active_pointer` | `site_id`、`params_version`、`grid_version`、`updated_at` | 单行；原子切换 |
| `admin_audit_events` | 复用现有表 | 事件类型 `calibration.*`；`details_json` 存前后版本、哈希、IP、requestId |

### 8.2 客户端存储键

| 键 | 内容 | 说明 |
|---|---|---|
| `col:logdist:v1` | 对数等距页面记录 | 只存有会话档位 |
| `grid:overlay:v1` | 覆盖层缓存 | `{gridVersion, paramsVersion, etag, fetchedAt, payload}` |
| `cfg:calibrationOperator` | 操作人 | 复用现有设置页字段 |
| `cfg:*` | 现有配置 | 不新增鉴权相关键 |

### 8.3 导出 schema 变更

| schema | 变更 | 兼容性 |
|---|---|---|
| `positioning_calibration_logdist` | 新增 | 独立类型；旧工具忽略 |
| `positioning_calibration_logdist_result` | 新增 | 仅评审输出 |
| `positioning_calibration_static` | 不变 | 完全兼容 |
| `ble_fingerprint_collection` | 记录新增可选 `gridVersion`、`paramsVersion` | 向后兼容；旧转换脚本忽略未知字段 |

---

## 9. 架构与复用

### 9.1 客户端

| 文件 | 变更 |
|---|---|
| `client/app.json` | 注册 `pages/collect/logdist/logdist`、`pages/calc/calc` |
| `client/pages/collect/logdist/*` | 新增页面（wxml/wxss/ts/json） |
| `client/pages/calc/*` | 新增页面 |
| `client/pages/settings/settings.wxml` | 新增「定位常量计算」section |
| `client/core/collection/logdist-store.ts` | 新增：对数等距记录 CRUD |
| `client/core/collection/export-builder.ts` | 新增 `buildLogdistExport` |
| `client/core/collection/static-sampler.ts` | 复用统计核心；支持注入固定斜距，避免依赖地图坐标 |
| `client/core/collection/grid-overlay.ts` | 新增：覆盖层拉取、校验、合并、缓存 |
| `client/core/config/runtime-config.ts` | 新增：`paramsVersion`、A/n、阈值缓存 |
| `client/pages/collect/fingerprint/fingerprint.ts` | 使用合并后的点集；记录写入 `gridVersion/paramsVersion`；顶部显示版本 |
| `client/core/collection/types.ts` | `FpRecord` 增加可选 `gridVersion`、`paramsVersion` |
| `client/data/fingerprint_grid.ts` | 不改结构；仍是设计层与兜底快照 |

### 9.2 服务端

| 文件 | 变更 |
|---|---|
| `backend/pathai_backend/routers/calibration.py` | 新增：上传/计算/apply/版本/回滚 |
| `backend/pathai_backend/routers/config.py` | 新增：`location-params`、`fingerprint-grid/overlay` |
| `backend/pathai_backend/calibration_service.py` | 新增：拟合、阈值、run 状态、CAS、事务 |
| `backend/pathai_backend/runtime_params.py` | 新增：active 版本加载、TTL 缓存、`Settings` 热替换 |
| `backend/pathai_backend/grid_recompute.py` | 新增：调用网格生成核心、staging、manifest、校验 |
| `backend/pathai_backend/app.py` | 注册路由；启动时加载 active 版本 |
| `backend/pathai_backend/config.py` | 新增 `PATHAI_GRID_BASELINE_DIR`、`PATHAI_CALIBRATION_DATA_DIR`、TTL 配置 |
| `backend/pathai_backend/models.py` | 新增 §8.1 表 |
| `backend/alembic/versions/*` | 新增迁移 |

### 9.3 工具

| 文件 | 变更 |
|---|---|
| `src/tools/fit_location_params.py` | 按 `type` 分派；新增 `positioning_calibration_logdist` 拟合与结果文档 |
| `src/tools/generate_fingerprint_grid.py` | 抽取可复用核心；支持传入 A/n 与逐信标安装高度；CLI 行为保持不变 |
| `src/tools/` | 不新增第二套网格算法 |

---

## 10. 兼容性与迁移

- **旧静态标定导出**：继续可用；计算控制台按现有 schema 解析。
- **旧指纹记录**：无 `gridVersion` 时按 `unknown` 处理；不重写本地存储。
- **旧内置网格**：`baseGridVersion` 不变时，覆盖层可直接合并；变化时必须发版。
- **环境变量部署**：无 DB active 版本时行为与现在完全一致；有 active 版本时 DB 优先。
- **多 worker**：启动时加载 active 版本；运行期 TTL ≤2 s；apply 后最迟一个 TTL 收敛。
- **回滚**：只能回滚到同 `baseGridVersion` 的历史对；跨基线拒绝。
- **主包体积**：新增页面与逻辑预计远小于主包 2048 KB 限制；覆盖层不打包，运行时缓存。

---

## 11. 测试计划

### 11.1 单元测试

- `logdist-store`：档位只存有会话项、会话追加、`captureIndex`、清理。
- 斜距计算：`h=0 → 1.3`、`h=18 → 18.05`、安装高度变化后的重算。
- `buildLogdistExport`：schema、`deployable:false`、空档位不出现、汇总计数。
- `fit_location_params.py`：
  - `type` 分派；
  - logdist 只产 A/n，不产阈值/回放；
  - 0 m 档敏感性；
  - 稳定性门槛与 warnings。
- 计算控制台状态机：所有按钮状态迁移；`blocked` 下 apply 永久禁用。
- ready 判定：客户端存在 vs 服务端正确的组合。
- CAS：并发 apply 只有一个成功；重复 apply 幂等。
- 覆盖层合并：基线不匹配拒绝、未知 `fpId` 忽略、缓存回退。
- `FpRecord` 兼容：无 `gridVersion` 的旧记录可导出。

### 11.2 集成测试

- 上传样本 → 计算 → `computed` → apply → `location-params` 返回新版本；TTL 内生效。
- 网格预览失败 → `compute_failed` → 线上不变。
- apply 时 staging 丢失 → `apply_failed` → 线上不变 → 重算后可重试。
- 回滚 → 参数与网格同时回退；客户端覆盖层刷新。
- 服务端重启 → 从 DB active 版本恢复；无 active 时回退环境变量。
- 公网来源请求 `calibration/*` → 403；私网来源正常。
- 超限/非法样本 → 413/422；不进入 staging。

### 11.3 兼容性验收

- 旧静态标定导出喂给计算控制台，结果与 `fit_location_params.py` CLI 一致（A/n/阈值）。
- 旧指纹导出（无 `gridVersion`）可被现有转换脚本解析。
- 新指纹导出带 `gridVersion/paramsVersion`，旧脚本忽略后仍可入库。
- 内置网格 + 覆盖层合并后，`filterVisiblePoints` 的 LOD/筛选结果与直接使用新全量网格一致（抽样断言）。

### 11.4 真机手工验收

- 首页第三候选进入对数等距页；单信标自动锁定；多信标必须选择。
- 30/60/120 秒倒计时、后台暂停/恢复、振动。
- 安装高度修改后斜距即时更新，已采会话保留旧高度。
- 导出文件在 PC 端可被拟合脚本读取。
- 计算页按钮状态符合 §6.5；apply 二次确认内容完整。
- apply 后指纹页顶部版本变化；离线时回退内置快照仍可采集。
- 回滚后版本与标记回到上一对。

---

## 12. 决策记录

| # | 决策 | 依据 |
|---|---|---|
| D1 | **不做鉴权**：不引入 admin 登录、Bearer token、密码二次确认；保留 apply 二次确认、CAS、局域网限定、限流、schema 校验、审计 | **用户明确决定（2026-11-09）**；风险与最小防护见 §6.9 |
| D2 | **指纹网格重算纳入 apply 链路**，不再是出界项 | **用户明确决定（2026-11-09）**；参数变化后网格标记必须同步，否则线上采集页会误导现场 |
| D3 | 对数等距页为独立页、独立 schema、`deployable:false` | **用户明确决定（2026-11-09）**；阶段一单信标不能产出可部署参数 |
| D4 | 入口为首页采集连体候选第三项 | **用户明确决定（2026-11-09）**；与现有静态标定/网格指纹同组，现场易找 |
| D5 | 单信标、安装高度可调、倒计时 30/60/120 秒 | **用户明确决定（2026-11-09）**；阶段一只有 1 枚信标 |
| D6 | 独立存储键 `col:logdist:v1`，只存有会话档位 | 与静态标定隔离；避免导出空会话导致拟合脚本报错 |
| D7 | 对数等距页不启用 `LocationPipeline`、不显示定位估计 | 单信标 `snapshot` 为 null，显示估计会误导现场 |
| D8 | `fit_location_params.py` 按 `type` 分派；对数等距只产 A/n，不产阈值/回放 | 单信标回放全部 lost，阈值会退化为兜底值，不能冒充标定结果 |
| D9 | 计算入口放设置页，不受采集工具开关控制 | 运维功能与采集工具分离；无鉴权已由用户拍板 |
| D10 | 样本由用户上传；地图包按 `mapVersion` 服务端解析；台账/路线/网格基线为服务端版本化资产 | 避免重复上传大文件；保证同版本可复算 |
| D11 | ready 判定分工：客户端管存在，服务端管正确 | 客户端不做业务校验，服务端是唯一判定真源 |
| D12 | apply = 参数 + 网格单事务提交；不允许半状态 | 参数与标记必须同版本；半状态会让现场按错误标记采集 |
| D13 | 网格重算使用实测全局 A/n；墙/门/`-85 dBm`/`≥3` 保持工程常量 | 与生产解算同口径；无实测依据不自动改政策参数；保持跨版本可比 |
| D14 | 网格交付采用混合方案：内置设计层 + 运行时覆盖层 + 兜底快照 | 兼顾闭环、改动量、离线可用性；全量拉取 2.6 MB 不划算；重新发版会断闭环 |
| D15 | `fpId` 不变；新增 `gridVersion/paramsVersion`；旧记录继续有效 | 编号由坐标派生，A/n 不改变几何 |
| D16 | 客户端刷新范围：`paramsVersion` + 覆盖层；内置 `BEACON_RSSI_AT_1M_DBM` 不热改，只作兜底 | 避免历史指纹 `accuracy` 口径漂移；真实定位由服务端参数决定 |
| D17 | 版本成对回滚，只允许同 `baseGridVersion` | 跨基线会改坐标/编号，旧记录无法对齐 |
| D18 | 阶段一样本可在计算页查看结果，但 apply 恒不可用 | 满足现场评审需要；不破坏“阶段二才可部署”的边界 |
| D19 | 分楼层 A/n 仅展示，不参与 apply 与网格重算 | 后端当前只有全局 `LocationParams`；避免网格与解算口径分裂 |
| D20 | `fingerprint-collector` 独立工具本轮不接入闭环 | 控制范围；它仍走发版路径 |

---

## 13. 边界与失败处理汇总

| 场景 | 行为 |
|---|---|
| 阶段一样本上传到计算页 | 可计算、可看结果；apply 永久禁用；不触发网格重算 |
| 阶段二样本 `deployable:false` | 同上 |
| 服务端基线缺失 | ready 失败；apply 不可用；提示配置基线目录 |
| 地图包版本与样本不一致 | ready 失败；提示重新导出或选择正确地图包 |
| 计算中断/服务重启 | run 状态以 DB 为准；staging 可恢复则 apply，不可恢复则要求重算 |
| apply 并发 | CAS 保证只有一个成功；另一个 409 |
| 运行时缓存未刷新 | 最多 2 s 收敛；页面提示「部分节点延迟」 |
| 客户端离线 | 使用缓存覆盖层；无缓存用内置快照；不阻塞采集 |
| 覆盖层基线不匹配 | 拒绝合并，回退内置快照，提示升级小程序 |
| 旧指纹记录无版本 | 标记 `unknown`；不丢弃、不回填 |
| 已采点变为不可采 | 记录保留；页面显示「已采·当前不可采」 |
| 回滚跨基线 | 拒绝；要求先发版对齐 |
| 网格产物过大 | 记录失败；本轮不自动改 diff，后续优化 |

---

## 14. 实施清单

> 本轮只评审，不实施。以下为获批后的实施顺序，按依赖排列。

### 14.1 阶段 A：对数等距页

1. 客户端新增 `logdist` 页面与存储模块；注册路由。
2. 复用扫描/统计核心，注入固定斜距；不接入 `LocationPipeline`。
3. 首页采集连体候选增加第三项；无障碍属性与现有按钮一致。
4. 导出构造与 `logdist` 结果文档。
5. `fit_location_params.py` 分派与 logdist 拟合。
6. 单测 + 真机验收。

### 14.2 阶段 B：计算控制台

1. 设置页入口与 `calc` 页面骨架；状态卡、文件卡、结果卡、历史卡。
2. 服务端表与迁移；样本上传、ready、run、版本、回滚接口。
3. 拟合服务接入 `fit_location_params.py` 的静态路径。
4. 运行时参数存储与 TTL 热切换；`GET /api/v1/config/location-params`。
5. 按钮状态机、CAS、二次确认、限流、私网限制、审计。
6. 单测 + 集成测试。

### 14.3 阶段 C：网格重算与交付

1. 抽取 `generate_fingerprint_grid.py` 可复用核心；支持 A/n 与逐信标安装高度输入。
2. 服务端 `grid_recompute`：基线目录、staging、manifest、校验、超时、互斥。
3. compute 接入网格预览；apply 接入 staging 校验与单事务提交。
4. 覆盖层接口 + 客户端 `grid-overlay` 合并、缓存、版本校验、兜底。
5. 指纹记录写入 `gridVersion/paramsVersion`；旧记录兼容。
6. 回滚成对生效；版本清理。
7. 单测 + 集成 + 真机验收。

### 14.4 验收门槛

- 阶段一：能独立完成 10 档采集、导出、拟合，结果带 `deployable:false`。
- 阶段二：上传 → 计算 → apply → 客户端版本刷新全链路通过。
- 网格：apply 失败不改变线上；成功后覆盖层可拉取、可缓存、可回退。
- 回滚：参数与网格成对回退。
- 无鉴权：最小防护全部生效，风险文档已随 spec 评审。

---

## 15. 待拍板项

> 以下为仍需用户确认的少量边界；每项给出建议，未确认前按建议执行。

| # | 待定项 | 建议 | 理由 |
|---|---|---|---|
| P1 | 网格覆盖层的拉取时机：启动时 / 进采集页时 / 两者都做 | **两者都做，异步不阻塞** | 启动拉取保证导航期间版本新；进页再查保证现场拿到最新；失败不阻塞 |
| P2 | 旧指纹记录（无 `gridVersion`）是否回填 | **不回填，标记 `unknown`** | 回填需要重算历史 `accuracy`，会改变已导出口径；标记 unknown 可追溯且不丢数据 |
| P3 | 对数等距样本是否允许进入计算页查看结果 | **允许查看，apply 永久禁用** | 现场需要在同一界面 review 阶段一 A/n；但 `deployable:false` 必须阻止 apply |
| P4 | 是否在结果页展示 `-82/-85/-88 dBm` 敏感性预览 | **展示，但不生效** | 帮助评审判断阈值；不改变网格产物与历史可比性 |
| P5 | 版本保留代数与跨基线回滚 | **保留 20 对；禁止跨 `baseGridVersion` 回滚** | 控制存储；跨基线会改 `fpId`，旧记录无法对齐 |
| P6 | 是否把 `fingerprint-collector` 纳入本轮闭环 | **不纳入，仍走发版** | 独立工具改造量大；本轮先保证主小程序闭环 |

---

## 16. 评审确认

- 本 spec 的 Part A/Part B 边界、无鉴权决定、网格重算五问、三方案对比与推荐，作为本轮评审结论。
- 评审通过后，按 §14 实施清单拆分任务；实施前不修改任何代码。
- 若 §15 任一项被否决，按否决意见更新本 spec 后再实施。
