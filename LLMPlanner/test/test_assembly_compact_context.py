"""装配 workflow 独立 compact_context：请求压缩、溢出恢复与 checkpoint 升级。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from planner.assembly_workflow.agent import AssemblyAgent


def _reply(call_id: str, name: str, args: dict | None = None):
    call = SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args or {}, ensure_ascii=False)),
    )
    message = SimpleNamespace(content="很长的分析 " + "x" * 1000, tool_calls=[call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
        usage=None,
        model_dump=lambda mode="python": {},
    )


def _overflow(prompt_tokens: int = 9000, requested: int = 16000) -> Exception:
    return ValueError(
        "Error code: 400 - {'error': {'message': \"This model's maximum context length is 10000 tokens. "
        f"However, you requested {requested} output tokens and your prompt contains at least {prompt_tokens} "
        "input tokens, for a total of at least 10001 tokens.\"}}"
    )


class Client:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Session:
    saved = True
    last_motion = None
    aliases = {}
    v3 = True

    def call(self, name, args):
        if name == "get_assembly_state":
            return {
                "ok": True,
                "components": [{"alias": "frame", "free": []}],
                "mates": [{"name": "m1"}],
                "mobility": 0,
            }
        if name == "finish":
            return {"ok": True}
        return {
            "ok": True,
            "name": f"{name}-1",
            "after": {"mobility": 1, "redundant": 0, "floating": []},
            "verbose_unused": "y" * 2000,
        }


def test_assembly_compact_context_keeps_recent_rounds_and_full_checkpoint_history():
    client = Client([
        _reply("a", "mate"),
        _reply("b", "delete_mate"),
        _reply("c", "mate"),
        _reply("d", "finish", {"summary": "ok"}),
    ])
    outcome = AssemblyAgent(
        client, "m", Session(), tools=[], compact_context=True, max_llm_calls=4,
    ).run("装配")

    assert outcome["status"] == "finished"
    assert outcome["compact_context"] is True
    assert outcome["request_modes"] == ["compact_history"] * 4
    fourth = client.requests[3]["messages"]
    tool_messages = {
        message["tool_call_id"]: message["content"]
        for message in fourth
        if message.get("role") == "tool"
    }
    assert json.loads(tool_messages["a"]) == {
        "ledger": True,
        "tool": "mate",
        "ok": True,
        "name": "mate-1",
        "after": {"mobility": 1, "redundant": 0, "floating": []},
    }
    assert "verbose_unused" in json.loads(tool_messages["b"])
    assert "verbose_unused" in json.loads(tool_messages["c"])
    full = {
        message["tool_call_id"]: message["content"]
        for message in outcome["messages"]
        if message.get("role") == "tool"
    }
    assert "verbose_unused" in json.loads(full["a"])


def test_assembly_compact_context_retries_with_current_state_after_overflow():
    client = Client([
        _reply("a", "mate"),
        _overflow(),
        _reply("b", "finish", {"summary": "recovered"}),
    ])
    outcome = AssemblyAgent(
        client, "m", Session(), tools=[], compact_context=True,
        context_limit=10000, max_llm_calls=2,
    ).run("装配")

    assert outcome["status"] == "finished"
    assert outcome["compact_retries"] == 1
    assert outcome["request_modes"] == ["compact_history", "emergency_compact"]
    assert "当前装配状态" in client.requests[-1]["messages"][-1]["content"]


def test_context_exceeded_checkpoint_can_resume_after_enabling_assembly_compaction(tmp_path):
    checkpoint = tmp_path / "assembly.checkpoint.json"
    first = Client([_reply("a", "mate"), _overflow()])
    stopped = AssemblyAgent(
        first, "m", Session(), tools=[], context_limit=10000,
        checkpoint_path=checkpoint, max_llm_calls=3,
    ).run("装配")
    assert stopped["status"] == "context_exceeded"

    second = Client([_reply("b", "finish", {"summary": "resumed"})])
    resumed = AssemblyAgent(
        second, "m", Session(), tools=[], context_limit=10000,
        checkpoint_path=checkpoint, compact_context=True, max_llm_calls=3,
    ).run("装配", resume=True)

    assert resumed["status"] == "finished"
    assert resumed["resumed"]["from_llm_calls"] == 1
    assert resumed["request_modes"][-1] == "compact_history"
