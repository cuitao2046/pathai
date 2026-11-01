# `result/ble_deployment.json` 数据结构与字段说明

> 文档版本：1.1 ｜ 对应数据版本：`schemaVersion 1.0`，**61 信标**（现行台账，`fix-rollback-ble-61` 回滚后状态）
> 生成方式：`generatedBy = "pathai-manual-deploy"`，经 `render_interactive.py` 部署模式拖拽/点选导出，由人工确认后入库 `result/`。
> 校订记录：2026-09-21 按 `result/ble_deployment.json` 实测逐字段复核（修正 `major` 语义、`minor` 取值区间、`sourceNodeId` 空值口径、`originalLocationDesc` 说明、`beaconId` 形态）；2026-10-27 修正 `minor`"万位对应 major"的错误不变量（实测 7 枚不符，见 §3.1 与 `docs/18` TODO-9），并记录 `major` 已统一为楼层（`BK-M-2-001/002` 由 1→2 修正）；2026-11-01 新增 **§8 部署时写入 `installHeightM` 的操作指引**（spec 0.3.1 高度补偿解算）。

## 1. 文件定位

`ble_deployment.json` 是**信标部署方案（位置规划）**，描述每枚 iBeacon 的部署位置与属性。**它不直接内嵌地图几何**（墙体/房间多边形在 `school_building_01_map_v9.geojson`），与地图的关联靠 **ID 引用 + 描述字段**：

> **当前状态（2026-10-27 用户确认）：全部 61 枚信标均未上墙。**
> 本文件是**基于交互渲染图（`render_interactive.py` 部署模式）生成的部署点位建议**，
> 属"规划态"而非"竣工台账"：`installDate` 全为空、`locationDesc` 部分是坐标回显，均属正常。
> 后续按这些点位**现场实际部署**，并逐枚配置参数（`major`/`minor` 照抄本文件，见 `docs/08` §3 第 3 步）。
> 现行 61 枚为 **Minew E5**，参数由 `beacon-provisioner` Windows 桌面程序从服务端台账读取后直接写入信标（见 `docs/27`）。
> 设备名短码（≤7 字符 `F{floor}-{minor后三位}`）与标签二维码（内容 = `beaconId`）在**发布阶段生成**，不回写本文件。
> 配置完成后应回填 `installDate` 与实测 `rssiAt1m`（仅用于后端参数标定，**不写入设备**），届时本文件才转为"竣工态"。

- 通过 `sourceNodeId` 关联到 geojson 的拓扑节点（`topology.nodes` 中的 TR/TD/TI 等）。
- 通过 `adjacentRooms` 关联到 geojson 的房间要素。
- 通过 `coordinates` 与 geojson 共用同一坐标系（米，原点在地图左下角，Y 向上为正）。

### 坐标系约定
- `coordinates: [x, y]`，单位**米**，与 `school_building_01_map_v9.geojson` 同源。
- `floor: 1 | 2` 为物理楼层，不进入坐标值，仅作分层标识。
- 标定常量（`parse_cad_pdf.py`）：`SCALE=0.0529 m/pt`、原点 `(2019.1, 1154.8) pt`、Y 翻转。

## 2. 顶层结构

| 字段 | 类型 | 含义 |
|---|---|---|
| `schemaVersion` | string | 数据格式版本，当前 `"1.0"` |
| `generatedBy` | string | 生成工具标识，当前 `"pathai-manual-deploy"` |
| `generatedAt` | string | 生成时间戳（ISO 8601，UTC） |
| `beacons` | array\<Beacon\> | 信标对象数组（当前 61 枚） |
| `summary` | object | 统计汇总，见 §4 |

## 3. 单信标字段（Beacon 对象）

> 出现率：`61/61` 为全部信标均含该**键**；标注 `n/61` 的为仅部分信标含该键。注意：部分字段键虽存在但值为**空串**（如 `sourceNodeId` 有 29 枚为空串、`sourceNodeType` 有 8 枚为空串），见各条说明。

### 3.1 标识与协议字段

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `beaconId` | string | ✅ | 信标唯一编号。实际存在三种形态：`BK-01-xxx`（F1 顺序位，39 枚）、`BK-02-xxx`（F2，4 枚）、`BK-TR-F1-xxx` / `BK-TR-F2-xxx`（测试路线补点，7+3 枚）、`BK-M-1-xxx` / `BK-M-2-xxx`（人工部署，6+2 枚） |
| `uuid` | string | ✅ | iBeacon UUID，全楼统一 `B9407F30-F5F8-466E-AFF9-25556B57FE6D` |
| `major` | number | ✅ | iBeacon major：**已与楼层一致**——`major=1`（52 枚，全 F1）/ `major=2`（9 枚，全 F2），61/61 满足 `major == floor`（2026-10-27 修正 `BK-M-2-001/002` 由 1→2）。仍以 `floor` 为楼层唯一权威（详见 `docs/18` TODO-9） |
| `minor` | number | ✅ | iBeacon minor（信标序号），实测区间 `10001` ~ `20002`。**"万位对应 major"不成立**：7 枚不符——`BK-02-001~004` 与 `BK-TR-F2-001~003` 为 `major=2` 但 `minor=10145~10160`（万位为 1，因 F2 主信标沿 F1 序列连续编号）。判楼层一律用 `floor`（详见 `docs/18` TODO-9） |
| `coordinates` | array\<number,2\> | ✅ | 平面坐标 `[x, y]`（米，同源 geojson 坐标系） |

### 3.2 部署位置与安装参数

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `floor` | number | ✅ | 物理楼层：`1`（52 枚）/ `2`（9 枚） |
| `locationDesc` | string | ✅ | 位置人类可读描述，如 `1F 电梯口（II-01#EL）· 门套或呼梯板旁` |
| `mountType` | string | ✅ | 安装方式：`wall`（壁装，54）/ `door_frame`（门框，7） |
| `installHeight` | number | ✅ | 安装高度（米），统一 `2.2`。**所在楼层地面 → 信标壳体中心**的垂直距离；它是发布包 `installHeightM` 的唯一来源，参与高度补偿解算（spec 0.3.1）。变更与写入操作见 **§8** |
| `txPower` | number | ✅ | 发射功率（dBm），**统一 `-12`（E5 3 档，覆盖半径约 15 m）**。注意：它**只描述广播档位，不参与测距**；距离解算使用 `RSSI@1m`（measured power，占位 -71 dBm，待现场标定） |
| `broadcastInterval` | number | ✅ | 广播间隔（ms），统一 `300` |
| `batteryModel` | string | ✅ | 电池型号，统一 `CR2477` |
| `expectedLifespan` | number | ✅ | 预期寿命（年），统一 `5` |
| `installDate` | string | 43/61 | 现场安装日期 `YYYY-MM-DD`，如 `2026-08-14` |
| `direction` | string | 13/61 | 朝向描述（带方向性布点用），如 `南` |

### 3.3 语义与来源（L2 定位层布点角色）

> 这些字段表达的是**定位层（L1–L5 模型中的 L2）的布点角色**，不是 L5 应用语音语义。

| 字段 | 类型 | 必填 | 含义 / 取值 |
|---|---|---|---|
| `semanticTag` | string | ✅ | L2 布点大类：`trilateration_route_base`（路线基线，43）/ `trilateration_route_fill`（密度填补，10）/ `manual_deploy`（人工补点，8） |
| `subType` | string | ✅ | 细分类型：`base`(21) / `fill`(17) / `dir`(13) / `manual_deploy`(8) / `elevator_door`(2) |
| `sourceNodeId` | string | 61/61（**其中 29 枚为空串**） | 挂靠的地图拓扑节点 ID（geojson `topology.nodes`），如 `F1-TF-0011`、`F1-TI-0023`。**空串表示无节点挂靠**（含人工部署 `BK-M-*` 8 枚、测试路线补点 `BK-TR-*`、部分微调后脱离节点的补点），消费方须容错 |
| `sourceNodeType` | string | 61/61（**其中 8 枚为空串**） | 来源节点类型：`intersection`(24) / `route_fill`(10) / `route_corridor`(7) / `doorway`(6) / `manual_adjusted`(4) / `facility`(2) / `""`(8) |
| `adjacentRooms` | array\<string\> | ✅ | 相邻房间 ID 列表（R6 校验要求门口/交叉口信标必填），如 `["F1-CR-0053","F1-RM-0066"]` |
| `riskLevel` | number\|string | ✅ | 定位风险等级：`low`(10) / `0.3`(7) / `0.5`(32) / `1`(11) / `2`(1)（数值越大风险越高） |

### 3.4 审计回溯字段（可选，微调/复核用）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `originalPlannedCoordinates` | array\<number,2\> | 41/61 | 审计回溯用的原始规划坐标（人工微调前的位置） |
| `originalSourceNodeId` | string | 11/61 | 原始来源拓扑节点 ID（位置微调后节点变更时记录） |
| `originalSourceNodeType` | string | 11/61 | 原始来源节点类型 |
| `originalLocationDesc` | string | 11/61 | 原始位置描述。**并非全部含 `原:` 前缀**：实测 11 枚中仅 4 枚含（如 `1F 交叉口（交叉口55） · 原: 交叉口11`），其余 7 枚为 `1F 交叉口（交叉口31）`、`1F 交叉口（交叉口12 · 南向）` 这类无 `原:` 的形态（记录的是重编号后的描述） |

## 4. `summary` 统计汇总

```json
{
  "total": 61,
  "byFloor":   { "1": 52, "2": 9 },
  "bySemantic":{ "trilateration_route_base": 43,
                 "trilateration_route_fill": 10,
                 "manual_deploy": 8 },
  "byMount":   { "door_frame": 7, "wall": 54 }
}
```

> ⚠️ `summary.total` 必须与 `beacons.length` 一致。本版为 61，与正文一致；历史上曾出现扩容后 summary 未同步（total=61 但正文 98）的数据质量缺陷，已通过回滚修正。

## 5. 与地图的关联方式（消费方须知）

1. **拓扑节点关联**：`sourceNodeId` 应能在 `school_building_01_map_v9.geojson` 的 `topology.nodes` 中找到对应 `id`；导航引擎据此把信标挂到房间中心(TR)/门(TD)/走廊交汇(TI)节点上。
2. **房间关联**：`adjacentRooms` 中的房间 ID 应能在 geojson 的房间要素中找到；用于"进入缓冲带→播报经过房间"等 L5 交互。
3. **坐标对齐**：`coordinates` 与 geojson 共用同一米制坐标系，可直接叠加渲染，无需额外变换。
4. **iBeacon 广播**：定位端（`fingerprint-collector` 小程序、`validate_beacon_deployment.py`）按 `uuid`+`major`+`minor` 扫描匹配，再据 `coordinates`/`floor` 做三点/指纹定位。

## 6. 版本与变更说明

| 版本 | commit | 信标数 | 说明 |
|---|---|---|---|
| 密度修复前基线 | `88c3549` | 61 | 人工部署 + adjacentRooms 注入（R6） |
| P0 密度修复 | `44c2658` | 98 | F1 按 5m 有效范围补点实现全覆盖、降 GDOP 退化 |
| **当前生效（回滚）** | `fix-rollback-ble-61` @ `533a006` | **61** | 按用户要求撤销 P0 补点，恢复 `88c3549` 状态 |

> 后续若重新启用 P0 密度方案，应同步更新 `summary` 与 `beacons`，并跑 `validate_beacon_deployment.py` 回归。

## 7. 相关工具与文档

- 生成/编辑：`src/rendering/render_interactive.py`（部署模式拖拽/点选 → 导出 `ble_deployment.json`）
- 校验：`src/tools/validate_beacon_deployment.py`（含 R6：门口/交叉口信标须有 `adjacentRooms`）
- 坐标修正：`src/tools/fix_beacon_coords.py`、`src/tools/improve_p0_coverage.py`
- 语义审计：`docs/17-信标部署语义审计报告.md`
- 字段增补背景：`docs/18-蓝牙信标语义优化TODO.md`（R6 要求注入 `adjacentRooms`）
- 指纹采集消费方：`fingerprint-collector/`（把信标作为分区采集的参考锚点候选）

## 8. 部署时写入 `installHeightM` 的操作指引

> 适用：spec 0.3.1（高度补偿斜距解算）。所有命令均在**仓库根目录**执行。

### 8.1 口径（先读，避免改错层）

- **统一安装高度**：一栋楼只用一个值（当前 `2.2` m），不允许"部分信标用另一个值"当作常态。
- **测量定义**：所在**楼层地面** → 信标**壳体中心**的垂直距离；卷尺读数保留到 `0.1 m`，取值 `> 0`。
- **只有发布包里的 `installHeightM` 参与解算**；台账 `result/ble_deployment.json` 的 `installHeight` 是它的唯一来源。
- 手机参考高度固定 **1.2 m**，是代码常量（后端 `location_solver.py::PHONE_HEIGHT_M`、客户端 `config/env.ts::PHONE_HEIGHT_M`），**不**随本次信标高度调整。
- 缺失或 `≤ 0` 的 `installHeightM` 属部署数据错误：该信标被解算忽略（计入 `ignoredBeaconIds`）；若因此有效信标 < 3，定位判 `lost`、导航暂停并播报原因与措施（不会回退 2D）。
- 管理后台（admin-web）的信标编辑**只能改 `locationDesc`**，`installHeightM` 在后台为只读展示，因此必须走下面 §8.4 的两条发布路径之一。

### 8.2 数据流

```
result/ble_deployment.json  ──installHeight──▶  backend/scripts/convert_map_v9.py::convert_beacons()
        （人工台账，唯一源头）                    （第 165 行：float(b["installHeight"])）
                                                        │
                                                        ▼
                   backend/maps_data/school-building-01/<mapVersion>.json  ──installHeightM──▶  后端解算
```

发布包**不可变**：任何内容变化都必须生成新的 `日期.序号` 版本，禁止原地重写。

### 8.3 步骤 1：现场实测与回填

1. 用放样卡定位每枚信标的地面投影点：`python src/tools/gen_beacon_install_cards.py`
   （产物 `result/beacon_install_cards.html`、`result/beacon_install_survey.csv`，`height_m` 列已预填设计值）。
2. 在回填表对应行记录**实测**高度。
3. 任一枚实测偏离设计值 → **先修正物理安装**再改数据；不得"以数据迁就误装"。

### 8.4 步骤 2：改台账

只改 `installHeight`，不要混入其它字段。逐枚手改或批量改：

```powershell
python -c "import json,pathlib; p=pathlib.Path('result/ble_deployment.json'); d=json.loads(p.read_text(encoding='utf-8')); H=2.2; [b.update({'installHeight': H}) for b in d['beacons']]; p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8'); print('updated', len(d['beacons']), 'beacons ->', H)"
```

> 该命令会按 `indent=2` 重写整个文件。若原文件排版不同，优先逐枚手改；批量改后必须用 `git diff` 确认无其它语义变化。

改完先看差异，确认只有 `installHeight` 变动：

```powershell
git diff --stat result/ble_deployment.json
git diff result/ble_deployment.json
```

> 若批量改后整文件被重排（缩进/键序变化），以 `git diff -w` 复核语义，必要时改回原排版后再提交，避免 diff 噪音掩盖真实改动。

### 8.5 步骤 3：校验台账

```powershell
python src/tools/validate_beacon_deployment.py
```

该脚本规则为 R1/R4/R5/R6/R7，**不检查高度**，因此再单独确认"齐全且统一"：

```powershell
python -c "import json; bs=json.load(open('result/ble_deployment.json',encoding='utf-8'))['beacons']; print('beacons',len(bs)); print('heights',{b.get('installHeight') for b in bs}); print('missing',[b['beaconId'] for b in bs if not isinstance(b.get('installHeight'),(int,float)) or b['installHeight']<=0])"
```

期望输出：`heights` 只有一个值、`missing` 为空列表。

### 8.6 步骤 4：发布新地图版本（二选一）

**新版本号** = 新日期 + 递增序号（示例 `2026-11-01.1`）。先确认当前 latest：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/sites/school-building-01/map?version=latest" |
  Select-Object mapVersion, dataSha256
```

**路径 A：全量重建（仅当当前 latest 没有管理后台编辑）**

```powershell
python backend/scripts/convert_map_v9.py --verify-only --version 2026-11-01.1   # 只校验
python backend/scripts/convert_map_v9.py --version 2026-11-01.1                # 写文件
```

⚠️ `convert_map_v9.py` 以 **v9 GeoJSON 源**为基准全量重建，会**静默回滚**管理后台对 `places`（地名/别名等）的编辑
（参见 `docs/问题与踩坑日志.md` 2026-10-27「重跑 convert_map_v9 会静默丢弃管理后台的地图编辑」）。
若不确定，改用路径 B。

**路径 B：最小补丁（推荐；以 current latest 为基线只改高度）**

```powershell
@'
import json, pathlib
from datetime import datetime, timezone

site = "school-building-01"
base = pathlib.Path("backend/maps_data") / site / "2026-10-30.1.json"   # ← 改成当前 latest
new_version = "2026-11-01.1"
H = 2.2                                                                  # ← 统一安装高度（米）

d = json.loads(base.read_text(encoding="utf-8"))
for b in d["beacons"]:
    b["installHeightM"] = H
d["mapVersion"] = new_version
d["publishedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

out = base.with_name(new_version + ".json")
assert not out.exists(), "版本号已存在：禁止覆盖已发布版本"
out.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", out, len(d["beacons"]), "beacons, installHeightM =", H)
'@ | python -
```

直接覆盖 PowerShell 中的 `python -` 管道即可执行，不需要落临时脚本文件。

发布产物的高度自检（必须全部有值且值统一）：

```powershell
python -c "import json; d=json.load(open('backend/maps_data/school-building-01/2026-11-01.1.json',encoding='utf-8')); bs=d['beacons']; print(d['mapVersion'], len(bs), {b.get('installHeightM') for b in bs}); print('missing',[b['beaconId'] for b in bs if b.get('installHeightM') is None])"
```

### 8.7 步骤 5：让新版本生效并验证

1. **重启后端**（手工新增的包不会被加载，`MapRepository` 只在启动或后台 publish/rollback 时刷新）：

   ```powershell
   backend/.local/start-local.ps1
   ```

2. 核对 latest 已切换（**不能只看磁盘文件名**）：

   ```powershell
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/sites/school-building-01/map?version=latest" |
     Select-Object mapVersion, dataSha256
   ```

3. 客户端需**冷启动 / 重新进入小程序**才会切到新版本。
4. 真机定位验证：导航中 `quality` 应为 `reliable/degraded`。若进入 `recovering`，先确认是否有信标缺 `installHeightM`
   导致有效信标不足 3 枚（`ignoredBeaconIds` 会列出被忽略的信标）。

### 8.8 步骤 6：同步衍生产物

安装高度会出现在现场文档上，发布后必须重新生成，否则卡片仍印旧值：

```powershell
python src/tools/gen_beacon_install_cards.py     # 卡片/回填表的 height_m 取自发布包 installHeightM
python src/tools/gen_beacon_labels.py            # 标签页（含 beaconId 二维码）
```

并按 `docs/25-第二阶段真机联调检查清单.md` §2.1 抽查"现场实际安装高度与地图包一致"。

### 8.9 常见问题对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `map?version=latest` 仍是旧版本 | 手工放包后未重启后端 | 重启 `backend/.local/start-local.ps1` |
| 客户端 416 / sha256 不符 | 同名重写了已发布版本 | 退回旧文件，改为生成新版本号 |
| 地名、别名莫名退回旧值 | 走了路径 A 全量重建，覆盖后台编辑 | 用 `git diff` 逐键比对非信标层，改走路径 B |
| 某枚信标不参与定位 | `installHeightM` 缺失或 ≤0 | 补台账 `installHeight` 并重新发布 |
| 有效信标 < 3、导航停在恢复态 | 高度缺失或同层信标不足 | 按提示原地稍候/移至覆盖区；持续发生则检查部署与包数据 |
| 新增信标高度又是 2.2 | `render_interactive.py` 的部署默认值 | 导出后手改台账，或同步改 `src/rendering/render_interactive.py` L1211 默认值 |
| 定位整体偏移、质量降级 | 台账高度与物理安装不符（个别信标实际不同） | 统一物理安装后重测重发；不得只改数据掩饰 |
