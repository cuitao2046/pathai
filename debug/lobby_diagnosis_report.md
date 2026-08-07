# 电梯前室 / 楼梯前室识别诊断报告

> 生成日期：2026-08-07
> 诊断对象：`src/parse_cad_pdf.py::classify_elevator_stair_lobby`
> 运行方式：`python src/parse_cad_pdf.py --no-skeleton`（在 `classify_elevator_stair_lobby` 中临时加入逐空间诊断打印后抓取）
> 原始诊断日志见同目录 `lobby_diagnosis_raw.txt`

---

## 1. 识别逻辑回顾

该函数（T2：公共空间细分类）在 `build_geojson → floor_block` 内、设施井道 `reconcile` 之后被调用，作用是从已识别的 **走廊（corridor）/ 门厅（lobby）** 中细分出更精细的前室节点。

**判定流程：**
1. 收集井道几何：电梯井 `evtr_boxes` + `elevator_hall` 房间多边形 → `elev_geoms`；楼梯井 `stair_boxes` + `staircase` 房间多边形 → `stair_geoms`。
2. 仅对 `type ∈ {corridor, lobby}` 且**不含"出入口"语义**、且面积 `≥ 8 m²` 的空间进行处理。
3. 计算该空间多边形到最近电梯井 / 楼梯井的最短欧氏距离 `d_elev` / `d_stair`。
4. 按下面阈值细分（**电梯前室优先判定**）：

| 目标类型 | 距离条件 | 面积上限 | 优先级 |
|---|---|---|---|
| 电梯前室 `elevator_lobby` | `d_elev < 1.5 m` | `≤ 150 m²` | 高（优先） |
| 楼梯前室 `stair_lobby` | `d_stair < 3.0 m` | `≤ 60 m²` | 低 |

- 面积超过上限的大贯穿走廊 / 门厅保持原类，不被误判。
- 同时贴近电梯与楼梯时**优先电梯前室**（无障碍导航关键节点）。

---

## 2. 总体结果

| 楼层 | 电梯前室 | 楼梯前室 | 小计 |
|---|---|---|---|
| F1 | 3 | 4 | 7 |
| F2 | 6 | 2 | 8 |
| **合计** | **9** | **6** | **15** |

**与仓库 `master`（HEAD）版本 geojson 的前室 id / 数量完全一致** → 识别逻辑稳定、可复现（见第 4.4 节关于产物差异的说明）。

---

## 3. 逐空间判定明细

### F1（候选 20 个）

| id | 类型 | 面积(m²) | d_elev | d_stair | 结果 | 主要依据 |
|---|---|---|---|---|---|---|
| F1-CR-0042 | corridor | 119.0 | 4.30m | 0.18m | NO_LOBBY | 面积超上限 |
| F1-CR-0043 | corridor | ≈60(几何59.11) | 2.35m | 0.23m | NO_LOBBY | **边界漏判（见 4.1）** |
| F1-CR-0044 | corridor | 159.3 | 0.43m | 0.24m | NO_LOBBY | 面积超 150（电梯不满足） |
| F1-LB-0045 | lobby | — | — | — | SKIP | 出入口语义 |
| F1-CR-0048 | corridor | 567.7 | 7.41m | 0.08m | NO_LOBBY | 面积超上限 |
| F1-LB-0049 | lobby | 883.4 | 20.16m | 0.24m | NO_LOBBY | 距离/面积均不满足 |
| F1-CR-0050 | corridor | 135.7 | 0.44m | 0.32m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F1-CR-0051 | corridor | 51.8 | 0.49m | 0.28m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F1-CR-0052 | corridor | 186.9 | 3.94m | 0.39m | NO_LOBBY | 距离/面积均不满足 |
| F1-CR-0053 | corridor | 98.3 | 0.44m | 0.17m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F1-CR-0054 | corridor | 178.6 | 3.40m | 0.00m | NO_LOBBY | 面积超上限（紧贴楼梯但过大） |
| F1-CR-0055 | corridor | 49.5 | 2.54m | 0.28m | **STAIR_LOBBY** | d_stair<3 |
| F1-CR-0056 | corridor | 126.8 | 3.46m | 0.24m | NO_LOBBY | 面积超上限 |
| F1-LB-0057 | lobby | — | — | — | SKIP | 出入口语义 |
| F1-CR-0058 | corridor | 6.1 | — | — | SKIP | 面积<8 |
| F1-LB-0059 | lobby | — | — | — | SKIP | 出入口语义 |
| F1-CR-0060 | corridor | 24.5 | 17.58m | 0.04m | **STAIR_LOBBY** | d_stair<3 |
| F1-CR-0061 | corridor | 8.4 | 20.49m | 0.09m | **STAIR_LOBBY** | d_stair<3 |
| F1-CR-0062 | corridor | 10.8 | 16.30m | 1.27m | **STAIR_LOBBY** | d_stair<3 |
| F1-CR-0063 | corridor | 6.1 | — | — | SKIP | 面积<8 |

### F2（候选 16 个）

| id | 类型 | 面积(m²) | d_elev | d_stair | 结果 | 主要依据 |
|---|---|---|---|---|---|---|
| F2-CR-0033 | corridor | 19.1 | 17.24m | 15.43m | NO_LOBBY | 距离均不满足 |
| F2-CR-0034 | corridor | 19.7 | 17.51m | 12.68m | NO_LOBBY | 距离均不满足 |
| F2-CR-0035 | corridor | 134.4 | 3.72m | 0.34m | NO_LOBBY | 面积超上限 |
| F2-CR-0036 | corridor | 65.5 | 0.51m | 0.24m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F2-CR-0037 | corridor | 115.5 | 0.44m | 0.00m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F2-CR-0038 | corridor | 143.6 | 6.99m | 0.23m | NO_LOBBY | 距离/面积均不满足 |
| F2-CR-0039 | corridor | 132.7 | 0.44m | 0.32m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F2-CR-0040 | corridor | 63.6 | 0.47m | 0.23m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F2-CR-0041 | corridor | 143.4 | 3.31m | 0.00m | NO_LOBBY | 面积超上限 |
| F2-CR-0042 | corridor | 30.9 | 20.33m | 0.28m | **STAIR_LOBBY** | d_stair<3 |
| F2-CR-0043 | corridor | 7.6 | — | — | SKIP | 面积<8 |
| F2-CR-0044 | corridor | 43.7 | 15.01m | 7.87m | NO_LOBBY | 距离均不满足 |
| F2-CR-0045 | corridor | 33.4 | 20.32m | 0.26m | **STAIR_LOBBY** | d_stair<3 |
| F2-CR-0046 | corridor | 149.7 | 0.43m | 0.34m | **ELEVATOR_LOBBY** | d_elev<1.5（卡 150 上限边界） |
| F2-CR-0047 | corridor | 55.8 | 0.80m | 0.55m | **ELEVATOR_LOBBY** | d_elev<1.5 |
| F2-CR-0048 | corridor | 160.8 | 3.51m | 0.00m | NO_LOBBY | 面积超上限 |

---

## 4. 边界与可疑点分析

### 4.1 `F1-CR-0043` 边界漏判 ⚠️
- 该空间距楼梯仅 **0.23 m（<3.0m）**，最终 geojson 几何面积 **59.11 m²（<60）**，**本应满足楼梯前室条件**。
- 但实际判为 `NO_LOBBY`。根因：**判定使用的面积是裁剪前的 `polygon_pt.area × SCALE²`，约为 60⁺ m²**，恰卡在 `stair_area_max_m2=60` 硬上限之外；而写回 geojson 的几何经过了 T1.5「沿建筑外轮廓裁剪」，面积变为 59.11 m²。
- 即 **判定面积（裁剪前）与输出面积（裁剪后）不一致**，导致 60 m² 上限附近的贴楼梯空间被漏判。

### 4.2 电梯前室粒度过宽（F2 尤甚）
- F2 有 **6 个**电梯前室；其中 `F2-CR-0046`（149.7 m²）**紧贴 150 m² 上限边界**，`F2-CR-0047`（d_elev=0.80m）比其余（0.43~0.51m）离电梯更远。
- 逻辑把「电梯井 1.5m 缓冲内的所有走廊段」一律标为电梯前室，可能包含"只是普通走廊恰好经过电梯井旁"的细长段。
- 对无障碍导航，把到电梯的缓冲空间都标为电梯前室可接受；但若想更精确，应加"该空间主要服务于电梯"的约束（如一端为电梯门、或袋形/尽端空间），避免长走廊段被误标。

### 4.3 紧贴楼梯的大走廊（`F1-CR-0054`，d_stair=0.00m，178 m²）
- 距楼梯 0 m（直接相连）但面积远超上限，保持 `corridor`。
- 当前设计将其视为"楼梯口大缓冲走廊"而非独立楼梯前室，合理；是否要单独标注取决于方案定义，可讨论。

### 4.4 运行环境导致的 geojson 产物差异（不影响前室）
- 本次为抓取诊断而重跑解析器，使 `school_building_01_map_v9.geojson` 产生约 **20 万行 diff**；但前室识别结果（id / 数量）与仓库 `master` 版本**完全一致**。
- 差异来自非前室字段的浮点 / 顶点序列化（本机 managed Python + fitz 1.28.0 与生成 master 版本的环境依赖版本不同），**不影响前室逻辑**。
- 诊断分析完成后，产物已 `git checkout` 还原到 `master` 版本，未污染仓库。

---

## 5. 改进建议

1. **面积阈值留余量，或对齐最终几何**：`stair_area_max_m2` 60 → 70、电梯 150 → 160；更彻底的做法是在 T1.5 裁剪**之后**再调用 `classify_elevator_stair_lobby`，使判定面积与输出面积一致（可同时修复 4.1 的漏判）。
2. **电梯前室加语义约束**：除距离外，要求该空间一端为电梯门或呈袋形，避免长走廊段被整段标为电梯前室。
3. **统一运行环境 / 规范化输出**：固定 fitz/opencv 版本，或在 geojson 输出时对坐标四舍五入、顶点排序，消除环境级 diff（20 万行无意义变更）。
4. **（可选）楼梯口大缓冲标注**：对"紧贴楼梯井且面积 < X"的走廊段直接标 `stair_lobby`，覆盖 `F1-CR-0054` 类场景。

---

## 6. 附：原始诊断日志
见同目录 `lobby_diagnosis_raw.txt`（40 行，含 `[LOBBY-DIAG]` 逐空间判定与 `公共空间细分类 / Walkable Polygon` 汇总）。
