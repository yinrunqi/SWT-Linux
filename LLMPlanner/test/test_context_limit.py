"""离线测试：小上下文模型（输入 + 推理 + 输出共用窗口）超长时压缩输出上限，仍装不下则 context_exceeded。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from planner.workflow.agent import WorkflowAgent, _context_overflow_prompt_tokens

QWEN = "Qwen3.8-Flash-Next-FP8"


def _overflow(prompt_tokens: int, requested: int) -> Exception:
    return ValueError(
        "Error code: 400 - {'error': {'message': \"This model's maximum context length is 73728 tokens. "
        f"However, you requested {requested} output tokens and your prompt contains at least {prompt_tokens} "
        "input tokens, for a total of at least 73729 tokens.\"}}"
    )


def _reply(calls):
    message = SimpleNamespace(content="", reasoning_content=None, tool_calls=[
        SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)
    ])
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=None,
                           model_dump=lambda mode="python": {})


class Client:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Session:
    def call(self, name, args):
        return {"ok": True}

    def state_summary(self):
        return {"bbox_mm": [10, 10, 10], "volume_mm3": 1000.0}


def test_parses_vllm_overflow_message():
    assert _context_overflow_prompt_tokens(_overflow(24769, 8000)) == 24769
    assert _context_overflow_prompt_tokens(ValueError("Error code: 400 - bad tool schema")) is None


def test_shrinks_max_tokens_to_fit_the_window():
    client = Client([_overflow(68000, 16000), _reply([("finish", {"summary": "ok"})])])
    outcome = WorkflowAgent(client, QWEN, Session(), version="v1").run("立方体")
    assert outcome["status"] == "finished"
    assert [r["max_tokens"] for r in client.requests] == [16000, 73728 - 68000 - 64]


def test_stops_cleanly_when_the_window_is_full():
    client = Client([_reply([("get_model_state", {})]), _overflow(72500, 16000)])
    outcome = WorkflowAgent(client, QWEN, Session(), version="v1").run("立方体")
    assert outcome["status"] == "context_exceeded"
    assert "72500" in outcome["error"]
    assert len(client.requests) == 2


def test_truncation_retry_grows_then_shrinks_to_the_window():
    truncated = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content="", reasoning_content=None, tool_calls=[]), finish_reason="length")],
        usage=None, model_dump=lambda mode="python": {})
    client = Client([truncated, truncated, _overflow(50000, 32000), _reply([("finish", {"summary": "ok"})])])
    WorkflowAgent(client, QWEN, Session(), version="v1").run("立方体")
    assert [r["max_tokens"] for r in client.requests] == [16000, 32000, 32000, 73728 - 50000 - 64]


def test_keeps_shrinking_when_the_reported_input_is_only_a_lower_bound():
    client = Client([_overflow(73728 - 32000 + 1, 32000), _overflow(73728 - 24000 + 1, 24000),
                     _reply([("finish", {"summary": "ok"})])])
    agent = WorkflowAgent(client, QWEN, Session(), version="v1", max_tokens=32000)
    outcome = agent.run("立方体")
    assert outcome["status"] == "finished"
    assert [r["max_tokens"] for r in client.requests] == [32000, 24000, 18000]


def test_other_models_keep_reporting_llm_error():
    client = Client([_overflow(68000, 8000)])
    outcome = WorkflowAgent(client, "claude-sonnet-5", Session(), version="v1").run("立方体")
    assert outcome["status"] == "llm_error"
    assert len(client.requests) == 1
