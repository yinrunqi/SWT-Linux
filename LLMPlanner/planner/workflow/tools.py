"""L1 工程师级建模工具：Agent 每次调用一个工具，拿到真实结果后再决定下一步。

每个特征工具都是“原子事务”：
    参数求值 → 解析平面引用 → 草图预检 → 执行原子操作 → 校验体积变化；
任何一步失败都会删除本次新建的草图/特征，模型回到调用前的状态，
并返回带错误码和修复提示的结果，交给 Agent 自己修正。
"""

from __future__ import annotations

import base64
import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import requests

from ..executor import SwExecutor
from ..gears import generate_internal_cavity, generate_involute_outline
from ..schemas import OperationType, SwOperation
from .errors import WorkflowError
from .frames import BASE_PLANES, SketchFrame, base_plane_frame, face_frame, ref_plane_frame
from .params import ParamTable
from .profiles import centerline_operation, draw_open_path, draw_operations, normalize_loops
from .refs import RefRegistry, resolve_ref
from .sketch_check import check_profile, to_geometry

VOLUME_EPS_MM3 = 1e-3
AXES = ("X", "Y", "Z")
LLMPLANNER_ROOT = Path(__file__).resolve().parents[2]
RENDER_PYTHON = os.getenv("GEOMCHECK_PYTHON") or str(LLMPLANNER_ROOT / "venv-occ" / "Scripts" / "python.exe")
RENDER_TIMEOUT_S = 180
# 工具结果里以 PNG base64 返回的图片；Agent 取出后作为图片消息发送，不写进 tool 文本
IMAGE_KEY = "_image_png_b64"
MAX_PLANAR_FACES = 40
MAX_CYLINDER_FACES = 30

_LOOP_SCHEMA = {
    "type": "object",
    "description": (
        "一个闭合轮廓，坐标为所在平面的 (u, v)，单位 mm。"
        "circle: {center:[u,v], diameter}；rect: {center:[u,v], size:[du,dv], corner_radius?}；"
        "regular_polygon: {center:[u,v], sides, across_flats 或 circumradius, angle_deg, corner_radius?}；"
        "slot: {start:[u,v], end:[u,v], width}（两端真圆弧）；"
        "polygon: {points:[[u,v],...], corner_radius? 或逐顶点 corner_radii?}；"
        "annular_sector: {center:[u,v], r_inner, r_outer, start_deg, end_deg}（扇环，逆时针，r_inner=0 为扇形）；"
        "path: {start:[u,v], segments:[...]}，每段都必须有终点 to:[u,v]；只有 to 为直线，另加 through:[u,v]（圆弧经过的点，"
        "最不易出错，半圆优先用它）、center:[u,v]（可带 ccw）、radius+ccw(+large) 或 tangent:true 之一为圆弧；"
        "ccw 指在 (u,v) 中从当前点绕到 to 的旋转方向；末点不回到起点时自动补直线。"
        "工具结果中的 profile_uv_bbox 是轮廓实际的 (u,v) 范围，可据此核对圆弧凸向。"
    ),
    "properties": {
        "type": {
            "type": "string",
            "enum": ["circle", "rect", "regular_polygon", "slot", "polygon", "annular_sector", "path"],
        },
    },
    "required": ["type"],
}
_PLANE_DESC = (
    "草图平面：基准面 front_plane(XY, 法向+Z) / top_plane(XZ, 法向+Y) / right_plane(YZ, 法向+X)，"
    "ref_plane 新建的基准面名称，"
    "或实体平面引用：top/bottom/front/back/left/right（该侧最外侧的平面）、"
    "face(+Y, outermost|innermost|largest|smallest)、face#<index>（来自 get_model_state）。"
    "(u,v) 规则：法向沿 Z → u=X,v=Y；法向沿 Y → u=X,v=Z；法向沿 X → u=Z,v=Y。"
)
_NUM = {"type": ["number", "string"], "description": "数值（mm/度），或以 = 开头的参数表达式，如 \"=wall*2\""}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOL_SPECS: list[dict] = [
    _tool(
        "get_model_state",
        "读取当前零件的真实状态：用户特征列表、包围盒、体积、平面与圆柱面（含 face#index、法向、位置、(u,v) 范围）、已定义参数。",
        {}, [],
    ),
    _tool(
        "set_parameters",
        "定义或更新驱动参数（mm/度），之后任何数值参数都可以写成 \"=表达式\"。例：{\"L\":80, \"wall\":\"=L/20\"}",
        {"values": {"type": "object", "description": "参数名 → 数值或表达式"}},
        ["values"],
    ),
    _tool(
        "extrude",
        "在平面上画轮廓并拉伸凸台（与已有实体合并）。同一次调用中的外环与其内部的内环构成带孔区域；互相重叠的轮廓会被预检拒绝。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "depth": _NUM,
            "up_to": {
                "type": "string",
                "description": "拉伸到该平面或实体为止（替代 depth）：面引用或 body（到整个实体）。用于侧凸台接到已有主体。",
            },
            "direction": {
                "type": "string",
                "description": "拉伸方向：+X/-X/+Y/-Y/+Z/-Z（须沿平面法向轴）或 both（两侧对称）。默认：基准面沿 +法向，实体面沿外法向。",
            },
            "draft_deg": _NUM,
            "draft_outward": {"type": "boolean"},
        },
        ["plane", "loops"],
    ),
    _tool(
        "cut",
        "在平面上画轮廓并拉伸切除。给 depth（盲切）、through_all=true（贯穿）或 up_to（切到指定面/实体）。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "depth": _NUM,
            "through_all": {"type": "boolean"},
            "up_to": {"type": "string", "description": "切到该平面或 body，替代 depth / through_all"},
            "direction": {
                "type": "string",
                "description": "切除方向：+X/-X/.../both，默认 auto：实体面向实体内部；基准面朝向实体所在一侧。",
            },
        },
        ["plane", "loops"],
    ),
    _tool(
        "hole",
        "在平面上按圆心列表打同直径的圆孔（切除），给 depth 或 through_all=true。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "centers": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "diameter": _NUM,
            "depth": _NUM,
            "through_all": {"type": "boolean"},
            "direction": {"type": "string"},
        },
        ["plane", "centers", "diameter"],
    ),
    _tool(
        "revolve",
        "在平面上画轮廓，绕平面内的一条轴线旋转成形（凸台或切除）。轮廓必须全部位于轴线一侧，可以贴着轴线。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "axis_start": {"type": "array", "items": {"type": "number"}, "description": "轴线起点 [u,v] mm"},
            "axis_end": {"type": "array", "items": {"type": "number"}, "description": "轴线终点 [u,v] mm"},
            "angle_deg": _NUM,
            "cut": {"type": "boolean"},
        },
        ["plane", "loops", "axis_start", "axis_end"],
    ),
    _tool(
        "fillet",
        "对边倒圆角。edges: \"all\"（全部边）、长方体语义边名（如 top_front、top_back、front_left 等），"
        "或射线 {origin:[x,y,z], direction:[dx,dy,dz]}（mm，射线命中的面上离交点最近的边）。",
        {"radius": _NUM, "edges": {"type": ["string", "object"]}},
        ["radius"],
    ),
    _tool(
        "chamfer",
        "对边倒角（等距）。edges 写法同 fillet。",
        {"distance": _NUM, "edges": {"type": ["string", "object"]}},
        ["distance"],
    ),
    _tool(
        "circular_pattern",
        "把最近一个特征绕平行于 axis 的轴圆周阵列 count 份（含原特征）。center 为轴线经过的点 [x,y,z] mm，缺省时自动找唯一的圆柱面。",
        {
            "count": {"type": "integer"},
            "axis": {"type": "string", "enum": list(AXES)},
            "angle_deg": _NUM,
            "center": {"type": "array", "items": {"type": "number"}},
        },
        ["count", "axis"],
    ),
    _tool(
        "linear_pattern",
        "把最近一个特征沿 1~2 个坐标方向线性阵列（含原特征）。direction 如 +X / -Z。",
        {
            "count1": {"type": "integer"}, "spacing1": _NUM, "direction1": {"type": "string"},
            "count2": {"type": "integer"}, "spacing2": _NUM, "direction2": {"type": "string"},
        },
        ["count1", "spacing1", "direction1"],
    ),
    _tool(
        "ref_plane",
        "新建与参考平面平行的基准面，用于在没有现成平面的位置画草图（如悬空的侧凸台、中间层的筋、镜像面）。"
        "offset 沿参考平面的法向（基准面为 +轴向，实体面为外法向），可为负。返回的 name 可作为其它工具的 plane。",
        {
            "reference": {"type": "string", "description": "参考平面：基准面名、已建基准面名或实体平面引用"},
            "offset": _NUM,
        },
        ["reference", "offset"],
    ),
    _tool(
        "mirror",
        "关于一个平面镜像。features 给出要镜像的特征名（来自 get_model_state 的 features）；"
        "或 body=true 镜像整个实体并合并（先做一半、再镜像成对称零件）。",
        {
            "plane": {"type": "string", "description": "镜像平面：基准面名、已建基准面名或实体平面引用"},
            "features": {"type": "array", "items": {"type": "string"}},
            "body": {"type": "boolean"},
        },
        ["plane"],
    ),
    _tool(
        "edit_feature",
        "修改已有特征的尺寸（拉伸深度、筋厚、圆角半径等），不要删除重做。name 来自 get_model_state；value 对长度尺寸是 mm，对角度尺寸是度。",
        {"name": {"type": "string"}, "value": _NUM, "dimension": {"type": "string", "description": "尺寸名，缺省改该特征的第一个尺寸"}},
        ["name", "value"],
    ),
    _tool(
        "rib",
        "在平面上画一条开放线段（连接已有两个面），加厚成筋。thickness 为筋厚 mm。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "start": {"type": "array", "items": {"type": "number"}, "description": "路径起点 [u,v] mm"},
            "end": {"type": "array", "items": {"type": "number"}, "description": "路径终点 [u,v] mm"},
            "thickness": _NUM,
            "both_sides": {"type": "boolean", "description": "默认 true，向草图两侧加厚"},
        },
        ["plane", "start", "end", "thickness"],
    ),
    _tool(
        "counterbore",
        "沉头孔：在平面上按圆心打同规格沉孔。through_diameter 为通孔直径，head_diameter/head_depth 为沉头直径与深度。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "centers": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "through_diameter": _NUM,
            "head_diameter": _NUM,
            "head_depth": _NUM,
            "through_all": {"type": "boolean"},
            "depth": _NUM,
            "direction": {"type": "string"},
        },
        ["plane", "centers", "through_diameter", "head_diameter", "head_depth"],
    ),
    _tool(
        "countersink",
        "沉头锥孔：through_diameter 通孔直径，head_diameter 为锥坑大端直径，head_depth 为锥坑深度。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "centers": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "through_diameter": _NUM,
            "head_diameter": _NUM,
            "head_depth": _NUM,
            "through_all": {"type": "boolean"},
            "depth": _NUM,
            "direction": {"type": "string"},
        },
        ["plane", "centers", "through_diameter", "head_diameter", "head_depth"],
    ),
    _tool(
        "tapped_hole",
        "螺纹底孔（可选装饰螺纹）。diameter 为底孔直径；thread_diameter 为公称直径（给了才加装饰螺纹）。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "centers": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
            "diameter": _NUM,
            "depth": _NUM,
            "through_all": {"type": "boolean"},
            "thread_diameter": _NUM,
            "direction": {"type": "string"},
        },
        ["plane", "centers", "diameter"],
    ),
    _tool(
        "sweep",
        "沿路径扫描截面。先指定路径所在平面和开放路径，再指定截面所在平面和闭合轮廓。"
        "截面必须画在路径起点处、且平面大致垂直于路径切向。",
        {
            "path_plane": {"type": "string", "description": _PLANE_DESC},
            "path_start": {"type": "array", "items": {"type": "number"}},
            "path_segments": {"type": "array", "description": "开放路径，写法同 path 轮廓的 segments"},
            "profile_plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "cut": {"type": "boolean"},
        },
        ["path_plane", "path_start", "path_segments", "profile_plane", "loops"],
    ),
    _tool(
        "loft",
        "在两个或多个截面之间放样。sections 按顺序排列，每个 {plane, loops}。",
        {
            "sections": {"type": "array", "items": {"type": "object"}},
            "cut": {"type": "boolean"},
        },
        ["sections"],
    ),
    _tool(
        "shell",
        "抽壳：去掉 open_faces 上的材料，留下 thickness 壁厚。open_faces 为面引用列表。",
        {
            "thickness": _NUM,
            "open_faces": {"type": "array", "items": {"type": "string"}},
            "outward": {"type": "boolean"},
        },
        ["thickness", "open_faces"],
    ),
    _tool(
        "draft",
        "对已有面拔模。neutral 是中性面，faces 是要倾斜的侧面，angle_deg 为拔模角。",
        {
            "neutral": {"type": "string", "description": _PLANE_DESC},
            "faces": {"type": "array", "items": {"type": "string"}},
            "angle_deg": _NUM,
        },
        ["neutral", "faces", "angle_deg"],
    ),
    _tool(
        "sheet_metal",
        "钣金基体法兰：在空零件上画闭合轮廓并指定板厚。只用于钣金件的第一特征。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "thickness": _NUM,
        },
        ["plane", "loops", "thickness"],
    ),
    _tool(
        "delete_feature",
        "删除一个特征（连同它吸收的草图）。用于撤销做错的一步；后续特征若依赖它也会被删除。",
        {"name": {"type": "string"}},
        ["name"],
    ),
    _tool(
        "finish",
        "确认零件已满足需求后调用，结束建模。调用前应先用 get_model_state 核对尺寸与特征。",
        {"summary": {"type": "string"}},
        ["summary"],
    ),
]
TOOL_NAMES = {spec["function"]["name"] for spec in TOOL_SPECS}

SPUR_GEAR_SPEC = _tool(
    "spur_gear",
    "一次建成渐开线直齿轮：轴线沿草图平面法向（默认 front_plane，即 +Z），"
    "原点在轴线，从 Z=0 向 +Z 拉伸齿宽。中心轴孔与齿形在同一特征里完成。"
    "只在装配零件生成会话中可用；50 题核心工具集不包含它。",
    {
        "module": _NUM,
        "teeth": {"type": "integer", "description": "齿数，≥10"},
        "face_width": _NUM,
        "bore": _NUM,
        "pressure_angle_deg": _NUM,
        "phase_deg": _NUM,
        "plane": {"type": "string", "description": "默认 front_plane（XY，法向 +Z）"},
        "keyway_width": _NUM,
        "keyway_depth": _NUM,
    },
    ["module", "teeth", "face_width", "bore"],
)
INTERNAL_GEAR_SPEC = _tool(
    "internal_gear",
    "一次建成渐开线内齿圈：外圆为环体外径，内孔为齿槽轮廓（齿顶圆 = 分度圆 − 模数）。"
    "轴线沿草图平面法向（默认 front_plane，即 +Z），原点在轴线，从 Z=0 向 +Z 拉伸齿宽。"
    "只在装配零件生成会话中可用；50 题核心工具集不包含它。",
    {
        "module": _NUM,
        "teeth": {"type": "integer", "description": "齿数，≥24"},
        "face_width": _NUM,
        "outer_diameter": {"type": "number", "description": "齿圈外径，须大于齿根圆直径 + 2 倍模数"},
        "pressure_angle_deg": _NUM,
        "phase_deg": _NUM,
        "plane": {"type": "string", "description": "默认 front_plane（XY，法向 +Z）"},
    },
    ["module", "teeth", "face_width", "outer_diameter"],
)
RENDER_VIEWS_SPEC = _tool(
    "render_views",
    "把当前零件按第三角画法投影成三视图（不带尺寸标注），图片附在下一条消息里，用来和输入图纸逐个视图对照。"
    "主视图沿 -Z 方向看（图上右 = +X、上 = +Y），俯视图在主视图上方（从 +Y 往下看，图上下 = +Z），"
    "右视图在主视图右方（从 +X 看，图上右 = -Z）。可见边为实线，隐藏边为虚线。只在看图建模会话中可用。",
    {},
    [],
)
EXTRA_TOOL_SPECS = {
    "spur_gear": SPUR_GEAR_SPEC,
    "internal_gear": INTERNAL_GEAR_SPEC,
    "render_views": RENDER_VIEWS_SPEC,
}
# 装配零件附加工具集的版本：v1 = {spur_gear}；v2 加入 internal_gear。结果文件按此标注。
EXTRA_TOOLSET_VERSION = "v2"


def tool_specs(extra_tools: list[str] | None = None) -> list[dict]:
    specs = list(TOOL_SPECS)
    for name in extra_tools or []:
        if name not in EXTRA_TOOL_SPECS:
            raise ValueError(f"未知的附加工具 {name}")
        specs.append(EXTRA_TOOL_SPECS[name])
    return specs


def _axis_sign(text: str) -> tuple[str, int]:
    value = str(text).strip().upper()
    if len(value) == 2 and value[0] in "+-" and value[1] in AXES:
        return value[1], 1 if value[0] == "+" else -1
    if len(value) == 1 and value in AXES:
        return value, 1
    raise WorkflowError("TOOL_ARGS", f"方向 {text!r} 应写成 +X/-X/+Y/-Y/+Z/-Z")


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


class WorkflowSession:
    """持有一个零件的建模会话：HTTP、参数表、命名引用和调用日志。"""

    # L1 = v1，行为保持不变以复现已有结果；L1_v2 见 tools_v2.WorkflowSessionV2
    version = "v1"

    def __init__(
        self,
        base_url: str = "http://localhost:5100",
        executor: SwExecutor | None = None,
        timeout: float = 120,
        extra_tools: list[str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout
        self.executor = executor or SwExecutor(base_url=self.base_url)
        self.params = ParamTable()
        self.registry = RefRegistry()
        self.baseline_features: set[str] = set()
        self.ref_planes: dict[str, SketchFrame] = {}
        self.has_part = False
        self.log: list[dict] = []
        self.last_transaction: tuple[dict, dict] | None = None
        self._handlers: dict[str, Callable[[dict], dict]] = {
            "get_model_state": self.get_model_state,
            "set_parameters": self.set_parameters,
            "extrude": self.extrude,
            "cut": self.cut,
            "hole": self.hole,
            "revolve": self.revolve,
            "fillet": self.fillet,
            "chamfer": self.chamfer,
            "circular_pattern": self.circular_pattern,
            "linear_pattern": self.linear_pattern,
            "ref_plane": self.ref_plane,
            "mirror": self.mirror,
            "edit_feature": self.edit_feature,
            "rib": self.rib,
            "counterbore": self.counterbore,
            "countersink": self.countersink,
            "tapped_hole": self.tapped_hole,
            "sweep": self.sweep,
            "loft": self.loft,
            "shell": self.shell,
            "draft": self.draft,
            "sheet_metal": self.sheet_metal,
            "delete_feature": self.delete_feature,
        }
        extra_tools = extra_tools or []
        if "spur_gear" in extra_tools:
            self._handlers["spur_gear"] = self.spur_gear
        if "internal_gear" in extra_tools:
            self._handlers["internal_gear"] = self.internal_gear
        if "render_views" in extra_tools:
            self._handlers["render_views"] = self.render_views

    # ------------------------------------------------------------ HTTP

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            if method == "GET":
                response = self.http.get(f"{self.base_url}{path}", timeout=self.timeout)
            else:
                response = self.http.post(f"{self.base_url}{path}", json=body or {}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WorkflowError("SW_UNAVAILABLE", str(exc)) from None
        try:
            return response.json()
        except ValueError:
            raise WorkflowError("SW_UNAVAILABLE", f"HTTP {response.status_code}: {response.text[:200]}") from None

    def snapshot(self) -> dict:
        data = self._request("GET", "/api/model-observation")
        if not data.get("success"):
            raise WorkflowError("NO_PART", str(data.get("message") or "读取模型失败"))
        return data

    def start_part(self) -> dict:
        data = self._request("POST", "/api/new-part")
        if not data.get("success"):
            raise WorkflowError("NO_PART", str(data.get("message") or "新建零件失败"))
        self.has_part = True
        self.baseline_features = {feature.get("name") for feature in self.snapshot().get("features", [])}
        return data

    def _run(self, operations: list[dict]) -> tuple[bool, list[dict]]:
        """依次执行；带 route 的操作直接调用 workflow 专用接口（不属于 L0 的原子操作集）。"""
        results: list[dict] = []
        for item in operations:
            if "route" in item:
                data = self._request("POST", item["route"], item.get("params") or {})
                result = {
                    "operation": item["operation"],
                    "success": bool(data.get("success")),
                    "message": data.get("message"),
                    "result": data.get("result", data),
                }
            else:
                step = SwOperation(
                    operation=OperationType(item["operation"]), params=item.get("params") or {},
                    description=item.get("description", ""),
                )
                executed = self.executor.execute_operations([step])
                result = executed[0] if executed else {
                    "operation": item["operation"], "success": False, "message": "执行器没有返回结果",
                }
            results.append(result)
            if not result.get("success"):
                return False, results
        return True, results

    # ------------------------------------------------------------ 状态

    def _user_features(self, snapshot: dict) -> list[dict]:
        return [
            {"name": feature.get("name"), "type": feature.get("type")}
            for feature in snapshot.get("features", [])
            if feature.get("name") not in self.baseline_features
        ]

    @staticmethod
    def _volume(snapshot: dict) -> float:
        value = snapshot.get("volumeMm3")
        return float(value) if isinstance(value, (int, float)) else 0.0

    @staticmethod
    def _bbox(snapshot: dict) -> dict | None:
        box = snapshot.get("boundingBox") or {}
        try:
            lows = [float(box[f"min{axis}"]) * 1000 for axis in AXES]
            highs = [float(box[f"max{axis}"]) * 1000 for axis in AXES]
        except (KeyError, TypeError, ValueError):
            return None
        return {
            **{axis: [round(lo, 3), round(hi, 3)] for axis, lo, hi in zip(AXES, lows, highs)},
            "size": [round(hi - lo, 3) for lo, hi in zip(lows, highs)],
        }

    def _face_summaries(self, faces: list[dict]) -> tuple[list[dict], list[dict]]:
        planar, cylinders = [], []
        aliases: dict[int, str] = {}
        for side in ("top", "bottom", "front", "back", "left", "right"):
            try:
                aliases[resolve_ref(side, faces).face_index] = side
            except WorkflowError:
                pass
        for face in faces:
            box = {key: float(value) * 1000 for key, value in (face.get("box") or {}).items()}
            if face.get("surfaceType") == "plane":
                try:
                    frame = face_frame(face, f"face#{face.get('index')}")
                except WorkflowError:
                    continue
                item = {
                    "ref": f"face#{face.get('index')}",
                    "normal": f"{'+' if frame.normal_sign > 0 else '-'}{frame.axis}",
                    "at": f"{frame.axis}={frame.offset_mm:.3f}",
                    "area_mm2": _round(float(face.get("area") or 0) * 1e6, 2),
                }
                if box:
                    item["u_range"] = [_round(box[f"min{frame.u_axis}"]), _round(box[f"max{frame.u_axis}"])]
                    item["v_range"] = [_round(box[f"min{frame.v_axis}"]), _round(box[f"max{frame.v_axis}"])]
                    item["uv"] = f"u={frame.u_axis},v={frame.v_axis}"
                if int(face.get("index")) in aliases:
                    item["alias"] = aliases[int(face.get("index"))]
                planar.append(item)
            elif face.get("surfaceType") == "cylinder" and face.get("radius"):
                axis = face.get("axis") or {}
                main = max(AXES, key=lambda key: abs(float(axis.get(key.lower(), 0))))
                origin = face.get("axisOrigin") or {}
                cylinders.append({
                    "ref": f"face#{face.get('index')}",
                    "diameter_mm": _round(float(face["radius"]) * 2000),
                    "axis": main,
                    "axis_point_mm": [_round(float(origin.get(key, 0)) * 1000) for key in "xyz"],
                    "span_mm": [_round(box.get(f"min{main}")), _round(box.get(f"max{main}"))] if box else None,
                })
        planar.sort(key=lambda item: -(item["area_mm2"] or 0))
        return planar[:MAX_PLANAR_FACES], cylinders[:MAX_CYLINDER_FACES]

    def state_summary(self, snapshot: dict | None = None) -> dict:
        snapshot = snapshot or self.snapshot()
        planar, cylinders = self._face_summaries(snapshot.get("faces") or [])
        return {
            "features": self._user_features(snapshot),
            "bbox_mm": self._bbox(snapshot),
            "volume_mm3": _round(self._volume(snapshot), 2),
            "planar_faces": planar,
            "cylindrical_faces": cylinders,
            "reference_planes": [
                {
                    "name": name,
                    "normal": f"{'+' if frame.normal_sign > 0 else '-'}{frame.axis}",
                    "at": f"{frame.axis}={frame.offset_mm:.3f}",
                    "uv": f"u={frame.u_axis},v={frame.v_axis}",
                }
                for name, frame in self.ref_planes.items()
            ],
            "parameters": {name: _round(value, 6) for name, value in self.params.resolve().items()},
        }

    def get_model_state(self, args: dict) -> dict:
        return {"state": self.state_summary()}

    def _render_png(self, step: Path, png: Path) -> None:
        """在 OCC 环境里投影 STEP；后端环境没有 OCP，所以走子进程。"""
        try:
            proc = subprocess.run(
                [RENDER_PYTHON, "-X", "utf8", "-m", "geomcheck.drawing", str(step), str(png)],
                cwd=LLMPLANNER_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=RENDER_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkflowError("RENDER_FAILED", f"{type(exc).__name__}: {exc}") from None
        if proc.returncode != 0 or not png.is_file():
            raise WorkflowError("RENDER_FAILED", (proc.stderr or proc.stdout or "").strip()[-300:])

    def render_views(self, args: dict) -> dict:
        state = self.state_summary()
        if not state.get("volume_mm3"):
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法投影")
        with tempfile.TemporaryDirectory(prefix="render_views_") as tmp:
            step, png = Path(tmp) / "current.step", Path(tmp) / "views.png"
            data = self._request("POST", "/api/export-step", {"stepPath": str(step)})
            if not data.get("success") or not step.is_file():
                raise WorkflowError("RENDER_FAILED", str(data.get("message") or data.get("error") or "STEP 导出失败"))
            self._render_png(step, png)
            image = base64.b64encode(png.read_bytes()).decode("ascii")
        return {
            "bbox_mm": state["bbox_mm"],
            "volume_mm3": state["volume_mm3"],
            "note": "三视图图片附在下一条消息里",
            IMAGE_KEY: image,
        }

    # ------------------------------------------------------------ 调度

    def call(self, name: str, args: dict | None) -> dict:
        args = args or {}
        entry = {"tool": name, "args": args}
        try:
            if name not in self._handlers:
                raise WorkflowError("TOOL_ARGS", f"未知工具 {name}")
            if not self.has_part:
                raise WorkflowError("NO_PART", "会话还没有零件")
            result = {"ok": True, **self._handlers[name](args)}
        except WorkflowError as exc:
            result = {"ok": False, "error": exc.to_dict()}
        except (KeyError, TypeError, ValueError) as exc:
            result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"参数错误: {exc}"}}
        entry["result"] = result
        self.log.append(entry)
        return result

    # ------------------------------------------------------------ 参数

    def _num(self, args: dict, key: str, default: Any = None, *, required: bool = False) -> float | None:
        value = args.get(key, default)
        if value is None:
            if required:
                raise WorkflowError("TOOL_ARGS", f"缺少参数 {key}")
            return None
        if isinstance(value, str):
            text = value.strip()
            return self.params.value(text[1:] if text.startswith("=") else text)
        return float(value)

    def _resolve_numbers(self, value: Any) -> Any:
        """把轮廓等嵌套结构里的 "=表达式" 字符串求值成数值。"""
        if isinstance(value, dict):
            return {key: (item if key == "type" else self._resolve_numbers(item)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_numbers(item) for item in value]
        if isinstance(value, str) and value.strip().startswith("="):
            return self.params.value(value.strip()[1:])
        return value

    def set_parameters(self, args: dict) -> dict:
        values = args.get("values")
        if not isinstance(values, dict) or not values:
            raise WorkflowError("TOOL_ARGS", "values 必须是非空对象")
        values = {
            name: (value.strip()[1:] if isinstance(value, str) and value.strip().startswith("=") else value)
            for name, value in values.items()
        }
        existing = {
            param.name: {"expr": param.expr, "unit": param.unit, "note": param.note}
            for param in self.params.params.values()
        }
        candidate = ParamTable.from_dict({**existing, **values})
        resolved = candidate.resolve()
        self.params = candidate
        return {"parameters": {name: _round(value, 6) for name, value in resolved.items()}}

    # ------------------------------------------------------------ 平面与草图

    def _frame(self, plane: str, snapshot: dict, *, arg: str = "plane") -> SketchFrame:
        if not plane:
            raise WorkflowError("TOOL_ARGS", f"缺少 {arg}")
        frame = base_plane_frame(plane)
        if frame is not None:
            return frame
        if str(plane).strip() in self.ref_planes:
            return self.ref_planes[str(plane).strip()]
        resolved = resolve_ref(str(plane), snapshot.get("faces") or [], self.registry)
        return face_frame(resolved.face, f"{plane}(face#{resolved.face_index})")

    @staticmethod
    def _open_sketch(frame: SketchFrame) -> dict:
        if frame.is_base_plane:
            return {"operation": "select_plane", "params": {"index": frame.plane_index}}
        if frame.ref_plane_name is not None:
            return {
                "operation": "select_ref_plane_and_sketch", "route": "/api/select-ref-plane-and-sketch",
                "params": {"planeName": frame.ref_plane_name},
            }
        return {"operation": "select_observed_face_and_sketch", "params": {"faceIndex": frame.face_index}}

    def _profile(self, args: dict, frame: SketchFrame, *, removing: bool) -> tuple[list[dict], dict, list[str]]:
        loops = normalize_loops(self._resolve_numbers(args.get("loops")))
        check = check_profile(loops)
        if not check.ok:
            first = check.issues[0]
            hint = ""
            if first.code == "SKETCH_LOOPS_OVERLAP":
                hint = (
                    "同一张草图里的轮廓不能部分重叠。把它们拆成先后两个特征（先拉伸一个，再在同一平面上拉伸另一个，"
                    "实体会自动合并），或者改写成一个合并后的 polygon。"
                )
            raise WorkflowError(first.code, first.message, issues=check.to_dict()["issues"], hint=hint)
        warnings: list[str] = []
        region = None
        for loop in loops:
            geometry = to_geometry(loop)
            region = geometry if region is None else region.union(geometry)
        check = check.to_dict()
        if region is not None and not region.is_empty:
            u0, v0, u1, v1 = region.bounds
            check["profile_uv_bbox"] = {"u": [_round(u0), _round(u1)], "v": [_round(v0), _round(v1)]}
        boundary = frame.boundary_uv()
        if boundary is not None:
            outline = to_geometry(boundary)
            if region is not None and outline is not None:
                overlap = region.intersection(outline.buffer(1e-6)).area
                if overlap <= 1e-9:
                    message = f"轮廓完全落在 {frame.label} 的范围之外（面的 u,v 范围见 get_model_state）"
                    if removing:
                        raise WorkflowError("SKETCH_MISSES_FACE", message)
                    warnings.append(message)
                elif overlap < region.area - 1e-6:
                    warnings.append(f"部分轮廓超出 {frame.label} 的包围范围")
        return loops, check, warnings

    # ------------------------------------------------------------ 事务

    def _transaction(
        self,
        operations: list[dict],
        *,
        expect: str,
        before: dict,
        context: dict,
    ) -> dict:
        """执行一组原子操作并校验效果；失败则删除新建特征，回到调用前状态。"""
        before_names = [feature.get("name") for feature in before.get("features", [])]
        before_volume = self._volume(before)
        try:
            ok, results = self._run(operations)
        except Exception as exc:
            ok, results = False, [{"operation": "executor", "success": False, "message": f"{type(exc).__name__}: {exc}"}]
        after = self.snapshot()
        self.last_transaction = (before, after)
        new_features = [
            feature.get("name") for feature in after.get("features", [])
            if feature.get("name") not in before_names
        ]
        after_volume = self._volume(after)
        delta = after_volume - before_volume

        failure: WorkflowError | None = None
        if not ok:
            failed = next((item for item in results if not item.get("success")), results[-1] if results else {})
            extra = {}
            if expect == "decrease":
                extra["hint"] = (
                    "SolidWorks 在切除范围内找不到可去除的材料时也会报这个错：检查轮廓沿切除方向是否真的穿过实体，"
                    "是否与已有的孔/槽/空腔完全重合（已切过的部分不能再切），方向是否朝向实体外侧。"
                )
            failure = WorkflowError(
                "FEATURE_FAILED",
                f"{failed.get('operation')}: {failed.get('message')}",
                **{**context, **extra},
            )
        elif expect == "increase" and delta <= VOLUME_EPS_MM3:
            failure = WorkflowError("FEATURE_NO_EFFECT", f"体积没有增加（Δ={delta:.3f} mm³）", **context)
        elif expect == "decrease" and delta >= -VOLUME_EPS_MM3:
            failure = WorkflowError(
                "FEATURE_NO_EFFECT",
                f"体积没有减少（Δ={delta:.3f} mm³），切除可能朝向了实体外侧或轮廓不在实体上",
                **context,
            )
        elif expect == "change" and abs(delta) <= VOLUME_EPS_MM3:
            failure = WorkflowError("FEATURE_NO_EFFECT", "体积没有变化", **context)

        if failure is not None:
            rolled_back = self._rollback(new_features)
            failure.context["rolled_back"] = rolled_back
            raise failure
        return {
            "created": new_features,
            "volume_mm3": _round(after_volume, 2),
            "volume_delta_mm3": _round(delta, 2),
            "bbox_mm": self._bbox(after),
            **context,
        }

    def _rollback(self, names: list[str]) -> list[str]:
        removed = []
        for name in reversed(names):
            data = self._request("POST", "/api/delete-feature", {"featureName": name})
            if data.get("success"):
                removed.append(name)
        return removed

    # ------------------------------------------------------------ 方向

    @staticmethod
    def _extrude_flip(frame: SketchFrame, direction: str | None) -> tuple[bool, bool, str]:
        """返回 (flipDirection, midPlane, 实际方向描述)。默认方向：基准面 +法向，实体面外法向。"""
        default_sign = frame.normal_sign
        if not direction:
            return False, False, f"{'+' if default_sign > 0 else '-'}{frame.axis}"
        if str(direction).strip().lower() == "both":
            return False, True, "both"
        axis, sign = _axis_sign(direction)
        if axis != frame.axis:
            raise WorkflowError("TOOL_ARGS", f"方向 {direction} 不沿平面法向轴 {frame.axis}")
        return sign != default_sign, False, f"{'+' if sign > 0 else '-'}{axis}"

    def _cut_direction(self, frame: SketchFrame, direction: str | None, snapshot: dict) -> tuple[str, int]:
        """返回 ("one"/"both", 切除朝向的符号)。"""
        if direction and str(direction).strip().lower() == "both":
            return "both", 0
        if direction and str(direction).strip().lower() != "auto":
            axis, sign = _axis_sign(direction)
            if axis != frame.axis:
                raise WorkflowError("TOOL_ARGS", f"方向 {direction} 不沿平面法向轴 {frame.axis}")
            return "one", sign
        if frame.is_face:
            return "one", -frame.normal_sign
        bbox = self._bbox(snapshot)
        if bbox is None:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法切除")
        lo, hi = bbox[frame.axis]
        offset = frame.offset_mm
        if offset <= lo + 1e-6:
            return "one", 1
        if offset >= hi - 1e-6:
            return "one", -1
        return "both", 0

    # ------------------------------------------------------------ 特征工具

    def _until_reference(self, value: Any, snapshot: dict) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if text.lower() == "body":
            return "body"
        return self._frame(text, snapshot).sw_reference

    def extrude(self, args: dict) -> dict:
        before = self.snapshot()
        frame = self._frame(args.get("plane"), before)
        loops, check, warnings = self._profile(args, frame, removing=False)
        until = self._until_reference(args.get("up_to"), before)
        flip, mid, actual = self._extrude_flip(frame, args.get("direction"))
        if until:
            if mid:
                raise WorkflowError("TOOL_ARGS", "up_to 不能与 direction=both 同时使用")
            feature = {
                "operation": "feature_until", "route": "/api/feature/until",
                "params": {"until": until, "flipDirection": flip, "cut": False},
            }
        else:
            depth = self._num(args, "depth", required=True)
            if depth <= 0:
                raise WorkflowError("TOOL_ARGS", "depth 必须为正，或给出 up_to")
            draft = self._num(args, "draft_deg", 0) or 0
            feature = {
                "operation": "feature_extrusion",
                "params": {
                    "depth": depth / 1000, "flipDirection": flip, "midPlane": mid,
                    "draftAngleDeg": draft, "draftOutward": bool(args.get("draft_outward", False)),
                },
            }
        operations = [self._open_sketch(frame), *draw_operations(frame, loops), feature]
        result = self._transaction(
            operations, expect="increase", before=before,
            context={"plane": frame.describe(), "direction": actual, "region_area_mm2": check["region_area_mm2"],
                     "profile_uv_bbox": check.get("profile_uv_bbox")},
        )
        return {**result, "warnings": warnings} if warnings else result

    def _cut_feature(self, frame: SketchFrame, args: dict, snapshot: dict) -> tuple[dict, str]:
        until = self._until_reference(args.get("up_to"), snapshot)
        through = bool(args.get("through_all", False))
        depth = self._num(args, "depth")
        if until:
            mode, sign = self._cut_direction(frame, args.get("direction"), snapshot)
            if mode == "both":
                raise WorkflowError("TOOL_ARGS", "up_to 不能与双向切除同时使用")
            default_sign = -frame.normal_sign
            return {
                "operation": "feature_until", "route": "/api/feature/until",
                "params": {"until": until, "flipDirection": sign != default_sign, "cut": True},
            }, f"{'+' if sign > 0 else '-'}{frame.axis} until {until}"
        if not through and (depth is None or depth <= 0):
            raise WorkflowError("TOOL_ARGS", "cut 需要正的 depth、through_all=true 或 up_to")
        mode, sign = self._cut_direction(frame, args.get("direction"), snapshot)
        if mode == "both":
            if through or depth is None:
                bbox = self._bbox(snapshot) or {"size": [1000, 1000, 1000]}
                depth = 2 * max(bbox["size"]) + 2
            return {
                "operation": "feature_cut_extrusion",
                "params": {"depth": depth / 1000, "endCondition": "mid_plane"},
            }, "both"
        # SolidWorks 默认切除方向与平面法向相反（实体面即朝内），实测基准面同样如此。
        default_sign = -frame.normal_sign
        params = {
            "depth": (depth or 0.001) / 1000,
            "endCondition": "through_all" if through else "blind",
            "flipDirection": sign != default_sign,
        }
        return {"operation": "feature_cut_extrusion", "params": params}, f"{'+' if sign > 0 else '-'}{frame.axis}"

    def cut(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法切除")
        frame = self._frame(args.get("plane"), before)
        loops, check, warnings = self._profile(args, frame, removing=True)
        feature, actual = self._cut_feature(frame, args, before)
        operations = [self._open_sketch(frame), *draw_operations(frame, loops), feature]
        result = self._transaction(
            operations, expect="decrease", before=before,
            context={"plane": frame.describe(), "direction": actual, "region_area_mm2": check["region_area_mm2"],
                     "profile_uv_bbox": check.get("profile_uv_bbox")},
        )
        return {**result, "warnings": warnings} if warnings else result

    def hole(self, args: dict) -> dict:
        centers = self._resolve_numbers(args.get("centers"))
        if not isinstance(centers, list) or not centers:
            raise WorkflowError("TOOL_ARGS", "centers 必须是非空的 [[u,v], ...]")
        diameter = self._num(args, "diameter", required=True)
        loops = [{"type": "circle", "center": center, "diameter": diameter} for center in centers]
        return self.cut({**args, "loops": loops})

    def revolve(self, args: dict) -> dict:
        before = self.snapshot()
        frame = self._frame(args.get("plane"), before)
        is_cut = bool(args.get("cut", False))
        if is_cut and self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法旋转切除")
        loops, check, warnings = self._profile(args, frame, removing=is_cut)
        start = [float(v) for v in self._resolve_numbers(args.get("axis_start") or [])]
        end = [float(v) for v in self._resolve_numbers(args.get("axis_end") or [])]
        if len(start) != 2 or len(end) != 2 or math.dist(start, end) <= 1e-9:
            raise WorkflowError("TOOL_ARGS", "axis_start/axis_end 必须是两个不同的 [u,v]")
        self._check_axis_side(loops, start, end)
        angle = self._num(args, "angle_deg", 360)
        operations = [
            self._open_sketch(frame),
            centerline_operation(frame, start, end),
            *draw_operations(frame, loops),
            {"operation": "feature_revolve", "params": {"angle": angle, "isCut": is_cut}},
        ]
        result = self._transaction(
            operations, expect="decrease" if is_cut else "increase", before=before,
            context={"plane": frame.describe(), "region_area_mm2": check["region_area_mm2"],
                     "profile_uv_bbox": check.get("profile_uv_bbox")},
        )
        return {**result, "warnings": warnings} if warnings else result

    @staticmethod
    def _check_axis_side(loops: list[dict], start: list[float], end: list[float]) -> None:
        du, dv = end[0] - start[0], end[1] - start[1]
        length = math.hypot(du, dv)
        sides = set()
        for index, loop in enumerate(loops):
            geometry = to_geometry(loop)
            for u, v in list(geometry.exterior.coords):
                distance = (du * (v - start[1]) - dv * (u - start[0])) / length
                if abs(distance) > 1e-6:
                    sides.add(1 if distance > 0 else -1)
            if len(sides) > 1:
                raise WorkflowError(
                    "SKETCH_CROSSES_AXIS",
                    f"轮廓 {index} 跨越了旋转轴；旋转轮廓应是截面的一半，全部位于轴线一侧",
                )

    def _edge_params(self, edges: Any) -> dict:
        if edges is None or edges == "":
            return {"edgeSelection": "all"}
        if isinstance(edges, str):
            return {"edgeSelection": edges}
        if isinstance(edges, dict) and "origin" in edges and "direction" in edges:
            origin = [float(v) / 1000 for v in self._resolve_numbers(edges["origin"])]
            direction = [float(v) for v in self._resolve_numbers(edges["direction"])]
            if len(origin) != 3 or len(direction) != 3:
                raise WorkflowError("TOOL_ARGS", "射线 origin/direction 必须是三维")
            return {
                "edgeSelection": "ray",
                "rx": origin[0], "ry": origin[1], "rz": origin[2],
                "dx": direction[0], "dy": direction[1], "dz": direction[2],
            }
        raise WorkflowError("TOOL_ARGS", "edges 必须是 all、语义边名或 {origin, direction}")

    def fillet(self, args: dict) -> dict:
        before = self.snapshot()
        radius = self._num(args, "radius", required=True)
        operation = {"operation": "feature_fillet", "params": {"radius": radius / 1000, **self._edge_params(args.get("edges"))}}
        return self._transaction([operation], expect="change", before=before, context={})

    def chamfer(self, args: dict) -> dict:
        before = self.snapshot()
        distance = self._num(args, "distance", required=True)
        operation = {
            "operation": "feature_chamfer",
            "params": {"type": "equal_distance", "distance1": distance / 1000, **self._edge_params(args.get("edges"))},
        }
        return self._transaction([operation], expect="change", before=before, context={})

    def circular_pattern(self, args: dict) -> dict:
        before = self.snapshot()
        count = int(args.get("count") or 0)
        if count < 2:
            raise WorkflowError("TOOL_ARGS", "count 至少为 2")
        axis, _ = _axis_sign(args.get("axis", "Y"))
        params = {"count": count, "axis": axis.lower(), "angleDeg": self._num(args, "angle_deg", 360)}
        center = self._resolve_numbers(args.get("center"))
        if center:
            if len(center) != 3:
                raise WorkflowError("TOOL_ARGS", "center 必须是 [x,y,z] mm")
            params.update({f"axisOrigin{key}": float(value) / 1000 for key, value in zip(AXES, center)})
        operation = {"operation": "feature_circular_pattern", "params": params}
        return self._transaction([operation], expect="change", before=before, context={})

    def linear_pattern(self, args: dict) -> dict:
        before = self.snapshot()
        axis1, sign1 = _axis_sign(args.get("direction1", "+X"))
        params = {
            "num1": int(args.get("count1") or 0),
            "spacing1": self._num(args, "spacing1", required=True) / 1000,
            "dir1Axis": axis1.lower(),
            "flip1": sign1 < 0,
            "num2": int(args.get("count2") or 1),
        }
        if params["num1"] < 2 and params["num2"] < 2:
            raise WorkflowError("TOOL_ARGS", "count1 或 count2 至少有一个 ≥ 2")
        if params["num2"] > 1:
            axis2, sign2 = _axis_sign(args.get("direction2", "+Z"))
            if axis2 == axis1:
                raise WorkflowError("TOOL_ARGS", "direction1 与 direction2 必须沿不同的轴")
            params.update({
                "spacing2": self._num(args, "spacing2", required=True) / 1000,
                "dir2Axis": axis2.lower(),
                "flip2": sign2 < 0,
            })
        operation = {"operation": "feature_linear_pattern", "params": params}
        return self._transaction([operation], expect="change", before=before, context={})

    def ref_plane(self, args: dict) -> dict:
        before = self.snapshot()
        base = self._frame(args.get("reference"), before, arg="reference")
        offset = self._num(args, "offset", required=True)
        target = base.offset_mm + base.normal_sign * offset
        index = AXES.index(base.axis)
        attempts = []
        # SolidWorks 的偏移方向取决于参考的朝向，按实际位置校验，不对就翻转重建。
        for flip in (offset < 0, offset >= 0):
            data = self._request("POST", "/api/ref-plane/offset", {
                "reference": base.sw_reference, "distance": abs(offset) / 1000, "flip": flip,
            })
            if not data.get("success"):
                raise WorkflowError("FEATURE_FAILED", str(data.get("message") or "创建基准面失败"))
            created = data.get("result") or {}
            name = str(created.get("featureName") or "")
            position = float((created.get("origin") or [0, 0, 0])[index]) * 1000
            attempts.append({"flip": flip, "at": _round(position)})
            if abs(position - target) <= 1e-3:
                sign = self._plane_normal_sign(created.get("rotation"), index, base.normal_sign)
                frame = ref_plane_frame(name, base.axis, sign, target)
                self.ref_planes[name] = frame
                return {"name": name, "plane": frame.describe(), "from": base.label}
            self._request("POST", "/api/delete-feature", {"featureName": name})
        raise WorkflowError(
            "FEATURE_FAILED", f"基准面位置与预期 {base.axis}={target:g}mm 不符", attempts=attempts,
        )

    @staticmethod
    def _plane_normal_sign(rotation: Any, index: int, default: int) -> int:
        """IRefPlane.Transform 的第三行是平面法向。"""
        try:
            value = float(rotation[6 + index])
        except (TypeError, IndexError, ValueError):
            return default
        return default if abs(value) < 0.5 else (1 if value > 0 else -1)

    def mirror(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法镜像")
        frame = self._frame(args.get("plane"), before)
        body = bool(args.get("body", False))
        names = [str(name) for name in args.get("features") or []]
        if not body:
            existing = {feature["name"] for feature in self._user_features(before)}
            if not names:
                raise WorkflowError("TOOL_ARGS", "给出 features（要镜像的特征名）或 body=true")
            missing = [name for name in names if name not in existing]
            if missing:
                raise WorkflowError(
                    "TOOL_ARGS", f"找不到特征 {missing}", hint=f"可用特征: {sorted(existing)}",
                )
        operation = {
            "operation": "feature_mirror", "route": "/api/feature/mirror",
            "params": {"plane": frame.sw_reference, "features": names, "mirrorBody": body},
        }
        return self._transaction(
            [operation], expect="increase" if body else "change", before=before,
            context={"plane": frame.describe(), "mirrored": "body" if body else names},
        )

    def edit_feature(self, args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        if not name:
            raise WorkflowError("TOOL_ARGS", "缺少 name")
        if name in self.baseline_features:
            raise WorkflowError("TOOL_ARGS", f"{name} 是默认基准特征，不能编辑")
        value = self._num(args, "value", required=True)
        before = self.snapshot()
        features = self._user_features(before)
        sketches = {"ProfileFeature", "3DProfileFeature", "OriginProfileFeature"}
        editable = [item for item in features if item.get("type") not in sketches]
        existing = {item["name"] for item in editable}
        if name not in existing:
            raise WorkflowError(
                "TOOL_ARGS", f"找不到可编辑特征 {name}",
                hint=f"请用实体特征名而不是草图名。可用: {sorted(existing)}",
            )
        data = self._request("POST", "/api/modify-dimension", {
            "featureName": name,
            "dimensionName": str(args.get("dimension") or ""),
            "newValueUser": value,
        })
        if not data.get("success"):
            raise WorkflowError("FEATURE_FAILED", str(data.get("message") or "编辑特征失败"))
        after = self.snapshot()
        unit = str(data.get("unit") or "mm")
        return {
            "edited": name,
            f"old_value_{unit}": _round(float(data.get("oldValueUser") or 0)),
            f"new_value_{unit}": _round(float(data.get("newValueUser") or value)),
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": self._bbox(after),
        }

    def _uv_pair(self, value: Any, name: str) -> list[float]:
        pair = self._resolve_numbers(value)
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise WorkflowError("TOOL_ARGS", f"{name} 必须是 [u, v]")
        return [float(pair[0]), float(pair[1])]

    def rib(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法加筋")
        frame = self._frame(args.get("plane"), before)
        start = self._uv_pair(args.get("start"), "start")
        end = self._uv_pair(args.get("end"), "end")
        thickness = self._num(args, "thickness", required=True)
        if thickness <= 0:
            raise WorkflowError("TOOL_ARGS", "thickness 必须为正")
        operations = [
            self._open_sketch(frame),
            *draw_open_path(frame, start, [{"to": end}]),
            {
                "operation": "feature_rib", "route": "/api/feature/rib",
                "params": {
                    "thickness": thickness / 1000,
                    "bothSides": bool(args.get("both_sides", True)),
                    "flip": bool(args.get("flip", False)),
                },
            },
        ]
        try:
            return self._transaction(
                operations, expect="increase", before=before,
                context={"plane": frame.describe(), "thickness_mm": thickness},
            )
        except WorkflowError as exc:
            self._request("POST", "/api/close-sketch-named", {})
            before_names = {feature.get("name") for feature in before.get("features", [])}
            leftover = {
                "ProfileFeature", "3DProfileFeature",
            }
            for item in reversed(self._user_features(self.snapshot())):
                if item["name"] not in before_names and item.get("type") in leftover:
                    self._request("POST", "/api/delete-feature", {"featureName": item["name"]})
            corner = [end[0], start[1]]
            if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-6:
                raise
            result = self.extrude({
                "plane": args.get("plane"),
                "loops": [{"type": "polygon", "points": [start, corner, end]}],
                "depth": thickness,
                "direction": "both",
            })
            result["warnings"] = [
                f"InsertRib 失败，已用直角三角凸台代替（{exc}）。"
                "若三角方向不对，请改用 extrude 自己画筋的截面。"
            ]
            return result

    def _head_and_through(self, args: dict, *, kind: str) -> dict:
        centers = self._resolve_numbers(args.get("centers"))
        if not isinstance(centers, list) or not centers:
            raise WorkflowError("TOOL_ARGS", "centers 必须是非空的 [[u,v], ...]")
        through_d = self._num(args, "through_diameter", required=True)
        head_d = self._num(args, "head_diameter", required=True)
        head_depth = self._num(args, "head_depth", required=True)
        if head_d <= through_d:
            raise WorkflowError("TOOL_ARGS", "head_diameter 必须大于 through_diameter")
        if head_depth <= 0:
            raise WorkflowError("TOOL_ARGS", "head_depth 必须为正")
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法打孔")
        frame = self._frame(args.get("plane"), before)
        shared = {key: args[key] for key in ("direction", "through_all", "depth") if key in args}
        head = self.cut({
            **shared, "plane": args.get("plane"), "depth": head_depth, "through_all": False,
            "loops": [{"type": "circle", "center": center, "diameter": head_d} for center in centers],
        })
        try:
            through = self.cut({
                **shared, "plane": args.get("plane"),
                "loops": [{"type": "circle", "center": center, "diameter": through_d} for center in centers],
            })
        except WorkflowError as exc:
            for name in reversed(head.get("created") or []):
                self._request("POST", "/api/delete-feature", {"featureName": name})
            exc.context["hint"] = (
                f"沉头部分已成功并已回滚，失败的是直径 {through_d:g} 的通孔部分。"
                "如果这个位置已经有同径的孔，只需用 cut 单独切出沉头。"
            )
            raise
        after = self.snapshot()
        return {
            "kind": kind,
            "created": (head.get("created") or []) + (through.get("created") or []),
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - self._volume(before), 2),
            "bbox_mm": self._bbox(after),
            "plane": frame.describe(),
        }

    def counterbore(self, args: dict) -> dict:
        return self._head_and_through(args, kind="counterbore")

    def countersink(self, args: dict) -> dict:
        return self._head_and_through(args, kind="countersink")

    def tapped_hole(self, args: dict) -> dict:
        result = self.hole(args)
        thread_d = self._num(args, "thread_diameter")
        if not thread_d:
            return result
        snapshot = self.snapshot()
        cylinders = [
            face for face in snapshot.get("faces") or []
            if face.get("surfaceType") == "cylinder" and face.get("radius")
            and abs(float(face["radius"]) * 2000 - thread_d) <= 0.6
        ]
        if not cylinders:
            result["warnings"] = [f"没有找到直径约 {thread_d}mm 的圆柱面，已留下底孔"]
            return result
        data = self._request("POST", "/api/feature/cosmetic-thread", {
            "face": f"face:{int(cylinders[0]['index'])}",
            "diameter": thread_d / 1000,
            "depth": (self._num(args, "depth") or 10) / 1000,
        })
        if not data.get("success"):
            result["warnings"] = [str(data.get("message") or "装饰螺纹失败")]
        else:
            result["thread"] = (data.get("result") or {}).get("featureName")
        return result

    def sweep(self, args: dict) -> dict:
        before = self.snapshot()
        is_cut = bool(args.get("cut", False))
        if is_cut and self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法扫描切除")
        path_frame = self._frame(args.get("path_plane"), before, arg="path_plane")
        profile_frame = self._frame(args.get("profile_plane"), before, arg="profile_plane")
        start = self._uv_pair(args.get("path_start"), "path_start")
        steps = args.get("path_segments")
        if not isinstance(steps, list) or not steps:
            raise WorkflowError("TOOL_ARGS", "path_segments 不能为空")
        loops, check, warnings = self._profile(args, profile_frame, removing=is_cut)
        operations = [
            self._open_sketch(path_frame),
            *draw_open_path(path_frame, start, steps),
            {"operation": "close_sketch", "route": "/api/close-sketch-named", "params": {}},
            self._open_sketch(profile_frame),
            *draw_operations(profile_frame, loops),
            {"operation": "feature_sweep", "route": "/api/feature/sweep", "params": {"cut": is_cut}},
        ]
        result = self._transaction(
            operations, expect="decrease" if is_cut else "increase", before=before,
            context={"path": path_frame.describe(), "profile": profile_frame.describe(),
                     "region_area_mm2": check["region_area_mm2"]},
        )
        return {**result, "warnings": warnings} if warnings else result

    def loft(self, args: dict) -> dict:
        before = self.snapshot()
        is_cut = bool(args.get("cut", False))
        if is_cut and self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法放样切除")
        sections = args.get("sections")
        if not isinstance(sections, list) or len(sections) < 2:
            raise WorkflowError("TOOL_ARGS", "sections 至少两段，每段 {plane, loops}")
        operations: list[dict] = []
        warnings: list[str] = []
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise WorkflowError("TOOL_ARGS", f"sections[{index}] 必须是对象")
            frame = self._frame(section.get("plane"), before)
            loops, check, extra = self._profile(section, frame, removing=is_cut)
            warnings.extend(extra)
            operations += [self._open_sketch(frame), *draw_operations(frame, loops)]
            if index < len(sections) - 1:
                operations.append({"operation": "close_sketch", "route": "/api/close-sketch-named", "params": {}})
        operations.append({
            "operation": "feature_loft", "route": "/api/feature/loft",
            "params": {"sectionCount": len(sections), "cut": is_cut},
        })
        result = self._transaction(
            operations, expect="decrease" if is_cut else "increase", before=before,
            context={"sections": len(sections)},
        )
        return {**result, "warnings": warnings} if warnings else result

    def shell(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法抽壳")
        thickness = self._num(args, "thickness", required=True)
        faces = [self._frame(item, before).sw_reference for item in (args.get("open_faces") or [])]
        if not faces:
            raise WorkflowError("TOOL_ARGS", "open_faces 不能为空")
        operation = {
            "operation": "feature_shell", "route": "/api/feature/shell",
            "params": {"thickness": thickness / 1000, "openFaces": faces, "outward": bool(args.get("outward", False))},
        }
        return self._transaction(
            [operation], expect="decrease", before=before,
            context={"thickness_mm": thickness, "open_faces": faces},
        )

    def draft(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) <= 0:
            raise WorkflowError("NO_BODY", "零件中还没有实体，无法拔模")
        angle = self._num(args, "angle_deg", required=True)
        neutral = self._frame(args.get("neutral"), before, arg="neutral")
        faces = [self._frame(item, before).sw_reference for item in (args.get("faces") or [])]
        if not faces:
            raise WorkflowError("TOOL_ARGS", "faces 不能为空")
        operation = {
            "operation": "feature_draft", "route": "/api/feature/draft",
            "params": {
                "angleDeg": angle, "neutral": neutral.sw_reference, "faces": faces,
                "flip": bool(args.get("flip", False)),
            },
        }
        return self._transaction(
            [operation], expect="change", before=before,
            context={"angle_deg": angle, "neutral": neutral.describe()},
        )

    def sheet_metal(self, args: dict) -> dict:
        before = self.snapshot()
        if self._volume(before) > VOLUME_EPS_MM3:
            raise WorkflowError("TOOL_ARGS", "钣金基体法兰只能用在空零件上")
        frame = self._frame(args.get("plane"), before)
        loops, check, warnings = self._profile(args, frame, removing=False)
        thickness = self._num(args, "thickness", required=True)
        if thickness <= 0:
            raise WorkflowError("TOOL_ARGS", "thickness 必须为正")
        operations = [
            self._open_sketch(frame),
            *draw_operations(frame, loops),
            {
                "operation": "feature_sheet_metal", "route": "/api/feature/sheet-metal-base",
                "params": {"thickness": thickness / 1000},
            },
        ]
        result = self._transaction(
            operations, expect="increase", before=before,
            context={"plane": frame.describe(), "thickness_mm": thickness,
                     "region_area_mm2": check["region_area_mm2"]},
        )
        return {**result, "warnings": warnings} if warnings else result

    def delete_feature(self, args: dict) -> dict:
        name = str(args.get("name") or "").strip()
        if not name:
            raise WorkflowError("TOOL_ARGS", "缺少 name")
        if name in self.baseline_features:
            raise WorkflowError("TOOL_ARGS", f"{name} 是默认基准特征，不能删除")
        before_volume = self._volume(self.snapshot())
        data = self._request("POST", "/api/delete-feature", {"featureName": name})
        if not data.get("success"):
            raise WorkflowError("FEATURE_FAILED", str(data.get("message")))
        after = self.snapshot()
        remaining = {feature.get("name") for feature in after.get("features", [])}
        self.ref_planes = {key: frame for key, frame in self.ref_planes.items() if key in remaining}
        return {
            "deleted": name,
            "volume_mm3": _round(self._volume(after), 2),
            "volume_delta_mm3": _round(self._volume(after) - before_volume, 2),
            "bbox_mm": self._bbox(after),
        }

    def spur_gear(self, args: dict) -> dict:
        if self._volume(self.snapshot()) > VOLUME_EPS_MM3:
            raise WorkflowError("TOOL_ARGS", "spur_gear 只能用在空零件上，请先建新零件")
        module = self._num(args, "module", required=True)
        teeth = int(self._num(args, "teeth", required=True))
        face_width = self._num(args, "face_width", required=True)
        bore = self._num(args, "bore", required=True)
        pressure = self._num(args, "pressure_angle_deg", 30) or 30
        phase = self._num(args, "phase_deg", 0) or 0
        if teeth < 10 or module <= 0 or face_width <= 0 or bore <= 0:
            raise WorkflowError("TOOL_ARGS", "teeth≥10，module/face_width/bore 必须为正")
        outline, params = generate_involute_outline(
            tooth_count=teeth,
            phase_angle_deg=phase,
            module_m=module / 1000,
            pressure_angle_deg=pressure,
        )
        loops = [
            {"type": "polygon", "points": [[x * 1000, y * 1000] for x, y in outline[:-1]]},
            {"type": "circle", "center": [0, 0], "diameter": bore},
        ]
        result = self.extrude({
            "plane": args.get("plane") or "front_plane",
            "loops": loops,
            "depth": face_width,
        })
        key_w = self._num(args, "keyway_width")
        key_d = self._num(args, "keyway_depth")
        if key_w and key_d:
            cut = self.cut({
                "plane": args.get("plane") or "front_plane",
                "loops": [{
                    "type": "rect",
                    "center": [0, bore / 2 + key_d / 2 - 0.05],
                    "size": [key_w, key_d + 0.1],
                }],
                "through_all": True,
            })
            result = {**result, "keyway": cut.get("created")}
        result["gear"] = {
            "teeth": teeth,
            "module_mm": module,
            "pitch_diameter_mm": _round(params["pitchRadius"] * 2000),
            "tip_diameter_mm": _round(params["outerRadius"] * 2000),
            "root_diameter_mm": _round(params["rootRadius"] * 2000),
            "bore_mm": bore,
            "face_width_mm": face_width,
            "pressure_angle_deg": pressure,
            "phase_deg": phase,
        }
        return result

    def internal_gear(self, args: dict) -> dict:
        if self._volume(self.snapshot()) > VOLUME_EPS_MM3:
            raise WorkflowError("TOOL_ARGS", "internal_gear 只能用在空零件上，请先建新零件")
        module = self._num(args, "module", required=True)
        teeth = int(self._num(args, "teeth", required=True))
        face_width = self._num(args, "face_width", required=True)
        outer = self._num(args, "outer_diameter", required=True)
        pressure = self._num(args, "pressure_angle_deg", 30) or 30
        phase = self._num(args, "phase_deg", 0) or 0
        if teeth < 24 or module <= 0 or face_width <= 0:
            raise WorkflowError("TOOL_ARGS", "teeth≥24，module/face_width 必须为正")
        cavity, params = generate_internal_cavity(
            tooth_count=teeth, phase_angle_deg=phase, module_m=module / 1000, pressure_angle_deg=pressure,
        )
        root_d = params["rootRadius"] * 2000
        if outer < root_d + 2 * module:
            raise WorkflowError("TOOL_ARGS", f"outer_diameter 至少 {root_d + 2 * module:.2f} mm（齿根圆 + 2 倍模数）")
        result = self.extrude({
            "plane": args.get("plane") or "front_plane",
            "loops": [
                {"type": "circle", "center": [0, 0], "diameter": outer},
                {"type": "polygon", "points": [[x * 1000, y * 1000] for x, y in cavity[:-1]]},
            ],
            "depth": face_width,
        })
        result["gear"] = {
            "internal": True,
            "teeth": teeth,
            "module_mm": module,
            "pitch_diameter_mm": _round(params["pitchRadius"] * 2000),
            "tip_diameter_mm": _round(params["tipRadius"] * 2000),
            "root_diameter_mm": _round(root_d),
            "outer_diameter_mm": outer,
            "face_width_mm": face_width,
            "pressure_angle_deg": pressure,
            "phase_deg": phase,
        }
        return result


def dumps_result(result: dict, limit: int = 6000) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…(截断)"


__all__ = [
    "TOOL_SPECS", "TOOL_NAMES", "EXTRA_TOOL_SPECS", "SPUR_GEAR_SPEC", "RENDER_VIEWS_SPEC", "IMAGE_KEY",
    "WorkflowSession", "dumps_result", "tool_specs", "BASE_PLANES",
]
