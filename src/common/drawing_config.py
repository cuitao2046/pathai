# -*- coding: utf-8 -*-
"""图纸级配置（审查 B5/D6）：换楼时只需调整此处或 CLI 参数，无需改源码。

外置内容（docs/code-review-2026-08-12.md）：
  B5 图纸配置外置：PDF 路径、输出路径、场馆元信息（venueId/venueName/version）；
  D6 图签区坐标：TITLE_BLOCK_X（x 起点，右侧剔除），图纸专属坐标，随 B5 一并外置。

使用方式：
  - 默认实例：`DEFAULT_CONFIG = DrawingConfig(pdf_f1=..., pdf_f2=..., out_geojson=...)`
    由 parse_cad_pdf 在项目根推导后构造，其余字段用本模块默认值；
  - CLI 覆盖：main() 从 argparse 显式参数经 dataclasses.replace 构造新实例传入
    parse_floor / build_geojson。
"""
from dataclasses import dataclass, replace

# 图签区 x 起点（PDF pt）：右侧剔除（图签/标题栏区域不参与解析）。
# 图纸专属坐标（审查 D6），随 B5 外置，换楼可经 CLI --title-block-x 覆盖。
DEFAULT_TITLE_BLOCK_X = 2900.0

# 场馆元信息默认值（写入 GeoJSON 顶层 venueId/venueName/version）。
# 属校楼级配置（审查 B5），换楼时经 CLI 覆盖或直接改此处。
DEFAULT_VENUE_ID = "school-building-01"
DEFAULT_VENUE_NAME = "初中学部1#教学楼"
DEFAULT_VERSION = "9.0.0"


@dataclass(frozen=True)
class DrawingConfig:
    """一张图纸（一栋楼）的完整解析配置。

    - pdf_f1 / pdf_f2 / out_geojson：输入 PDF 与输出 GeoJSON 路径；
    - title_block_x：图签区 x 起点（右侧剔除），图纸专属坐标（D6）；
    - venue_id / venue_name / version：场馆元信息（B5）。
    """

    pdf_f1: str = ""
    pdf_f2: str = ""
    out_geojson: str = ""
    title_block_x: float = DEFAULT_TITLE_BLOCK_X
    venue_id: str = DEFAULT_VENUE_ID
    venue_name: str = DEFAULT_VENUE_NAME
    version: str = DEFAULT_VERSION

    def with_overrides(self, **kwargs):
        """返回仅覆盖给定字段的新实例（frozen dataclass 复制式更新）。"""
        return replace(self, **{k: v for k, v in kwargs.items() if v is not None})
