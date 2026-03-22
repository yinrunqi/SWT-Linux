"""
schemas.py — Pydantic 结构化输出模型

定义 SolidWorks 建模操作的数据结构，用于 LLM 结构化输出（Structured Output）。
LLM 将用户自然语言指令解析为 SwModelingPlan，其中包含有序的 SwOperation 列表。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OperationType(str, Enum):
    """SolidWorks 操作类型枚举，对应 C# HTTP API 端点。"""
    NEW_PART = "new_part"
    SELECT_PLANE = "select_plane"
    SKETCH_LINE = "sketch_line"
    SKETCH_RECTANGLE = "sketch_rectangle"
    SKETCH_CIRCLE = "sketch_circle"
    FEATURE_EXTRUSION = "feature_extrusion"
    CLOSE_SKETCH = "close_sketch"
    # --- 查询 & 编辑 ---
    FEATURE_TREE = "feature_tree"
    BODY_BOUNDING_BOX = "body_bounding_box"
    SELECT_FACE_AND_SKETCH = "select_face_and_sketch"
    MODIFY_DIMENSION = "modify_dimension"


class SwOperation(BaseModel):
    """
    单个 SolidWorks 操作。

    每个操作对应 C# HTTP API 的一个端点调用。
    参数通过 params 字典传递，具体参数取决于 operation 类型。
    """
    operation: OperationType = Field(
        description="操作类型"
    )
    params: dict = Field(
        default_factory=dict,
        description=(
            "操作参数（键值对）。各操作类型的参数说明：\n"
            "- new_part: 无参数\n"
            "- select_plane: {index: int} — 0=前视面, 1=上视面, 2=右视面\n"
            "- sketch_line: {x1, y1, x2, y2: float} — 起点终点坐标（米）\n"
            "- sketch_rectangle: {x1, y1, x2, y2: float} — 对角线两点坐标（米）\n"
            "- sketch_circle: {cx, cy, radius: float} — 圆心坐标和半径（米）\n"
            "- feature_extrusion: {depth: float} — 拉伸深度（米）\n"
            "- close_sketch: 无参数\n"
            "- feature_tree: 无参数（查询操作，返回特征树含尺寸信息）\n"
            "- body_bounding_box: 无参数（查询操作，返回包围盒）\n"
            "- select_face_and_sketch: {rx, ry, rz, dx, dy, dz: float} — 射线起点和方向（米）\n"
            "- modify_dimension: {featureName: str, dimensionName: str (可选), newValue: float 或 delta: float} — 修改特征尺寸。newValue 设绝对值，delta 设增量（二选一）"
        )
    )
    description: str = Field(
        default="",
        description="对该步骤的简要中文说明"
    )


class SwModelingPlan(BaseModel):
    """
    SolidWorks 建模计划 — LLM 结构化输出的顶层模型。

    包含对用户意图的理解、建模步骤序列以及注意事项。
    """
    user_intent: str = Field(
        description="对用户建模意图的简要中文概括"
    )
    steps: list[SwOperation] = Field(
        description="有序的操作步骤列表，将按顺序执行"
    )
    notes: str = Field(
        default="",
        description="补充说明或注意事项（如尺寸假设、单位换算等）"
    )
