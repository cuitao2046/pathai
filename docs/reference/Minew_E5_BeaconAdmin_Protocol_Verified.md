# Minew E5（BeaconSET / MinewBeaconAdmin SDK）实测协议规格

> 来源：对 `docs/reference/MinewBeaconAdmin/Android/…/minewBeaconAdmin.jar`（2025.04.25）反编译核验。
> 方法：`javap -p -c -constants` 反汇编 + 常量池比对；证据类名/方法名随文标注。
> 结论：**该 SDK 不使用 `a3c875xx` 服务族，也不做 AES 鉴权**；`Minew_E5_BeaconPlus_Windows_App_Design_Doc.md` §2 的协议部分不适用，以本文为准。
> 状态：静态反编译结论，**写参行为仍需 1 枚实物 P1-a 验证**（尤其 FFF5 档位编码）。

---

## 1. 服务与特征值

服务：`0000fff0-0000-1000-8000-00805f9b34fb`（`com.minew.beaconset.b.b` 静态常量 `l`）

| 特征值 | SDK 字段 | 含义 | 写 payload | 读回 |
|---|---|---|---|---|
| `0000fff1-…` | `m` | UUID | 16 字节，**UUID 文本序**（去 `-` 的 32 位 hex 直接转字节） | 32 位 hex 字符串（`b.c.a(byte[])`） |
| `0000fff2-…` | `n` | Major | 2 字节大端（值→hex 补 4 位→字节） | hex → `parseInt(…,16)` |
| `0000fff3-…` | `o` | Minor | 2 字节大端 | 同上 |
| `0000fff4-…` | `p` | 校准 RSSI@1m（calibratedTxPower） | 1 字节有符号 | 1 字节 |
| `0000fff5-…` | `q` | 射频发射功率 | 1 字节 **档位码/索引**（非 dBm） | hex 字符串（nRF 设备为 `FF` 开头的档位列表格式，见 §4） |
| `0000fff6-…` | `r` | 设备密码 | ASCII 字节 | 连接时跳过 |
| `0000fff7-…` | `s` | 广播间隔（单位 ms） | 2 字节大端（300 ms = `0x012C`；范围 100 ms~10 s） | `getIntValue(FORMAT_UINT16)` |
| `0000fff8-…` | `t` | Device ID | 4 字节大端（Long→hex 补 8 位） | hex 字符串 |
| `0000fff9-…` | `u` | **设备名称** | ASCII 字节（无长度补位） | `getStringValue(0)`（ASCII） |
| `0000fffa-…` | `v` | 未在 SDK 流程中使用 | — | 连接时跳过 |
| `0000fffb-…` | `w` | 软关机 | 1 字节 `0x00`（无密码） | 读 1 字节 |
| `0000fffe-…` | `x` | 广播模式（mode） | 1 字节（0 = iBeacon） | `parseInt`；**读完该特征即判定连接完成** |
| `0000ffff-…` | `y` | **提交/生效（带口令）** | 密码 ASCII 字节 | — |

设备信息服务（`0000180a`）：`2a29` 厂商、`2a25` 序列号、`2a27` 硬件版本、`2a26` 固件版本、`2a23` System ID、`2a2a` 证书；
电池服务 `0000180f` / `2a19`。

---

## 2. 写入流程（`MinewBeaconConnection.writeSetting(String password)`）

按字段变更标志逐项写，随后写 `FFFF` 提交：

```
FFF1 ← UUID 16B            （若 uuidChange）
FFF2 ← Major 2B BE         （若 majorChange）
FFF3 ← Minor 2B BE         （若 minorChange）
FFF4 ← 校准 RSSI 1B        （若 calibratChange，本项目不写）
FFF5 ← 功率档位码 1B       （若 txpowerChange）
FFF7 ← 间隔 2B BE          （若 broadcasetintervalChange）
FFF8 ← DeviceID 4B         （若 deviceIdChange，本项目不写）
FFF9 ← 名称 ASCII          （若 nameChange）
FFFE ← mode 1B             （若 modeChange，本项目不写）
FFF6 ← 新密码 ASCII        （若 passwordChange，本项目不写）
FFFF ← password ASCII      （提交/生效；每次配置必发）
```

- **设备侧单位是毫秒**：写入 `FFF7` 的是 ms（300 → `0x012C`）；官方 App 的输入框以 100 ms 为单位（输入 20 → 实际 2000 ms），
  不要把 UI 输入值直接当 ms 写。合法范围 **100 ms ~ 10 s**（`0x0064` ~ `0x2710`）。
- **无 AES、无 16 字节报文头、无随机填充**：`b.a` 的 Base64 只是 SDK 内部编码，`b.c.a(String)=Base64.decode` 会还原成上面的原始字节。
- **无槽位（Slot）概念**：BeaconSET 固件只有一个广播配置，不存在 Slot 0~5。
- **无需订阅通知**：连接后 SDK 只做「逐特征 Read」，写入完成靠 GATT 写回调（`onCharacteristicWrite`）计数，没有依赖 CCCD 通知。
- 写完 `FFFF` 后 SDK 只做「写成功 → 延迟断开 GATT」，**没有重启指令**（`MinewBeaconConnection` 仅有
  `writeSetting / writeBatchSetting / writePowerOffByAuth`，无 restart/reboot）。保存后设备可能自行停止广播，
  但**不可依赖**：校验前必须**人工断电重启**（见 `docs/30` 步骤 E）。

## 3. 连接后自动读回（`ConnectService` 特征遍历）

连接发现服务后，SDK 对 `FFF1/FFF2/FFF3/FFF4/FFF5/FFF7/FFF8/FFF9/FFFE` 及 DIS/电池特征逐个 **Read**，
跳过 `FFF6/FFFA/FFFB/FFFF`。因此「读当前设置」在 Windows 端完全可行：

| 字段 | 解析 |
|---|---|
| UUID | 返回 32 位 hex，按 8-4-4-4-12 格式化 |
| Major / Minor | hex → 整数 |
| 间隔 | `FORMAT_UINT16`（大端 2 字节） |
| 名称 | ASCII 字符串 |
| 功率 | 见 §4 |
| 模式 | 整数（读完即连接完成） |

## 4. 功率档位（重点）

`MinewBeaconSetting` 内置 dBm 列表：`-30, -20, -16, -12, -8, -4, 4, 0`，`getTxPower()/setTxPower()` 用的是**列表索引**。

- 写入：合同版本走 `b.a.a(index+"", 2)` → 1 字节 hex（例如索引 3 → `0x03`）；老版本走 `getTxPowerHex()`。
- **功率只能从设备支持的档位列表中选择**（不是任意 dBm）：写前先读 `FFF5` 的码表，确认 -12 dBm 在列表内再选码；
  若 E5 档位表不含 -12 dBm，**暂停入库**：回填规划台账为实际支持的档位并重新发布地图包，
  不要以「最近档」直接入库（服务端 intake 校验要求写入值等于规划值）。
- 读回：若返回 hex 长度 ≥ 8 且以 `FF` 开头，按「前缀 FF + 当前码 + 支持码列表」解析；否则按 `FORMAT_UINT16` 整数读。
- **`-12 dBm` 不应直接写成字节 `0xF4`**。正确做法：先读 `FFF5` 拿到设备支持的档位码表，再按 `-12 dBm` 选中对应码写入；P1-a 用真机确认码表。

## 4.1 校准距离（`FFF4`）

- 官方手册口径：范围 **-127 ~ 0**，常见出厂值 **-55 / -59**，**不建议修改**；
- 广播帧中**看不到**该值，只有连上设备才能读（与 `FFF4` 只读定位一致）；
- 本项目**不写** `FFF4`，仅连接后读回展示；定位解算使用后端配置的 `-71`（`docs/28`）。

## 5. 设备名称

- 写入 `FFF9`：ASCII 字节，**无 7 字符硬限制**（SDK 不截断，固件侧上限以实测为准；本项目仍用 ≤7 的短码 `F{floor}-{minor后三位}`）。
- 读回 `FFF9`：`getStringValue(0)` 即 ASCII 明文，可直接与短码比对。

## 6. 与旧文档的差异（必须替换）

| 项 | `Minew_E5_BeaconPlus_Windows_App_Design_Doc.md` | 实际 SDK（本文） |
|---|---|---|
| 配置服务 | `a3c87500-8ed3-4bdf-8a39-a01bebede295` | `0000fff0-…00805f9b34fb` |
| 特征值 | `a3c87501/02/03/04/05/0a/0b` | `fff1…fff9 / fffe / ffff` |
| 通知/鉴权 | `7f280001/2/3` + AES-128-ECB | 无通知依赖；无 AES；口令以 ASCII 写 `FFFF` 提交 |
| 帧结构 | iBeacon 两包（`0x80 0x10 0xA0 0xFF`…） | 每个字段一个特征，直写原始值 |
| 槽位 | Slot 0~5 | 无槽位 |
| 读回 | 未覆盖 | 连接后逐特征 Read（含间隔、名称、功率） |
| 设备名 | 未覆盖 | `FFF9` 可写可读 |
| 关机 | OpCode `0x1B` 16B 报文 | `FFFB` ← `0x00`（本项目不开放） |

> 依据：`com.minew.beaconset.b.b`（UUID 常量）、`MinewBeaconConnection.writeSetting`（字段→特征映射）、
> `com.minew.beaconset.b.a` / `b.c`（hex/Base64 编码，最终落到原始字节）、`ConnectService`（连接后读取与 `FFFE` 完成判定）、
> `MinewBeaconConnection.writePowerOffByAuth`（`FFFB`）。全包未发现 `javax/crypto` 或 AES 实现。

## 7. 官方手册口径（BeaconSET 用户手册 v2.0.0，2025-12-24）

来源：`docs/reference/BeaconSET用户操作手册_v2.0.0.pdf`。用于校验本项目参数范围，二者冲突时以「手册范围 + SDK 字节语义」为准。

| 参数 | 手册规范 | 与特征值的对应 |
|---|---|---|
| UUID | 32 位 hex（`0-9`、`a-f`，大小写均可） | `FFF1` 16 字节（32 hex 去 `-` 后转字节） |
| Major / Minor | **0 ~ 65535** | `FFF2` / `FFF3` 2 字节大端 |
| 校验距离（校准 RSSI） | **-127 ~ 0**，常见 `-55` / `-59`；**不建议修改**；广播中不可见，连上才可读 | `FFF4`（只读，本项目不写） |
| 广播功率 | **从设备档位列表勾选**（非任意值）；广播中不可见，连上才可读 | `FFF5` 档位码（见 §4） |
| 广播周期 | **100 ms ~ 10 s**；App 手动输入值 ×100 = 实际 ms（输入 20 → 2000 ms） | `FFF7` 直接写 **ms**（300 → `0x012C`） |
| 设备名称 | 手动输入（手册未给长度上限） | `FFF9` ASCII；本项目 ≤7 |
| 连接模式 | `YES`=可连接，`NO`=不可连接；设 `NO` 后**仍可连接 5 分钟**，之后不广播设备名称 | `FFFE`；本项目固定 `YES` |
| 重启密码 | **8 位**；保存参数时输入 | `FFFF` 提交用；`FFF6` 不改 |
| 连接超时 | 连接后 **5 分钟**自动断开 | 写入流程须在 5 分钟内完成 |
| 连续 5 次错误密码 | 设备广播 **5 分钟不可连接**（重新上电可恢复） | 禁止自动重试错误密码 |

**对本项目的直接影响**：

1. `FFF7` 写 300 ms 合法；若 UI 提供输入，必须按「100 ms 单位」换算，不能把输入值当 ms；
2. 广播功率必须先读 `FFF5` 列表再选 `-12 dBm`；列表无该档位时**暂停入库**，回填台账并重新发布（服务端要求写入值 = 规划值）；
3. `FFF4` 只读；手册常见值 `-55/-59` 与后端解算常量 `-71` 是两回事（后端不依赖设备该字段）；
4. 连接模式必须保持 `YES`，否则 5 分钟后名称不再广播、按名称过滤会失败；
5. 密码为 8 位，错误重试会触发 5 分钟不可连接，程序不得自动反复尝试。
