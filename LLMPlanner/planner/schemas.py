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
    SKETCH_POLYGON = "sketch_polygon"
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
            "- sketch_rectangle: {x1, y1, x2, y2: float} 或 {x1, y1, z1, x2, y2, z2: float} — "
            "对角线两点坐标（米）。在基准面上画矩形用 2D (x,y)；在实体面上画矩形时**必须传 3D 模型坐标**。"
            "⚠️ 例如在 Y=0.05 顶面画矩形：y1=y2=0.05（面的Y坐标），Z方向偏移放 z1/z2（不能放 y）！\n"
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
            "- sketch_polygon: {cx, cy, circumradius, sides: int, startAngleDeg: float (可选,默认0), cz: float (可选)} — "
            "在草图中画正 n 边形（3~64 边）。circumradius 是**外接圆半径**（即中心到顶点的距离，米），sides 是边数。"
            "startAngleDeg 控制第一个顶点相对于草图 X 轴的角度：正六边形用 startAngleDeg=90 让顶点朝上（'尖朝上'），"
            "用 startAngleDeg=0 让一条边水平（'平放'）。多边形闭合，可直接用于拉伸/切除。"
            "在基准面上画时用 2D 草图坐标 (cx,cy)，在实体面上画时传 3D 模型坐标 (cx,cy,cz)。\n"
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
            "- feature_fillet: {radius: float, edgeSelection: str (可选, 默认'all'), rx,ry,rz,dx,dy,dz: float (edgeSelection='ray' 时必填)} — "
            "圆角特征。radius 为圆角半径（米）。\n"
            "  edgeSelection 选边规则（按零件形状二选一，不可混用）：\n"
            "  A) **轴对齐长方体**（由 sketch_rectangle 拉伸得到）：可用语义化边名 top_front/top_back/top_left/top_right/bottom_front 等，"
            "或 'all'/'ray'。\n"
            "  B) **圆柱/轴对称/台阶轴零件**（由 sketch_circle 拉伸、feature_revolve 等得到）："
            "**只能**用 edgeSelection='ray'，且 params 中**必须**包含 rx,ry,rz,dx,dy,dz 六个参数。"
            "**禁止**使用任何含下划线的语义名（如 bottom_top、bottom_front、top_front 等），否则选边必失败。\n"
            "  圆柱台阶圆边 ray 通用策略：起点在台阶圆边外侧（径向=较小圆柱半径×1.4，轴向=台阶高度+0.001m），"
            "方向斜向圆边（径向朝轴心 dx=-1，轴向略向下 dy=-1，dz=0）。\n"
            "- feature_chamfer: {type: str, distance1: float, edgeSelection: str (可选, 默认'all'), rx,ry,rz,dx,dy,dz: float (ray时必填)} — "
            "倒角特征。type: 'equal_distance'(等距), 'distance_distance'(不等距), 'distance_angle'(距离-角度)。"
            "distance1 必填（米），distance2 (不等距时, 米)，angle (距离-角度时, 度, 默认45)。"
            "edgeSelection 规则同 feature_fillet：长方体可用语义选边；圆柱/轴对称零件只能用 'all' 或 'ray'（ray 时必填六个坐标）\n"
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
