"""L1 工程师级建模工具：Agent 每次调用一个工具，拿到真实结果后再决定下一步。

每个特征工具都是“原子事务”：
    参数求值 → 解析平面引用 → 草图预检 → 执行原子操作 → 校验体积变化；
任何一步失败都会删除本次新建的草图/特征，模型回到调用前的状态，
并返回带错误码和修复提示的结果，交给 Agent 自己修正。
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable

import requests

from ..executor import SwExecutor
from ..schemas import OperationType, SwOperation
from .errors import WorkflowError
from .frames import BASE_PLANES, SketchFrame, base_plane_frame, face_frame, ref_plane_frame
from .params import ParamTable
from .profiles import centerline_operation, draw_operations, normalize_loops
from .refs import RefRegistry, resolve_ref
from .sketch_check import check_profile, to_geometry

VOLUME_EPS_MM3 = 1e-3
AXES = ("X", "Y", "Z")
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
        "path: {start:[u,v], segments:[...]}，每段 {to:[u,v]} 为直线，另加 center:[u,v]（可带 ccw）、"
        "radius+ccw(+large)、through:[u,v] 或 tangent:true 之一为圆弧；末点不回到起点时自动补直线。"
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
            "direction": {
                "type": "string",
                "description": "拉伸方向：+X/-X/+Y/-Y/+Z/-Z（须沿平面法向轴）或 both（两侧对称）。默认：基准面沿 +法向，实体面沿外法向。",
            },
            "draft_deg": _NUM,
            "draft_outward": {"type": "boolean"},
        },
        ["plane", "loops", "depth"],
    ),
    _tool(
        "cut",
        "在平面上画轮廓并拉伸切除。给 depth（盲切）或 through_all=true（贯穿）。",
        {
            "plane": {"type": "string", "description": _PLANE_DESC},
            "loops": {"type": "array", "items": _LOOP_SCHEMA},
            "depth": _NUM,
            "through_all": {"type": "boolean"},
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

    def __init__(
        self,
        base_url: str = "http://localhost:5100",
        executor: SwExecutor | None = None,
        timeout: float = 120,
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
            "delete_feature": self.delete_feature,
        }

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

    def _frame(self, plane: str, snapshot: dict) -> SketchFrame:
        if not plane:
            raise WorkflowError("TOOL_ARGS", "缺少 plane")
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
        boundary = frame.boundary_uv()
        if boundary is not None:
            outline = to_geometry(boundary)
            region = None
            for loop in loops:
                geometry = to_geometry(loop)
                region = geometry if region is None else region.union(geometry)
            if region is not None and outline is not None:
                overlap = region.intersection(outline.buffer(1e-6)).area
                if overlap <= 1e-9:
                    message = f"轮廓完全落在 {frame.label} 的范围之外（面的 u,v 范围见 get_model_state）"
                    if removing:
                        raise WorkflowError("SKETCH_MISSES_FACE", message)
                    warnings.append(message)
                elif overlap < region.area - 1e-6:
                    warnings.append(f"部分轮廓超出 {frame.label} 的包围范围")
        return loops, check.to_dict(), warnings

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
        new_features = [
            feature.get("name") for feature in after.get("features", [])
            if feature.get("name") not in before_names
        ]
        after_volume = self._volume(after)
        delta = after_volume - before_volume

        failure: WorkflowError | None = None
        if not ok:
            failed = next((item for item in results if not item.get("success")), results[-1] if results else {})
            failure = WorkflowError(
                "FEATURE_FAILED",
                f"{failed.get('operation')}: {failed.get('message')}",
                **context,
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

    def extrude(self, args: dict) -> dict:
        before = self.snapshot()
        frame = self._frame(args.get("plane"), before)
        loops, check, warnings = self._profile(args, frame, removing=False)
        depth = self._num(args, "depth", required=True)
        if depth <= 0:
            raise WorkflowError("TOOL_ARGS", "depth 必须为正")
        flip, mid, actual = self._extrude_flip(frame, args.get("direction"))
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
            context={"plane": frame.describe(), "direction": actual, "region_area_mm2": check["region_area_mm2"]},
        )
        return {**result, "warnings": warnings} if warnings else result

    def _cut_feature(self, frame: SketchFrame, args: dict, snapshot: dict) -> tuple[dict, str]:
        through = bool(args.get("through_all", False))
        depth = self._num(args, "depth")
        if not through and (depth is None or depth <= 0):
            raise WorkflowError("TOOL_ARGS", "cut 需要正的 depth，或 through_all=true")
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
            context={"plane": frame.describe(), "direction": actual, "region_area_mm2": check["region_area_mm2"]},
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
            context={"plane": frame.describe(), "region_area_mm2": check["region_area_mm2"]},
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
        base = self._frame(args.get("reference"), before)
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

def dumps_result(result: dict, limit: int = 6000) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…(截断)"


__all__ = ["TOOL_SPECS", "TOOL_NAMES", "WorkflowSession", "dumps_result", "BASE_PLANES"]
