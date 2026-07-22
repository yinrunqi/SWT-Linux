"""
schemas.py — Pydantic 结构化输出模型

定义 SolidWorks 建模操作的数据结构，用于 LLM 结构化输出（Structured Output）。
LLM 将用户自然语言指令解析为 SwModelingPlan，其中包含有序的 SwOperation 列表。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class OperationType(str, Enum):
    """SolidWorks 操作类型枚举，对应 C# HTTP API 端点。"""
    NEW_PART = "new_part"
    SELECT_PLANE = "select_plane"
    SKETCH_LINE = "sketch_line"
    SKETCH_POLYLINE = "sketch_polyline"
    SKETCH_RECTANGLE = "sketch_rectangle"
    SKETCH_CIRCLE = "sketch_circle"
    SKETCH_CIRCLES = "sketch_circles"
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
    SELECT_OBSERVED_FACE_AND_SKETCH = (
        "select_observed_face_and_sketch"
    )
    MODIFY_DIMENSION = "modify_dimension"
    # --- 文档持久化 & 装配体 ---
    SAVE_PART_AS = "save_part_as"
    ASSEMBLY_NEW = "assembly_new"
    ASSEMBLY_OPEN = "assembly_open"
    ASSEMBLY_ADD_COMPONENT = "assembly_add_component"
    ASSEMBLY_FIX_COMPONENT = "assembly_fix_component"
    ASSEMBLY_UNFIX_COMPONENT = "assembly_unfix_component"
    ASSEMBLY_MATE_COINCIDENT = "assembly_mate_coincident"
    ASSEMBLY_MATE_CONCENTRIC = "assembly_mate_concentric"
    ASSEMBLY_MATE_GEAR = "assembly_mate_gear"
    ASSEMBLY_REBUILD = "assembly_rebuild"
    ASSEMBLY_SAVE = "assembly_save"
    ASSEMBLY_CHECK_INTERFERENCE = "assembly_check_interference"
    ASSEMBLY_GET_COMPONENT_TRANSFORM = "assembly_get_component_transform"
    ASSEMBLY_SET_COMPONENT_POSE = "assembly_set_component_pose"
    ASSEMBLY_SET_CRANK_SLIDER_POSE = "assembly_set_crank_slider_pose"
    ASSEMBLY_SET_PLANAR_CHAIN_POSE = (
        "assembly_set_planar_chain_pose"
    )
    ASSEMBLY_SET_RADIAL_DEPLOYABLE_POSE = (
        "assembly_set_radial_deployable_pose"
    )
    ASSEMBLY_SET_PLANETARY_TRAIN_POSE = (
        "assembly_set_planetary_train_pose"
    )
    ASSEMBLY_SET_RADIAL_SLIDERS_POSE = (
        "assembly_set_radial_sliders_pose"
    )
    ASSEMBLY_SET_INDEXED_ROTARY_POSE = (
        "assembly_set_indexed_rotary_pose"
    )
    ASSEMBLY_SET_SYNCHRONIZED_ROTARY_POSE = (
        "assembly_set_synchronized_rotary_pose"
    )
    ASSEMBLY_SET_FOUR_BAR_POSE = "assembly_set_four_bar_pose"
    ASSEMBLY_SET_CAM_FOLLOWER_POSE = (
        "assembly_set_cam_follower_pose"
    )


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
            "- sketch_polyline: {points: [{x,y,z?}, ...], closed: bool} — "
            "批量绘制连续折线；复杂 L/T/H 形闭合轮廓应在一个操作中提交，避免跨轮留下半成品草图\n"
            "- sketch_rectangle: {x1, y1, x2, y2: float} 或 {x1, y1, z1, x2, y2, z2: float} — "
            "对角线两点坐标（米）。在基准面上画矩形用 2D (x,y)；在实体面上画矩形时**必须传 3D 模型坐标**。"
            "⚠️ 例如在 Y=0.05 顶面画矩形：y1=y2=0.05（面的Y坐标），Z方向偏移放 z1/z2（不能放 y）！\n"
            "- sketch_circle: {cx, cy, radius: float} 或 {cx, cy, cz, radius: float} — "
            "在基准面上画圆时用 2D (cx,cy)；在实体面上画圆时**必须用 3D 模型坐标 (cx,cy,cz)**。"
            "⚠️ 例如在 Y=0 底面画圆：cy 必须=0（面的Y坐标），Z方向偏移放 cz（不能放 cy）！"
            "坐标为模型空间坐标（米），C# 端自动转换为草图坐标。radius 控制圆的半径（米），即孔径=radius×2\n"
            "- sketch_circles: {circles: [{cx, cy, cz, radius}, ...]} — "
            "在当前草图中批量绘制多个圆；重复孔或圆形阵列应优先使用此操作，整批只计一个 Agent action\n"
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
            "**拔模拉伸**：draftAngleDeg>0 时启用梯形拉伸——draftOutward=false（默认）表示顶面小于底面，"
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
            "若零件里只有一个轴对称实体，不传即可（服务端自动找唯一的圆柱面）。"
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
            "- select_observed_face_and_sketch: {faceIndex: int} — "
            "按当前 observation.faces 的 index 选择已观察到的平面并进入草图；"
            "Multi-turn 修复时优先使用，避免重新猜射线坐标\n"
            "- modify_dimension: {featureName: str, dimensionName: str (可选), newValue: float 或 delta: float} — 修改特征尺寸。newValue 设绝对值，delta 设增量（二选一）\n"
            "- save_part_as: {savePath: str} — 把活动零件保存为绝对 .SLDPRT 路径\n"
            "- assembly_new: {savePath: str}; assembly_open: {assemblyPath: str}\n"
            "- assembly_add_component: {partPath: str, x,y,z: float} — 插入已保存零件，返回 componentName\n"
            "- assembly_fix_component / assembly_unfix_component: {componentName: str}\n"
            "- assembly_mate_coincident: {component1,plane1,component2,plane2: str}\n"
            "- assembly_mate_concentric: {component1,rx1,ry1,rz1,dx1,dy1,dz1,component2,rx2,ry2,rz2,dx2,dy2,dz2}\n"
            "- assembly_mate_gear: {component1,component2,gearRatioNumerator,"
            "gearRatioDenominator,reverse?}；中心孔圆柱面由 C# 自动解析，不要提供射线\n"
            "- assembly_rebuild / assembly_save / assembly_check_interference: 无参数\n"
            "- assembly_get_component_transform: {componentName: str}\n"
            "- assembly_set_component_pose: {componentName,x,y,z,axisX,axisY,axisZ,angleDeg}\n"
            "- assembly_set_crank_slider_pose: {crankComponent,rodComponent,sliderComponent,"
            "centerX,centerY,centerZ,crankRadius,rodLength,sliderAxisX,sliderAxisY,sliderAxisZ,"
            "rotationAxisX,rotationAxisY,rotationAxisZ,angleDeg,synchronizedComponents?}；"
            "长度单位米，synchronizedComponents 每项含 componentName,x,y,z,ratio,reverse?,phaseDeg?"
            "\n- assembly_set_planar_chain_pose: {baseX,baseY,baseZ,rotationAxisX?,"
            "rotationAxisY?,rotationAxisZ?,baseAngleDeg?,segments:[{length,angleDeg,"
            "componentName?或componentNames?,axisOffsets?}],jointComponents?,endComponents?}；"
            "每段 angleDeg 为相对上一段的关节角，组件局部 +X 沿杆长方向；可让多根平行杆"
            "共享同一段，并返回所有关节点坐标"
            "\n- assembly_set_radial_deployable_pose: {centerX,centerY,centerZ,"
            "deploymentAxisX?,deploymentAxisY?,deploymentAxisZ?,referenceRadialX?,"
            "referenceRadialY?,referenceRadialZ?,hubRadius,runnerRadius,ribLength,"
            "ribAttachmentDistance,stretcherLength,ribDownAngleDeg,runnerComponent,"
            "bays:[{ribComponent,stretcherComponent,phaseDeg?}]}；用于伞骨、径向支架、"
            "可展开天线等重复径向连杆，组件局部 +X 沿杆中心线"
            "\n- assembly_set_planetary_train_pose: {centerX,centerY,centerZ,"
            "axisX?,axisY?,axisZ?,referenceRadialX?,referenceRadialY?,"
            "referenceRadialZ?,inputAngleDeg,stages:[{sunComponent,"
            "carrierComponent,sunTeeth,ringTeeth,planetTeeth,orbitRadius,"
            "axialOffset?,carrierAxialOffset?,planetComponents:[{componentName,"
            "phaseDeg?}]}],outputComponents?:[{componentName,ratio?,reverse?,"
            "phaseDeg?}]}；固定内齿圈多级行星轮系，齿轮局部 +X 为轴线"
            "\n- assembly_set_radial_sliders_pose: {centerX,centerY,centerZ,"
            "axisX?,axisY?,axisZ?,referenceRadialX?,referenceRadialY?,"
            "referenceRadialZ?,radialDistance,sliders:[{componentName,"
            "phaseDeg?,axialOffset?,bodyLength?}]}；用于三爪夹头、径向夹具和"
            "同步滑块，组件局部 +X 沿径向"
            "\n- assembly_set_indexed_rotary_pose: {driverComponent,indexedComponent,inputAngleDeg,"
            "slotCount?,engagementStartDeg?,engagementEndDeg?,reverseOutput?,"
            "continuousComponents?,indexedComponents?}；连续输入在啮合区推动输出轮每周期"
            "分度 360/slotCount，其余区间锁止；同步组件支持 ratio/reverse/phaseDeg"
            "\n- assembly_set_synchronized_rotary_pose: {inputAngleDeg,"
            "rotors:[{componentName,x,y,z,ratio,reverse?,phaseDeg?}],"
            "rotationAxisX?,rotationAxisY?,rotationAxisZ?}；按统一输入角和传动比设置多个转子"
            "\n- assembly_set_four_bar_pose: {groundAX,groundAY,groundAZ,groundBX,"
            "groundBY,groundBZ,inputLength,couplerLength,outputLength,inputAngleDeg,"
            "inputComponent,couplerComponent,outputComponent,assemblyMode?,"
            "rotationAxisX?,rotationAxisY?,rotationAxisZ?,couplerPointX?,"
            "couplerPointY?,couplerPointZ?,couplerPointComponents?}；求解通用平面四杆机构"
            "\n- assembly_set_cam_follower_pose: {driverComponent,followerComponent,"
            "centerX,centerY,centerZ,baseRadius,lift,inputAngleDeg,phaseDeg?,profile?,"
            "rotationAxisX?,rotationAxisY?,rotationAxisZ?,followerAxisX?,"
            "followerAxisY?,followerAxisZ?}；设置通用凸轮从动件姿态"
        )
    )
    description: str = Field(
        default="",
        description="对该步骤的简要中文说明"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_provider_shape(cls, value):
        if not isinstance(value, dict):
            return value
        item = dict(value)
        operation = (
            item.get("operation")
            or item.get("action")
            or item.get("type")
            or item.get("name")
            or item.get("op")
            or item.get("operation_type")
            or item.get("step_type")
            or item.get("endpoint")
        )
        if isinstance(operation, dict):
            operation = (
                operation.get("operation")
                or operation.get("type")
                or operation.get("name")
            )
        if isinstance(operation, str):
            operation = operation.strip().lower().replace("-", "_")
            if operation.startswith("/api/"):
                operation = operation[5:].replace("/", "_")
            item["operation"] = operation
        params = (
            item.get("params")
            or item.get("parameters")
            or item.get("arguments")
            or {}
        )
        if isinstance(params, dict):
            params = dict(params)
            nested_args = (
                params.get("args")
                or params.get("arguments")
            )
            if isinstance(nested_args, dict):
                params = {
                    **nested_args,
                    **{
                        key: nested_value
                        for key, nested_value in params.items()
                        if key not in {"args", "arguments"}
                    },
                }
            reserved = {
                "operation",
                "action",
                "type",
                "name",
                "op",
                "operation_type",
                "step_type",
                "endpoint",
                "params",
                "parameters",
                "arguments",
                "description",
                "comment",
            }
            # Some providers flatten operation arguments beside `op` instead
            # of nesting them under `params`.
            for key, flattened_value in item.items():
                if key not in reserved:
                    params.setdefault(key, flattened_value)
            operation_value = (
                operation.value
                if isinstance(operation, OperationType)
                else str(operation or "").strip().lower().replace("-", "_")
            )
            if operation_value == "sketch_line":
                if "x1" in params and "x2" not in params:
                    params["x2"] = params["x1"]
                if "x2" in params and "x1" not in params:
                    params["x1"] = params["x2"]
                if "y1" in params and "y2" not in params:
                    params["y2"] = params["y1"]
                if "y2" in params and "y1" not in params:
                    params["y1"] = params["y2"]
            aliases_by_operation = {
                "save_part_as": {
                    "savePath": ("path", "filePath", "outputPath"),
                },
                "assembly_new": {
                    "savePath": (
                        "path",
                        "filePath",
                        "assemblyPath",
                        "outputPath",
                    ),
                },
                "assembly_open": {
                    "assemblyPath": ("path", "filePath", "savePath"),
                },
                "assembly_add_component": {
                    "partPath": (
                        "path",
                        "file",
                        "filePath",
                        "part_file",
                        "componentPath",
                    ),
                },
                "assembly_fix_component": {
                    "componentName": ("component", "name"),
                },
                "assembly_unfix_component": {
                    "componentName": ("component", "name"),
                },
            }
            for target, candidates in aliases_by_operation.get(
                operation_value,
                {},
            ).items():
                if target not in params:
                    for candidate in candidates:
                        if candidate in params:
                            params[target] = params[candidate]
                            break
            if operation_value == "feature_cut_extrusion":
                raw_end_condition = str(
                    params.get("endCondition")
                    or params.get("end_condition")
                    or "blind"
                ).strip().lower().replace("-", "_")
                compact_end_condition = raw_end_condition.replace(
                    "_", ""
                ).replace(" ", "")
                end_condition = {
                    "throughall": "through_all",
                    "midplane": "mid_plane",
                    "uptonext": "up_to_next",
                }.get(compact_end_condition, raw_end_condition)
                params["endCondition"] = end_condition
                # SolidWorks' through-all API still requires a numeric D1
                # argument even though the termination geometry ignores it.
                if end_condition in {"through_all", "up_to_next"}:
                    params.setdefault("depth", 1.0)
            if operation_value == "assembly_add_component":
                position = (
                    params.get("position")
                    or params.get("translation")
                    or params.get("translation_m")
                )
                if (
                    isinstance(position, (list, tuple))
                    and len(position) >= 3
                ):
                    for key, coordinate in zip(
                        ("x", "y", "z"),
                        position[:3],
                    ):
                        params.setdefault(key, coordinate)
            if operation_value.startswith("assembly_mate_"):
                for target, candidates in {
                    "component1": ("comp1", "component1Name"),
                    "component2": ("comp2", "component2Name"),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
            if operation_value in {
                "assembly_mate_concentric",
                "assembly_mate_gear",
            }:
                for ray_index in (1, 2):
                    ray = params.get(f"ray{ray_index}")
                    if isinstance(ray, (list, tuple)) and len(ray) >= 6:
                        for name, value in zip(
                            ("rx", "ry", "rz", "dx", "dy", "dz"),
                            ray[:6],
                        ):
                            params.setdefault(f"{name}{ray_index}", value)
            if operation_value == "assembly_mate_gear":
                for target, candidates in {
                    "gearRatioNumerator": (
                        "ratioNumerator",
                        "numerator",
                    ),
                    "gearRatioDenominator": (
                        "ratioDenominator",
                        "denominator",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
            if operation_value == "assembly_set_crank_slider_pose":
                for target, candidates in {
                    "crankComponent": ("crank", "crank_component"),
                    "rodComponent": ("rod", "rod_component"),
                    "sliderComponent": ("slider", "slider_component"),
                    "crankRadius": ("crank_radius",),
                    "rodLength": ("rod_length", "connectingRodLength"),
                    "angleDeg": ("angle", "angle_deg"),
                    "synchronizedComponents": (
                        "synchronized_components",
                        "synchronized",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                synchronized = params.get("synchronizedComponents")
                if isinstance(synchronized, list):
                    normalized_synchronized = []
                    for raw_entry in synchronized:
                        if not isinstance(raw_entry, dict):
                            normalized_synchronized.append(raw_entry)
                            continue
                        entry = dict(raw_entry)
                        if "componentName" not in entry:
                            for candidate in ("name", "component"):
                                if candidate in entry:
                                    entry["componentName"] = entry[candidate]
                                    break
                        position = entry.get("position")
                        if (
                            isinstance(position, (list, tuple))
                            and len(position) >= 3
                        ):
                            for key, coordinate in zip(
                                ("x", "y", "z"),
                                position[:3],
                            ):
                                entry.setdefault(key, coordinate)
                        normalized_synchronized.append(entry)
                    params["synchronizedComponents"] = (
                        normalized_synchronized
                    )
            if operation_value == "assembly_set_planar_chain_pose":
                for target, candidates in {
                    "baseX": (
                        "base_x",
                        "originX",
                        "origin_x",
                        "basePivotX",
                        "base_pivot_x",
                    ),
                    "baseY": (
                        "base_y",
                        "originY",
                        "origin_y",
                        "basePivotY",
                        "base_pivot_y",
                    ),
                    "baseZ": (
                        "base_z",
                        "originZ",
                        "origin_z",
                        "basePivotZ",
                        "base_pivot_z",
                    ),
                    "baseAngleDeg": (
                        "base_angle_deg",
                        "baseAngle",
                    ),
                    "segments": ("links", "chain", "arms"),
                    "jointComponents": (
                        "joint_components",
                        "joints",
                    ),
                    "endComponents": (
                        "end_components",
                        "endEffectors",
                        "end_effectors",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                for target, pivot_key in (
                    ("baseX", "basePivotX"),
                    ("baseY", "basePivotY"),
                    ("baseZ", "basePivotZ"),
                ):
                    if pivot_key in params:
                        params[target] = params[pivot_key]
                params.setdefault("baseX", 0.0)
                params.setdefault("baseY", 0.0)
                params.setdefault("baseZ", 0.0)
                rotation_axis = params.get("rotationAxis")
                if isinstance(rotation_axis, str):
                    axis_name = rotation_axis.strip().lower().lstrip("+")
                    axis_values = {
                        "x": (1.0, 0.0, 0.0),
                        "y": (0.0, 1.0, 0.0),
                        "z": (0.0, 0.0, 1.0),
                        "-x": (-1.0, 0.0, 0.0),
                        "-y": (0.0, -1.0, 0.0),
                        "-z": (0.0, 0.0, -1.0),
                    }.get(axis_name)
                    if axis_values:
                        for key, coordinate in zip(
                            (
                                "rotationAxisX",
                                "rotationAxisY",
                                "rotationAxisZ",
                            ),
                            axis_values,
                        ):
                            params.setdefault(key, coordinate)
                segments = params.get("segments")
                segment_count = (
                    len(segments) if isinstance(segments, list) else 0
                )
                raw_joints = params.get("jointComponents")
                if isinstance(raw_joints, list):
                    normalized_joints = []
                    for index, raw_joint in enumerate(raw_joints):
                        if isinstance(raw_joint, str):
                            joint = {"componentName": raw_joint}
                        elif isinstance(raw_joint, dict):
                            joint = dict(raw_joint)
                            joint.setdefault(
                                "componentName",
                                joint.get("name")
                                or joint.get("component"),
                            )
                        else:
                            normalized_joints.append(raw_joint)
                            continue
                        if joint.get("componentName"):
                            joint.setdefault(
                                "anchorIndex",
                                min(index + 1, segment_count),
                            )
                        normalized_joints.append(joint)
                    params["jointComponents"] = normalized_joints
                raw_end_components = params.get("endComponents")
                if isinstance(raw_end_components, list):
                    normalized_end_components = []
                    end_angle = params.get("endAbsoluteAngleDeg")
                    for raw_end in raw_end_components:
                        if isinstance(raw_end, str):
                            end_item = {"componentName": raw_end}
                        elif isinstance(raw_end, dict):
                            end_item = dict(raw_end)
                            end_item.setdefault(
                                "componentName",
                                end_item.get("name")
                                or end_item.get("component"),
                            )
                        else:
                            normalized_end_components.append(raw_end)
                            continue
                        if end_angle is not None:
                            end_item.setdefault("angleDeg", end_angle)
                        normalized_end_components.append(end_item)
                    params["endComponents"] = normalized_end_components
            if operation_value == "assembly_set_radial_deployable_pose":
                # Providers frequently group shared mechanism dimensions in a
                # nested object while leaving bay instances at the top level.
                # Flatten these semantic containers without overwriting an
                # explicitly supplied top-level value.
                for container_name in (
                    "mechanism",
                    "geometry",
                    "dimensions",
                    "kinematics",
                ):
                    container = params.get(container_name)
                    if isinstance(container, dict):
                        for key, value in container.items():
                            params.setdefault(key, value)
                for target, candidates in {
                    "ribDownAngleDeg": (
                        "rib_down_angle_deg",
                        "ribAngleDeg",
                        "ribAngle",
                        "rib_down_angle",
                        "initialAngleDeg",
                        "deploymentAngle",
                        "deploymentAngleDeg",
                        "deployAngleDeg",
                        "angleDeg",
                    ),
                    "runnerComponent": (
                        "runner_component",
                        "runner",
                        "runnerComponentName",
                        "runnerName",
                        "slidingRunner",
                        "slidingRunnerName",
                        "sliding_runner",
                        "slidingRunnerComponent",
                        "sliderComponent",
                        "sliderName",
                    ),
                    "hubRadius": ("hub_radius", "hubPivotRadius"),
                    "runnerRadius": (
                        "runner_radius",
                        "runnerPivotRadius",
                        "sliderRadius",
                    ),
                    "ribLength": (
                        "rib_length",
                        "mainRibLength",
                        "main_rib_length",
                    ),
                    "ribAttachmentDistance": (
                        "rib_attachment_distance",
                        "ribAttachDistance",
                        "ribAttachment",
                        "ribConnectionDistance",
                        "attachmentDistance",
                        "attachmentPointDistance",
                        "stretcherAttachDistance",
                        "stretcherConnectDistance",
                        "stretcherConnectDist",
                        "stretcherAttachmentDistance",
                    ),
                    "stretcherLength": (
                        "stretcher_length",
                        "linkLength",
                        "supportRodLength",
                    ),
                    "bays": ("links", "radialLinks", "radial_links"),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                center = (
                    params.get("center")
                    or params.get("hubCenter")
                    or params.get("centerXYZ")
                )
                if isinstance(center, (list, tuple)) and len(center) >= 3:
                    for key, coordinate in zip(
                        ("centerX", "centerY", "centerZ"),
                        center[:3],
                    ):
                        params.setdefault(key, coordinate)
                deployment_axis = (
                    params.get("deploymentAxis")
                    or params.get("deployAxis")
                )
                if isinstance(deployment_axis, str):
                    axis_name = (
                        deployment_axis.strip().lower().lstrip("+")
                    )
                    axis_values = {
                        "x": (1.0, 0.0, 0.0),
                        "y": (0.0, 1.0, 0.0),
                        "z": (0.0, 0.0, 1.0),
                        "-x": (-1.0, 0.0, 0.0),
                        "-y": (0.0, -1.0, 0.0),
                        "-z": (0.0, 0.0, -1.0),
                    }.get(axis_name)
                    if axis_values:
                        for key, coordinate in zip(
                            (
                                "deploymentAxisX",
                                "deploymentAxisY",
                                "deploymentAxisZ",
                            ),
                            axis_values,
                        ):
                            params.setdefault(key, coordinate)
                raw_bays = params.get("bays")
                if isinstance(raw_bays, list):
                    normalized_bays = []
                    for index, raw_bay in enumerate(raw_bays):
                        if not isinstance(raw_bay, dict):
                            normalized_bays.append(raw_bay)
                            continue
                        bay = dict(raw_bay)
                        for target, candidates in {
                            "ribComponent": (
                                "rib",
                                "rib_component",
                                "ribName",
                                "ribComponentName",
                            ),
                            "stretcherComponent": (
                                "stretcher",
                                "stretcher_component",
                                "stretcherName",
                                "stretcherComponentName",
                                "linkComponent",
                            ),
                            "phaseDeg": (
                                "phase_deg",
                                "azimuthDeg",
                                "angleDeg",
                            ),
                        }.items():
                            if target not in bay:
                                for candidate in candidates:
                                    if candidate in bay:
                                        bay[target] = bay[candidate]
                                        break
                        bay.setdefault(
                            "phaseDeg",
                            index * 360.0 / max(len(raw_bays), 1),
                        )
                        normalized_bays.append(bay)
                    params["bays"] = normalized_bays
                    # Shared values are also accepted on every bay. This is
                    # equivalent to top-level mechanism geometry and is useful
                    # for providers that emit self-contained repeated items.
                    if normalized_bays:
                        first_bay = normalized_bays[0]
                        for target, candidates in {
                            "ribLength": (
                                "ribLength",
                                "rib_length",
                                "mainRibLength",
                            ),
                            "ribAttachmentDistance": (
                                "ribAttachmentDistance",
                                "ribAttachDistance",
                                "ribConnectionDistance",
                                "attachmentDistance",
                                "attachmentPointDistance",
                            ),
                            "stretcherLength": (
                                "stretcherLength",
                                "stretcher_length",
                                "linkLength",
                            ),
                            "ribDownAngleDeg": (
                                "ribDownAngleDeg",
                                "ribAngleDeg",
                                "angleDeg",
                            ),
                            "runnerComponent": (
                                "runnerComponent",
                                "runner",
                                "runnerName",
                                "sliderComponent",
                                "sliderName",
                            ),
                        }.items():
                            if target not in params:
                                for candidate in candidates:
                                    if candidate in first_bay:
                                        params[target] = first_bay[candidate]
                                        break
                params.setdefault("centerX", 0.0)
                params.setdefault("centerY", 0.0)
                params.setdefault("centerZ", 0.0)
            if operation_value == "assembly_set_planetary_train_pose":
                for container_name in (
                    "mechanism",
                    "geometry",
                    "kinematics",
                ):
                    container = params.get(container_name)
                    if isinstance(container, dict):
                        for key, value in container.items():
                            params.setdefault(key, value)
                for target, candidates in {
                    "inputAngleDeg": (
                        "input_angle_deg",
                        "motorAngleDeg",
                        "driverAngleDeg",
                        "angleDeg",
                    ),
                    "stages": (
                        "gearStages",
                        "planetaryStages",
                        "planetary_stages",
                    ),
                    "outputComponents": (
                        "output_components",
                        "synchronizedOutputs",
                        "outputs",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                center = (
                    params.get("center")
                    or params.get("centerXYZ")
                    or params.get("gearboxCenter")
                )
                if isinstance(center, (list, tuple)) and len(center) >= 3:
                    for key, coordinate in zip(
                        ("centerX", "centerY", "centerZ"),
                        center[:3],
                    ):
                        params.setdefault(key, coordinate)
                axis = params.get("axis") or params.get("rotationAxis")
                if isinstance(axis, str):
                    axis_values = {
                        "x": (1.0, 0.0, 0.0),
                        "y": (0.0, 1.0, 0.0),
                        "z": (0.0, 0.0, 1.0),
                        "-x": (-1.0, 0.0, 0.0),
                        "-y": (0.0, -1.0, 0.0),
                        "-z": (0.0, 0.0, -1.0),
                    }.get(axis.strip().lower().lstrip("+"))
                    if axis_values:
                        for key, coordinate in zip(
                            ("axisX", "axisY", "axisZ"),
                            axis_values,
                        ):
                            params.setdefault(key, coordinate)
                raw_stages = params.get("stages")
                if isinstance(raw_stages, list):
                    normalized_stages = []
                    for raw_stage in raw_stages:
                        if not isinstance(raw_stage, dict):
                            normalized_stages.append(raw_stage)
                            continue
                        stage = dict(raw_stage)
                        for target, candidates in {
                            "sunComponent": (
                                "sun",
                                "sunName",
                                "sun_component",
                            ),
                            "carrierComponent": (
                                "carrier",
                                "carrierName",
                                "carrier_component",
                            ),
                            "sunTeeth": (
                                "sun_teeth",
                                "sunToothCount",
                            ),
                            "ringTeeth": (
                                "ring_teeth",
                                "ringToothCount",
                            ),
                            "planetTeeth": (
                                "planet_teeth",
                                "planetToothCount",
                            ),
                            "orbitRadius": (
                                "orbit_radius",
                                "planetOrbitRadius",
                            ),
                            "planetComponents": (
                                "planets",
                                "planet_components",
                            ),
                            "axialOffset": (
                                "axial_offset",
                                "stageOffset",
                            ),
                            "carrierAxialOffset": (
                                "carrier_axial_offset",
                                "carrierOffset",
                            ),
                        }.items():
                            if target not in stage:
                                for candidate in candidates:
                                    if candidate in stage:
                                        stage[target] = stage[candidate]
                                        break
                        raw_planets = stage.get("planetComponents")
                        if isinstance(raw_planets, list):
                            planets = []
                            for index, raw_planet in enumerate(raw_planets):
                                if isinstance(raw_planet, str):
                                    planet = {
                                        "componentName": raw_planet
                                    }
                                elif isinstance(raw_planet, dict):
                                    planet = dict(raw_planet)
                                    planet.setdefault(
                                        "componentName",
                                        planet.get("name")
                                        or planet.get("component")
                                        or planet.get("planetName"),
                                    )
                                else:
                                    planets.append(raw_planet)
                                    continue
                                planet.setdefault(
                                    "phaseDeg",
                                    index * 360.0
                                    / max(len(raw_planets), 1),
                                )
                                planets.append(planet)
                            stage["planetComponents"] = planets
                        normalized_stages.append(stage)
                    params["stages"] = normalized_stages
                raw_outputs = params.get("outputComponents")
                if isinstance(raw_outputs, list):
                    outputs = []
                    for raw_output in raw_outputs:
                        if isinstance(raw_output, str):
                            output = {"componentName": raw_output}
                        elif isinstance(raw_output, dict):
                            output = dict(raw_output)
                            output.setdefault(
                                "componentName",
                                output.get("name")
                                or output.get("component")
                                or output.get("outputName"),
                            )
                        else:
                            outputs.append(raw_output)
                            continue
                        outputs.append(output)
                    params["outputComponents"] = outputs
                params.setdefault("centerX", 0.0)
                params.setdefault("centerY", 0.0)
                params.setdefault("centerZ", 0.0)
                params.setdefault("outputComponents", [])
            if operation_value == "assembly_set_radial_sliders_pose":
                for target, candidates in {
                    "radialDistance": (
                        "radial_distance",
                        "jawCenterRadius",
                        "openingRadius",
                        "sliderRadius",
                    ),
                    "sliders": (
                        "jaws",
                        "sliderComponents",
                        "slider_components",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                center = (
                    params.get("center")
                    or params.get("centerXYZ")
                    or params.get("chuckCenter")
                )
                if isinstance(center, (list, tuple)) and len(center) >= 3:
                    for key, coordinate in zip(
                        ("centerX", "centerY", "centerZ"),
                        center[:3],
                    ):
                        params.setdefault(key, coordinate)
                axis = params.get("axis") or params.get("rotationAxis")
                if isinstance(axis, str):
                    axis_values = {
                        "x": (1.0, 0.0, 0.0),
                        "y": (0.0, 1.0, 0.0),
                        "z": (0.0, 0.0, 1.0),
                        "-x": (-1.0, 0.0, 0.0),
                        "-y": (0.0, -1.0, 0.0),
                        "-z": (0.0, 0.0, -1.0),
                    }.get(axis.strip().lower().lstrip("+"))
                    if axis_values:
                        for key, coordinate in zip(
                            ("axisX", "axisY", "axisZ"),
                            axis_values,
                        ):
                            params.setdefault(key, coordinate)
                raw_sliders = params.get("sliders")
                if isinstance(raw_sliders, list):
                    sliders = []
                    for index, raw_slider in enumerate(raw_sliders):
                        if isinstance(raw_slider, str):
                            slider = {"componentName": raw_slider}
                        elif isinstance(raw_slider, dict):
                            slider = dict(raw_slider)
                            slider.setdefault(
                                "componentName",
                                slider.get("name")
                                or slider.get("component")
                                or slider.get("jawName"),
                            )
                        else:
                            sliders.append(raw_slider)
                            continue
                        slider.setdefault(
                            "phaseDeg",
                            index * 360.0
                            / max(len(raw_sliders), 1),
                        )
                        sliders.append(slider)
                    params["sliders"] = sliders
                params.setdefault("centerX", 0.0)
                params.setdefault("centerY", 0.0)
                params.setdefault("centerZ", 0.0)
            if operation_value == "assembly_set_indexed_rotary_pose":
                for target, candidates in {
                    "driverComponent": (
                        "driver",
                        "driver_component",
                        "driverName",
                        "driverComponentName",
                        "driver_component_name",
                        "drivingComponent",
                        "input",
                        "input_component",
                        "inputComponent",
                        "inputComponentName",
                    ),
                    "indexedComponent": (
                        "indexed",
                        "indexed_component",
                        "indexedName",
                        "indexedComponentName",
                        "indexed_component_name",
                        "drivenComponent",
                        "output",
                        "output_component",
                        "outputComponent",
                        "outputComponentName",
                    ),
                    "inputAngleDeg": (
                        "input_angle_deg",
                        "angleDeg",
                        "angle",
                    ),
                    "slotCount": ("slot_count", "slots"),
                    "engagementStartDeg": (
                        "engagement_start_deg",
                        "engageStartDeg",
                    ),
                    "engagementEndDeg": (
                        "engagement_end_deg",
                        "engageEndDeg",
                    ),
                    "continuousComponents": (
                        "continuous_components",
                        "continuous",
                        "continuousSynchronized",
                        "continuous_synchronized",
                    ),
                    "indexedComponents": (
                        "indexed_components",
                        "indexedSynchronized",
                        "indexed_synchronized",
                    ),
                }.items():
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break
                components = params.get("components")
                if isinstance(components, dict):
                    for target, candidates in {
                        "driverComponent": (
                            "driver",
                            "input",
                            "driving",
                        ),
                        "indexedComponent": (
                            "indexed",
                            "output",
                            "driven",
                        ),
                    }.items():
                        if target in params:
                            continue
                        for candidate in candidates:
                            value = components.get(candidate)
                            if isinstance(value, str):
                                params[target] = value
                                break
                            if isinstance(value, dict):
                                name = (
                                    value.get("componentName")
                                    or value.get("name")
                                    or value.get("component")
                                )
                                if name:
                                    params[target] = name
                                    break
                for target in ("driverComponent", "indexedComponent"):
                    value = params.get(target)
                    if isinstance(value, dict):
                        name = (
                            value.get("componentName")
                            or value.get("name")
                            or value.get("component")
                        )
                        if name:
                            params[target] = name
                for list_key in (
                    "continuousComponents",
                    "indexedComponents",
                ):
                    raw_items = params.get(list_key)
                    if not isinstance(raw_items, list):
                        continue
                    normalized_items = []
                    for raw_entry in raw_items:
                        if not isinstance(raw_entry, dict):
                            normalized_items.append(raw_entry)
                            continue
                        entry = dict(raw_entry)
                        if "componentName" not in entry:
                            for candidate in ("name", "component"):
                                if candidate in entry:
                                    entry["componentName"] = entry[candidate]
                                    break
                        position = entry.get("position")
                        if (
                            isinstance(position, (list, tuple))
                            and len(position) >= 3
                        ):
                            for key, coordinate in zip(
                                ("x", "y", "z"),
                                position[:3],
                            ):
                                entry.setdefault(key, coordinate)
                        normalized_items.append(entry)
                    params[list_key] = normalized_items
            if operation_value in {
                "assembly_set_synchronized_rotary_pose",
                "assembly_set_four_bar_pose",
                "assembly_set_cam_follower_pose",
            }:
                def set_alias(target, *candidates):
                    if target not in params:
                        for candidate in candidates:
                            if candidate in params:
                                params[target] = params[candidate]
                                break

                def set_xyz(source, targets, *, axis=False):
                    vector = source
                    if isinstance(vector, str) and axis:
                        vector = {
                            "x": (1.0, 0.0, 0.0),
                            "y": (0.0, 1.0, 0.0),
                            "z": (0.0, 0.0, 1.0),
                            "-x": (-1.0, 0.0, 0.0),
                            "-y": (0.0, -1.0, 0.0),
                            "-z": (0.0, 0.0, -1.0),
                        }.get(vector.strip().lower().lstrip("+"))
                    if isinstance(vector, dict):
                        vector = tuple(
                            vector.get(key, vector.get(key.upper()))
                            for key in ("x", "y", "z")
                        )
                    if isinstance(vector, (list, tuple)) and len(vector) >= 3:
                        for target, coordinate in zip(targets, vector[:3]):
                            if coordinate is not None:
                                params.setdefault(target, coordinate)

                def component_name(value):
                    if not isinstance(value, dict):
                        return value
                    return (
                        value.get("componentName")
                        or value.get("component_name")
                        or value.get("name")
                        or value.get("component")
                    )

                set_alias(
                    "inputAngleDeg",
                    "input_angle_deg",
                    "angleDeg",
                    "angle_deg",
                    "angle",
                )

                if operation_value == "assembly_set_synchronized_rotary_pose":
                    set_alias(
                        "rotors",
                        "rotorComponents",
                        "rotor_components",
                        "synchronizedComponents",
                        "synchronized_components",
                        "components",
                    )
                    axis = (
                        params.get("rotationAxis")
                        or params.get("rotation_axis")
                        or params.get("axis")
                    )
                    set_xyz(
                        axis,
                        ("rotationAxisX", "rotationAxisY", "rotationAxisZ"),
                        axis=True,
                    )
                    raw_rotors = params.get("rotors")
                    if isinstance(raw_rotors, list):
                        rotors = []
                        for raw_rotor in raw_rotors:
                            if isinstance(raw_rotor, str):
                                rotor = {"componentName": raw_rotor}
                            elif isinstance(raw_rotor, dict):
                                rotor = dict(raw_rotor)
                                rotor.setdefault(
                                    "componentName",
                                    component_name(rotor),
                                )
                            else:
                                rotors.append(raw_rotor)
                                continue
                            for target, candidates in {
                                "phaseDeg": ("phase_deg", "phase"),
                                "reverse": ("reversed", "isReverse"),
                                "ratio": ("speedRatio", "speed_ratio"),
                            }.items():
                                if target not in rotor:
                                    for candidate in candidates:
                                        if candidate in rotor:
                                            rotor[target] = rotor[candidate]
                                            break
                            position = (
                                rotor.get("position")
                                or rotor.get("center")
                                or rotor.get("origin")
                            )
                            if isinstance(position, dict):
                                position = [
                                    position.get("x", position.get("X")),
                                    position.get("y", position.get("Y")),
                                    position.get("z", position.get("Z")),
                                ]
                            if (
                                isinstance(position, (list, tuple))
                                and len(position) >= 3
                            ):
                                for key, coordinate in zip(
                                    ("x", "y", "z"),
                                    position[:3],
                                ):
                                    if coordinate is not None:
                                        rotor.setdefault(key, coordinate)
                            rotors.append(rotor)
                        params["rotors"] = rotors

                if operation_value == "assembly_set_four_bar_pose":
                    for target, candidates in {
                        "inputLength": ("input_length", "crankLength"),
                        "couplerLength": ("coupler_length", "linkLength"),
                        "outputLength": ("output_length", "rockerLength"),
                        "inputComponent": (
                            "input_component",
                            "input",
                            "crankComponent",
                        ),
                        "couplerComponent": (
                            "coupler_component",
                            "coupler",
                            "linkComponent",
                        ),
                        "outputComponent": (
                            "output_component",
                            "output",
                            "rockerComponent",
                        ),
                        "assemblyMode": (
                            "assembly_mode",
                            "configuration",
                            "mode",
                        ),
                        "couplerPointComponents": (
                            "coupler_point_components",
                            "pointComponents",
                        ),
                    }.items():
                        set_alias(target, *candidates)
                    set_xyz(
                        params.get("groundA")
                        or params.get("ground_a")
                        or params.get("pivotA")
                        or params.get("inputPivot"),
                        ("groundAX", "groundAY", "groundAZ"),
                    )
                    set_xyz(
                        params.get("groundB")
                        or params.get("ground_b")
                        or params.get("pivotB")
                        or params.get("outputPivot"),
                        ("groundBX", "groundBY", "groundBZ"),
                    )
                    for prefix in ("groundA", "groundB"):
                        snake = "ground_a" if prefix == "groundA" else "ground_b"
                        for coordinate in ("X", "Y", "Z"):
                            set_alias(
                                f"{prefix}{coordinate}",
                                f"{snake}_{coordinate.lower()}",
                            )
                    set_xyz(
                        params.get("rotationAxis")
                        or params.get("rotation_axis")
                        or params.get("axis"),
                        ("rotationAxisX", "rotationAxisY", "rotationAxisZ"),
                        axis=True,
                    )
                    set_xyz(
                        params.get("couplerPoint")
                        or params.get("coupler_point"),
                        ("couplerPointX", "couplerPointY", "couplerPointZ"),
                    )
                    for target in (
                        "inputComponent",
                        "couplerComponent",
                        "outputComponent",
                    ):
                        params[target] = component_name(params.get(target))
                    raw_points = params.get("couplerPointComponents")
                    if isinstance(raw_points, list):
                        params["couplerPointComponents"] = [
                            {"componentName": item}
                            if isinstance(item, str)
                            else {
                                **item,
                                "componentName": component_name(item),
                            }
                            if isinstance(item, dict)
                            else item
                            for item in raw_points
                        ]

                if operation_value == "assembly_set_cam_follower_pose":
                    for target, candidates in {
                        "driverComponent": (
                            "driver_component",
                            "driver",
                            "camComponent",
                        ),
                        "followerComponent": (
                            "follower_component",
                            "follower",
                        ),
                        "baseRadius": ("base_radius",),
                        "phaseDeg": ("phase_deg", "phase"),
                        "profile": ("profile_type", "profileType", "camProfile"),
                    }.items():
                        set_alias(target, *candidates)
                    set_xyz(
                        params.get("center")
                        or params.get("camCenter")
                        or params.get("cam_center"),
                        ("centerX", "centerY", "centerZ"),
                    )
                    for coordinate in ("X", "Y", "Z"):
                        set_alias(
                            f"center{coordinate}",
                            f"center_{coordinate.lower()}",
                        )
                    axes = params.get("axes")
                    if not isinstance(axes, dict):
                        axes = {}
                    set_xyz(
                        params.get("rotationAxis")
                        or params.get("rotation_axis")
                        or params.get("driverAxis")
                        or axes.get("rotation")
                        or axes.get("driver"),
                        ("rotationAxisX", "rotationAxisY", "rotationAxisZ"),
                        axis=True,
                    )
                    set_xyz(
                        params.get("followerAxis")
                        or params.get("follower_axis")
                        or axes.get("follower"),
                        ("followerAxisX", "followerAxisY", "followerAxisZ"),
                        axis=True,
                    )
                    for target in ("driverComponent", "followerComponent"):
                        params[target] = component_name(params.get(target))
            if operation_value == "assembly_mate_coincident":
                plane_aliases = {
                    "front": "前视基准面",
                    "front plane": "前视基准面",
                    "top": "上视基准面",
                    "top plane": "上视基准面",
                    "right": "右视基准面",
                    "right plane": "右视基准面",
                }
                for key in ("plane1", "plane2"):
                    value = str(params.get(key, "")).strip().lower()
                    if value in plane_aliases:
                        params[key] = plane_aliases[value]
        item["params"] = params
        if "description" not in item and item.get("comment"):
            item["description"] = item["comment"]
        return item

    @model_validator(mode="after")
    def _validate_operation_contract(self):
        required = {
            OperationType.SELECT_PLANE: ("index",),
            OperationType.SKETCH_LINE: (
                "x1",
                "y1",
                "x2",
                "y2",
            ),
            OperationType.SKETCH_POLYLINE: ("points", "closed"),
            OperationType.SKETCH_RECTANGLE: (
                "x1",
                "y1",
                "x2",
                "y2",
            ),
            OperationType.SKETCH_CIRCLE: ("cx", "cy", "radius"),
            OperationType.FEATURE_EXTRUSION: ("depth",),
            OperationType.FEATURE_CUT_EXTRUSION: ("depth",),
            OperationType.SELECT_OBSERVED_FACE_AND_SKETCH: (
                "faceIndex",
            ),
            OperationType.ASSEMBLY_OPEN: ("assemblyPath",),
            OperationType.ASSEMBLY_ADD_COMPONENT: (
                "partPath",
                "x",
                "y",
                "z",
            ),
            OperationType.ASSEMBLY_MATE_COINCIDENT: (
                "component1",
                "component2",
            ),
            OperationType.ASSEMBLY_MATE_CONCENTRIC: (
                "component1",
                "rx1",
                "ry1",
                "rz1",
                "dx1",
                "dy1",
                "dz1",
                "component2",
                "rx2",
                "ry2",
                "rz2",
                "dx2",
                "dy2",
                "dz2",
            ),
            OperationType.ASSEMBLY_MATE_GEAR: (
                "component1",
                "component2",
                "gearRatioNumerator",
                "gearRatioDenominator",
            ),
            OperationType.ASSEMBLY_SET_COMPONENT_POSE: (
                "componentName",
                "x",
                "y",
                "z",
                "axisX",
                "axisY",
                "axisZ",
                "angleDeg",
            ),
            OperationType.ASSEMBLY_SET_CRANK_SLIDER_POSE: (
                "crankComponent",
                "rodComponent",
                "sliderComponent",
                "crankRadius",
                "rodLength",
                "angleDeg",
            ),
            OperationType.ASSEMBLY_SET_PLANAR_CHAIN_POSE: (
                "baseX",
                "baseY",
                "baseZ",
                "segments",
            ),
            OperationType.ASSEMBLY_SET_RADIAL_DEPLOYABLE_POSE: (
                "centerX",
                "centerY",
                "centerZ",
                "hubRadius",
                "runnerRadius",
                "ribLength",
                "ribAttachmentDistance",
                "stretcherLength",
                "ribDownAngleDeg",
                "runnerComponent",
                "bays",
            ),
            OperationType.ASSEMBLY_SET_PLANETARY_TRAIN_POSE: (
                "centerX",
                "centerY",
                "centerZ",
                "inputAngleDeg",
                "stages",
            ),
            OperationType.ASSEMBLY_SET_RADIAL_SLIDERS_POSE: (
                "centerX",
                "centerY",
                "centerZ",
                "radialDistance",
                "sliders",
            ),
            OperationType.ASSEMBLY_SET_INDEXED_ROTARY_POSE: (
                "driverComponent",
                "indexedComponent",
                "inputAngleDeg",
            ),
            OperationType.ASSEMBLY_SET_SYNCHRONIZED_ROTARY_POSE: (
                "inputAngleDeg",
                "rotors",
            ),
            OperationType.ASSEMBLY_SET_FOUR_BAR_POSE: (
                "groundAX",
                "groundAY",
                "groundAZ",
                "groundBX",
                "groundBY",
                "groundBZ",
                "inputLength",
                "couplerLength",
                "outputLength",
                "inputAngleDeg",
                "inputComponent",
                "couplerComponent",
                "outputComponent",
            ),
            OperationType.ASSEMBLY_SET_CAM_FOLLOWER_POSE: (
                "driverComponent",
                "followerComponent",
                "centerX",
                "centerY",
                "centerZ",
                "baseRadius",
                "lift",
                "inputAngleDeg",
            ),
        }.get(self.operation, ())
        missing = [
            key
            for key in required
            if key not in self.params
            or self.params[key] is None
            or self.params[key] == ""
        ]
        if missing:
            raise ValueError(
                f"{self.operation.value} 缺少必要参数: "
                + ", ".join(missing)
            )
        return self


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

    @model_validator(mode="before")
    @classmethod
    def _normalize_provider_shape(cls, value):
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        intent = (
            payload.get("user_intent")
            or payload.get("intent")
            or payload.get("description")
            or payload.get("task")
            or "根据当前输入完成 SolidWorks 建模"
        )
        steps = (
            payload.get("steps")
            or payload.get("operations")
            or payload.get("actions")
            or []
        )
        if isinstance(steps, dict):
            steps = steps.get("steps") or steps.get("operations") or [steps]
        payload.setdefault("user_intent", str(intent))
        payload.setdefault("steps", steps)
        return payload


class ModelingMode(str, Enum):
    """建模执行模式。"""

    SINGLE_TURN = "single_turn"
    MULTI_TURN = "multi_turn"


class AgentDecision(str, Enum):
    """Multi-turn 每轮的下一步决策。"""

    ACT = "act"
    FINISH = "finish"
    BLOCKED = "blocked"


class MilestoneStatus(str, Enum):
    """A deterministic unit of progress in a multi-turn task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PostconditionSpec(BaseModel):
    """Machine-checkable condition for a milestone."""

    kind: str
    params: dict = Field(default_factory=dict)


class Milestone(BaseModel):
    """One dependency-ordered modeling milestone."""

    id: str
    title: str
    depends_on: list[str] = Field(default_factory=list)
    postconditions: list[PostconditionSpec] = Field(default_factory=list)
    max_actions: int = Field(default=5, ge=1, le=5)
    max_failures: int = Field(default=3, ge=1, le=10)
    status: MilestoneStatus = MilestoneStatus.PENDING
    evidence: dict = Field(default_factory=dict)
    failure_count: int = 0


class TaskPlan(BaseModel):
    """Multi-turn task decomposition; never used by single-turn."""

    user_goal: str
    milestones: list[Milestone]
    notes: str = ""
    recovery_buffer: int = Field(default=4, ge=0, le=20)


class ValidationResult(BaseModel):
    """Deterministic postcondition evaluation result."""

    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)
    message: str = ""


class ModelObservation(BaseModel):
    """一次 SolidWorks 状态快照。"""

    features: list[dict] = Field(default_factory=list)
    faces: list[dict] = Field(default_factory=list)
    sketches: list[dict] = Field(default_factory=list)
    feature_sketch_links: list[dict] = Field(default_factory=list)
    bounding_box_mm: Optional[dict] = None
    volume_mm3: Optional[float] = None
    document_type: str = "part"
    assembly_path: str = ""
    components: list[dict] = Field(default_factory=list)
    mates: list[dict] = Field(default_factory=list)
    interference_summary: Optional[dict] = None
    last_execution: list[dict] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)
    state_fingerprint: str = ""


class AgentTurn(BaseModel):
    """LLM 在 Multi-turn 中输出的一轮增量决策。"""

    decision: AgentDecision
    milestone_id: str = ""
    progress_summary: str = Field(default="", max_length=500)
    actions: list[SwOperation] = Field(default_factory=list, max_length=5)
    check_summary: str = Field(default="", max_length=500)
    next_milestone: str = Field(default="", max_length=200)

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

    @model_validator(mode="after")
    def validate_decision_has_consistent_actions(self):
        """Validate defaults too; field validators skip an omitted actions field."""
        if self.decision == AgentDecision.ACT and not self.actions:
            raise ValueError("decision=act 时 actions 不能为空")
        if self.decision != AgentDecision.ACT and self.actions:
            raise ValueError("只有 decision=act 时才允许 actions")
        return self


class MultiTurnRecord(BaseModel):
    """一次 Observe → Decide → Act → Check 的完整记录。"""

    turn: int
    observation_before: ModelObservation
    decision: AgentTurn
    execution_results: list[dict] = Field(default_factory=list)
    observation_after: Optional[ModelObservation] = None
    validation: Optional[ValidationResult] = None


class MultiTurnResult(BaseModel):
    """Multi-turn Agent 的最终结果。"""

    mode: ModelingMode = ModelingMode.MULTI_TURN
    status: str
    user_goal: str
    turns: list[MultiTurnRecord] = Field(default_factory=list)
    milestones: list[dict] = Field(default_factory=list)
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
    count: int = Field(
        default=1,
        ge=1,
        description="图纸中属于这一组的相同特征数量；单个特征为 1"
    )
    locations_mm: list[dict] = Field(
        default_factory=list,
        description="可读到位置尺寸时，逐个记录视图内中心坐标或边距（毫米）"
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_top_level_aliases(cls, value):
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        aliases = {
            "part_type": ("type", "partType", "geometry_type"),
            "base_view": ("baseView", "primary_view"),
            "overall_size_mm": (
                "overall_size",
                "overall_dimensions",
                "bounding_box_mm",
            ),
            "profiles": ("views", "view_profiles"),
            "dimensions": ("measurements", "dimension_specs"),
            "features": ("feature_hints",),
        }
        for target, candidates in aliases.items():
            if target not in payload:
                for candidate in candidates:
                    if candidate in payload:
                        payload[target] = payload[candidate]
                        break
        payload.setdefault("part_type", "other")
        return payload

    @field_validator("part_type", mode="before")
    @classmethod
    def _normalize_part_type(cls, value):
        if isinstance(value, dict):
            value = value.get("type") or value.get("name") or "other"
        text = str(value or "other").strip().lower().replace("-", "_")
        aliases = {
            "l bracket": "l_bracket",
            "l型支架": "l_bracket",
            "l形支架": "l_bracket",
            "extruded_prism": "extrude_prism",
            "plate": "plate_with_holes",
        }
        return aliases.get(text, text)

    @field_validator("base_view", mode="before")
    @classmethod
    def _normalize_base_view(cls, value):
        if isinstance(value, dict):
            value = value.get("view") or value.get("name") or value.get("base")
        return str(value or "front").strip().lower()

    @field_validator("overall_size_mm", mode="before")
    @classmethod
    def _coerce_overall_size_to_dict(cls, value):
        """Accept common Vision shorthand [length, height, depth]."""
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            return {
                "length": value[0],
                "height": value[1],
                "depth": value[2],
            }
        return value

    @field_validator("profiles", mode="before")
    @classmethod
    def _normalize_profile_aliases(cls, value):
        """Accept common provider aliases without spending retries."""
        if isinstance(value, str):
            value = [value]
        if isinstance(value, dict):
            value = [
                {"view": key, **(item if isinstance(item, dict) else {
                    "description": item,
                })}
                for key, item in value.items()
            ]
        if not isinstance(value, list):
            return value
        plane_by_view = {"front": "front", "top": "top", "right": "right"}
        view_aliases = {
            "主视图": "front",
            "前视图": "front",
            "front view": "front",
            "俯视图": "top",
            "top view": "top",
            "侧视图": "right",
            "右视图": "right",
            "right view": "right",
        }
        normalized = []
        view_order = ("front", "top", "right")
        for index, item in enumerate(value):
            if isinstance(item, str):
                view = view_order[index] if index < len(view_order) else f"view_{index}"
                item = {
                    "view_name": view,
                    "base_plane": plane_by_view.get(view, view),
                    "outline_desc": item,
                }
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = dict(item)
            view = (
                item.get("view_name")
                or item.get("view")
                or item.get("name")
                or (view_order[index] if index < len(view_order) else f"view_{index}")
            )
            view = view_aliases.get(str(view).strip().lower(), view)
            item.setdefault("view_name", view)
            item.setdefault("base_plane", plane_by_view.get(str(view), str(view)))
            description = (
                item.get("outline_desc")
                or item.get("description")
                or item.get("outline")
                or item.get("type")
                or item.get("shape")
                or "轮廓描述未提供"
            )
            item.setdefault("outline_desc", description)
            normalized.append(item)
        return normalized

    @field_validator("dimensions", mode="before")
    @classmethod
    def _normalize_dimension_aliases(cls, value):
        if not isinstance(value, list):
            return value
        normalized = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = dict(item)
            label = (
                item.get("label")
                or item.get("name")
                or item.get("description")
                or item.get("applies_to")
                or f"dimension_{index + 1}"
            )
            number = (
                item.get("value_mm")
                if item.get("value_mm") is not None
                else item.get("value")
            )
            if label is not None:
                item.setdefault("label", label)
            if number is not None:
                item.setdefault("value_mm", number)
            normalized.append(item)
        return normalized

    @field_validator("features", mode="before")
    @classmethod
    def _normalize_feature_aliases(cls, value):
        if not isinstance(value, list):
            return value
        normalized = []
        for item in value:
            if not isinstance(item, dict):
                normalized.append(item)
                continue
            item = dict(item)
            kind = (
                item.get("kind")
                or item.get("type")
                or item.get("name")
                or "other"
            )
            location = (
                item.get("location_desc")
                or item.get("location")
                or item.get("position")
            )
            size = (
                item.get("size_desc")
                or item.get("size")
                or item.get("dimensions")
            )
            item.setdefault("kind", kind)
            if location is not None:
                item.setdefault("location_desc", str(location))
            if size is not None:
                item.setdefault("size_desc", str(size))
            if "count" not in item and item.get("quantity") is not None:
                item["count"] = item["quantity"]
            if "locations_mm" not in item:
                locations = (
                    item.get("locations")
                    or item.get("centers")
                    or item.get("positions")
                )
                if isinstance(locations, list):
                    item["locations_mm"] = locations
            elif isinstance(item.get("locations_mm"), str):
                item["locations_mm"] = [{
                    "description": item["locations_mm"],
                }]
            elif isinstance(item.get("locations_mm"), list):
                item["locations_mm"] = [
                    (
                        location
                        if isinstance(location, dict)
                        else {"description": str(location)}
                    )
                    for location in item["locations_mm"]
                ]
            elif isinstance(item.get("locations_mm"), dict):
                location_map = item["locations_mm"]
                if any(isinstance(v, list) for v in location_map.values()):
                    length = max(
                        (
                            len(v)
                            for v in location_map.values()
                            if isinstance(v, list)
                        ),
                        default=0,
                    )
                    item["locations_mm"] = [
                        {
                            key: (
                                values[index]
                                if isinstance(values, list) and index < len(values)
                                else values
                            )
                            for key, values in location_map.items()
                        }
                        for index in range(length)
                    ]
                else:
                    item["locations_mm"] = [location_map]
            normalized.append(item)
        return normalized

    @field_validator("assumptions", mode="before")
    @classmethod
    def _coerce_assumptions_to_str(cls, v):
        """兼容 Claude 系列输出的 list[str]：自动 join 成单 string。"""
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)
        return v
