"""参数曲线：模型用 t 的表达式描述精确曲线（渐开线、螺线、凸轮、翼型、变螺距螺旋），系统采样成样条通过点。

表达式语法与参数表相同（params.py 的受限 AST），可以引用已定义的参数；t 是曲线参数。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .errors import WorkflowError
from .params import CURVE_FUNCTIONS, _eval, _parse

SAG_TOLERANCE_MM = 0.02
INITIAL_INTERVALS = 16
MAX_POINTS = 400


def _expr(spec: Mapping[str, Any], key: str, what: str) -> Any:
    raw = spec.get(key)
    if isinstance(raw, (int, float)):
        return _parse(repr(float(raw)))
    if not isinstance(raw, str) or not raw.strip():
        raise WorkflowError("TOOL_ARGS", f"{what}.{key} 应为 t 的表达式字符串，例如 \"r*cos(t)\"")
    text = raw.strip()
    return _parse(text[1:] if text.startswith("=") else text, CURVE_FUNCTIONS)


def _t_range(spec: Mapping[str, Any], what: str) -> tuple[float, float]:
    raw = spec.get("t")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise WorkflowError("TOOL_ARGS", f"{what}.t 应为 [t0, t1]（参数 t 的起止值）")
    t0, t1 = float(raw[0]), float(raw[1])
    if not math.isfinite(t0) or not math.isfinite(t1) or t0 == t1:
        raise WorkflowError("TOOL_ARGS", f"{what}.t 的起止值必须是不相等的有限数")
    return t0, t1


def _offset(spec: Mapping[str, Any], keys: Sequence[str], env: Mapping[str, float], what: str) -> float:
    raw = spec.get("offset")
    if raw in (None, 0):
        return 0.0
    if len(keys) != 2:
        raise WorkflowError("TOOL_ARGS", f"{what}.offset 只用于平面曲线（u, v）")
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(_eval(_parse(str(raw).strip().lstrip("="), CURVE_FUNCTIONS), dict(env), CURVE_FUNCTIONS))
    except WorkflowError:
        raise
    except (ArithmeticError, ValueError) as exc:
        raise WorkflowError("TOOL_ARGS", f"{what}.offset 计算失败: {exc}") from None


def _offset_curve(base, t0: float, t1: float, offset: float, what: str):
    """沿法向等距偏移：开放曲线 offset>0 偏向前进方向左侧；闭合曲线 offset>0 向外、<0 向内（与走向无关）。"""
    span = t1 - t0
    h = span * 1e-6

    def tangent(t: float) -> tuple[float, float]:
        a, b = base(t - h), base(t + h)
        du, dv = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(du, dv)
        if norm <= 1e-12:
            raise WorkflowError("TOOL_ARGS", f"{what} 在 t={t:g} 处切向为零，无法按法向偏移")
        return du / norm, dv / norm

    probe = [base(t0 + span * i / 64) for i in range(65)]
    closed = math.dist(probe[0], probe[-1]) <= 1e-3
    sign = 1.0
    if closed:
        area = sum(probe[i][0] * probe[i + 1][1] - probe[i + 1][0] * probe[i][1] for i in range(64)) / 2
        sign = -1.0 if area > 0 else 1.0
    distance = sign * offset

    def at(t: float) -> tuple:
        p = base(t)
        tu, tv = tangent(t)
        return p[0] - tv * distance, p[1] + tu * distance

    for i in range(256):
        t = t0 + span * (i + 0.5) / 256
        q0, q1 = at(t - span / 1024), at(t + span / 1024)
        tu, tv = tangent(t)
        if (q1[0] - q0[0]) * tu + (q1[1] - q0[1]) * tv <= 0:
            raise WorkflowError(
                "TOOL_ARGS", f"{what}.offset={offset:g} 大于曲线在 t≈{t:g} 处的曲率半径，偏移曲线会自交",
                hint="减小 offset，或检查偏移方向（闭合曲线 offset>0 向外、<0 向内）。",
            )
    return at


def sample(spec: Mapping[str, Any], keys: Sequence[str], env: Mapping[str, float], what: str = "curve") -> list[tuple]:
    """按弦高误差自适应采样，返回从 t0 到 t1 的点列（坐标单位与表达式一致，mm）。"""
    if not isinstance(spec, Mapping):
        raise WorkflowError("TOOL_ARGS", f"{what} 应为对象 {{{', '.join(keys)}, t:[t0, t1]}}")
    trees = [_expr(spec, key, what) for key in keys]
    t0, t1 = _t_range(spec, what)
    scope = dict(env)

    def base(t: float) -> tuple:
        scope["t"] = t
        values = []
        for key, tree in zip(keys, trees):
            try:
                value = _eval(tree, scope, CURVE_FUNCTIONS)
            except WorkflowError:
                raise
            except (ArithmeticError, ValueError) as exc:
                raise WorkflowError("TOOL_ARGS", f"{what}.{key} 在 t={t:g} 处计算失败: {exc}") from None
            if not math.isfinite(value):
                raise WorkflowError("TOOL_ARGS", f"{what}.{key} 在 t={t:g} 处不是有限值")
            values.append(value)
        return tuple(values)

    at = base
    offset = _offset(spec, keys, env, what)
    if offset:
        at = _offset_curve(base, t0, t1, offset, what)

    ts = [t0 + (t1 - t0) * i / INITIAL_INTERVALS for i in range(INITIAL_INTERVALS + 1)]
    points = [at(t) for t in ts]
    while len(points) < MAX_POINTS:
        refined_t, refined_p, split = [ts[0]], [points[0]], False
        for i in range(len(ts) - 1):
            tm = (ts[i] + ts[i + 1]) / 2
            pm = at(tm)
            chord_mid = tuple((a + b) / 2 for a, b in zip(points[i], points[i + 1]))
            if math.dist(pm, chord_mid) > SAG_TOLERANCE_MM and len(points) + len(refined_t) < MAX_POINTS * 2:
                refined_t.append(tm)
                refined_p.append(pm)
                split = True
            refined_t.append(ts[i + 1])
            refined_p.append(points[i + 1])
        ts, points = refined_t, refined_p
        if not split:
            break
    deduped = [points[0]]
    for point in points[1:]:
        if math.dist(point, deduped[-1]) > 1e-6:
            deduped.append(point)
    if len(deduped) < 2:
        raise WorkflowError("SKETCH_DEGENERATE", f"{what} 退化成了一个点")
    if len(deduped) > MAX_POINTS:
        step = (len(deduped) - 1) / (MAX_POINTS - 1)
        deduped = [deduped[round(i * step)] for i in range(MAX_POINTS)]
    return [tuple(round(value, 6) for value in point) for point in deduped]


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))
