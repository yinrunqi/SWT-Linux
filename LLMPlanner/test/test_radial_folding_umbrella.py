import json
from pathlib import Path

from eval.eval_radial_folding_umbrella_multiturn import (
    SPEC_PATH,
    _pose_for_openness,
    _pose_payload,
    score_result,
)
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.llm_client import _normalize_agent_decision_payload
from planner.schemas import ModelObservation, OperationType, SwOperation


def _spec() -> dict:
    return json.loads(Path(SPEC_PATH).read_text(encoding="utf-8"))


def test_radial_deployable_aliases_and_endpoint() -> None:
    operation = SwOperation(
        operation="assembly_set_radial_deployable_pose",
        params={
            "center": [0, 0, 0.34],
            "deploymentAxis": "z",
            "hub_radius": 0.018,
            "runner_radius": 0.015,
            "rib_length": 0.3,
            "attachmentDistance": 0.12,
            "stretcher_length": 0.16,
            "angleDeg": 12,
            "runner": "runner-1",
            "links": [
                {"rib": "rib-1", "stretcher": "link-1"},
                {
                    "rib_component": "rib-2",
                    "stretcher_component": "link-2",
                },
            ],
        },
    )
    payload = operation.params
    assert payload["centerZ"] == 0.34
    assert payload["deploymentAxisZ"] == 1.0
    assert payload["ribDownAngleDeg"] == 12
    assert payload["bays"][0] == {
        "rib": "rib-1",
        "stretcher": "link-1",
        "ribComponent": "rib-1",
        "stretcherComponent": "link-1",
        "phaseDeg": 0.0,
    }
    assert payload["bays"][1]["phaseDeg"] == 180.0
    assert OPERATION_ENDPOINT_MAP[
        OperationType.ASSEMBLY_SET_RADIAL_DEPLOYABLE_POSE
    ] == ("POST", "/api/assembly/mechanism/radial-deployable-pose")


def test_radial_shared_geometry_can_be_nested() -> None:
    operation = SwOperation(
        operation="assembly_set_radial_deployable_pose",
        params={
            "center": [0, 0, 0.34],
            "geometry": {
                "hubRadius": 0.018,
                "runnerRadius": 0.015,
                "mainRibLength": 0.3,
                "attachmentPointDistance": 0.12,
                "linkLength": 0.16,
                "ribAngle": 12,
                "runnerComponentName": "runner-1",
            },
            "bays": [
                {"rib": "rib-1", "stretcher": "stretcher-1"},
                {"rib": "rib-2", "stretcher": "stretcher-2"},
            ],
        },
    )
    assert operation.params["ribLength"] == 0.3
    assert operation.params["ribAttachmentDistance"] == 0.12
    assert operation.params["stretcherLength"] == 0.16
    assert operation.params["ribDownAngleDeg"] == 12
    assert operation.params["runnerComponent"] == "runner-1"


def test_provider_radial_shape_and_runner_are_recovered() -> None:
    observation = ModelObservation(
        document_type="assembly",
        components=[
            {"name": "rib_1-1", "path": r"C:\parts\rib_1.SLDPRT"},
            {
                "name": "sliding_runner-1",
                "path": r"C:\parts\sliding_runner.SLDPRT",
            },
        ],
    )
    payload = _normalize_agent_decision_payload(
        {
            "decision": "act",
            "actions": [{
                "operation": "assembly_set_radial_deployable_pose",
                "params": {
                    "centerXYZ": [0, 0, 0.34],
                    "deployAxis": "z",
                    "hubRadius": 0.018,
                    "sliderRadius": 0.015,
                    "ribLength": 0.3,
                    "stretcherConnectDistance": 0.12,
                    "stretcherLength": 0.16,
                    "initialAngleDeg": 12,
                    "bays": [
                        {
                            "ribComponentName": "rib_1-1",
                            "stretcherComponentName": "stretcher_1-1",
                            "angleDeg": 0,
                        },
                        {
                            "ribComponentName": "rib_2-1",
                            "stretcherComponentName": "stretcher_2-1",
                            "angleDeg": 180,
                        },
                    ],
                },
            }],
        },
        observation,
    )
    operation = SwOperation.model_validate(payload["actions"][0])
    assert operation.params["runnerComponent"] == "sliding_runner-1"
    assert operation.params["runnerRadius"] == 0.015
    assert operation.params["ribAttachmentDistance"] == 0.12
    assert operation.params["bays"][0]["phaseDeg"] == 0


def test_openness_interpolates_declared_poses() -> None:
    spec = _spec()
    assert _pose_for_openness(spec, 0) == 78
    assert _pose_for_openness(spec, 50) == 45
    assert _pose_for_openness(spec, 100) == 12
    assert _pose_for_openness(spec, 25) == 61.5


def test_pose_payload_has_eight_uniform_bays() -> None:
    names = {
        "sliding_runner": "runner-1",
        **{f"rib_{index}": f"rib-{index}" for index in range(1, 9)},
        **{
            f"stretcher_{index}": f"stretcher-{index}"
            for index in range(1, 9)
        },
    }
    payload = _pose_payload(names, _spec(), 12)
    assert payload["centerZ"] == 0.34
    assert payload["ribAttachmentDistance"] == 0.12
    assert payload["stretcherLength"] == 0.16
    assert [bay["phaseDeg"] for bay in payload["bays"]] == [
        0,
        45,
        90,
        135,
        180,
        225,
        270,
        315,
    ]


def test_full_umbrella_result_scores_one_hundred() -> None:
    score = score_result(
        status="completed",
        static_passed=True,
        kinematics={
            "samples": [{"passed": True}] * 3,
            "runner_motion_monotonic": True,
            "canopy_radius_monotonic": True,
        },
        successful_actions=24,
        total_actions=24,
    )
    assert score == 100
