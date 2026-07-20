from pathlib import Path

from planner.agent_loop import MultiTurnAgent
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.llm_client import _normalize_agent_decision_payload
from planner.operation_optimizer import group_feature_batches
from planner.schemas import (
    AgentDecision,
    AgentTurn,
    ModelObservation,
    OperationType,
    PostconditionSpec,
    SwOperation,
)
from planner.task_decomposer import TaskDecomposer
from planner.validators import ValidatorRegistry


def op(operation: OperationType, **params) -> SwOperation:
    return SwOperation(operation=operation, params=params)


def test_assembly_operations_have_http_endpoints():
    expected = {
        OperationType.ASSEMBLY_NEW: "/api/assembly/new",
        OperationType.ASSEMBLY_ADD_COMPONENT: "/api/assembly/add-component",
        OperationType.ASSEMBLY_FIX_COMPONENT: "/api/assembly/fix-component",
        OperationType.ASSEMBLY_MATE_COINCIDENT:
            "/api/assembly/mate-coincident",
        OperationType.ASSEMBLY_MATE_CONCENTRIC:
            "/api/assembly/mate-concentric",
        OperationType.ASSEMBLY_MATE_GEAR: "/api/assembly/mate-gear",
        OperationType.ASSEMBLY_CHECK_INTERFERENCE:
            "/api/assembly/check-interference",
        OperationType.ASSEMBLY_SAVE: "/api/assembly/save",
        OperationType.ASSEMBLY_SET_COMPONENT_POSE:
            "/api/assembly/set-component-pose",
        OperationType.ASSEMBLY_SET_CRANK_SLIDER_POSE:
            "/api/assembly/mechanism/crank-slider-pose",
        OperationType.ASSEMBLY_SET_INDEXED_ROTARY_POSE:
            "/api/assembly/mechanism/indexed-rotary-pose",
    }
    for operation, endpoint in expected.items():
        assert OPERATION_ENDPOINT_MAP[operation][1] == endpoint


def test_assembly_blueprint_gets_structural_postconditions(tmp_path: Path):
    assembly_path = tmp_path / "drive.SLDASM"
    steps = [
        op(OperationType.ASSEMBLY_NEW, savePath=str(assembly_path)),
        op(
            OperationType.ASSEMBLY_ADD_COMPONENT,
            partPath="a.SLDPRT",
            x=0,
            y=0,
            z=0,
        ),
        op(
            OperationType.ASSEMBLY_ADD_COMPONENT,
            partPath="b.SLDPRT",
            x=0.05,
            y=0,
            z=0,
        ),
        op(
            OperationType.ASSEMBLY_MATE_GEAR,
            component1="a-1",
            component2="b-1",
            gearRatioNumerator=2,
            gearRatioDenominator=1,
        ),
        op(OperationType.ASSEMBLY_CHECK_INTERFERENCE),
        op(OperationType.ASSEMBLY_SAVE),
    ]
    batches = group_feature_batches(steps)
    plan = TaskDecomposer().decompose_blueprint("创建装配体", batches)

    kinds = [
        post.kind
        for milestone in plan.milestones
        for post in milestone.postconditions
    ]
    assert "component_count" in kinds
    assert "mate_count" in kinds
    assert "no_interference" in kinds
    assert "assembly_saved" in kinds
    assert "valid_assembly" in kinds


def test_assembly_validators_use_components_mates_and_interference():
    observation = ModelObservation(
        document_type="assembly",
        components=[{"name": f"component-{index}"} for index in range(5)],
        mates=[
            {
                "name": "gear-1",
                "type": "MateGear",
                "gearRatioNumerator": 12,
                "gearRatioDenominator": 24,
            },
            {
                "name": "gear-2",
                "type": "MateGear",
                "gearRatioNumerator": 12,
                "gearRatioDenominator": 36,
            },
            {
                "name": "gear-3",
                "type": "MateGear",
                "gearRatioNumerator": 1,
                "gearRatioDenominator": 1,
            },
            {"name": "coincident-1", "type": "MateCoincident"},
        ],
        interference_summary={"interferenceCount": 0},
    )
    result = ValidatorRegistry.validate(
        [
            PostconditionSpec(kind="valid_assembly"),
            PostconditionSpec(
                kind="component_count",
                params={"exact": 5},
            ),
            PostconditionSpec(kind="mate_count", params={"minimum": 4}),
            PostconditionSpec(
                kind="mate_type_count",
                params={"keyword": "gear", "minimum": 3},
            ),
            PostconditionSpec(
                kind="gear_ratios",
                params={"expected": [1, 2, 3]},
            ),
            PostconditionSpec(
                kind="no_interference",
                params={"maximum": 0},
            ),
        ],
        observation,
    )
    assert result.passed


def test_first_assembly_turn_does_not_inject_new_part():
    turn = AgentTurn(
        decision=AgentDecision.ACT,
        actions=[op(OperationType.ASSEMBLY_OPEN, assemblyPath="drive.SLDASM")],
    )
    normalized = MultiTurnAgent._normalize_first_turn(
        object(),
        turn,
        assembly_goal=True,
    )
    assert normalized.actions == turn.actions


def test_first_assembly_turn_fills_planned_save_path():
    turn = AgentTurn(
        decision=AgentDecision.ACT,
        actions=[SwOperation(operation=OperationType.ASSEMBLY_NEW)],
    )
    normalized = MultiTurnAgent._normalize_first_turn(
        object(),
        turn,
        assembly_goal=True,
        assembly_save_path=r"C:\artifacts\drive.SLDASM",
    )
    assert (
        normalized.actions[0].params["savePath"]
        == r"C:\artifacts\drive.SLDASM"
    )


def test_agent_decision_normalizes_operation_list_alias():
    payload = _normalize_agent_decision_payload({
        "decision": "continue",
        "operations": [
            {
                "operation": "assembly_new",
                "params": {"savePath": "drive.SLDASM"},
            }
        ],
    })
    turn = AgentTurn.model_validate(payload)
    assert turn.decision == AgentDecision.ACT
    assert turn.actions[0].operation == OperationType.ASSEMBLY_NEW


def test_agent_decision_rejects_omitted_actions_for_act():
    try:
        AgentTurn.model_validate({"decision": "act"})
    except ValueError as exc:
        assert "actions" in str(exc)
    else:
        raise AssertionError("act without actions must be rejected")


def test_assembly_component_path_alias_is_normalized():
    operation = SwOperation.model_validate({
        "operation": "assembly_add_component",
        "params": {"filePath": "gear.SLDPRT", "x": 0, "y": 0, "z": 0},
    })
    assert operation.params["partPath"] == "gear.SLDPRT"


def test_flattened_component_arguments_are_nested():
    operation = SwOperation.model_validate({
        "op": "assembly_add_component",
        "file": "gear.SLDPRT",
        "x": 0.1,
        "y": 0,
        "z": 0,
    })
    assert operation.params == {
        "file": "gear.SLDPRT",
        "x": 0.1,
        "y": 0,
        "z": 0,
        "partPath": "gear.SLDPRT",
    }

    positioned = SwOperation.model_validate({
        "operation": "assembly_add_component",
        "params": {
            "filePath": "plate.SLDPRT",
            "position": [0.1, -0.2, 0.03],
        },
    })
    assert positioned.params["x"] == 0.1
    assert positioned.params["y"] == -0.2
    assert positioned.params["z"] == 0.03


def test_assembly_mate_parameter_shapes_are_normalized():
    gear = SwOperation.model_validate({
        "operation": "assembly_mate_gear",
        "params": {
            "component1": "a-1",
            "component2": "b-1",
            "ray1": [0, 0, 0.01, 1, 0, 0],
            "ray2": [0.05, 0, 0.01, 1, 0, 0],
            "ratioNumerator": 24,
            "ratioDenominator": 12,
        },
    })
    assert gear.params["rx1"] == 0
    assert gear.params["rz2"] == 0.01
    assert gear.params["gearRatioNumerator"] == 24

    coincident = SwOperation.model_validate({
        "operation": "assembly_mate_coincident",
        "params": {
            "component1": "a-1",
            "plane1": "Front Plane",
            "component2": "b-1",
            "plane2": "front",
        },
    })
    assert coincident.params["plane1"] == "前视基准面"
    assert coincident.params["plane2"] == "前视基准面"


def test_gear_mate_requires_ratio_but_not_rays():
    gear = SwOperation.model_validate({
        "operation": "assembly_mate_gear",
        "params": {
            "component1": "a-1",
            "component2": "b-1",
            "gearRatioNumerator": 24,
            "gearRatioDenominator": 12,
        },
    })
    assert "rx1" not in gear.params


def test_crank_slider_pose_contract_and_defaults():
    operation = SwOperation.model_validate({
        "operation": "assembly_set_crank_slider_pose",
        "params": {
            "crankComponent": "crank-1",
            "rodComponent": "rod-1",
            "sliderComponent": "piston-1",
            "crankRadius": 0.025,
            "rodLength": 0.09,
            "angleDeg": 90,
            "synchronizedComponents": [
                {
                    "name": "cam-1",
                    "position": [0.072, 0, 0],
                    "ratio": 2,
                    "reverse": True,
                },
            ],
        },
    })
    assert operation.params["crankRadius"] == 0.025
    assert (
        operation.params["synchronizedComponents"][0]["componentName"]
        == "cam-1"
    )
    assert operation.params["synchronizedComponents"][0]["x"] == 0.072
    assert operation.params["synchronizedComponents"][0]["ratio"] == 2


def test_indexed_rotary_pose_aliases_and_nested_components():
    operation = SwOperation.model_validate({
        "operation": "assembly_set_indexed_rotary_pose",
        "params": {
            "driver": "driver-1",
            "outputComponent": "wheel-1",
            "input_angle_deg": 180,
            "slot_count": 4,
            "continuous_components": [{
                "name": "shutter-1",
                "position": [0.04, 0, 0.02],
                "phaseDeg": 90,
            }],
            "indexed_components": [{
                "component": "reel-1",
                "ratio": 4,
            }],
        },
    })
    assert operation.params["driverComponent"] == "driver-1"
    assert operation.params["indexedComponent"] == "wheel-1"
    assert operation.params["inputAngleDeg"] == 180
    assert operation.params["slotCount"] == 4
    continuous = operation.params["continuousComponents"][0]
    assert continuous["componentName"] == "shutter-1"
    assert continuous["x"] == 0.04
    indexed = operation.params["indexedComponents"][0]
    assert indexed["componentName"] == "reel-1"

    named = SwOperation.model_validate({
        "operation": "assembly_set_indexed_rotary_pose",
        "params": {
            "driver_component_name": "driver-1",
            "indexed_component_name": "wheel-1",
            "angle": 0,
        },
    })
    assert named.params["driverComponent"] == "driver-1"
    assert named.params["indexedComponent"] == "wheel-1"
