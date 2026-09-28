"""评测模式下 GT 目标值不能进入 LLM prompt。"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.compressed_memory import CompressedMemory
from planner.llm_client import SwLLMClient
from planner.milestone_ledger import MilestoneLedger
from planner.milestone_view import public_evidence, public_milestone
from planner.schemas import Milestone, ModelObservation, PostconditionSpec, TaskPlan
from planner.validators import ValidatorRegistry

GT_POSITION = [123.456, 0, 78.9]
GT_RATIOS = [1.0, 2.75, 3.125]


def _milestone() -> Milestone:
    return Milestone(
        id="drive",
        title="插入组件并建立齿轮配合",
        postconditions=[
            PostconditionSpec(
                kind="component_positions",
                params={
                    "expected": [{"path_contains": "wheel_z36", "translation_mm": GT_POSITION}],
                    "tolerance_mm": 0.5,
                },
            ),
            PostconditionSpec(kind="gear_ratios", params={"expected": GT_RATIOS, "tolerance": 1e-6}),
            PostconditionSpec(kind="mate_type_count", params={"keyword": "gear", "minimum": 3}),
        ],
    )


def _failed_observation() -> ModelObservation:
    return ModelObservation(
        document_type="assembly",
        components=[{"name": "wheel_z36-1", "path": "C:/x/wheel_z36.SLDPRT",
                     "translationXYZ": [0.1, 0.0, 0.0789]}],
        mates=[{"name": "Gear1", "type": "gear", "gearRatioNumerator": 1, "gearRatioDenominator": 2}],
    )


def _with_failed_evidence() -> Milestone:
    milestone = _milestone()
    validation = ValidatorRegistry.validate(milestone.postconditions, _failed_observation())
    ledger = MilestoneLedger(TaskPlan(user_goal="g", milestones=[milestone]))
    ledger.mark_from_validation("drive", validation)
    return ledger.task_plan.milestones[0]


def _assert_no_targets(text: str) -> None:
    for value in ("123.456", "78.9", "2.75", "3.125"):
        assert value not in text, f"GT 目标值 {value} 出现在 LLM 可见内容中"


def test_public_milestone_hides_targets_but_keeps_actuals_and_direction():
    view = public_milestone(_with_failed_evidence())
    text = json.dumps(view, ensure_ascii=False)
    _assert_no_targets(text)
    assert view["postconditions"][2]["params"] == {"keyword": "gear"}
    positions = next(v for k, v in view["evidence"].items() if k.endswith("component_positions"))
    check = positions["checks"][0]
    assert check["actual_mm"] == [100.0, 0.0, 78.9] or check["actual_mm"][0] == pytest.approx(100.0)
    assert check["axis_status"][0] == "偏小"
    ratios = next(v for k, v in view["evidence"].items() if k.endswith("gear_ratios"))
    assert ratios["status"] == "不匹配"
    assert ratios["actual"] == [2.0]


def test_public_evidence_bbox_direction():
    details = {"0:bbox_axes": {"actual_mm": [10, 20, 30], "expected_mm": [10, 25, 28], "tolerance_mm": 1.0}}
    public = public_evidence(details)
    assert public["0:bbox_axes"]["axis_status"] == ["达标", "偏小", "偏大"]
    assert "expected_mm" not in public["0:bbox_axes"]


def test_memory_payload_hides_targets_only_when_requested():
    milestone = _with_failed_evidence()
    ledger = MilestoneLedger(TaskPlan(user_goal="g", milestones=[milestone]))
    memory = CompressedMemory("g", ledger.task_plan)
    hidden = json.dumps(memory.to_payload(ledger, hide_targets=True), ensure_ascii=False)
    _assert_no_targets(hidden)
    revealed = json.dumps(memory.to_payload(ledger), ensure_ascii=False)
    assert "3.125" in revealed


def test_agent_prompt_contains_no_gt_values_when_hidden():
    captured: list[list[dict]] = []

    class _Completions:
        def create(self, **kwargs):
            captured.append(kwargs["messages"])
            raise RuntimeError("stop after capture")

    class _Chat:
        completions = _Completions()

    class _Provider:
        chat = _Chat()

    client = object.__new__(SwLLMClient)
    client.use_cache = False
    client.cache = None
    client.plan_model = "test-model"
    client._assemble_system_prompt = lambda *_args, **_kwargs: "system"
    client.client = _Provider()

    milestone = _with_failed_evidence()
    ledger = MilestoneLedger(TaskPlan(user_goal="g", milestones=[milestone]))
    memory = CompressedMemory("g", ledger.task_plan)
    with pytest.raises(Exception):
        client.decide_agent_turn(
            "装配一个齿轮箱",
            _failed_observation(),
            memory.to_payload(ledger, hide_targets=True),
            2,
            current_milestone=ledger.task_plan.milestones[0],
            repair_required=True,
            hide_targets=True,
        )
    assert captured
    _assert_no_targets(captured[0][1]["content"])
