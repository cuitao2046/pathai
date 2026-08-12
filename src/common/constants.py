# -*- coding: utf-8 -*-
"""
PathAI 全局常量唯一来源。

背景：此前 SCALE/BLIND_WALK_SPEED/DOOR_PENALTY 等在 parse_cad_pdf.py、
topology.py、pipeline.py、route_rules.py、render_interactive.py 及各 tools 脚本
中重复定义或裸写，改动易漏（见 docs/code-review-2026-08-12.md D1-D4）。
本模块不依赖任何业务模块，供全项目引用。
"""

# 比例尺校准：轴网 8400mm = 158.8pt（AXIS 层间距众数），
# 与窗编号 M2GW5924(5900mm)=111.5pt 互证。v7 的 0.0644 偏大 22%，已弃用。
SCALE = 0.0529          # 米 / pt
ORIGIN_X = 2019.1       # pt
ORIGIN_Y = 1154.8       # pt

# 步行速度（指南 5.2）
BLIND_WALK_SPEED = 0.8  # 视障步速 0.8 m/s
NORMAL_WALK_SPEED = 1.2  # 普通步行 1.2 m/s

# 路由规则（route_rules / render_interactive 共用，避免两边漂移）
DOOR_PENALTY = {"swing": 0.0, "fire": 0.5, "opening": 1.0}  # 门类型边权惩罚（米）
