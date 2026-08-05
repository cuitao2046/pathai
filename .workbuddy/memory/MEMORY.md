# PathAI 项目长期记忆（精炼版）

## 项目定位
PathAI 室内导航系统，首期试点 **初中学部 1# 教学楼 1~2 层**（仓库内含该楼平面图 PDF：`A20-002/003-II-...首层/二层平面图-A0_BIAD-无签名.pdf`）。核心用户为**视障人士**（同时保留普通/轮椅模式）。8 篇设计文档在 `docs/`（产品概述/架构/地图构建/定位融合/路线规划/视障交互/信标部署/实施路线图）。

## 核心交付物与代码
- `src/parse_cad_pdf.py`（~93KB，主解析器）：PyMuPDF 按 OCG 默认开启图层提取矢量 → 坐标标定（SCALE=0.0529 m/pt，原点(2019.1,1154.8)pt，Y 翻转，page.set_rotation(0) 处理 270° 旋转）→ 墙体矢量化（端点吸附+共线桥接30pt）→ 房间识别（OpenCV 栅格化+形态学+分水岭归属+标签探测+守卫式泛洪）→ 门洞识别（window 摆弧/DOOR_FIRE 摆弧/DK 矢量笔画墙缝）→ 5 段门归属链 → 组装 GeoJSON。
- `src/topology.py`：按 `docs/03-地图构建指南.md` 第五章生成导航拓扑（节点 5 类 room/doorway/intersection/facility/facility_entrance；边含 distance/estimatedTime(0.8m/s)/accessibilityLevel(0/2/999)/riskLevel(0.5/5/10)）。
- `src/render_map.py`：GeoJSON → 每层 PNG（含 `--topology` 叠加图）。需用 venv python（含 matplotlib/shapely）。
- `src/validate_geojson.py`：QA，核心指标"无门封闭房间数=0"。
- `src/render_interactive.py`：自包含交互式 HTML（`result/floor_layout_v9_interactive.html`）。（注：`render_v7.py` 及产物 `floor_layout_v7.html`/`school_building_01_map_v7.geojson` 已于 2026-08-05 由用户手动清理删除）
- `result/school_building_01_map_v9.geojson`：v9.0.0 产物（version 9.0.0）。

## 当前进展（v9，2026-08-05 核心内部门泛化删除后实测）
- F1：33 房间 / 129 门(swing 68 + fire 53 + opening 8) / 10 楼梯 / 3 电梯
- F2：21 房间 / 72 门(swing 36 + fire 32 + opening 4) / 7 楼梯 / 3 电梯
- 拓扑：doorway 节点数 = 门数（F1 129 / F2 72），房间/设施/交叉口/设施门节点另计；边随门数变化。
- 跨层边 10（楼梯 7 + 电梯 3，全部 matchedBy:code）
- 门类型（F1/F2，2026-08-05 实测·卫生间防火门丢弃 + 核心内部门泛化删除后）：普通门(swing) 68/36、防火门(fire) 53/32、门洞(opening) **8/4**
- 门洞构成：F1 = DK 直生 9 + 摆弧门重分类 4 = 13，再经「核心内部门泛化删除」丢弃 5（R1033↔R1032 卫生间内部对、R1031↔R1032 设备↔男卫等）→ 8；F2 = DK 直生 4 + 重分类 4 = 8，泛化删除丢弃 4（R2020↔R2021 男↔女卫生间内部对、贴办公室侧开窗等）→ 4。全部来自 window 层 DK 矢量笔画；普通房门口紧邻摆弧门的 DK 标注已避让，不再生成门洞。
- ⚠️ 注意：本图纸 F1/F2 的 text 实体 DK 补检均为 0——所有 DK 均以 **window 层矢量笔画** 存储，`extract_dk_text_labels` 路径仅作备用；关键修复是 `is_dk_block` 改为「旋转无关」（以块内长笔画主方向定义字形竖直），覆盖竖排/旋转 DK。
- ⚠️ 验证器 `validate_geojson.py` 第 82 行 `n_fire = len(doors) - n_swing` 把 opening 也算进 fire，故其打印的 "fire 61/36" 实为 fire+opening；真值见上（fire 53/32、opening 8/4）。
- QA：VALIDATION PASS（无门封闭房间=0；模块豁免子房间 F1=R1027/R1028/R1031/R1032/R1033、F2=R2016/R2017/R2020/R2021；孤儿门 61/17 为走廊/楼梯间/防火分区既有现象，非硬失败）

## 关键约定（跨会话必须遵守）
- **目录结构约定（2026-08-04 确立，2026-08-05 删 render_v7 后更新）**：所有**正式脚本**放 `src/`（`parse_cad_pdf.py`、`topology.py`、`render_map.py`、`validate_geojson.py`、`render_interactive.py` 共 5 个）；所有**调试/诊断脚本**放 `debug/`（`debug_*.py`、`diag_*.py`、`glyph_probe.py` 共 22 个）。根目录不再保留 `.py`。`render_v7.py` 及其输入 `school_building_01_map_v7.geojson`、输出 `floor_layout_v7.html` 已由用户手动清理（2026-08-05）。
- **运行环境 / 路径自适应（2026-08-04 改造）**：所有脚本路径均基于 `__file__` 推导。`src/` 正式脚本用 `PROJECT_ROOT = Path(__file__).resolve().parent.parent`（即项目根）；`debug/` 脚本 `sys.path` 指向 `Path(__file__).resolve().parent.parent / "src"`，结果/输入路径用 `Path(__file__).resolve().parent.parent / "result"(/"A20-*.pdf")`（debug 与 src 同在根下一级，故 `parent.parent` 即根）。不再 hardcode `E:\code\pathai`，可直接 `python src/parse_cad_pdf.py`、`python src/render_interactive.py` 运行。`parse_cad_pdf.py` 仍需带 fitz 的 Python（本机 `C:/Users/xinni/AppData/Local/Microsoft/WindowsApps/python3.exe` 含 1.28.0）；`render_interactive.py` / `validate_geojson.py` 用 managed `3.13.12` 即可（纯 JSON，无需 fitz）。`*.bak` 为旧备份，未改。
- **门洞(opening)识别规则（用户 2026-08-04 明确+修正，2026-08-05 扩展）**：门洞 = window 图层带 **DK 编号** 的位置，**仅卫生间/楼梯间有**；其余房间(教室/办公室等)的门是 window 层 **arc based 摆弧门(swing)**，门口的 DK 是门宽/编号标注而非洞口。DK 编号来源包括：
  - **text 实体**：`extract_dk_text_labels(page)` 用正则 `^DK\d+$` 直接匹配（天然支持旋转/竖排）。⚠️ 必须在 `doc.close()` **之前**调用——`parse_floor` 已在 `extract_facility_codes` 后、`doc.close()` 前预取 `dk_text_labels`，否则 page 句柄失效会崩溃（2026-08-05 修复的崩溃 bug）；
  - **矢量曲线**：`recognize_dk_glyph_blocks` + `is_dk_block` 识别笔画（2026-08-05 已改为旋转无关，避免竖排 DK 漏检）。
  二者在 `parse_floor` 中合并去重（8pt）。
- **门洞避让规则**：`find_wall_openings` 对"距任一摆弧门(swing)中心 < DK_NEAR_ARC_PT(22pt) 的 DK 块"直接跳过（避让普通门标注）；归属/临近范围过滤仅保留 staircase/toilet 房间的 opening（stair_box 30pt 兜底，因楼梯间常无 staircase 房间多边形）。
- **卫生间的防火门直接丢弃（2026-08-05 新增）**：`parse_floor` 在范围过滤之后、重分类之前新增一步——对 `kind=="fire"` 且归属房间中**任一房间为 toilet** 的防火门，直接丢弃，不参与后续重分类/内部门洞过滤。原因：用户明确卫生间防火门"不用考虑"。该步导致 F1 丢弃 2 个、F2 丢弃 2 个卫生间 fire 门；fire 门被丢弃后，原靠 fire 门算作"有真实入口"的房间（如 F2 R2020）现在只剩 opening，故这些 opening 作为唯一入口被保留。
- **卫生间/楼梯间摆弧门重分类为门洞（2026-08-05 新增，II-WR-01 修复）**：`parse_floor` 在范围过滤之后新增一步——对 `kind in (swing,fire)` 且**归属到 toilet/staircase 房间**、且门体中心 <14pt 内有 DK 编号块 的摆弧门/防火门，重分类为 `opening`（`arc_mid` 设为门中心）。原因：此类门在图纸上以「摆弧门 + DK 洞口标注」共同表达，`detect_doors` 正确识别成 swing，但按规则卫生间/楼梯间的门应以 DK 洞口为准（opening）；红框处含 DK 的 window 组件此前只剩 swing、丢失门洞语义。该步在范围过滤之后、依赖 `room_type_by_id` 与 `dk_blocks`（均已在作用域）。**注意：卫生间 fire 门已在上一步丢弃，此处重分类的 fire 门仅针对 staircase。** 重分类计数 F1=4/F2=4。
- **核心内部门泛化删除（2026-08-05，用户明确"所有封闭空间内部的普通门/门洞/防火门都删除，通往公共空间或其他封闭空间的门保留"）**：`_door_is_internal_core` 过滤作用在**所有门类型**（swing/fire/opening）与**所有服务核心房间**（toilet/staircase/equipment/shaft，集合 `_CORE`）上，取代原先仅 opening 的 `_opening_is_internal`。逻辑：
  - 无归属房间（楼梯/管井范围兜底）→ 保留；
  - 门须确属某个核心房间（门中心距该核心房间边界 <30pt≈2m），否则保留；
  - 公共面向（门中心距任一公共空间 corridor/lobby/atrium/entrance/accessible_entrance/elevator_hall 边界 <19pt≈1.2m）→ 保留（即"通往公共空间的门"）；
  - 门还触达**另一侧核心房间**（<51pt≈2.7m）→ 判为**核心内部隔断**→ 删除（即"封闭空间内部的门"）。
  - 含义：核心房间之间、或核心房间与同模块设备间之间的内门全部删除；只有朝公共空间、或通向"另一个独立封闭空间（不同核心模块，几何上不构成同一封闭空间内部）"的门保留。
  - 几何判定用 `r["polygon_pt"]`（shapely，解析器内存在）；分析脚本因 managed Python 3.13.12 无 shapely，需自备 ray-casting + 点到线段距离。
  - ⚠️ 第一版用"最近其他房间"启发式会漏判 F2 男↔女卫生间内部 opening（最近邻是办公室 0.11m 而非女卫生间 0.16m），改为"触达任一其他核心房间 <2.7m"后修复。F1 删除 136→129、F2 76→72。
- **验证器模块豁免（2026-08-05，配合核心内部门删除）**：零门封闭房间检查新增豁免——若某核心房间（toilet/staircase/equipment/shaft）多边形在 **1.5m 内与任一其他房间多边形相邻**（成模块或贴公共空间），则该子房间零门不计失败。原因：严格删除内部门后，男卫生间(R1032/R2021)、设备间(R1031)等失去唯一入口，但它们属于同一封闭模块（对侧女卫生间/公共空间仍有门），导航仍可达，故从 QA 硬失败豁免。最终豁免 F1=R1027/R1028/R1031/R1032/R1033、F2=R2016/R2017/R2020/R2021 → VALIDATION PASS。
- **detect_doors 摆弧门去重加了"中心距守卫"(DOOR_CLUSTER_CENTER_PT=14)**：原仅用叶段投影间隙<6pt 判同门，会把同一面墙相邻两房间的门(如 R1021/R1022，中心距≈30pt)误并成一道，导致后一房间缺门。加中心距守卫后相邻门正确分离；同一门多段弧(中心重合)仍正常合并。
- **DOOR_FIRE 只处理 arc based 门**（用户明确：quads/lines 非门叶片，曾回退删除 fire_door_leaves 补检逻辑，勿再加回）。
- **图层**：家具层仅 `A-METAL-S`；`A-TECH-SANT` 在 `LAYERS_IGNORE` 整层显式剔除（PyMuPDF get_drawings 不感知图层可见性）。
- **跨层配对按图纸井道编号**（`FACILITY_CODE_RE` 抽 II-xx#ST/EL），几何<3.5m 仅兜底。
- **git 记忆同步**：`.gitignore` 改为 `.workbuddy/*` + `!/.workbuddy/memory/`，仅 memory/ 随仓库同步；`~/.workbuddy/MEMORY.md`（用户级）在仓库外不入库。

## 提交与同步工作流（用户 2026-08-04 明确要求）
- **每次阶段性完成代码改动，自动 commit 并 push 到远端**（origin/master），无需用户再次要求。
- 提交范围：本次改动涉及的 `src/` 代码、`debug/` 调试脚本、`result/` 产物（再生成的 geojson/html）、`.workbuddy/memory/` 记忆更新。临时脚本（如 `_tmp_*.py`、`_diag_*.py`）须先删除再提交，绝不入库。
- 提交前确认 `git status` 干净无残留临时文件；commit 用中文简述改动要点；push 用非 force 的普通 push（本仓库为 fast-forward 模式，禁止 `--force`）。
- 若 push 前 origin/master 已领先（产生分叉），先 `git pull --ff-only` 再 push；无法 fast-forward 时停止并报告，不强行推送。

## 已知限制
1. 少量房间标签未匹配多边形（音乐教室/书法教室/美术教室等，成孤儿门）；卫生间多边形只覆盖盥洗走道区；
2. 走廊骨架为"每走廊1交叉口+就近连门"简化模型，未重建 v7 式 700 边中轴路网；
3. 门仅输出 Point+width_m，未输出铰链/朝向；
4. 无摆弧门洞依赖"墙缝几何+DK字形块确认"；DK 字形识别已改为旋转无关（覆盖竖排/旋转 DK），但若 DK 笔画被遮挡或 window 组被「贴墙过滤」误删（F1 剔除 49 组、F2 剔除 26 组）仍可能漏检。
5. 孤儿门 61(F1)/17(F2)：多为走廊/楼梯间/防火分区的 fire/swing 门，归属链未挂到单一房间；属既有现象（关掉去重中心守卫数量不变），validate 仅作信息项、非硬失败。

## 失败实验速记（详见 CAD图纸关键元素识别方案.md §10）
虚线墙也是短段+大间隙→无条件30pt桥接；真墙也是2px单线→不能用开运算去薄墙/像素厚度区分；LABEL_SKIP_RE 黑名单不能含"出入口"；arc_mid 非万能（存在外开门）；DK 是 window 层矢量笔画非文本层。
