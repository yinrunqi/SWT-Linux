"""看图建模（L1 端到端）的离线测试：不连接 SolidWorks，也不调用真实模型。"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from planner.workflow import tools as workflow_tools
from planner.workflow.agent import DRAWING_NOTE, RENDERED_PLACEHOLDER, SYSTEM_PROMPT, WorkflowAgent
from planner.workflow.tools import IMAGE_KEY, TOOL_SPECS, WorkflowSession, tool_specs

ROOT = Path(__file__).resolve().parents[1]
DRAWING = ROOT / "eval" / "three_view" / "drawings" / "b01_l_bracket.png"
REFERENCE = ROOT / "eval" / "three_view" / "references" / "b01_l_bracket.step"


def _call(name, args=None, call_id="c1"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))


class ScriptedClient:
    """按顺序返回预设的 tool calls，并记录每次请求看到的消息。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.seen.append(json.loads(json.dumps(kwargs["messages"])))
        calls = self.script.pop(0)
        message = SimpleNamespace(content="", tool_calls=calls)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class StubSession:
    """render_views 每次返回一张不同的“图片”。"""

    def __init__(self):
        self.renders = 0

    def call(self, name, args):
        if name == "render_views":
            self.renders += 1
            return {"ok": True, "bbox_mm": None, IMAGE_KEY: f"IMG{self.renders}"}
        return {"ok": True}

    def state_summary(self):
        return {"bbox_mm": {"size": [1, 1, 1]}, "volume_mm3": 1.0}


def _image_urls(messages):
    return [
        part["image_url"]["url"]
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def test_drawing_goes_into_first_user_message_and_system_prompt_is_unchanged():
    client = ScriptedClient([[_call("finish", {"summary": "ok"})]])
    outcome = WorkflowAgent(client, "m", StubSession()).run("建模 L 型支架", image_b64="AAAA", image_mime="image/png")
    first = client.seen[0]
    assert first[0] == {"role": "system", "content": SYSTEM_PROMPT}
    text, image = first[1]["content"]
    assert "建模 L 型支架" in text["text"] and DRAWING_NOTE in text["text"]
    assert image == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    assert outcome["status"] == "finished"
    assert _image_urls(outcome["messages"]) == []


def test_text_only_run_keeps_plain_string_content():
    client = ScriptedClient([[_call("finish", {"summary": "ok"})]])
    WorkflowAgent(client, "m", StubSession()).run("创建一个 100mm 的正方体")
    assert isinstance(client.seen[0][1]["content"], str)
    assert DRAWING_NOTE not in client.seen[0][1]["content"]


def test_rendered_views_are_sent_as_image_and_only_latest_is_kept():
    script = [[_call("render_views", call_id="r1")], [_call("render_views", call_id="r2")], [_call("finish")]]
    client = ScriptedClient(script)
    outcome = WorkflowAgent(client, "m", StubSession(), extra_tools=["render_views"]).run("x", image_b64="DRAW")

    second, third = client.seen[1], client.seen[2]
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert "IMG1" not in tool_msg["content"]
    assert _image_urls(second) == ["data:image/png;base64,DRAW", "data:image/png;base64,IMG1"]
    # 第二次投影后，第一张被换成占位文字，图纸本身始终保留
    assert _image_urls(third) == ["data:image/png;base64,DRAW", "data:image/png;base64,IMG2"]
    assert any(m.get("content") == RENDERED_PLACEHOLDER for m in third)
    assert [item["tool"] for item in outcome["tool_calls"]] == ["render_views", "render_views", "finish"]


def test_render_views_is_an_opt_in_tool():
    names = [spec["function"]["name"] for spec in tool_specs(["render_views"])]
    assert names[-1] == "render_views" and len(names) == len(TOOL_SPECS) + 1
    assert "render_views" not in [spec["function"]["name"] for spec in TOOL_SPECS]
    assert "render_views" not in WorkflowSession(base_url="http://fake")._handlers


class ExportingSession(WorkflowSession):
    """export-step 时把参考 STEP 复制到目标路径，代替 SolidWorks 导出。"""

    def __init__(self, volume=1.0):
        super().__init__(base_url="http://fake", extra_tools=["render_views"])
        self.has_part = True
        self.volume = volume

    def state_summary(self, snapshot=None):
        return {"bbox_mm": {"size": [60, 40, 30]}, "volume_mm3": self.volume}

    def _request(self, method, path, body=None):
        assert path == "/api/export-step"
        shutil.copy(REFERENCE, body["stepPath"])
        return {"success": True}


def test_render_views_refuses_empty_part():
    result = ExportingSession(volume=0.0).call("render_views", {})
    assert not result["ok"] and result["error"]["code"] == "NO_BODY"


def test_render_views_reports_render_failure(monkeypatch):
    monkeypatch.setattr(workflow_tools, "RENDER_PYTHON", str(ROOT / "no-such-python.exe"))
    result = ExportingSession().call("render_views", {})
    assert not result["ok"] and result["error"]["code"] == "RENDER_FAILED"


@pytest.mark.skipif(not Path(workflow_tools.RENDER_PYTHON).is_file(), reason="需要 venv-occ 来投影")
def test_render_views_projects_exported_step_to_png():
    result = ExportingSession().call("render_views", {})
    assert result["ok"], result
    png = base64.b64decode(result[IMAGE_KEY])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    assert result["bbox_mm"]["size"] == [60, 40, 30]


def test_web_detects_image_mime():
    pytest.importorskip("flask")
    from web.app import _image_mime

    png = base64.b64encode(DRAWING.read_bytes()[:16]).decode()
    assert _image_mime(png) == "image/png"
    assert _image_mime("/9j/4AAQ") == "image/jpeg"
    assert _image_mime("xxxx", "image/webp") == "image/webp"
