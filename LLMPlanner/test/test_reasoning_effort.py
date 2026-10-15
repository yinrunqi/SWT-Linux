"""reasoning_effort 从 /api/run 传到 L1 Agent 或 L0 Plan；未指定时请求体不变。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import web.app as web_app
from planner.assembly_workflow.agent import AssemblyAgent
from planner.llm_client import PlanOutputExhaustedError, SwLLMClient
from planner.workflow.agent import WorkflowAgent


class RecordingCompletions:
    def __init__(self):
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        message = SimpleNamespace(content="", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
        )


class StubSession:
    def call(self, name, args):
        return {"ok": True}

    def state_summary(self):
        return {}


def _agent_requests(reasoning_effort):
    completions = RecordingCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    outcome = WorkflowAgent(
        client,
        "model",
        StubSession(),
        max_llm_calls=2,
        reasoning_effort=reasoning_effort,
    ).run("goal")
    return completions.requests, outcome


def test_agent_omits_reasoning_effort_by_default():
    requests, outcome = _agent_requests(None)
    assert requests and all("reasoning_effort" not in item for item in requests)
    assert outcome["reasoning_effort"] is None
    assert outcome["finish_reasons"] == ["stop", "stop"]
    assert len(outcome["llm_responses"]) == 2


def test_agent_sends_reasoning_effort_on_every_call():
    requests, outcome = _agent_requests("medium")
    assert [item["reasoning_effort"] for item in requests] == ["medium", "medium"]
    assert outcome["reasoning_effort"] == "medium"


class StubAssemblySession(StubSession):
    last_motion = None
    aliases: dict = {}
    saved = False


def test_assembly_agent_reasoning_effort_and_raw_responses():
    for effort, expected in ((None, False), ("medium", True)):
        completions = RecordingCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        outcome = AssemblyAgent(
            client, "model", StubAssemblySession(), max_llm_calls=2, tools=[], reasoning_effort=effort,
        ).run("goal")
        assert all(("reasoning_effort" in item) is expected for item in completions.requests)
        assert outcome["reasoning_effort"] == effort
        assert len(outcome["llm_responses"]) == len(completions.requests) == 2


def test_claude_sonnet_5_defaults_to_medium_on_every_agent():
    for model in ("claude-sonnet-5", "basic/claude-sonnet-5", "Basic/Claude-Sonnet-5"):
        completions = RecordingCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        part = WorkflowAgent(client, model, StubSession(), max_llm_calls=1).run("goal")
        asm = AssemblyAgent(client, model, StubAssemblySession(), max_llm_calls=1, tools=[]).run("goal")
        assert part["reasoning_effort"] == asm["reasoning_effort"] == "medium"
        assert [item["reasoning_effort"] for item in completions.requests] == ["medium", "medium"]
    explicit = WorkflowAgent(None, "claude-sonnet-5", StubSession(), reasoning_effort="high")
    assert explicit.reasoning_effort == "high"
    for model in ("claude-sonnet-5-5", "Qwen3.5-397B-A17B", "gpt-6-astra"):
        assert WorkflowAgent(None, model, StubSession()).reasoning_effort is None


def test_qwen38_uses_same_context_limit_for_every_track():
    for model in ("Qwen3.8-Flash-Next-FP8", "qwen3.8-flash-next-fp8"):
        agent = WorkflowAgent(None, model, StubSession())
        assert agent.context_limit == 73728


def _post(json_body, monkeypatch):
    captured = {}

    def fake_run_workflow_l1(instruction, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(web_app, "executor", object())
    monkeypatch.setattr(web_app, "_run_workflow_l1", fake_run_workflow_l1)
    response = web_app.app.test_client().post("/api/run", json={
        "instruction": "创建 20mm 立方体",
        "api_key": "test-key",
        **json_body,
    })
    return response, captured


def test_api_run_forwards_reasoning_effort_to_l1(monkeypatch):
    response, captured = _post({"action_level": "L1", "reasoning_effort": "Medium"}, monkeypatch)
    assert response.status_code == 200
    assert captured["reasoning_effort"] == "medium"

    response, captured = _post({"action_level": "L1"}, monkeypatch)
    assert response.status_code == 200
    assert captured["reasoning_effort"] is None


def test_api_run_rejects_unknown_reasoning_effort(monkeypatch):
    response, captured = _post({"action_level": "L1", "reasoning_effort": "max"}, monkeypatch)
    assert response.status_code == 400 and not captured


def test_api_run_compact_context_is_strict_opt_in(monkeypatch):
    response, captured = _post({"action_level": "L1_v2"}, monkeypatch)
    assert response.status_code == 200
    assert captured["compact_context"] is False

    response, captured = _post({"action_level": "L1_v2", "compact_context": True}, monkeypatch)
    assert response.status_code == 200
    assert captured["compact_context"] is True

    response, captured = _post({"action_level": "L1_v2", "compact_context": "true"}, monkeypatch)
    assert response.status_code == 400 and not captured
    response, captured = _post({"action_level": "L1", "compact_context": True}, monkeypatch)
    assert response.status_code == 400 and not captured


def test_api_run_forwards_reasoning_effort_to_l0(monkeypatch):
    captured = {}

    class FakeClient:
        def usage_snapshot(self):
            return {}

        def usage_since(self, _before):
            return {"calls": 1, "total_tokens": 10}

        def plan(self, *_args, **_kwargs):
            return SimpleNamespace(steps=[], model_dump=lambda: {
                "user_intent": "test", "steps": [], "notes": "",
            })

    def fake_get_client(*_args, **kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(web_app, "_get_llm_client", fake_get_client)
    monkeypatch.setattr(
        web_app,
        "executor",
        SimpleNamespace(execute_plan=lambda *_args, **_kwargs: []),
    )
    response = web_app.app.test_client().post("/api/run", json={
        "instruction": "创建 20mm 立方体",
        "api_key": "test-key",
        "action_level": "L0",
        "reasoning_effort": "Medium",
        "max_plan_tokens": 32000,
        "enable_thinking": False,
        "use_cache": False,
    })
    assert response.status_code == 200
    assert captured["reasoning_effort"] == "medium"
    assert captured["plan_max_tokens"] == 32000
    assert captured["enable_thinking"] is False


def test_l0_plan_request_sends_reasoning_effort_only_when_configured():
    for effort, expected in ((None, False), ("medium", True)):
        completions = RecordingCompletions()
        completions.create = lambda **kwargs: (
            completions.requests.append(kwargs)
            or SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"user_intent":"test","steps":[],"notes":""}',
                ))],
                usage=None,
            )
        )
        client = SwLLMClient.__new__(SwLLMClient)
        client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        client.model = "model"
        client.reasoning_effort = effort
        client.plan_max_tokens = 32000
        client.enable_thinking = False
        client._call_json_mode([
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "test"},
        ])
        assert ("reasoning_effort" in completions.requests[0]) is expected
        assert completions.requests[0]["max_tokens"] == 32000
        assert completions.requests[0]["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": False},
        }


def test_l0_length_without_content_is_not_retried_or_downgraded():
    completions = RecordingCompletions()

    def exhausted(**kwargs):
        completions.requests.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=None),
                finish_reason="length",
            )],
            usage=None,
        )

    completions.create = exhausted
    client = SwLLMClient.__new__(SwLLMClient)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client.model = "model"
    client.reasoning_effort = "medium"
    client.plan_max_tokens = 32000
    client.enable_thinking = None
    client._use_structured_output = True

    try:
        client._call_api("test", system_prompt="Return JSON.")
    except PlanOutputExhaustedError:
        pass
    else:
        raise AssertionError("expected output exhaustion")

    assert len(completions.requests) == 1
    assert client._use_structured_output is True
