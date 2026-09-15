# -*- coding: utf-8 -*-
"""生成两份面向导师的正式文档：
   1) docs/14-项目进展汇报-导师用.docx
   2) docs/15-工具材料采购预算说明.docx
依赖 python-docx（已在托管 venv 安装）。
内容基线：截至 2026-09-04 项目状态（信标 61 枚、采集小程序建成、docs/16 亚米级方案）。
"""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

OUT_DIR = r"E:/code/pathai/docs"
REPORT_DATE = "2026-09-04"
os.makedirs(OUT_DIR, exist_ok=True)

# ---------- 字体工具 ----------
def set_eastasia(run, font="宋体"):
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), font)
    rfonts.set(qn('w:ascii'), font)
    rfonts.set(qn('w:hAnsi'), font)

def set_base_font(doc, font="宋体", size=11):
    st = doc.styles['Normal']
    st.font.name = font
    st.font.size = Pt(size)
    rpr = st.element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), font)
    rfonts.set(qn('w:ascii'), font)
    rfonts.set(qn('w:hAnsi'), font)

def heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10 if level == 1 else 6)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.bold = True
    if level == 1:
        r.font.size = Pt(15); set_eastasia(r, "黑体")
    elif level == 2:
        r.font.size = Pt(13); set_eastasia(r, "黑体")
    else:
        r.font.size = Pt(11.5); set_eastasia(r, "黑体")
    return p

def para(doc, text, size=10.5, bold=False, align=None, after=4):
    p = doc.add_paragraph()
    if align: p.alignment = align
    p.paragraph_format.space_after = Pt(after)
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    set_eastasia(r)
    return p

def bullet(doc, text, size=10.5):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(text)
    r.font.size = Pt(size)
    set_eastasia(r)
    return p

def make_table(doc, headers, rows, widths=None, font_size=9.5):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        r = hdr[i].paragraphs[0].add_run(h)
        r.bold = True; r.font.size = Pt(font_size); set_eastasia(r, "黑体")
        tcPr = hdr[i]._tc.get_or_add_tcPr()
        shd = tcPr.makeelement(qn('w:shd'), {qn('w:val'):'clear', qn('w:color'):'auto', qn('w:fill'):'D9E2F3'})
        tcPr.append(shd)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(val))
            r.font.size = Pt(font_size); set_eastasia(r)
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Cm(w)
    return t

def hrule(doc):
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    pbdr = pPr.makeelement(qn('w:pBdr'), {})
    bottom = pbdr.makeelement(qn('w:bottom'), {qn('w:val'):'single', qn('w:sz'):'6', qn('w:space'):'1', qn('w:color'):'999999'})
    pbdr.append(bottom); pPr.append(pbdr)
    p.paragraph_format.space_after = Pt(6)

# =====================================================================
# 文档一：项目进展汇报
# =====================================================================
def build_progress_report():
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = Cm(2.8); sec.right_margin = Cm(2.8)
    sec.top_margin = Cm(2.5); sec.bottom_margin = Cm(2.5)
    set_base_font(doc)

    # 抬头
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("PathAI 室内蓝牙导航项目"); r.bold = True; r.font.size = Pt(20); set_eastasia(r, "黑体")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("项目进展汇报（导师审阅）"); r.bold = True; r.font.size = Pt(15); set_eastasia(r, "黑体")
    para(doc, "初中学部 1# 教学楼 1~2 层 · 视障人士室内导航系统", size=11, align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
    hrule(doc)
    meta = doc.add_table(rows=1, cols=3); meta.style = 'Table Grid'
    mc = meta.rows[0].cells
    for i, (k, v) in enumerate([("汇报日期", REPORT_DATE), ("汇报人", "（请填写）"), ("审阅导师", "（请填写）")]):
        mc[i].text = ""; rr = mc[i].paragraphs[0].add_run(f"{k}：{v}")
        rr.font.size = Pt(10); set_eastasia(rr)
    doc.add_paragraph()

    heading(doc, "一、项目背景与目标", 1)
    bullet(doc, "服务对象：视障人士（核心用户），需在陌生教学楼内独立完成 1~2 层室内导航。")
    bullet(doc, "任务目标：将 CAD 建筑图纸转化为可导航的室内地图，部署蓝牙信标与指纹库，实现米级室内定位与语音导航。")
    bullet(doc, "全链路：CAD PDF → GeoJSON 矢量化 → 交互地图 → 信标部署与语义标注 → 指纹采集（小程序）→ 定位/融合算法 → 视障交互。")
    bullet(doc, "阶段策略：先在测试路线（一层音乐教室一带走廊）达成近实时亚米级定位，验证后再推广至全楼层。")

    heading(doc, f"二、核心进展（截至 {REPORT_DATE}）", 1)

    heading(doc, "2.1 建筑空间数字化（CAD → GeoJSON）", 2)
    bullet(doc, "完成 1# 楼 1~2 层全要素矢量化：房间 F1 81 间 / F2 55 间，门 F1 132 / F2 76，墙体 4442 段。")
    bullet(doc, "拓扑与可导航路网：手动骨架优先，开放-封闭空间分治，生成带门节点（TD）的楼层拓扑。")
    bullet(doc, "质量校验（validate_geojson）：无门封闭房间数 = 0，关键指标全部通过（QA PASS）。")
    bullet(doc, "【本期新增】交互地图可用性修复：墙体等细线要素原先点击无响应（描边过细难以命中），已增加透明命中区；全图层元素 ID 去重完成（重复数 = 0，墙 4442 / 窗 212 / 信标 61 / 拓扑节点 465 / 指纹点 647）。")

    heading(doc, "2.2 信标部署与语义层建设", 2)
    bullet(doc, "现行部署 61 枚（F1 52 + F2 9，含 8 枚手动点位），已完成单坐标 schema 收敛：coordinates 为部署真值，originalPlannedCoordinates 仅作历史追溯，消除「规划坐标与实测坐标混用」的歧义。")
    bullet(doc, "语义质量治理：吸附阈值由 10m 收窄为 3m 且按类型加权（避免走廊中信标被误吸到 6m 外的门口）；新增信标部署 QA 校验器，坐标漂移 >3m 直接报错，当前 ERROR = 0。")
    bullet(doc, "相邻房间（adjacentRooms）已注入 61/61，支撑「正经过音乐教室门口」一类语音提示的数据基础。")
    bullet(doc, "建立五层语义模型（L1 物理层 → L2 定位层 → L3 拓扑层 → L4 语义层 → L5 语音层），明确「定位负责给坐标、语义负责给动作」的职责边界。")

    heading(doc, "2.3 定位精度分析（GDOP × 高/底比）", 2)
    bullet(doc, "误差框架：σ_pos = GDOP × σ_range；距离误差 σ_range = (ln10/10N)·d·σ_RSSI（N=3.5，与距离成正比）。")
    bullet(doc, "精度达标标准：GDOP × σ_range ≤ 3.0 m（取 σ_range = 1.5 m；等价 ≤ 2.0 m @ σ_range = 1.0 m）。需区分两项方向相反的比率：「子集内精度达标率」随高/底阈值升高而升高（≥0.20 时 94.7%、≥0.25 时 95.9%、≥0.40 时 100%），而「覆盖占比」（满足阈值的点数/总数）随阈值升高而减小；全体（不限高/底比）的整体达标率 = 90.2%，等于互斥分桶 Σ(覆盖占比 × 子集达标率)。")
    bullet(doc, "工程判据：推荐最小高/底 = 0.20（保守 0.25）；高/底 < 0.05 为禁用区（最大 GDOP 11.85，误差放大近 12 倍）。")
    bullet(doc, "【本期新增】61 枚方案密度复核（对照 647 个路线点）：标称 7m 半径下覆盖 F1 98.9% / F2 100%，GDOP ≤2 达标率 91.5% / 91.7%；但若有效半径降至 5m，F1 覆盖仅 52.5%、GDOP>3 退化点 30 个（最差 12.0）——见第三部分风险。")

    heading(doc, "2.4 定位算法突破：纯三点定位亚米级误差优化（零指纹）★", 2)
    bullet(doc, "认识修正：建库精度并非瓶颈（直角坐标法已仿真验证 ≤2.83cm、零歧义）；真正的战场是在线手机 RSSI 噪声（σ_d 约 1~3m）与墙体造成的测距偏置。")
    bullet(doc, "关键发现：墙体 NLOS 造成的恒定测距偏置才是主要误差源，必须从测距中减掉（而非靠加权降权）。校正后中位误差由 5.76m 降至 0.38m，≤1m 占比由 6.3% 升至 81.1%。")
    bullet(doc, "性能上界（走廊场景、已知墙体几何）：EKF 跟踪后中位误差 0.29m、P95 0.60m、100% 的点 ≤1m。")
    bullet(doc, "在线实测（用估计位置替代真值做 NLOS 判定）：中位 1.33m、仅 35.9% 的点 ≤1m——尚未达标。根因是粗定位 bootstrap 误差导致单帧 NLOS 分类准确率仅 87~91%，误差沿轨迹累积。")
    bullet(doc, "组合方案（地图匹配粒子滤波粗位 + 几何/RSSI 双校验 + EKF + BLE/IMU 紧耦合）：走廊轨迹中位误差 0.18~0.24m、≤1m 占比 100%（要求 NLOS 分类准确率 ≥95%）。")
    bullet(doc, "时效性：单帧计算 <5ms（等效 >200Hz），粒子滤波 1.3ms/步，计算侧近实时无压力；端到端刷新率取决于 BLE 广播与扫描周期（典型 1~2Hz，可配置至 5~10Hz 即严格亚秒级）。")
    bullet(doc, "投入方向结论：粗位精度由 1m 改善到 0m 可带来 0.7~0.9m 收益，而换更高档信标（σ_d 由 2m 降到 1m）仅改善约 0.2m——精力与预算应投向粗定位子系统，而非硬件升级。")

    heading(doc, "2.5 指纹采集工具链建成", 2)
    bullet(doc, "采集小程序 fingerprint-collector 已开发完成并合入主干：现场扫描 iBeacon、按「分区 + 锚点相对坐标」采集、导出 JSON，并可一键转换为 CSV / SQL 直接入库。")
    bullet(doc, "指纹网格规模：全楼网格 1434 点（2m 间距）、测试路线网格 647 点（1m 间距，已从渲染产物反向恢复）。")
    bullet(doc, "采集双轨：坐标真值用直角坐标法（激光测距，误差 ≤2.83cm），RSSI 用小程序采集，二者按采集点 ID 关联。")

    heading(doc, "2.6 指纹采集有效区与方案仿真", 2)
    para(doc, "在有效区以 0.6m 网格仿真三种定位方法的覆盖与误差（锚点取建筑柱 + 墙角）：", after=2)
    make_table(doc,
        ["采集定位方法", "有效区可用率", "定位误差", "歧义点", "结论"],
        [["两点法（仅柱+墙角）", "27%", "≤8.1cm", "860", "走廊张角退化，不可用"],
         ["三边测量（3+锚点）", "23%", "最大≈48m", "0", "共线 GDOP 退化，不可用"],
         ["直角坐标法（垂直打墙+沿墙量距）", "97.5%/98.8%", "恒定 2.83cm", "0", "✅ 推荐"],
         ["分区间锚点两点法（增强）", "98.7%~99.9%", "≤5.4cm（均值2.1cm）", "少量", "✅ 可用"]],
        widths=[5.2, 3.0, 3.0, 1.8, 3.5])
    bullet(doc, "指纹有效采集空间：1F 1984.86 m²、2F 1127.91 m²，已与建筑坐标系配准，可直接与墙体/柱坐标叠加使用。")

    heading(doc, "2.7 文献调研", 2)
    bullet(doc, "已收集视障导航与 BLE 定位方向相关文献（NavCog 视障导航系统、iBeacon 定位与邻近度、无障碍室内导航综述等），用于算法对标与方案可行性论证。")

    heading(doc, "三、主要问题与风险", 1)
    bullet(doc, "★ 信标有效覆盖半径风险：61 枚方案在标称 7m 下覆盖良好，但真实走廊存在人体遮挡与多径，有效半径常降至约 5m，此时 F1 覆盖降至 52.5%、退化点 30 个。补点方案（+37 枚至 98 枚）已验证可把 @5m 覆盖提升到 100%、退化点降到 1，但当前按 61 枚执行——需现场实测后决策是否追加。")
    bullet(doc, "★ 在线定位尚未达亚米级：在线 NLOS 分类准确率天花板 87~91%，在线中位误差 1.33m、仅 35.9% 的点 ≤1m。必须先把粗定位 bootstrap 做到 <0.5m，再进入几何闭环。")
    bullet(doc, "组合方案验证范围有限：目前仅在走廊直线段 38 步轨迹验证，拐弯、电梯/楼梯、开阔教室区域尚未验证，不能外推为全楼层承诺。")
    bullet(doc, "4 枚人工调整信标（BK-01-008 / 032 / 033 / 036）离交叉口 4~5m，需现场复核是否确实位于走廊中间，必要时修正坐标。")
    bullet(doc, "语义层待补：13 枚走廊填充信标尚无动作语义，约 10 枚位置描述偏工程化、不可直接语音播报，风险等级未做分级。")
    bullet(doc, "降噪方案（多帧中值滤波 + 卡尔曼滤波）已完成设计但尚未实施，当前在线精度仍依赖后续落地。")
    bullet(doc, "数据质量遗留：少量标签未匹配多边形（孤儿门 F1 61 / F2 17）；卫生间多边形仅覆盖盥洗走道区；走廊骨架为简化模型，未重建中轴路网。")
    bullet(doc, "单元测试有 2 个 golden 基线失败（房间解析 F1-RM-0058 等），属既有问题，需刷新基线以确认非回归。")
    bullet(doc, "信标供电与维护：纽扣电池续航 1~2 年，需制定巡检与更换计划。")

    heading(doc, "四、下一步计划", 1)
    bullet(doc, "实现「地图匹配粒子滤波粗定位 + 几何/RSSI 双校验」，把粗位压到 <0.5m、分类准确率提到 >95%，接入 EKF 闭环，目标在线中位误差 ≤1m。")
    bullet(doc, "现场踏勘：61 枚信标安装、4 枚点位复核，并实测有效覆盖半径，以决定是否追加 37 枚补点。")
    bullet(doc, "指纹库首轮采集：小程序采集 RSSI + 直角坐标法建立坐标真值，并按 10~20 点抽查现场配准误差。")
    bullet(doc, "实施降噪（多帧中值滤波 + 卡尔曼滤波），验证 σ_pos 由约 2m 降至约 1m。")
    bullet(doc, "补齐语义层（走廊段 ID、位置描述语音化、风险分级），支撑「经过提示」「危险停顿」等语音事件。")
    bullet(doc, "端到端导航 demo 联调（定位 + 路线规划 + 视障语音交互）。")
    bullet(doc, "补数据质量遗留（孤儿门、卫生间多边形、golden 基线刷新）。")

    heading(doc, "五、资源需求", 1)
    para(doc, "为支撑现场实施，需采购测距定位设备、蓝牙信标、采集终端与辅助耗材，详见附件《工具材料采购预算说明》。", after=2)
    bullet(doc, "按当前 61 枚部署 + 10 枚备用 = 71 枚计：激光测距套装约 ¥693；蓝牙信标三档备选 ¥1,917~3,195；辅助耗材 ¥280；采集终端约 ¥2,000（实验室已有则不另购）。")
    bullet(doc, "采集终端建议选用 iOS 手机：采集小程序依赖 iBeacon 扫描，且 iOS 会回报测算距离与发射功率，Android 这两项常为空、仅有 RSSI。")
    bullet(doc, "若现场实测有效半径确为 5m 级，需追加 37 枚信标，信标支出增至 ¥2,916~4,860。")
    bullet(doc, "按算法侧结论，精度提升主要来自粗定位与融合算法，不建议追加高单价测距硬件。")

    doc.save(os.path.join(OUT_DIR, "14-项目进展汇报-导师用.docx"))
    print("OK progress report")

# =====================================================================
# 文档二：采购预算说明
# =====================================================================
def build_budget():
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = Cm(2.8); sec.right_margin = Cm(2.8)
    sec.top_margin = Cm(2.5); sec.bottom_margin = Cm(2.5)
    set_base_font(doc)

    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("PathAI 项目 工具材料采购预算说明"); r.bold = True; r.font.size = Pt(18); set_eastasia(r, "黑体")
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("（请导师审批）"); r.font.size = Pt(12); set_eastasia(r)
    hrule(doc)
    meta = doc.add_table(rows=1, cols=3); meta.style = 'Table Grid'
    mc = meta.rows[0].cells
    for i, (k, v) in enumerate([("申请人", "（请填写）"), ("日期", REPORT_DATE), ("导师审批", "（签字）____________")]):
        mc[i].text = ""; rr = mc[i].paragraphs[0].add_run(f"{k}：{v}")
        rr.font.size = Pt(10); set_eastasia(rr)
    doc.add_paragraph()

    heading(doc, "一、采购背景与依据", 1)
    bullet(doc, "为支撑初中学部 1# 教学楼 1~2 层视障导航系统的现场实施，需采购：①指纹采集定位设备（用于建立坐标真值）；②蓝牙信标（定位基础设施）；③采集终端与辅助耗材。")
    bullet(doc, "测距设备选型依据：仿真验证直角坐标法可实现约 100% 覆盖、误差 ≤2.83cm、零歧义，对锚点几何无严苛要求（建筑内任意点距墙 <30m 均满足），故采用经济型组合即可，无需万元级全站仪。")
    bullet(doc, "信标数量依据：当前部署方案 ble_deployment.json 登记 61 枚（F1 52 + F2 9，含 8 枚手动点位），另购 10 枚备用。")
    bullet(doc, "精度投入依据：docs/16 敏感性分析表明，在线定位误差的主因是粗定位 bootstrap 精度而非测距噪声——粗位由 1m 改善到 0m 可带来 0.7~0.9m 收益，而换更高档信标（σ_d 由 2m 降到 1m）仅改善约 0.2m。因此预算应优先保障信标数量与现场标定，而非追求高单价信标型号。")

    heading(doc, "二、采购清单", 1)

    heading(doc, "A. 指纹采集定位设备（建库坐标测量）", 2)
    make_table(doc,
        ["序号", "物品", "规格", "单价(¥)", "数量", "小计(¥)", "用途"],
        [["A1", "激光测距仪", "纯距离，±2mm（得力/优利德/米家）", "169", "2", "338", "测垂距与沿墙距离"],
         ["A2", "全景云台", "带水平刻度环+双水平泡", "200", "1", "200", "固定激光尺+水平旋转扫描"],
         ["A3", "设备夹具/快装板", "夹持激光尺机身", "30", "1", "30", "云台固定"],
         ["A4", "三脚架", "金属，1/4螺口", "100", "1", "100", "架设于锚点"],
         ["A5", "卷尺", "5-10m", "25", "1", "25", "沿墙量距"],
         ["", "合计", "", "", "", "693", ""]],
        widths=[1.0, 2.6, 4.3, 1.8, 1.2, 1.8, 3.3])
    para(doc, "京东在售参考价：A1 得力 50m±2mm 测距仪约 ¥150–170；A2 水平刻度环全景云台约 ¥200–400（徕图 LB-60N 约 ¥403）；A3 专用夹具/快装板约 ¥20–40；A4 数魅 S-MT14 迷你三脚架约 ¥77；A5 得力 8210 钢卷尺 10m 约 ¥28。上表单价为预算估算值，实际以下单时为准。", size=9, after=4)

    heading(doc, "B. 蓝牙信标（三档选型 × 两方案数量）", 2)
    para(doc, "数量口径：方案 I（推荐，按当前部署）61 枚部署 + 10 备用 = 71 枚；方案 II（风险预案）若现场实测有效覆盖半径因人体遮挡/多径降至约 5m，按 P0 测算需补 37 枚，即 98 部署 + 10 备用 = 108 枚。建议先按方案 I 采购，现场实测后再决定是否追加。", size=9.5, after=4)
    make_table(doc,
        ["档位", "品牌 / 型号", "芯片方案", "单价(¥)", "方案I 71枚小计", "方案II 108枚小计"],
        [["档1（首选·Minew）", "E5 领航型定位信标", "Nordic nRF52（BLE 5.x）", "45", "3,195", "4,860"],
         ["档2（替代A·国产）", "E5 国产芯片版", "博通集成 BK / 泰凌微 Telink", "35", "2,485", "3,780"],
         ["档3（替代B·预算版）", "RF-STAR 信驰达 RF-B-SR1", "Silicon Labs EFR32BG22", "27", "1,917", "2,916"]],
        widths=[2.6, 3.4, 3.2, 1.3, 2.4, 2.4])
    para(doc, "档1 为进口 Nordic 芯片，RSSI 稳定性与兼容性最佳；档2/档3 为替代品牌，单价更低。三档均需统一配置为发射功率 −10dBm、挂装高度 2.2m（项目语义规范要求全站一致）。档3 已依据用户手册确认 UUID/Major/Minor/发射功率/广播间隔均可写、8m 外稳定扫描。批量采购（100+）通常可再降 10~20%。", size=9, after=4)

    heading(doc, "C. 采集终端", 2)
    make_table(doc,
        ["序号", "物品", "规格", "单价(¥)", "数量", "小计(¥)", "用途"],
        [["C1", "iOS 手机（iPhone）", "支持 iBeacon 扫描，微信真机调试", "2,000", "1", "2,000", "运行指纹采集小程序 + 导航 demo（实验室已有则不采购）"],
         ["", "合计（按需）", "", "", "", "2,000 / 0", ""]],
        widths=[1.0, 2.6, 4.3, 1.8, 1.2, 1.8, 3.3])
    para(doc, "改选 iOS 的理由：已建成的采集小程序基于微信 iBeacon 扫描接口，iOS 会回报测算距离与发射功率，Android 这两项常为空、仅有 RSSI。为保证采集数据完整（含距离真值用于标定），建议用 iPhone。信标批量配置可用厂商免费 App（MinewBeaconAdmin）在同一台手机完成，不另计费。", size=9, after=4)

    heading(doc, "D. 辅助耗材", 2)
    make_table(doc,
        ["序号", "物品", "单价(¥)", "数量", "小计(¥)"],
        [["D1", "编号标签/标记贴纸", "30", "1", "30"],
         ["D2", "平面图纸打印（A1，1~2层）", "50", "2", "100"],
         ["D3", "充电宝/电池", "100", "1", "100"],
         ["D4", "杂项（记号笔、胶带等）", "50", "1", "50"],
         ["", "合计", "", "", "280"]],
        widths=[1.4, 6.0, 2.4, 1.6, 2.4])

    heading(doc, "三、预算合计", 1)
    para(doc, "测距设备 A（¥693）与辅助耗材 D（¥280）为固定项，合计 ¥973；信标 B 按三档 × 两方案计；采集终端 C 按需（新购 ¥2,000 / 用现有 ¥0）。", size=9.5, after=4)
    para(doc, "方案 I（71 枚信标）", size=10, bold=True, after=2)
    make_table(doc,
        ["信标选型", "信标小计(¥)", "用现有终端(¥)", "含新购终端(¥)"],
        [["档1 Minew E5（¥45 × 71）", "3,195", "4,168", "6,168"],
         ["档2 E5 国产芯片版（¥35 × 71）", "2,485", "3,458", "5,458"],
         ["档3 RF-B-SR1（¥27 × 71）", "1,917", "2,890", "4,890"]],
        widths=[5.6, 3.0, 3.4, 3.4])
    para(doc, "方案 II（108 枚信标，5m 覆盖风险预案）", size=10, bold=True, after=2)
    make_table(doc,
        ["信标选型", "信标小计(¥)", "用现有终端(¥)", "含新购终端(¥)"],
        [["档1 Minew E5（¥45 × 108）", "4,860", "5,833", "7,833"],
         ["档2 E5 国产芯片版（¥35 × 108）", "3,780", "4,753", "6,753"],
         ["档3 RF-B-SR1（¥27 × 108）", "2,916", "3,889", "5,889"]],
        widths=[5.6, 3.0, 3.4, 3.4])
    para(doc, "计算式：合计 = 信标小计 + 973（A+D）+ 终端（0 或 2,000）。建议先按方案 I 采购（推荐档3 预算版或档1 Minew），验样 2~3 枚走通配置流程；现场实测有效覆盖半径后，再决定是否按方案 II 追加 37 枚。", size=9, after=4)

    heading(doc, "四、可选升级方案（非必须）", 1)
    make_table(doc,
        ["物品", "规格", "参考价(¥)", "说明"],
        [["徕卡 DISTO S910 掌上全站仪", "测距±1mm+测角±1°，蓝牙SDK", "≈14,000", "自动测角，免后视定向，多轮采集更省时"],
         ["徕卡 X3 + DST360 适配器", "测角+三脚架", "≈6,800", "中档，cm 级精度"]],
        widths=[4.5, 4.5, 2.5, 4.5])
    para(doc, "采购优先级建议：按 docs/16 结论，基础款测距设备已满足 ≤3cm 建库精度需求；提升在线定位精度的收益主要来自算法侧（粗定位 bootstrap、NLOS 双校验、EKF/IMU 融合），而非升级测距硬件或换更高档信标。上述升级仅在大规模多轮采集、需显著缩短现场工时时才建议。", size=9, after=4)

    heading(doc, "五、依据与说明", 1)
    bullet(doc, "测距设备：仿真验证直角坐标法对锚点几何无严苛要求（任意点距墙 <30m），激光尺 + 云台即可完成「垂直打墙 + 沿墙量距」，无需测角全站仪。")
    bullet(doc, "信标数量：ble_deployment.json 现行 61 枚（F1 52 + F2 9），10 枚备用；5m 覆盖风险预案需追加 37 枚（P0 测算）。")
    bullet(doc, "终端选型：采集小程序依赖 iBeacon 扫描，iOS 回报测算距离与发射功率，Android 常为空。")
    bullet(doc, "采集工作量：全楼网格 1434 点（2m 间距）、路线网格 647 点（1m）；直角坐标法建库约 12s/点，小程序 RSSI 采集约 6s/点。全量布设墙靶标（约 2592 个）需约 43h 不可取，推荐自然锚点优先 + 稀疏补盲。")
    bullet(doc, "参考文档：docs/13 信标部署质量分析、docs/16 亚米级误差优化、docs/18 语义优化 TODO、fingerprint-collector/README.md 采集小程序说明。")
    bullet(doc, "所有价格取自主流电商公开报价，为估算值，实际以采购时为准。")

    doc.add_paragraph()
    p = doc.add_paragraph(); p.paragraph_format.space_before = Pt(10)
    r = p.add_run("申请人（签字）：________________    日期：__________")
    r.font.size = Pt(11); set_eastasia(r)
    p = doc.add_paragraph()
    r = p.add_run("导师审批（签字）：________________    日期：__________")
    r.font.size = Pt(11); set_eastasia(r)

    doc.save(os.path.join(OUT_DIR, "15-工具材料采购预算说明.docx"))
    print("OK budget")

if __name__ == "__main__":
    build_progress_report()
    build_budget()
    print("ALL DONE")
