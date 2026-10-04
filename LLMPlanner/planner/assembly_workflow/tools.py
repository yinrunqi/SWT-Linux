"""装配 Workflow 的工具声明（给 LLM 看的契约）。"""

from __future__ import annotations


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_REF = {
    "type": "string",
    "description": (
        "组件上的几何引用，写成 组件名:选择器。"
        "cyl(d=10) 或 cyl(d=10, near=[u,v])：直径最接近的圆柱面，near 为零件局部 XY（mm）；"
        "face(+Y, largest) / face(-Z, outermost)：平面；"
        "plane(front|top|right) 或 front：零件默认基准面；"
        "face:12 / face#12：get_assembly_state 返回的面序号。"
    ),
}
_NUM = {"type": "number"}
_ALIGN = {"type": "string", "enum": ["closest", "aligned", "anti"]}

TOOL_SPECS: list[dict] = [
    _tool(
        "get_assembly_state",
        "读取装配体真实状态：组件（含别名、固定、约束状态、原点、圆柱/平面摘要）、已有配合、最近一次运动检查。",
        {}, [],
    ),
    _tool(
        "insert_component",
        "把一个已保存的 .SLDPRT 插入当前装配体。at 为装配坐标 mm，缺省为原点；"
        "rotate_deg 绕 +Z 的初始转角，必须在加配合之前设置（用于齿轮啮合相位）。name 是之后引用该组件的别名。",
        {
            "part": {"type": "string", "description": "零件绝对路径"},
            "name": {"type": "string", "description": "别名，如 input_shaft"},
            "at": {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] mm"},
            "rotate_deg": _NUM,
        },
        ["part", "name"],
    ),
    _tool(
        "ground_component",
        "把组件固定在当前位置，通常用于机体/底板。",
        {"component": {"type": "string"}},
        ["component"],
    ),
    _tool(
        "mate",
        "在两个引用之间加配合。type: coincident / concentric / parallel / distance / angle / gear。"
        "distance 单位 mm，angle_deg 单位度。gear 需要 ratio_numerator / ratio_denominator，外啮合 reverse=true。",
        {
            "type": {"type": "string", "enum": [
                "coincident", "concentric", "parallel", "distance", "angle", "gear",
            ]},
            "a": _REF,
            "b": _REF,
            "align": _ALIGN,
            "distance": _NUM,
            "angle_deg": _NUM,
            "ratio_numerator": _NUM,
            "ratio_denominator": _NUM,
            "reverse": {"type": "boolean"},
        },
        ["type", "a", "b"],
    ),
    _tool(
        "delete_mate",
        "删除一个配合（名称来自 get_assembly_state）。",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _tool(
        "mount_on_shaft",
        "把零件套到轴上：同心 + 轴向定位（默认用双方前视基准面，offset 为零件前视面相对轴前视面沿其法向的 mm，可为负）。",
        {
            "part": {"type": "string"},
            "shaft": {"type": "string"},
            "diameter": _NUM,
            "offset": _NUM,
            "lock_rotation": {"type": "boolean", "description": "true 时再加 1:1 同向齿轮配合，相当于键连接"},
        },
        ["part", "shaft"],
    ),
    _tool(
        "revolute_joint",
        "铰链：两圆柱同心，再让两个端面/基准面重合，只剩绕该轴的转动。",
        {
            "a": _REF,
            "b": _REF,
            "face_a": _REF,
            "face_b": _REF,
        },
        ["a", "b"],
    ),
    _tool(
        "slider_joint",
        "滑动副：两圆柱同心（或两个平面重合）并再约束一个平面，使零件只能沿一条轴线移动。",
        {
            "a": _REF,
            "b": _REF,
            "guide_a": _REF,
            "guide_b": _REF,
        },
        ["a", "b", "guide_a", "guide_b"],
    ),
    _tool(
        "mesh_gears",
        "外啮合一对直齿轮：从几何估计模数与齿数，检查中心距，必要时旋转从动轮到啮合相位，再建立反向齿轮配合。"
        "可显式给出 teeth_a/teeth_b/module；缺省则从齿顶圆与中心距反推。"
        "internal=true 为内啮合：b 是内齿圈，必须给 teeth_a/teeth_b/module，中心距按 m(z_b−z_a)/2 检查，"
        "旋转的是外齿轮 a（齿圈常为固定件），默认同向（reverse=false）。",
        {
            "a": {"type": "string", "description": "主动齿轮别名或组件名；内啮合时为外齿轮"},
            "b": {"type": "string", "description": "从动齿轮；内啮合时为内齿圈"},
            "teeth_a": {"type": "integer"},
            "teeth_b": {"type": "integer"},
            "module": _NUM,
            "reverse": {"type": "boolean"},
            "internal": {"type": "boolean"},
        },
        ["a", "b"],
    ),
    _tool(
        "crank_slider",
        "一次建好曲柄滑块三处连接：曲柄孔-机架轴承（转动）、连杆大头-曲柄销、连杆小头-滑块/活塞销，"
        "并给滑块加导向（默认滑块与机架的右视基准面重合）。",
        {
            "frame": {"type": "string"},
            "crank": {"type": "string"},
            "rod": {"type": "string"},
            "slider": {"type": "string"},
            "crank_bore": _NUM,
            "frame_bore": _NUM,
            "crank_pin": _NUM,
            "rod_big": _NUM,
            "rod_small": _NUM,
            "slider_pin": _NUM,
        },
        ["frame", "crank", "rod", "slider"],
    ),
    _tool(
        "check_interference",
        "当前姿态下做 SolidWorks 原生干涉检测。",
        {}, [],
    ),
    _tool(
        "check_motion",
        "在主动件上加旋转马达，用 Motion Study 采样一个周期：逐帧干涉、实测转角/位移、本该动却没动的组件。"
        "finish 前必须有一次通过的 check_motion。",
        {
            "driver": {"type": "string", "description": "主动件别名"},
            "driver_ref": _REF,
            "sweep_deg": _NUM,
            "frames": {"type": "integer"},
            "rpm": _NUM,
            "expect_moving": {"type": "array", "items": {"type": "string"}},
            "expect_fixed": {"type": "array", "items": {"type": "string"}},
        },
        ["driver"],
    ),
    _tool(
        "save_assembly",
        "保存当前装配体。",
        {"path": {"type": "string"}},
        [],
    ),
    _tool(
        "finish",
        "装配完成。要求已经保存，并且最近一次 check_motion 通过。",
        {"summary": {"type": "string"}},
        ["summary"],
    ),
]

# ------------------------------------------------------------------ v2：数据挖掘补齐的配合与宏工具

_REF_V2 = {
    "type": "string",
    "description": _REF["description"] + (
        "另外：edge(line, +X, longest) / edge(circle, d=10)：零件局部坐标下的直线/圆边，edge:7 为边序号；"
        "sphere 或 sphere(d=16)：球面；component：整个组件（lock 用）。"
    ),
}
_MATE_TYPES_V2 = [
    "coincident", "concentric", "parallel", "distance", "angle", "gear",
    "slot", "rack_pinion", "screw", "linear_coupler", "lock",
]
_JOINT_TYPES = ["fixed", "revolute", "cylindrical", "slider", "planar", "ball", "pin_slot"]


def _v2_spec(spec: dict) -> dict:
    name = spec["function"]["name"]
    if name == "mate":
        return _tool(
            "mate",
            "在两个引用之间加一条 SolidWorks 配合（底层原语，优先用 joint 等宏工具）。"
            "coincident / concentric / parallel / distance / angle / gear 同前；"
            "concentric 加 lock_rotation=true 表示键连接（不能相对转动）；"
            "distance 给 distance_min / distance_max 即为限位距离（行程限位）；"
            "slot：a=槽口面，b=销的圆柱面；rack_pinion：a=齿条运动方向的直线边，b=小齿轮圆柱面，给 pitch_diameter（mm）"
            "或 travel_per_rev（mm/转）；screw：a=螺杆圆柱，b=螺母孔，给 lead（mm/转）；"
            "linear_coupler 请用 linear_coupler 工具；lock：a/b 只写组件名，两者整体锁死。",
            {
                "type": {"type": "string", "enum": _MATE_TYPES_V2},
                "a": _REF_V2,
                "b": _REF_V2,
                "align": _ALIGN,
                "distance": _NUM,
                "distance_min": _NUM,
                "distance_max": _NUM,
                "angle_deg": _NUM,
                "ratio_numerator": _NUM,
                "ratio_denominator": _NUM,
                "reverse": {"type": "boolean"},
                "lock_rotation": {"type": "boolean"},
                "pitch_diameter": _NUM,
                "travel_per_rev": _NUM,
                "lead": _NUM,
                "slot_constraint": {"type": "string", "enum": ["free", "center", "distance", "percent"]},
            },
            ["type", "a", "b"],
        )
    if name == "mount_on_shaft":
        return _tool(
            "mount_on_shaft",
            "把零件套到轴上：同心 + 轴向定位（默认用双方前视基准面，offset 为零件前视面相对轴前视面沿其法向的 mm，可为负）。"
            "lock_rotation=true 时同心配合锁定旋转，相当于键连接，零件与轴成为一个刚体。",
            spec["function"]["parameters"]["properties"],
            ["part", "shaft"],
        )
    params = spec["function"]["parameters"]
    properties = {key: (_REF_V2 if value is _REF else value) for key, value in params["properties"].items()}
    return _tool(name, spec["function"]["description"], properties, params["required"])


TOOL_SPECS_V2: list[dict] = [_v2_spec(spec) for spec in TOOL_SPECS] + [
    _tool(
        "joint",
        "按机构关节建一组 SolidWorks 配合（先想关节，再落到配合）。type："
        "fixed（a/b 为圆柱引用时=同心锁转+端面贴合；只给组件名时=lock 整体锁死）；"
        "revolute（同心 + face_a/face_b 贴合，或给 offset：face_a 相对 face_b 沿其法向的 mm，可为负）；cylindrical（只同心，可转可沿轴滑）；"
        "slider（a/b 两圆柱同心或两平面贴合，再用 guide_a/guide_b 止转；给 limit_min/limit_max 与 travel_a/travel_b 加行程限位）；"
        "planar（两平面贴合）；ball（两球面同心）；pin_slot（a=槽口面，b=销圆柱，再 face_a/face_b 贴合）。",
        {
            "type": {"type": "string", "enum": _JOINT_TYPES},
            "a": _REF_V2,
            "b": _REF_V2,
            "face_a": _REF_V2,
            "face_b": _REF_V2,
            "offset": _NUM,
            "guide_a": _REF_V2,
            "guide_b": _REF_V2,
            "travel_a": _REF_V2,
            "travel_b": _REF_V2,
            "limit_min": _NUM,
            "limit_max": _NUM,
            "slot_constraint": {"type": "string", "enum": ["free", "center"]},
        },
        ["type", "a", "b"],
    ),
    _tool(
        "close_loop",
        "闭环机构的最后一个关节（两端都已经连到固定件）。默认 minimal=true 只加必要约束"
        "（revolute 只同心、pin_slot 只加槽口，slider 圆柱导向时不加止转面），避免平面闭环的冗余约束；"
        "若出现过定义会撤销并改用完整关节再试，返回每次尝试的结果。参数同 joint。",
        {
            "type": {"type": "string", "enum": _JOINT_TYPES},
            "a": _REF_V2,
            "b": _REF_V2,
            "face_a": _REF_V2,
            "face_b": _REF_V2,
            "offset": _NUM,
            "guide_a": _REF_V2,
            "guide_b": _REF_V2,
            "minimal": {"type": "boolean"},
        },
        ["type", "a", "b"],
    ),
    _tool(
        "rack_pinion",
        "齿条-小齿轮耦合：齿条已有滑动关节、小齿轮已有转动关节后再加。"
        "pitch_diameter（mm）或 module + teeth；默认齿条取最长直线边、小齿轮取第一个圆柱面（轴孔）。",
        {
            "rack": {"type": "string"},
            "pinion": {"type": "string"},
            "pitch_diameter": _NUM,
            "module": _NUM,
            "teeth": {"type": "integer"},
            "rack_edge": _REF_V2,
            "pinion_ref": _REF_V2,
            "reverse": {"type": "boolean"},
        },
        ["rack", "pinion"],
    ),
    _tool(
        "screw_pair",
        "螺旋副：螺杆与螺母同轴，并按导程把转动与轴向移动耦合（本身已含同心，不要再另加 concentric）。"
        "缺省自动找两者直径相同的圆柱面。",
        {
            "screw": {"type": "string"},
            "nut": {"type": "string"},
            "lead": {"type": "number", "description": "导程 mm/转"},
            "screw_ref": _REF_V2,
            "nut_ref": _REF_V2,
            "reverse": {"type": "boolean"},
        },
        ["screw", "nut", "lead"],
    ),
    _tool(
        "linear_coupler",
        "两个滑动件的位移按比例联动：ratio = b 的位移 / a 的位移。两者都要已有滑动关节。"
        "默认各取最长直线边作方向，参考件默认固定件。",
        {
            "a": {"type": "string"},
            "b": {"type": "string"},
            "ratio": _NUM,
            "reverse": {"type": "boolean"},
            "edge_a": _REF_V2,
            "edge_b": _REF_V2,
            "reference": {"type": "string"},
        },
        ["a", "b"],
    ),
    _tool(
        "check_dof",
        "自由度预算（Grübler）：按已建关节与耦合估算机构活动度，列出悬空、未接地、过定义的组件。"
        "expected_mobility 给出设计活动度（如单输入机构为 1）时会比对。",
        {"expected_mobility": {"type": "integer"}},
        [],
    ),
]


# ------------------------------------------------------------------ v3：增量状态、秩法自由度、按需轴向约束、位姿自检

_POSE_SET = {
    "type": "array",
    "description": "按运动链顺序的临时配合：{type: angle, a, b, angle_deg} 两个平面 / 基准面夹角；"
                   "{type: offset, a, b, offset_mm} a 面落在 b 面法向一侧的有符号距离；{type: distance, a, b, distance_mm}。",
    "items": {"type": "object"},
}
_POSE_POINTS = {
    "type": "array",
    "description": "要核对的关键点：{role, local: [x,y,z] 该零件局部 mm, expect: {x|y|z|r_xy: mm}}，"
                   "坐标在固定件（frame 参数，默认第一个固定件）的局部坐标系里取。",
    "items": {"type": "object"},
}

_V3_OVERRIDES = {
    "get_assembly_state": (
        "读取装配体当前状态：每个组件的别名、固定、约束状态、原点、绕 Z 转角、剩余自由度 free；全部配合；"
        "按真实配合求秩得到的活动度 mobility 与冗余约束数 redundant。面列表默认不返回"
        "（insert_component 时已给过），需要时 faces=true 或 components=[别名…]。",
        {"faces": {"type": "boolean"}, "components": {"type": "array", "items": {"type": "string"}}},
        [],
    ),
    "check_dof": (
        "自由度诊断：按 SolidWorks 里的全部真实配合（含直接 mate 与耦合）求约束秩，返回活动度 mobility、"
        "冗余约束数 redundant、每个零件相对机架剩余的自由度 free、悬空件、过定义件。"
        "expected_mobility 给出设计活动度时比对（matches_expected）。",
        {"expected_mobility": {"type": "integer"}},
        [],
    ),
    "finish": (
        "装配完成。要求已经保存，并且：最近一次 check_motion 通过（整周回转机构）；"
        "或最近一次 check_pose 通过且 check_dof(expected_mobility) 吻合（有限行程 / 多自由度机构）。"
        "之后又改了结构的话需要重新检查。",
        {"summary": {"type": "string"}},
        ["summary"],
    ),
}

_V3_DESCRIPTION_SUFFIX = {
    "insert_component": "返回该零件的圆柱面 / 平面 / 球面摘要（零件局部坐标），可直接用 face:序号 引用。",
    "joint": "转动副、销槽、圆柱导向滑块会先加主约束（同心 / 槽口），再看轴向位置（或止转）是否已被其他关节定住："
             "闭环里已定住就不再加，避免冗余过定义，返回 note；否则按 face_a/face_b、offset 或 at_z 加上。"
             "at_z：a 的零件原点（前视基准面）装好后的装配 Z 坐标（mm），按 b 当前位姿换算成 offset。",
    "close_loop": "v3 中 joint 已经自动处理闭环，close_loop 与 joint 等价。",
    "check_motion": "返回 interference_pairs：按零件对汇总首次干涉帧、帧数与最大体积。",
}


def _v3_spec(spec: dict) -> dict:
    name = spec["function"]["name"]
    if name in _V3_OVERRIDES:
        description, properties, required = _V3_OVERRIDES[name]
        return _tool(name, description, properties, required)
    params = spec["function"]["parameters"]
    properties = dict(params["properties"])
    if name in ("joint", "close_loop"):
        properties["at_z"] = _NUM
    description = spec["function"]["description"]
    if name in _V3_DESCRIPTION_SUFFIX:
        description += _V3_DESCRIPTION_SUFFIX[name]
    return _tool(name, description, properties, params["required"])


TOOL_SPECS_V3: list[dict] = [_v3_spec(spec) for spec in TOOL_SPECS_V2] + [
    _tool(
        "check_pose",
        "位姿自检（有限行程、多自由度或直线输入的机构用它代替 check_motion）：按 set 加临时配合把机构摆到一个位形，"
        "核对 points 关键点、做静态干涉与固定件漂移检查，最后自动删掉临时配合。"
        "可一次给多个位姿 poses=[{name, set, points}]。angle 的转向有两种解，工具会按 points 自动选。",
        {
            "name": {"type": "string"},
            "set": _POSE_SET,
            "points": _POSE_POINTS,
            "poses": {"type": "array", "items": {"type": "object"}},
            "expect_fixed": {"type": "array", "items": {"type": "string"}},
            "frame": {"type": "string"},
            "tolerance_mm": _NUM,
        },
        [],
    ),
]


def tool_specs(workflow: str) -> list[dict]:
    if workflow == "v3":
        return TOOL_SPECS_V3
    return TOOL_SPECS_V2 if workflow == "v2" else TOOL_SPECS


def tool_names(workflow: str) -> set[str]:
    return {spec["function"]["name"] for spec in tool_specs(workflow)}
