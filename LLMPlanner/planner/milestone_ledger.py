"""Dependency-aware progress ledger for multi-turn modeling."""

from __future__ import annotations

from .schemas import (
    Milestone,
    MilestoneStatus,
    TaskPlan,
    ValidationResult,
)


class MilestoneLedger:
    """Own milestone state; the LLM may propose actions but cannot mark work done."""

    def __init__(self, task_plan: TaskPlan):
        self.task_plan = task_plan.model_copy(deep=True)
        self._by_id = {
            milestone.id: milestone
            for milestone in self.task_plan.milestones
        }
        if len(self._by_id) != len(self.task_plan.milestones):
            raise ValueError("milestone id 必须唯一")
        for milestone in self.task_plan.milestones:
            unknown = set(milestone.depends_on) - set(self._by_id)
            if unknown:
                raise ValueError(
                    f"{milestone.id} 引用了未知依赖: {sorted(unknown)}"
                )

    def current(self) -> Milestone | None:
        for milestone in self.task_plan.milestones:
            if milestone.status not in (
                MilestoneStatus.PENDING,
                MilestoneStatus.IN_PROGRESS,
            ):
                continue
            if all(
                self._by_id[item].status == MilestoneStatus.DONE
                for item in milestone.depends_on
            ):
                if milestone.status == MilestoneStatus.PENDING:
                    milestone.status = MilestoneStatus.IN_PROGRESS
                return milestone
        return None

    def mark_from_validation(
        self,
        milestone_id: str,
        validation: ValidationResult,
    ) -> None:
        milestone = self._by_id[milestone_id]
        if milestone.status == MilestoneStatus.DONE:
            return
        if validation.passed:
            milestone.status = MilestoneStatus.DONE
            milestone.evidence = validation.details
        else:
            milestone.status = MilestoneStatus.IN_PROGRESS
            milestone.failure_count += 1
            milestone.evidence = validation.details

    def mark_failed(self, milestone_id: str, reason: str) -> None:
        milestone = self._by_id[milestone_id]
        milestone.status = MilestoneStatus.FAILED
        milestone.failure_count += 1
        milestone.evidence = {"reason": reason}

    def is_complete(self) -> bool:
        return bool(self.task_plan.milestones) and all(
            item.status in (MilestoneStatus.DONE, MilestoneStatus.SKIPPED)
            for item in self.task_plan.milestones
        )

    def has_terminal_failure(self) -> bool:
        return any(
            item.status == MilestoneStatus.FAILED
            for item in self.task_plan.milestones
        )

    def summary(self) -> list[dict]:
        return [
            {
                "id": item.id,
                "title": item.title,
                "status": item.status.value,
                "depends_on": item.depends_on,
                "failure_count": item.failure_count,
                "evidence": item.evidence,
            }
            for item in self.task_plan.milestones
        ]

