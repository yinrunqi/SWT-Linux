"""Unit tests for the bounded Multi-turn Agent; no real LLM or SolidWorks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.agent_loop import MultiTurnAgent
from planner.executor import SwExecutor
from planner.llm_client import _normalize_agent_decision_payload
from planner.schemas import (
    AgentDecision,
    AgentTurn,
    ModelObservation,
    OperationType,
    SwModelingPlan,
    SwOperation,
)


def operation(name: OperationType, **params) -> SwOperation:
    return SwOperation(operation=name, params=params)


class FakeLLM:
    def __init__(self, decisions: list[AgentTurn]):
        self.decisions = decisions
        self.calls: list[dict] = []

    def decide_agent_turn(self, goal, observation, recent_turns, turn_number):
        self.calls.append({
            "goal": goal,
            "fingerprint": observation.state_fingerprint,
            "recent_turns": recent_turns,
            "turn": turn_number,
        })
        return self.decisions[len(self.calls) - 1]


class FakeExecutor:
    base_url = "http://fake"
    session = None

    def __init__(self, failures: dict[int, int] | None = None):
        self.calls: list[list[SwOperation]] = []
        self.failures = failures or {}

    def execute_operations(self, operations, *, step_offset=0, **_kwargs):
        call_index = len(self.calls)
        self.calls.append(operations)
        results = []
        fail_at = self.failures.get(call_index)
        for index, item in enumerate(operations, 1):
            success = fail_at != index
            results.append({
                "step": step_offset + index,
                "turn_step": index,
                "operation": item.operation.value,
                "success": success,
                "message": "ok" if success else "invalid edge selection",
            })
            if not success:
                break
        return results


class FakeObserver:
    def __init__(self, observations: list[ModelObservation]):
        self.observations = observations
        self.calls = 0

    def collect(self, last_execution=None):
        observation = self.observations[min(self.calls, len(self.observations) - 1)]
        self.calls += 1
        return observation.model_copy(update={"last_execution": last_execution or []})


def valid_observation(fingerprint: str) -> ModelObservation:
    return ModelObservation(
        features=[{"index": 1, "name": "Boss-Extrude1", "type": "Extrusion"}],
        bounding_box_mm={"sizeX": 50.0, "sizeY": 30.0, "sizeZ": 50.0},
        volume_mm3=58000.0,
        state_fingerprint=fingerprint,
    )


def test_failure_observe_adjust_finish() -> None:
    first = AgentTurn(
        decision=AgentDecision.ACT,
        progress_summary="创建圆柱并尝试倒角",
        actions=[
            operation(OperationType.SELECT_PLANE, index=1),
            operation(OperationType.SKETCH_CIRCLE, cx=0, cy=0, radius=0.025),
            operation(OperationType.FEATURE_EXTRUSION, depth=0.03),
            operation(
                OperationType.FEATURE_CHAMFER,
                type="equal_distance",
                distance1=0.001,
                edgeSelection="top_front",
            ),
        ],
    )
    correction = AgentTurn(
        decision=AgentDecision.ACT,
        progress_summary="根据失败改用 ray 选边",
        actions=[operation(
            OperationType.FEATURE_CHAMFER,
            type="equal_distance",
            distance1=0.001,
            edgeSelection="ray",
            rx=0.035,
            ry=0.031,
            rz=0,
            dx=-1,
            dy=-1,
            dz=0,
        )],
    )
    finish = AgentTurn(
        decision=AgentDecision.FINISH,
        progress_summary="圆柱和倒角均已完成",
        check_summary="特征、包围盒和体积有效",
    )
    llm = FakeLLM([first, correction, finish])
    executor = FakeExecutor(failures={0: 5})
    observer = FakeObserver([
        valid_observation("after-failure"),
        valid_observation("after-correction"),
    ])

    result = MultiTurnAgent(
        llm,
        executor,
        observer,
        max_turns=5,
    ).run("创建圆柱并在顶部做 C1 倒角")

    assert result.status == "completed"
    assert len(result.turns) == 3
    assert result.llm_calls == 3
    assert executor.calls[0][0].operation == OperationType.NEW_PART
    assert executor.calls[1][0].params["edgeSelection"] == "ray"
    assert llm.calls[1]["fingerprint"] == "after-failure"
    print("[PASS] failure → observe → adjusted action → finish")


def test_reject_finish_without_observation() -> None:
    llm = FakeLLM([AgentTurn(
        decision=AgentDecision.FINISH,
        progress_summary="错误地提前完成",
    )])
    result = MultiTurnAgent(
        llm,
        FakeExecutor(),
        FakeObserver([valid_observation("unused")]),
    ).run("创建立方体")
    assert result.status == "blocked"
    assert "拒绝提前完成" in result.message
    print("[PASS] finish requires a real post-execution observation")


def test_repeated_actions_are_bounded() -> None:
    repeated = AgentTurn(
        decision=AgentDecision.ACT,
        progress_summary="重复动作",
        actions=[operation(OperationType.FEATURE_FILLET, radius=0.002)],
    )
    executor = FakeExecutor()
    result = MultiTurnAgent(
        FakeLLM([repeated, repeated, repeated, repeated]),
        executor,
        FakeObserver([valid_observation("same")]),
        max_turns=5,
        repeated_action_limit=2,
    ).run("创建圆角")
    assert result.status == "blocked"
    assert len(executor.calls) == 3
    assert "重复" in result.message
    print("[PASS] repeated action loop is stopped")


def test_execute_plan_remains_compatible() -> None:
    executor = SwExecutor(base_url="http://unused", verbose=False)
    executor._execute_step = lambda step: {"success": True, "message": "ok"}
    plan = SwModelingPlan(
        user_intent="兼容性测试",
        steps=[
            operation(OperationType.NEW_PART),
            operation(OperationType.SELECT_PLANE, index=1),
        ],
    )
    results = executor.execute_plan(plan)
    executor.close()
    assert len(results) == 2
    assert all(result["success"] for result in results)
    assert [result["step"] for result in results] == [1, 2]
    assert all("turn_step" not in result for result in results)
    print("[PASS] existing execute_plan behavior remains compatible")


def test_golden_dataset_contains_mvp_scenarios() -> None:
    path = Path(__file__).parent / "data" / "multi_turn_conversations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = {session["id"] for session in payload["sessions"]}
    assert {
        "auto_fix_wrong_size",
        "step_by_step_feature_add",
        "execution_failure_retry",
    } <= ids
    print("[PASS] MVP golden replay scenarios are present")


def test_provider_decision_aliases() -> None:
    payload = _normalize_agent_decision_payload({
        "decision": "continue",
        "actions": [{"operation": "new_part", "params": {}}],
    })
    turn = AgentTurn.model_validate(payload)
    assert turn.decision == AgentDecision.ACT
    print("[PASS] compatible-provider 'continue' alias becomes 'act'")


if __name__ == "__main__":
    test_failure_observe_adjust_finish()
    test_reject_finish_without_observation()
    test_repeated_actions_are_bounded()
    test_execute_plan_remains_compatible()
    test_golden_dataset_contains_mvp_scenarios()
    test_provider_decision_aliases()
    print("\nAll Multi-turn Agent tests passed.")
