# -*- coding: utf-8 -*-
"""房间类型语义分类：关键词表 ROOM_TYPE_RULES + 标签归类 classify_room_type。

原内嵌于 src/parsing/parse_cad_pdf.py（审查 B1）：
  ROOM_TYPE_RULES / classify_room_type

纯字符串逻辑，不依赖 shapely / 几何 / 拓扑模块。
"""
ROOM_TYPE_RULES = [
    ("卫生间", "toilet"), ("洗手间", "toilet"),
    ("楼梯", "staircase"), ("电梯", "elevator_hall"),
    ("走道", "corridor"), ("走廊", "corridor"), ("过道", "corridor"),
    ("门厅", "lobby"), ("大厅", "lobby"),
    ("门厅无障碍出入口", "accessible_entrance"),
    ("无障碍出入口", "accessible_entrance"),
    ("人防主出入口", "entrance"), ("出入口", "entrance"),
    # 合班教室 = 大型封闭教室，禁止当公共/开放空间
    ("合班教室", "classroom"), ("合班", "classroom"),
    ("教室", "classroom"), ("书法", "classroom"), ("美术", "classroom"),
    ("音乐", "classroom"), ("实验室", "lab"),
    (" resource", "classroom"), (" resource教室", "classroom"),
    ("办公", "office"), ("会议", "meeting"), ("接待", "meeting"),
    ("设备", "equipment"), ("机房", "equipment"), ("配电", "equipment"),
    ("水井", "infrastructure"), ("风井", "infrastructure"), ("排风井", "infrastructure"), ("管井", "infrastructure"), ("井", "infrastructure"),
    ("储藏", "storage"), ("存放", "storage"), ("资料", "storage"), ("档案", "storage"),
    ("广播", "equipment"), ("管控", "equipment"),
    # 饮水处为服务核心内的开敞壁龛（无门，紧贴水井/卫生间模块），
    # 归为服务设备类，纳入「服务核心模块豁免」，避免误判为不可达封闭房间。
    ("饮水", "equipment"),
    ("图书", "library"), ("阅览", "library"),
    ("卫生室", "medical"), ("心理", "counseling"), ("辅导", "counseling"),
    ("活动", "activity"), ("社团", "activity"),
    ("传达", "reception"), ("前台", "reception"),
    ("庭园", "atrium"), ("上空", "atrium"),
]


def classify_room_type(label):
    # 合班教室：始终为封闭教室，不得落入 corridor/lobby/activity 等开放类型
    if label and ("合班教室" in label or ( "合班" in label and "教室" in label)):
        return "classroom"
    if label and "合班" in label and not any(
            k in label for k in ("走道", "走廊", "门厅", "大厅")):
        return "classroom"
    for kw, tp in ROOM_TYPE_RULES:
        if kw in label:
            return tp
    return "room"


# 开放空间类型 → 独立编号前缀（区别于封闭房间的 RM- 系列）；
# 走廊/门厅/大厅/活动/中庭与房间/管井/电梯/楼梯间等封闭空间是不同类型，
# 不能合并编号，故在生成 room id 时分流到各自 OBJ_TYPE 键。
_OPEN_ID_KEY = {
    "corridor": "corridor", "lobby": "lobby",
    "activity": "activity", "atrium": "atrium",
}

# 功能房间统一类型（需求⑳+1）：办公室/实验室/教室等封闭功能房间一律 type="room"，
# 原用途（classroom/office/lab...）落到 roomSubType 子类别；走廊/门厅/楼梯/卫生间/
# 电梯厅/管井/出入口等公共/设施型保持独立 type 不变。
FUNCTIONAL_ROOM_TYPES = {
    "room", "classroom", "lab", "office", "meeting", "storage",
    "equipment", "library", "medical", "counseling", "activity",
    "reception",
}
