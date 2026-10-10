"""离线测试：未知参数拒绝、钻尖盲孔、抽壳开口面回显。"""

from __future__ import annotations

import math

import pytest

from test_workflow_v2 import FakeV2


def test_misplaced_nested_field_is_rejected_with_its_owner():
    session = FakeV2(volume=0.0, delta=2000.0)
    result = session.call("sweep", {
        "path_3d": {"points": [[0, 0, 0], [0, 0, 50], [50, 0, 50]]}, "corner_radius": 10, "profile_diameter": 8,
    })
    assert result["ok"] is False and result["error"]["code"] == "TOOL_ARGS"
    assert "corner_radius" in result["error"]["message"]
    assert "path_3d" in result["error"]["hint"]
    assert session.ran == []


def test_argument_of_another_tool_is_rejected():
    session = FakeV2(delta=1000.0)
    result = session.call("extrude", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 5}],
                                      "depth": 3, "cut": True})
    assert result["ok"] is False
    assert "loft" in result["error"]["hint"] and "sweep" in result["error"]["hint"]
    typo = FakeV2(delta=-10.0).call("hole", {"plane": "top", "center": [[0, 0]], "diameter": 5, "depth": 3})
    assert typo["ok"] is False and "centers" in typo["error"]["hint"]


def test_harmless_extra_argument_only_adds_a_note():
    session = FakeV2(delta=-10.0)
    result = session.call("hole", {"plane": "top", "centers": [[0, 0]], "diameter": 5, "depth": 3,
                                   "description": "M5 底孔"})
    assert result["ok"] is True, result
    assert any("description" in note for note in result["notes"])


def test_hole_with_tip_angle_adds_a_drill_point_cone():
    session = FakeV2(delta=-50.0)
    result = session.call("hole", {"plane": "top", "centers": [[0, 0], [20, 0]], "diameter": 10, "depth": 12,
                                   "tip_angle": 118})
    assert result["ok"] is True, result
    cone = session.ran[-1][-1]
    assert cone["route"] == "/api/feature/cut-extrusion"
    assert cone["params"]["draftAngleDeg"] == pytest.approx(59)
    assert cone["params"]["depth"] == pytest.approx((5 - 0.005) / math.tan(math.radians(59)) / 1000)
    assert "118" in result["drill_point"]
    assert len(session.ran) == 2
    flat = FakeV2(delta=-50.0).call("hole", {"plane": "top", "centers": [[0, 0]], "diameter": 10, "through_all": True,
                                             "tip_angle": 118})
    assert flat["ok"] is False and "盲孔" in flat["error"]["message"]


def test_shell_echoes_open_faces_and_warns_about_top_bottom():
    session = FakeV2(delta=-500.0)
    result = session.call("shell", {"thickness": 2, "open_faces": ["bottom"]})
    assert result["ok"] is True, result
    assert any("法向 -Y" in text for text in result["open_faces"])
    assert any("face(+Z)" in text for text in result["warnings"])
    plain = FakeV2(delta=-500.0).call("shell", {"thickness": 2, "open_faces": ["face(+Z)"]})
    assert plain["ok"] is True and not plain.get("warnings")
