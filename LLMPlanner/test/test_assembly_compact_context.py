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


def _multi_reply(*calls):
    tool_calls = [
        SimpleNamespace(
            id=call_id,
            function=SimpleNamespace(name=name, arguments=json.dumps(args or {}, ensure_ascii=False)),
        )
        for call_id, name, args in calls
    ]
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=tool_calls),
            finish_reason="tool_calls",
        )],
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
    assert all("parallel_tool_calls" not in request for request in client.requests)
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


class SpeedSession(Session):
    saved = False
    assembly_path = "result.SLDASM"

    def __init__(self):
        self.aliases = {"frame": "frame-1"}
        self.snapshot_calls = 0
        self.restore_calls = 0
        self.calls = []

    def call(self, name, args):
        self.calls.append(name)
        return super().call(name, args)

    def compact_state_digest(self):
        return {
            "version": 3,
            "components": [{"alias": "frame", "component": "frame-1", "fixed": True}],
            "mates": [{"name": "m1", "type": "concentric", "components": ["frame", "arm"]}],
            "floating": [],
            "saved": self.saved,
            "validation": {"motion": None, "pose": None, "dof": None},
            "full_state_age_mutations": 1,
        }

    def progress_snapshot(self):
        return {
            "components": 1,
            "mates": 1,
            "fixed": 1,
            "floating": 0,
            "broken": 0,
            "over_defined": 0,
            "mobility": 0,
            "saved": self.saved,
            "motion_passed": False,
            "pose_passed": False,
            "dof_passed": False,
            "version": 3,
        }

    def save_best_snapshot(self, path):
        self.snapshot_calls += 1
        return str(path)

    def restore_best_snapshot(self, path, final_path):
        self.restore_calls += 1


def test_speed_mode_sends_current_digest_and_parallel_tool_hint():
    client = Client([
        _multi_reply(
            ("a", "insert_component", {"part": "a.SLDPRT", "name": "a"}),
            ("b", "ground_component", {"component": "frame"}),
        ),
        _reply("c", "finish", {"summary": "ok"}),
    ])
    session = SpeedSession()
    outcome = AssemblyAgent(
        client,
        "m",
        session,
        tools=[],
        compact_context=True,
        speed_optimizations=True,
        max_llm_calls=2,
    ).run("装配")

    assert outcome["status"] == "finished"
    assert client.requests[0]["parallel_tool_calls"] is True
    assert "current_state_digest" in client.requests[0]["messages"][-1]["content"]
    assert "current_state_digest" in client.requests[1]["messages"][-1]["content"]
    assert '"version":3' in client.requests[0]["messages"][-1]["content"]
    assert "低帧数快速预检" in client.requests[0]["messages"][0]["content"]
    assert session.calls[:2] == ["insert_component", "ground_component"]
    assert outcome["timings"]["llm_calls"]
    assert "duration_ms" in outcome["tool_calls"][0]


def test_speed_mode_early_stops_and_restores_best_complete_state():
    client = Client([
        _reply("a", "mate"),
        _reply("b", "mate"),
        _reply("c", "mate"),
        _reply("d", "mate"),
    ])
    session = SpeedSession()
    outcome = AssemblyAgent(
        client,
        "m",
        session,
        tools=[],
        compact_context=True,
        speed_optimizations=True,
        expected_components=1,
        early_stop_min_calls=2,
        stagnation_window=2,
        max_llm_calls=4,
    ).run("装配")

    assert outcome["status"] == "stalled_best_restored"
    assert outcome["llm_calls"] == 3
    assert session.snapshot_calls == 1 and session.restore_calls == 1
    assert "连续 2 次" in outcome["early_stop_reason"]


def test_speed_checkpoint_restores_best_state_metadata_on_resume(tmp_path):
    checkpoint = tmp_path / "agent.checkpoint.json"
    first_session = SpeedSession()
    stopped = AssemblyAgent(
        Client([_reply("a", "mate"), RuntimeError("temporary")]),
        "m",
        first_session,
        tools=[],
        compact_context=True,
        speed_optimizations=True,
        expected_components=1,
        checkpoint_path=checkpoint,
        max_llm_calls=3,
    ).run("装配")
    assert stopped["status"] == "llm_error"
    assert stopped["best_snapshot_path"]

    second_session = SpeedSession()
    resumed = AssemblyAgent(
        Client([_reply("b", "finish", {"summary": "resumed"})]),
        "m",
        second_session,
        tools=[],
        compact_context=True,
        speed_optimizations=True,
        expected_components=1,
        checkpoint_path=checkpoint,
        max_llm_calls=3,
    ).run("装配", resume=True)

    assert resumed["status"] == "finished"
    assert resumed["resumed"]["from_llm_calls"] == 1
    assert resumed["best_snapshot_path"] == stopped["best_snapshot_path"]
