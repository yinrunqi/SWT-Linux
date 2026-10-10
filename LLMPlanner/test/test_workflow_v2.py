"""L1_v2 的纯逻辑测试：不连接 SolidWorks；同时确认 L1（v1）的工具说明与提示词不受影响。"""

from __future__ import annotations

import json
import math

import pytest

from planner.workflow.agent import SYSTEM_PROMPT, SYSTEM_PROMPT_V2, WorkflowAgent
from planner.workflow.errors import WorkflowError
from planner.workflow.profiles import normalize_loop
from planner.workflow.sketch_check import to_geometry
from planner.workflow.tools import TOOL_SPECS, WorkflowSession, tool_specs
from planner.workflow.tools_v2 import (
    HELIX_POINTS_PER_TURN,
    TOOL_SPECS_V2,
    VIEW_MODEL_SPEC,
    WorkflowSessionV2,
    coerce_args,
    create_session,
    helix_geometry,
    hull_loop,
    polar_centers,
    polyline_geometry,
    prepare_loops,
)
from test_eval_traces import ScriptedClient, _call
from test_workflow_tools import FakeSession


class FakeV2(FakeSession, WorkflowSessionV2):
    """FakeSession 的内存快照 + v2 的覆盖方法；基准面按 SolidWorks 的偏移规则模拟。"""

    def __init__(self, cascade: dict | None = None, bodies: int | None = None, **kwargs):
        super().__init__(**kwargs)
        self.cascade = cascade or {}
        self.bodies = bodies
        self.requests = []

    def snapshot(self):
        data = super().snapshot()
        if self.bodies is not None:
            data["bodyCount"] = self.bodies
        return data

    def _request(self, method, path, body=None):
        self.requests.append((path, body))
        if path == "/api/ref-plane/offset":
            name = f"基准面{len(self.features) + 1}"
            self.features.append(name)
            index = {"plane:0": 2, "plane:1": 1, "plane:2": 0}[body["reference"]]
            origin = [0.0, 0.0, 0.0]
            origin[index] = body["distance"] * (-1 if body["flip"] else 1)
            rotation = [1, 0, 0, 0, 1, 0, 0, 0, 1]
            rotation[6:9] = [1 if i == index else 0 for i in range(3)]
            return {"success": True, "result": {"featureName": name, "origin": origin, "rotation": rotation}}
        if path == "/api/ref-axis/planes":
            name = f"基准轴{len(self.features) + 1}"
            self.features.append(name)
            return {"success": True, "result": {"featureName": name}}
        if path == "/api/delete-feature":
            name = body["featureName"]
            self.deleted.append(name)
            for removed in [name, *self.cascade.get(name, [])]:
                if removed in self.features:
                    self.features.remove(removed)
            return {"success": True}
        raise AssertionError(path)


# ------------------------------------------------------------ v1 不变


def test_v1_specs_and_prompt_are_untouched():
    assert tool_specs() == TOOL_SPECS
    assert all("hull" not in json.dumps(spec, ensure_ascii=False) for spec in TOOL_SPECS)
    assert "Z=40" not in SYSTEM_PROMPT and "llm_calls_left" not in SYSTEM_PROMPT
    agent = WorkflowAgent(ScriptedClient([]), "m", WorkflowSession(base_url="http://fake"))
    assert agent.version == "v1" and agent.system_prompt == SYSTEM_PROMPT and agent.tools == TOOL_SPECS


def test_v2_specs_keep_tool_names_and_add_new_options():
    assert [s["function"]["name"] for s in TOOL_SPECS_V2] == [s["function"]["name"] for s in TOOL_SPECS] + ["axis_cylinder"]
    by_name = {s["function"]["name"]: s["function"] for s in TOOL_SPECS_V2}
    assert "polar" in by_name["hole"]["parameters"]["properties"]
    assert "hull" in by_name["extrude"]["parameters"]["properties"]["loops"]["items"]["properties"]["type"]["enum"]
    assert "Z=40" in by_name["cut"]["parameters"]["properties"]["plane"]["description"]
    assert "names" in by_name["delete_feature"]["parameters"]["properties"]
    agent = WorkflowAgent(ScriptedClient([]), "m", create_session("v2", base_url="http://fake"))
    assert agent.version == "v2" and agent.system_prompt == SYSTEM_PROMPT_V2
    assert agent.tools == TOOL_SPECS_V2 + [VIEW_MODEL_SPEC]
    agent = WorkflowAgent(ScriptedClient([]), "m", create_session("v2", base_url="http://fake"), view_tool=False)
    assert agent.tools == TOOL_SPECS_V2
    assert isinstance(create_session("v1", base_url="http://fake"), WorkflowSession)
    assert not isinstance(create_session("v1", base_url="http://fake"), WorkflowSessionV2)


# ------------------------------------------------------------ 参数与轮廓


def test_coerce_args_parses_json_strings_and_extra_quotes():
    args = coerce_args({
        "plane": "\"top\"",
        "loops": json.dumps([{"type": "circle", "center": [0, 0], "diameter": 5}]),
        "edges": "{\"origin\": [0, 0, 0], \"direction\": [0, 1, 0]}",
        "through_all": "true",
        "depth": "=wall",
    })
    assert args["plane"] == "top"
    assert args["loops"][0]["diameter"] == 5
    assert args["edges"]["direction"] == [0, 1, 0]
    assert args["through_all"] is True
    assert args["depth"] == "=wall"


def test_hull_of_two_circles_has_closed_form_area():
    loop = normalize_loop(hull_loop({"type": "hull", "circles": [
        {"center": [0, 0], "diameter": 20}, {"center": [50, 0], "diameter": 20},
    ]}))
    assert to_geometry(loop).area == pytest.approx(math.pi * 100 + 50 * 20, rel=2e-3)
    unequal = normalize_loop(hull_loop({"type": "hull", "circles": [
        {"center": [0, 0], "radius": 20}, {"center": [0, 60], "radius": 8},
    ]}))
    geometry = to_geometry(unequal)
    u0, v0, u1, v1 = geometry.bounds
    assert (u0, v0, u1, v1) == pytest.approx((-20, -20, 20, 68), abs=0.05)


def test_unequal_hull_warns_about_non_parallel_sides():
    notes: list[str] = []
    prepare_loops([{
        "type": "hull",
        "circles": [{"center": [0, 0], "diameter": 40}, {"center": [100, 0], "diameter": 15}],
    }], notes)
    assert any("直径不等" in note and "等宽" in note for note in notes)
    equal_notes: list[str] = []
    prepare_loops([{
        "type": "hull",
        "circles": [{"center": [0, 0], "diameter": 20}, {"center": [50, 0], "diameter": 20}],
    }], equal_notes)
    assert not any("直径不等" in note for note in equal_notes)


def test_v2_prompt_covers_failure_patterns():
    assert "等宽手柄" in SYSTEM_PROMPT_V2
    assert "R + width/2" in SYSTEM_PROMPT_V2 or "R+width/2" in SYSTEM_PROMPT_V2
    assert "穿过轴线的宽 slot" in SYSTEM_PROMPT_V2
    assert "紧定螺钉" in SYSTEM_PROMPT_V2
    assert "双盘" in SYSTEM_PROMPT_V2
    assert "环形把手" in SYSTEM_PROMPT_V2
    assert "等宽手柄" not in SYSTEM_PROMPT
    assert "两面平的扁条" in SYSTEM_PROMPT_V2
    assert "沿原边线的方向延长" in SYSTEM_PROMPT_V2
    assert "首尾相接" in SYSTEM_PROMPT_V2
    assert "最后统一开孔" in SYSTEM_PROMPT_V2
    assert "钻通后不要再加深" in SYSTEM_PROMPT_V2
    assert "外侧面就是轮毂端面" in SYSTEM_PROMPT_V2


def test_half_arc_with_center_only_must_give_ccw():
    loop = {"type": "path", "start": [10, 0], "segments": [{"to": [-10, 0], "center": [0, 0]}]}
    with pytest.raises(WorkflowError) as info:
        prepare_loops([loop])
    assert "ccw" in info.value.context["hint"]
    ok = {"type": "path", "start": [10, 0], "segments": [{"to": [-10, 0], "center": [0, 0], "ccw": True}]}
    assert prepare_loops([ok]) == [ok]


def test_slightly_off_arc_center_is_snapped():
    notes = []
    loop = {"type": "path", "start": [0, 0], "segments": [
        {"to": [36.678, 0]}, {"to": [0, 0], "center": [18.31, 0.5], "ccw": True},
    ]}
    snapped = prepare_loops([loop], notes)[0]
    u, v = snapped["segments"][1]["center"]
    assert math.hypot(u, v) == pytest.approx(math.hypot(36.678 - u, v), abs=1e-6)
    assert (u, v) == pytest.approx((18.339, 0.5))
    assert "圆心由" in notes[0]
    far = {"type": "path", "start": [0, 0], "segments": [{"to": [30, 0]}, {"to": [0, 0], "center": [10, 0], "ccw": True}]}
    assert prepare_loops([far], [])[0] == far


def test_full_annular_sector_becomes_annulus():
    notes = []
    loops = prepare_loops([{"type": "annular_sector", "center": [0, 0], "r_inner": 10, "r_outer": 20,
                            "start_deg": 0, "end_deg": 360}], notes)
    assert [loop["radius"] for loop in loops] == [20.0, 10.0] and "圆环" in notes[0]
    sector = {"type": "annular_sector", "center": [0, 0], "r_inner": 0, "r_outer": 20, "start_deg": 0, "end_deg": 120}
    assert prepare_loops([sector], []) == [sector]


def test_sweep_profile_is_placed_at_path_start():
    session = FakeV2(delta=500)
    result = session.call("sweep", {
        "path_plane": "front_plane", "path_start": [40, 5],
        "path_segments": [{"to": [40, 45]}],
        "loops": [{"type": "circle", "center": [0, 0], "diameter": 8}],
    })
    assert result["ok"] is True, result
    assert any("Y=5" in note for note in result["notes"])
    offsets = [body for path, body in session.requests if path == "/api/ref-plane/offset"]
    assert len(offsets) == 1 and offsets[0]["reference"] == "plane:1"
    assert offsets[0]["distance"] == pytest.approx(0.005)
    circles = [op["params"]["circles"] for op in session.ran[0] if "circles" in op.get("params", {})]
    assert circles[-1][0]["cx"] == pytest.approx(0.04) and circles[-1][0]["cz"] == pytest.approx(0.0)
    arc = FakeV2(delta=500).call("sweep", {
        "path_plane": "front_plane", "path_start": [40, 0],
        "path_segments": [{"to": [0, 40], "center": [0, 0], "ccw": True}],
        "loops": [{"type": "circle", "center": [0, 0], "diameter": 8}],
    })
    assert arc["ok"] is True and any("Y=0" in note for note in arc["notes"])
    slanted = FakeV2(delta=500).call("sweep", {
        "path_plane": "front_plane", "path_start": [0, 0], "path_segments": [{"to": [30, 30]}],
        "loops": [{"type": "circle", "center": [0, 0], "diameter": 8}],
    })
    assert slanted["error"]["code"] == "TOOL_ARGS" and "profile_plane" in slanted["error"]["hint"]


def test_helix_geometry_starts_on_radial_axis_and_follows_handedness():
    helix = helix_geometry({"axis": "Z", "center": [0, 0, 5], "diameter": 40, "pitch": 8, "turns": 2})
    assert helix["start"] == pytest.approx([20, 0, 5])
    assert helix["points"][-1] == pytest.approx([20, 0, 21], abs=1e-6)
    quarter = helix["points"][HELIX_POINTS_PER_TURN // 4]
    assert quarter == pytest.approx([0, 20, 7], abs=1e-6)
    assert helix["length_mm"] == pytest.approx(2 * math.hypot(40 * math.pi, 8))
    left = helix_geometry({"axis": "-Y", "center": [0, 0, 0], "radius": 10, "pitch": 4, "height": 8,
                           "right_handed": False})
    assert left["start"] == pytest.approx([10, 0, 0]) and left["axial"] == (0, -1, 0)
    assert left["points"][-1][1] == pytest.approx(-8)
    with pytest.raises(WorkflowError):
        helix_geometry({"axis": "Z", "center": [0, 0, 0], "diameter": 10, "pitch": 0, "turns": 3})


def test_polyline_geometry_merges_collinear_points_and_checks_corner_room():
    line = polyline_geometry({"points": [[0, 0, 0], [50, 0, 0], [100, 0, 0], [100, 80, 0]], "corner_radius": 20})
    assert line["points"] == [[0, 0, 0], [100, 0, 0], [100, 80, 0]]
    assert line["length_mm"] == pytest.approx(180 - 40 + math.pi / 2 * 20)
    with pytest.raises(WorkflowError) as info:
        polyline_geometry({"points": [[0, 0, 0], [10, 0, 0], [10, 50, 0]], "corner_radius": 20})
    assert "corner_radius" in info.value.context["hint"]
    with pytest.raises(WorkflowError):
        polyline_geometry([[0, 0, 0], [10, 0, 0], [0, 0, 0]])


def test_sweep_along_helix_with_circular_profile_reports_nominal_volume():
    session = FakeV2(volume=0.0, delta=4450.0)
    result = session.call("sweep", {
        "helix": json.dumps({"axis": "Z", "center": [0, 0, 0], "diameter": 40, "pitch": 8, "turns": 5}),
        "profile_diameter": 3,
    })
    assert result["ok"] is True, result
    path, feature = session.ran[0]
    assert path["route"] == "/api/sketch3d/path" and path["params"]["spline"] is True
    assert path["params"]["points"][0] == pytest.approx([0.02, 0, 0])
    assert feature["params"]["circularDiameter"] == pytest.approx(0.003)
    assert result["nominal_volume_mm3"] == pytest.approx(math.pi * 1.5 ** 2 * 5 * math.hypot(40 * math.pi, 8), rel=1e-4)
    assert "warnings" not in result
    tube = FakeV2(volume=0.0, delta=5000.0).call("sweep", {
        "path_3d": {"points": [[0, 0, 0], [100, 0, 0], [100, 80, 0]], "corner_radius": 20},
        "profile_diameter": 12, "inner_diameter": 8,
    })
    assert tube["ok"] is True and any("相差" in w for w in tube["warnings"])


def test_sweep_helix_custom_profile_uses_radial_and_axial_coordinates():
    session = FakeV2(volume=0.0, delta=3000.0)
    result = session.call("sweep", {
        "helix": {"axis": "Z", "center": [0, 0, 0], "radius": 25, "pitch": 10, "turns": 3},
        "loops": [{"type": "rect", "center": [0, 0], "size": [4, 2]}],
    })
    assert result["ok"] is True, result
    assert any("径向" in note for note in result["notes"])
    polyline = next(op for op in session.ran[0] if op["operation"] == "sketch_polyline")
    xs = sorted({round(p["x"], 6) for p in polyline["params"]["points"]})
    ys = sorted({round(p["y"], 6) for p in polyline["params"]["points"]})
    assert xs == [0.023, 0.027] and ys == [-0.001, 0.001]
    slanted = FakeV2(volume=0.0, delta=10.0).call("sweep", {
        "path_3d": {"points": [[0, 0, 0], [30, 30, 0]]},
        "loops": [{"type": "circle", "center": [0, 0], "diameter": 4}],
    })
    assert slanted["error"]["code"] == "TOOL_ARGS" and "profile_diameter" in slanted["error"]["hint"]


def test_loft_point_section_guides_and_tangency():
    session = FakeV2(delta=16000.0)
    result = session.call("loft", {"sections": [
        {"plane": "front_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [40, 40]}]},
        {"plane": "front_plane", "point": [0, 0]},
    ], "guides": [{"points": [[20, 0, 0], [0, 0, 0]]}]})
    assert result["ok"] is True, result
    ops = session.ran[0]
    assert any(op["operation"] == "sketch_point" for op in ops)
    assert ops[-1]["params"]["guideCount"] == 1 and ops[-2]["route"] == "/api/sketch3d/path"
    middle = FakeV2(delta=1.0).call("loft", {"sections": [
        {"plane": "front_plane", "loops": [{"type": "circle", "center": [0, 0], "diameter": 10}]},
        {"plane": "front_plane", "point": [0, 0]},
        {"plane": "front_plane", "loops": [{"type": "circle", "center": [0, 0], "diameter": 10}]},
    ]})
    assert middle["error"]["code"] == "TOOL_ARGS"
    failing = FakeV2(run_ok=False)
    failed = failing.call("loft", {"sections": [
        {"plane": "front_plane", "loops": [{"type": "circle", "center": [0, 0], "diameter": 30}]},
        {"plane": "front_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [20, 20]}]},
    ], "start_tangent": "normal"})
    assert failed["error"]["code"] == "FEATURE_FAILED"
    attempts = [ops[-1]["params"] for ops in failing.ran]
    assert [(a["startTangent"], a["reverseStart"]) for a in attempts] == [(1, True), (1, False)]


def test_failed_boss_hints_tangency_and_implicit_both_cut_is_flagged():
    failed = FakeV2(run_ok=False).call("extrude", {
        "plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 8}], "depth": 5,
    })
    assert failed["error"]["code"] == "FEATURE_FAILED" and "相切" in failed["error"]["hint"]
    cut = FakeV2(delta=-200).call("cut", {
        "plane": "front_plane", "loops": [{"type": "rect", "center": [0, 5], "size": [10, 4]}], "depth": 10,
    })
    assert cut["ok"] is True, cut
    assert any("没有给 direction" in text for text in cut["warnings"])


def test_param_and_face_edge_errors_get_hints():
    session = FakeV2()
    result = session.call("set_parameters", {"values": {"a": "=10/tan(30)"}})
    assert result["error"]["code"] == "PARAM_FORBIDDEN" and "tand" in result["error"]["hint"]
    edges = FakeV2(delta=-5).call("chamfer", {"distance": 1, "edges": "face#0"})
    assert edges["error"]["code"] == "TOOL_ARGS" and "射线" in edges["error"]["hint"]


def test_polar_centers_follow_uv_angle_convention():
    centers = polar_centers({"center": [0, 0], "pcd": 100, "count": 4, "start_deg": 45})
    assert centers[0] == pytest.approx([35.355339, 35.355339])
    assert centers[1] == pytest.approx([-35.355339, 35.355339])
    arc = polar_centers({"center": [10, 0], "radius": 10, "count": 3, "start_deg": 0, "span_deg": 180})
    assert arc == [pytest.approx([20, 0]), pytest.approx([10, 10]), pytest.approx([0, 0], abs=1e-6)]


def test_hole_accepts_polar_layout():
    session = FakeV2(delta=-300)
    result = session.call("hole", {
        "plane": "top", "polar": {"center": [0, 0], "radius": 20, "count": 6}, "diameter": 5, "depth": 4,
    })
    assert result["ok"] is True, result
    circles = session.ran[0][1]["params"]["circles"]
    assert len(circles) == 6
    assert "polar" in result["notes"][0]


def test_polar_layout_deduplicates_explicit_seed_center():
    session = FakeV2(delta=-300)
    result = session.call("hole", {
        "plane": "top",
        "centers": [[20, 0]],
        "polar": {"center": [0, 0], "radius": 20, "count": 6},
        "diameter": 5,
        "depth": 4,
    })
    assert result["ok"] is True, result
    circles = session.ran[0][1]["params"]["circles"]
    assert len(circles) == 6


# ------------------------------------------------------------ 平面写法


def test_axis_plane_syntax_creates_and_reuses_reference_plane():
    session = FakeV2(delta=100)
    loops = [{"type": "circle", "center": [0, 0], "diameter": 8}]
    first = session.call("extrude", {"plane": "Y=25", "loops": loops, "depth": 5})
    assert first["ok"] is True, first
    second = session.call("extrude", {"plane": "Y=25", "loops": loops, "depth": 3})
    assert second["ok"] is True, second
    offsets = [body for path, body in session.requests if path == "/api/ref-plane/offset"]
    assert len(offsets) == 1 and offsets[0]["reference"] == "plane:1"
    assert session.log[0]["args"]["plane"] == "Y=25"
    assert session.ran[0][0]["route"] == "/api/select-ref-plane-and-sketch"
    assert first["extent_mm"]["Y"] == [25.0, 30.0]
    zero = session.call("extrude", {"plane": "Z=0", "loops": loops, "depth": 2})
    assert zero["ok"] is True and session.ran[-1][0] == {"operation": "select_plane", "params": {"index": 0}}


def test_ref_plane_is_reused_at_same_position():
    session = FakeV2()
    first = session.call("ref_plane", {"reference": "top_plane", "offset": 12})
    again = session.call("ref_plane", {"reference": "top_plane", "offset": 12})
    assert again["reused"] is True and again["name"] == first["name"]


def test_edit_feature_rejects_reference_planes():
    session = FakeV2()
    name = session.call("ref_plane", {"reference": "top_plane", "offset": 12})["name"]
    result = session.call("edit_feature", {"name": name, "value": 20})
    assert result["ok"] is False and "Z=" in result["error"]["hint"]


def test_stale_face_index_gets_hint():
    result = FakeV2(delta=-10).call("cut", {
        "plane": "face#99", "loops": [{"type": "circle", "center": [0, 0], "diameter": 4}], "depth": 2,
    })
    assert result["error"]["code"] == "REF_NOT_FOUND"
    assert "get_model_state" in result["error"]["hint"]


# ------------------------------------------------------------ 特征反馈


def test_one_sided_through_all_from_mid_plane_is_flagged():
    session = FakeV2(delta=-500)
    result = session.call("cut", {
        "plane": "front_plane", "loops": [{"type": "rect", "center": [0, 5], "size": [10, 4]}],
        "through_all": True, "direction": "+Z",
    })
    assert result["ok"] is True, result
    assert result["extent_mm"] == {"X": [-5.0, 5.0], "Y": [3.0, 7.0], "Z": [0.0, 30.0]}
    assert any("direction=both" in text for text in result["warnings"])


def test_one_sided_through_all_from_face_below_a_boss_is_flagged():
    session = FakeV2(delta=-500)
    original = session.snapshot

    def snapshot():
        data = original()
        data["boundingBox"] = {**data["boundingBox"], "maxY": 0.03}
        return data

    session.snapshot = snapshot
    loops = [{"type": "circle", "center": [0, 0], "diameter": 6}]
    result = session.call("cut", {"plane": "face#0", "loops": loops, "through_all": True})
    assert result["ok"] is True, result
    assert any("实体面 Y=10" in text and "[10, 30]" in text for text in result["warnings"])
    clean = FakeV2(delta=-500).call("cut", {"plane": "face#0", "loops": loops, "through_all": True})
    assert not any("through_all" in text for text in clean.get("warnings", []))


def test_low_removal_ratio_is_flagged_and_both_depth_is_total():
    session = FakeV2(delta=-100)
    result = session.call("cut", {
        "plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 10}], "depth": 5,
    })
    assert result["removal_ratio"] == pytest.approx(100 / (math.pi * 25 * 5), abs=3e-3)
    assert any("只去除了" in text for text in result["warnings"])
    both = FakeV2(delta=1000).call("extrude", {
        "plane": "front_plane", "loops": [{"type": "rect", "center": [0, 50], "size": [10, 10]}],
        "depth": 10, "direction": "both",
    })
    assert both["extent_mm"]["Z"] == [-5.0, 5.0] and "总厚度" in both["note"]


def test_extra_bodies_are_rejected_and_rolled_back():
    session = FakeV2(delta=100)
    original = session.snapshot

    def snapshot():
        data = original()
        data["bodyCount"] = 1 if not session.ran else 2
        return data

    session.snapshot = snapshot
    result = session.call("extrude", {
        "plane": "front_plane", "loops": [{"type": "circle", "center": [0, 80], "diameter": 8}], "depth": 5,
    })
    assert result["error"]["code"] == "FEATURE_DISCONNECTED"
    assert result["error"]["rolled_back"]


def test_new_cylinders_are_reported():
    session = FakeV2(delta=-50)
    original = session.snapshot

    def snapshot():
        data = original()
        if session.ran:
            data["faces"] = data["faces"] + [{
                "index": 6, "surfaceType": "cylinder", "radius": 0.0025,
                "axis": {"x": 0, "y": 1, "z": 0}, "axisOrigin": {"x": 0.01, "y": 0.0, "z": -0.02},
                "box": {"minX": 0.0075, "minY": 0.0, "minZ": -0.0225, "maxX": 0.0125, "maxY": 0.01, "maxZ": -0.0175},
            }]
        return data

    session.snapshot = snapshot
    result = session.call("hole", {"plane": "top", "centers": [[10, -20]], "diameter": 5, "through_all": True})
    assert result["new_cylinders"] == [{"diameter_mm": 5.0, "axis": "Y", "count": 1, "axis_at_mm": [[10.0, 0.0, -20.0]]}]


def test_fillet_reports_estimated_edge_length_and_accepts_ray_list():
    radius = 2.0
    session = FakeV2(delta=-(1 - math.pi / 4) * radius * radius * 80)
    rays = [{"origin": [0, 20, 30], "direction": [0, -1, 0]}, {"origin": [0, 20, -30], "direction": [0, -1, 0]}]
    result = session.call("fillet", {"radius": radius, "edges": rays})
    assert result["ok"] is True, result
    (op,) = session.ran[0]
    assert op["route"] == "/api/feature/fillet" and op["params"]["edgeSelection"] == "rays"
    assert op["params"]["rays"] == [[0, 0.02, 0.03, 0, -1, 0], [0, 0.02, -0.03, 0, -1, 0]]
    assert "propagate" not in op["params"]
    assert result["estimated_edge_length_mm"] == pytest.approx(80, abs=0.1)
    assert "warnings" not in result
    single = FakeV2(delta=-5)
    single.call("fillet", {"radius": 1, "edges": "top_front", "propagate": "false"})
    (op,) = single.ran[0]
    assert op["route"] == "/api/feature/fillet" and op["params"]["propagate"] is False
    mixed = FakeV2(delta=-5)
    mixed.call("chamfer", {"distance": 1, "edges": ["top_front", rays[0]]})
    assert [o["params"]["edgeSelection"] for o in mixed.ran[0]] == ["top_front", "ray"]


def test_countersink_cuts_a_cone_whose_depth_follows_the_angle():
    session = FakeV2(delta=-80)
    result = session.call("countersink", {
        "plane": "top", "centers": [[0, 0], [20, 0]], "through_diameter": 4.5, "head_diameter": 9,
        "through_all": True,
    })
    assert result["ok"] is True, result
    cone = session.ran[0][-1]
    assert cone["route"] == "/api/feature/cut-extrusion"
    assert cone["params"]["draftAngleDeg"] == pytest.approx(45)
    assert cone["params"]["depth"] == pytest.approx(0.00225)
    assert result["cone_expected_mm3"] == pytest.approx(2 * math.pi * 2.25 / 12 * (81 + 40.5 + 20.25), rel=1e-4)
    assert session.ran[1][-1]["params"]["endCondition"] == "through_all"
    derived = FakeV2(delta=-80).call("countersink", {
        "plane": "top", "centers": [[0, 0]], "through_diameter": 4, "head_diameter": 8, "head_depth": 2 / math.sqrt(3),
        "through_all": True,
    })
    assert derived["angle_deg"] == pytest.approx(120, abs=0.01)
    assert "counterbore" in {s["function"]["name"]: s["function"] for s in TOOL_SPECS_V2}["countersink"]["description"]


def test_ambiguous_edge_rays_are_flagged():
    along_edge = FakeV2(delta=-50).call("fillet", {"radius": 2, "edges": {"origin": [40, 5, 30], "direction": [0, 1, 0]}})
    assert any("贴着面掠过" in text for text in along_edge["warnings"])
    corner = FakeV2(delta=-50).call("fillet", {"radius": 2, "edges": [
        {"origin": [0, 20, 30], "direction": [0, -1, 0]}, {"origin": [40, 20, 30], "direction": [0, -1, 0]},
    ]})
    assert len(corner["warnings"]) == 1 and "第 2 条射线命中 face#0" in corner["warnings"][0]


def test_pattern_volume_is_checked_against_seed():
    session = FakeV2(delta=-100)
    session.call("hole", {"plane": "top", "centers": [[20, 0]], "diameter": 5, "depth": 3})
    session.delta = -150
    result = session.call("circular_pattern", {"count": 6, "axis": "Y", "center": [0, 0, 0]})
    assert result["expected_delta_mm3"] == pytest.approx(-500)
    assert any("阵列" in text for text in result["warnings"])
    axis_requests = [body for path, body in session.requests if path == "/api/ref-axis/planes"]
    assert axis_requests == [{"first": "plane:2", "second": "plane:0"}]
    assert session.ran[-1][0]["params"]["axisFeature"] == session.features[-3]
    session.call("circular_pattern", {"count": 6, "axis": "Y", "center": [0, 5, 0]})
    assert len([1 for path, _ in session.requests if path == "/api/ref-axis/planes"]) == 1


def test_circular_pattern_rejects_spacing_given_as_total_angle():
    session = FakeV2(delta=-100)
    session.call("hole", {"plane": "top", "centers": [[20, 0]], "diameter": 5, "depth": 3})
    result = session.call("circular_pattern", {"count": 5, "axis": "Y", "center": [0, 0, 0], "angle_deg": 72})
    assert result["error"]["code"] == "TOOL_ARGS"
    assert "总角度" in result["error"]["message"]
    partial = session.call("circular_pattern", {"count": 3, "axis": "Y", "center": [0, 0, 0], "angle_deg": 90})
    assert partial["ok"] is True


def test_face_sketch_selects_observed_face_directly():
    session = FakeV2(delta=-50)
    session.call("cut", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 4}], "depth": 2})
    assert session.ran[0][0] == {
        "operation": "select_observed_face_and_sketch", "route": "/api/select-observed-face-and-sketch",
        "params": {"faceIndex": 0, "exact": True},
    }
    v1 = FakeSession(delta=-50)
    v1.call("cut", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 4}], "depth": 2})
    assert v1.ran[0][0] == {"operation": "select_observed_face_and_sketch", "params": {"faceIndex": 0}}


def test_delete_feature_reports_cascade():
    session = FakeV2()
    session.features = ["凸台1", "切除1", "阵列1"]
    session.cascade = {"切除1": ["阵列1"]}
    result = session.call("delete_feature", {"name": "切除1"})
    assert result["deleted"] == ["切除1"] and result["also_removed"] == ["阵列1"]
    many = FakeV2()
    many.features = ["A", "B", "C"]
    out = many.call("delete_feature", {"names": ["C", "B"]})
    assert out["deleted"] == ["C", "B"] and "also_removed" not in out


# ------------------------------------------------------------ 状态


def _cylinder(index, x, z, diameter=6.0, top=10.0):
    return {
        "index": index, "surfaceType": "cylinder", "radius": diameter / 2000,
        "axis": {"x": 0, "y": 1, "z": 0}, "axisOrigin": {"x": x / 1000, "y": 0, "z": z / 1000},
        "box": {"minX": (x - 3) / 1000, "minY": 0.0, "minZ": (z - 3) / 1000,
                "maxX": (x + 3) / 1000, "maxY": top / 1000, "maxZ": (z + 3) / 1000},
    }


def test_state_groups_holes_and_filters():
    session = FakeV2()
    original = session.snapshot

    def snapshot():
        data = original()
        holes = []
        for i, (x, z) in enumerate([(20, 20), (-20, 20), (20, -20), (-20, -20)]):
            holes += [_cylinder(10 + 2 * i, x, z), _cylinder(11 + 2 * i, x, z)]
        data["faces"] = data["faces"] + holes + [_cylinder(30, 0, 0, diameter=12)]
        return data

    session.snapshot = snapshot
    state = session.call("get_model_state", {})["state"]
    groups = {group["diameter_mm"]: group for group in state["cylinder_groups"]}
    assert groups[6.0]["count"] == 4 and len(groups[6.0]["refs"]) == 8
    assert groups[12.0]["count"] == 1
    near = session.call("get_model_state", {"type": "cylinder", "near": [20, 5, 20], "radius": 2})["state"]
    assert "planar_faces" not in near
    assert [group["count"] for group in near["cylinder_groups"]] == [1]
    planes = session.call("get_model_state", {"type": "plane", "axis": "Y"})["state"]
    assert {face["normal"] for face in planes["planar_faces"]} == {"+Y", "-Y"}


# ------------------------------------------------------------ agent 循环


class StateSession(FakeV2):
    pass


def test_agent_v2_blocks_duplicates_and_repeated_failures_and_trims_old_state():
    session = StateSession(delta=0.0)
    bad = {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 4}], "depth": 2}
    client = ScriptedClient([
        (1, [_call("a", "get_model_state"), _call("b", "cut", bad), _call("c", "cut", bad)]),
        (2, [_call("d", "cut", bad)]),
        (3, [_call("e", "cut", bad), _call("f", "get_model_state")]),
        (4, [_call("g", "finish", {"summary": "s"})]),
    ])
    outcome = WorkflowAgent(client, "m", session, max_llm_calls=8).run("立方体")
    codes = [item["error_code"] for item in outcome["tool_calls"]]
    assert codes == [None, "FEATURE_NO_EFFECT", "DUPLICATE_CALL", "FEATURE_NO_EFFECT", "REPEATED_FAILURE", None, None]
    assert [item.get("blocked", False) for item in outcome["tool_calls"]][2:5] == [True, False, True]
    tool_messages = [m for m in outcome["messages"] if m["role"] == "tool"]
    assert tool_messages[0]["content"].startswith("（此前的 get_model_state")
    latest = json.loads(tool_messages[5]["content"])
    assert latest["ok"] is True and latest["llm_calls_left"] == 5
    assert outcome["status"] == "finished"


def test_agent_v2_finish_rejects_multiple_bodies():
    session = FakeV2(bodies=2)
    client = ScriptedClient([(1, [_call("a", "finish", {"summary": "s"})])])
    outcome = WorkflowAgent(client, "m", session, max_llm_calls=1).run("立方体")
    assert outcome["tool_calls"][0]["error_code"] == "MULTI_BODY"
