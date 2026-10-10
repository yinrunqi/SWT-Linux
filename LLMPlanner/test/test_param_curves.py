"""离线测试：参数曲线（轮廓 curve 段 / curve 轮廓 / path_3d.curve）与变螺距螺旋线。"""

from __future__ import annotations

import math

import pytest

from planner.workflow import param_curves
from planner.workflow.errors import WorkflowError
from planner.workflow.tools_v2 import helix_geometry
from test_workflow_v2 import FakeV2


def _profile_segments(session):
    ops = [op for op in session.ran[0] if op["operation"] == "sketch_profile"]
    return [segment for op in ops for segment in op["params"]["segments"]]


def test_sample_follows_the_formula_and_hits_both_ends():
    rb = 30.0
    spec = {"u": "rb*(cos(t) + t*sin(t))", "v": "rb*(sin(t) - t*cos(t))", "t": [0, 1.2]}
    points = param_curves.sample(spec, ("u", "v"), {"rb": rb})
    assert points[0] == pytest.approx((rb, 0.0))
    end = 1.2
    assert points[-1] == pytest.approx((rb * (math.cos(end) + end * math.sin(end)),
                                        rb * (math.sin(end) - end * math.cos(end))), abs=1e-6)
    assert 17 <= len(points) <= param_curves.MAX_POINTS
    for (u0, v0), (u1, v1) in zip(points, points[1:]):
        assert math.hypot(u1 - u0, v1 - v0) < 10


def test_sample_reports_bad_expressions():
    with pytest.raises(WorkflowError) as info:
        param_curves.sample({"u": "sqrt(t)", "v": "t", "t": [-1, 1]}, ("u", "v"), {})
    assert "t=-1" in str(info.value)
    with pytest.raises(WorkflowError):
        param_curves.sample({"u": "t", "v": "t", "t": [0]}, ("u", "v"), {})
    with pytest.raises(WorkflowError):
        param_curves.sample({"u": "undefined_name*t", "v": "t", "t": [0, 1]}, ("u", "v"), {})


def test_path_curve_segment_becomes_a_spline_and_closes_with_a_line():
    session = FakeV2(delta=4000.0)
    result = session.call("extrude", {
        "plane": "Z=0", "depth": 5,
        "loops": [{"type": "path", "segments": [{"curve": {"u": "50*cos(t)", "v": "50*sin(t)", "t": [0, "=pi"]}}]}],
    })
    assert result["ok"] is True, result
    assert result["region_area_mm2"] == pytest.approx(math.pi * 50 ** 2 / 2, rel=2e-3)
    kinds = [segment["type"] for segment in _profile_segments(session)]
    assert kinds == ["spline", "line"]
    spline = _profile_segments(session)[0]
    assert spline["points"][0]["x"] == pytest.approx(0.05) and spline["points"][-1]["x"] == pytest.approx(-0.05)


def test_closed_curve_loop_is_split_into_two_splines():
    session = FakeV2(delta=4000.0)
    session.call("set_parameters", {"values": {"R": 40}})
    result = session.call("extrude", {
        "plane": "Z=0", "depth": 5,
        "loops": [{"type": "curve", "u": "R*cos(t)", "v": "0.5*R*sin(t)", "t": [0, "=2*pi"]}],
    })
    assert result["ok"] is True, result
    assert result["region_area_mm2"] == pytest.approx(math.pi * 40 * 20, rel=2e-3)
    assert [segment["type"] for segment in _profile_segments(session)] == ["spline", "spline"]


def test_curve_mixed_with_lines_inserts_a_bridge_when_the_start_is_off():
    session = FakeV2(delta=1000.0)
    result = session.call("extrude", {
        "plane": "Z=0", "depth": 2,
        "loops": [{"type": "path", "start": [0, 0], "segments": [
            {"to": [10, 0]},
            {"curve": {"u": "10 + 5*t", "v": "5*sin(pi*t)", "t": [0.1, 1]}},
            {"to": [15, 10]}, {"to": [0, 10]},
        ]}],
    })
    assert result["ok"] is True, result
    assert any("不重合" in note for note in result["notes"])
    assert [segment["type"] for segment in _profile_segments(session)] == ["line", "line", "spline", "line", "line", "line"]


def test_closing_step_that_repeats_the_curve_end_is_dropped():
    session = FakeV2(delta=4000.0)
    result = session.call("extrude", {
        "plane": "Z=0", "depth": 5,
        "loops": [{"type": "path", "segments": [
            {"curve": {"u": "50*cos(t)", "v": "50*sin(t)", "t": [0, "=pi"]}},
            {"curve": {"u": "50*cos(t)", "v": "-20*sin(t)", "t": ["=pi", 0]}},
            {"to": [50, 0]},
        ]}],
    })
    assert result["ok"] is True, result
    assert any("已省略" in note for note in result["notes"])
    assert [segment["type"] for segment in _profile_segments(session)] == ["spline", "spline"]


def test_sweep_along_path_3d_curve_uses_a_spline():
    session = FakeV2(volume=0.0, delta=2000.0)
    result = session.call("sweep", {
        "path_3d": {"curve": {"x": "20*cos(t)", "y": "20*sin(t)", "z": "3*t", "t": [0, "=4*pi"]}},
        "profile_diameter": 2,
    })
    assert result["ok"] is True, result
    path = session.ran[0][0]
    assert path["route"] == "/api/sketch3d/path" and path["params"]["spline"] is True
    assert path["params"]["points"][0] == pytest.approx([0.02, 0, 0])
    assert path["params"]["points"][-1] == pytest.approx([0.02, 0, 0.012 * math.pi], abs=1e-6)
    assert result["path_length_mm"] == pytest.approx(2 * math.hypot(40 * math.pi, 6 * math.pi), rel=1e-3)


def test_variable_pitch_helix_integrates_the_pitch_table():
    table = [[0, 5], [0.75, 5], [1.25, 12], [5.75, 12], [6.25, 5], [7, 5]]
    helix = helix_geometry({"axis": "Z", "center": [0, 0, 0], "diameter": 40, "pitch": table, "turns": 7,
                            "right_handed": False})
    assert helix["points"][-1][2] == pytest.approx(70, abs=1e-6)
    one_turn = helix["points"][36]
    assert one_turn[2] == pytest.approx(3.75 + 0.25 * 5 + 7 * 0.25 ** 2, abs=1e-6)
    assert "变螺距" in helix["describe"] and "70" in helix["describe"]
    with pytest.raises(WorkflowError):
        helix_geometry({"axis": "Z", "center": [0, 0, 0], "diameter": 40, "pitch": table, "height": 70})
    with pytest.raises(WorkflowError):
        helix_geometry({"axis": "Z", "center": [0, 0, 0], "diameter": 40, "pitch": [[1, 5], [0.5, 6]], "turns": 2})
