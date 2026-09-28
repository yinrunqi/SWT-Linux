"""由直线和圆弧组成的闭合轮廓（单位 mm，平面 (u, v) 坐标）。

内部段格式：
    {"kind": "line", "p0": (u, v), "p1": (u, v)}
    {"kind": "arc",  "p0": (u, v), "p1": (u, v), "c": (u, v), "ccw": bool}
圆弧从 p0 绕圆心 c 转到 p1，ccw 为 (u, v) 平面内的逆时针。发给 SolidWorks 时
转成「起点 + 终点 + 弧上中点」的三点圆弧，与各平面草图坐标的手性无关。
"""

from __future__ import annotations

import math
from typing import Sequence

from .errors import WorkflowError

Point = tuple[float, float]
ARC_STEP_DEG = 5.0
LENGTH_EPS = 1e-6


def _sub(a: Point, b: Point) -> Point:
    return a[0] - b[0], a[1] - b[1]


def _norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def _unit(a: Point, index: int) -> Point:
    length = _norm(a)
    if length <= LENGTH_EPS:
        raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index} 有长度为零的边", loop=index)
    return a[0] / length, a[1] / length


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def line(p0: Point, p1: Point) -> dict:
    return {"kind": "line", "p0": tuple(p0), "p1": tuple(p1)}


def arc(p0: Point, p1: Point, center: Point, ccw: bool) -> dict:
    return {"kind": "arc", "p0": tuple(p0), "p1": tuple(p1), "c": tuple(center), "ccw": bool(ccw)}


def arc_sweep(segment: dict) -> float:
    """圆弧转过的角度（弧度，0 < sweep ≤ 2π）。"""
    c = segment["c"]
    a0 = math.atan2(segment["p0"][1] - c[1], segment["p0"][0] - c[0])
    a1 = math.atan2(segment["p1"][1] - c[1], segment["p1"][0] - c[0])
    sweep = (a1 - a0) if segment["ccw"] else (a0 - a1)
    sweep %= 2 * math.pi
    return sweep if sweep > 1e-9 else 2 * math.pi


def arc_point(segment: dict, fraction: float) -> Point:
    c = segment["c"]
    radius = _norm(_sub(segment["p0"], c))
    a0 = math.atan2(segment["p0"][1] - c[1], segment["p0"][0] - c[0])
    angle = a0 + (1 if segment["ccw"] else -1) * arc_sweep(segment) * fraction
    return c[0] + radius * math.cos(angle), c[1] + radius * math.sin(angle)


def discretize(segments: Sequence[dict]) -> list[Point]:
    """闭合段序列 → 多边形顶点（不重复首点），供 shapely 预检。"""
    points: list[Point] = []
    for segment in segments:
        points.append(tuple(segment["p0"]))
        if segment["kind"] == "arc":
            steps = max(4, math.ceil(math.degrees(arc_sweep(segment)) / ARC_STEP_DEG))
            points.extend(arc_point(segment, i / steps) for i in range(1, steps))
    return points


def rounded_polygon(points: Sequence[Point], radii: Sequence[float], index: int = 0) -> list[dict]:
    """多边形各顶点按给定半径倒圆（0 表示尖角），返回闭合段序列。"""
    n = len(points)
    if n < 3:
        raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index} 至少需要 3 个顶点", loop=index)
    if len(radii) != n:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 corner_radii 个数应为 {n}", loop=index)
    corners = []
    for i in range(n):
        prev, here, nxt = points[i - 1], points[i], points[(i + 1) % n]
        d_in = _unit(_sub(here, prev), index)
        d_out = _unit(_sub(nxt, here), index)
        radius = float(radii[i])
        turn = math.atan2(_cross(d_in, d_out), d_in[0] * d_out[0] + d_in[1] * d_out[1])
        if radius <= 0 or abs(turn) < 1e-9:
            corners.append((here, here, None, None))
            continue
        tangent = radius * math.tan(abs(turn) / 2)
        t1 = (here[0] - d_in[0] * tangent, here[1] - d_in[1] * tangent)
        t2 = (here[0] + d_out[0] * tangent, here[1] + d_out[1] * tangent)
        left = 1 if turn > 0 else -1
        normal = (-d_in[1] * left, d_in[0] * left)
        center = (t1[0] + normal[0] * radius, t1[1] + normal[1] * radius)
        corners.append((t1, t2, center, turn > 0))
    for i in range(n):
        edge = _norm(_sub(points[(i + 1) % n], points[i]))
        used = _norm(_sub(corners[i][1], points[i])) + _norm(_sub(points[(i + 1) % n], corners[(i + 1) % n][0]))
        if used > edge + 1e-6:
            raise WorkflowError(
                "SKETCH_DEGENERATE",
                f"轮廓 {index} 第 {i} 条边长 {edge:.3f}mm，放不下两端圆角（需要 {used:.3f}mm），请减小 corner_radius",
                loop=index,
            )
    segments: list[dict] = []
    for i in range(n):
        t1, t2, center, ccw = corners[i]
        if center is not None:
            segments.append(arc(t1, t2, center, ccw))
        nxt_start = corners[(i + 1) % n][0]
        if _norm(_sub(nxt_start, t2)) > LENGTH_EPS:
            segments.append(line(t2, nxt_start))
    return segments


def slot(start: Point, end: Point, width: float, index: int = 0) -> list[dict]:
    """两端半圆的直槽口：两条直线 + 两个半圆弧。"""
    d = _unit(_sub(end, start), index)
    half = width / 2
    n = (-d[1] * half, d[0] * half)
    a = (start[0] - n[0], start[1] - n[1])
    b = (end[0] - n[0], end[1] - n[1])
    c = (end[0] + n[0], end[1] + n[1])
    e = (start[0] + n[0], start[1] + n[1])
    return [line(a, b), arc(b, c, end, True), line(c, e), arc(e, a, start, True)]


def annular_sector(
    center: Point, r_inner: float, r_outer: float, start_deg: float, end_deg: float, index: int = 0,
) -> list[dict]:
    """扇环（r_inner=0 时为扇形），角度从 start_deg 逆时针到 end_deg。"""
    if r_outer <= r_inner or r_inner < 0:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 需要 0 ≤ r_inner < r_outer", loop=index)
    sweep = (end_deg - start_deg) % 360
    if sweep <= 1e-6 or sweep >= 360 - 1e-6:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的张角必须在 (0, 360) 度之间", loop=index)
    a0, a1 = math.radians(start_deg), math.radians(start_deg + sweep)

    def at(radius: float, angle: float) -> Point:
        return center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)

    outer0, outer1 = at(r_outer, a0), at(r_outer, a1)
    segments = [arc(outer0, outer1, center, True)]
    if r_inner > 0:
        inner1, inner0 = at(r_inner, a1), at(r_inner, a0)
        segments += [line(outer1, inner1), arc(inner1, inner0, center, False), line(inner0, outer0)]
    else:
        segments += [line(outer1, center), line(center, outer0)]
    return segments


def _circumcenter(a: Point, b: Point, c: Point, index: int) -> Point:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index} 的圆弧三点共线", loop=index)
    sa, sb, sc = (p[0] ** 2 + p[1] ** 2 for p in (a, b, c))
    return (
        (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d,
        (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d,
    )


def _end_tangent(segment: dict) -> Point:
    if segment["kind"] == "line":
        return _unit(_sub(segment["p1"], segment["p0"]), 0)
    radial = _unit(_sub(segment["p1"], segment["c"]), 0)
    return (-radial[1], radial[0]) if segment["ccw"] else (radial[1], -radial[0])


def path(start: Point, steps: Sequence[dict], index: int = 0, pair=None) -> list[dict]:
    """LLM 写法的路径 → 闭合段序列；末点不回到起点时自动补一条直线。

    每步 {"to": [u, v]} 为直线，另加以下之一则为圆弧：
        "center": [u, v]（可带 "ccw"，缺省取不超过 180° 的那段）
        "radius": r（可带 "ccw"，默认 true；"large": true 取大于 180° 的那段）
        "through": [u, v]（三点圆弧）
        "tangent": true（与上一段相切）
    """
    pair = pair or (lambda value, name: (float(value[0]), float(value[1])))
    if not steps:
        raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的 segments 不能为空", loop=index)
    segments: list[dict] = []
    cursor = tuple(start)
    for step_index, step in enumerate(steps):
        if not isinstance(step, dict) or "to" not in step:
            raise WorkflowError(
                "TOOL_ARGS", f"轮廓 {index} 第 {step_index} 段缺少 to", loop=index,
                hint="圆弧段同样要写终点 to，再用 through/center/radius/tangent 之一说明弧形，例如从 [0,0] 出发的 {to:[20,0], through:[10,10]}",
            )
        target = pair(step["to"], "to")
        if _norm(_sub(target, cursor)) <= LENGTH_EPS:
            raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index} 第 {step_index} 段长度为零", loop=index)
        if "center" in step:
            center = pair(step["center"], "center")
            r0, r1 = _norm(_sub(cursor, center)), _norm(_sub(target, center))
            if abs(r0 - r1) > max(1e-3, 1e-4 * r0):
                raise WorkflowError(
                    "SKETCH_DEGENERATE",
                    f"轮廓 {index} 第 {step_index} 段：起点和终点到圆心的距离不同（{r0:.3f} vs {r1:.3f}mm）",
                    loop=index,
                )
            if "ccw" in step:
                ccw = bool(step["ccw"])
            else:
                ccw = _cross(_sub(cursor, center), _sub(target, center)) > 0
            segments.append(arc(cursor, target, center, ccw))
        elif "radius" in step:
            radius = float(step["radius"])
            chord = _norm(_sub(target, cursor))
            if radius <= 0 or chord > 2 * radius + 1e-9:
                raise WorkflowError(
                    "SKETCH_DEGENERATE",
                    f"轮廓 {index} 第 {step_index} 段：半径 {radius}mm 小于弦长的一半（{chord / 2:.3f}mm）",
                    loop=index,
                )
            ccw = bool(step.get("ccw", True))
            large = bool(step.get("large", False))
            mid = ((cursor[0] + target[0]) / 2, (cursor[1] + target[1]) / 2)
            d = _unit(_sub(target, cursor), index)
            height = math.sqrt(max(radius * radius - (chord / 2) ** 2, 0.0))
            side = 1 if ccw != large else -1
            center = (mid[0] - d[1] * height * side, mid[1] + d[0] * height * side)
            segments.append(arc(cursor, target, center, ccw))
        elif "through" in step:
            through = pair(step["through"], "through")
            center = _circumcenter(cursor, through, target, index)
            ccw = _cross(_sub(through, cursor), _sub(target, through)) > 0
            segments.append(arc(cursor, target, center, ccw))
        elif step.get("tangent"):
            if not segments:
                raise WorkflowError("TOOL_ARGS", f"轮廓 {index} 的第一段不能是 tangent 圆弧", loop=index)
            tangent = _end_tangent(segments[-1])
            normal = (-tangent[1], tangent[0])
            chord = _sub(target, cursor)
            denominator = 2 * (normal[0] * chord[0] + normal[1] * chord[1])
            if abs(denominator) < 1e-9:
                segments.append(line(cursor, target))
            else:
                signed = (chord[0] ** 2 + chord[1] ** 2) / denominator
                center = (cursor[0] + normal[0] * signed, cursor[1] + normal[1] * signed)
                segments.append(arc(cursor, target, center, signed > 0))
        else:
            segments.append(line(cursor, target))
        cursor = target
    if _norm(_sub(cursor, tuple(start))) > LENGTH_EPS:
        segments.append(line(cursor, tuple(start)))
    if len(segments) < 2 and segments[0]["kind"] == "line":
        raise WorkflowError("SKETCH_DEGENERATE", f"轮廓 {index} 不能围成区域", loop=index)
    return segments
