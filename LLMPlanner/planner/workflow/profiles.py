"""把工程师写法的草图轮廓转换成预检格式和原子绘图操作。

LLM 侧轮廓（单位 mm，坐标为所在平面的 (u, v)）：
    {"type": "circle", "center": [u, v], "diameter": d}        # 或 "radius"
    {"type": "rect", "center": [u, v], "size": [du, dv]}        # 或 "corner1"/"corner2"；可加 "corner_radius"
    {"type": "regular_polygon", "center": [u, v], "sides": 6,
     "circumradius": r | "across_flats": s, "angle_deg": 0}     # angle_deg 为第一个顶点相对 +u 的角度；可加 "corner_radius"
    {"type": "slot", "start": [u, v], "end": [u, v], "width": w} # 两端为半圆
    {"type": "polygon", "points": [[u, v], ...]}                # 自动闭合；可加 "corner_radius" 或逐点 "corner_radii"
    {"type": "annular_sector", "center": [u, v], "r_inner": a, "r_outer": b,
     "start_deg": s, "end_deg": e}                              # 扇环，r_inner=0 为扇形
    {"type": "path", "start": [u, v], "segments": [{"to": [u, v]}, {"to": [u, v], "center": [u, v]}, ...]}
                                                                # 直线与圆弧组成的任意闭合轮廓，见 curves.path
"""

from __future__ import annotations

import math
from typing import Sequence

from shapely.geometry import Polygon

from . import curves
from .errors import WorkflowError
from .frames import SketchFrame
from .sketch_check import to_geometry

PROFILE_ROUTE = "/api/sketch/profile"


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


def _corner_radii(loop: dict, count: int, index: int) -> list[float] | None:
    if "corner_radii" in loop:
        radii = loop["corner_radii"]
        if not isinstance(radii, (list, tuple)) or len(radii) != count:
            raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 corner_radii 应为 {count} 个数", loop=index)
        values = [float(value) for value in radii]
    elif loop.get("corner_radius") not in (None, 0):
        values = [float(loop["corner_radius"])] * count
    else:
        return None
    if any(value < 0 for value in values):
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的圆角半径不能为负", loop=index)
    return values if any(value > 0 for value in values) else None


def _path(segments: list[dict]) -> dict:
    return {"type": "path", "segments": segments}


def _polygon_points(normalized: dict) -> list[tuple[float, float]]:
    return list(to_geometry(normalized).exterior.coords)[:-1]


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
        rect = {"type": "rect", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
        radii = _corner_radii(loop, 4, index)
        if radii is None:
            return rect
        lo_u, hi_u, lo_v, hi_v = min(x1, x2), max(x1, x2), min(y1, y2), max(y1, y2)
        corners = [(lo_u, lo_v), (hi_u, lo_v), (hi_u, hi_v), (lo_u, hi_v)]
        return _path(curves.rounded_polygon(corners, radii, index))
    if kind == "regular_polygon":
        cx, cy = _pair(loop.get("center"), "center", index)
        sides = int(loop.get("sides", 6))
        if sides < 3:
            raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 sides 至少为 3", loop=index)
        if "across_flats" in loop:
            radius = _positive(loop, "across_flats", index) / 2 / math.cos(math.pi / sides)
        else:
            radius = _positive(loop, "circumradius", index)
        polygon = {
            "type": "regular_polygon", "cx": cx, "cy": cy, "r": radius,
            "sides": sides, "angle_deg": float(loop.get("angle_deg", 0)),
        }
        radii = _corner_radii(loop, sides, index)
        if radii is None:
            return polygon
        return _path(curves.rounded_polygon(_polygon_points(polygon), radii, index))
    if kind == "slot":
        start = _pair(loop.get("start"), "start", index)
        end = _pair(loop.get("end"), "end", index)
        return _path(curves.slot(start, end, _positive(loop, "width", index), index))
    if kind == "polygon":
        points = [_pair(p, "points[i]", index) for p in loop.get("points") or []]
        if len(points) >= 2 and math.dist(points[0], points[-1]) <= 1e-9:
            points = points[:-1]
        radii = _corner_radii(loop, len(points), index)
        if radii is None:
            return {"type": "polygon", "points": [list(p) for p in points]}
        return _path(curves.rounded_polygon(points, radii, index))
    if kind == "annular_sector":
        center = _pair(loop.get("center"), "center", index)
        r_inner = float(loop.get("r_inner", 0) or 0)
        return _path(curves.annular_sector(
            center, r_inner, _positive(loop, "r_outer", index),
            float(loop.get("start_deg", 0)), float(loop.get("end_deg", 90)), index,
        ))
    if kind == "path":
        start = _pair(loop.get("start"), "start", index)
        steps = loop.get("segments")
        if not isinstance(steps, (list, tuple)):
            raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 segments 必须是数组", loop=index)
        return _path(curves.path(
            start, steps, index, pair=lambda value, name: _pair(value, name, index),
            closed=bool(loop.get("closed", True)),
        ))
    raise WorkflowError(
        "TOOL_ARGS",
        f"轮廓 {index} 类型 {kind!r} 不支持，可用 circle/rect/regular_polygon/slot/polygon/annular_sector/path",
        loop=index,
    )


def normalize_loops(loops: Sequence[dict]) -> list[dict]:
    if not isinstance(loops, (list, tuple)) or not loops:
        raise WorkflowError("TOOL_ARGS", "loops 必须是非空数组")
    return [normalize_loop(loop, index) for index, loop in enumerate(loops)]


def _vertices(loop: dict) -> list[tuple[float, float]]:
    geometry = to_geometry(loop)
    if not isinstance(geometry, Polygon):
        raise WorkflowError("TOOL_ARGS", "轮廓不是闭合多边形")
    return list(geometry.exterior.coords)[:-1]


def _profile_segment(frame: SketchFrame, segment: dict) -> dict:
    start = frame.point_params(*segment["p0"], keys=("x1", "y1", "z1"))
    end = frame.point_params(*segment["p1"], keys=("x2", "y2", "z2"))
    if segment["kind"] == "line":
        return {"type": "line", **start, **end}
    mid = frame.point_params(*curves.arc_point(segment, 0.5), keys=("xm", "ym", "zm"))
    return {"type": "arc", **start, **end, **mid}


def draw_operations(frame: SketchFrame, loops: Sequence[dict]) -> list[dict]:
    """已规范化的轮廓 → 原子绘图操作（不含进入草图与特征操作）。"""
    operations: list[dict] = []
    circles = []
    for loop in loops:
        if loop["type"] == "circle":
            center = frame.point_params(loop["cx"], loop["cy"], keys=("cx", "cy", "cz"))
            circles.append({**center, "radius": loop["r"] / 1000.0})
            continue
        if loop["type"] == "path":
            segments = [_profile_segment(frame, segment) for segment in loop["segments"]]
            operations.append({
                "operation": "sketch_profile", "route": PROFILE_ROUTE,
                "params": {"segments": segments, "closed": bool(loop.get("closed", True))},
            })
            continue
        points = [frame.point_params(u, v) for u, v in _vertices(loop)]
        operations.append({"operation": "sketch_polyline", "params": {"points": points, "closed": True}})
    if circles:
        operations.insert(0, {"operation": "sketch_circles", "params": {"circles": circles}})
    return operations


def draw_open_path(frame: SketchFrame, start: Sequence[float], steps: Sequence[dict]) -> list[dict]:
    """开放路径（筋、扫描路径）：不预检闭合，不自动补边。"""
    segments = curves.path(
        (float(start[0]), float(start[1])), steps, closed=False,
        pair=lambda value, name: _pair(value, name, 0),
    )
    return [{
        "operation": "sketch_profile", "route": PROFILE_ROUTE,
        "params": {"segments": [_profile_segment(frame, segment) for segment in segments], "closed": False},
    }]


def centerline_operation(frame: SketchFrame, start: Sequence[float], end: Sequence[float]) -> dict:
    a = frame.point_params(float(start[0]), float(start[1]), keys=("x1", "y1", "z1"))
    b = frame.point_params(float(end[0]), float(end[1]), keys=("x2", "y2", "z2"))
    return {"operation": "sketch_centerline", "params": {**a, **b}}
