"""Guardrails that keep benchmark answers out of production modeling."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from planner.llm_client import SwLLMClient
from planner.task_decomposer import TaskDecomposer


ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / "planner"
SKILLS = PLANNER / "skills"


def test_production_skills_do_not_name_benchmark_cases() -> None:
    case_id = re.compile(r"\b(?:B\d{2}|C\d+)\b")
    forbidden_phrases = (
        "必须完全按此模板",
        "完整 17 步",
        "所有已经打通的样例",
        "骰子",
        "收纳盒",
        "键盘键帽",
        "门锁扣板",
        "开关面板",
    )
    violations: list[str] = []
    for path in SKILLS.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if case_id.search(text) or any(item in text for item in forbidden_phrases):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"benchmark references in production skills: {violations}"


def test_operation_schema_descriptions_do_not_name_benchmarks() -> None:
    text = (PLANNER / "schemas.py").read_text(encoding="utf-8")
    for forbidden in ("骰子", "收纳盒", "键盘键帽", "门锁扣板", "开关面板"):
        assert forbidden not in text


def test_production_skills_do_not_embed_numbered_golden_plans() -> None:
    numbered_operation = re.compile(
        r"(?m)^\s*\d+\.\s+(?:new_part|select_plane|sketch_|feature_)"
    )
    violations = [
        str(path.relative_to(ROOT))
        for path in SKILLS.rglob("*.md")
        if numbered_operation.search(path.read_text(encoding="utf-8"))
    ]
    assert not violations, f"numbered operation plans in production skills: {violations}"


def test_task_decomposer_has_no_named_object_recipes() -> None:
    source = inspect.getsource(TaskDecomposer)
    for forbidden in ("骰子", "色子", "dice_body", "收纳盒", "storage box"):
        assert forbidden not in source


def test_default_client_uses_routed_skills() -> None:
    signature = inspect.signature(SwLLMClient)
    assert signature.parameters["skill_mode"].default == "routed"


def test_vision_stage_does_not_receive_modeling_operation_manual() -> None:
    source = inspect.getsource(SwLLMClient._extract_three_view_ir)
    assert "build_system_prompt" not in source
    assert 'split("## 阶段 B"' in source


def test_planner_does_not_import_evaluation_fixtures() -> None:
    forbidden = re.compile(r"(?:from|import)\s+.*(?:datasets|eval|test)")
    violations: list[str] = []
    for path in PLANNER.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if forbidden.search(text):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"fixture imports in production planner: {violations}"
