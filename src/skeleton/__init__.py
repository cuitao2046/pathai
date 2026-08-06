# -*- coding: utf-8 -*-
"""PathAI 骨架提取子包：中轴 / 矢量化 / 剪枝 / 交叉口 / 门投影。"""

from .medial_axis import extract_medial_axis, prune_dangling_branches
from .skeleton_vectorize import skeleton_to_graph, graph_to_linestrings
from .junction_detector import detect_junctions, simplify_degree2_paths
from .door_projector import project_doors_to_skeleton, project_points_to_skeleton
from .pipeline import build_skeleton_topology

__all__ = [
    "extract_medial_axis",
    "prune_dangling_branches",
    "skeleton_to_graph",
    "graph_to_linestrings",
    "detect_junctions",
    "simplify_degree2_paths",
    "project_doors_to_skeleton",
    "project_points_to_skeleton",
    "build_skeleton_topology",
]
