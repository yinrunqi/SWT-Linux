"""从零装配评测的零件阶段：用 L1_v2 + view_model 建零件，旧 L1 结果不复用（模型与 SolidWorks 都用替身）。"""

from __future__ import annotations

import json

import pytest

import planner.workflow.agent as workflow_agent
from eval import eval_assembly_scratch as scratch
from planner.workflow.tools_v2 import WorkflowSessionV2


class FakeAgent:
    created: list[dict] = []

    def __init__(self, client, model, session, **kwargs):
        FakeAgent.created.append({"session": session, **kwargs})

    def run(self, prompt, resume=False):
        return {"status": "finished", "token_usage": {}, "llm_calls": 1, "tool_calls": []}


@pytest.fixture
def fake_env(monkeypatch):
    FakeAgent.created = []
    monkeypatch.setattr(workflow_agent, "WorkflowAgent", FakeAgent)
    monkeypatch.setattr(WorkflowSessionV2, "start_part", lambda self: None)
    monkeypatch.setattr(scratch, "_close", lambda sw: None)
    monkeypatch.setattr(scratch, "_openai", lambda: None)
    monkeypatch.setattr(scratch.requests, "post", lambda *a, **k: None)
    return FakeAgent.created


SPEC = {"id": "gear_a", "prompt": "做一个齿轮", "extra_tools": ["spur_gear"]}


@pytest.mark.parametrize("view_tool", [True, False])
def test_generate_part_uses_l1_v2(fake_env, tmp_path, view_tool):
    result = scratch.generate_part(SPEC, tmp_path / "gear_a", sw="http://fake", model="m",
                                   max_llm_calls=3, view_tool=view_tool)
    (made,) = fake_env
    assert isinstance(made["session"], WorkflowSessionV2)
    assert made["view_tool"] is view_tool
    assert made["extra_tools"] == ["spur_gear"]
    assert result["part_workflow"] == {"version": "L1_v2", "view_tool": view_tool}


def test_resume_does_not_reuse_l1_part(fake_env, tmp_path):
    dest = tmp_path / "gear_a"
    sldprt = dest.with_suffix(".SLDPRT")
    sldprt.write_text("x")
    dest.with_suffix(".checkpoint.json").write_text("{}")
    old = {"status": "finished", "artifacts": {"sldprt": str(sldprt)}}
    dest.with_suffix(".result.json").write_text(json.dumps(old), encoding="utf-8")

    result = scratch.generate_part(SPEC, dest, sw="http://fake", model="m", max_llm_calls=3, resume=True)

    assert not result.get("reused")
    assert len(fake_env) == 1
    assert not dest.with_suffix(".checkpoint.json").exists()


def test_resume_reuses_matching_part(fake_env, tmp_path):
    dest = tmp_path / "gear_a"
    first = scratch.generate_part(SPEC, dest, sw="http://fake", model="m", max_llm_calls=3)
    sldprt = dest.with_suffix(".SLDPRT")
    sldprt.write_text("x")
    first["artifacts"] = {"sldprt": str(sldprt)}
    dest.with_suffix(".result.json").write_text(json.dumps(first), encoding="utf-8")

    again = scratch.generate_part(SPEC, dest, sw="http://fake", model="m", max_llm_calls=3, resume=True)

    assert again["reused"] is True
    assert len(fake_env) == 1
