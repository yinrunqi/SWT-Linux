"""Normalize LLM plans into bounded, atomic high-level operations."""

from __future__ import annotations

from math import isclose

from .schemas import OperationType, SwOperation


def _point(params: dict, suffix: str) -> dict:
    point = {
        "x": float(params[f"x{suffix}"]),
        "y": float(params[f"y{suffix}"]),
    }
    z_key = f"z{suffix}"
    if z_key in params:
        point["z"] = float(params[z_key])
    return point


def _same_point(left: dict, right: dict) -> bool:
    return all(
        isclose(
            float(left.get(axis, 0)),
            float(right.get(axis, 0)),
            abs_tol=1e-9,
        )
        for axis in ("x", "y", "z")
    )


def _collapse_lines(lines: list[SwOperation]) -> SwOperation | None:
    if len(lines) < 2:
        return None
    points = [_point(lines[0].params, "1")]
    current = _point(lines[0].params, "2")
    points.append(current)
    for line in lines[1:]:
        start = _point(line.params, "1")
        end = _point(line.params, "2")
        if not _same_point(current, start):
            return None
        points.append(end)
        current = end
    closed = _same_point(points[0], points[-1])
    if closed:
        points.pop()
    return SwOperation(
        operation=OperationType.SKETCH_POLYLINE,
        params={"points": points, "closed": closed},
        description=f"批量绘制 {len(points)} 点连续轮廓",
    )


def optimize_operations(operations: list[SwOperation]) -> list[SwOperation]:
    """Collapse contiguous sketch primitives without changing plan semantics."""
    optimized: list[SwOperation] = []
    index = 0
    while index < len(operations):
        operation = operations[index]
        if operation.operation == OperationType.SKETCH_LINE:
            end = index + 1
            while (
                end < len(operations)
                and operations[end].operation == OperationType.SKETCH_LINE
            ):
                end += 1
            collapsed = _collapse_lines(operations[index:end])
            if collapsed is not None:
                optimized.append(collapsed)
            else:
                optimized.extend(operations[index:end])
            index = end
            continue
        if operation.operation == OperationType.SKETCH_CIRCLE:
            end = index + 1
            while (
                end < len(operations)
                and operations[end].operation == OperationType.SKETCH_CIRCLE
            ):
                end += 1
            circles = operations[index:end]
            if len(circles) > 1:
                optimized.append(SwOperation(
                    operation=OperationType.SKETCH_CIRCLES,
                    params={
                        "circles": [
                            dict(circle.params)
                            for circle in circles
                        ],
                    },
                    description=f"批量绘制 {len(circles)} 个圆",
                ))
            else:
                optimized.append(operation)
            index = end
            continue
        optimized.append(operation)
        index += 1
    return optimized


_FEATURE_OPERATIONS = {
    OperationType.FEATURE_EXTRUSION,
    OperationType.FEATURE_CUT_EXTRUSION,
    OperationType.FEATURE_REVOLVE,
    OperationType.FEATURE_FILLET,
    OperationType.FEATURE_CHAMFER,
    OperationType.FEATURE_CIRCULAR_PATTERN,
    OperationType.FEATURE_LINEAR_PATTERN,
    OperationType.MODIFY_DIMENSION,
}


def group_feature_batches(
    operations: list[SwOperation],
    *,
    maximum_actions: int = 5,
) -> list[list[SwOperation]]:
    """Group selection/sketch/feature transactions without splitting them."""
    batches: list[list[SwOperation]] = []
    current: list[SwOperation] = []
    for operation in optimize_operations(operations):
        current.append(operation)
        if operation.operation in _FEATURE_OPERATIONS:
            batches.append(current)
            current = []
        elif len(current) >= maximum_actions:
            # Queries or document setup may not terminate in a feature.
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches
