"""Pure unit tests for milestone, memory, validation, and idempotency."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.compressed_memory import CompressedMemory
from planner.idempotency import IdempotencyGuard
from planner.milestone_ledger import MilestoneLedger
from planner.schemas import (
    AgentDecision,
    AgentTurn,
    Milestone,
    ModelObservation,
    MultiTurnRecord,
    OperationType,
    PostconditionSpec,
    SwOperation,
    TaskPlan,
    ValidationResult,
)
from planner.task_decomposer import TaskDecomposer
from planner.validators import ValidatorRegistry


def test_dice_decomposition_and_dependency_order() -> None:
    plan = TaskDecomposer().decompose("创建一个标准骰子")
    ledger = MilestoneLedger(plan)
    assert len(plan.milestones) == 8
    assert ledger.current().id == "dice_body"
    ledger.mark_from_validation(
        "dice_body",
        ValidationResult(passed=True, details={"bbox": [20, 20, 20]}),
    )
    assert ledger.current().id == "dice_fillet"
    assert not ledger.is_complete()
    print("[PASS] dice recipe has dependency-ordered milestones")


def test_face_pips_validation_is_face_specific_and_idempotent() -> None:
    observation = ModelObservation(
        features=[
            {"name": "Boss1", "type": "Extrusion"},
            {"name": "Cut1", "type": "ICE"},
        ],
        sketches=[{
            "name": "Sketch2",
            "faceKey": "+Y",
            "circleCount": 1,
            "circles": [{"radius": 0.0015}],
        }],
        feature_sketch_links=[{
            "featureName": "Cut1",
            "featureType": "ICE",
            "sketchName": "Sketch2",
            "faceKey": "+Y",
            "depth": 0.0015,
        }],
        bounding_box_mm={"sizeX": 20, "sizeY": 20, "sizeZ": 20},
        volume_mm3=7900,
    )
    spec = PostconditionSpec(
        kind="face_pips",
        params={
            "face_key": "+Y",
            "circle_count": 1,
            "diameter_mm": 3,
            "cut_depth_mm": 1.5,
            "minimum_ice_count": 1,
        },
    )
    assert ValidatorRegistry.validate([spec], observation).passed
    duplicate = observation.model_copy(update={
        "feature_sketch_links": [
            *observation.feature_sketch_links,
            {
                "featureName": "Cut2",
                "featureType": "ICE",
                "sketchName": "Sketch3",
                "faceKey": "+Y",
                "depth": 0.0015,
            },
        ],
    })
    assert not ValidatorRegistry.validate([spec], duplicate).passed
    print("[PASS] face-pip validation rejects duplicate face machining")


def test_memory_keeps_ledger_and_compresses_old_turns() -> None:
    plan = TaskPlan(
        user_goal="test",
        milestones=[Milestone(id="m1", title="one")],
    )
    ledger = MilestoneLedger(plan)
    memory = CompressedMemory("test", plan)
    observation = ModelObservation(state_fingerprint="state")
    for turn in range(1, 7):
        record = MultiTurnRecord(
            turn=turn,
            observation_before=observation,
            decision=AgentTurn(
                decision=AgentDecision.ACT,
                milestone_id="m1",
                actions=[SwOperation(operation=OperationType.NEW_PART)],
            ),
            execution_results=[{
                "operation": "new_part",
                "success": turn != 2,
                "message": "failed" if turn == 2 else "ok",
            }],
            observation_after=observation,
            validation=ValidationResult(passed=False),
        )
        memory.update(record, ledger)
    payload = memory.to_payload(ledger)
    assert len(payload["last_3_turns"]) == 3
    assert len(payload["older_turns_compact"]) == 3
    assert payload["failure_digest"]["by_operation"]["new_part"] == 1
    assert payload["milestones"][0]["id"] == "m1"
    print("[PASS] compressed memory never loses ledger or failures")


def test_idempotency_blocks_repeated_failed_action() -> None:
    guard = IdempotencyGuard(failure_limit=2)
    actions = [SwOperation(
        operation=OperationType.FEATURE_FILLET,
        params={"radius": 0.0015, "edgeSelection": "all"},
    )]
    for _ in range(2):
        guard.record_after(
            "fillet",
            actions,
            [{"success": False}],
            "same",
            "same",
            False,
        )
    allowed, reason = guard.check_before("fillet", actions)
    assert not allowed
    assert "更换策略" in reason
    print("[PASS] idempotency guard blocks repeated semantic failure")


if __name__ == "__main__":
    test_dice_decomposition_and_dependency_order()
    test_face_pips_validation_is_face_specific_and_idempotent()
    test_memory_keeps_ledger_and_compresses_old_turns()
    test_idempotency_blocks_repeated_failed_action()
    print("\nAll ledger/validator tests passed.")
