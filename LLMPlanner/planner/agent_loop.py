"""Bounded Observe → Decide → Act → Check loop for Multi-turn modeling."""

from __future__ import annotations

import json
import time

from .executor import SwExecutor
from .llm_client import SwLLMClient
from .observer import SwObserver
from .schemas import (
    AgentDecision,
    AgentTurn,
    ModelObservation,
    MultiTurnRecord,
    MultiTurnResult,
    OperationType,
    SwOperation,
)


class MultiTurnAgent:
    """Run a synchronous, bounded multi-turn modeling session."""

    def __init__(
        self,
        llm_client: SwLLMClient,
        executor: SwExecutor,
        observer: SwObserver | None = None,
        *,
        max_turns: int = 8,
        max_actions_per_turn: int = 5,
        repeated_action_limit: int = 2,
    ):
        self.llm_client = llm_client
        self.executor = executor
        self.observer = observer or SwObserver(
            executor.base_url,
            session=executor.session,
        )
        self.max_turns = max(1, min(max_turns, 20))
        self.max_actions_per_turn = max(1, min(max_actions_per_turn, 5))
        self.repeated_action_limit = max(1, repeated_action_limit)

    @staticmethod
    def _action_signature(turn: AgentTurn) -> str:
        return json.dumps(
            [action.model_dump(mode="json") for action in turn.actions],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _compact_turn(record: MultiTurnRecord) -> dict:
        after = record.observation_after
        return {
            "turn": record.turn,
            "decision": record.decision.decision.value,
            "progress_summary": record.decision.progress_summary,
            "actions": [
                action.model_dump(mode="json")
                for action in record.decision.actions
            ],
            "execution_results": record.execution_results,
            "state_after": None if after is None else {
                "feature_types": [
                    feature.get("type", "")
                    for feature in after.features
                ],
                "bounding_box_mm": after.bounding_box_mm,
                "volume_mm3": after.volume_mm3,
                "errors": after.errors,
                "state_fingerprint": after.state_fingerprint,
            },
        }

    @staticmethod
    def _can_finish(
        observation: ModelObservation,
        records: list[MultiTurnRecord],
    ) -> tuple[bool, str]:
        if not records:
            return False, "尚未执行任何建模操作"
        if observation.errors:
            return False, f"状态查询仍有错误: {observation.errors}"
        if not observation.features or observation.bounding_box_mm is None:
            return False, "缺少有效实体或包围盒"
        if observation.volume_mm3 is None or observation.volume_mm3 <= 0:
            return False, "实体体积无效"
        last_results = records[-1].execution_results
        if any(not result.get("success", False) for result in last_results):
            return False, "最近一轮仍有未处理的执行失败"
        return True, ""

    def _normalize_first_turn(self, turn: AgentTurn) -> AgentTurn:
        """A fresh MVP session always starts from a deterministic new part."""
        if turn.decision != AgentDecision.ACT:
            return turn
        if turn.actions and turn.actions[0].operation == OperationType.NEW_PART:
            return turn
        actions = [
            SwOperation(
                operation=OperationType.NEW_PART,
                description="Multi-turn 新任务：创建空白零件",
            ),
            *turn.actions[: self.max_actions_per_turn - 1],
        ]
        return turn.model_copy(update={"actions": actions})

    def _result(
        self,
        *,
        status: str,
        user_goal: str,
        records: list[MultiTurnRecord],
        observation: ModelObservation,
        started: float,
        llm_calls: int,
        message: str,
    ) -> MultiTurnResult:
        results = [
            result
            for record in records
            for result in record.execution_results
        ]
        return MultiTurnResult(
            status=status,
            user_goal=user_goal,
            turns=records,
            final_observation=observation,
            total_actions=len(results),
            successful_actions=sum(
                bool(result.get("success", False))
                for result in results
            ),
            llm_calls=llm_calls,
            duration_seconds=round(time.perf_counter() - started, 3),
            message=message,
        )

    def run(self, user_goal: str) -> MultiTurnResult:
        started = time.perf_counter()
        records: list[MultiTurnRecord] = []
        observation = ModelObservation(
            errors={"session": "新任务尚未创建零件"},
        )
        signature_counts: dict[str, int] = {}
        llm_calls = 0
        step_offset = 0

        for turn_number in range(1, self.max_turns + 1):
            recent_turns = [
                self._compact_turn(record)
                for record in records[-3:]
            ]
            decision = self.llm_client.decide_agent_turn(
                user_goal,
                observation,
                recent_turns,
                turn_number,
            )
            llm_calls += 1
            if turn_number == 1:
                decision = self._normalize_first_turn(decision)

            if decision.decision == AgentDecision.FINISH:
                can_finish, reason = self._can_finish(observation, records)
                if can_finish:
                    records.append(MultiTurnRecord(
                        turn=turn_number,
                        observation_before=observation,
                        decision=decision,
                        observation_after=observation,
                    ))
                    return self._result(
                        status="completed",
                        user_goal=user_goal,
                        records=records,
                        observation=observation,
                        started=started,
                        llm_calls=llm_calls,
                        message=decision.check_summary or "建模目标已完成",
                    )
                decision = AgentTurn(
                    decision=AgentDecision.BLOCKED,
                    progress_summary=decision.progress_summary,
                    check_summary=f"拒绝提前完成: {reason}",
                    next_milestone="需要继续检查或修正模型",
                )

            if decision.decision == AgentDecision.BLOCKED:
                records.append(MultiTurnRecord(
                    turn=turn_number,
                    observation_before=observation,
                    decision=decision,
                    observation_after=observation,
                ))
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=decision.check_summary or decision.progress_summary,
                )

            actions = decision.actions[: self.max_actions_per_turn]
            decision = decision.model_copy(update={"actions": actions})
            signature = self._action_signature(decision)
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
            if signature_counts[signature] > self.repeated_action_limit:
                blocked = AgentTurn(
                    decision=AgentDecision.BLOCKED,
                    progress_summary="检测到重复操作循环",
                    check_summary="相同操作批次已重复，已停止以避免无限循环",
                )
                records.append(MultiTurnRecord(
                    turn=turn_number,
                    observation_before=observation,
                    decision=blocked,
                    observation_after=observation,
                ))
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=blocked.check_summary,
                )

            execution_results = self.executor.execute_operations(
                actions,
                step_offset=step_offset,
                include_turn_step=True,
            )
            step_offset += len(execution_results)
            after = self.observer.collect(execution_results)
            records.append(MultiTurnRecord(
                turn=turn_number,
                observation_before=observation,
                decision=decision,
                execution_results=execution_results,
                observation_after=after,
            ))
            observation = after

        return self._result(
            status="blocked",
            user_goal=user_goal,
            records=records,
            observation=observation,
            started=started,
            llm_calls=llm_calls,
            message=f"达到最大轮数 {self.max_turns}，任务尚未完成",
        )
