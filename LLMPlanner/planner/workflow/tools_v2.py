"""L1_v2 工具层：在 L1（tools.WorkflowSession）之上修补失败轨迹里反复出现的问题。

L1 的工具名、参数和行为保持不变；这里只在子类里覆盖：
    参数容错（字符串 JSON、多余引号）、"Z=40" 平面写法与基准面复用、
    特征的世界坐标范围 / 去除比例 / 新增圆柱面反馈、单向贯穿与多实体检查、
    分组压缩且可过滤的 get_model_state、delete_feature 连带删除报告、
    hull 轮廓、极坐标布孔、半圆弧方向检查、圆心微差自动校正、360° 扇环转圆环、
    多条射线倒圆角与射线歧义提示、阵列体积核对、
    sweep 的空间折线/螺旋线路径与圆形截面、loft 的点截面/引导线/首尾相切。
"""

from __future__ import annotations

import copy
import difflib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from . import param_curves
from .errors import WorkflowError
from .frames import UV_AXES, SketchFrame, face_frame
from .params import FUNCTIONS as PARAM_FUNCTIONS
from .profiles import PROFILE_ROUTE, draw_open_path, draw_operations
from .refs import _vec, resolve_ref
from .standard_sizes import resolve_size
from .tools import (
    AXES,
    EXTRA_TOOL_SPECS,
    IMAGE_KEY,
    TOOL_SPECS,
    VOLUME_EPS_MM3,
    WorkflowSession,
    _axis_sign,
    _round,
    _tool,
)

_AXIS_PLANE = re.compile(r"^\s*([XYZxyz])\s*=\s*([+-]?\d+(?:\.\d+)?)\s*(?:mm)?\s*$")
_AXIS_BASE_PLANE = {"Z": "front_plane", "Y": "top_plane", "X": "right_plane"}
_PLANE_KEYS = ("plane", "path_plane", "profile_plane", "reference", "up_to", "neutral")
_BOOL_KEYS = {"through_all", "cut", "body", "both_sides", "outward", "flip", "draft_outward"}
_HOLE_TOOLS = {"hole", "counterbore", "countersink", "tapped_hole"}
# (参数名, 是否公称直径)：这些孔径可以写钻头/螺纹代号，由 standard_sizes 查表
_SIZE_KEYS = {
    "hole": (("diameter", False),),
    "tapped_hole": (("diameter", False), ("thread_diameter", True)),
    "counterbore": (("through_diameter", False),),
    "countersink": (("through_diameter", False),),
}
_STATE_PLANAR_LIMIT = 60
_STATE_CYLINDER_LIMIT = 40
_NEW_CYLINDER_LIMIT = 12
OBSERVATION_MAX_FACES = 1000
LOW_REMOVAL_RATIO = 0.5
LOW_ADDITION_RATIO = 0.3
PATTERN_TOLERANCE = 0.15
FILLET_FACTOR = 1 - math.pi / 4


# ------------------------------------------------------------ 参数预处理


def _unquote(text: str) -> str:
    text = text.strip()
    while len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


def coerce_args(args: dict) -> dict:
    """模型常把数组/对象写成 JSON 字符串、给字符串多套一层引号、把布尔写成 "true"。"""
    out = {}
    for key, value in args.items():
        if isinstance(value, str):
            text = _unquote(value)
            if text[:1] in "[{":
                try:
                    value = json.loads(text)
                except ValueError:
                    value = text
            elif key in _BOOL_KEYS and text.lower() in ("true", "false"):
                value = text.lower() == "true"
            else:
                value = text
        out[key] = value
    return out


def _pair(value: Any, what: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise WorkflowError("TOOL_ARGS", f"{what} 必须是 [u, v]")
    return float(value[0]), float(value[1])


def _circle_radius(circle: dict, what: str) -> float:
    if "diameter" in circle:
        radius = float(circle["diameter"]) / 2
    elif "radius" in circle:
        radius = float(circle["radius"])
    else:
        raise WorkflowError("TOOL_ARGS", f"{what} 需要 diameter 或 radius")
    if radius <= 0:
        raise WorkflowError("TOOL_ARGS", f"{what} 的半径必须为正")
    return radius


def hull_loop(loop: dict, index: int = 0) -> dict:
    """两个圆的外公切线外形（腰形连杆、两端不等径的耳片）→ path 轮廓。"""
    circles = loop.get("circles")
    if not isinstance(circles, list) or len(circles) != 2:
        raise WorkflowError(
            "TOOL_ARGS", f"轮廓 {index} 的 hull 需要恰好两个圆 circles:[{{center, diameter}}, {{center, diameter}}]",
            hint="三个及以上的圆请两两做 hull，分成先后几个 extrude（实体会合并）。",
        )
    (u1, v1), (u2, v2) = (_pair(c.get("center"), f"轮廓 {index} 的 circles[i].center") for c in circles)
    r1, r2 = (_circle_radius(c, f"轮廓 {index} 的 circles[i]") for c in circles)
    distance = math.hypot(u2 - u1, v2 - v1)
    if distance <= abs(r1 - r2) + 1e-6:
        raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index}: 一个圆包含另一个圆，hull 就是大圆本身")
    theta = math.atan2(v2 - v1, u2 - u1)
    phi = math.acos((r1 - r2) / distance)

    def at(cu: float, cv: float, r: float, angle: float) -> list[float]:
        return [cu + r * math.cos(angle), cv + r * math.sin(angle)]

    a1, b1 = at(u1, v1, r1, theta - phi), at(u1, v1, r1, theta + phi)
    a2, b2 = at(u2, v2, r2, theta - phi), at(u2, v2, r2, theta + phi)
    return {
        "type": "path",
        "start": a1,
        "segments": [
            {"to": a2},
            {"to": b2, "center": [u2, v2], "ccw": True},
            {"to": b1},
            {"to": a1, "center": [u1, v1], "ccw": True},
        ],
    }


def _check_half_arcs(loop: dict, index: int) -> None:
    """只给 center 的 180° 圆弧有两种走法，L1 默认顺时针，模型几乎都以为是另一侧。"""
    try:
        cursor = _pair(loop.get("start"), "start")
    except WorkflowError:
        return
    for step_index, step in enumerate(loop.get("segments") or []):
        if not isinstance(step, dict) or "to" not in step:
            return
        try:
            target = _pair(step["to"], "to")
        except (WorkflowError, TypeError, ValueError):
            return
        if "center" in step and "ccw" not in step:
            try:
                cu, cv = _pair(step["center"], "center")
            except (WorkflowError, TypeError, ValueError):
                return
            a = (cursor[0] - cu, cursor[1] - cv)
            b = (target[0] - cu, target[1] - cv)
            norm = math.hypot(*a) * math.hypot(*b)
            if norm > 0 and abs(a[0] * b[1] - a[1] * b[0]) / norm < 1e-3 and a[0] * b[0] + a[1] * b[1] < 0:
                raise WorkflowError(
                    "TOOL_ARGS",
                    f"轮廓 {index} 第 {step_index} 段是 180° 圆弧，只给 center 无法确定走哪一侧",
                    loop=index,
                    hint="加上 ccw:true/false（(u,v) 中从当前点绕到 to 的方向），或改用 through:[u,v] 给出弧上一点。",
                )
        cursor = target


ARC_SNAP_TOLERANCE = 0.2


def _snap_arc_centers(loop: dict, index: int, notes: list[str]) -> dict:
    """圆心给出的圆弧，起终点半径差在 ARC_SNAP_TOLERANCE 以内时把圆心挪到中垂线上（离原圆心最近的点）。"""
    try:
        cursor = _pair(loop.get("start"), "start")
    except (WorkflowError, TypeError, ValueError):
        return loop
    segments = []
    for step_index, step in enumerate(loop.get("segments") or []):
        try:
            target = _pair(step["to"], "to")
        except (WorkflowError, KeyError, TypeError, ValueError):
            return loop
        if "center" in step:
            try:
                cu, cv = _pair(step["center"], "center")
            except (WorkflowError, TypeError, ValueError):
                return loop
            r0 = math.hypot(cursor[0] - cu, cursor[1] - cv)
            r1 = math.hypot(target[0] - cu, target[1] - cv)
            chord = (target[0] - cursor[0], target[1] - cursor[1])
            length = math.hypot(*chord)
            if 1e-3 < abs(r0 - r1) <= ARC_SNAP_TOLERANCE and length > 1e-6:
                mid = ((cursor[0] + target[0]) / 2, (cursor[1] + target[1]) / 2)
                normal = (-chord[1] / length, chord[0] / length)
                shift = (cu - mid[0]) * normal[0] + (cv - mid[1]) * normal[1]
                snapped = [round(mid[0] + shift * normal[0], 6), round(mid[1] + shift * normal[1], 6)]
                notes.append(
                    f"轮廓 {index} 第 {step_index} 段：起终点到圆心距离差 {abs(r0 - r1):.3f}mm，"
                    f"圆心由 [{cu:g}, {cv:g}] 调整为 {snapped}"
                )
                step = {**step, "center": snapped}
        segments.append(step)
        cursor = target
    return {**loop, "segments": segments}


def _full_sector(loop: dict, index: int, notes: list[str]) -> list[dict] | None:
    """张角为 360° 的 annular_sector 就是圆或圆环。"""
    try:
        start, end = float(loop["start_deg"]), float(loop["end_deg"])
        r_inner, r_outer = float(loop.get("r_inner") or 0), float(loop["r_outer"])
    except (KeyError, TypeError, ValueError):
        return None
    if start == end or abs((end - start) % 360) > 1e-6 and abs((end - start) % 360 - 360) > 1e-6:
        return None
    center = loop.get("center", [0, 0])
    loops = [{"type": "circle", "center": center, "radius": r_outer}]
    if r_inner > 0:
        loops.append({"type": "circle", "center": center, "radius": r_inner})
    notes.append(f"轮廓 {index} 的张角是 360°，按{'圆环' if r_inner > 0 else '整圆'}处理")
    return loops


def prepare_loops(loops: Any, notes: list[str] | None = None) -> Any:
    if not isinstance(loops, list):
        return loops
    notes = notes if notes is not None else []
    prepared = []
    for index, loop in enumerate(loops):
        kind = str(loop.get("type", "")).lower() if isinstance(loop, dict) else ""
        if kind == "hull":
            circles = loop.get("circles") if isinstance(loop, dict) else None
            if isinstance(circles, list) and len(circles) == 2:
                try:
                    r1 = _circle_radius(circles[0], f"轮廓 {index} 的 circles[0]")
                    r2 = _circle_radius(circles[1], f"轮廓 {index} 的 circles[1]")
                    if abs(r1 - r2) > 1e-3:
                        notes.append(
                            f"轮廓 {index}: hull 两圆直径不等（{2 * r1:g} 与 {2 * r2:g}），"
                            "外公切线不平行、没有等宽直边；若题面要等宽手柄/板条，请改用 rect 或等径圆的 hull"
                        )
                except WorkflowError:
                    pass
            loop = hull_loop(loop, index)
        if kind == "annular_sector" and (full := _full_sector(loop, index, notes)):
            prepared.extend(full)
            continue
        if kind == "path":
            loop = _snap_arc_centers(loop, index, notes)
            _check_half_arcs(loop, index)
        prepared.append(loop)
    return prepared


def polar_centers(polar: Any) -> list[list[float]]:
    """{center:[u,v], radius 或 pcd, count, start_deg?, span_deg?} → 圆心列表。

    角度在草图 (u,v) 内量：0° 为 +u，正角度从 +u 转向 +v；span_deg=360（默认）时均布不重复首孔。
    """
    if not isinstance(polar, dict):
        raise WorkflowError("TOOL_ARGS", "polar 必须是 {center, radius|pcd, count, start_deg?, span_deg?}")
    cu, cv = _pair(polar.get("center", [0, 0]), "polar.center")
    if "pcd" in polar:
        radius = float(polar["pcd"]) / 2
    elif "radius" in polar:
        radius = float(polar["radius"])
    else:
        raise WorkflowError("TOOL_ARGS", "polar 需要 radius（分布圆半径）或 pcd（分布圆直径）")
    count = int(polar.get("count") or 0)
    if count < 1 or radius <= 0:
        raise WorkflowError("TOOL_ARGS", "polar 需要 count ≥ 1 且 radius > 0")
    start = float(polar.get("start_deg", 0) or 0)
    span = float(polar.get("span_deg", 360) or 360)
    step = span / count if abs(span - 360) < 1e-9 else (span / (count - 1) if count > 1 else 0.0)
    centers = []
    for i in range(count):
        angle = math.radians(start + i * step)
        centers.append([round(cu + radius * math.cos(angle), 6), round(cv + radius * math.sin(angle), 6)])
    return centers


# ------------------------------------------------------------ 工具说明


_PLANE_DESC_V2 = (
    "草图平面：基准面 front_plane(XY, 法向+Z) / top_plane(XZ, 法向+Y) / right_plane(YZ, 法向+X)；"
    "\"Z=40\" / \"Y=-12.5\" / \"X=0\" 这种写法表示垂直于该轴、位于该坐标处的平面（法向为 +轴，自动创建或复用基准面，"
    "最稳定，推荐）；ref_plane 返回的基准面名称；"
    "或实体平面引用：face(+Z, outermost|innermost|largest|smallest)、face#<index>（来自最新的 get_model_state，"
    "每个特征之后都可能变化）、top/bottom/front/back/left/right（固定指 ±Y/±Z/±X 一侧最外的平面，与需求里哪个轴向上无关）。"
    "(u,v) 规则：法向沿 Z → u=X,v=Y；法向沿 Y → u=X,v=Z；法向沿 X → u=Z,v=Y。"
)
_LOOP_DESC_V2_EXTRA = (
    "hull: {circles:[{center:[u,v], diameter}, {center:[u,v], diameter}]}（两个圆加两条外公切线围成的外形，"
    "如连杆、腰形耳片；圆孔另外用 circle 轮廓）。两圆直径不等时外公切线不平行、没有等宽直边；"
    "等宽手柄/板条请用 rect 或等径圆的 hull。"
    "slot 的 start/end 是中心线端点（两端半圆圆心），净开口再 ±width/2；径向槽内缘要 ≥R 时，"
    "start 到原点距离 ≥ R+width/2。轮辐不要用穿过轴线的宽 slot。"
    "path 中只给 center 的 180° 圆弧必须写 ccw，半圆最好用 through。"
    "精确曲线（渐开线、阿基米德/渐开线涡旋、凸轮廓线、翼型、摆线等）不要手写点列，写成参数曲线由系统采样成样条："
    "path 的一段可以是 {curve:{u:\"t 的表达式\", v:\"t 的表达式\", t:[t0, t1]}}（曲线起点与上一点不重合时自动补直线；"
    "path 的第一段是 curve 时 start 可省略，取曲线起点），与直线/圆弧段混用拼成闭合轮廓；"
    "整条闭合曲线可直接写轮廓 {type:\"curve\", u, v, t:[t0, t1]}（首尾重合则闭合，否则自动补直线）。"
    "表达式可引用 set_parameters 定义的参数，函数有 sin/cos/tan/asin/acos/atan/atan2（弧度）、sind/cosd/tand（度）、"
    "sqrt/exp/log/hypot/abs/min/max/clamp(x,lo,hi)/step(x)（x≥0 为 1，用于分段函数），常量 pi。"
    "一条 curve 内部必须光滑；有尖角处（如翼型尖后缘、凸轮升程段之间若不光滑）拆成两段 curve。"
    "等距（法向）偏移曲线加 offset（mm，可写表达式）：闭合 curve 轮廓 offset>0 向外、<0 向内；"
    "path 里的 curve 段 offset>0 偏向前进方向左侧。滚子从动件凸轮的实际廓线 = 理论（节）曲线 offset −滚子半径，"
    "不要自己推导法向偏移公式（容易把角度导数和弧度导数混用）。"
)
_POLAR_SCHEMA = {
    "type": "object",
    "description": (
        "极坐标布孔（可代替或补充 centers）：{center:[u,v], radius 或 pcd(分布圆直径), count, start_deg?, span_deg?}。"
        "角度在草图 (u,v) 内量：0° 为 +u，正角度从 +u 转向 +v（法向 Z 的平面上即从 +X 转向 +Y）；"
        "span_deg 缺省 360 为均布。结果里会回显实际圆心。"
    ),
}
_TIP_ANGLE = {
    "type": "number",
    "description": "盲孔孔底钻尖锥角（全角，度），麻花钻 118；depth 不含钻尖。省略为平底",
}
_EXTENT_NOTE = (
    "结果中 extent_mm 是本特征在模型坐标中的范围（X/Y/Z，mm），removal_ratio / fill_ratio 是实际体积变化"
    "与 轮廓面积×深度 之比，new_cylinders 列出新出现的圆柱面（孔/凸台）。"
)


_SIZE_NOTE = (
    "图纸给的是英制钻头代号时直接把代号写进孔径（hole/tapped_hole 的 diameter、沉头孔的 through_diameter）："
    "号码钻 \"#21\"、字母钻 \"H\"、分数英寸 \"17/64\"，系统按 ANSI 钻头表换算成 mm 并在 notes 里回显，不要凭记忆换算。"
)


def _patch_spec(spec: dict, description: str | None = None, properties: dict | None = None,
                required: list[str] | None = None) -> dict:
    spec = copy.deepcopy(spec)
    function = spec["function"]
    if description is not None:
        function["description"] = description
    props = function["parameters"]["properties"]
    for key, value in (properties or {}).items():
        if value is None:
            props.pop(key, None)
        else:
            props[key] = value
    if required is not None:
        function["parameters"]["required"] = required
    for value in props.values():
        if isinstance(value, dict) and value.get("description", "").startswith("草图平面："):
            value["description"] = _PLANE_DESC_V2
        items = value.get("items") if isinstance(value, dict) else None
        if isinstance(items, dict) and items.get("type") == "object" and "enum" in (items.get("properties") or {}).get("type", {}):
            items = copy.deepcopy(items)
            items["description"] = items["description"] + _LOOP_DESC_V2_EXTRA
            items["properties"]["type"]["enum"] = [*items["properties"]["type"]["enum"], "hull", "curve"]
            value["items"] = items
    return spec


def _build_specs_v2() -> list[dict]:
    by_name = {spec["function"]["name"]: spec for spec in TOOL_SPECS}
    edges_v2 = {
        "type": ["string", "object", "array"],
        "description": (
            "\"all\"（全部边）、长方体语义边名（top_front 等）、射线 {origin:[x,y,z], direction:[dx,dy,dz]}（mm，射线命中的面上"
            "离交点 2mm 内最近的边；射线在命中前已在 2mm 内掠过某条边时取那条边），或射线数组（每条射线一条边，全部放进同一个圆角/倒角特征，任一射线未命中则整体失败）。"
            "射线要从实体外垂直射向与目标棱相邻的一个面，命中点离目标棱约 1mm、离其它棱更远；不要沿着棱的方向射，也不要射在面的角上。"
            "结果中 estimated_edge_length_mm 按 90° 棱从体积变化反推被倒圆的总棱长，与预期差很多说明选错了边。"
        ),
    }
    patches = {
        "get_model_state": dict(
            description=(
                "读取当前零件的真实状态：用户特征、包围盒、体积、实体数、平面（face#index、法向、位置、(u,v) 范围）、"
                "圆柱面（按 直径+轴向 分组，列出每个轴线位置）、基准面、参数。"
                "可选过滤：type=plane|cylinder、axis=X|Y|Z、diameter（±0.05mm）、near=[x,y,z]+radius（mm）。"
                "face# 编号在每次特征之后都可能变化，引用前以最新一次结果为准。"
            ),
            properties={
                "type": {"type": "string", "enum": ["plane", "cylinder"]},
                "axis": {"type": "string", "enum": list(AXES)},
                "diameter": {"type": "number"},
                "near": {"type": "array", "items": {"type": "number"}, "description": "[x,y,z] mm"},
                "radius": {"type": "number", "description": "near 的搜索半径 mm，默认 10"},
            },
        ),
        "extrude": dict(
            description=(
                "在平面上画轮廓并拉伸凸台（与已有实体合并）。同一次调用中的外环与其内部的内环构成带孔区域；互相重叠的轮廓会被预检拒绝。"
                "新凸台必须伸入已有实体或与其有面接触；只在一条线/一个点上相切会生成独立实体并被拒绝。"
                "direction=both 时 depth 是总厚度（两侧各一半）。" + _EXTENT_NOTE
            ),
            properties={"direction": {
                "type": "string",
                "description": "拉伸方向：+X/-X/+Y/-Y/+Z/-Z（须沿平面法向轴）或 both（以草图平面为中面，depth 为总厚度）。"
                               "默认：基准面和 \"Z=40\" 平面沿 +法向，实体面沿外法向。",
            }},
        ),
        "cut": dict(
            description=(
                "在平面上画轮廓并拉伸切除。给 depth（盲切）、through_all=true（贯穿）或 up_to（切到指定面/实体）。"
                "单向 through_all 只穿透草图平面一侧的材料；草图平面在实体中间、要两侧都切穿时用 direction=both。"
                "direction=both 时 depth 是总深度（两侧各一半）。" + _EXTENT_NOTE
            ),
            properties={"direction": {
                "type": "string",
                "description": "切除方向：+X/-X/.../both，默认 auto：实体面向实体内部；基准面在实体边界时朝向实体，在实体中间时为 both。",
            }},
        ),
        "hole": dict(
            description=(
                "在平面上按圆心列表（centers）或极坐标布孔（polar）打同直径的圆孔（切除），给 depth 或 through_all=true。"
                "钻孔盲孔的锥形孔底给 tip_angle（麻花钻 118°）：depth 为全直径圆柱段的深度（到钻尖肩部），"
                "钻尖锥另加 (diameter/2)/tan(tip_angle/2)；不给 tip_angle 为平底孔。" + _SIZE_NOTE
            ),
            properties={"polar": _POLAR_SCHEMA, "tip_angle": _TIP_ANGLE}, required=["plane", "diameter"],
        ),
        "counterbore": dict(properties={"polar": _POLAR_SCHEMA},
                            required=["plane", "through_diameter", "head_diameter", "head_depth"]),
        "countersink": dict(
            description=(
                "锥形沉头孔（埋头孔，切除）：在 plane（零件表面）上切出口部直径 head_diameter、锥角 angle_deg（默认 90°）的锥面，"
                "锥面深度自动为 (head_diameter − through_diameter)/2 ÷ tan(angle/2)，再打 through_diameter 的孔"
                "（给 depth 或 through_all=true）。只给 head_depth 不给 angle_deg 时由它推出锥角。圆柱形沉头请用 counterbore。"
            ),
            properties={
                "polar": _POLAR_SCHEMA,
                "angle_deg": {"type": "number", "description": "锥角（全角）度，默认 90"},
            },
            required=["plane", "through_diameter", "head_diameter"],
        ),
        "tapped_hole": dict(
            description=(
                "螺纹底孔（可选装饰螺纹）。diameter 为底孔直径；thread_diameter 为公称直径（给了才加装饰螺纹）。"
                "diameter 可写螺纹代号（\"10-32 UNF\"、\"M8\"、\"M8x1\"）自动取推荐底孔钻，或写钻头代号；"
                "thread_diameter 写螺纹代号时取大径。" + _SIZE_NOTE
            ),
            properties={"polar": _POLAR_SCHEMA, "tip_angle": _TIP_ANGLE}, required=["plane", "diameter"],
        ),
        "fillet": dict(
            description=(
                "对边倒圆角。一般放在最后；外形轮廓上的圆角（板的竖直棱等）优先在轮廓里用 corner_radius 表达。"
                "同一半径的多条棱写成一个射线数组一次倒完（同一个特征，角部过渡正确）；分多次逐条倒圆时，"
                "后倒的棱会顺着已有圆角端部的相切边传播到别的棱上。SolidWorks 默认沿相切边传播，"
                "propagate=false 时只倒选中的棱。"
            ),
            properties={"edges": edges_v2, "propagate": {
                "type": "boolean", "description": "是否沿相切边自动延伸，默认 true",
            }},
        ),
        "chamfer": dict(
            description="对边倒角（等距）。edges 写法同 fillet；多条棱写成射线数组时在同一个特征里一次倒完。",
            properties={"edges": edges_v2},
        ),
        "circular_pattern": dict(
            description=(
                "把最近一个特征绕平行于 axis 的轴圆周阵列 count 份（含原特征）。center 为轴线经过的点 [x,y,z] mm，请总是给出；"
                "缺省时自动找唯一的圆柱面。结果中 expected_delta_mm3 = 种子特征体积变化×(count−1)，与实际相差大说明有实例没生效。"
            ),
            properties={"angle_deg": {"type": "number", "description": "阵列覆盖的总角度，默认 360（整圈均分）；不是相邻两份的间隔"}},
        ),
        "linear_pattern": dict(
            description="把最近一个特征沿 1~2 个坐标方向线性阵列（含原特征）。direction 如 +X / -Z。结果会核对体积是否等于种子×份数。",
        ),
        "sweep": dict(
            description=(
                "沿开放路径扫描截面（cut=true 为扫描切除）。路径三选一："
                "(1) 平面路径 path_plane + path_start + path_segments；"
                "(2) 空间折线 path_3d={points:[[x,y,z],...], corner_radius?}（mm，模型坐标，corner_radius 写在 path_3d 里面，给每个转角加圆角，弯管常用）；"
                "各转角弯曲半径不同时改写 corner_radii:[r1, r2, ...]（每个内部点一个，0 表示尖角），整条管一次扫出，不要拆成几段；"
                "空间曲线写 path_3d={curve:{x:\"t 的表达式\", y:…, z:…, t:[t0, t1]}}，由系统采样成光滑样条（表达式写法同轮廓的 curve），"
                "或 path_3d={points:[...], spline:true} 让样条通过各点；不要手写几十上百个点的折线；"
                "(3) 螺旋线 helix={axis:\"Z\"|\"-Y\"…, center:[x,y,z]（螺旋起始端的轴心）, diameter|radius, pitch, turns|height, "
                "right_handed?=true}，起点在 center 沿 +X（axis 为 X 时沿 +Z）偏一个半径处（弹簧、螺旋槽、螺纹）；"
                "变螺距时 pitch 写成 [[圈数, 螺距], ...]（圈数从 0 起递增，相邻两点之间螺距线性变化），此时必须给 turns，"
                "系统按螺距对圈数积分算轴向高度，结果里回显终点和总升高。"
                "截面二选一：圆形截面 profile_diameter（+ 可选 inner_diameter 生成空心管），任意路径方向都可用，最省事；"
                "或自定义闭合 loops：平面路径/折线路径省略 profile_plane 时，截面自动放在路径起点、垂直于起点切向"
                "（要求第一段沿坐标轴），loops 以路径起点为原点 (0,0)、按该平面的 (u,v) 轴给出"
                "（法向沿 X → u=Z,v=Y；沿 Y → u=X,v=Z；沿 Z → u=X,v=Y）；螺旋线的 loops 用 (径向向外, 轴向前进) 坐标、以螺旋起点为原点。"
                "给出 profile_plane 时按原写法：截面画在路径起点处、平面大致垂直于路径切向，坐标为模型坐标。"
                "圆形截面会返回 path_length_mm 与 nominal_volume_mm3（截面积×路径长）供核对。"
            ),
            properties={
                "profile_plane": {
                    "type": "string",
                    "description": "截面平面；省略或 \"auto\" 时自动放在路径起点并垂直于起点切向，loops 用相对路径起点的坐标。",
                },
                "path_3d": {
                    "type": "object",
                    "description": "空间路径：折线 {points:[[x,y,z],...], corner_radius? 或逐角 corner_radii?}、样条 {points, spline:true} 或参数曲线 {curve:{x, y, z, t:[t0, t1]}}，mm",
                },
                "helix": {
                    "type": "object",
                    "description": "螺旋线路径 {axis, center:[x,y,z], diameter|radius, pitch（常数或 [[圈数, 螺距], ...]）, turns|height, right_handed?}",
                },
                "profile_diameter": {"type": "number", "description": "圆形截面直径 mm（与 loops 二选一）"},
                "inner_diameter": {"type": "number", "description": "圆形截面的内径 mm，生成空心管（仅凸台）"},
            },
            required=[],
        ),
        "loft": dict(
            description=(
                "在两个或多个截面之间放样（cut=true 为放样切除）。sections 按顺序排列，每个 {plane, loops}；"
                "第一个或最后一个截面可以写成 {plane, point:[u,v]}，收成一个点（尖顶、锥头）。"
                "guides 为可选引导线数组，每条为平面开放路径 {plane, start, segments}（写法同 sweep 的平面路径）"
                "或空间折线/样条 {points:[[x,y,z],...], spline?, corner_radius? 或 corner_radii?}、空间参数曲线 {curve:{x, y, z, t:[t0, t1]}}；"
                "平面路径的段也可以是 {curve:{u, v, t}}（写法同轮廓）。引导线必须与每个截面的轮廓相交（端点落在截面轮廓上）。"
                "start_tangent / end_tangent = none（默认）| normal：首/尾截面处的侧面垂直于截面（平滑过渡到相邻的拉伸段），"
                "tangent_length 为相切影响系数（默认 1）。"
            ),
            properties={
                "sections": {
                    "type": "array", "items": {"type": "object"},
                    "description": "截面数组，每个 {plane, loops} 或首/尾的 {plane, point:[u,v]}",
                },
                "guides": {"type": "array", "items": {"type": "object"}, "description": "引导线数组（可选）"},
                "start_tangent": {"type": "string", "enum": ["none", "normal"]},
                "end_tangent": {"type": "string", "enum": ["none", "normal"]},
                "tangent_length": {"type": "number"},
            },
        ),
        "ref_plane": dict(
            description=(
                "新建与参考平面平行的基准面。offset 沿参考平面的法向（基准面为 +轴向，实体面为外法向），可为负。"
                "同一位置、同一法向的基准面会直接复用。多数情况下直接把 \"Z=40\" 写进 plane 更简单。"
            ),
        ),
        "edit_feature": dict(
            description=(
                "修改已有实体特征的第一个尺寸（拉伸深度、筋厚、圆角半径、阵列总角度等）。name 来自 get_model_state；"
                "value 对长度尺寸是 mm，对角度尺寸是度；结果里的 old_value_mm / old_value_deg 标明了单位。"
                "基准面不能用它修改，请直接用新的 \"Z=…\" 平面。"
            ),
        ),
        "delete_feature": dict(
            description=(
                "删除特征（连同它吸收的草图）。依赖它的后续特征会被 SolidWorks 一并删除，结果中 also_removed 列出被连带删除的特征。"
                "可给 name 或 names（按给出的顺序删除）。改尺寸请用 edit_feature。"
            ),
            properties={"names": {"type": "array", "items": {"type": "string"}}}, required=[],
        ),
        "finish": dict(
            description=(
                "确认零件已满足需求后调用，结束建模。调用前用 get_model_state 核对：总体尺寸、孔/凸台的数量与直径、只有一个实体。"
                "零件有多个实体时会被拒绝。"
            ),
        ),
    }
    specs = []
    for spec in TOOL_SPECS:
        name = spec["function"]["name"]
        patch = patches.get(name)
        specs.append(_patch_spec(spec, **patch) if patch else _patch_spec(spec))
    assert {s["function"]["name"] for s in specs} == set(by_name)
    return specs


_AXIS_DISTANCE = {
    "type": ["number", "object"],
    "description": "沿 direction 到 origin 的有符号距离 mm，或 {z: 8}（轴线到达 z=8 处，x/y 同理）、{point:[x,y,z]}（轴上一点）",
}
AXIS_CYLINDER_SPEC = _tool(
    "axis_cylinder",
    "沿任意空间轴线生成圆柱：凸台（接管、斜柱，inner_diameter 生成空心管）或 cut=true 的圆柱孔（斜孔、径向孔）。"
    "轴线 = origin + s·direction，圆柱占 s ∈ [from, to]（mm，from 缺省 0）。不必自己算两端点坐标："
    "direction 可写向量 [dx,dy,dz]（不必归一化，可用 \"=tand(20)\" 这类表达式），"
    "或 {lat_deg, lon_deg}（纬度从 XY 平面向 +Z 量，经度从 +X 逆时针量；球心出发的径向接管直接用球心作 origin），"
    "或 {toward:[x,y,z]}（指向轴上另一点）。"
    "from/to 也可写成 {z: 8}（轴线到达 z=8 处）或 {point:[x,y,z]}。"
    "斜孔：origin 取孔口中心，direction 指向材料内部，from 取 -diameter 左右，让圆柱从实体外开始，保证孔口完整。"
    "接管凸台：整个起点截面都要埋进已有实体，不能只让轴线与外表面的交点落在表面上——"
    "轴线不过球心/不垂直于表面时，截面边缘处的外表面更靠里，会留缝。壳体类零件最稳的顺序是："
    "先建实心外形，接管从实心体内部开始（如 from=0 或轴线进入实体很深处），再切内腔、最后打接管内孔。"
    "起点截面露在实体外时结果会给出 warning。"
    "盲孔的锥形孔底给 tip_angle（麻花钻 118°），钻尖接在 to 端之后。"
    "结果回显两端点、单位方向和纬度/经度，请核对。轴向沿坐标轴的普通孔仍优先用 hole。",
    {
        "origin": {"type": "array", "items": {"type": "number"}, "description": "轴线上的参考点 [x,y,z] mm"},
        "direction": {"type": ["array", "object"], "description": "[dx,dy,dz]、{lat_deg, lon_deg} 或 {toward:[x,y,z]}"},
        "from": _AXIS_DISTANCE,
        "to": _AXIS_DISTANCE,
        "diameter": {"type": "number", "description": "圆柱直径 mm"},
        "inner_diameter": {"type": "number", "description": "空心管内径 mm（仅凸台）"},
        "cut": {"type": "boolean", "description": "true 为切除（孔）"},
        "tip_angle": _TIP_ANGLE,
    },
    ["origin", "direction", "to", "diameter"],
)

TOOL_SPECS_V2: list[dict] = [*_build_specs_v2(), AXIS_CYLINDER_SPEC]


VIEW_MODEL_SPEC = _tool(
    "view_model",
    "在 SolidWorks 里截取当前零件的着色图（带边线，隐藏基准面和草图），图片附在下一条消息里，"
    "用来直观检查形状、特征位置和朝向是否符合需求。views 为视向列表，最多 4 个，缺省为 "
    "[\"isometric\", \"front\", \"top\", \"right\"]；可选 isometric / trimetric / dimetric / front / back / "
    "left / right / top / bottom。前视沿 -Z 看（右 = +X、上 = +Y），俯视从 +Y 往下看（右 = +X、下 = +Z），"
    "右视从 +X 看（右 = -Z、上 = +Y）。图片很占上下文，历史里只保留最新一张；尺寸仍以 get_model_state 的数值为准。",
    {"views": {"type": "array", "items": {"type": "string"}, "description": "视向列表，最多 4 个"}},
    [],
)


_TOOL_PARAMS: dict[str, set[str]] = {
    spec["function"]["name"]: set(spec["function"]["parameters"]["properties"])
    for spec in [*TOOL_SPECS_V2, *map(_patch_spec, EXTRA_TOOL_SPECS.values()), VIEW_MODEL_SPEC]
}
# 对象型参数里的字段，用来提示“这个字段应写在哪个对象里”
_NESTED_FIELDS = {
    "path_3d": ("points", "corner_radius", "corner_radii", "spline", "curve"),
    "helix": ("axis", "center", "diameter", "radius", "pitch", "turns", "height", "right_handed"),
    "polar": ("center", "radius", "pcd", "count", "start_deg", "span_deg"),
}
# 不影响结果的多余参数只提示，不拒绝
_IGNORABLE_ARGS = {"name", "description", "comment", "note", "notes", "reason", "summary", "propagate"}


def unknown_arg_issue(tool: str, args: dict) -> tuple[list[str], WorkflowError | None]:
    """返回 (可忽略的多余参数, 需要拒绝时的错误)。不认识的工具不检查。"""
    known = _TOOL_PARAMS.get(tool)
    if known is None:
        return [], None
    unknown = [key for key in args if key not in known]
    ignorable = [key for key in unknown if key in _IGNORABLE_ARGS]
    rejected = [key for key in unknown if key not in _IGNORABLE_ARGS]
    if not rejected:
        return ignorable, None
    hints = []
    for key in rejected:
        owners = [owner for owner, fields in _NESTED_FIELDS.items() if owner in known and key in fields]
        if owners:
            hints.append(f"{key} 应写在 {' 或 '.join(owners)} 对象里面")
            continue
        close = difflib.get_close_matches(key, sorted(known), n=1, cutoff=0.75)
        if close:
            hints.append(f"{key} 是不是 {close[0]}")
            continue
        others = sorted(name for name, params in _TOOL_PARAMS.items() if key in params)
        if others:
            hints.append(f"{key} 是 {', '.join(others[:4])} 的参数，{tool} 没有这个参数")
    error = WorkflowError(
        "TOOL_ARGS", f"{tool} 不认识参数 {', '.join(rejected)}；未知参数不会生效，本次调用未执行",
        hint="；".join(hints) + ("。" if hints else "") + f"{tool} 的参数：{', '.join(sorted(known))}",
    )
    return ignorable, error


def tool_specs_v2(extra_tools: list[str] | None = None, *, view_tool: bool = True) -> list[dict]:
    specs = list(TOOL_SPECS_V2)
    for name in extra_tools or []:
        if name not in EXTRA_TOOL_SPECS:
            raise ValueError(f"未知的附加工具 {name}")
        specs.append(_patch_spec(EXTRA_TOOL_SPECS[name]))
    if view_tool:
        specs.append(VIEW_MODEL_SPEC)
    return specs


# ------------------------------------------------------------ 会话


def _parse_direction(text: Any) -> tuple[str, int]:
    """工具结果里的 direction 描述 → ("one"/"both"/"until", 符号)。"""
    value = str(text or "")
    if value == "both":
        return "both", 0
    if " until " in value:
        return "until", 1 if value.startswith("+") else -1
    if len(value) >= 2 and value[0] in "+-":
        return "one", 1 if value[0] == "+" else -1
    return "", 0


def _cylinder_key(face: dict) -> tuple | None:
    if face.get("surfaceType") != "cylinder" or not face.get("radius"):
        return None
    axis = face.get("axis") or {}
    main = max(AXES, key=lambda key: abs(float(axis.get(key.lower(), 0))))
    origin = face.get("axisOrigin") or {}
    point = [float(origin.get(key, 0)) * 1000 for key in "xyz"]
    # 轴线上的点只保留垂直于轴的两个坐标，同一根轴线上的面才能对上
    for i, key in enumerate(AXES):
        if key == main:
            point[i] = 0.0
    return round(float(face["radius"]) * 2000, 2), main, tuple(round(value, 2) for value in point)


def _shift(value: Any, delta: float) -> Any:
    if isinstance(value, str) and value.strip().startswith("="):
        return f"=({value.strip()[1:]})+({delta:g})"
    return float(value) + delta


def _shift_pair(point: Any, du: float, dv: float) -> Any:
    if isinstance(point, (list, tuple)) and len(point) == 2:
        return [_shift(point[0], du), _shift(point[1], dv)]
    return point


def translate_loop(loop: dict, du: float, dv: float) -> dict:
    """把轮廓整体平移 (du, dv)；数值和 "=表达式" 都支持。"""
    moved = dict(loop)
    for key in ("center", "start", "end", "corner1", "corner2"):
        if key in moved:
            moved[key] = _shift_pair(moved[key], du, dv)
    if isinstance(moved.get("points"), list):
        moved["points"] = [_shift_pair(point, du, dv) for point in moved["points"]]
    if isinstance(moved.get("segments"), list):
        moved["segments"] = [
            {key: (_shift_pair(value, du, dv) if key in ("to", "center", "through")
                   else [_shift_pair(point, du, dv) for point in value] if key == "spline" and isinstance(value, list)
                   else value)
             for key, value in step.items()} if isinstance(step, dict) else step
            for step in moved["segments"]
        ]
    if isinstance(moved.get("circles"), list):
        moved["circles"] = [translate_loop(circle, du, dv) if isinstance(circle, dict) else circle
                            for circle in moved["circles"]]
    return moved


def path_start_tangent(start: tuple[float, float], step: dict) -> tuple[float, float]:
    """开放路径第一段在起点处的 (u,v) 切向（未归一化）。center 圆弧不写 ccw 时按 L1 的默认顺时针。"""
    target = _pair(step.get("to"), "to")
    if step.get("spline"):
        first = _pair(step["spline"][0], "spline[0]")
        return first[0] - start[0], first[1] - start[1]
    if "center" in step:
        cu, cv = _pair(step["center"], "center")
        ru, rv = start[0] - cu, start[1] - cv
        return (-rv, ru) if step.get("ccw") else (rv, -ru)
    if "through" in step:
        mid = _pair(step["through"], "through")
        ax, ay = start
        bx, by = mid
        cx, cy = target
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) > 1e-9:
            ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
            uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
            ccw = (bx - ax) * (cy - by) - (by - ay) * (cx - bx) > 0
            ru, rv = ax - ux, ay - uy
            return (-rv, ru) if ccw else (rv, -ru)
    return target[0] - start[0], target[1] - start[1]


# ------------------------------------------------------------ 3D 路径

HELIX_POINTS_PER_TURN = 36
_AXIS_VECTORS = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def _axis_spec(text: Any, what: str = "axis") -> tuple[str, int]:
    """"Z" / "+Z" / "-Y" → (轴名, 符号)。"""
    value = str(text or "").strip().upper()
    sign = -1 if value.startswith("-") else 1
    name = value.lstrip("+-")
    if name not in _AXIS_VECTORS:
        raise WorkflowError("TOOL_ARGS", f"{what} 应为 X/Y/Z（可带 +/-），收到 {text!r}")
    return name, sign


def _cross(a: Any, b: Any) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _point3(value: Any, what: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise WorkflowError("TOOL_ARGS", f"{what} 必须是 [x, y, z]（mm）")
    return [float(item) for item in value]


def helix_geometry(spec: dict) -> dict:
    """螺旋线 → 采样点（mm）与起点处的局部方向。

    起点 = center + r·e_u（e_u 为轴向平面的第一个草图轴：Z/Y 轴 → +X，X 轴 → +Z）；
    右旋（默认）：沿 axis 前进时绕 axis 逆时针旋转。
    """
    axis, sign = _axis_spec(spec.get("axis", "Z"), "helix.axis")
    center = _point3(spec.get("center"), "helix.center")
    radius = _circle_radius(spec, "helix")
    table = _pitch_table(spec.get("pitch"))
    pitch = table[0][1] if len(table) == 1 else None
    if spec.get("turns") is not None:
        turns = float(spec["turns"])
    elif spec.get("height") is not None and pitch is not None:
        turns = float(spec["height"]) / pitch
    else:
        raise WorkflowError(
            "TOOL_ARGS", "helix 需要 turns（圈数）" + ("或 height（总高 mm）" if pitch is not None else "（变螺距时必填）"))
    if turns <= 0 or turns > 200:
        raise WorkflowError("TOOL_ARGS", f"helix 圈数 {turns:g} 不合理")
    right = spec.get("right_handed", True)
    right = str(right).strip().lower() != "false" if isinstance(right, str) else bool(right)
    e_u = _AXIS_VECTORS[UV_AXES[axis][0]]
    d = tuple(sign * value for value in _AXIS_VECTORS[axis])
    e_w = _cross(d, e_u)
    if not right:
        e_w = tuple(-value for value in e_w)
    count = max(8, math.ceil(turns * HELIX_POINTS_PER_TURN))
    points = []
    length = 0.0
    for i in range(count + 1):
        n = turns * i / count
        theta = 2 * math.pi * n
        rise = _pitch_integral(table, n)
        points.append([
            round(center[k] + radius * (math.cos(theta) * e_u[k] + math.sin(theta) * e_w[k]) + rise * d[k], 6)
            for k in range(3)
        ])
        if i:
            length += math.dist(points[-2], points[-1])
    if pitch is not None:
        length = turns * math.hypot(2 * math.pi * radius, pitch)
        pitch_text = f"螺距 {pitch:g}"
    else:
        pitch_text = f"变螺距 {[[_round(n, 4), _round(p, 4)] for n, p in table]}（[圈数, 螺距]，之间线性变化）"
    height = _pitch_integral(table, turns)
    return {
        "points": points,
        "start": points[0],
        "radial": e_u,
        "axial": d,
        "normal": e_w,
        "length_mm": length,
        "describe": (
            f"{'右' if right else '左'}旋螺旋线：轴 {'+' if sign > 0 else '-'}{axis}，起点 {points[0]}，终点 {points[-1]}，"
            f"半径 {radius:g}，{pitch_text}，{turns:g} 圈，轴向总升高 {_round(height, 4):g}"
        ),
    }


def _pitch_table(raw: Any) -> list[tuple[float, float]]:
    """pitch 为常数，或 [[圈数, 螺距], ...]（圈数递增，之间线性插值，两端外延为常数）。"""
    if isinstance(raw, (list, tuple)):
        table = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise WorkflowError("TOOL_ARGS", "变螺距 helix.pitch 应为 [[圈数, 螺距], ...]")
            table.append((float(item[0]), float(item[1])))
        if not table or any(b[0] <= a[0] for a, b in zip(table, table[1:])):
            raise WorkflowError("TOOL_ARGS", "helix.pitch 的圈数必须严格递增")
    else:
        table = [(0.0, float(raw or 0))]
    if any(p <= 0 for _, p in table):
        raise WorkflowError("TOOL_ARGS", "helix.pitch（螺距 mm）必须为正")
    return table


def _pitch_integral(table: list[tuple[float, float]], n: float) -> float:
    """∫₀ⁿ pitch(m) dm：螺旋线转过 n 圈时的轴向升高。"""
    if len(table) == 1:
        return table[0][1] * n
    knots = [(0.0, _pitch_at(table, 0.0))]
    knots += [(m, p) for m, p in table if 0.0 < m < n]
    knots.append((n, _pitch_at(table, n)))
    return sum((b[0] - a[0]) * (a[1] + b[1]) / 2 for a, b in zip(knots, knots[1:]))


def _pitch_at(table: list[tuple[float, float]], n: float) -> float:
    if n <= table[0][0]:
        return table[0][1]
    for (m0, p0), (m1, p1) in zip(table, table[1:]):
        if n <= m1:
            return p0 + (p1 - p0) * (n - m0) / (m1 - m0)
    return table[-1][1]


def polyline_geometry(spec: Any) -> dict:
    """3D 折线（可带转角圆角）→ 去掉重合/共线的中间点，校验圆角放得下，并给出路径长度。"""
    if isinstance(spec, list):
        spec = {"points": spec}
    if not isinstance(spec, dict):
        raise WorkflowError("TOOL_ARGS", "path_3d 应为 {points:[[x,y,z],...], corner_radius?}")
    raw = [_point3(p, "path_3d.points[i]") for p in spec.get("points") or []]
    radius = float(spec.get("corner_radius") or 0)
    per_corner = spec.get("corner_radii")
    if per_corner is not None:
        if not isinstance(per_corner, list) or len(per_corner) != max(0, len(raw) - 2):
            raise WorkflowError(
                "TOOL_ARGS", f"path_3d.corner_radii 须是 {max(0, len(raw) - 2)} 个数（每个内部点一个，0 表示不倒圆）",
                hint="例：points 为 [P0, P1, P2, P3] 时写 corner_radii: [45, 60]。",
            )
        raw_radii = [0.0, *(float(r or 0) for r in per_corner), 0.0]
    else:
        raw_radii = [0.0, *([radius] * max(0, len(raw) - 2)), 0.0]
    points: list[list[float]] = []
    radii: list[float] = []
    for point, r in zip(raw, raw_radii):
        if points and math.dist(point, points[-1]) < 1e-6:
            radii[-1] = max(radii[-1], r)
            continue
        if len(points) >= 2:
            a, b = points[-2], points[-1]
            u = [b[k] - a[k] for k in range(3)]
            w = [point[k] - b[k] for k in range(3)]
            if math.sqrt(sum(c * c for c in _cross(u, w))) <= 1e-9 * math.dist(a, b) * math.dist(b, point) \
                    and sum(u[k] * w[k] for k in range(3)) > 0:
                points[-1], radii[-1] = point, r
                continue
        points.append(point)
        radii.append(r)
    if len(points) < 2:
        raise WorkflowError("TOOL_ARGS", "path_3d 至少需要 2 个不重合的点")
    radii[0] = radii[-1] = 0.0
    lengths = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(lengths)
    cuts = [0.0] * len(lengths)
    for i in range(1, len(points) - 1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        u = [(b[k] - a[k]) / lengths[i - 1] for k in range(3)]
        w = [(c[k] - b[k]) / lengths[i] for k in range(3)]
        turn = math.acos(max(-1.0, min(1.0, sum(u[k] * w[k] for k in range(3)))))
        if turn > math.pi - 1e-6:
            raise WorkflowError("TOOL_ARGS", f"path_3d 在第 {i} 个点处原路折返")
        if radii[i] > 0:
            tangent = radii[i] * math.tan(turn / 2)
            cuts[i - 1] += tangent
            cuts[i] += tangent
            total += radii[i] * turn - 2 * tangent
    for i, (length, cut) in enumerate(zip(lengths, cuts)):
        if cut > length + 1e-6:
            raise WorkflowError(
                "TOOL_ARGS",
                f"path_3d 第 {i} 段长 {length:g}mm，放不下两端的转角圆角（需 {cut:g}mm）",
                hint="减小 corner_radius 或加长该段。",
            )
    interior = radii[1:-1]
    uniform = len(set(interior)) <= 1
    if not interior or not any(interior):
        rounding = ""
    elif uniform:
        rounding = f"，转角圆角 R{interior[0]:g}"
    else:
        rounding = "，转角圆角 " + "/".join(f"R{r:g}" for r in interior)
    return {
        "points": points,
        "start": points[0],
        "tangent": [points[1][k] - points[0][k] for k in range(3)],
        "corner_radius": interior[0] if interior and uniform else 0.0,
        "corner_radii": None if uniform else interior,
        "length_mm": total,
        "describe": f"3D 折线 {len(points)} 点{rounding}",
    }


def path3d_operation(points: list[list[float]], *, spline: bool = False, corner_radius: float = 0,
                     corner_radii: list[float] | None = None) -> dict:
    params: dict[str, Any] = {
        "points": [[value / 1000.0 for value in point] for point in points],
        "spline": spline, "cornerRadius": corner_radius / 1000.0,
    }
    if corner_radii is not None:
        params["cornerRadii"] = [r / 1000.0 for r in corner_radii]
    return {"operation": "sketch3d_path", "route": "/api/sketch3d/path", "params": params}


@dataclass(frozen=True)
class LocalFrame(SketchFrame):
    """轴对齐平面上的局部坐标：(a, b) → 平面 (u, v) = origin + a·a_dir + b·b_dir。"""

    plain: SketchFrame | None = None
    origin_uv: tuple[float, float] = (0.0, 0.0)
    a_dir: tuple[float, float] = (1.0, 0.0)
    b_dir: tuple[float, float] = (0.0, 1.0)
    local_label: str = ""

    @classmethod
    def on(cls, plain: SketchFrame, origin_uv: tuple[float, float], a_dir: tuple[float, float],
           b_dir: tuple[float, float], local_label: str) -> "LocalFrame":
        return cls(
            label=plain.label, axis=plain.axis, normal_sign=plain.normal_sign, offset_mm=plain.offset_mm,
            plane_index=plain.plane_index, face_index=plain.face_index, face_box_mm=None,
            ref_plane_name=plain.ref_plane_name, plain=plain, origin_uv=origin_uv,
            a_dir=a_dir, b_dir=b_dir, local_label=local_label,
        )

    def _uv(self, a: float, b: float) -> tuple[float, float]:
        return (self.origin_uv[0] + a * self.a_dir[0] + b * self.b_dir[0],
                self.origin_uv[1] + a * self.a_dir[1] + b * self.b_dir[1])

    def to_model_mm(self, a: float, b: float) -> tuple[float, float, float]:
        return self.plain.to_model_mm(*self._uv(a, b))

    def point_params(self, a: float, b: float, *, keys=("x", "y", "z")) -> dict:
        return self.plain.point_params(*self._uv(a, b), keys=keys)

    def boundary_uv(self) -> dict | None:
        return None

    def describe(self) -> str:
        return f"{self.plain.describe()}；截面局部坐标 {self.local_label}"


def _axis_frame_dirs(plane_axis: str, a_vec: Any, b_vec: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    u_axis, v_axis = UV_AXES[plane_axis]
    ui, vi = AXES.index(u_axis), AXES.index(v_axis)
    return (a_vec[ui], a_vec[vi]), (b_vec[ui], b_vec[vi])


_ADDING_TOOLS = {"extrude", "sweep", "revolve", "loft", "rib"}


def _with_hint(error: dict, original: dict, tool: str = "") -> dict:
    code = error.get("code")
    if code in ("REF_NOT_FOUND", "REF_NOT_PLANAR", "REF_AMBIGUOUS") and "face#" in json.dumps(original, ensure_ascii=False):
        error.setdefault(
            "hint",
            "face# 编号在每次特征之后都会重新编号；请用最新的 get_model_state，"
            "或改用 face(+Z, outermost) 或 \"Z=40\" 这类不随编号变化的写法。",
        )
    elif code == "FEATURE_FAILED" and not original.get("cut") and tool in _ADDING_TOOLS:
        error["hint"] = (
            "新凸台/扫描体常见的失败原因：轮廓只和已有实体相切或线接触（例如轮廓边恰好落在圆柱面半径上、截面贴着轮毂外圆），"
            "SolidWorks 无法合并零厚度接触。让新轮廓伸入已有实体 ≥0.5mm，或离开足够距离后再连接。"
            + (f" 原提示：{error['hint']}" if error.get("hint") else "")
        )
    elif code == "PARAM_FORBIDDEN":
        error.setdefault(
            "hint",
            f"可用函数：{', '.join(PARAM_FUNCTIONS)}；常量 pi。三角函数按度计算，写 sind/cosd/tand"
            "（按弧度的 sin/cos/tan 只能用在参数曲线 curve 的表达式里）。",
        )
    return error


def _axis_planes(snapshot: dict) -> list[tuple[int, float, int, dict, dict]]:
    """轴对齐的平面：(法向轴, 位置 mm, 法向符号, 各轴范围 mm, 原始面)。"""
    planes = []
    for face in snapshot.get("faces") or []:
        if face.get("surfaceType") != "plane":
            continue
        normal = [float((face.get("normal") or {}).get(key, 0)) for key in "xyz"]
        axis = max(range(3), key=lambda i: abs(normal[i]))
        box = face.get("box") or {}
        if abs(normal[axis]) < 0.999 or not box:
            continue
        ranges = {i: (float(box[f"min{key}"]) * 1000, float(box[f"max{key}"]) * 1000) for i, key in enumerate(AXES)}
        planes.append((axis, ranges[axis][0], 1 if normal[axis] > 0 else -1, ranges, face))
    return planes


def ray_ambiguity(snapshot: dict, ray: dict, index: int | None = None, eps: float = 0.3) -> str | None:
    """按快照里的轴对齐平面估算射线命中点；射线贴着面掠过或命中面的角点时，选中的边不确定。"""
    try:
        origin = [float(v) for v in ray["origin"]]
        direction = [float(v) for v in ray["direction"]]
    except (KeyError, TypeError, ValueError):
        return None
    norm = math.sqrt(sum(v * v for v in direction))
    if len(origin) != 3 or len(direction) != 3 or norm == 0:
        return None
    direction = [v / norm for v in direction]
    label = f"第 {index + 1} 条射线" if index is not None else "射线"
    inside = lambda point, ranges, skip: all(  # noqa: E731
        ranges[i][0] - eps <= point[i] <= ranges[i][1] + eps for i in range(3) if i != skip
    )
    hit = None
    for axis, at, _sign, ranges, face in _axis_planes(snapshot):
        if abs(direction[axis]) < 1e-6:
            if abs(origin[axis] - at) <= eps and inside(origin, ranges, axis):
                return (
                    f"{label}的起点在 face#{face.get('index')}（{AXES[axis]}={at:g}）上且方向与该面平行，射线贴着面掠过，"
                    "选中的边不确定。射线应从实体外、垂直射向与目标棱相邻的一个面，命中点离目标棱约 1mm、离其它棱更远。"
                )
            continue
        t = (at - origin[axis]) / direction[axis]
        if t < -1e-6:
            continue
        point = [origin[i] + t * direction[i] for i in range(3)]
        if inside(point, ranges, axis) and (hit is None or t < hit[0]):
            hit = (t, axis, at, ranges, face, point)
    if hit is None:
        return None
    _t, axis, at, ranges, face, point = hit
    near = [i for i in range(3) if i != axis and min(abs(point[i] - ranges[i][0]), abs(point[i] - ranges[i][1])) <= eps]
    if len(near) < 2:
        return None
    where = ", ".join(f"{v:g}" for v in (round(value, 3) for value in point))
    return (
        f"{label}命中 face#{face.get('index')}（{AXES[axis]}={at:g}）的角点 ({where})，那里有两条以上的棱，选中的边不确定。"
        "让命中点落在目标棱旁约 1mm、离其它棱更远的位置。"
    )


class WorkflowSessionV2(WorkflowSession):
    version = "v2"

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._notes: list[str] = []
        self._axes: dict[tuple, str] = {}
        self._handlers["view_model"] = self.view_model
        self._handlers["axis_cylinder"] = self.axis_cylinder

    def view_model(self, args: dict) -> dict:
        from planner.sw_views import capture_grid

        state = self.state_summary()
        if not state.get("volume_mm3"):
            raise WorkflowError("NO_BODY", "零件中还没有实体，截图看不到东西")
        image, meta = capture_grid(self._request, "part", args.get("views"))
        return {
            "bbox_mm": state["bbox_mm"],
            "volume_mm3": state["volume_mm3"],
            **meta,
            "note": "截图附在下一条消息里",
            IMAGE_KEY: image,
        }

    def snapshot(self) -> dict:
        data = self._request("GET", f"/api/model-observation?maxFaces={OBSERVATION_MAX_FACES}")
        if not data.get("success"):
            raise WorkflowError("NO_PART", str(data.get("message") or "读取模型失败"))
        return data

    @staticmethod
    def _open_sketch(frame: SketchFrame) -> dict:
        if frame.is_face:
            return {
                "operation": "select_observed_face_and_sketch", "route": "/api/select-observed-face-and-sketch",
                "params": {"faceIndex": frame.face_index, "exact": True},
            }
        return WorkflowSession._open_sketch(frame)

    # ------------------------------------------------------------ 调度

    def call(self, name: str, args: dict | None) -> dict:
        original = args or {}
        self._notes = []
        try:
            ignored, rejected = unknown_arg_issue(name, original)
            if rejected is not None:
                raise rejected
            if ignored:
                self._notes.append(f"已忽略多余参数 {', '.join(ignored)}（{name} 不使用它们）")
            prepared = self._prepare_args(name, coerce_args(original)) if self.has_part else coerce_args(original)
        except WorkflowError as exc:
            result = {"ok": False, "error": _with_hint(exc.to_dict(), original, name)}
            self.log.append({"tool": name, "args": original, "result": result})
            return result
        except (KeyError, TypeError, ValueError) as exc:
            result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"参数错误: {exc}"}}
            self.log.append({"tool": name, "args": original, "result": result})
            return result
        result = super().call(name, prepared)
        self.log[-1]["args"] = original
        if self._notes:
            result["notes"] = [*result.get("notes", []), *self._notes]
        if result.get("error"):
            _with_hint(result["error"], original, name)
        return result

    def _prepare_args(self, name: str, args: dict) -> dict:
        for key in _PLANE_KEYS:
            if isinstance(args.get(key), str):
                args[key] = self._axis_plane(args[key])
        if "loops" in args:
            args["loops"] = prepare_loops(self._expand_curve_loops(args["loops"]), self._notes)
        if isinstance(args.get("sections"), list):
            sections = []
            for section in args["sections"]:
                if isinstance(section, dict):
                    section = coerce_args(section)
                    if isinstance(section.get("plane"), str):
                        section["plane"] = self._axis_plane(section["plane"])
                    section["loops"] = prepare_loops(self._expand_curve_loops(section.get("loops")), self._notes)
                sections.append(section)
            args["sections"] = sections
        if isinstance(args.get("path_segments"), list):
            args["path_start"], args["path_segments"] = self._expand_curve_steps(
                args.get("path_start"), args["path_segments"], "path_segments")
        if isinstance(args.get("guides"), list):
            guides = []
            for guide in args["guides"]:
                if isinstance(guide, dict) and isinstance(guide.get("segments"), list):
                    guide = dict(guide)
                    guide["start"], guide["segments"] = self._expand_curve_steps(
                        guide.get("start"), guide["segments"], "guides[i].segments")
                guides.append(guide)
            args["guides"] = guides
        if name in _HOLE_TOOLS and args.get("polar") is not None:
            centers = list(self._resolve_numbers(args.get("centers")) or [])
            generated = polar_centers(self._resolve_numbers(args["polar"]))
            # 模型有时既给 polar，又把极坐标阵列的第一个圆心放进 centers。
            # 重复圆会生成重合草图实体，SolidWorks 可能长时间卡在切除特征中。
            merged: list[list[float]] = []
            for value in centers + generated:
                center = [float(item) for item in value]
                if not any(math.dist(center, existing) <= 1e-6 for existing in merged):
                    merged.append(center)
            args["centers"] = merged
            self._notes.append(f"polar 生成的圆心 (u,v): {generated}")
        for key, nominal in _SIZE_KEYS.get(name, ()):
            value = args.get(key)
            if isinstance(value, str) and value.strip() not in self.params.params:
                found = resolve_size(value, nominal=nominal)
                if found is not None:
                    args[key] = round(found[0], 4)
                    self._notes.append(f"{key}=\"{value}\" 按标准表取 {found[1]} = {found[0]:.4f}mm")
        return args

    def _curve_points(self, spec: Any, keys: tuple[str, ...], what: str) -> list[tuple]:
        spec = coerce_args(spec) if isinstance(spec, dict) else spec
        if isinstance(spec, dict) and spec.get("t") is not None:
            spec = {**spec, "t": self._resolve_numbers(spec["t"])}
        return param_curves.sample(spec, keys, self.params.resolve(), what)

    def _expand_curve_steps(self, start: Any, steps: list, what: str) -> tuple[Any, list]:
        """路径里的 {curve:{u, v, t}} 段 → 通过采样点的样条段；起点缺省时取第一条曲线的起点。"""
        if not any(isinstance(step, dict) and "curve" in step for step in steps):
            return start, steps
        cursor = self._resolve_numbers(start)
        expanded: list = []
        after_curve = False
        for index, step in enumerate(steps):
            if not (isinstance(step, dict) and "curve" in step):
                target = self._resolve_numbers(step["to"]) if isinstance(step, dict) and "to" in step else None
                if (after_curve and target is not None and len(step) == 1
                        and math.dist(_pair(cursor, "起点"), _pair(target, f"{what}[{index}].to")) <= 1e-3):
                    self._notes.append(f"{what}[{index}] 与前一条曲线的终点重合，已省略")
                    continue
                expanded.append(step)
                after_curve = False
                if target is not None:
                    cursor = target
                continue
            points = self._curve_points(step["curve"], ("u", "v"), f"{what}[{index}].curve")
            first, last = list(points[0]), list(points[-1])
            if cursor is None:
                start = cursor = first
            elif math.dist(_pair(cursor, "起点"), first) > 1e-3:
                expanded.append({"to": first})
                self._notes.append(
                    f"{what}[{index}] 的曲线起点 {first} 与上一点 {list(cursor)} 不重合，已用直线连接")
            expanded.append({"to": last, "spline": [list(p) for p in points[1:-1]]})
            cursor = last
            after_curve = True
        return start, expanded

    def _expand_curve_loops(self, loops: Any) -> Any:
        if not isinstance(loops, list):
            return loops
        result = []
        for index, loop in enumerate(loops):
            kind = str(loop.get("type", "")).lower() if isinstance(loop, dict) else ""
            if kind == "curve":
                points = [list(p) for p in self._curve_points(loop, ("u", "v"), f"loops[{index}]")]
                if math.dist(points[0], points[-1]) <= 1e-3:
                    points = points[:-1]
                    half = len(points) // 2
                    if half < 2:
                        raise WorkflowError("SKETCH_DEGENERATE", f"loops[{index}] 闭合曲线的点太少")
                    segments = [{"to": points[half], "spline": points[1:half]},
                                {"to": points[0], "spline": points[half + 1:]}]
                else:
                    segments = [{"to": points[-1], "spline": points[1:-1]}]
                result.append({"type": "path", "start": points[0], "segments": segments})
            elif kind == "path" and isinstance(loop.get("segments"), list):
                start, segments = self._expand_curve_steps(loop.get("start"), loop["segments"], f"loops[{index}].segments")
                result.append({**loop, "start": start, "segments": segments})
            else:
                result.append(loop)
        return result

    def _axis_plane(self, text: str) -> str:
        """"Z=40" → 该位置的基准面名（Z=0 即 front_plane）；其它写法原样返回。"""
        match = _AXIS_PLANE.match(text)
        if not match:
            return text
        axis, value = match.group(1).upper(), float(match.group(2))
        if abs(value) <= 1e-9:
            return _AXIS_BASE_PLANE[axis]
        existing = self._find_ref_plane(axis, value, 1)
        if existing:
            return existing
        created = self.ref_plane({"reference": _AXIS_BASE_PLANE[axis], "offset": value})
        self._notes.append(f"{text} → 新建基准面 {created['name']}（{created['plane']}）")
        return created["name"]

    def _find_ref_plane(self, axis: str, offset: float, sign: int) -> str | None:
        for name, frame in self.ref_planes.items():
            if frame.axis == axis and frame.normal_sign == sign and abs(frame.offset_mm - offset) <= 1e-3:
                return name
        return None

    # ------------------------------------------------------------ 事务

    def _bodies(self, snapshot: dict | None) -> int | None:
        value = (snapshot or {}).get("bodyCount")
        return int(value) if isinstance(value, (int, float)) else None

    def _transaction(self, operations: list[dict], *, expect: str, before: dict, context: dict) -> dict:
        result = super()._transaction(operations, expect=expect, before=before, context=context)
        after = self.last_transaction[1]
        bodies_before, bodies_after = self._bodies(before), self._bodies(after)
        if bodies_after is not None and bodies_after > max(bodies_before or 0, 1):
            rolled_back = self._rollback(result.get("created") or [])
            self._prune_ref_planes()
            raise WorkflowError(
                "FEATURE_DISCONNECTED",
                f"特征执行后零件变成了 {bodies_after} 个实体（之前 {bodies_before or 0} 个），已回滚",
                hint=(
                    "新增的部分没有和已有实体连成一体：只有线/点接触（相切）或中间有间隙，或者切除把零件切成了几块。"
                    "让新凸台伸入已有实体 ≥0.5mm（例如把轮廓或深度加长一点），或检查切除范围。"
                    "双盘+立柱：先做贯通两盘间距的立柱，再贴合拉伸第二盘并保持题面给定的盘厚，不要为了连体去加厚盘；"
                    "或一次画出柱+盘的完整截面再拉伸。"
                ),
                rolled_back=rolled_back,
                bodies=after.get("bodies"),
                **context,
            )
        changes = self._cylinder_changes(before, after)
        if changes:
            result.update(changes)
        if bodies_after is not None:
            result["bodies"] = bodies_after
        return result

    def _rollback(self, names: list[str]) -> list[str]:
        removed = super()._rollback(names)
        self._prune_ref_planes()
        return removed

    def _prune_ref_planes(self) -> None:
        if not self.ref_planes:
            return
        remaining = {feature.get("name") for feature in self.snapshot().get("features", [])}
        self.ref_planes = {key: frame for key, frame in self.ref_planes.items() if key in remaining}

    @staticmethod
    def _cylinder_groups(snapshot: dict) -> dict[tuple, set]:
        groups: dict[tuple, set] = {}
        for face in snapshot.get("faces") or []:
            key = _cylinder_key(face)
            if key is not None:
                groups.setdefault(key[:2], set()).add(key[2])
        return groups

    def _cylinder_changes(self, before: dict, after: dict) -> dict:
        old, new = self._cylinder_groups(before), self._cylinder_groups(after)
        added, removed = [], []
        for key in sorted(set(old) | set(new)):
            gained = sorted(new.get(key, set()) - old.get(key, set()))
            lost = sorted(old.get(key, set()) - new.get(key, set()))
            for target, points in ((added, gained), (removed, lost)):
                if points:
                    target.append({
                        "diameter_mm": key[0], "axis": key[1], "count": len(points),
                        "axis_at_mm": [list(point) for point in points[:_NEW_CYLINDER_LIMIT]],
                    })
        out = {}
        if added:
            out["new_cylinders"] = added
        if removed:
            out["removed_cylinders"] = removed
        return out

    # ------------------------------------------------------------ 范围与比例

    def _span(self, frame: SketchFrame, mode: str, sign: int, depth: float | None, through: bool,
              bbox: dict | None) -> list[float] | None:
        offset = frame.offset_mm
        lo, hi = (bbox or {}).get(frame.axis, [None, None])
        if mode == "both":
            if through or depth is None:
                return [lo, hi] if lo is not None else None
            return [offset - depth / 2, offset + depth / 2]
        if mode != "one":
            return None
        if through:
            if lo is None:
                return None
            return [offset, hi] if sign > 0 else [lo, offset]
        if depth is None:
            return None
        return sorted([offset, offset + sign * depth])

    @staticmethod
    def _extent(frame: SketchFrame, uv_bbox: dict | None, span: list[float] | None) -> dict | None:
        if not uv_bbox:
            return None
        extent = {frame.u_axis: uv_bbox.get("u"), frame.v_axis: uv_bbox.get("v")}
        if span is not None:
            extent[frame.axis] = [_round(span[0]), _round(span[1])]
        return {axis: extent.get(axis) for axis in AXES if extent.get(axis) is not None}

    def _feature_feedback(self, args: dict, result: dict, before: dict, *, removing: bool) -> dict:
        frame = self._frame(args.get("plane"), before)
        mode, sign = _parse_direction(result.get("direction"))
        through = bool(args.get("through_all", False)) and not args.get("up_to")
        depth = None if args.get("up_to") else self._num(args, "depth")
        bbox_before = self._bbox(before)
        span = self._span(frame, mode, sign, depth, through, bbox_before if removing else None)
        extent = self._extent(frame, result.get("profile_uv_bbox"), span)
        warnings = list(result.get("warnings") or [])
        if extent:
            result["extent_mm"] = extent
        area = float(result.get("region_area_mm2") or 0)
        delta = float(result.get("volume_delta_mm3") or 0)
        if area > 0 and depth and not through and mode in ("one", "both"):
            expected = area * depth
            ratio = abs(delta) / expected if expected > 0 else None
            if ratio is not None:
                result["removal_ratio" if removing else "fill_ratio"] = _round(ratio, 3)
                if removing and ratio < LOW_REMOVAL_RATIO:
                    warnings.append(
                        f"实际只去除了 轮廓面积×深度（{expected:.0f}mm³）的 {ratio:.0%}：轮廓大部分落在已有孔腔或实体之外，"
                        "或深度方向朝外，请核对 extent_mm 与零件包围盒。"
                    )
                elif not removing and ratio < LOW_ADDITION_RATIO:
                    warnings.append(
                        f"新增体积只有 轮廓面积×深度（{expected:.0f}mm³）的 {ratio:.0%}：新特征大部分与已有实体重叠，"
                        "检查拉伸方向和起始平面。"
                    )
        if mode == "both" and depth and not through:
            result["note"] = f"both：以 {frame.axis}={frame.offset_mm:g} 为中面，两侧各 {depth / 2:g}mm（depth 是总厚度）"
        if removing and mode == "both" and not args.get("direction"):
            warnings.append(
                f"没有给 direction，草图平面 {frame.axis}={frame.offset_mm:g} 在实体中间，按 both 向两侧切除；"
                "只想切一侧时写 direction=+轴/-轴。"
            )
        if removing and through and mode == "one" and bbox_before:
            lo, hi = bbox_before[frame.axis]
            offset = frame.offset_mm
            if lo + 1e-3 < offset < hi - 1e-3:
                other = f"[{offset:g}, {hi:g}]" if sign < 0 else f"[{lo:g}, {offset:g}]"
                where = "实体面" if frame.is_face else "草图平面"
                warnings.append(
                    f"{where} {frame.axis}={offset:g} 不在零件 {frame.axis} 方向的端部（范围 [{lo:g}, {hi:g}]），单向 through_all "
                    f"只切了 {'+' if sign > 0 else '-'}{frame.axis} 一侧；{frame.axis}∈{other} 一侧若在轮廓范围内也有材料（如凸台、轮毂），"
                    "那部分没有切。要整根贯穿，改在零件端面上画，或用 direction=both。"
                )
        if warnings:
            result["warnings"] = warnings
        return result

    def extrude(self, args: dict) -> dict:
        result = super().extrude(args)
        return self._feature_feedback(args, result, self.last_transaction[0], removing=False)

    def cut(self, args: dict) -> dict:
        result = super().cut(args)
        return self._feature_feedback(args, result, self.last_transaction[0], removing=True)

    def sweep(self, args: dict) -> dict:
        three_d = args.get("helix") is not None or args.get("path_3d") is not None
        if not three_d and args.get("profile_diameter") is None:
            if str(args.get("profile_plane") or "auto").strip().lower() == "auto":
                args = self._auto_sweep_profile(args)
            return super().sweep(args)
        before = self.snapshot()
        is_cut = bool(args.get("cut", False))
        if is_cut and self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法扫描切除")
        path_ops, path = self._sweep_path(args, before)
        context: dict[str, Any] = {"path": path["describe"]}
        if path.get("length_mm"):
            context["path_length_mm"] = _round(path["length_mm"], 2)
        warnings: list[str] = []
        diameter = self._num(args, "profile_diameter")
        if diameter is not None:
            inner = self._num(args, "inner_diameter") or 0.0
            if diameter <= 0 or inner < 0 or inner >= diameter:
                raise WorkflowError("TOOL_ARGS", "profile_diameter 须为正，inner_diameter 须小于它")
            if inner and is_cut:
                raise WorkflowError("TOOL_ARGS", "扫描切除不支持 inner_diameter")
            operations = [*path_ops, {
                "operation": "feature_sweep", "route": "/api/feature/sweep",
                "params": {"cut": is_cut, "circularDiameter": diameter / 1000, "innerDiameter": inner / 1000},
            }]
            context["profile"] = f"圆形截面 Ø{diameter:g}" + (f"，内径 Ø{inner:g}" if inner else "")
            if path.get("length_mm"):
                area = math.pi / 4 * (diameter ** 2 - inner ** 2)
                context["nominal_volume_mm3"] = _round(area * path["length_mm"], 2)
        else:
            frame = self._sweep_profile_frame(args, path, before)
            loops, check, warnings = self._profile(args, frame, removing=is_cut)
            operations = [
                *path_ops, self._open_sketch(frame), *draw_operations(frame, loops),
                {"operation": "feature_sweep", "route": "/api/feature/sweep", "params": {"cut": is_cut}},
            ]
            context["profile"] = frame.describe()
            context["region_area_mm2"] = check["region_area_mm2"]
        result = self._transaction(
            operations, expect="decrease" if is_cut else "increase", before=before, context=context,
        )
        nominal = context.get("nominal_volume_mm3")
        if nominal and not is_cut and self._volume(before) <= 0:
            delta = float(result.get("volume_delta_mm3") or 0)
            if abs(delta - nominal) > 0.05 * nominal:
                warnings.append(
                    f"扫描体积 {delta:g} mm³ 与 截面积×路径长 = {nominal:g} mm³ 相差较多，路径可能自交或截面在转弯处重叠"
                )
        return {**result, "warnings": warnings} if warnings else result

    def _sweep_path(self, args: dict, snapshot: dict) -> tuple[list[dict], dict]:
        """扫描路径的绘制操作和几何信息（起点、起点切向、长度，单位 mm）。"""
        if args.get("helix") is not None:
            spec = self._resolve_numbers(coerce_args(args["helix"]) if isinstance(args["helix"], dict) else args["helix"])
            if not isinstance(spec, dict):
                raise WorkflowError("TOOL_ARGS", "helix 应为 {axis, center, diameter|radius, pitch, turns|height}")
            helix = helix_geometry(spec)
            return [path3d_operation(helix["points"], spline=True)], {**helix, "kind": "helix"}
        if args.get("path_3d") is not None:
            spec = args["path_3d"]
            spec = coerce_args(spec) if isinstance(spec, dict) else spec
            if isinstance(spec, dict) and (spec.get("curve") is not None or spec.get("spline")):
                curve = self._spline_path(spec, "path_3d")
                return [path3d_operation(curve["points"], spline=True)], curve
            line = polyline_geometry(self._resolve_numbers(spec))
            op = path3d_operation(line["points"], corner_radius=line["corner_radius"], corner_radii=line["corner_radii"])
            return [op], {**line, "kind": "polyline"}
        path_frame = self._frame(args.get("path_plane"), snapshot, arg="path_plane")
        start = self._uv_pair(args.get("path_start"), "path_start")
        steps = self._resolve_numbers(args.get("path_segments"))
        if not isinstance(steps, list) or not steps:
            raise WorkflowError("TOOL_ARGS", "path_segments 不能为空")
        operations = [
            self._open_sketch(path_frame),
            *draw_open_path(path_frame, start, steps),
            {"operation": "close_sketch", "route": "/api/close-sketch-named", "params": {}},
        ]
        return operations, {"kind": "planar", "describe": path_frame.describe()}

    def _spline_path(self, spec: dict, what: str) -> dict:
        """{curve:{x, y, z, t}} 或 {points, spline:true} → 通过各点的 3D 样条。"""
        if spec.get("curve") is not None:
            points = [list(p) for p in self._curve_points(spec["curve"], ("x", "y", "z"), f"{what}.curve")]
            source = "参数曲线"
        else:
            points = [_point3(p, f"{what}.points[i]") for p in self._resolve_numbers(spec.get("points")) or []]
            source = "样条"
        if len(points) < 2:
            raise WorkflowError("TOOL_ARGS", f"{what} 样条至少需要 2 个点")
        length = param_curves.polyline_length(points)
        return {
            "kind": "curve", "points": points, "start": points[0],
            "tangent": [points[1][k] - points[0][k] for k in range(3)], "length_mm": length,
            "describe": f"3D {source}样条 {len(points)} 点，起点 {points[0]}，终点 {points[-1]}",
        }

    def _sweep_profile_frame(self, args: dict, path: dict, snapshot: dict) -> SketchFrame:
        explicit = str(args.get("profile_plane") or "auto").strip()
        if explicit.lower() != "auto":
            return self._frame(explicit, snapshot, arg="profile_plane")
        if path["kind"] == "planar":
            raise WorkflowError(
                "TOOL_ARGS", "平面路径 + 自定义截面请省略 profile_diameter，按原写法给 loops",
            )
        start = path["start"]
        if path["kind"] == "helix":
            normal = max(range(3), key=lambda i: abs(path["normal"][i]))
            plane_axis = AXES[normal]
            a_dir, b_dir = _axis_frame_dirs(plane_axis, path["radial"], path["axial"])
            label = "a=径向（向外）, b=轴向（螺旋前进方向），原点 = 螺旋起点"
        else:
            tangent = path["tangent"]
            norm = math.sqrt(sum(value * value for value in tangent))
            normal = max(range(3), key=lambda i: abs(tangent[i]))
            if abs(tangent[normal]) / norm < 0.999:
                raise WorkflowError(
                    "TOOL_ARGS", "path_3d 第一段不沿坐标轴，无法自动放置自定义截面",
                    hint="圆形截面改用 profile_diameter（任意方向都可以）；自定义截面请让第一段沿坐标轴。",
                )
            plane_axis = AXES[normal]
            a_dir, b_dir = (1.0, 0.0), (0.0, 1.0)
            u_axis, v_axis = UV_AXES[plane_axis]
            label = f"a={u_axis}, b={v_axis}，原点 = 路径起点"
        at = f"{start[normal]:.6f}".rstrip("0").rstrip(".")
        plain = self._frame(self._axis_plane(f"{plane_axis}={at}"), self.snapshot())
        u_axis, v_axis = UV_AXES[plane_axis]
        origin = (start[AXES.index(u_axis)], start[AXES.index(v_axis)])
        self._notes.append(f"截面平面自动取 {plane_axis}={at}；loops 坐标：{label}")
        return LocalFrame.on(plain, origin, a_dir, b_dir, label)

    def loft(self, args: dict) -> dict:
        guides = args.get("guides") or []
        if not isinstance(guides, list):
            raise WorkflowError("TOOL_ARGS", "guides 应为数组")
        tangents = {"none": 0, "normal": 1}
        start_tangent = str(args.get("start_tangent") or "none").strip().lower()
        end_tangent = str(args.get("end_tangent") or "none").strip().lower()
        if start_tangent not in tangents or end_tangent not in tangents:
            raise WorkflowError("TOOL_ARGS", "start_tangent / end_tangent 只能是 none 或 normal")
        sections = args.get("sections")
        has_point = isinstance(sections, list) and any(
            isinstance(section, dict) and section.get("point") is not None for section in sections
        )
        if not guides and not has_point and start_tangent == end_tangent == "none":
            return super().loft(args)
        before = self.snapshot()
        is_cut = bool(args.get("cut", False))
        if is_cut and self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法放样切除")
        if not isinstance(sections, list) or len(sections) < 2:
            raise WorkflowError("TOOL_ARGS", "sections 至少两段，每段 {plane, loops} 或 {plane, point}")
        close = {"operation": "close_sketch", "route": "/api/close-sketch-named", "params": {}}
        operations: list[dict] = []
        warnings: list[str] = []
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise WorkflowError("TOOL_ARGS", f"sections[{index}] 必须是对象")
            frame = self._frame(section.get("plane"), before)
            if section.get("point") is not None:
                if index not in (0, len(sections) - 1):
                    raise WorkflowError("TOOL_ARGS", "点截面只能是第一个或最后一个截面")
                u, v = self._uv_pair(section["point"], f"sections[{index}].point")
                operations += [self._open_sketch(frame), {
                    "operation": "sketch_point", "route": "/api/sketch/point", "params": frame.point_params(u, v),
                }, close]
                continue
            loops, _check, extra = self._profile(section, frame, removing=is_cut)
            warnings.extend(extra)
            operations += [self._open_sketch(frame), *draw_operations(frame, loops), close]
        for index, guide in enumerate(guides):
            guide = coerce_args(guide) if isinstance(guide, dict) else guide
            if not isinstance(guide, dict):
                raise WorkflowError("TOOL_ARGS", f"guides[{index}] 必须是对象")
            if guide.get("curve") is not None:
                operations.append(path3d_operation(self._spline_path(guide, f"guides[{index}]")["points"], spline=True))
                continue
            if guide.get("points") is not None:
                points = self._resolve_numbers(guide["points"])
                if guide.get("spline"):
                    points = [_point3(p, f"guides[{index}].points[i]") for p in points or []]
                    if len(points) < 2:
                        raise WorkflowError("TOOL_ARGS", f"guides[{index}] 样条至少需要 2 个点")
                    operations.append(path3d_operation(points, spline=True))
                else:
                    line = polyline_geometry({
                        "points": points, "corner_radius": self._resolve_numbers(guide.get("corner_radius")),
                        "corner_radii": self._resolve_numbers(guide.get("corner_radii")),
                    })
                    operations.append(path3d_operation(
                        line["points"], corner_radius=line["corner_radius"], corner_radii=line["corner_radii"]))
                continue
            plane = guide.get("plane")
            frame = self._frame(self._axis_plane(plane) if isinstance(plane, str) else plane, before,
                                arg=f"guides[{index}].plane")
            steps = self._resolve_numbers(guide.get("segments"))
            if not isinstance(steps, list) or not steps:
                raise WorkflowError("TOOL_ARGS", f"guides[{index}] 需要 segments（或 3D points）")
            start = self._uv_pair(guide.get("start"), f"guides[{index}].start")
            operations += [self._open_sketch(frame), *draw_open_path(frame, start, steps), close]
        params = {
            "sectionCount": len(sections), "cut": is_cut, "guideCount": len(guides),
            "startTangent": tangents[start_tangent], "endTangent": tangents[end_tangent],
            "tangentLength": float(self._num(args, "tangent_length") or 1.0),
        }
        context = {"sections": len(sections), "guides": len(guides), "tangency": f"{start_tangent}/{end_tangent}"}
        span = self._loft_span([self._frame(section.get("plane"), before) for section in sections])
        # 相切方向的正负随截面朝向而变：逐个尝试，取不越过首尾截面的那个
        flips = [(True, True), (False, False), (True, False), (False, True)]
        if start_tangent == "none":
            flips = [(False, end) for end in (True, False)]
        elif end_tangent == "none":
            flips = [(start, False) for start in (True, False)]
        failure: WorkflowError | None = None
        for reverse_start, reverse_end in flips:
            attempt = [*operations, {"operation": "feature_loft", "route": "/api/feature/loft", "params": {
                **params, "reverseStart": reverse_start, "reverseEnd": reverse_end}}]
            try:
                result = self._transaction(
                    attempt, expect="decrease" if is_cut else "increase", before=before, context=context,
                )
            except WorkflowError as exc:
                failure = exc
                if params["startTangent"] == params["endTangent"] == 0:
                    raise
                continue
            overshoot = self._loft_overshoot(span, before, result) if not is_cut else None
            if overshoot is None:
                return {**result, "warnings": warnings} if warnings else result
            self._rollback(result.get("created") or [])
            failure = WorkflowError("FEATURE_FAILED", overshoot, **context)
        raise failure  # type: ignore[misc]

    @staticmethod
    def _loft_span(frames: list[SketchFrame]) -> tuple[int, float, float] | None:
        axes = {frame.axis for frame in frames}
        if len(axes) != 1:
            return None
        offsets = [frame.offset_mm for frame in frames]
        return AXES.index(frames[0].axis), min(offsets), max(offsets)

    def _loft_overshoot(self, span: tuple[int, float, float] | None, before: dict, result: dict) -> str | None:
        """首尾相切方向反了时，实体会凸出到首/尾截面之外。"""
        if span is None:
            return None
        axis, low, high = span
        box = self._bbox(self.last_transaction[1])
        if box is None:
            return None
        old = self._bbox(before) if self._volume(before) > 0 else None
        if old is not None:
            low, high = min(low, old[AXES[axis]][0]), max(high, old[AXES[axis]][1])
        tolerance = 0.1 + 0.005 * (high - low)
        lo, hi = box[AXES[axis]]
        if lo < low - tolerance or hi > high + tolerance:
            return (f"放样首尾相切后实体沿 {AXES[axis]} 越出截面范围 [{low:g}, {high:g}]（实际 [{lo:g}, {hi:g}]），"
                    "相切方向不对")
        return None

    def _auto_sweep_profile(self, args: dict) -> dict:
        """截面平面放在路径起点、垂直于起点切向；loops 从相对路径起点的坐标平移到该平面的 (u,v)。"""
        path_frame = self._frame(args.get("path_plane"), self.snapshot(), arg="path_plane")
        start = tuple(float(v) for v in self._resolve_numbers(self._uv_pair(args.get("path_start"), "path_start")))
        steps = args.get("path_segments")
        if not isinstance(steps, list) or not steps or not isinstance(steps[0], dict):
            raise WorkflowError("TOOL_ARGS", "path_segments 不能为空")
        tu, tv = path_start_tangent(start, self._resolve_numbers(steps[0]))
        tangent = [0.0, 0.0, 0.0]
        tangent[AXES.index(path_frame.u_axis)] = tu
        tangent[AXES.index(path_frame.v_axis)] = tv
        norm = math.sqrt(sum(value * value for value in tangent))
        axis = max(range(3), key=lambda i: abs(tangent[i]))
        if norm == 0 or abs(tangent[axis]) / norm < 0.999:
            raise WorkflowError(
                "TOOL_ARGS", "路径起点的切向不沿坐标轴，无法自动放置截面",
                hint="给出 profile_plane（截面画在路径起点、垂直于切向），或让路径的第一段沿坐标轴。",
            )
        point = path_frame.to_model_mm(*start)
        name = AXES[axis]
        plane = self._axis_plane(f"{name}={f'{point[axis]:.6f}'.rstrip('0').rstrip('.')}")
        u_axis, v_axis = UV_AXES[name]
        du, dv = point[AXES.index(u_axis)], point[AXES.index(v_axis)]
        loops = args.get("loops")
        if isinstance(loops, list):
            loops = [translate_loop(loop, du, dv) if isinstance(loop, dict) else loop for loop in loops]
        self._notes.append(
            f"截面平面自动取 {name}={point[axis]:g}（垂直于路径起点切向），截面原点 = 路径起点 "
            f"({', '.join(f'{v:g}' for v in point)})，u={u_axis}, v={v_axis}"
        )
        return {**args, "profile_plane": plane, "loops": loops}

    # ------------------------------------------------------------ 圆角、阵列

    def _edge_feature(self, args: dict, *, kind: str) -> dict:
        before = self.snapshot()
        size_key = "radius" if kind == "fillet" else "distance"
        size = self._num(args, size_key, required=True)
        edges = args.get("edges")
        selections = edges if isinstance(edges, list) else [edges]
        if not selections:
            raise WorkflowError("TOOL_ARGS", "edges 数组不能为空")
        propagate = args.get("propagate", True)
        propagate = str(propagate).strip().lower() != "false" if isinstance(propagate, str) else bool(propagate)
        base = ({"radius": size / 1000, **({} if propagate else {"propagate": False})} if kind == "fillet"
                else {"type": "equal_distance", "distance1": size / 1000})
        route = "/api/feature/fillet" if kind == "fillet" else "/api/feature/chamfer"
        params = [self._edge_params(item) for item in selections]
        if len(params) > 1 and all(p["edgeSelection"] == "ray" for p in params):
            # 多条棱放进同一个特征：逐条倒圆时，后倒的棱会顺着先倒圆角的端部相切传播到别的棱上
            rays = [[p[k] for k in ("rx", "ry", "rz", "dx", "dy", "dz")] for p in params]
            operations = [{"operation": f"feature_{kind}", "route": route,
                           "params": {**base, "edgeSelection": "rays", "rays": rays}}]
        else:
            operations = [{"operation": f"feature_{kind}", **({"route": route} if not propagate else {}),
                           "params": {**base, **p}} for p in params]
        ray_warnings = [
            text for index, item in enumerate(selections)
            if isinstance(item, dict) and (text := ray_ambiguity(before, item, index if len(selections) > 1 else None))
        ]
        result = self._transaction(operations, expect="change", before=before, context={})
        signed = float(result.get("volume_delta_mm3") or 0)
        if signed:
            result["edge_kind"] = (
                "convex（外凸棱，体积减少）" if signed < 0 else "concave（内凹棱/交接处，体积增加）")
            result["edge_kind_note"] = (
                "倒圆/倒角外凸棱会减少体积，内凹棱（腹板与轮毂、筋与底板的交线等）会增加体积；"
                "和题面要处理的棱不一致时说明射线选到了相邻的另一条棱。")
        delta = abs(signed)
        section = FILLET_FACTOR * size * size if kind == "fillet" else size * size / 2
        if section > 0:
            result["estimated_edge_length_mm"] = _round(delta / section, 1)
            result["note"] = "estimated_edge_length_mm 按 90° 棱估算被处理的总棱长；与预期差很多说明选中的边不对"
        if ray_warnings:
            result["warnings"] = [*result.get("warnings", []), *ray_warnings]
        return result

    def _edge_params(self, edges: Any) -> dict:
        if isinstance(edges, str) and edges.strip().lower().startswith("face"):
            raise WorkflowError(
                "TOOL_ARGS", f"edges 不支持面引用 {edges!r}",
                hint="倒某个面上的边：每条边给一条射线 {origin, direction}，命中该面、离目标棱约 1mm；多条边写成射线数组。",
            )
        return super()._edge_params(edges)

    def hole(self, args: dict) -> dict:
        """tip_angle 给出时：先打 depth 深的圆柱孔，再从孔底用带拔模角的盲切切出钻尖锥。"""
        tip = self._num(args, "tip_angle")
        if not tip:
            return super().hole({key: value for key, value in args.items() if key != "tip_angle"})
        if not 60 <= tip < 180:
            raise WorkflowError("TOOL_ARGS", f"tip_angle={tip:g} 不合理（麻花钻常用 118°，中心钻 60°）")
        if args.get("through_all") or args.get("up_to"):
            raise WorkflowError("TOOL_ARGS", "通孔没有钻尖；tip_angle 只用于给了 depth 的盲孔")
        depth = self._num(args, "depth", required=True)
        diameter = self._num(args, "diameter", required=True)
        centers = self._resolve_numbers(args.get("centers"))
        if not isinstance(centers, list) or not centers:
            raise WorkflowError("TOOL_ARGS", "centers 必须是非空的 [[u,v], ...]（或给 polar）")
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法打孔")
        frame = self._frame(args.get("plane"), before)
        mode, sign = self._cut_direction(frame, args.get("direction"), before)
        if mode == "both":
            raise WorkflowError("TOOL_ARGS", "带钻尖的盲孔不支持 direction=both")
        direction = f"{'+' if sign > 0 else '-'}{frame.axis}"
        body = super().hole({**{k: v for k, v in args.items() if k != "tip_angle"}, "direction": direction})
        half = math.radians(tip / 2)
        radius = diameter / 2
        # 锥尖留 0.01mm 的小平面，避免拔模切除收成一点时失败
        cone_depth = (radius - 0.005) / math.tan(half)
        bottom = frame.offset_mm + sign * depth
        bottom_text = f"{bottom:.6f}".rstrip("0").rstrip(".")
        try:
            plane = self._axis_plane(f"{frame.axis}={bottom_text}")
            snapshot = self.snapshot()
            bottom_frame = self._frame(plane, snapshot)
            loops, _check, _warnings = self._profile(
                {"loops": [{"type": "circle", "center": c, "diameter": diameter} for c in centers]},
                bottom_frame, removing=True)
            feature, _actual = self._cut_feature(
                bottom_frame, {"direction": direction, "depth": cone_depth, "through_all": False}, snapshot)
            feature = {**feature, "route": "/api/feature/cut-extrusion",
                       "params": {**feature["params"], "draftAngleDeg": tip / 2}}
            cone = self._transaction(
                [self._open_sketch(bottom_frame), *draw_operations(bottom_frame, loops), feature],
                expect="decrease", before=snapshot, context={})
        except WorkflowError as exc:
            self._rollback(body.get("created") or [])
            exc.context["hint"] = "圆柱孔部分已回滚，失败的是孔底钻尖锥；可以先不给 tip_angle 打平底孔"
            raise
        after = self.snapshot()
        return {
            **body,
            "created": (body.get("created") or []) + (cone.get("created") or []),
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": self._bbox(after),
            "drill_point": (
                f"孔底 {frame.axis}={bottom_text} 处加 {tip:g}° 钻尖锥，锥高 {_round(radius / math.tan(half), 3)} mm，"
                f"孔总深（含钻尖）{_round(depth + radius / math.tan(half), 3)} mm"
            ),
        }

    # ------------------------------------------------------------ 任意轴线圆柱

    def _axis_direction(self, raw: Any, origin: list[float]) -> list[float]:
        raw = self._resolve_numbers(coerce_args(raw) if isinstance(raw, dict) else raw)
        if isinstance(raw, str):
            axis, sign = _axis_spec(raw, "direction")
            vector = [sign * value for value in _AXIS_VECTORS[axis]]
        elif isinstance(raw, dict) and raw.get("toward") is not None:
            target = _point3(raw["toward"], "direction.toward")
            vector = [target[k] - origin[k] for k in range(3)]
        elif isinstance(raw, dict) and raw.get("lat_deg") is not None and raw.get("lon_deg") is not None:
            lat, lon = math.radians(float(raw["lat_deg"])), math.radians(float(raw["lon_deg"]))
            vector = [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)]
        elif isinstance(raw, (list, tuple)) and len(raw) == 3:
            vector = [float(value) for value in raw]
        else:
            raise WorkflowError("TOOL_ARGS", "direction 应为 [dx,dy,dz]、{lat_deg, lon_deg} 或 {toward:[x,y,z]}")
        norm = math.sqrt(sum(value * value for value in vector))
        if norm < 1e-9:
            raise WorkflowError("TOOL_ARGS", "direction 不能为零向量")
        return [value / norm for value in vector]

    def _axis_distance(self, raw: Any, origin: list[float], unit: list[float], what: str) -> float:
        raw = self._resolve_numbers(coerce_args(raw) if isinstance(raw, dict) else raw)
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, dict) and raw.get("point") is not None:
            point = _point3(raw["point"], f"{what}.point")
            offset = [point[k] - origin[k] for k in range(3)]
            s = sum(offset[k] * unit[k] for k in range(3))
            miss = math.dist(point, [origin[k] + s * unit[k] for k in range(3)])
            if miss > 0.05:
                raise WorkflowError("TOOL_ARGS", f"{what}.point 离轴线 {miss:.3f} mm，不在轴线上")
            return s
        if isinstance(raw, dict):
            keys = [key for key in raw if key.upper() in AXES]
            if len(keys) == 1:
                k = AXES.index(keys[0].upper())
                if abs(unit[k]) < 1e-6:
                    raise WorkflowError("TOOL_ARGS", f"轴线与 {keys[0].upper()} 平面平行，{what} 不能写成 {{{keys[0]}: …}}")
                return (float(raw[keys[0]]) - origin[k]) / unit[k]
        raise WorkflowError("TOOL_ARGS", f"{what} 应为距离（mm）、{{z: 值}} 或 {{point:[x,y,z]}}")

    def axis_cylinder(self, args: dict) -> dict:
        origin = _point3(self._resolve_numbers(args.get("origin")), "origin")
        unit = self._axis_direction(args.get("direction"), origin)
        start = self._axis_distance(args.get("from", 0), origin, unit, "from")
        end = self._axis_distance(args.get("to"), origin, unit, "to") if args.get("to") is not None else None
        if end is None:
            raise WorkflowError("TOOL_ARGS", "缺少 to（圆柱终点沿轴线的位置）")
        if end - start < 1e-3:
            raise WorkflowError(
                "TOOL_ARGS", f"沿 direction 的范围 from={start:g} → to={end:g} 为空：to 必须大于 from",
                hint="direction 应从 from 端指向 to 端；钻孔时 direction 指向材料内部。")
        diameter = self._num(args, "diameter", required=True)
        cut = bool(args.get("cut", False))
        tip = self._num(args, "tip_angle")
        if tip and not cut:
            raise WorkflowError("TOOL_ARGS", "tip_angle 只用于 cut=true 的盲孔")
        if tip and not 60 <= tip < 180:
            raise WorkflowError("TOOL_ARGS", f"tip_angle={tip:g} 不合理（麻花钻常用 118°）")
        p0 = [_round(origin[k] + start * unit[k], 6) for k in range(3)]
        p1 = [_round(origin[k] + end * unit[k], 6) for k in range(3)]
        sweep_args: dict[str, Any] = {"path_3d": {"points": [p0, p1]}, "profile_diameter": diameter, "cut": cut}
        if args.get("inner_diameter") is not None:
            if cut:
                raise WorkflowError("TOOL_ARGS", "inner_diameter 只用于凸台（空心管）")
            sweep_args["inner_diameter"] = self._num(args, "inner_diameter")
        before = self.snapshot()
        result = self.sweep(sweep_args)
        lat = math.degrees(math.asin(max(-1.0, min(1.0, unit[2]))))
        lon = math.degrees(math.atan2(unit[1], unit[0])) % 360 if math.hypot(unit[0], unit[1]) > 1e-9 else 0.0
        result["axis"] = {
            "start": p0, "end": p1, "direction": [_round(value, 5) for value in unit],
            "lat_deg": _round(lat, 3), "lon_deg": _round(lon, 3), "length_mm": _round(end - start, 4),
        }
        if not cut and self._volume(before) > 0:
            exposed = self._exposed_start_face(before, self.snapshot(), p0, unit, diameter / 2)
            if exposed is not None:
                result["warnings"] = [*result.get("warnings", []), (
                    f"凸台起点截面（{p0} 处）有一部分露在已有实体外（残留起点平面 face#{exposed}）："
                    "起点没有完全埋进实体，凸台与壳体之间会留缝或台阶。"
                    "把 from 往实体内部移，让整个起点截面都在材料里；内腔已经切好时，"
                    "可删掉内腔切除，先做接管再重新切内腔。")]
        if not tip:
            return result
        try:
            cone = self._axis_cone(p1, unit, diameter / 2, tip)
        except WorkflowError as exc:
            self._rollback(result.get("created") or [])
            exc.context["hint"] = "圆柱孔部分已回滚，失败的是孔底钻尖锥；可以先不给 tip_angle 打平底孔"
            raise
        if not cone:
            return result
        after = self.snapshot()
        height = diameter / 2 / math.tan(math.radians(tip / 2))
        apex = [_round(p1[k] + height * unit[k], 4) for k in range(3)]
        result.update({
            "created": (result.get("created") or []) + cone,
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": self._bbox(after),
            "drill_point": f"{tip:g}° 钻尖锥接在 {p1} 之后，锥高 {_round(height, 3)} mm，锥尖 {apex}",
        })
        return result

    @staticmethod
    def _exposed_start_face(before: dict, after: dict, point: list[float], unit: list[float],
                            radius: float) -> int | None:
        """凸台起点平面上露出的端面（法向朝 −unit、过 point、在圆柱截面内）；起点本来就在已有平面上时不算。"""

        def on_start_plane(face: dict, facing: bool) -> bool:
            if face.get("surfaceType") != "plane":
                return False
            normal = _vec(face.get("normal"))
            norm = math.sqrt(sum(v * v for v in normal)) or 1.0
            alignment = sum(normal[k] * unit[k] for k in range(3)) / norm
            if (alignment > -0.999) if facing else (abs(alignment) < 0.999):
                return False
            center = [value * 1000 for value in _vec(face.get("center"))]
            offset = [center[k] - point[k] for k in range(3)]
            return abs(sum(offset[k] * unit[k] for k in range(3))) <= 0.05

        if any(on_start_plane(face, False) for face in before.get("faces") or []):
            return None
        for face in after.get("faces") or []:
            if not on_start_plane(face, True):
                continue
            center = [value * 1000 for value in _vec(face.get("center"))]
            offset = [center[k] - point[k] for k in range(3)]
            along = sum(offset[k] * unit[k] for k in range(3))
            if math.sqrt(max(0.0, sum(v * v for v in offset) - along * along)) <= radius + 1e-3:
                return int(face.get("index"))
        return None

    @staticmethod
    def _hole_bottom_face(snapshot: dict, point: list[float], unit: list[float]) -> int | None:
        """扫描切除留下的孔底：中心在 point、法向与轴线平行的平面。"""
        best, best_distance = None, 0.05
        for face in snapshot.get("faces") or []:
            if face.get("surfaceType") != "plane":
                continue
            normal = _vec(face.get("normal"))
            norm = math.sqrt(sum(v * v for v in normal)) or 1.0
            if abs(sum(normal[k] * unit[k] for k in range(3))) / norm < 0.999:
                continue
            center = [value * 1000 for value in _vec(face.get("center"))]
            distance = math.dist(center, point)
            if distance < best_distance:
                best, best_distance = int(face.get("index")), distance
        return best

    def _axis_cone(self, point: list[float], unit: list[float], radius: float, tip: float) -> list[str]:
        """在扫描切除留下的孔底平面上画半径 radius 的圆，带拔模角盲切出钻尖锥；返回新建特征名。"""
        face = self._hole_bottom_face(self.snapshot(), point, unit)
        if face is None:
            self._notes.append("孔底没有留下平面（可能已落在别的孔腔里），没有生成钻尖")
            return []
        open_sketch = {"operation": "select_observed_face_and_sketch", "route": "/api/select-observed-face-and-sketch",
                       "params": {"faceIndex": face, "exact": True}}
        helper = [1.0, 0.0, 0.0] if abs(unit[0]) < 0.9 else [0.0, 1.0, 0.0]
        e1 = _cross(unit, helper)
        n1 = math.sqrt(sum(v * v for v in e1))
        e1 = [v / n1 for v in e1]
        e2 = list(_cross(unit, e1))

        def at(a: float, b: float, keys: tuple[str, str, str]) -> dict:
            return {keys[i]: (point[i] + radius * (a * e1[i] + b * e2[i])) / 1000 for i in range(3)}

        segments = [
            {"type": "arc", **at(1, 0, ("x1", "y1", "z1")), **at(-1, 0, ("x2", "y2", "z2")), **at(0, 1, ("xm", "ym", "zm"))},
            {"type": "arc", **at(-1, 0, ("x1", "y1", "z1")), **at(1, 0, ("x2", "y2", "z2")), **at(0, -1, ("xm", "ym", "zm"))},
        ]
        depth = (radius - 0.005) / math.tan(math.radians(tip / 2))
        expected = math.pi * radius * radius * radius / math.tan(math.radians(tip / 2)) / 3
        last: WorkflowError | None = None
        for flip in (False, True):
            before = self.snapshot()
            operations = [
                open_sketch,
                {"operation": "sketch_profile", "route": PROFILE_ROUTE, "params": {"segments": segments, "closed": True}},
                {"operation": "feature_cut_extrusion", "route": "/api/feature/cut-extrusion",
                 "params": {"depth": depth / 1000, "endCondition": "blind", "flipDirection": flip,
                            "draftAngleDeg": tip / 2}},
            ]
            try:
                cone = self._transaction(operations, expect="decrease", before=before, context={})
            except WorkflowError as exc:
                last = exc
                continue
            removed = -float(cone.get("volume_delta_mm3") or 0)
            if removed <= expected * 1.3:
                return list(cone.get("created") or [])
            self._rollback(cone.get("created") or [])
            last = WorkflowError("FEATURE_FAILED", f"钻尖锥去除 {removed:g} mm³，远大于理论值 {expected:.2f} mm³")
        if last is not None and last.code == "FEATURE_FAILED" and "远大于" not in str(last):
            self._notes.append("钻尖锥两个方向都没有切到材料（孔底可能已落在别的孔腔里），没有生成钻尖")
            return []
        raise last or WorkflowError("FEATURE_FAILED", "钻尖锥切除失败")

    def countersink(self, args: dict) -> dict:
        """锥形沉头：带拔模角的盲切切出 head_diameter → through_diameter 的锥台，再打通孔。"""
        centers = self._resolve_numbers(args.get("centers"))
        if not isinstance(centers, list) or not centers:
            raise WorkflowError("TOOL_ARGS", "centers 必须是非空的 [[u,v], ...]（或给 polar）")
        through_d = self._num(args, "through_diameter", required=True)
        head_d = self._num(args, "head_diameter", required=True)
        if head_d <= through_d:
            raise WorkflowError("TOOL_ARGS", "head_diameter 必须大于 through_diameter")
        angle = self._num(args, "angle_deg")
        head_depth = self._num(args, "head_depth")
        if angle is None and head_depth:
            angle = 2 * math.degrees(math.atan((head_d - through_d) / 2 / head_depth))
            self._notes.append(f"由 head_depth={head_depth:g} 推出锥角 {angle:.2f}°（未给 angle_deg）")
        angle = 90.0 if angle is None else angle
        if not 20 <= angle < 179:
            raise WorkflowError("TOOL_ARGS", f"angle_deg={angle:g} 不合理（常用 90° 或 82°/100°/120°）")
        cone_depth = (head_d - through_d) / 2 / math.tan(math.radians(angle / 2))
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法打孔")
        frame = self._frame(args.get("plane"), before)
        if str(args.get("direction") or "").strip().lower() == "both":
            raise WorkflowError("TOOL_ARGS", "countersink 不支持 direction=both")
        loops, check, _warnings = self._profile(
            {"loops": [{"type": "circle", "center": c, "diameter": head_d} for c in centers]}, frame, removing=True)
        feature, actual = self._cut_feature(
            frame, {"direction": args.get("direction"), "depth": cone_depth, "through_all": False}, before)
        if feature.get("operation") != "feature_cut_extrusion" or feature["params"].get("endCondition") != "blind":
            raise WorkflowError("TOOL_ARGS", "锥形沉头只能在实体表面向内切；请在零件表面上给 plane")
        feature = {**feature, "route": "/api/feature/cut-extrusion",
                   "params": {**feature["params"], "draftAngleDeg": angle / 2}}
        expected = len(centers) * math.pi * cone_depth / 12 * (head_d ** 2 + head_d * through_d + through_d ** 2)
        cone = self._transaction(
            [self._open_sketch(frame), *draw_operations(frame, loops), feature], expect="decrease", before=before,
            context={"plane": frame.describe(), "direction": actual},
        )
        removed = -float(cone.get("volume_delta_mm3") or 0)
        warnings = []
        if removed > expected * 1.15:
            warnings.append(f"锥面去除 {removed:g} mm³，大于 {len(centers)} 个锥台的理论值 {expected:.2f} mm³，锥面可能朝外扩")
        shared = {key: args[key] for key in ("direction", "through_all", "depth") if key in args}
        try:
            through = self.cut({**shared, "plane": args.get("plane"),
                                "loops": [{"type": "circle", "center": c, "diameter": through_d} for c in centers]})
        except WorkflowError as exc:
            self._rollback(cone.get("created") or [])
            exc.context["hint"] = (
                f"锥形沉头已成功并已回滚，失败的是直径 {through_d:g} 的通孔部分。给 depth 或 through_all=true；"
                "如果这个位置已经有同径的孔，只需单独做锥面。"
            )
            raise
        after = self.snapshot()
        result = {
            "kind": "countersink",
            "created": (cone.get("created") or []) + (through.get("created") or []),
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": self._bbox(after),
            "plane": frame.describe(),
            "angle_deg": _round(angle, 2),
            "cone_depth_mm": _round(cone_depth, 3),
            "cone_removed_mm3": _round(removed, 2),
            "cone_expected_mm3": _round(expected, 2),
        }
        return {**result, "warnings": warnings} if warnings else result

    def fillet(self, args: dict) -> dict:
        return self._edge_feature(args, kind="fillet")

    def chamfer(self, args: dict) -> dict:
        return self._edge_feature(args, kind="chamfer")

    def _seed_delta(self) -> float | None:
        for entry in reversed(self.log):
            result = entry.get("result") or {}
            if not result.get("ok"):
                continue
            if entry.get("tool") in ("circular_pattern", "linear_pattern", "mirror", "get_model_state",
                                     "set_parameters", "ref_plane", "delete_feature", "edit_feature"):
                return None
            value = result.get("volume_delta_mm3")
            return float(value) if isinstance(value, (int, float)) else None
        return None

    def _check_pattern(self, result: dict, copies: int) -> dict:
        seed = self._seed_delta()
        if seed is None or abs(seed) <= VOLUME_EPS_MM3:
            return result
        expected = seed * copies
        actual = float(result.get("volume_delta_mm3") or 0)
        result["expected_delta_mm3"] = _round(expected, 2)
        if abs(actual - expected) > PATTERN_TOLERANCE * abs(expected):
            result.setdefault("warnings", []).append(
                f"阵列体积变化 {actual:.1f}mm³，按种子特征（{seed:.1f}mm³）×{copies} 应约为 {expected:.1f}mm³："
                "可能有实例与已有几何重叠、落在实体之外，或阵列轴/方向不对。请核对 new_cylinders 或 get_model_state。"
            )
        return result

    def _axis_through(self, axis: str, center: list[float]) -> str:
        """平行于 axis、穿过 center 的参考轴：另外两根轴上各取一个平面求交线。"""
        others = [key for key in AXES if key != axis]
        key = (axis, *(_round(float(center[AXES.index(other)]), 4) for other in others))
        remaining = {feature.get("name") for feature in self.snapshot().get("features", [])}
        if key in self._axes and self._axes[key] in remaining:
            return self._axes[key]
        planes = []
        for other in others:
            name = self._axis_plane(f"{other}={float(center[AXES.index(other)]):g}")
            planes.append(self._frame(name, {}).sw_reference)
        data = self._request("POST", "/api/ref-axis/planes", {"first": planes[0], "second": planes[1]})
        if not data.get("success"):
            raise WorkflowError("FEATURE_FAILED", str(data.get("message") or "创建参考轴失败"))
        name = str((data.get("result") or {}).get("featureName") or "")
        self._axes[key] = name
        return name

    def circular_pattern(self, args: dict) -> dict:
        angle = self._num(args, "angle_deg", 360)
        count = int(args.get("count") or 0)
        if count >= 2 and angle < 360 and abs(angle * count - 360) < 1e-6:
            raise WorkflowError(
                "TOOL_ARGS", f"angle_deg={angle:g} 是阵列覆盖的总角度，不是相邻间隔；{count} 份会挤在 {angle:g}° 内",
                hint=f"整圈均分 {count} 份请省略 angle_deg 或写 360。",
            )
        center = self._resolve_numbers(args.get("center"))
        if not center:
            result = super().circular_pattern(args)
            return self._check_pattern(result, int(args.get("count") or 0) - 1)
        count = int(args.get("count") or 0)
        if count < 2:
            raise WorkflowError("TOOL_ARGS", "count 至少为 2")
        if not isinstance(center, list) or len(center) != 3:
            raise WorkflowError("TOOL_ARGS", "center 必须是 [x,y,z] mm")
        axis, _ = _axis_sign(args.get("axis", "Y"))
        axis_name = self._axis_through(axis, [float(value) for value in center])
        before = self.snapshot()
        operation = {
            "operation": "feature_circular_pattern", "route": "/api/feature/circular-pattern",
            "params": {"count": count, "axis": axis.lower(), "angleDeg": self._num(args, "angle_deg", 360),
                       "axisFeature": axis_name},
        }
        result = self._transaction(
            [operation], expect="change", before=before,
            context={"axis": f"平行于 {axis}、穿过 {[float(v) for v in center]} 的 {axis_name}"},
        )
        return self._check_pattern(result, count - 1)

    def linear_pattern(self, args: dict) -> dict:
        result = super().linear_pattern(args)
        count1 = max(int(args.get("count1") or 1), 1)
        count2 = max(int(args.get("count2") or 1), 1)
        return self._check_pattern(result, count1 * count2 - 1)

    # ------------------------------------------------------------ 基准面、编辑、删除

    def ref_plane(self, args: dict) -> dict:
        before = self.snapshot()
        base = self._frame(args.get("reference"), before, arg="reference")
        offset = self._num(args, "offset", required=True)
        target = base.offset_mm + base.normal_sign * offset
        sign = base.normal_sign
        existing = self._find_ref_plane(base.axis, target, sign)
        if existing:
            frame = self.ref_planes[existing]
            return {"name": existing, "plane": frame.describe(), "from": base.label, "reused": True}
        return super().ref_plane(args)

    def edit_feature(self, args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        if name in self.ref_planes:
            raise WorkflowError(
                "TOOL_ARGS", f"{name} 是基准面，不能用 edit_feature 修改",
                hint="需要别的位置时直接在 plane 里写 \"Z=…\"（或再建一个 ref_plane）。",
            )
        return super().edit_feature(args)

    def delete_feature(self, args: dict) -> dict:
        names = args.get("names")
        if names is None:
            names = [args.get("name")]
        if not isinstance(names, list) or not names or not all(str(n or "").strip() for n in names):
            raise WorkflowError("TOOL_ARGS", "给出 name 或非空的 names")
        names = [str(name).strip() for name in names]
        before = self.snapshot()
        before_user = [item["name"] for item in self._user_features(before)]
        missing = [name for name in names if name not in before_user]
        if missing:
            raise WorkflowError("TOOL_ARGS", f"找不到特征 {missing}", hint=f"可用特征: {before_user}")
        deleted, skipped = [], []
        last: dict = {}
        for name in names:
            current = {feature.get("name") for feature in self.snapshot().get("features", [])}
            if name not in current:
                skipped.append(name)
                continue
            last = super().delete_feature({"name": name})
            deleted.append(name)
        after = self.snapshot()
        remaining = {feature.get("name") for feature in after.get("features", [])}
        also_removed = [name for name in before_user if name not in remaining and name not in deleted]
        result = {
            "deleted": deleted,
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": last.get("bbox_mm", self._bbox(after)),
            "remaining_features": [item["name"] for item in self._user_features(after)],
        }
        if also_removed:
            result["also_removed"] = also_removed
            result["warnings"] = [
                f"依赖被删特征的 {also_removed} 也被一并删除了；需要的话请重新建出这些特征。"
            ]
        return result

    # ------------------------------------------------------------ 状态

    def state_summary(self, snapshot: dict | None = None, filters: dict | None = None) -> dict:
        snapshot = snapshot or self.snapshot()
        filters = filters or {}
        faces = snapshot.get("faces") or []
        kind = str(filters.get("type") or "").lower()
        axis = str(filters.get("axis") or "").upper()
        diameter = filters.get("diameter")
        near = filters.get("near")
        radius = float(filters.get("radius") or 10)
        if near is not None:
            if not isinstance(near, (list, tuple)) or len(near) != 3:
                raise WorkflowError("TOOL_ARGS", "near 必须是 [x,y,z] mm")
            near = [float(value) for value in near]

        def close(face: dict) -> bool:
            if near is None:
                return True
            box = face.get("box") or {}
            if not box:
                return False
            # 点到面包围盒的距离
            gaps = []
            for i, key in enumerate(AXES):
                lo, hi = float(box[f"min{key}"]) * 1000, float(box[f"max{key}"]) * 1000
                gaps.append(max(lo - near[i], 0.0, near[i] - hi))
            return math.sqrt(sum(g * g for g in gaps)) <= radius

        state: dict = {
            "features": self._user_features(snapshot),
            "bbox_mm": self._bbox(snapshot),
            "volume_mm3": _round(self._volume(snapshot), 2),
        }
        bodies = self._bodies(snapshot)
        if bodies is not None:
            state["bodies"] = bodies
            if snapshot.get("bodies") and bodies > 1:
                state["body_list"] = snapshot.get("bodies")

        planar_all = self._planar_summaries(faces)
        if kind in ("", "plane"):
            planar = [item for item in planar_all if close(item["_face"])]
            if axis:
                planar = [item for item in planar if item["normal"][1:] == axis]
            state["planar_faces"] = [
                {key: value for key, value in item.items() if key != "_face"} for item in planar[:_STATE_PLANAR_LIMIT]
            ]
            if len(planar) > _STATE_PLANAR_LIMIT:
                state["planar_faces_omitted"] = len(planar) - _STATE_PLANAR_LIMIT
        if kind in ("", "cylinder"):
            groups = self._cylinder_summary(faces, axis=axis, diameter=diameter, close=close)
            state["cylinder_groups"] = groups[:_STATE_CYLINDER_LIMIT]
            if len(groups) > _STATE_CYLINDER_LIMIT:
                state["cylinder_groups_omitted"] = len(groups) - _STATE_CYLINDER_LIMIT
        state["reference_planes"] = [
            {
                "name": name,
                "normal": f"{'+' if frame.normal_sign > 0 else '-'}{frame.axis}",
                "at": f"{frame.axis}={frame.offset_mm:.3f}",
                "uv": f"u={frame.u_axis},v={frame.v_axis}",
            }
            for name, frame in self.ref_planes.items()
        ]
        state["parameters"] = {name: _round(value, 6) for name, value in self.params.resolve().items()}
        limit = snapshot.get("faceLimit") or 200
        if len(faces) >= int(limit):
            state["faces_note"] = f"零件的面多于观测上限 {limit}，列表不完整；用 near/axis/diameter 过滤查看局部"
        return state

    @staticmethod
    def _planar_summaries(faces: list[dict]) -> list[dict]:
        aliases: dict[int, str] = {}
        for side in ("top", "bottom", "front", "back", "left", "right"):
            try:
                aliases[resolve_ref(side, faces).face_index] = side
            except WorkflowError:
                pass
        planar = []
        for face in faces:
            if face.get("surfaceType") != "plane":
                continue
            try:
                frame = face_frame(face, f"face#{face.get('index')}")
            except WorkflowError:
                continue
            box = {key: float(value) * 1000 for key, value in (face.get("box") or {}).items()}
            item = {
                "ref": f"face#{face.get('index')}",
                "normal": f"{'+' if frame.normal_sign > 0 else '-'}{frame.axis}",
                "at": f"{frame.axis}={frame.offset_mm:.3f}",
                "area_mm2": _round(float(face.get("area") or 0) * 1e6, 2),
                "_face": face,
            }
            if box:
                item["u_range"] = [_round(box[f"min{frame.u_axis}"]), _round(box[f"max{frame.u_axis}"])]
                item["v_range"] = [_round(box[f"min{frame.v_axis}"]), _round(box[f"max{frame.v_axis}"])]
                item["uv"] = f"u={frame.u_axis},v={frame.v_axis}"
            if int(face.get("index")) in aliases:
                item["alias"] = aliases[int(face.get("index"))]
            planar.append(item)
        planar.sort(key=lambda item: -(item["area_mm2"] or 0))
        return planar

    def _cylinder_summary(self, faces: list[dict], *, axis: str, diameter: Any, close) -> list[dict]:
        groups: dict[tuple, dict] = {}
        for face in faces:
            key = _cylinder_key(face)
            if key is None or not close(face):
                continue
            size, main, point = key
            if axis and main != axis:
                continue
            if diameter is not None and abs(size - float(diameter)) > 0.05:
                continue
            box = {k: float(v) * 1000 for k, v in (face.get("box") or {}).items()}
            span = (_round(box.get(f"min{main}")), _round(box.get(f"max{main}"))) if box else None
            group = groups.setdefault((size, main, span), {
                "diameter_mm": size, "axis": main, "span_mm": list(span) if span else None,
                "positions": {}, "refs": [],
            })
            group["positions"].setdefault(point, f"face#{face.get('index')}")
            group["refs"].append(f"face#{face.get('index')}")
        out = []
        for group in groups.values():
            positions = group.pop("positions")
            group["count"] = len(positions)
            group["axis_at_mm"] = [list(point) for point in positions]
            group["refs"] = sorted(set(group["refs"]), key=lambda ref: int(ref[5:]))[:24]
            out.append(group)
        out.sort(key=lambda g: (-g["diameter_mm"], g["axis"]))
        return out

    def get_model_state(self, args: dict) -> dict:
        return {"state": self.state_summary(filters=args)}


def create_session(version: str = "v1", **kwargs: Any) -> WorkflowSession:
    """version: v1 = L1（tools.WorkflowSession），v2 = L1_v2。"""
    if version == "v1":
        return WorkflowSession(**kwargs)
    if version == "v2":
        return WorkflowSessionV2(**kwargs)
    raise ValueError(f"未知的 workflow 版本 {version}")


WORKFLOW_VERSIONS = ("v1", "v2")


def dumps_result_v2(result: dict, limit: int = 16000) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…(截断)"


__all__ = [
    "TOOL_SPECS_V2", "WORKFLOW_VERSIONS", "WorkflowSessionV2", "coerce_args", "create_session",
    "dumps_result_v2", "hull_loop", "polar_centers", "prepare_loops", "tool_specs_v2",
]
