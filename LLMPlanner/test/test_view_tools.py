"""SolidWorks 截图工具（view_model / view_assembly）的离线测试：截图接口与模型都用替身。"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from planner import sw_views
from planner.assembly_workflow.agent import IMAGE_PLACEHOLDER, AssemblyAgent
from planner.assembly_workflow.session import AssemblySession
from planner.assembly_workflow.tools import tool_specs as asm_tool_specs
from planner.workflow.agent import IMAGE_PLACEHOLDER_V2, WorkflowAgent
from planner.workflow.errors import WorkflowError
from planner.workflow.tools import IMAGE_KEY
from planner.workflow.tools_v2 import WorkflowSessionV2
from test_workflow_three_view import ScriptedClient, _call, _image_urls


def _fake_capture(calls: list[dict]):
    """模拟 C# /api/view/capture：按请求的视向在 outDir 里写 BMP。"""

    def request(method, path, body=None):
        assert method == "POST" and path == "/api/view/capture"
        calls.append(body)
        images = []
        for index, view in enumerate(body["views"]):
            path_ = Path(body["outDir"]) / f"{index:02d}_{view}.bmp"
            size = (body["width"] or 1600, body["height"] or 900)
            Image.new("RGB", size, (200, 50 + index * 40, 50)).save(path_, format="BMP")
            images.append({"view": view, "path": str(path_)})
        return {"success": True, "result": {"images": images, "missing_highlight": []}}

    return request


def _png_size(b64: str) -> tuple[int, int]:
    image = Image.open(io.BytesIO(base64.b64decode(b64)))
    assert image.format == "PNG"
    return image.size


def test_capture_grid_lays_out_views_with_labels():
    calls: list[dict] = []
    image, meta = sw_views.capture_grid(_fake_capture(calls), "part", None)
    assert calls[0]["views"] == sw_views.DEFAULT_VIEWS and calls[0]["target"] == "part"
    cell_w, cell_h = sw_views.CELL_WIDTH, 288  # 伪截图 1600×900
    assert _png_size(image) == (2 * cell_w, 2 * (cell_h + sw_views.LABEL_HEIGHT))
    assert meta["views"][1] == sw_views.VIEW_NOTES["front"]

    image, meta = sw_views.capture_grid(_fake_capture(calls), "assembly", "Top")
    assert calls[-1]["views"] == ["top"]
    assert _png_size(image) == (cell_w, cell_h + sw_views.LABEL_HEIGHT)


def test_capture_grid_rejects_unknown_or_too_many_views():
    with pytest.raises(WorkflowError, match="未知视向"):
        sw_views.normalize_views(["side"])
    with pytest.raises(WorkflowError, match="最多"):
        sw_views.normalize_views(["front", "back", "left", "right", "top"])


def test_capture_grid_reports_service_failure():
    with pytest.raises(WorkflowError) as exc:
        sw_views.capture_grid(lambda *a: {"success": False, "message": "SaveBMP 失败"}, "part", None)
    assert exc.value.code == "CAPTURE_FAILED"


class CapturingPartSession(WorkflowSessionV2):
    def __init__(self, volume=1000.0):
        super().__init__(base_url="http://fake")
        self.has_part = True
        self.volume = volume
        self.calls: list[dict] = []
        self._request = _fake_capture(self.calls)

    def state_summary(self):
        return {"bbox_mm": {"size": [10, 10, 10]}, "volume_mm3": self.volume}


def test_view_model_returns_image_and_state():
    session = CapturingPartSession()
    result = session.call("view_model", {"views": '["isometric", "front"]'})
    assert result["ok"] and result["volume_mm3"] == 1000.0
    assert session.calls[0]["views"] == ["isometric", "front"]
    assert _png_size(result[IMAGE_KEY])[0] == 2 * sw_views.CELL_WIDTH
    assert CapturingPartSession(volume=0.0).call("view_model", {})["error"]["code"] == "NO_BODY"


class CapturingAssemblySession(AssemblySession):
    def __init__(self):
        super().__init__(base_url="http://fake", workflow="v3")
        self.has_assembly = True
        self.aliases = {"base": "base-1"}
        self.calls: list[dict] = []
        capture = _fake_capture(self.calls)

        def request(method, path, body=None):
            if path == "/api/asmwf/components":
                return {"success": True, "result": {"components": [{"name": "base-1"}, {"name": "arm-1"}]}}
            return capture(method, path, body)

        self._request = request


def test_view_assembly_resolves_highlight_roles():
    session = CapturingAssemblySession()
    result = session.call("view_assembly", {"views": ["isometric"], "highlight": ["base", "arm", "nope"]})
    assert result["ok"] and IMAGE_KEY in result
    assert session.calls[0]["highlight"] == ["base-1", "arm-1"] and session.calls[0]["target"] == "assembly"
    assert result["highlighted"] == ["base-1", "arm-1"] and result["highlight_not_found"] == ["nope"]


def test_view_assembly_is_only_in_v3_and_can_be_turned_off():
    names = lambda specs: {s["function"]["name"] for s in specs}  # noqa: E731
    assert "view_assembly" in names(asm_tool_specs("v3"))
    assert "view_assembly" not in names(asm_tool_specs("v3", view_tool=False))
    assert "view_assembly" not in names(asm_tool_specs("v2")) | names(asm_tool_specs("v1"))


class ImageAsmSession:
    last_motion = None
    saved = False
    v3 = False

    def __init__(self):
        self.aliases: dict = {}
        self.shots = 0
        self.calls: list[str] = []

    def call(self, name, args):
        self.calls.append(name)
        if name == "view_assembly":
            self.shots += 1
            return {"ok": True, "views": ["等轴测"], IMAGE_KEY: f"SHOT{self.shots}"}
        return {"ok": True}


def test_assembly_agent_sends_latest_screenshot_and_keeps_it_out_of_checkpoint(tmp_path):
    path = tmp_path / "agent.checkpoint.json"
    script = [[_call("view_assembly", call_id="v1")], [_call("insert_component", {"role": "a"}, call_id="i1"),
                                                         _call("view_assembly", call_id="v2")],
              [_call("finish", {"summary": "ok"}, call_id="f")]]
    client = ScriptedClient(script)
    outcome = AssemblyAgent(client, "m", ImageAsmSession(), tools=asm_tool_specs("v3"),
                            checkpoint_path=path).run("装配")
    assert outcome["status"] == "finished"
    second, third = client.seen[1], client.seen[2]
    assert "SHOT1" not in next(m for m in second if m["role"] == "tool")["content"]
    assert _image_urls(second) == ["data:image/png;base64,SHOT1"]
    assert _image_urls(third) == ["data:image/png;base64,SHOT2"]
    assert any(m.get("content") == IMAGE_PLACEHOLDER for m in third)
    text = path.read_text(encoding="utf-8")
    assert "SHOT" not in text and "SHOT" not in json.dumps(outcome["messages"])

    session = ImageAsmSession()
    resumed = AssemblyAgent(ScriptedClient([]), "m", session, tools=asm_tool_specs("v3"),
                            checkpoint_path=path).run("装配", resume=True)
    assert resumed["status"] == "finished" and session.calls == ["insert_component"]


def test_assembly_agent_without_view_tool_hides_and_blocks_it():
    client = ScriptedClient([[_call("view_assembly")], [_call("finish", {"summary": "ok"})]])
    session = ImageAsmSession()
    agent = AssemblyAgent(client, "m", session, tools=asm_tool_specs("v3"), view_tool=False)
    assert "view_assembly" not in {s["function"]["name"] for s in agent.tools}
    outcome = agent.run("装配")
    assert session.calls == ["finish"] and outcome["tool_calls"][0]["error_code"] == "TOOL_ARGS"
    assert _image_urls(client.seen[1]) == []


class ImagePartSession:
    version = "v2"

    def __init__(self):
        self.shots = 0

    def call(self, name, args):
        if name == "view_model":
            self.shots += 1
            return {"ok": True, IMAGE_KEY: f"PART{self.shots}"}
        return {"ok": True}

    def state_summary(self):
        return {"bbox_mm": {"size": [1, 1, 1]}, "volume_mm3": 1.0, "bodies": 1}


def test_workflow_agent_v2_view_model_uses_generic_placeholder():
    script = [[_call("view_model", call_id="a")], [_call("view_model", {"views": ["top"]}, call_id="b")],
              [_call("finish", {"summary": "ok"})]]
    client = ScriptedClient(script)
    outcome = WorkflowAgent(client, "m", ImagePartSession()).run("立方体")
    assert outcome["status"] == "finished" and outcome["view_tool"] is True
    assert _image_urls(client.seen[1]) == ["data:image/png;base64,PART1"]
    assert _image_urls(client.seen[2]) == ["data:image/png;base64,PART2"]
    assert any(m.get("content") == IMAGE_PLACEHOLDER_V2 for m in client.seen[2])
    caption = next(m for m in client.seen[1] if isinstance(m.get("content"), list))["content"][0]["text"]
    assert caption.startswith("view_model：")


def test_workflow_agent_without_view_tool_blocks_view_model():
    client = ScriptedClient([[_call("view_model")], [_call("finish", {"summary": "ok"})]])
    session = ImagePartSession()
    agent = WorkflowAgent(client, "m", session, view_tool=False)
    assert "view_model" not in {s["function"]["name"] for s in agent.tools}
    outcome = agent.run("立方体")
    assert session.shots == 0 and outcome["tool_calls"][0]["error_code"] == "TOOL_ARGS"
