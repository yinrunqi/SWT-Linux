"""把工程师写法的草图轮廓转换成预检格式和原子绘图操作。

LLM 侧轮廓（单位 mm，坐标为所在平面的 (u, v)）：
    {"type": "circle", "center": [u, v], "diameter": d}        # 或 "radius"
    {"type": "rect", "center": [u, v], "size": [du, dv]}        # 或 "corner1"/"corner2"
    {"type": "regular_polygon", "center": [u, v], "sides": 6,
     "circumradius": r | "across_flats": s, "angle_deg": 0}     # angle_deg 为第一个顶点相对 +u 的角度
    {"type": "slot", "start": [u, v], "end": [u, v], "width": w} # 两端为半圆
    {"type": "polygon", "points": [[u, v], ...]}                # 自动闭合
"""

from __future__ import annotations

import math
from typing import Sequence

from shapely.geometry import Polygon

from .errors import WorkflowError
from .frames import SketchFrame
from .sketch_check import to_geometry

SLOT_ARC_SEGMENTS = 8


def _pair(value, name: str, index: int) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 {name} 必须是 [u, v]", loop=index)
    return float(value[0]), float(value[1])


def _positive(loop: dict, key: str, index: int) -> float:
    try:
        value = float(loop[key])
    except (KeyError, TypeError, ValueError):
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 缺少数值 {key}", loop=index) from None
    if value <= 0:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 {key} 必须为正", loop=index)
    return value


def normalize_loop(loop: dict, index: int = 0) -> dict:
    """LLM 写法 → sketch_check 的内部写法（x 即 u，y 即 v）。"""
    if not isinstance(loop, dict):
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 必须是对象", loop=index)
    kind = str(loop.get("type", "")).lower()
    if kind == "circle":
        cx, cy = _pair(loop.get("center"), "center", index)
        if "diameter" in loop:
            radius = _positive(loop, "diameter", index) / 2
        else:
            radius = _positive(loop, "radius", index)
        return {"type": "circle", "cx": cx, "cy": cy, "r": radius}
    if kind == "rect":
        if "corner1" in loop or "corner2" in loop:
            x1, y1 = _pair(loop.get("corner1"), "corner1", index)
            x2, y2 = _pair(loop.get("corner2"), "corner2", index)
        else:
            cx, cy = _pair(loop.get("center"), "center", index)
            du, dv = _pair(loop.get("size"), "size", index)
            if du <= 0 or dv <= 0:
                raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 size 必须为正", loop=index)
            x1, y1, x2, y2 = cx - du / 2, cy - dv / 2, cx + du / 2, cy + dv / 2
        return {"type": "rect", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if kind == "regular_polygon":
        cx, cy = _pair(loop.get("center"), "center", index)
        sides = int(loop.get("sides", 6))
        if sides < 3:
            raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 sides 至少为 3", loop=index)
        if "across_flats" in loop:
            radius = _positive(loop, "across_flats", index) / 2 / math.cos(math.pi / sides)
        else:
            radius = _positive(loop, "circumradius", index)
        return {
            "type": "regular_polygon", "cx": cx, "cy": cy, "r": radius,
            "sides": sides, "angle_deg": float(loop.get("angle_deg", 0)),
        }
    if kind == "slot":
        x1, y1 = _pair(loop.get("start"), "start", index)
        x2, y2 = _pair(loop.get("end"), "end", index)
        return {"type": "slot", "x1": x1, "y1": y1, "x2": x2, "y2": y2, "width": _positive(loop, "width", index)}
    if kind == "polygon":
        points = loop.get("points") or []
        return {"type": "polygon", "points": [list(_pair(p, "points[i]", index)) for p in points]}
    raise WorkflowError(
        "TOOL_ARGS",
        f"轮廓 {index} 类型 {kind!r} 不支持，可用 circle/rect/regular_polygon/slot/polygon",
        loop=index,
    )


def normalize_loops(loops: Sequence[dict]) -> list[dict]:
    if not isinstance(loops, (list, tuple)) or not loops:
        raise WorkflowError("TOOL_ARGS", "loops 必须是非空数组")
    return [normalize_loop(loop, index) for index, loop in enumerate(loops)]


def _vertices(loop: dict) -> list[tuple[float, float]]:
    kind = loop["type"]
    if kind == "slot":
        (x1, y1), (x2, y2), half = (loop["x1"], loop["y1"]), (loop["x2"], loop["y2"]), loop["width"] / 2
        heading = math.atan2(y2 - y1, x2 - x1)
        points = []
        for (cx, cy), start in (((x2, y2), heading - math.pi / 2), ((x1, y1), heading + math.pi / 2)):
            for step in range(SLOT_ARC_SEGMENTS + 1):
                angle = start + math.pi * step / SLOT_ARC_SEGMENTS
                points.append((cx + half * math.cos(angle), cy + half * math.sin(angle)))
        return points
    geometry = to_geometry(loop)
    if not isinstance(geometry, Polygon):
        raise WorkflowError("TOOL_ARGS", "轮廓不是闭合多边形")
    return list(geometry.exterior.coords)[:-1]


def draw_operations(frame: SketchFrame, loops: Sequence[dict]) -> list[dict]:
    """已规范化的轮廓 → 原子绘图操作（不含进入草图与特征操作）。"""
    operations: list[dict] = []
    circles = []
    for loop in loops:
        if loop["type"] == "circle":
            center = frame.point_params(loop["cx"], loop["cy"], keys=("cx", "cy", "cz"))
            circles.append({**center, "radius": loop["r"] / 1000.0})
            continue
        points = [frame.point_params(u, v) for u, v in _vertices(loop)]
        operations.append({"operation": "sketch_polyline", "params": {"points": points, "closed": True}})
    if circles:
        operations.insert(0, {"operation": "sketch_circles", "params": {"circles": circles}})
    return operations


def centerline_operation(frame: SketchFrame, start: Sequence[float], end: Sequence[float]) -> dict:
    a = frame.point_params(float(start[0]), float(start[1]), keys=("x1", "y1", "z1"))
    b = frame.point_params(float(end[0]), float(end[1]), keys=("x2", "y2", "z2"))
    return {"operation": "sketch_centerline", "params": {**a, **b}}
