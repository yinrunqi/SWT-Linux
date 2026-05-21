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
    SKETCH_CENTERLINE = "sketch_centerline"
    SKETCH_ARC = "sketch_arc"
    SKETCH_ELLIPSE = "sketch_ellipse"
    FEATURE_EXTRUSION = "feature_extrusion"
    FEATURE_CUT_EXTRUSION = "feature_cut_extrusion"
    FEATURE_REVOLVE = "feature_revolve"
    FEATURE_FILLET = "feature_fillet"
    FEATURE_CHAMFER = "feature_chamfer"
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
            "- sketch_circle: {cx, cy, radius: float} 或 {cx, cy, cz, radius: float} — "
            "在基准面上画圆时用 2D (cx,cy)；在实体面上画圆时**必须用 3D 模型坐标 (cx,cy,cz)**。"
            "⚠️ 例如在 Y=0 底面画圆：cy 必须=0（面的Y坐标），Z方向偏移放 cz（不能放 cy）！"
            "坐标为模型空间坐标（米），C# 端自动转换为草图坐标。radius 控制圆的半径（米），即孔径=radius×2\n"
            "- sketch_centerline: {x1, y1, x2, y2: float} — 中心线（构造线）起点终点坐标（米），"
            "用于旋转特征的旋转轴\n"
            "- sketch_arc: {cx, cy, startX, startY, endX, endY: float, direction: int} — "
            "圆弧的圆心、起点、终点坐标（米）和方向（1=顺时针, -1=逆时针）\n"
            "- sketch_ellipse: {cx, cy, majorRadius, minorRadius: float} 或 {cx, cy, cz, majorRadius, minorRadius: float} — "
            "椭圆的中心坐标（米）、长轴半径（米）、短轴半径（米）。"
            "长轴沿 X 方向，短轴沿 Y 方向（草图坐标系）\n"
            "- feature_extrusion: {depth: float, flipDirection: bool (可选, 默认false), midPlane: bool (可选, 默认false)} — "
            "拉伸深度（米）。⚠️ 在实体面上拉伸时默认方向就是面的外法向量（离开实体的方向），"
            "例如从底面(法向量-Y)拉伸默认就是向下——**不需要 flipDirection=true**！"
            "flipDirection=true 会反转方向导致向实体内部拉伸。"
            "midPlane=true 从草图面两侧各拉伸 depth/2\n"
            "- feature_cut_extrusion: {depth: float, endCondition: str (可选, 默认blind), flipDirection: bool (可选)} — "
            "切除拉伸/打孔（米）。孔的直径由之前 sketch_circle 的 radius 决定。"
            "endCondition 可选: blind(盲孔), through_all(贯穿), mid_plane(中间面), up_to_next(到下一面)\n"
            "- feature_revolve: {angle: float (可选, 默认360), isCut: bool (可选, 默认false), reverseDir: bool (可选)} — "
            "旋转凸台或切除。angle 为旋转角度（度），isCut=true 为旋转切除。"
            "草图中必须包含一条中心线作为旋转轴\n"
            "- feature_fillet: {radius: float, edgeSelection: str (可选, 默认'all')} — "
            "圆角特征。radius 为圆角半径（米）。edgeSelection 可选值：'all'(所有边), 'ray'(射线选边), "
            "或语义化边位置名如 'top_front'(顶面前边)、'top_back'(顶面后边)、'top_left'(顶面左边)、"
            "'top_right'(顶面右边)、'bottom_front'(底面前边)、'front_left'(前面左边)等。"
            "语义化选边强烈推荐——无需计算坐标，C#端自动处理！\n"
            "- feature_chamfer: {type: str, distance1: float, edgeSelection: str (可选, 默认'all')} — "
            "倒角特征。type: 'equal_distance'(等距), 'distance_distance'(不等距), 'distance_angle'(距离-角度)。"
            "distance1 必填（米），distance2 (不等距时, 米)，angle (距离-角度时, 度, 默认45)。"
            "edgeSelection 同 feature_fillet（支持 'all'、'ray'、语义化选边如 'top_front'）\n"
            "- close_sketch: 无参数\n"
            "- feature_tree: 无参数（查询操作，返回特征树含尺寸信息）\n"
            "- body_bounding_box: 无参数（查询操作，返回包围盒）\n"
            "- select_face_and_sketch: {rx, ry, rz, dx, dy, dz: float} — 射线起点和方向（米），用于选中实体面并进入草图编辑\n"
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
