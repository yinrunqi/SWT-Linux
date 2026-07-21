"""Ledger-driven Observe → Decide → Act → Validate loop."""

from __future__ import annotations

import math
import time

from .compressed_memory import CompressedMemory
from .executor import SwExecutor
from .idempotency import IdempotencyGuard
from .llm_client import SwLLMClient
from .milestone_ledger import MilestoneLedger
from .observer import SwObserver
from .schemas import (
    AgentDecision,
    AgentTurn,
    Milestone,
    ModelObservation,
    MultiTurnRecord,
    MultiTurnResult,
    OperationType,
    SwOperation,
    TaskPlan,
    ValidationResult,
)
from .task_decomposer import TaskDecomposer
from .validators import ValidatorRegistry


class MultiTurnAgent:
    """Execute exactly one machine-verifiable milestone at a time."""

    def __init__(
        self,
        llm_client: SwLLMClient,
        executor: SwExecutor,
        observer: SwObserver | None = None,
        *,
        max_turns: int | None = None,
        max_actions_per_turn: int = 5,
        repeated_action_limit: int = 2,
        task_decomposer: TaskDecomposer | None = None,
    ):
        self.llm_client = llm_client
        self.executor = executor
        self.observer = observer or SwObserver(
            executor.base_url,
            session=executor.session,
        )
        self.user_max_turns = (
            None if max_turns is None else max(1, min(max_turns, 100))
        )
        self.max_actions_per_turn = max(1, min(max_actions_per_turn, 5))
        self.repeated_action_limit = max(1, repeated_action_limit)
        self.task_decomposer = task_decomposer or TaskDecomposer(llm_client)

    def _normalize_first_turn(
        self,
        turn: AgentTurn,
        *,
        assembly_goal: bool = False,
        assembly_save_path: str = "",
    ) -> AgentTurn:
        if turn.decision != AgentDecision.ACT:
            return turn
        if assembly_goal:
            if not assembly_save_path:
                return turn
            normalized = []
            for action in turn.actions:
                if (
                    action.operation == OperationType.ASSEMBLY_NEW
                    and not action.params.get("savePath")
                ):
                    params = dict(action.params)
                    params["savePath"] = assembly_save_path
                    action = action.model_copy(update={"params": params})
                normalized.append(action)
            return turn.model_copy(update={"actions": normalized})
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

    @staticmethod
    def _valid_finish_state(observation: ModelObservation) -> bool:
        if observation.document_type == "assembly":
            return not observation.errors and bool(observation.components)
        return (
            not observation.errors
            and observation.bounding_box_mm is not None
            and (observation.volume_mm3 or 0) > 0
        )

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
        ledger: MilestoneLedger,
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
            milestones=ledger.summary(),
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

    def _settle_agent_cache(self, execution_succeeded: bool) -> None:
        settle = getattr(
            self.llm_client,
            "settle_agent_turn_cache",
            None,
        )
        if callable(settle):
            settle(execution_succeeded)

    @staticmethod
    def _stabilize_repair_face_selection(
        actions: list[SwOperation],
        observation: ModelObservation,
    ) -> list[SwOperation]:
        """Replace repair-time ray guesses with observed planar face indices."""
        faces = observation.faces or []
        if not faces:
            return actions
        stabilized: list[SwOperation] = []
        for action in actions:
            if action.operation != OperationType.SELECT_FACE_AND_SKETCH:
                stabilized.append(action)
                continue
            params = action.params
            try:
                origin = [
                    float(params[key])
                    for key in ("rx", "ry", "rz")
                ]
                direction = [
                    float(params[key])
                    for key in ("dx", "dy", "dz")
                ]
                length = math.sqrt(sum(value * value for value in direction))
                if length <= 1e-12:
                    raise ValueError("zero ray direction")
                unit = [value / length for value in direction]
            except (KeyError, TypeError, ValueError):
                stabilized.append(action)
                continue

            best: tuple[float, int] | None = None
            for face in faces:
                if str(face.get("surfaceType", "")).lower() != "plane":
                    continue
                normal_data = face.get("normal") or {}
                center_data = face.get("center") or {}
                try:
                    normal = [
                        float(normal_data[key])
                        for key in ("x", "y", "z")
                    ]
                    center = [
                        float(center_data[key])
                        for key in ("x", "y", "z")
                    ]
                    face_index = int(face["index"])
                except (KeyError, TypeError, ValueError):
                    continue
                normal_length = math.sqrt(
                    sum(value * value for value in normal)
                )
                if normal_length <= 1e-12:
                    continue
                alignment = abs(sum(
                    unit[i] * normal[i] / normal_length
                    for i in range(3)
                ))
                if alignment < 0.70:
                    continue
                relative = [
                    center[i] - origin[i]
                    for i in range(3)
                ]
                forward = sum(
                    relative[i] * unit[i]
                    for i in range(3)
                )
                lateral = math.sqrt(sum(
                    (
                        relative[i] - forward * unit[i]
                    ) ** 2
                    for i in range(3)
                ))
                area = max(float(face.get("area") or 0), 0.0)
                score = (
                    alignment * 100.0
                    + (5.0 if forward >= 0 else 0.0)
                    + math.log1p(area * 1e6) * 0.1
                    - lateral * 100.0
                    - abs(forward) * 1000.0
                )
                if best is None or score > best[0]:
                    best = (score, face_index)
            if best is None:
                stabilized.append(action)
                continue
            stabilized.append(SwOperation(
                operation=OperationType.SELECT_OBSERVED_FACE_AND_SKETCH,
                params={"faceIndex": best[1]},
                description=(
                    "修复轮：用当前 observation 的平面索引替代射线猜测"
                ),
            ))
        return stabilized

    @staticmethod
    def _rejected_record(
        turn_number: int,
        observation: ModelObservation,
        decision: AgentTurn,
        validation: ValidationResult,
    ) -> MultiTurnRecord:
        return MultiTurnRecord(
            turn=turn_number,
            observation_before=observation,
            decision=decision,
            observation_after=observation,
            validation=validation,
        )

    def run(
        self,
        user_goal: str,
        task_plan: TaskPlan | None = None,
    ) -> MultiTurnResult:
        started = time.perf_counter()
        if task_plan is None:
            task_plan = self.task_decomposer.decompose(user_goal)
        ledger = MilestoneLedger(task_plan)
        memory = CompressedMemory(user_goal, task_plan)
        guard = IdempotencyGuard(self.repeated_action_limit)
        dynamic_turns = max(
            8,
            len(task_plan.milestones) + task_plan.recovery_buffer,
        )
        # When omitted, use the dynamic budget. An explicit user value is a hard ceiling.
        if self.user_max_turns is None:
            max_turns = min(dynamic_turns, 100)
        else:
            max_turns = min(self.user_max_turns, 100)

        records: list[MultiTurnRecord] = []
        assembly_goal = any(
            marker in user_goal.lower()
            for marker in ("装配", "assembly", "mate", "配合")
        )
        assembly_save_path = ""
        if assembly_goal:
            for planned_milestone in task_plan.milestones:
                for postcondition in planned_milestone.postconditions:
                    if postcondition.kind == "assembly_saved":
                        assembly_save_path = str(
                            postcondition.params.get("path", "")
                        )
                        if assembly_save_path:
                            break
                if assembly_save_path:
                    break
        observation = ModelObservation(
            document_type="assembly" if assembly_goal else "part",
            errors={
                "session": (
                    "新任务尚未创建装配体"
                    if assembly_goal
                    else "新任务尚未创建零件"
                )
            },
        )
        llm_calls = 0
        step_offset = 0

        for turn_number in range(1, max_turns + 1):
            milestone: Milestone | None = ledger.current()
            if milestone is None:
                status = "completed" if ledger.is_complete() else "blocked"
                return self._result(
                    status=status,
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=(
                        "全部里程碑通过机器验收"
                        if status == "completed"
                        else "没有可继续执行的里程碑"
                    ),
                    ledger=ledger,
                )

            context = memory.to_payload(ledger)
            decision = self.llm_client.decide_agent_turn(
                user_goal,
                observation,
                context,
                turn_number,
                current_milestone=milestone,
            )
            llm_calls += 1
            decision = decision.model_copy(
                update={"milestone_id": milestone.id}
            )
            if turn_number == 1:
                decision = self._normalize_first_turn(
                    decision,
                    assembly_goal=assembly_goal,
                    assembly_save_path=assembly_save_path,
                )

            if decision.decision == AgentDecision.FINISH:
                self._settle_agent_cache(False)
                validation = ValidatorRegistry.validate(
                    milestone.postconditions,
                    observation,
                )
                rejected = decision.model_copy(update={
                    "check_summary": (
                        "拒绝由 LLM 提前完成；账本仍有待办里程碑"
                    ),
                })
                record = self._rejected_record(
                    turn_number,
                    observation,
                    rejected,
                    validation,
                )
                records.append(record)
                memory.update(record, ledger)
                continue

            if decision.decision == AgentDecision.BLOCKED:
                self._settle_agent_cache(False)
                ledger.mark_failed(
                    milestone.id,
                    decision.check_summary or decision.progress_summary,
                )
                record = MultiTurnRecord(
                    turn=turn_number,
                    observation_before=observation,
                    decision=decision,
                    observation_after=observation,
                )
                records.append(record)
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=decision.check_summary or decision.progress_summary,
                    ledger=ledger,
                )

            actions = decision.actions[
                : min(self.max_actions_per_turn, milestone.max_actions)
            ]
            decision = decision.model_copy(update={"actions": actions})
            allowed, reason = guard.check_before(milestone.id, actions)
            if not allowed:
                self._settle_agent_cache(False)
                validation = ValidationResult(
                    passed=False,
                    message=reason,
                    details={"idempotency": reason},
                )
                rejected = AgentTurn(
                    decision=AgentDecision.BLOCKED,
                    milestone_id=milestone.id,
                    progress_summary="幂等守卫拒绝重复动作",
                    check_summary=reason,
                )
                record = self._rejected_record(
                    turn_number,
                    observation,
                    rejected,
                    validation,
                )
                records.append(record)
                memory.forbid({
                    "milestone_id": milestone.id,
                    "reason": reason,
                })
                memory.update(record, ledger)
                continue

            before_fingerprint = observation.state_fingerprint
            execution_results = self.executor.execute_operations(
                actions,
                step_offset=step_offset,
                include_turn_step=True,
            )
            execution_succeeded = bool(execution_results) and all(
                result.get("success", False)
                for result in execution_results
            )
            step_offset += len(execution_results)
            after = self.observer.collect(execution_results)
            validation = ValidatorRegistry.validate(
                milestone.postconditions,
                after,
            )
            if any(
                not result.get("success", False)
                for result in execution_results
            ):
                validation = validation.model_copy(update={
                    "passed": False,
                    "message": "执行存在失败，里程碑不能完成",
                })
            self._settle_agent_cache(
                execution_succeeded and validation.passed
            )
            ledger.mark_from_validation(milestone.id, validation)
            record = MultiTurnRecord(
                turn=turn_number,
                observation_before=observation,
                decision=decision,
                execution_results=execution_results,
                observation_after=after,
                validation=validation,
            )
            records.append(record)
            memory.update(record, ledger)
            forbidden = guard.record_after(
                milestone.id,
                actions,
                execution_results,
                before_fingerprint,
                after.state_fingerprint,
                validation.passed,
            )
            if forbidden is not None:
                memory.forbid(forbidden)
            observation = after

            if validation.passed and ledger.is_complete():
                if not self._valid_finish_state(observation):
                    return self._result(
                        status="blocked",
                        user_goal=user_goal,
                        records=records,
                        observation=observation,
                        started=started,
                        llm_calls=llm_calls,
                        message="账本已完成，但最终实体状态无效",
                        ledger=ledger,
                    )
                return self._result(
                    status="completed",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message="全部里程碑通过机器验收",
                    ledger=ledger,
                )

            if milestone.failure_count >= milestone.max_failures:
                ledger.mark_failed(
                    milestone.id,
                    (
                        "同一里程碑连续 "
                        f"{milestone.max_failures} 轮未通过机器验收"
                    ),
                )
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=f"里程碑 {milestone.id} 连续失败",
                    ledger=ledger,
                )

        return self._result(
            status="blocked",
            user_goal=user_goal,
            records=records,
            observation=observation,
            started=started,
            llm_calls=llm_calls,
            message=f"达到最大轮数 {max_turns}，任务尚未完成",
            ledger=ledger,
        )

    def run_blueprint(
        self,
        user_goal: str,
        task_plan: TaskPlan,
        batches: list[list[SwOperation]],
        *,
        llm_calls: int = 0,
        max_repair_turns_per_milestone: int = 3,
    ) -> MultiTurnResult:
        """Execute a global blueprint, then repair failed milestones in-place."""
        started = time.perf_counter()
        ledger = MilestoneLedger(task_plan)
        memory = CompressedMemory(user_goal, task_plan)
        guard = IdempotencyGuard(self.repeated_action_limit)
        records: list[MultiTurnRecord] = []
        observation = ModelObservation(
            errors={"session": "新任务尚未创建零件"},
        )
        step_offset = 0
        turn_number = 0
        dynamic_turns = min(
            100,
            len(batches) * (1 + max_repair_turns_per_milestone),
        )
        max_turns = self.user_max_turns or dynamic_turns

        if len(batches) != len(task_plan.milestones):
            raise ValueError(
                "蓝图批次数必须与里程碑数一致: "
                f"{len(batches)} != {len(task_plan.milestones)}"
            )

        def execute_turn(
            decision: AgentTurn,
            milestone: Milestone,
        ) -> ValidationResult:
            nonlocal observation, step_offset, turn_number
            turn_number += 1
            before = observation
            execution_results = self.executor.execute_operations(
                decision.actions,
                step_offset=step_offset,
                include_turn_step=True,
            )
            execution_succeeded = bool(execution_results) and all(
                result.get("success", False)
                for result in execution_results
            )
            step_offset += len(execution_results)
            observation = self.observer.collect(execution_results)
            validation = ValidatorRegistry.validate(
                milestone.postconditions,
                observation,
            )
            if any(
                not result.get("success", False)
                for result in execution_results
            ):
                validation = validation.model_copy(update={
                    "passed": False,
                    "message": "执行存在失败，里程碑不能完成",
                })
            self._settle_agent_cache(
                execution_succeeded and validation.passed
            )
            ledger.mark_from_validation(milestone.id, validation)
            record = MultiTurnRecord(
                turn=turn_number,
                observation_before=before,
                decision=decision,
                execution_results=execution_results,
                observation_after=observation,
                validation=validation,
            )
            records.append(record)
            memory.update(record, ledger)
            guard.record_after(
                milestone.id,
                decision.actions,
                execution_results,
                before.state_fingerprint,
                observation.state_fingerprint,
                validation.passed,
            )
            return validation

        for batch_index, batch in enumerate(batches, 1):
            if turn_number >= max_turns:
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=f"达到用户最大轮数 {max_turns}",
                    ledger=ledger,
                )
            milestone = ledger.current()
            if milestone is None:
                break
            if len(batch) > min(self.max_actions_per_turn, milestone.max_actions):
                raise ValueError(
                    f"里程碑 {milestone.id} 蓝图批次超过 5 个高层操作"
                )
            decision = AgentTurn(
                decision=AgentDecision.ACT,
                milestone_id=milestone.id,
                progress_summary=(
                    f"执行全局蓝图阶段 {batch_index}/{len(batches)}"
                ),
                actions=batch,
                check_summary="执行后读取结构化模型并验收当前里程碑",
            )
            validation = execute_turn(decision, milestone)
            if validation.passed:
                continue

            repaired = False
            for _repair_attempt in range(max_repair_turns_per_milestone):
                if turn_number >= max_turns:
                    break
                repair = self.llm_client.decide_agent_turn(
                    user_goal,
                    observation,
                    memory.to_payload(ledger),
                    turn_number + 1,
                    current_milestone=milestone,
                    repair_required=True,
                )
                llm_calls += 1
                repair = repair.model_copy(
                    update={"milestone_id": milestone.id}
                )
                if repair.decision != AgentDecision.ACT:
                    self._settle_agent_cache(False)
                    rejected_validation = ValidatorRegistry.validate(
                        milestone.postconditions,
                        observation,
                    )
                    rejected = self._rejected_record(
                        turn_number + 1,
                        observation,
                        repair.model_copy(update={
                            "check_summary": (
                                "机器验收未通过，拒绝结束；请提出修复动作"
                            ),
                        }),
                        rejected_validation,
                    )
                    turn_number += 1
                    records.append(rejected)
                    memory.update(rejected, ledger)
                    continue

                repair_actions = repair.actions[
                    : min(self.max_actions_per_turn, milestone.max_actions)
                ]
                repair_actions = self._stabilize_repair_face_selection(
                    repair_actions,
                    observation,
                )
                if self._valid_finish_state(observation):
                    repair_actions = [
                        action for action in repair_actions
                        if action.operation != OperationType.NEW_PART
                    ]
                if not repair_actions:
                    self._settle_agent_cache(False)
                    continue
                allowed, reason = guard.check_before(
                    milestone.id,
                    repair_actions,
                )
                if not allowed:
                    self._settle_agent_cache(False)
                    memory.forbid({
                        "milestone_id": milestone.id,
                        "reason": reason,
                    })
                    continue
                repair = repair.model_copy(update={"actions": repair_actions})
                validation = execute_turn(repair, milestone)
                if validation.passed:
                    repaired = True
                    break

            if not repaired:
                return self._result(
                    status="blocked",
                    user_goal=user_goal,
                    records=records,
                    observation=observation,
                    started=started,
                    llm_calls=llm_calls,
                    message=f"里程碑 {milestone.id} 蓝图执行及原位修复均失败",
                    ledger=ledger,
                )

        status = "completed" if ledger.is_complete() else "blocked"
        return self._result(
            status=status,
            user_goal=user_goal,
            records=records,
            observation=observation,
            started=started,
            llm_calls=llm_calls,
            message=(
                "全局蓝图全部里程碑通过机器验收"
                if status == "completed"
                else "全局蓝图执行完毕，但机器验收未全部通过"
            ),
            ledger=ledger,
        )

