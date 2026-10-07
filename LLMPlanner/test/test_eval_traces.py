"""离线测试：评测记录完整保存每次 LLM 调用的原始响应，图片 base64 不写进 trace。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from planner.assembly_workflow.agent import AssemblyAgent
from planner.workflow.agent import WorkflowAgent, raw_response

ROOT = Path(__file__).resolve().parents[1]


def _load_three_view_bench():
    spec = importlib.util.spec_from_file_location("eval_three_view_bench", ROOT / "eval" / "eval_three_view_bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DumpableResponse(SimpleNamespace):
    """模拟 openai SDK 的 ChatCompletion：带 model_dump 和厂商扩展字段 reasoning_content。"""

    def model_dump(self, mode="python"):
        message = self.choices[0].message
        return {
            "id": self.id,
            "choices": [{
                "finish_reason": self.finish_reason,
                "message": {
                    "content": message.content,
                    "reasoning_content": message.reasoning_content,
                    "tool_calls": [
                        {"id": c.id, "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in message.tool_calls or []
                    ],
                },
            }],
            "usage": None,
        }


def _call(call_id, name, args=None):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))


class ScriptedClient:
    def __init__(self, script):
        self.script = list(script)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        index, calls = self.script.pop(0)
        message = SimpleNamespace(content=f"text{index}", reasoning_content=f"think{index}", tool_calls=calls)
        return DumpableResponse(id=f"resp{index}", choices=[SimpleNamespace(message=message)],
                                finish_reason="tool_calls", usage=None)


class PartSession:
    def call(self, name, args):
        return {"ok": True}

    def state_summary(self):
        return {"bbox_mm": [10, 10, 10], "volume_mm3": 1000.0}


class AsmSession:
    last_motion = None
    aliases: dict = {}
    saved = False

    def call(self, name, args):
        return {"ok": True}


def test_workflow_agent_keeps_raw_responses():
    client = ScriptedClient([(1, [_call("a", "get_model_state")]), (2, [_call("b", "finish", {"summary": "s"})])])
    outcome = WorkflowAgent(client, "m", PartSession()).run("立方体")
    assert outcome["status"] == "finished"
    assert [r["id"] for r in outcome["llm_responses"]] == ["resp1", "resp2"]
    message = outcome["llm_responses"][0]["choices"][0]["message"]
    assert message["reasoning_content"] == "think1"
    assert message["tool_calls"][0]["function"]["name"] == "get_model_state"
    assert "reasoning_content" not in outcome["messages"][2]


def test_workflow_agent_retries_truncated_reply_with_larger_budget():
    budgets = []

    def create(**kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) <= 3:
            choice = SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="length")
        else:
            message = SimpleNamespace(content="", tool_calls=[_call("a", "finish", {"summary": "s"})])
            choice = SimpleNamespace(message=message, finish_reason="tool_calls")
        return SimpleNamespace(choices=[choice], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    outcome = WorkflowAgent(client, "m", PartSession(), max_tokens=8000).run("立方体")
    assert outcome["status"] == "finished"
    assert budgets == [8000, 16000, 32000, 32000]
    assert sum("被截断" in str(m.get("content")) for m in outcome["messages"]) == 1


class RecordingSession(PartSession):
    def __init__(self, crash_on=None, fail=()):
        self.calls, self.crash_on, self.fail = [], crash_on, set(fail)

    def call(self, name, args):
        self.calls.append((name, args))
        if self.crash_on and args.get("id") == self.crash_on:
            raise RuntimeError("SolidWorks 崩溃")
        if args.get("id") in self.fail:
            return {"ok": False, "error": {"code": "SW_FAIL", "message": "x"}}
        return {"ok": True}


def test_workflow_agent_resumes_from_checkpoint_without_repeating_llm_calls(tmp_path):
    path = tmp_path / "case_r1.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "extrude", {"id": "A"})]), (2, [_call("b", "extrude", {"id": "B"})])])
    try:
        WorkflowAgent(first, "m", RecordingSession(crash_on="B"), checkpoint_path=path).run("立方体")
    except RuntimeError:
        pass
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "running" and saved["usage"]["calls"] == 2
    assert saved["pending"]["tool_calls"][0]["id"] == "b"
    assert [item["args"]["id"] for item in saved["tool_log"]] == ["A"]

    session = RecordingSession()
    second = ScriptedClient([(3, [_call("c", "finish", {"summary": "s"})])])
    outcome = WorkflowAgent(second, "m", session, checkpoint_path=path).run("立方体", resume=True)
    assert outcome["status"] == "finished" and outcome["llm_calls"] == 3
    assert [args["id"] for _, args in session.calls] == ["A", "B"]
    assert outcome["resumed"] == {"from_llm_calls": 2, "replayed_tools": 1, "mismatch": None}
    assert [r["id"] for r in outcome["llm_responses"]] == ["resp1", "resp2", "resp3"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "finished"


class LosingSession(RecordingSession):
    has_part = True

    def call(self, name, args):
        if args.get("id") == "LOST":
            self.calls.append((name, args))
            return {"ok": False, "error": {"code": "NO_PART", "message": "当前没有活动文档"}}
        return super().call(name, args)


def test_workflow_agent_stops_when_part_is_lost_and_resumes_the_round(tmp_path):
    path = tmp_path / "case_r1.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "extrude", {"id": "A"})]),
                            (2, [_call("b", "extrude", {"id": "B"}), _call("c", "extrude", {"id": "LOST"})])])
    outcome = WorkflowAgent(first, "m", LosingSession(), checkpoint_path=path).run("立方体")
    assert outcome["status"] == "sw_lost" and outcome["llm_calls"] == 2
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [item["args"]["id"] for item in saved["tool_log"]] == ["A"]
    assert [c["id"] for c in saved["pending"]["tool_calls"]] == ["b", "c"]

    session = RecordingSession()
    second = ScriptedClient([(3, [_call("d", "finish", {"summary": "s"})])])
    outcome = WorkflowAgent(second, "m", session, checkpoint_path=path).run("立方体", resume=True)
    assert outcome["status"] == "finished" and outcome["llm_calls"] == 3
    assert [args["id"] for _, args in session.calls] == ["A", "B", "LOST"]


def test_workflow_agent_stops_resume_when_replay_differs(tmp_path):
    path = tmp_path / "case_r1.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "extrude", {"id": "A"})])])
    WorkflowAgent(first, "m", RecordingSession(), checkpoint_path=path, max_llm_calls=1).run("立方体")
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["status"] = "running"
    path.write_text(json.dumps(saved), encoding="utf-8")

    second = ScriptedClient([])
    outcome = WorkflowAgent(second, "m", RecordingSession(fail={"A"}), checkpoint_path=path).run("立方体", resume=True)
    assert outcome["status"] == "resume_failed" and "extrude" in outcome["error"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"


def test_workflow_agent_returns_finished_checkpoint_after_replay(tmp_path):
    path = tmp_path / "case_r1.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "extrude", {"id": "A"})]), (2, [_call("b", "finish", {"summary": "s"})])])
    WorkflowAgent(first, "m", RecordingSession(), checkpoint_path=path).run("立方体")
    session = RecordingSession()
    outcome = WorkflowAgent(ScriptedClient([]), "m", session, checkpoint_path=path).run("立方体", resume=True)
    assert outcome["status"] == "finished" and outcome["summary"] == "s" and outcome["llm_calls"] == 2
    assert [args["id"] for _, args in session.calls] == ["A"]


def test_workflow_agent_keeps_raised_budget_after_truncation():
    budgets = []

    def create(**kwargs):
        budgets.append(kwargs["max_tokens"])
        if len(budgets) == 1:
            choice = SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None), finish_reason="length")
        elif len(budgets) == 2:
            message = SimpleNamespace(content="", tool_calls=[_call("a", "get_model_state")])
            choice = SimpleNamespace(message=message, finish_reason="tool_calls")
        else:
            message = SimpleNamespace(content="", tool_calls=[_call("b", "finish", {"summary": "s"})])
            choice = SimpleNamespace(message=message, finish_reason="tool_calls")
        return SimpleNamespace(choices=[choice], usage=None)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    outcome = WorkflowAgent(client, "m", PartSession(), max_tokens=8000).run("立方体")
    assert outcome["status"] == "finished"
    assert budgets == [8000, 16000, 16000]


def test_streamed_completion_reassembles_tool_calls_and_usage():
    from openai.types.chat import ChatCompletionChunk
    from planner.workflow.agent import _streamed_completion

    def chunk(delta=None, finish=None, usage=None):
        choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
        return ChatCompletionChunk.model_validate({"id": "c1", "created": 1, "model": "m",
                                                   "object": "chat.completion.chunk", "choices": choices,
                                                   "usage": usage})

    chunks = [
        chunk({"role": "assistant", "content": "先拉伸"}),
        chunk({"tool_calls": [{"index": 0, "id": "t1", "type": "function",
                               "function": {"name": "extrude", "arguments": '{"w": '}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "20}"}}]}),
        chunk({"tool_calls": [{"index": 1, "id": "t2", "type": "function",
                               "function": {"name": "finish", "arguments": "{}"}}]}, finish="tool_calls"),
        chunk(usage={"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}),
    ]
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return iter(chunks)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    response = _streamed_completion(client, model="m", messages=[])
    assert seen["stream"] is True and seen["stream_options"] == {"include_usage": True}
    choice = response.choices[0]
    assert choice.finish_reason == "tool_calls" and choice.message.content == "先拉伸"
    assert [(t.id, t.function.name, t.function.arguments) for t in choice.message.tool_calls] == [
        ("t1", "extrude", '{"w": 20}'), ("t2", "finish", "{}")]
    assert response.usage.total_tokens == 12


def test_streamed_completion_retries_when_gateway_ends_stream_with_error():
    from openai.types.chat import ChatCompletionChunk

    def chunk(delta, finish=None):
        return ChatCompletionChunk.model_construct(
            id="c1", created=1, model="m", object="chat.completion.chunk", usage=None,
            choices=[SimpleNamespace(index=0, delta=SimpleNamespace(content=delta.get("content"),
                                                                     reasoning_content=None,
                                                                     tool_calls=delta.get("tool_calls")),
                                     finish_reason=finish)])

    finish_call = SimpleNamespace(index=0, id="t1", function=SimpleNamespace(name="finish", arguments="{}"))
    streams = [[chunk({"content": "x"}, finish="error")],
               [chunk({"tool_calls": [finish_call]}, finish="tool_calls")]]

    def create(**kwargs):
        return iter(streams.pop(0))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    outcome = WorkflowAgent(client, "m", PartSession(), stream=True).run("立方体")
    assert outcome["status"] == "finished" and outcome["llm_calls"] == 1 and not streams


def test_assembly_agent_keeps_raw_responses():
    client = ScriptedClient([(1, [_call("a", "finish", {"summary": "ok"})])])
    outcome = AssemblyAgent(client, "m", AsmSession(), tools=[]).run("装配")
    assert outcome["status"] == "finished"
    assert outcome["llm_responses"][0]["choices"][0]["message"]["reasoning_content"] == "think1"


class RecordingAsmSession(AsmSession):
    def __init__(self, crash_on=None, fail=(), lost_on=None):
        self.calls, self.crash_on, self.fail, self.lost_on = [], crash_on, set(fail), lost_on

    def call(self, name, args):
        self.calls.append((name, args))
        if self.crash_on and args.get("role") == self.crash_on:
            raise RuntimeError("SolidWorks 崩溃")
        if self.lost_on and args.get("role") == self.lost_on:
            return {"ok": False, "error": {"code": "FEATURE_FAILED", "message": "没有活动的装配体文档"}}
        if args.get("role") in self.fail:
            return {"ok": False, "error": {"code": "SW_FAIL", "message": "x"}}
        return {"ok": True}


def test_assembly_agent_resumes_from_checkpoint_without_repeating_llm_calls(tmp_path):
    path = tmp_path / "agent.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "insert_component", {"role": "base"}), _call("s", "get_assembly_state")]),
                            (2, [_call("b", "insert_component", {"role": "arm"})])])
    try:
        AssemblyAgent(first, "m", RecordingAsmSession(crash_on="arm"), tools=[], checkpoint_path=path).run("装配")
    except RuntimeError:
        pass
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "running" and saved["usage"]["calls"] == 2
    assert saved["pending"]["tool_calls"][0]["id"] == "b"
    assert [item["tool"] for item in saved["tool_log"]] == ["insert_component", "get_assembly_state"]

    session = RecordingAsmSession()
    second = ScriptedClient([(3, [_call("c", "finish", {"summary": "ok"})])])
    outcome = AssemblyAgent(second, "m", session, tools=[], checkpoint_path=path).run("装配", resume=True)
    assert outcome["status"] == "finished" and outcome["llm_calls"] == 3 and outcome["summary"] == "ok"
    assert [(name, args.get("role")) for name, args in session.calls] == [
        ("insert_component", "base"), ("insert_component", "arm"), ("finish", None)]
    assert outcome["resumed"] == {"from_llm_calls": 2, "replayed_tools": 2, "mismatch": None}
    assert [r["id"] for r in outcome["llm_responses"]] == ["resp1", "resp2", "resp3"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "finished"


def test_assembly_agent_stops_when_document_is_lost_and_resumes_the_round(tmp_path):
    path = tmp_path / "agent.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "insert_component", {"role": "base"})]),
                            (2, [_call("b", "insert_component", {"role": "arm"}),
                                 _call("c", "mate", {"role": "LOST"})])])
    outcome = AssemblyAgent(first, "m", RecordingAsmSession(lost_on="LOST"), tools=[],
                            checkpoint_path=path).run("装配")
    assert outcome["status"] == "sw_lost" and outcome["llm_calls"] == 2
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [item["args"]["role"] for item in saved["tool_log"]] == ["base"]
    assert [c["id"] for c in saved["pending"]["tool_calls"]] == ["b", "c"]
    assert saved["messages"][-1]["role"] == "tool"

    session = RecordingAsmSession()
    second = ScriptedClient([(3, [_call("d", "finish", {"summary": "ok"})])])
    outcome = AssemblyAgent(second, "m", session, tools=[], checkpoint_path=path).run("装配", resume=True)
    assert outcome["status"] == "finished" and outcome["llm_calls"] == 3
    assert [args.get("role") for _, args in session.calls] == ["base", "arm", "LOST", None]


def test_assembly_agent_resume_fails_when_replay_differs_and_returns_finished_result(tmp_path):
    path = tmp_path / "agent.checkpoint.json"
    first = ScriptedClient([(1, [_call("a", "insert_component", {"role": "base"})]),
                            (2, [_call("b", "finish", {"summary": "ok"})])])
    AssemblyAgent(first, "m", RecordingAsmSession(), tools=[], checkpoint_path=path).run("装配")

    session = RecordingAsmSession()
    outcome = AssemblyAgent(ScriptedClient([]), "m", session, tools=[], checkpoint_path=path).run("装配", resume=True)
    assert outcome["status"] == "finished" and outcome["summary"] == "ok" and outcome["llm_calls"] == 2
    assert [args.get("role") for _, args in session.calls] == ["base"]

    outcome = AssemblyAgent(ScriptedClient([]), "m", RecordingAsmSession(fail={"base"}), tools=[],
                            checkpoint_path=path).run("装配", resume=True)
    assert outcome["status"] == "resume_failed" and "insert_component" in outcome["error"]
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "finished"


def test_assembly_agent_rejects_checkpoint_of_another_goal(tmp_path):
    path = tmp_path / "agent.checkpoint.json"
    AssemblyAgent(ScriptedClient([(1, [_call("a", "finish", {"summary": "ok"})])]), "m", RecordingAsmSession(),
                  tools=[], checkpoint_path=path).run("装配")
    try:
        AssemblyAgent(ScriptedClient([]), "m", RecordingAsmSession(), tools=[], checkpoint_path=path).run(
            "别的装配", resume=True)
    except ValueError as exc:
        assert "另一个需求" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_raw_response_without_model_dump_falls_back_to_repr():
    assert raw_response(SimpleNamespace(x=1)) == {"repr": "namespace(x=1)"}


def test_three_view_trace_strips_images(tmp_path):
    bench = _load_three_view_bench()
    request = {"instruction": "建模", "image": "data:image/png;base64,AAAA"}
    body = {
        "success": True,
        "raw_llm_trace": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}}]}],
        "workflow": {"llm_responses": [{"id": "r1"}]},
    }
    path = tmp_path / "d01_r1.trace.json"
    bench._write_trace(path, request, 200, body, None)
    text = path.read_text(encoding="utf-8")
    assert "AAAA" not in text and "BBBB" not in text
    trace = json.loads(text)
    assert trace["request"]["image"] == "[image]"
    assert trace["response"]["workflow"]["llm_responses"] == [{"id": "r1"}]


def test_three_view_run_one_writes_trace_on_request_failure(tmp_path, monkeypatch):
    bench = _load_three_view_bench()
    image = tmp_path / "d.png"
    image.write_bytes(b"\x89PNG")

    def boom(*args, **kwargs):
        raise ConnectionError("web down")

    monkeypatch.setattr(bench.requests, "post", boom)
    monkeypatch.setattr(bench, "_close_documents", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    args = SimpleNamespace(out_dir=tmp_path, sw_url="sw", web_url="web", plan_model="m", action_level="L1",
                           max_llm_calls=40, no_views_tool=False, no_view_tool=False,
                           reasoning_effort=None, provider="aiping")
    row = bench.run_one({"case": "d01", "prompt": "p", "image": image}, 1, args)
    trace = json.loads(Path(row["trace"]).read_text(encoding="utf-8"))
    assert trace["error"] == "web down"
    assert trace["request"]["capture_trace"] is True
    assert trace["request"]["image"] == "[image]"
    assert trace["request"]["checkpoint_path"] == str(tmp_path / "d01_r1.checkpoint.json")
    assert trace["request"]["resume"] is False
    assert bench.needs_rerun(row)


def test_three_view_l1_v2_sends_text_track_reasoning_and_view_settings(tmp_path, monkeypatch):
    bench = _load_three_view_bench()
    image = tmp_path / "d.png"
    image.write_bytes(b"\x89PNG")

    def boom(*args, **kwargs):
        raise ConnectionError("stop after request capture")

    monkeypatch.setattr(bench.requests, "post", boom)
    monkeypatch.setattr(bench, "_close_documents", lambda url: None)
    monkeypatch.setattr(bench.time, "sleep", lambda s: None)
    args = SimpleNamespace(
        out_dir=tmp_path, sw_url="sw", web_url="web", plan_model="Qwen3.8-Flash-Next-FP8",
        action_level="L1_v2", max_llm_calls=40, no_views_tool=False, no_view_tool=False,
        reasoning_effort="medium", provider="qwenlocal",
    )
    row = bench.run_one({"case": "d01", "prompt": "p", "image": image}, 1, args)
    request = json.loads(Path(row["trace"]).read_text(encoding="utf-8"))["request"]
    assert request["provider"] == "qwenlocal"
    assert request["action_level"] == "L1_v2"
    assert request["reasoning_effort"] == "medium"
    assert request["views_tool"] is True
    assert request["view_tool"] is True


def test_three_view_resume_continues_interrupted_runs_from_checkpoint(tmp_path, monkeypatch):
    bench = _load_three_view_bench()
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    out_dir = tmp_path / "eval" / "artifacts" / "tv"
    out_dir.mkdir(parents=True)
    rows = [{"case": "d01", "repeat": 1, "status": "finished"},
            {"case": "d01", "repeat": 2, "status": "llm_error"},
            {"case": "d01", "repeat": 2, "status": "llm_error"}]
    (out_dir / "runs.json").write_text(json.dumps(rows), encoding="utf-8")
    (out_dir / "d01_r2.checkpoint.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bench, "load_tasks", lambda lang, ids: [{"case": "d01", "prompt": "p", "image": None}])
    seen = []

    def fake_run_one(task, k, args, resume=False):
        seen.append((k, resume))
        return {"case": task["case"], "repeat": k, "status": "finished", "duration_seconds": 1.0}

    monkeypatch.setattr(bench, "run_one", fake_run_one)
    monkeypatch.setattr(bench.sys, "argv", ["x", "--name", "tv", "--repeats", "3", "--resume"])
    assert bench.main() == 0
    assert seen == [(2, True), (3, False)]
    log = json.loads((out_dir / "runs.json").read_text(encoding="utf-8"))
    assert [(r["repeat"], r["status"]) for r in log] == [(1, "finished"), (2, "finished"), (3, "finished")]
