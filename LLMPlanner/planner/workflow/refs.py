"""语义面引用：让 LLM 说 "base.top" / "face(+X, largest)" / "H1"，而不是猜射线坐标。

解析只依赖 observation.faces（index、surfaceType、center、normal、area，单位米），
是纯函数。服务端返回的平面 normal 是实体外法向（IFace2.Normal），
所以 top 就是“外法向朝 +Y 的平面中最靠 +Y 的那个”，薄底板加高凸台时也不会误判。

SolidWorks 默认坐标：前视 = +Z，上视 = +Y，右视 = +X。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import WorkflowError

SIDE_ALIASES = {
    "top": "+Y", "bottom": "-Y",
    "front": "+Z", "back": "-Z",
    "right": "+X", "left": "-X",
    "上": "+Y", "下": "-Y", "前": "+Z", "后": "-Z", "右": "+X", "左": "-X",
}
SELECTORS = ("outermost", "innermost", "largest", "smallest")

_FACE_CALL = re.compile(
    r"^(?:(?P<owner>[A-Za-z_][\w]*)\.)?face\(\s*(?P<axis>[+-][XYZ])\s*(?:,\s*(?P<sel>\w+)\s*)?\)$",
    re.IGNORECASE,
)
_SIDE = re.compile(r"^(?:(?P<owner>[A-Za-z_][\w]*)\.)?(?P<side>top|bottom|front|back|left|right)$", re.IGNORECASE)
_INDEX = re.compile(r"^face#(?P<index>\d+)$", re.IGNORECASE)

_AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}
AXIS_TOLERANCE = 0.98  # |n·axis| 下限，约 11°


@dataclass(frozen=True)
class ResolvedRef:
    ref: str
    face_index: int
    face: dict
    reason: str

    def to_operation(self) -> dict:
        """转成现有原子操作 select_observed_face_and_sketch 的参数。"""
        return {
            "operation": "select_observed_face_and_sketch",
            "params": {"faceIndex": self.face_index},
            "description": f"语义引用 {self.ref}: {self.reason}",
        }


def _vec(value: dict | Sequence[float] | None) -> tuple[float, float, float]:
    if value is None:
        return (0.0, 0.0, 0.0)
    if isinstance(value, dict):
        return (float(value.get("x", 0)), float(value.get("y", 0)), float(value.get("z", 0)))
    return tuple(float(v) for v in value)  # type: ignore[return-value]


def _facing(face: dict, axis: int, sign: float) -> bool:
    if str(face.get("surfaceType", "plane")) != "plane":
        return False
    normal = _vec(face.get("normal"))
    length = math.sqrt(sum(v * v for v in normal)) or 1.0
    return sign * normal[axis] / length >= AXIS_TOLERANCE


def _select(
    faces: Sequence[dict],
    axis_key: str,
    selector: str,
    *,
    ref: str,
    tolerance: float = 1e-6,
) -> ResolvedRef:
    sign = 1.0 if axis_key[0] == "+" else -1.0
    axis = _AXIS_INDEX[axis_key[1].upper()]
    candidates = [face for face in faces if _facing(face, axis, sign)]
    if not candidates:
        raise WorkflowError("REF_NOT_FOUND", f"{ref}: 没有外法向朝 {axis_key} 的平面", ref=ref)

    def offset(face: dict) -> float:
        return sign * _vec(face.get("center"))[axis]

    def area(face: dict) -> float:
        return float(face.get("area") or 0)

    # outermost/innermost 先按离包围盒中心的距离筛选，同一高度的多个面再取面积最大者。
    if selector in ("outermost", "innermost"):
        pick = max if selector == "outermost" else min
        extreme = pick(offset(face) for face in candidates)
        pool = sorted(
            (face for face in candidates if abs(offset(face) - extreme) <= tolerance),
            key=area,
            reverse=True,
        )
    elif selector in ("largest", "smallest"):
        pool = sorted(candidates, key=area, reverse=selector == "largest")
    else:
        raise WorkflowError("REF_SYNTAX", f"{ref}: 未知选择器 {selector!r}，可用 {SELECTORS}", ref=ref)

    best = pool[0]
    ties = [
        face for face in pool[1:]
        if abs(area(face) - area(best)) <= max(1e-12, 1e-6 * area(best))
    ]
    if ties:
        raise WorkflowError(
            "REF_AMBIGUOUS", f"{ref}: {len(ties) + 1} 个面同样满足 {axis_key} {selector}",
            ref=ref, candidates=[best.get("index")] + [face.get("index") for face in ties],
        )
    return ResolvedRef(ref, int(best.get("index")), best, f"{axis_key} {selector}")


def resolve_ref(
    ref: str,
    faces: Sequence[dict],
    registry: "RefRegistry | None" = None,
) -> ResolvedRef:
    """解析一条语义引用。

    支持：
      top/bottom/front/back/left/right（可带前缀 base.）→ 该侧最外侧平面；
      face(+X[, outermost|innermost|largest|smallest])；
      face#12 → 直接用 observation.faces 的 index；
      命名引用（如 H1、boss.top_face）→ 由 RefRegistry 按几何签名重新定位。
    """
    text = str(ref).strip()
    if not faces:
        raise WorkflowError("REF_NOT_FOUND", f"{text}: 当前模型没有可引用的面", ref=text)
    match = _INDEX.match(text)
    if match:
        index = int(match.group("index"))
        for face in faces:
            if int(face.get("index", -1)) == index:
                return ResolvedRef(text, index, face, "显式 index")
        raise WorkflowError("REF_NOT_FOUND", f"{text}: 不存在 index={index} 的面", ref=text)
    match = _SIDE.match(text)
    if match:
        return _select(faces, SIDE_ALIASES[match.group("side").lower()], "outermost", ref=text)
    if text in SIDE_ALIASES:
        return _select(faces, SIDE_ALIASES[text], "outermost", ref=text)
    match = _FACE_CALL.match(text)
    if match:
        selector = (match.group("sel") or "outermost").lower()
        return _select(faces, match.group("axis").upper(), selector, ref=text)
    if registry is not None and text in registry:
        return registry.resolve(text, faces)
    if registry is not None:
        raise WorkflowError("REF_UNBOUND", f"{text}: 命名引用尚未绑定", ref=text)
    raise WorkflowError("REF_SYNTAX", f"无法解析引用 {text!r}", ref=text)


@dataclass
class RefRegistry:
    """把名字绑定到某个面的几何签名；模型重建后按签名最近邻重新定位。"""

    bindings: dict[str, dict] = field(default_factory=dict)

    def __contains__(self, name: str) -> bool:
        return name in self.bindings

    def bind(self, name: str, face: dict) -> None:
        self.bindings[name] = {
            "surfaceType": face.get("surfaceType"),
            "center": _vec(face.get("center")),
            "normal": _vec(face.get("normal")),
            "area": float(face.get("area") or 0),
        }

    def bind_ref(self, name: str, ref: str, faces: Sequence[dict]) -> ResolvedRef:
        resolved = resolve_ref(ref, faces, self)
        self.bind(name, resolved.face)
        return resolved

    def resolve(self, name: str, faces: Iterable[dict], *, max_distance_m: float = 0.002) -> ResolvedRef:
        if name not in self.bindings:
            raise WorkflowError("REF_UNBOUND", f"{name}: 命名引用尚未绑定", ref=name)
        target = self.bindings[name]
        best, best_cost = None, math.inf
        for face in faces:
            if face.get("surfaceType") != target["surfaceType"]:
                continue
            normal = _vec(face.get("normal"))
            if abs(sum(a * b for a, b in zip(normal, target["normal"]))) < AXIS_TOLERANCE:
                continue
            distance = math.dist(_vec(face.get("center")), target["center"])
            area = float(face.get("area") or 0)
            area_ratio = abs(math.log((area + 1e-12) / (target["area"] + 1e-12)))
            cost = distance + 0.001 * area_ratio
            if cost < best_cost:
                best, best_cost = face, cost
        if best is None or math.dist(_vec(best.get("center")), target["center"]) > max_distance_m:
            raise WorkflowError("REF_NOT_FOUND", f"{name}: 模型变化后找不到对应的面", ref=name)
        return ResolvedRef(name, int(best.get("index")), best, "命名引用（几何签名匹配）")
