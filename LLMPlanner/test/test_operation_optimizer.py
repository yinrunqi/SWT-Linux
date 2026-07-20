"""Tests for atomic operation normalization and blueprint decomposition."""

from __future__ import annotations

from planner.operation_optimizer import (
    group_feature_batches,
    optimize_operations,
)
from planner.schemas import OperationType, SwOperation, ThreeViewIR
from planner.task_decomposer import TaskDecomposer


def op(kind: OperationType, **params) -> SwOperation:
    return SwOperation(operation=kind, params=params)


def test_closed_line_chain_becomes_one_polyline() -> None:
    operations = [
        op(OperationType.SKETCH_LINE, x1=0, y1=0, x2=0.06, y2=0),
        op(OperationType.SKETCH_LINE, x1=0.06, y1=0, x2=0.06, y2=0.02),
        op(OperationType.SKETCH_LINE, x1=0.06, y1=0.02, x2=0.03, y2=0.02),
        op(OperationType.SKETCH_LINE, x1=0.03, y1=0.02, x2=0.03, y2=0.04),
        op(OperationType.SKETCH_LINE, x1=0.03, y1=0.04, x2=0, y2=0.04),
        op(OperationType.SKETCH_LINE, x1=0, y1=0.04, x2=0, y2=0),
    ]

    optimized = optimize_operations(operations)

    assert len(optimized) == 1
    assert optimized[0].operation == OperationType.SKETCH_POLYLINE
    assert optimized[0].params["closed"] is True
    assert len(optimized[0].params["points"]) == 6


def test_circles_are_batched_and_feature_transactions_stay_atomic() -> None:
    operations = [
        op(OperationType.NEW_PART),
        op(OperationType.SELECT_PLANE, index=0),
        op(
            OperationType.SKETCH_RECTANGLE,
            x1=-0.04,
            y1=-0.02,
            x2=0.04,
            y2=0.02,
        ),
        op(OperationType.FEATURE_EXTRUSION, depth=0.01),
        op(
            OperationType.SELECT_FACE_AND_SKETCH,
            rx=0,
            ry=0,
            rz=0.02,
            dx=0,
            dy=0,
            dz=-1,
        ),
        op(OperationType.SKETCH_CIRCLE, cx=-0.02, cy=0, cz=0.01, radius=0.005),
        op(OperationType.SKETCH_CIRCLE, cx=0.02, cy=0, cz=0.01, radius=0.005),
        op(
            OperationType.FEATURE_CUT_EXTRUSION,
            depth=0.01,
            endCondition="through_all",
        ),
    ]

    batches = group_feature_batches(operations)

    assert len(batches) == 2
    assert len(batches[0]) == 4
    assert len(batches[1]) == 3
    assert batches[1][1].operation == OperationType.SKETCH_CIRCLES


def test_blueprint_milestones_track_cumulative_features_and_final_geometry() -> None:
    batches = [
        [
            op(OperationType.NEW_PART),
            op(OperationType.SELECT_PLANE, index=0),
            op(
                OperationType.SKETCH_RECTANGLE,
                x1=0,
                y1=0,
                x2=0.08,
                y2=0.04,
            ),
            op(OperationType.FEATURE_EXTRUSION, depth=0.01),
        ],
        [
            op(OperationType.SELECT_FACE_AND_SKETCH),
            op(OperationType.SKETCH_CIRCLES, circles=[]),
            op(
                OperationType.FEATURE_CUT_EXTRUSION,
                depth=0.01,
                endCondition="through_all",
            ),
        ],
    ]

    plan = TaskDecomposer().decompose_blueprint(
        "带孔平板",
        batches,
        expected_dims_mm=[80, 40, 10],
        expected_volume_mm3=30429.204,
    )

    assert len(plan.milestones) == 2
    assert plan.milestones[0].postconditions[0].params["keyword"] == "extr"
    final_kinds = {
        condition.kind for condition in plan.milestones[-1].postconditions
    }
    assert {"feature_count", "valid_entity", "bbox_axes", "volume_range"} <= final_kinds


def test_three_view_ir_accepts_common_size_array_shorthand() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "switch_panel",
        "overall_size_mm": [86, 86, 10],
    })

    assert ir.overall_size_mm == {
        "length": 86,
        "height": 86,
        "depth": 10,
    }
