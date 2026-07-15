"""
schemas.py — Pydantic 结构化输出模型

定义 SolidWorks 建模操作的数据结构，用于 LLM 结构化输出（Structured Output）。
LLM 将用户自然语言指令解析为 SwModelingPlan，其中包含有序的 SwOperation 列表。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


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
    FEATURE_LINEAR_PATTERN = "feature_linear_pattern"
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
            "- feature_extrusion: {depth: float, flipDirection: bool (可选, 默认false), midPlane: bool (可选, 默认false), "
            "draftAngleDeg: float (可选, 默认0=无拔模), draftOutward: bool (可选, 默认false=内收)} — "
            "拉伸深度（米）。⚠️ 在实体面上拉伸时默认方向就是面的外法向量（离开实体的方向），"
            "例如从底面(法向量-Y)拉伸默认就是向下——**不需要 flipDirection=true**！"
            "flipDirection=true 会反转方向导致向实体内部拉伸。"
            "midPlane=true 从草图面两侧各拉伸 depth/2。"
            "**拔模拉伸**：draftAngleDeg>0 时启用梯形拉伸——draftOutward=false（默认）表示顶面小于底面（键盘键帽/花盆/漏斗内壁），"
            "draftOutward=true 表示顶面大于底面（漏斗外壁）。角度公式：atan((底半宽-顶半宽)/深度) 度。"
            "例如底 18×18mm、顶 14×14mm、深 8mm → draftAngleDeg=atan(2/8)≈14.04°、draftOutward=false。\n"
            "- feature_cut_extrusion: {depth: float, endCondition: str (可选, 默认blind), flipDirection: bool (可选)} — "
            "切除拉伸/打孔（米）。孔的直径由之前 sketch_circle 的 radius 决定。"
            "endCondition 可选: blind(盲孔), through_all(贯穿), mid_plane(中间面), up_to_next(到下一面)\n"
            "- feature_revolve: {angle: float (可选, 默认360), isCut: bool (可选, 默认false), reverseDir: bool (可选)} — "
            "旋转凸台或切除。angle 为旋转角度（度），isCut=true 为旋转切除。"
            "草图中必须包含一条中心线作为旋转轴\n"
            "- feature_fillet: {radius: float, edgeSelection: str (可选, 默认'all'), "
            "rx,ry,rz,dx,dy,dz: float (edgeSelection='ray' 时必填)} — "
            "圆角特征。轴对齐长方体可用 top_front/top_back 等语义边名，或 'all'/'ray'。"
            "圆柱、轴对称件和台阶轴的局部圆边只能用 edgeSelection='ray' 并提供六个射线参数；"
            "禁止对这些零件使用 bottom_top、bottom_front、top_front 等方盒语义名。\n"
            "- feature_chamfer: {type: str, distance1: float, edgeSelection: str (可选, 默认'all'), "
            "rx,ry,rz,dx,dy,dz: float (edgeSelection='ray' 时必填)} — "
            "倒角特征。type: 'equal_distance'(等距), 'distance_distance'(不等距), 'distance_angle'(距离-角度)。"
            "distance1 必填（米），distance2 (不等距时, 米)，angle (距离-角度时, 度, 默认45)。"
            "edgeSelection 规则同 feature_fillet：长方体可用语义边名；圆柱/轴对称件局部倒角只能用 'ray'。\n"
            "- feature_circular_pattern: {count: int, axis: str (可选,默认'y'), angleDeg: float (可选,默认360), reverseDir: bool (可选), "
            "axisOriginX: float (可选,米), axisOriginY: float (可选,米), axisOriginZ: float (可选,米)} — "
            "圆周阵列：对**最后一个创建的特征**（拉伸/切除/圆角等）围绕**指定圆柱轴**均匀复制 count 份（含源特征本身）。"
            "axis 可选 'x'/'y'/'z'。angleDeg 是阵列覆盖的总角度（默认 360°=整圆均分）。"
            "**多实体零件的关键参数** axisOriginX/Y/Z：显式指定要绕的轴心 3D 坐标（米）。"
            "若零件里只有一个实体（如单个齿轮盘），不传即可（服务端自动找唯一的圆柱面）。"
            "若零件里有**多个实体**（如两个齿轮），**必须传 axisOriginX/Y/Z 显式指定绕哪个圆盘**——"
            "否则服务端会随便选一个圆柱面导致阵列绕错圆心！"
            "例：小齿轮中心在 (X=-35mm, Y=0, Z=0)，阵列小齿轮的齿时传 axisOriginX=-0.035, axisOriginY=0, axisOriginZ=0；"
            "大齿轮中心在 (X=+35mm, Y=0, Z=0)，阵列大齿轮的齿时传 axisOriginX=0.035, axisOriginY=0, axisOriginZ=0。"
            "典型用途：齿轮的齿、法兰盘的螺栓孔、通风口等。"
            "**重要**：先建好【一个】源特征（如一个齿、一个孔），再调用 circular_pattern，一步生成所有副本，"
            "不要手工用 sketch 画 N 个圆/多边形绕圈。\n"
            "- feature_linear_pattern: {num1: int, spacing1: float, dir1Axis: str (可选,默认'x'), "
            "num2: int (可选,默认1), spacing2: float (可选), dir2Axis: str (可选,默认'y'), "
            "flip1: bool (可选), flip2: bool (可选)} — "
            "线性阵列：对**最后一个创建的特征**沿 1~2 个方向复制 num1×num2 份（含源特征本身）。"
            "num1/spacing1/dir1Axis 描述方向 1，num2/spacing2/dir2Axis 描述方向 2；num2=1 表示单向阵列。"
            "spacing 单位是米。dir1Axis 和 dir2Axis 必须不同。dir1Axis/dir2Axis 可选 'x'/'y'/'z'（沿坐标轴复制）。"
            "flip1/flip2 用于反转方向（默认沿 +axis 方向）。"
            "典型用途：**键盘按键矩阵、格栅通风孔阵、蜂窝板、瓷砖、砖墙**等所有网格阵列。"
            "**重要**：先建好【一个】源特征（如一个键帽、一个孔），再调用 linear_pattern，一步生成 num1×num2 副本；"
            "不要手工画 N 次 sketch + N 次拉伸。前提：实体上必须有沿目标 axis 的直线边"
            "（任何长方体/立方体天然满足，圆盘/球体不满足）。\n"
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


class ModelingMode(str, Enum):
    """建模执行模式。"""

    SINGLE_TURN = "single_turn"
    MULTI_TURN = "multi_turn"


class AgentDecision(str, Enum):
    """Multi-turn 每轮的下一步决策。"""

    ACT = "act"
    FINISH = "finish"
    BLOCKED = "blocked"


class ModelObservation(BaseModel):
    """一次 SolidWorks 状态快照。"""

    features: list[dict] = Field(default_factory=list)
    bounding_box_mm: Optional[dict] = None
    volume_mm3: Optional[float] = None
    last_execution: list[dict] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    state_fingerprint: str = ""


class AgentTurn(BaseModel):
    """LLM 在 Multi-turn 中输出的一轮增量决策。"""

    decision: AgentDecision
    progress_summary: str = ""
    actions: list[SwOperation] = Field(default_factory=list, max_length=5)
    check_summary: str = ""
    next_milestone: str = ""

    @field_validator("actions")
    @classmethod
    def validate_actions_for_decision(
        cls,
        actions: list[SwOperation],
        info,
    ) -> list[SwOperation]:
        decision = info.data.get("decision")
        if decision == AgentDecision.ACT and not actions:
            raise ValueError("decision=act 时 actions 不能为空")
        if decision != AgentDecision.ACT and actions:
            raise ValueError("只有 decision=act 时才允许 actions")
        return actions


class MultiTurnRecord(BaseModel):
    """一次 Observe → Decide → Act → Check 的完整记录。"""

    turn: int
    observation_before: ModelObservation
    decision: AgentTurn
    execution_results: list[dict] = Field(default_factory=list)
    observation_after: Optional[ModelObservation] = None


class MultiTurnResult(BaseModel):
    """Multi-turn Agent 的最终结果。"""

    mode: ModelingMode = ModelingMode.MULTI_TURN
    status: str
    user_goal: str
    turns: list[MultiTurnRecord] = Field(default_factory=list)
    final_observation: ModelObservation
    total_actions: int = 0
    successful_actions: int = 0
    llm_calls: int = 0
    duration_seconds: float = 0.0
    message: str = ""


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
        default="",
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

    @field_validator("assumptions", mode="before")
    @classmethod
    def _coerce_assumptions_to_str(cls, v):
        """兼容 Claude 系列输出的 list[str]：自动 join 成单 string。"""
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)
        return v
