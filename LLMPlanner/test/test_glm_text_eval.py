"""GLM Text-to-Part 配置与请求协议的纯离线测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eval import eval_text_singleturn_qwen as bench
from planner.workflow.agent import (
    WorkflowAgent,
    _prepare_glm_request,
    default_reasoning_effort,
)
from web import app as web_app


def _request(**overrides):
    request = {
        "model": "GLM-4.6V",
        "messages": [
            {"role": "system", "content": "使用工具建模。"},
            {"role": "user", "content": "创建零件。"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "finish",
                "description": "完成",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
            },
        }],
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 32000,
        "reasoning_effort": "medium",
    }
    request.update(overrides)
    return request


def test_glm_request_is_strict_utf8_single_tool_call_protocol():
    source = _request()
    prepared = _prepare_glm_request(source)

    assert "strict" not in source["tools"][0]["function"]
    assert prepared["tools"][0]["function"]["strict"] is True
    assert prepared["tools"][0]["function"]["parameters"]["additionalProperties"] is False
    assert prepared["parallel_tool_calls"] is False
    assert prepared["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True},
    }
    assert prepared["messages"][1]["content"] == "创建零件。"


@pytest.mark.parametrize("max_tokens", [0, 65536, True])
def test_glm_request_rejects_output_budget_outside_total_context(max_tokens):
    with pytest.raises(ValueError, match="max_tokens"):
        _prepare_glm_request(_request(max_tokens=max_tokens))


def test_glm_request_rejects_broken_unicode_before_provider_call():
    request = _request()
    request["messages"][1]["content"] = "损坏\ufffd题面"
    with pytest.raises(ValueError, match="replacement character"):
        _prepare_glm_request(request)


def test_glm_agent_defaults_match_deployment_contract():
    session = SimpleNamespace(version="v2")
    agent = WorkflowAgent(None, "GLM-4.6V", session, version="v2", compact_context=True)
    assert agent.context_limit == 65536
    assert agent.max_output_tokens == 32768
    assert agent.reasoning_effort == "medium"
    assert default_reasoning_effort("basic/GLM-4.6V") == "medium"


def test_glm_agent_applies_protocol_to_actual_completion_request():
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        message = SimpleNamespace(content="", reasoning_content=None, tool_calls=[])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            model_dump=lambda mode="json": {},
        )

    client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create),
    ))
    session = SimpleNamespace(version="v2")
    WorkflowAgent(
        client,
        "GLM-4.6V",
        session,
        version="v2",
        max_llm_calls=1,
    ).run("Create a 20 mm cube.")

    assert len(requests) == 1
    sent = requests[0]
    assert sent["reasoning_effort"] == "medium"
    assert sent["parallel_tool_calls"] is False
    assert sent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert all(tool["function"]["strict"] is True for tool in sent["tools"])
    assert all(
        tool["function"]["parameters"]["additionalProperties"] is False
        for tool in sent["tools"]
    )


def test_glm_provider_uses_dedicated_environment():
    assert web_app._provider_env("glmlocal") == (
        "GLMLOCAL_API_KEY",
        "GLMLOCAL_BASE_URL",
    )


def test_all100_glm_preflight_does_not_call_llm_or_solidworks(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "GLMLOCAL_API_KEY=test-key\n"
        "GLMLOCAL_BASE_URL=http://glm.example/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bench, "LLM_ROOT", tmp_path)
    monkeypatch.setattr(
        bench,
        "_query_balance",
        lambda *_args, **_kwargs: pytest.fail("preflight must not query any service"),
    )
    monkeypatch.setattr(
        bench.sys,
        "argv",
        [
            "eval_text_singleturn_qwen.py",
            "--provider", "glmlocal",
            "--action-level", "L1_v2",
            "--compact-context",
            "--split", "all100",
            "--lang", "en",
            "--preflight-only",
        ],
    )

    assert bench.main() == 0
