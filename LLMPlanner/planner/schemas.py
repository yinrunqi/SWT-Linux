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
    SKETCH_SLOT = "sketch_slot"
    FEATURE_EXTRUSION = "feature_extrusion"
    FEATURE_CUT_EXTRUSION = "feature_cut_extrusion"
    FEATURE_REVOLVE = "feature_revolve"
    FEATURE_FILLET = "feature_fillet"
    FEATURE_CHAMFER = "feature_chamfer"
    FEATURE_CIRCULAR_PATTERN = "feature_circular_pattern"
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
            "- sketch_slot: {cx, cy, length, width, angleDeg: float (可选,默认0), cz: float (可选)} — "
            "画一个 length × width 的直线型槽形轮廓（当前实现为中心矩形近似，角是直角而非圆角）。"
            "cx/cy(/cz) 是槽中心的坐标（米），length 沿长轴方向、width 沿短轴方向。"
            "闭合，可直接用于拉伸/切除。用途：钥匙圈孔、螺栓过孔、槽形调整孔等——功能上矩形与真正圆角长圆等价。\n"
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
            "- feature_circular_pattern: {count: int, axis: str (可选,默认'y'), angleDeg: float (可选,默认360), reverseDir: bool (可选)} — "
            "圆周阵列：对**最后一个创建的特征**（拉伸/切除/圆角等）围绕坐标轴均匀复制 count 份（含源特征本身）。"
            "axis 可选 'x'/'y'/'z'（轴穿过坐标原点）。angleDeg 是阵列覆盖的总角度（默认 360°=整圆均分）。"
            "典型用途：齿轮的齿、法兰盘的螺栓孔、通风口等。"
            "**重要**：先建好【一个】源特征（如一个齿、一个孔），再调用 circular_pattern，一步生成所有副本，"
            "不要手工用 sketch 画 N 个圆/多边形绕圈。\n"
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


# ============================================================
# 三视图中间表示（IR）—— 多模态建模第一阶段的结构化输出
# ============================================================


class DimensionSpec(BaseModel):
    """三视图图纸上的一条尺寸标注。"""
    label: str = Field(
        description="尺寸的名称/含义（如 '总长' '孔径' '厚度' '中心距'）"
    )
    value_mm: float = Field(
        description="尺寸数值（毫米）。阶段 B 会统一 ÷1000 转米。"
    )
    applies_to: str = Field(
        default="",
        description="该尺寸作用的对象（如 'front.width' 'hole1.diameter' 'plate.thickness'），"
                    "便于阶段 B 定位到具体的建模参数。"
    )


class ViewProfile(BaseModel):
    """单个视图的轮廓结构。"""
    view_name: str = Field(
        description="视图名。三视图约定使用 'front'(主视图) / 'top'(俯视图) / 'right'(侧视图)"
    )
    base_plane: str = Field(
        description="该视图对应的 SolidWorks 基准面："
                    "front→前视面(index=0), top→上视面(index=1), right→右视面(index=2)"
    )
    outline_desc: str = Field(
        description="该视图轮廓的中文自然语言描述（形状类型、大致轮廓、关键构成元素）。"
                    "示例：'一个 L 形轮廓，外接矩形 60×40mm，内凹缺口 30×20mm 位于右上角'"
    )


class FeatureHint(BaseModel):
    """图纸上的附加特征（孔/圆角/倒角/凹槽等）。"""
    kind: str = Field(
        description="特征种类：'hole'(孔) / 'fillet'(圆角) / 'chamfer'(倒角) / "
                    "'slot'(槽) / 'pocket'(凹坑) / 'boss'(凸台) 等"
    )
    location_desc: str = Field(
        description="该特征在哪个视图/哪个位置。示例：'front 视图左下角，距底边 10mm、距左边 8mm'"
    )
    size_desc: str = Field(
        default="",
        description="该特征的尺寸描述。示例：'⌀5mm 贯穿孔' / 'R3mm 圆角' / '10×5mm 长圆槽'"
    )


class ThreeViewIR(BaseModel):
    """
    三视图中间表示（Intermediate Representation）。

    多模态建模两阶段管线的**阶段 A** 结构化输出：
        输入：一张标注了尺寸的三视图图片（PNG/JPG，可能附文字补充）
        输出：ThreeViewIR（本模型）
    然后阶段 B 会把这个 IR 交给建模 LLM 转换为 SwModelingPlan。

    职责单一：只做"看图 → 结构化描述几何与尺寸"，不排具体 SolidWorks 步骤。
    """
    part_type: str = Field(
        description="零件的顶层类型分类（决定阶段 B 的建模策略）。可选值："
                    "'extrude_prism'(拉伸棱柱) / 'revolve'(旋转体) / "
                    "'plate_with_holes'(带孔平板) / 'stepped_shaft'(阶梯轴) / "
                    "'l_bracket'(L 型支架) / 'flange'(法兰) / 'other'(其它)"
    )
    base_view: str = Field(
        default="front",
        description="主视图对应的基准面（front/top/right）。约定：主视图→前视面(0)、"
                    "俯视图→上视面(1)、侧视图→右视面(2)。"
    )
    overall_size_mm: dict = Field(
        default_factory=dict,
        description="零件的总体包围盒尺寸（毫米），至少含 length/width/height 三个键（可缺则填 0）。"
                    "示例：{'length': 60, 'width': 40, 'height': 20}"
    )
    profiles: list[ViewProfile] = Field(
        default_factory=list,
        description="各视图的轮廓描述列表。至少给出主视图；有俯视图/侧视图也一并列出。"
    )
    dimensions: list[DimensionSpec] = Field(
        default_factory=list,
        description="**逐条枚举图纸上所有可读到的尺寸标注**。这一步至关重要——阶段 B 依赖此列表"
                    "还原绝对尺寸，缺失/误读会直接导致建出的零件尺寸错误。宁可多不可少。"
    )
    features: list[FeatureHint] = Field(
        default_factory=list,
        description="附加特征列表（孔/圆角/倒角/槽/凹坑等），每项都要指明位置和尺寸。"
    )
    assumptions: str = Field(
        default="",
        description="若图纸上某些尺寸未标注/看不清，此处说明所做的合理假设"
                    "（如'厚度未标注，假设为 10mm；孔位对称假设'）"
    )
