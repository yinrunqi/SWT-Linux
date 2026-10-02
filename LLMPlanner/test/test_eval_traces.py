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


def test_assembly_agent_keeps_raw_responses():
    client = ScriptedClient([(1, [_call("a", "finish", {"summary": "ok"})])])
    outcome = AssemblyAgent(client, "m", AsmSession(), tools=[]).run("装配")
    assert outcome["status"] == "finished"
    assert outcome["llm_responses"][0]["choices"][0]["message"]["reasoning_content"] == "think1"


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
                           max_llm_calls=40, no_views_tool=False)
    row = bench.run_one({"case": "d01", "prompt": "p", "image": image}, 1, args)
    trace = json.loads(Path(row["trace"]).read_text(encoding="utf-8"))
    assert trace["error"] == "web down"
    assert trace["request"]["capture_trace"] is True
    assert trace["request"]["image"] == "[image]"
