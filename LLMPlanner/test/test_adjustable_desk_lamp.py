from __future__ import annotations

import json
from pathlib import Path

from eval.eval_adjustable_desk_lamp_multiturn import (
    ROLES,
    SPEC_PATH,
    _angles_for_control,
    _pose_payload,
    score_result,
)
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.schemas import OperationType, SwOperation


SPEC = json.loads(Path(SPEC_PATH).read_text(encoding="utf-8"))


def test_planar_chain_operation_aliases_and_endpoint():
    operation = SwOperation.model_validate({
        "operation": "assembly_set_planar_chain_pose",
        "params": {
            "basePivotX": 0,
            "basePivotY": 0.06,
            "basePivotZ": 0,
            "baseY": 0,
            "rotationAxis": "z",
            "endAbsoluteAngleDeg": -15,
            "links": [
                {
                    "componentName": "lower_arm_left-1",
                    "length": 0.12,
                    "angleDeg": 60,
                },
            ],
            "jointComponents": ["elbow_joint-1"],
            "endComponents": ["lamp_shade-1"],
        },
    })
    assert operation.params["baseY"] == 0.06
    assert operation.params["segments"][0]["length"] == 0.12
    assert operation.params["rotationAxisZ"] == 1.0
    assert operation.params["jointComponents"] == [{
        "componentName": "elbow_joint-1",
        "anchorIndex": 1,
    }]
    assert operation.params["endComponents"] == [{
        "componentName": "lamp_shade-1",
        "angleDeg": -15,
    }]
    assert OPERATION_ENDPOINT_MAP[
        OperationType.ASSEMBLY_SET_PLANAR_CHAIN_POSE
    ] == ("POST", "/api/assembly/mechanism/planar-chain-pose")


def test_working_control_uses_declared_joint_angles():
    assert _angles_for_control(SPEC, 50) == (60.0, -35.0)


def test_pose_payload_updates_parallel_arms_and_head():
    names = {role: f"{role}-1" for role in ROLES}
    payload = _pose_payload(names, SPEC, 60, -35)
    assert payload["segments"][0]["componentNames"] == [
        "lower_arm_left-1",
        "lower_arm_right-1",
        "lower_spring_left-1",
        "lower_spring_right-1",
    ]
    assert payload["segments"][1]["length"] == 0.105
    assert payload["segments"][0]["bodyLengths"] == [
        0.12,
        0.12,
        0.095,
        0.095,
    ]
    assert {
        item["componentName"]
        for item in payload["endComponents"]
    } == {"lamp_shade-1", "bulb-1"}


def test_full_lamp_result_scores_one_hundred():
    kinematics = {
        "samples": [
            {"passed": True},
            {"passed": True},
            {"passed": True},
        ],
    }
    assert score_result(
        status="completed",
        static_passed=True,
        kinematics=kinematics,
        succeeded_actions=24,
        total_actions=24,
    ) == 100.0
