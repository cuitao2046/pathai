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

## 当前进展（v9，2026-08-04 门洞规则修正后实测）
- F1：33 房间 / 138 门 / 10 楼梯 / 3 电梯 / 201 拓扑节点 / 253 边
- F2：22 房间 / 78 门 / 7 楼梯 / 3 电梯 / 113 拓扑节点 / 167 边
- 跨层边 10（楼梯 7 + 电梯 3，全部 matchedBy:code）
- 门类型（F1/F2，2026-08-05 实测）：普通门(swing 摆弧) 74/40、防火门(fire) 64/38、门洞(opening) **9/4**
- 门洞构成：F1 = 卫生间5 + 楼梯间4；F2 = 卫生间4。全部来自 window 层 DK 矢量笔画；普通房门口紧邻摆弧门的 DK 标注已避让，不再生成门洞。
- ⚠️ 注意：本图纸 F1/F2 的 text 实体 DK 补检均为 0——所有 DK 均以 **window 层矢量笔画** 存储，`extract_dk_text_labels` 路径仅作备用；关键修复是 `is_dk_block` 改为「旋转无关」（以块内长笔画主方向定义字形竖直），覆盖竖排/旋转 DK。
- QA：VALIDATION PASS（无门封闭房间=0；孤儿门 61/17 为走廊/楼梯间/防火分区既有现象，非硬失败）

## 关键约定（跨会话必须遵守）
- **目录结构约定（2026-08-04 确立，2026-08-05 删 render_v7 后更新）**：所有**正式脚本**放 `src/`（`parse_cad_pdf.py`、`topology.py`、`render_map.py`、`validate_geojson.py`、`render_interactive.py` 共 5 个）；所有**调试/诊断脚本**放 `debug/`（`debug_*.py`、`diag_*.py`、`glyph_probe.py` 共 22 个）。根目录不再保留 `.py`。`render_v7.py` 及其输入 `school_building_01_map_v7.geojson`、输出 `floor_layout_v7.html` 已由用户手动清理（2026-08-05）。
- **运行环境 / 路径自适应（2026-08-04 改造）**：所有脚本路径均基于 `__file__` 推导。`src/` 正式脚本用 `PROJECT_ROOT = Path(__file__).resolve().parent.parent`（即项目根）；`debug/` 脚本 `sys.path` 指向 `Path(__file__).resolve().parent.parent / "src"`，结果/输入路径用 `Path(__file__).resolve().parent.parent / "result"(/"A20-*.pdf")`（debug 与 src 同在根下一级，故 `parent.parent` 即根）。不再 hardcode `E:\code\pathai`，可直接 `python src/parse_cad_pdf.py`、`python src/render_interactive.py` 运行。`parse_cad_pdf.py` 仍需带 fitz 的 Python（本机 `C:/Users/xinni/AppData/Local/Microsoft/WindowsApps/python3.exe` 含 1.28.0）；`render_interactive.py` / `validate_geojson.py` 用 managed `3.13.12` 即可（纯 JSON，无需 fitz）。`*.bak` 为旧备份，未改。
- **门洞(opening)识别规则（用户 2026-08-04 明确+修正，2026-08-05 扩展）**：门洞 = window 图层带 **DK 编号** 的位置，**仅卫生间/楼梯间有**；其余房间(教室/办公室等)的门是 window 层 **arc based 摆弧门(swing)**，门口的 DK 是门宽/编号标注而非洞口。DK 编号来源包括：
  - **text 实体**：`extract_dk_text_labels(page)` 用正则 `^DK\d+$` 直接匹配（天然支持旋转/竖排）。⚠️ 必须在 `doc.close()` **之前**调用——`parse_floor` 已在 `extract_facility_codes` 后、`doc.close()` 前预取 `dk_text_labels`，否则 page 句柄失效会崩溃（2026-08-05 修复的崩溃 bug）；
  - **矢量曲线**：`recognize_dk_glyph_blocks` + `is_dk_block` 识别笔画（2026-08-05 已改为旋转无关，避免竖排 DK 漏检）。
  二者在 `parse_floor` 中合并去重（8pt）。
- **门洞避让规则**：`find_wall_openings` 对"距任一摆弧门(swing)中心 < DK_NEAR_ARC_PT(22pt) 的 DK 块"直接跳过（避让普通门标注）；归属/临近范围过滤仅保留 staircase/toilet 房间的 opening（stair_box 30pt 兜底，因楼梯间常无 staircase 房间多边形）。
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
