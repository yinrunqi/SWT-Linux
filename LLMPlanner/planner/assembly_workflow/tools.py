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
        "把零件套到轴上：同心 + 轴向定位（默认用双方前视基准面，offset 为沿轴 mm）。",
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
        "可显式给出 teeth_a/teeth_b/module；缺省则从齿顶圆与中心距反推。",
        {
            "a": {"type": "string", "description": "主动齿轮别名或组件名"},
            "b": {"type": "string", "description": "从动齿轮"},
            "teeth_a": {"type": "integer"},
            "teeth_b": {"type": "integer"},
            "module": _NUM,
            "reverse": {"type": "boolean"},
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
