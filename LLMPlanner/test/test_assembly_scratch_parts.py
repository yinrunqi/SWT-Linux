"""从零装配评测的零件阶段：用 L1_v2 + view_model 建零件，旧 L1 结果不复用（模型与 SolidWorks 都用替身）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(scratch, "_openai", lambda *a, **k: None)
    monkeypatch.setattr(scratch.requests, "post", lambda *a, **k: None)
    return FakeAgent.created


SPEC = {"id": "gear_a", "prompt": "做一个齿轮", "extra_tools": ["spur_gear"]}


@pytest.mark.parametrize("view_tool", [True, False])
@pytest.mark.parametrize("compact_context", [True, False])
def test_generate_part_uses_l1_v2(fake_env, tmp_path, view_tool, compact_context):
    result = scratch.generate_part(SPEC, tmp_path / "gear_a", sw="http://fake", model="m",
                                   max_llm_calls=3, view_tool=view_tool,
                                   compact_context=compact_context)
    (made,) = fake_env
    assert isinstance(made["session"], WorkflowSessionV2)
    assert made["view_tool"] is view_tool
    assert made["extra_tools"] == ["spur_gear"]
    assert made["stream"] is True
    assert made["compact_context"] is compact_context
    assert callable(made["log"])
    assert result["part_workflow"] == {
        "version": "L1_v2",
        "view_tool": view_tool,
        "compact_context": compact_context,
    }


@pytest.mark.parametrize("action_level", ["L0", "L0_mt"])
def test_generate_part_uses_same_single_turn_atomic_for_both_l0_levels(
    monkeypatch, tmp_path, action_level,
):
    import planner.executor as executor_module
    import planner.llm_client as client_module

    class FakeClient:
        def __init__(self, **kwargs):
            self.token_usage = {
                "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "calls": 1,
            }

        def plan(self, prompt):
            return SimpleNamespace(steps=[], notes="", model_dump=lambda **kwargs: {"steps": []})

    class FakeExecutor:
        def __init__(self, **kwargs):
            pass

        def execute_plan(self, plan):
            return []

    monkeypatch.setattr(client_module, "SwLLMClient", FakeClient)
    monkeypatch.setattr(executor_module, "SwExecutor", FakeExecutor)
    monkeypatch.setattr(scratch, "_close", lambda sw: None)
    monkeypatch.setattr(scratch, "_openai", lambda *args, **kwargs: None)
    monkeypatch.setattr(scratch.requests, "post", lambda *args, **kwargs: None)

    result = scratch.generate_part(
        SPEC, tmp_path / action_level, sw="http://fake", model="m",
        max_llm_calls=3, action_level=action_level,
    )

    assert result["part_workflow"] == {
        "version": "L0", "atomic_api": "part-api-v1", "mode": "single_turn",
    }
    assert result["status"] == "finished"


def test_three_action_levels_have_distinct_assembly_metadata():
    assert scratch.assembly_workflow_metadata("L0", "atomic", False)["mode"] == "single_turn"
    assert scratch.assembly_workflow_metadata("L0_mt", "atomic_mt", True) == {
        "version": "L0_mt", "atomic_api": "assembly-api-v1", "mode": "multi_turn",
    }
    assert scratch.assembly_workflow_metadata("L1", "v3", True, True) == {
        "version": "L1_v3", "view_tool": True, "compact_context": True,
    }
    assert scratch.assembly_workflow_metadata("L1", "v3", True, True, True)["speed_optimizations"] is True


def test_assembly_speed_flag_rejects_non_v3_workflow(monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["eval_assembly_scratch.py", "--asm-speed-optimizations", "--workflow", "v2"],
    )
    with pytest.raises(SystemExit, match="2"):
        scratch.main()
    assert "--asm-speed-optimizations 只支持 --action-level L1 --workflow v3" in capsys.readouterr().err


def test_openai_wrapper_passes_streams_through(monkeypatch):
    from openai.resources.chat.completions import Completions

    stream = object()
    monkeypatch.setenv("QWENLOCAL_BASE_URL", "http://qwen.local/v1")
    monkeypatch.delenv("QWENLOCAL_API_KEY", raising=False)
    monkeypatch.setattr(Completions, "create", lambda self, **kwargs: stream)
    client = scratch._openai()

    assert client.timeout == 600
    assert str(client.base_url).startswith("http://qwen.local/v1")
    assert client.chat.completions.create(model="m", messages=[], stream=True) is stream


def test_openai_wrapper_does_not_replay_context_overflow(monkeypatch):
    from openai.resources.chat.completions import Completions

    calls = []

    def overflow(self, **kwargs):
        calls.append(kwargs)
        raise ValueError("maximum context length is 73728 tokens. However, your prompt contains at least "
                         "70000 input tokens")

    monkeypatch.setenv("QWENLOCAL_BASE_URL", "http://qwen.local/v1")
    monkeypatch.setattr(Completions, "create", overflow)
    monkeypatch.setattr(scratch.time, "sleep", lambda s: pytest.fail("不应等待重试"))
    with pytest.raises(ValueError):
        scratch._openai().chat.completions.create(model="m", messages=[])
    assert len(calls) == 1


def test_call_detail_reads_raw_response():
    raw = {"choices": [{"finish_reason": "tool_calls", "message": {"content": "ab", "tool_calls": [{}, {}]}}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 5, "completion_tokens_details": {"reasoning_tokens": 3}}}
    assert scratch._call_detail(raw) == {
        "finish_reason": "tool_calls", "completion_tokens": 5, "reasoning_tokens": 3, "prompt_tokens": 10,
        "prompt_cache_hit_tokens": None, "n_tool_calls": 2, "text_chars": 2,
    }


def test_grade_step_falls_back_to_occ_python(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_occ(name, *args, **kwargs):
        if name.startswith("geomcheck.occ_part"):
            raise ImportError("No module named 'OCP'")
        return real_import(name, *args, **kwargs)

    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        out = Path(cmd[cmd.index("--output") + 1])
        out.write_text(json.dumps([{"step": "x", "score": 100.0, "accepted": True}]), encoding="utf-8")

    occ_python = tmp_path / "python.exe"
    occ_python.write_text("")
    monkeypatch.setattr(builtins, "__import__", no_occ)
    monkeypatch.setattr(scratch, "OCC_PYTHON", occ_python)
    monkeypatch.setattr(scratch.subprocess, "run", fake_run)

    grade = scratch._grade_step(tmp_path / "gear_a.step", tmp_path / "gear_a.yaml")

    assert grade == {"score": 100.0, "accepted": True}
    assert commands[0][0] == str(occ_python) and "geomcheck.rubric" in commands[0]


def test_reused_part_with_skipped_grade_is_graded(fake_env, tmp_path, monkeypatch):
    dest = tmp_path / "gear_a"
    first = scratch.generate_part(SPEC, dest, sw="http://fake", model="m", max_llm_calls=3)
    sldprt, step = dest.with_suffix(".SLDPRT"), dest.with_suffix(".step")
    sldprt.write_text("x")
    step.write_text("x")
    first.update(artifacts={"sldprt": str(sldprt), "step": str(step)}, accepted=False, score=None,
                 grade={"accepted": False, "score": None, "skipped": True})
    dest.with_suffix(".result.json").write_text(json.dumps(first), encoding="utf-8")
    monkeypatch.setattr(scratch, "_grade_step", lambda s, r: {"accepted": True, "score": 100.0})

    again = scratch.generate_part(SPEC, dest, sw="http://fake", model="m", max_llm_calls=3, resume=True)

    assert again["reused"] is True and again["accepted"] is True and again["score"] == 100.0
    saved = json.loads(dest.with_suffix(".result.json").read_text(encoding="utf-8"))
    assert saved["grade"] == {"accepted": True, "score": 100.0}


def test_defaults_match_text2cad_qwen():
    assert scratch.PROVIDER == "qwenlocal"
    assert scratch.MODEL == "Qwen3.8-Flash-Next-FP8"
    assert scratch.MODEL_REASONING_EFFORTS["qwen3.8-flash-next-fp8"] == "medium"


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
