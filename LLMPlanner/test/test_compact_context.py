"""L1_v2 compact_context：请求副本压缩、核对回退和 length recovery。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from planner.workflow.agent import WorkflowAgent
from planner.workflow.context_compact import CONTINUE_FULL_CONTEXT


def _call(call_id: str, name: str, args: dict | None = None):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args or {}, ensure_ascii=False)),
    )


class RecordingClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        finish_reason, calls = self.script.pop(0)
        message = SimpleNamespace(content="", tool_calls=calls)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
            usage=None,
        )


class CompactSession:
    version = "v2"
    has_part = True

    def __init__(self, bodies: int = 1):
        self.bodies = bodies

    def call(self, name, args):
        if name == "get_model_state":
            return {"ok": True, "state": self.state_summary()}
        return {
            "ok": True,
            "created": [f"{name}-feature"],
            "bbox_mm": {"size": [10, 20, 30]},
            "volume_mm3": 6000,
            "volume_delta_mm3": 100,
            "bodies": self.bodies,
            "new_cylinders": [{"diameter_mm": 5, "count": 1}],
            "verbose_unused_field": "x" * 1000,
        }

    def state_summary(self):
        return {
            "bbox_mm": {"size": [10, 20, 30]},
            "volume_mm3": 6000,
            "bodies": self.bodies,
            "features": [{"name": "凸台-拉伸1", "type": "Extrusion", "unused": "x" * 100}],
            "cylinder_groups": [{"diameter_mm": 5, "count": 1}],
            "planar_faces": [{"ref": "face#0", "unused": "x" * 1000}],
        }


class SemanticFailureSession(CompactSession):
    def call(self, name, args):
        if name == "cut":
            return {"ok": False, "error": {
                "code": "FEATURE_FAILED",
                "message": f"切除策略失败: {args.get('depth')}",
            }}
        return super().call(name, args)


def _tool_names(request: dict) -> list[str]:
    return [item["function"]["name"] for item in request["tools"]]


def test_compact_context_is_opt_in_and_default_request_is_unchanged():
    client = RecordingClient([("tool_calls", [_call("f", "finish", {"summary": "ok"})])])
    outcome = WorkflowAgent(client, "m", CompactSession(), version="v2").run("零件")
    assert outcome["status"] == "finished"
    assert outcome["compact_context"] is False
    assert outcome["request_modes"] == ["full"]
    assert client.requests[0]["messages"][0]["content"] == outcome["messages"][0]["content"]
    assert client.requests[0]["max_tokens"] == 16000


def test_compact_history_keeps_two_recent_rounds_and_ledgers_older_success():
    client = RecordingClient([
        ("tool_calls", [_call("a", "extrude", {"depth": 10})]),
        ("tool_calls", [_call("b", "cut", {"depth": 2})]),
        ("tool_calls", [_call("c", "hole", {"diameter": 5})]),
        ("tool_calls", [_call("d", "finish", {"summary": "ok"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", CompactSession(), version="v2", compact_context=True, view_tool=False, max_llm_calls=4,
    ).run("零件")
    assert outcome["status"] == "finished"
    fourth = client.requests[3]["messages"]
    tools = {message["tool_call_id"]: message["content"] for message in fourth if message["role"] == "tool"}
    old = json.loads(tools["a"])
    assert old["ledger"] is True and old["tool"] == "extrude"
    assert "verbose_unused_field" not in old
    assert "ledger" not in json.loads(tools["b"])
    assert "ledger" not in json.loads(tools["c"])
    # 完整 trace/checkpoint 主历史不被请求压缩改写。
    full = {message["tool_call_id"]: message["content"] for message in outcome["messages"] if message["role"] == "tool"}
    assert "verbose_unused_field" in json.loads(full["a"])


def test_compact_verifier_uses_short_tools_and_can_finish():
    client = RecordingClient([
        ("tool_calls", [_call("s", "get_model_state")]),
        ("tool_calls", [_call("f", "finish", {"summary": "verified"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", CompactSession(), version="v2", compact_context=True, view_tool=False,
        reasoning_effort="medium",
    ).run("零件", image_b64="DRAW")
    assert outcome["status"] == "finished" and outcome["summary"] == "verified"
    assert outcome["request_modes"] == ["compact_history", "compact_verifier"]
    assert outcome["compact_verifier_calls"] == 1
    assert _tool_names(client.requests[1]) == ["finish", CONTINUE_FULL_CONTEXT]
    verifier = client.requests[1]["messages"]
    assert len(verifier) == 3
    state = verifier[2]["content"]
    assert "cylinder_groups" in state and "planar_faces" not in state
    assert outcome["tool_calls"][-1]["compact_verifier"] is True
    assert client.requests[0]["max_tokens"] == 32000
    assert client.requests[0]["reasoning_effort"] == "medium"
    assert client.requests[1]["max_tokens"] == 4096
    assert client.requests[1]["reasoning_effort"] == "low"


def test_compact_expected_bodies_updates_prompt_and_finish_validation():
    client = RecordingClient([("tool_calls", [_call("f", "finish", {"summary": "two bodies"})])])
    outcome = WorkflowAgent(
        client, "m", CompactSession(bodies=2), version="v2", compact_context=True,
        expected_bodies=2, view_tool=False,
    ).run("两个分开的实体")
    assert outcome["status"] == "finished"
    assert outcome["expected_bodies"] == 2
    prompt = client.requests[0]["messages"][0]["content"]
    assert "恰好 2 个分开的实体" in prompt
    assert "零件必须是单一实体" not in prompt

    rejected = RecordingClient([("tool_calls", [_call("f", "finish", {"summary": "wrong"})])])
    mismatch = WorkflowAgent(
        rejected, "m", CompactSession(bodies=1), version="v2", compact_context=True,
        expected_bodies=2, view_tool=False, max_llm_calls=1,
    ).run("两个分开的实体")
    assert mismatch["status"] == "max_llm_calls"
    assert mismatch["tool_calls"][-1]["error_code"] == "BODY_COUNT"


def test_compact_verifier_can_restore_full_history_for_one_correction_turn():
    client = RecordingClient([
        ("tool_calls", [_call("s", "get_model_state")]),
        ("tool_calls", [_call("v", CONTINUE_FULL_CONTEXT, {"reason": "缺少通孔"})]),
        ("tool_calls", [_call("h", "hole", {"diameter": 5})]),
        ("tool_calls", [_call("f", "finish", {"summary": "fixed"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", CompactSession(), version="v2", compact_context=True, view_tool=False,
    ).run("零件")
    assert outcome["status"] == "finished"
    assert outcome["compact_fallbacks"] == 1
    assert outcome["request_modes"] == [
        "compact_history", "compact_verifier", "full_fallback", "compact_history",
    ]
    assert any("缺少通孔" in str(message.get("content")) for message in client.requests[2]["messages"])
    state_tool = next(message for message in client.requests[2]["messages"] if message["role"] == "tool")
    assert json.loads(state_tool["content"])["state"]["planar_faces"]


def test_compact_length_recovery_starts_with_large_budget_and_keeps_honest_usage():
    client = RecordingClient([
        ("length", []),
        ("tool_calls", [_call("f", "finish", {"summary": "ok"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", CompactSession(), version="v2", compact_context=True, view_tool=False,
    ).run("零件")
    assert outcome["status"] == "finished"
    assert [request["max_tokens"] for request in client.requests] == [32000, 32000]
    assert outcome["llm_calls"] == 2 and outcome["length_retries"] == 1
    assert outcome["request_modes"] == ["compact_history", "length_recovery"]
    assert "上一条回复因长度耗尽" in str(client.requests[1]["messages"][-1]["content"])
    assert not any("被截断了" in str(message.get("content")) for message in outcome["messages"])


def test_compact_verifier_length_recovery_restores_large_budget():
    client = RecordingClient([
        ("tool_calls", [_call("s", "get_model_state")]),
        ("length", []),
        ("tool_calls", [_call("f", "finish", {"summary": "verified"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", CompactSession(), version="v2", compact_context=True,
        view_tool=False, reasoning_effort="medium",
    ).run("零件")
    assert outcome["status"] == "finished"
    assert outcome["request_modes"] == [
        "compact_history", "compact_verifier", "compact_verifier_recovery",
    ]
    assert [request["max_tokens"] for request in client.requests] == [32000, 4096, 32000]
    assert [request["reasoning_effort"] for request in client.requests] == ["medium", "low", "low"]
    assert outcome["length_retries"] == 1


def test_compact_semantic_failure_family_forces_one_alternative_strategy_turn():
    client = RecordingClient([
        ("tool_calls", [_call("a", "cut", {"depth": 1})]),
        ("tool_calls", [_call("b", "cut", {"depth": 2})]),
        ("tool_calls", [_call("c", "cut", {"depth": 3})]),
        ("tool_calls", [_call("f", "finish", {"summary": "recovered"})]),
    ])
    outcome = WorkflowAgent(
        client, "m", SemanticFailureSession(), version="v2", compact_context=True,
        view_tool=False, max_llm_calls=4,
    ).run("零件")
    assert outcome["status"] == "finished"
    assert outcome["semantic_recoveries"] == 1
    assert outcome["failure_families"]["cut:FEATURE_FAILED"] == 3
    assert outcome["request_modes"] == [
        "compact_history", "compact_history", "compact_history", "semantic_recovery",
    ]
    assert "cut" not in _tool_names(client.requests[3])
    assert "暂不提供 cut" in str(client.requests[3]["messages"][-1]["content"])


def test_compact_checkpoint_resume_keeps_optional_state(tmp_path):
    checkpoint = tmp_path / "compact.checkpoint.json"
    first = RecordingClient([("tool_calls", [_call("a", "extrude", {"depth": 10})])])
    WorkflowAgent(
        first, "m", CompactSession(), version="v2", compact_context=True,
        view_tool=False, max_llm_calls=1, checkpoint_path=checkpoint,
    ).run("零件")
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["compact_context"] is True
    assert saved["expected_bodies"] == 1
    assert saved["last_round_tools"] == ["extrude"]
    assert saved["request_modes"] == ["compact_history"]
    saved["status"] = "running"
    checkpoint.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    second = RecordingClient([("tool_calls", [_call("f", "finish", {"summary": "resumed"})])])
    outcome = WorkflowAgent(
        second, "m", CompactSession(), version="v2", compact_context=True,
        view_tool=False, checkpoint_path=checkpoint,
    ).run("零件", resume=True)
    assert outcome["status"] == "finished"
    assert outcome["summary"] == "resumed"
    assert outcome["resumed"]["from_llm_calls"] == 1
