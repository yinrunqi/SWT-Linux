"""发给 LLM 的里程碑“公开视图”：保留要做什么，隐藏验收目标数值。

评测时里程碑的 postconditions 往往直接来自 GT（期望坐标、齿轮比、包围盒……）。
若把它们原样放进 prompt，模型只需照抄目标值，benchmark 就测不出建模能力。
公开视图只保留条件类型和描述性参数，evidence 只保留实测值与定性方向。
"""

from __future__ import annotations

from typing import Any

from .schemas import Milestone

# 描述“对哪个对象检查”的参数，不含目标数值，可以公开。
PUBLIC_PARAM_KEYS = frozenset({"keyword", "path", "face_key", "path_contains"})

# evidence 中暴露目标或容差的字段。
_TARGET_KEY_HINTS = ("expected", "minimum", "maximum", "target", "tolerance", "exact", "wanted")


def _is_target_key(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _TARGET_KEY_HINTS)


def _direction(actual: float, low: float, high: float) -> str:
    if actual < low:
        return "偏小"
    if actual > high:
        return "偏大"
    return "达标"


def _axis_directions(actual: list, expected: list, tolerance: float) -> list[str]:
    return [
        _direction(float(a), float(e) - tolerance, float(e) + tolerance)
        for a, e in zip(actual, expected)
    ]


def _strip_targets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_targets(item)
            for key, item in value.items()
            if not _is_target_key(str(key))
        }
    if isinstance(value, list):
        return [_strip_targets(item) for item in value]
    return value


def public_evidence(evidence: dict, kinds: dict[str, str] | None = None) -> dict:
    """把验证 details 转成不含目标值的形式，并按条件类型补充“偏大/偏小”提示。"""
    kinds = kinds or {}
    public: dict = {}
    for key, detail in (evidence or {}).items():
        kind = kinds.get(key) or (key.split(":", 1)[1] if ":" in str(key) else "")
        if not isinstance(detail, dict):
            public[key] = detail
            continue
        item = _strip_targets(detail)
        try:
            if kind == "bbox_axes" and detail.get("expected_mm"):
                item["axis_status"] = _axis_directions(
                    detail.get("actual_mm") or [],
                    detail["expected_mm"],
                    float(detail.get("tolerance_mm", 0)),
                )
            elif kind == "volume_range" and detail.get("actual_mm3") is not None:
                item["status"] = _direction(
                    float(detail["actual_mm3"]),
                    float(detail.get("minimum_mm3", float("-inf"))),
                    float(detail.get("maximum_mm3", float("inf"))),
                )
            elif kind == "component_positions":
                tolerance = float(detail.get("tolerance_mm", 0))
                item["checks"] = [
                    {
                        "path_contains": check.get("path_contains"),
                        "actual_mm": check.get("actual_mm"),
                        "passed": check.get("passed"),
                        "axis_status": _axis_directions(
                            check.get("actual_mm") or [],
                            check.get("expected_mm") or [],
                            tolerance,
                        ),
                    }
                    for check in detail.get("checks") or []
                ]
            elif kind in ("feature_count", "mate_count", "mate_type_count", "component_count"):
                actual = detail.get("actual")
                minimum = detail.get("minimum")
                exact = detail.get("exact")
                if isinstance(actual, (int, float)):
                    if exact is not None:
                        item["status"] = _direction(actual, float(exact), float(exact))
                    elif minimum is not None:
                        item["status"] = "不足" if actual < float(minimum) else "达标"
            elif kind == "gear_ratios":
                item["status"] = "达标" if detail.get("actual") and len(
                    detail.get("actual") or []
                ) == len(detail.get("expected") or []) and all(
                    abs(float(a) - float(e)) <= float(detail.get("tolerance", 0))
                    for a, e in zip(sorted(detail["actual"]), sorted(detail["expected"]))
                ) else "不匹配"
        except (TypeError, ValueError):
            pass
        public[key] = item
    return public


def _postcondition_kinds(milestone: Milestone) -> dict[str, str]:
    return {
        f"{index}:{spec.kind}": spec.kind
        for index, spec in enumerate(milestone.postconditions)
    }


def public_milestone(milestone: Milestone) -> dict:
    """LLM 可见的当前里程碑：id、标题、依赖、条件类型与描述性参数、定性 evidence。"""
    return {
        "id": milestone.id,
        "title": milestone.title,
        "depends_on": list(milestone.depends_on),
        "postconditions": [
            {
                "kind": spec.kind,
                "params": {
                    key: value
                    for key, value in spec.params.items()
                    if key in PUBLIC_PARAM_KEYS
                },
            }
            for spec in milestone.postconditions
        ],
        "max_actions": milestone.max_actions,
        "max_failures": milestone.max_failures,
        "status": milestone.status.value,
        "failure_count": milestone.failure_count,
        "evidence": public_evidence(milestone.evidence, _postcondition_kinds(milestone)),
    }


def public_ledger_summary(milestones: list[Milestone]) -> list[dict]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "status": item.status.value,
            "depends_on": item.depends_on,
            "failure_count": item.failure_count,
            "max_failures": item.max_failures,
            "evidence": public_evidence(item.evidence, _postcondition_kinds(item)),
        }
        for item in milestones
    ]
