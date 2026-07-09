"""三视图合成生成器工具箱。"""
from .three_view_gen import (
    ExtrudedPart,
    Hole,
    FilletEdges,
    CutoutOnFace,
    render_three_view_png,
    save_groundtruth,
    build_part,
)

__all__ = [
    "ExtrudedPart",
    "Hole",
    "FilletEdges",
    "CutoutOnFace",
    "render_three_view_png",
    "save_groundtruth",
    "build_part",
]
