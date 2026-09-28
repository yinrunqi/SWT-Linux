"""Loss-bounded memory for long multi-turn modeling sessions."""

from __future__ import annotations

from collections import Counter, deque

from .milestone_ledger import MilestoneLedger
from .milestone_view import public_evidence, public_ledger_summary
from .schemas import MultiTurnRecord, TaskPlan


class CompressedMemory:
    """Keep durable progress and failures while bounding verbose turn history."""

    def __init__(self, user_goal: str, task_plan: TaskPlan):
        self.user_goal = user_goal
        self.task_plan = task_plan
        self._turns: list[dict] = []
        self._failures: deque[dict] = deque(maxlen=5)
        self._failure_counts: Counter[str] = Counter()
        self._forbidden_actions: list[dict] = []

    def update(
        self,
        record: MultiTurnRecord,
        ledger: MilestoneLedger,
    ) -> None:
        after = record.observation_after
        failed = [
            result
            for result in record.execution_results
            if not result.get("success", False)
        ]
        for result in failed:
            operation = str(result.get("operation", "unknown"))
            self._failure_counts[operation] += 1
            self._failures.append({
                "turn": record.turn,
                "milestone_id": record.decision.milestone_id,
                "operation": operation,
                "message": result.get("message", ""),
            })
        self._turns.append({
            "turn": record.turn,
            "milestone_id": record.decision.milestone_id,
            "decision": record.decision.decision.value,
            "actions": [
                action.model_dump(mode="json")
                for action in record.decision.actions
            ],
            "successful_actions": sum(
                bool(result.get("success"))
                for result in record.execution_results
            ),
            "failed_actions": len(failed),
            "validation": (
                None
                if record.validation is None
                else record.validation.model_dump(mode="json")
            ),
            "state_fingerprint": (
                "" if after is None else after.state_fingerprint
            ),
        })

    @staticmethod
    def _public_turn(turn: dict) -> dict:
        validation = turn.get("validation")
        if not validation:
            return turn
        return {
            **turn,
            "validation": {
                **validation,
                "details": public_evidence(validation.get("details") or {}),
            },
        }

    def forbid(self, action: dict) -> None:
        if action not in self._forbidden_actions:
            self._forbidden_actions.append(action)

    def to_payload(self, ledger: MilestoneLedger, *, hide_targets: bool = False) -> dict:
        last_turns = self._turns[-3:]
        if hide_targets:
            last_turns = [self._public_turn(item) for item in last_turns]
        older = [
            {
                "turn": item["turn"],
                "milestone_id": item["milestone_id"],
                "decision": item["decision"],
                "successful_actions": item["successful_actions"],
                "failed_actions": item["failed_actions"],
                "state_fingerprint": item["state_fingerprint"],
            }
            for item in self._turns[:-3]
        ]
        return {
            "milestones": (
                public_ledger_summary(ledger.task_plan.milestones)
                if hide_targets
                else ledger.summary()
            ),
            "older_turns_compact": older,
            "last_3_turns": last_turns,
            "failure_digest": {
                "by_operation": dict(self._failure_counts),
                "recent": list(self._failures),
            },
            "forbidden_actions": self._forbidden_actions[-10:],
        }

