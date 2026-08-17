#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import docx

PATH = 'E:/code/pathai/docs/14-项目进展汇报-导师用.docx'
NEW = ('精度达标标准：GDOP × σ_range ≤ 3.0 m（取 σ_range = 1.5 m；'
       '等价 ≤ 2.0 m @ σ_range = 1.0 m）。实测 695 个 ≥3 信标覆盖点，'
       '按高/底比筛选后的「子集内精度达标率」为：≥0.20 时 94.7%，'
       '≥0.25 时 95.9%，≥0.40 时 100%——阈值越高、筛留的点几何越好，'
       '故子集内达标率越高；这与「覆盖占比」（满足阈值的点数/总数，'
       '随阈值升高而减小）方向相反。全体（不限高/底比）的整体达标率 = 90.2%，'
       '等于互斥分桶 Σ(覆盖占比 × 子集达标率)。')

doc = docx.Document(PATH)
for p in doc.paragraphs:
    if '实测 695' in p.text:
        p.runs[0].text = NEW
        print('replaced, style=', p.style.name, 'runs=', len(p.runs))
        break
doc.save(PATH)
print('saved')
