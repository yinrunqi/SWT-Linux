"""Semantic duplicate and no-progress protection for multi-turn actions."""

from __future__ import annotations

import json
from collections import Counter

from .schemas import SwOperation


def _normalized(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {
            key: _normalized(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


class IdempotencyGuard:
    def __init__(self, failure_limit: int = 2):
        self.failure_limit = max(1, failure_limit)
        self._failures: Counter[str] = Counter()
        self._successful_milestones: set[str] = set()

    @staticmethod
    def signature(
        milestone_id: str,
        actions: list[SwOperation],
    ) -> str:
        payload = {
            "milestone_id": milestone_id,
            "actions": [
                {
                    "operation": action.operation.value,
                    "params": _normalized(action.params),
                }
                for action in actions
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def check_before(
        self,
        milestone_id: str,
        actions: list[SwOperation],
    ) -> tuple[bool, str]:
        if milestone_id in self._successful_milestones:
            return False, f"里程碑 {milestone_id} 已完成，禁止重复执行"
        signature = self.signature(milestone_id, actions)
        if self._failures[signature] >= self.failure_limit:
            return False, "同一语义动作已连续失败，必须更换策略"
        return True, ""

    def record_after(
        self,
        milestone_id: str,
        actions: list[SwOperation],
        results: list[dict],
        before_fingerprint: str,
        after_fingerprint: str,
        milestone_completed: bool,
    ) -> dict | None:
        signature = self.signature(milestone_id, actions)
        failed = any(not result.get("success", False) for result in results)
        no_progress = (
            bool(before_fingerprint)
            and before_fingerprint == after_fingerprint
        )
        if failed or no_progress:
            self._failures[signature] += 1
            return {
                "milestone_id": milestone_id,
                "signature": signature,
                "reason": "execution_failed" if failed else "state_unchanged",
                "attempts": self._failures[signature],
            }
        if milestone_completed:
            self._successful_milestones.add(milestone_id)
        return None

