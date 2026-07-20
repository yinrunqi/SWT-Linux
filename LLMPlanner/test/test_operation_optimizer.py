"""Tests for atomic operation normalization and blueprint decomposition."""

from __future__ import annotations

from planner.operation_optimizer import (
    group_feature_batches,
    optimize_operations,
    stabilize_three_view_blueprint,
)
from planner.schemas import (
    OperationType,
    SwModelingPlan,
    SwOperation,
    ThreeViewIR,
)
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


def test_three_view_ir_normalizes_common_provider_aliases() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "extrude_prism",
        "profiles": [{"view": "front", "description": "凹多边形"}],
        "dimensions": [{"name": "overall_width", "value": 60}],
        "features": [{
            "name": "notch",
            "type": "pocket",
            "location": "右上角",
            "size": "30x20mm",
        }],
    })

    assert ir.profiles[0].view_name == "front"
    assert ir.profiles[0].base_plane == "front"
    assert ir.dimensions[0].label == "overall_width"
    assert ir.dimensions[0].value_mm == 60
    assert ir.features[0].kind == "pocket"
    assert ir.features[0].location_desc == "右上角"

    compact = ThreeViewIR.model_validate({
        "type": "l bracket",
        "views": {"front": "L-shaped concave outline"},
        "measurements": [{
            "value": 30,
            "unit": "mm",
            "applies_to": "notch.width",
        }],
        "features": [{
            "type": "hole",
            "quantity": 2,
            "centers": [{"x": 20, "y": 20}, {"x": 60, "y": 20}],
        }, {
            "type": "fillet",
            "locations_mm": ["front_left", "front_right"],
        }],
    })
    assert compact.part_type == "l_bracket"
    assert compact.profiles[0].outline_desc == "L-shaped concave outline"
    assert compact.dimensions[0].label == "notch.width"
    assert compact.features[0].count == 2
    assert len(compact.features[0].locations_mm) == 2
    assert compact.features[1].locations_mm[0]["description"] == "front_left"


def test_incomplete_concave_blueprint_is_rebuilt_from_ir_dimensions() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "l_bracket",
        "base_view": "front",
        "overall_size_mm": {"length": 60, "height": 40, "depth": 20},
        "dimensions": [
            {"label": "notch_width", "value_mm": 30},
            {"label": "notch_height", "value_mm": 20},
        ],
    })
    malformed = SwModelingPlan(
        user_intent="创建凹多边形棱柱",
        steps=[
            op(OperationType.NEW_PART),
            op(OperationType.SELECT_PLANE, index=0),
            op(OperationType.SKETCH_POLYGON, sides=6, circumradius=0.03),
        ],
    )

    stabilized = stabilize_three_view_blueprint(malformed, ir)

    assert [step.operation for step in stabilized.steps] == [
        OperationType.NEW_PART,
        OperationType.SELECT_PLANE,
        OperationType.SKETCH_POLYLINE,
        OperationType.FEATURE_EXTRUSION,
    ]
    assert stabilized.steps[2].params["points"][3] == {"x": 0.03, "y": 0.02}
    assert stabilized.steps[3].params["depth"] == 0.02


def test_modeling_plan_normalizes_common_provider_aliases() -> None:
    plan = SwModelingPlan.model_validate({
        "intent": "创建主体",
        "operations": [{
            "action": "/api/sketch/polyline",
            "parameters": {
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 0}],
                "closed": False,
            },
            "comment": "连续折线",
        }],
    })

    assert plan.user_intent == "创建主体"
    assert plan.steps[0].operation == OperationType.SKETCH_POLYLINE
    assert plan.steps[0].description == "连续折线"


def test_through_all_cut_gets_required_transport_depth_from_base() -> None:
    ir = ThreeViewIR(
        part_type="plate_with_holes",
        overall_size_mm={"length": 80, "height": 40, "depth": 10},
    )
    plan = SwModelingPlan(
        user_intent="创建带孔平板",
        steps=[
            op(OperationType.FEATURE_EXTRUSION, depth=0.01),
            op(OperationType.FEATURE_CUT_EXTRUSION, endCondition="through_all"),
        ],
    )

    stabilized = stabilize_three_view_blueprint(plan, ir)

    assert stabilized.steps[1].params["depth"] == 0.01


def test_t_profile_is_derived_from_generic_outline_relationships() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "other",
        "base_view": "front",
        "overall_size_mm": {"length": 60, "height": 45, "depth": 15},
        "profiles": [{
            "view": "front",
            "shape": "T-shaped profile with centered vertical stem",
        }],
        "dimensions": [
            {"label": "stem_width", "value_mm": 20},
            {"label": "stem_height", "value_mm": 30},
        ],
    })
    plan = SwModelingPlan(
        user_intent="创建 T 形棱柱",
        steps=[
            op(OperationType.NEW_PART),
            op(OperationType.SELECT_PLANE, index=0),
            op(OperationType.SKETCH_RECTANGLE, x1=0, y1=0, x2=0.06, y2=0.045),
            op(OperationType.FEATURE_EXTRUSION, depth=0.015),
            op(
                OperationType.SELECT_FACE_AND_SKETCH,
                rx=0,
                ry=0.02,
                rz=-0.01,
                dx=0,
                dy=0,
                dz=1,
            ),
            op(OperationType.SKETCH_RECTANGLE, x1=0.02, y1=0.015, x2=0.04, y2=0.045),
            op(OperationType.FEATURE_CUT_EXTRUSION, depth=0.015),
        ],
    )

    stabilized = stabilize_three_view_blueprint(plan, ir)

    assert [step.operation for step in stabilized.steps] == [
        OperationType.NEW_PART,
        OperationType.SELECT_PLANE,
        OperationType.SKETCH_POLYLINE,
        OperationType.FEATURE_EXTRUSION,
    ]
    assert stabilized.steps[2].params["points"][:3] == [
        {"x": 0.02, "y": 0.0},
        {"x": 0.04, "y": 0.0},
        {"x": 0.04, "y": 0.03},
    ]


def test_tapered_top_projection_is_not_treated_as_through_hole() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "extrude_prism",
        "overall_size_mm": {"length": 18, "height": 18, "depth": 8},
        "profiles": [
            {"view": "front", "shape": "outer square with inner square"},
            {"view": "top", "shape": "trapezoid"},
        ],
        "features": [{"type": "hole", "size": "14x14mm rectangle"}],
    })
    plan = SwModelingPlan(
        user_intent="创建拔模棱柱",
        steps=[
            op(OperationType.NEW_PART),
            op(OperationType.SELECT_PLANE, index=0),
            op(OperationType.SKETCH_RECTANGLE, x1=-0.009, y1=-0.009, x2=0.009, y2=0.009),
            op(
                OperationType.FEATURE_EXTRUSION,
                depth=0.008,
                draftAngleDeg=14.04,
            ),
            op(OperationType.SELECT_FACE_AND_SKETCH),
            op(OperationType.SKETCH_RECTANGLE, x1=-0.007, y1=-0.007, x2=0.007, y2=0.007),
            op(
                OperationType.FEATURE_CUT_EXTRUSION,
                depth=0.008,
                endCondition="through_all",
            ),
        ],
    )

    stabilized = stabilize_three_view_blueprint(plan, ir)

    assert any(
        step.operation == OperationType.FEATURE_EXTRUSION
        for step in stabilized.steps
    )
    assert not any(
        step.operation == OperationType.FEATURE_CUT_EXTRUSION
        for step in stabilized.steps
    )


def test_drafted_prism_with_blind_pocket_is_derived_from_ir() -> None:
    ir = ThreeViewIR.model_validate({
        "part_type": "extrude_prism",
        "overall_size_mm": {"length": 18, "height": 8, "depth": 18},
        "profiles": [
            {"view": "front", "shape": "square"},
            {"view": "top", "shape": "trapezoid"},
            {"view": "right", "shape": "trapezoid"},
        ],
        "dimensions": [
            {"label": "顶面宽度", "value_mm": 14},
            {"label": "顶面深度", "value_mm": 14},
            {"label": "盲槽宽度", "value_mm": 12},
            {"label": "盲槽深度", "value_mm": 12},
            {"label": "盲槽切深", "value_mm": 3},
            {"label": "顶面圆角半径", "value_mm": 1.5},
        ],
        "features": [
            {"type": "square_recess"},
            {"type": "fillet", "quantity": 4},
        ],
    })
    plan = SwModelingPlan(user_intent="创建参数化拔模棱柱", steps=[])

    stabilized = stabilize_three_view_blueprint(plan, ir)

    assert len(stabilized.steps) == 11
    assert stabilized.steps[3].operation == OperationType.FEATURE_EXTRUSION
    assert round(stabilized.steps[3].params["draftAngleDeg"], 3) == 14.036
    assert stabilized.steps[6].operation == OperationType.FEATURE_CUT_EXTRUSION
    assert stabilized.steps[6].params["depth"] == 0.003
    assert sum(
        step.operation == OperationType.FEATURE_FILLET
        for step in stabilized.steps
    ) == 4
