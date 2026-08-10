# PathAI 项目长期记忆（精炼版）

## 定位
初中学部 1# 教学楼 1~2 层室内导航（视障核心用户）。平面图 PDF：`A20-002/003-II-...首层/二层平面图-A0_BIAD-无签名.pdf`。设计文档在 `docs/`（8 篇）。

## 代码（src/ 正式脚本，共 12 个）
- `parse_cad_pdf.py`：主解析器。PyMuPDF 按 OCG 提取矢量→坐标标定(SCALE=0.0529 m/pt, 原点(2019.1,1154.8)pt, Y翻转, set_rotation(0) 处理270°旋转)→墙体矢量化→房间识别(OpenCV栅格化+形态学+分水岭+标签探测)→门洞识别(window摆弧/DOOR_FIRE/DK笔画)→门归属链→GeoJSON。需带 fitz 的 Python。
- `topology.py`：导航拓扑定义。**实际产物由 `src/skeleton/pipeline.py` 的 `build_skeleton_topology` 生成**（手动骨架优先时直接取 `result/skeleton_manual_parsed.json` 的 TI/TI-TI/骨架线，跳过中轴提取），topology.py 的 `build_floor_topology` 仅回退路径；改拓扑须两处同步。
- **门节点(TD)生成规则（v9）**：同物理开口门(swing+fire+opening)按 center_m(0.8m)与投影后坐标(1.0m)两次合并为一个 TD；仅当归属≥1封闭房间才建 TD，纯走廊通行门不建 TD（走廊连通性由 TI↔TI 承担）。`validate_geojson.py` 校验「每个 TR 须有 TR↔TD 边」。
- `render_interactive.py`：自包含交互式 HTML(result/floor_layout_v9_interactive.html)。图层开关、缩放/平移(触控板双指)、悬停/点击详情、楼层跳转、拓扑联动高亮、导出所选图层 SVG、拓扑边编辑(双击加边/选中删除/浏览器写回 GeoJSON)、门节点编号(TD-xxxx)、**前端 Dijkstra 路由(对齐 route_rules.py 三条规则：同层禁 facility 中转 / 门优先级 swing>fire>opening / room↔room 经门连公共空间 / 盲模式禁楼梯跨层 / 穿墙 TI↔TI 边剔除+桥边回退)**。
- `render_map.py`：GeoJSON→PNG。
- `validate_geojson.py`：QA，核心指标"无门封闭房间数=0"。
- `route_rules.py`：后端导航规则模块（与前端 Dijkstra 同步）。`DOOR_PENALTY={swing:0,fire:0.5,opening:1}`；`SAME_FLOOR_MID_TYPES={intersection,facility_entrance,doorway}` / `CROSS_FLOOR_MID_TYPES`+facility；`edge_allowed`(盲模式剔 blindAccessible=False / accessibilityLevel=999 / crossFloor+staircase)；`shortest_path` 三层回退(最佳门→所有门→穿墙边 wall_fallback)；`validate_wall_crossing`(doorway/facility/facility_entrance 相邻段豁免，无门卫生间例外放行)；`is_doorless_toilet`+`_add_doorless_toilet_links`(虚拟穿墙边 XR-TW)。
- `fix_wall_crossing.py`：栅格 A* 拓扑清洗工具（独立运行）；因 walkable 在薄隔墙处跨接，对当前数据收益有限，规则正确性已由 route_rules 路由层保证。
- `fix_crossing_edges.py`：仅处理穿封闭房间多边形/管井的边绕行；重路由段仍可能切墙。
- `merge_manual_edges.py` / `generate_fingerprint_grid.py` / `export_skeleton_template.py` / `import_manual_skeleton.py` / `apply_manual_skeleton.py` / `apply_room_overrides.py`：手动边/指纹网格/手工骨架标注闭环/房间属性覆盖。

## 当前进展(v9, 2026-08-10)
房间 F1 81/F2 55；门 F1 132/F2 76；墙 4442 段；跨层边10(楼梯7+电梯3)；合班 F1-RM-0050 真实多边形 211.9m²（v2 射线投票，图纸标注 214.17m²，准确率 98.9%）；骨架 TI F1 63/F2 64；walkable F1 31/F2 16；指纹点 F1 975/F2 467。QA PASS。

## 关键约定
- 目录：正式脚本仅 src/(12个)；调试脚本 debug/；根目录不留.py。探索性副本(src/optimize*/src/adjcent/src/fix/src/pathai_src/debug/conn/debug/heban)已 .gitignore 取消跟踪。
- 路径基于 `__file__` 推导，不 hardcode 绝对路径。
- 骨架模式依赖 scikit-image + networkx；networkx 3.6 在 Py3.14 有 dataclasses bug，需给 configs.py 的 Config 补显式 `__init__`。
- 门洞(opening)=window层带 DK 矢量笔画。DK 旋转无关4方向。几何优先级：① DK距window组<13pt用真实轴/宽并移除该window；② 复用50pt内最近墙缝；③ 吸附最近墙(宽≈1.6m)。
- ⚠️ **门不做合并（用户明确，2026-08-09）**：同一物理开口只允许一扇门，禁止 `dedupe_doorways` 合并(13pt)、`_merge_nearby_doors` 合并(0.8m/1.0m)等任何形式门合并——合并会混叠 rooms 归属（如 F2-TD-0010 出现 ['F2-CR-0042','F2-RM-0005']）导致门归属错误。三处合并逻辑待禁用：`parse_cad_pdf.py:dedupe_doorways`(2984调用)、`skeleton/pipeline.py:_merge_nearby_doors`(499调用)、`topology.py:_merge_nearby_doors`(345调用)。
- 卫生间防火门直接丢弃；卫生间/楼梯间摆弧门(kind∈swing/fire 且有DK<14pt)重分类为 opening(F1=4/F2=4)。
- ⚠️ 合班 `_heban_real_polygon` 陷阱：`cv2.floodFill` 把 newVal 写回**图像**、mask 只置1，取填充区须 `图像==newVal`。
- git：`.workbuddy/memory/` 与 `result/skeleton_manual_parsed.json`(手绘骨架，属代码) 随仓库同步；`result/` 其余产物(geojson/html) 为可复现渲染输出，按铁律纳入提交。⚠️ 编辑 .gitignore 后务必 `git add` 再 commit，否则 merge 中静默丢失。
- ⚠️ **手动骨架优先**：`result/skeleton_manual_parsed.json` 存在时 `build_skeleton_topology(manual_skeleton=...)` 用其 TI/TI-TI/骨架线 替代中轴提取(跳过 medial-axis)，TR/TD/TF/TEN 仍自动挂到手动 TI；F2 孤岛由 pipeline 软桥补边保 100% 连通。`parse_cad_pdf.py` 自动检测，`--no-manual-skeleton` 强制自动。
- ⚠️ **骨架数据必须入库（用户明确，2026-08-10）**：`result/skeleton_manual_parsed.json` 是代码的一部分（含手绘红线 + 红色无填充矩形标注，矩形在 JSON 里以闭合折线线段形式存入骨架 edges，无独立 `rect` 实体），**每次重新生成必须 `git add`+`commit` 到分支，不得只留工作区**；**严禁用 `git checkout <旧提交> -- result/skeleton_manual_parsed.json` 把它回退成旧版**（曾因此导致渲染缺矩形，而 git HEAD 实际含矩形）；其正确性以仓库 HEAD 为准，渲染缺矩形先查工作区是否被本地回退覆盖。重生成命令：`python src/tools/import_manual_skeleton.py --input "C:/Users/Administrator/Downloads/cr_skeleton.svg"`（注意用 `C:/` Windows 路径，非 `/c/`）。
- ⚠️ geojson 字段陷阱：最终 `semantic.rooms` 房间类型在 **`type`** 字段（如 type="elevator_lobby"），**不是** `roomType`；序列化时由中间变量 `roomType` 映射到 `type`。

## 提交工作流
### ⚠️ 铁律 0：禁止直接在 master 上工作（2026-08-09 起）
**所有功能开发、优化、bugfix 一律在新分支上完成**，master 只保留已发布/已合并的稳定状态。流程：
1. 从最新 master 切新分支：`git checkout -b <feature|fix|refactor>/<描述>`（或先 fetch 再切）。
2. 在分支上完成开发 + 记录当日日志（`.workbuddy/memory/YYYY-MM-DD.md`，append-only）。
3. 分支 commit（禁 --force），推送到 GitHub 对应分支（`git push origin <分支>`）。
4. 人工校验通过后合入 master（fast-forward 或 squash，禁 --force），再推 master。
5. **合入的分支默认删除，无需再确认（2026-08-10 起）**：合入 master 后立即删除已合入分支（本地 `git branch -d` + 远端 `git push origin --delete` + `git fetch --prune`），保持仓库整洁。
沙箱 refs 写入受限时（带斜杠分支名常失败），用**无斜杠分支名**（如 `refactor-package-restructure`，单文件 ref 写入稳定），或手动 `mkdir -p .git/refs/heads/<分支>` 再写 ref，或直接更新 master（需用户确认）。

### ⚠️ 铁律 0b：一个分支只包含一个需求的改动（2026-08-10 起，用户明确）
**需求/改动与当前分支正在进行的工作不相关时，必须从最新 master 单独创建新分支实现**，不得在现有 feature 分支上堆叠无关提交。
- 判定标准：该需求是否属于当前分支的主题（如 wall-fallback-visualization 分支只应包含穿墙回退可视化相关改动；"点击任意节点展示详情"这类独立需求必须另开分支）。
- 同一条 feature 分支内的多笔提交必须同主题、同需求；混入无关改动属于违规。
- 若已误堆叠：用 `git reset --hard <本分支最后相关提交>` 摘除无关提交，再从 master 切新分支 `git cherry-pick` 迁走（涉及远端需 force push 时先经用户确认）。

### ⚠️ 铁律：每次功能/优化完成必做两件事
1. **记录当天工作日志**：功能开发或优化完成后，必须把成果追加到 `.workbuddy/memory/YYYY-MM-DD.md`（append-only，不覆盖）。
2. **推送 GitHub**：日志提交后必须 push 到当前分支。沙箱无凭据时，push 由用户终端执行，但**必须明确告知用户"待 push"并列出 commit 清单**，不得静默跳过。

范围 src/代码、debug/脚本、result/产物、.workbuddy/memory/更新；临时脚本先删再提交。阶段性改动直接在分支 commit（禁 --force）。

## 已知限制
1. 少量标签未匹配多边形(音乐/书法/美术教室等孤儿门)；卫生间多边形只覆盖盥洗走道区。
2. 走廊骨架为简化模型，未重建 v7 式700边中轴路网。
3. 门仅 Point+width_m，无铰链/朝向。
4. DK 被遮挡或 window 组被贴墙过滤误删(F1剔49/F2剔26)仍漏检。
5. 孤儿门 F1 61/F2 17，validate 仅信息项。
6. CAD 文字标签包围盒误识别为房间多边形；render_interactive 用 `_is_label_bbox`(面积<6m²且长宽比≥1.5)过滤。
7. 开放/封闭空间分治(OPEN_SPACE_TYPES={corridor,lobby,activity,atrium})，开放空间建 intersection 节点，不得合并处理。
8. 导航穿墙：全部穿墙路线均为 walkable 多边形在薄隔墙处跨接的「桥边回退」，属数据质量问题(独立修复)，非规则逻辑缺陷；路由层已保证 0 可避免穿墙。

## 方案迭代历史（近 6 条，详见各日 .md）
| 日期 | 标题 | 要点 |
|---|---|---|
| 2026-08-05 | 合班教室真实闭合墙体 | `_heban_real_polygon` 局部 MORPH_CLOSE+泛洪重建 190.4m² 真实多边形，替换3m占位 |
| 2026-08-07 | 走廊/教室多边形重叠修复 | `_resolve_open_closed_overlaps` 开放空间与封闭房间取差；F1-CR-0048 565.5→192.6m²，连通覆盖 100% |
| 2026-08-07 | 空间块功能回滚 | 用户去掉空间分解/分类/图层；手动删避免 reset --hard 误删 `9d2838f` |
| 2026-08-08 | 骨架手动标注闭环+导航绕行 | export/import/apply 骨架；fix_crossing_edges 绕行；门编号 TD-xxxx；src 扩至 12 |
| 2026-08-09 | 手动骨架 JSON 入库+pipeline优先 | `build_skeleton_topology(manual_skeleton=)` 有 JSON 用 JSON 无则自动；确定性可复现 |
| 2026-08-09 | 路由规则三层落地 | 新建 `route_rules.py`(三条规则+三层回退)；前端 Dijkstra 对齐；栅格 A* 清洗工具 `fix_wall_crossing.py`；验证全绿 |
| 2026-08-10 | 合班教室射线投票 v2 | `_heban_real_polygon_v2` 替换 v1 形态学闭运算；语义种子+射线投票→211.9m²(98.9%准确率)；零重叠、正确门关联 |

## 失败实验速记
虚线墙=短段+大间隙→无条件30pt桥接；真墙=2px单线→不能开运算去薄墙；LABEL_SKIP_RE 不含"出入口"；arc_mid 非万能(存在外开门)；DK 是 window 层矢量笔画非文本层；宽可见图 O(n²) shapely 在大数据段错误→改 numpy 栅格 A*。
