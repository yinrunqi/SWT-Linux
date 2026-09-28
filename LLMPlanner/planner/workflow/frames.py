"""草图平面的统一二维坐标系。

LLM 在任何草图平面上都用 (u, v) 毫米坐标画轮廓，(u, v) 固定对应两根模型轴：

    法向沿 Z（前视基准面、前/后表面）：u = X, v = Y
    法向沿 Y（上视基准面、顶/底表面）：u = X, v = Z
    法向沿 X（右视基准面、左/右表面）：u = Z, v = Y

这样同一个点在任何平面上的写法都和模型坐标一致，不需要知道 SolidWorks
草图坐标在各基准面上的翻转规则。换算规则（在真机上实测）：
基准面草图直接使用草图坐标，上视面 sketch = (X, -Z)，右视面 sketch = (-Z, Y)；
实体面和用户新建基准面上的草图接受模型三维坐标，由 C# 端经 ModelToSketchTransform 换算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import WorkflowError

UV_AXES = {"Z": ("X", "Y"), "Y": ("X", "Z"), "X": ("Z", "Y")}
_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

# 名称 → (select_plane 的 index, 法向轴)。基准面默认拉伸方向为 +法向轴。
BASE_PLANES = {
    "front_plane": (0, "Z"),
    "top_plane": (1, "Y"),
    "right_plane": (2, "X"),
}
BASE_PLANE_ALIASES = {
    "front_plane": "front_plane", "xy": "front_plane", "plane_xy": "front_plane", "前视基准面": "front_plane",
    "top_plane": "top_plane", "xz": "top_plane", "plane_xz": "top_plane", "上视基准面": "top_plane",
    "right_plane": "right_plane", "yz": "right_plane", "plane_yz": "right_plane", "右视基准面": "right_plane",
}
# 基准面草图坐标 = (su * u, sv * v)
_BASE_SKETCH_SIGNS = {"Z": (1.0, 1.0), "Y": (1.0, -1.0), "X": (-1.0, 1.0)}


@dataclass(frozen=True)
class SketchFrame:
    label: str
    axis: str            # 法向所在模型轴
    normal_sign: int     # 基准面为 +1；实体面为外法向的符号
    offset_mm: float     # 平面在法向轴上的位置
    plane_index: int | None = None
    face_index: int | None = None
    face_box_mm: dict | None = None
    ref_plane_name: str | None = None

    @property
    def u_axis(self) -> str:
        return UV_AXES[self.axis][0]

    @property
    def v_axis(self) -> str:
        return UV_AXES[self.axis][1]

    @property
    def is_base_plane(self) -> bool:
        return self.plane_index is not None

    @property
    def is_face(self) -> bool:
        return self.face_index is not None

    @property
    def sw_reference(self) -> str:
        """C# SelectReference 的引用写法。"""
        if self.is_base_plane:
            return f"plane:{self.plane_index}"
        if self.ref_plane_name is not None:
            return f"refplane:{self.ref_plane_name}"
        return f"face:{self.face_index}"

    def describe(self) -> str:
        side = "+" if self.normal_sign > 0 else "-"
        return (
            f"{self.label}: 平面 {self.axis}={self.offset_mm:g}mm，法向 {side}{self.axis}；"
            f"u={self.u_axis}, v={self.v_axis}（模型坐标，mm）"
        )

    def to_model_mm(self, u: float, v: float) -> tuple[float, float, float]:
        point = [0.0, 0.0, 0.0]
        point[_AXIS_INDEX[self.u_axis]] = u
        point[_AXIS_INDEX[self.v_axis]] = v
        point[_AXIS_INDEX[self.axis]] = self.offset_mm
        return tuple(point)  # type: ignore[return-value]

    def point_params(self, u: float, v: float, *, keys=("x", "y", "z")) -> dict:
        """一个 (u, v) 点对应的 C# 参数（米）。"""
        if self.is_base_plane:
            su, sv = _BASE_SKETCH_SIGNS[self.axis]
            return {keys[0]: su * u / 1000.0, keys[1]: sv * v / 1000.0}
        x, y, z = self.to_model_mm(u, v)
        return {keys[0]: x / 1000.0, keys[1]: y / 1000.0, keys[2]: z / 1000.0}

    def boundary_uv(self) -> dict | None:
        """实体面包围盒在 (u, v) 上的投影，作为 check_within 的近似边界。"""
        if not self.face_box_mm:
            return None
        box = self.face_box_mm
        u, v = self.u_axis, self.v_axis
        return {
            "type": "rect",
            "x1": box[f"min{u}"], "y1": box[f"min{v}"],
            "x2": box[f"max{u}"], "y2": box[f"max{v}"],
        }


def base_plane_frame(name: str) -> SketchFrame | None:
    key = BASE_PLANE_ALIASES.get(str(name).strip().lower()) or BASE_PLANE_ALIASES.get(str(name).strip())
    if key is None:
        return None
    index, axis = BASE_PLANES[key]
    return SketchFrame(label=key, axis=axis, normal_sign=1, offset_mm=0.0, plane_index=index)


def ref_plane_frame(name: str, axis: str, normal_sign: int, offset_mm: float) -> SketchFrame:
    """用户新建的轴对齐基准面：草图按模型坐标传入，由 C# 端经 ModelToSketchTransform 换算。"""
    return SketchFrame(label=name, axis=axis, normal_sign=normal_sign, offset_mm=offset_mm, ref_plane_name=name)


def face_frame(face: dict, label: str) -> SketchFrame:
    """由 observation 中的平面（单位米）构造草图坐标系；只支持轴对齐平面。"""
    if face.get("surfaceType") != "plane":
        raise WorkflowError("REF_NOT_PLANAR", f"{label} 不是平面，不能在上面画草图", ref=label)
    normal = face.get("normal") or {}
    values = {axis: float(normal.get(axis.lower(), 0)) for axis in "XYZ"}
    length = math.sqrt(sum(value * value for value in values.values())) or 1.0
    axis = max(values, key=lambda key: abs(values[key]))
    if abs(values[axis]) / length < 0.98:
        raise WorkflowError(
            "REF_NOT_AXIS_ALIGNED", f"{label} 的法向不与坐标轴对齐，暂不支持在斜面上画草图", ref=label,
        )
    center = face.get("center") or {}
    box = face.get("box") or {}
    box_mm = {key: float(value) * 1000.0 for key, value in box.items()} if box else None
    return SketchFrame(
        label=label,
        axis=axis,
        normal_sign=1 if values[axis] > 0 else -1,
        offset_mm=float(center.get(axis.lower(), 0)) * 1000.0,
        face_index=int(face.get("index")),
        face_box_mm=box_mm,
    )
