from pathlib import Path

import pytest

from planner.agent_loop import MultiTurnAgent
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.llm_client import _normalize_agent_decision_payload
from planner.observer import (
    MAX_AUTO_INTERFERENCE_COMPONENTS,
    SwObserver,
)
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


def test_component_positions_prefer_exact_part_stem() -> None:
    observation = ModelObservation(
        document_type="assembly",
        components=[
            {
                "name": "needle_bar-1",
                "path": r"C:\parts\needle_bar.SLDPRT",
                "translationXYZ": [0.045, 0.040, 0.0],
            },
            {
                "name": "needle-1",
                "path": r"C:\parts\needle.SLDPRT",
                "translationXYZ": [0.045, 0.005, 0.0],
            },
        ],
    )
    result = ValidatorRegistry.validate(
        [
            PostconditionSpec(
                kind="component_positions",
                params={
                    "expected": [{
                        "path_contains": "needle",
                        "translation_mm": [45, 5, 0],
                    }],
                    "tolerance_mm": 0.1,
                },
            ),
        ],
        observation,
    )
    assert result.passed is True


def test_large_assembly_observation_skips_quadratic_interference() -> None:
    observer = SwObserver()
    observer._get = lambda _path: ({
        "result": {
            "assemblyPath": r"C:\parts\large.SLDASM",
            "components": [
                {"name": f"part-{index}"}
                for index in range(MAX_AUTO_INTERFERENCE_COMPONENTS + 1)
            ],
            "mates": [],
        },
    }, None)

    def unexpected_post(*_args, **_kwargs):
        raise AssertionError("large assembly must not auto-check interference")

    observer._post = unexpected_post
    result = observer._collect_assembly([])
    assert result.interference_summary == {
        "skipped": True,
        "reason": "component_count_threshold",
        "componentCount": 21,
        "threshold": 20,
    }
    observer.close()


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
        OperationType.ASSEMBLY_SET_SYNCHRONIZED_ROTARY_POSE:
            "/api/assembly/mechanism/synchronized-rotary-pose",
        OperationType.ASSEMBLY_SET_FOUR_BAR_POSE:
            "/api/assembly/mechanism/four-bar-pose",
        OperationType.ASSEMBLY_SET_CAM_FOLLOWER_POSE:
            "/api/assembly/mechanism/cam-follower-pose",
    }
    for operation, endpoint in expected.items():
        assert OPERATION_ENDPOINT_MAP[operation] == ("POST", endpoint)


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


def test_synchronized_rotary_pose_aliases_and_vectors():
    operation = SwOperation.model_validate({
        "operation": "assembly-set-synchronized-rotary-pose",
        "params": {
            "input_angle_deg": 120,
            "rotation_axis": "z",
            "rotor_components": [
                {
                    "component_name": "rotor-1",
                    "position": {"x": 0.01, "y": 0.02, "z": 0.03},
                    "speed_ratio": 2,
                    "phase_deg": 15,
                },
                "rotor-2",
            ],
        },
    })
    assert operation.params["inputAngleDeg"] == 120
    assert operation.params["rotationAxisZ"] == 1.0
    rotor = operation.params["rotors"][0]
    assert rotor["componentName"] == "rotor-1"
    assert (rotor["x"], rotor["y"], rotor["z"]) == (0.01, 0.02, 0.03)
    assert rotor["ratio"] == 2
    assert rotor["phaseDeg"] == 15
    assert operation.params["rotors"][1] == {"componentName": "rotor-2"}


def test_four_bar_pose_aliases_vectors_and_component_shapes():
    operation = SwOperation.model_validate({
        "operation": "assembly_set_four_bar_pose",
        "params": {
            "ground_a": [0, 0, 0],
            "groundB": {"x": 0.1, "y": 0, "z": 0},
            "input_length": 0.04,
            "coupler_length": 0.09,
            "output_length": 0.06,
            "angle": 35,
            "input": {"name": "input-1"},
            "coupler": {"component": "coupler-1"},
            "output": "output-1",
            "assembly_mode": "open",
            "rotationAxis": [0, 0, 1],
            "coupler_point": [0.02, 0.01, 0],
            "coupler_point_components": ["marker-1"],
        },
    })
    assert operation.params["groundBX"] == 0.1
    assert operation.params["inputComponent"] == "input-1"
    assert operation.params["couplerComponent"] == "coupler-1"
    assert operation.params["outputComponent"] == "output-1"
    assert operation.params["rotationAxisZ"] == 1
    assert operation.params["couplerPointY"] == 0.01
    assert operation.params["couplerPointComponents"] == [
        {"componentName": "marker-1"},
    ]


def test_cam_follower_pose_aliases_nested_components_and_axes():
    operation = SwOperation.model_validate({
        "operation": "assembly_set_cam_follower_pose",
        "params": {
            "driver": {"component_name": "cam-1"},
            "follower_component": {"name": "follower-1"},
            "cam_center": [0.01, -0.02, 0.03],
            "base_radius": 0.025,
            "lift": 0.008,
            "input_angle_deg": 90,
            "phase_deg": 10,
            "profile_type": "harmonic",
            "axes": {"driver": "z", "follower": [0, 1, 0]},
        },
    })
    assert operation.params["driverComponent"] == "cam-1"
    assert operation.params["followerComponent"] == "follower-1"
    assert operation.params["centerY"] == -0.02
    assert operation.params["baseRadius"] == 0.025
    assert operation.params["inputAngleDeg"] == 90
    assert operation.params["profile"] == "harmonic"
    assert operation.params["rotationAxisZ"] == 1.0
    assert operation.params["followerAxisY"] == 1


@pytest.mark.parametrize(
    ("operation", "params", "missing"),
    [
        (
            "assembly_set_synchronized_rotary_pose",
            {"inputAngleDeg": 0},
            "rotors",
        ),
        (
            "assembly_set_four_bar_pose",
            {
                "groundA": [0, 0, 0],
                "groundB": [1, 0, 0],
                "inputLength": 1,
                "couplerLength": 1,
                "outputLength": 1,
                "inputAngleDeg": 0,
                "inputComponent": "input-1",
                "couplerComponent": "coupler-1",
            },
            "outputComponent",
        ),
        (
            "assembly_set_cam_follower_pose",
            {
                "driverComponent": "cam-1",
                "followerComponent": "follower-1",
                "center": [0, 0, 0],
                "baseRadius": 1,
                "inputAngleDeg": 0,
            },
            "lift",
        ),
    ],
)
def test_new_mechanism_operations_validate_required_fields(
    operation,
    params,
    missing,
):
    with pytest.raises(ValueError, match=missing):
        SwOperation.model_validate({
            "operation": operation,
            "params": params,
        })


def test_new_mechanism_operations_terminate_feature_batches():
    operations = [
        op(
            OperationType.ASSEMBLY_SET_SYNCHRONIZED_ROTARY_POSE,
            inputAngleDeg=0,
            rotors=[],
        ),
        op(
            OperationType.ASSEMBLY_SET_CAM_FOLLOWER_POSE,
            driverComponent="cam-1",
            followerComponent="follower-1",
            centerX=0,
            centerY=0,
            centerZ=0,
            baseRadius=1,
            lift=0.1,
            inputAngleDeg=0,
        ),
    ]
    assert group_feature_batches(operations) == [
        [operations[0]],
        [operations[1]],
    ]
