"""workflow 工具层的纯逻辑测试：不连接 SolidWorks。"""

from __future__ import annotations

import math

import pytest

from planner.workflow.errors import WorkflowError
from planner.workflow.frames import base_plane_frame, face_frame
from planner.workflow.profiles import draw_open_path, draw_operations, normalize_loop, normalize_loops
from planner.workflow.tools import TOOL_NAMES, TOOL_SPECS, WorkflowSession


def _box_faces(size=(80.0, 10.0, 60.0)):
    """以原点为中心、底面在 Y=0 的长方体六个面（单位米，外法向）。"""
    sx, sy, sz = (value / 1000 for value in size)
    faces = []
    specs = [
        ((0, 1, 0), (0, sy, 0), (-sx / 2, sy, -sz / 2, sx / 2, sy, sz / 2), sx * sz),
        ((0, -1, 0), (0, 0, 0), (-sx / 2, 0, -sz / 2, sx / 2, 0, sz / 2), sx * sz),
        ((1, 0, 0), (sx / 2, sy / 2, 0), (sx / 2, 0, -sz / 2, sx / 2, sy, sz / 2), sy * sz),
        ((-1, 0, 0), (-sx / 2, sy / 2, 0), (-sx / 2, 0, -sz / 2, -sx / 2, sy, sz / 2), sy * sz),
        ((0, 0, 1), (0, sy / 2, sz / 2), (-sx / 2, 0, sz / 2, sx / 2, sy, sz / 2), sx * sy),
        ((0, 0, -1), (0, sy / 2, -sz / 2), (-sx / 2, 0, -sz / 2, sx / 2, sy, -sz / 2), sx * sy),
    ]
    for index, (normal, center, box, area) in enumerate(specs):
        faces.append({
            "index": index,
            "surfaceType": "plane",
            "normal": dict(zip("xyz", normal)),
            "center": dict(zip("xyz", center)),
            "box": dict(zip(("minX", "minY", "minZ", "maxX", "maxY", "maxZ"), box)),
            "area": area,
        })
    return faces


class FakeSession(WorkflowSession):
    """用内存快照代替 SolidWorks：_run 按预设的体积变化更新快照。"""

    def __init__(self, volume=48000.0, delta=0.0, run_ok=True):
        super().__init__(base_url="http://fake")
        self.has_part = True
        self.volume = volume
        self.delta = delta
        self.run_ok = run_ok
        self.features = []
        self.deleted = []
        self.ran = []

    def snapshot(self):
        return {
            "features": [{"name": name, "type": "ICE"} for name in self.features],
            "faces": _box_faces(),
            "volumeMm3": self.volume,
            "boundingBox": {"minX": -0.04, "minY": 0.0, "minZ": -0.03, "maxX": 0.04, "maxY": 0.01, "maxZ": 0.03},
        }

    def _run(self, operations):
        self.ran.append(operations)
        self.features += [f"草图{len(self.features) + 1}", f"特征{len(self.features) + 2}"]
        if self.run_ok:
            self.volume += self.delta
        return self.run_ok, [{"operation": op["operation"], "success": self.run_ok, "message": "x"} for op in operations]

    def _request(self, method, path, body=None):
        if path == "/api/delete-feature":
            self.deleted.append(body["featureName"])
            self.features.remove(body["featureName"])
            return {"success": True}
        raise AssertionError(path)


def test_tool_specs_are_named_uniquely():
    names = [spec["function"]["name"] for spec in TOOL_SPECS]
    assert len(names) == len(set(names)) == len(TOOL_NAMES)
    assert "finish" in TOOL_NAMES and "extrude" in TOOL_NAMES


def test_normalize_engineer_loops():
    assert normalize_loop({"type": "circle", "center": [1, 2], "diameter": 10}) == {
        "type": "circle", "cx": 1.0, "cy": 2.0, "r": 5.0,
    }
    rect = normalize_loop({"type": "rect", "center": [0, 0], "size": [80, 60]})
    assert (rect["x1"], rect["y1"], rect["x2"], rect["y2"]) == (-40, -30, 40, 30)
    hexagon = normalize_loop({"type": "regular_polygon", "center": [0, 0], "sides": 6, "across_flats": 13})
    assert math.isclose(hexagon["r"], 13 / 2 / math.cos(math.pi / 6))
    with pytest.raises(WorkflowError) as err:
        normalize_loop({"type": "circle", "center": [0, 0], "diameter": -1})
    assert err.value.code == "TOOL_ARGS"
    with pytest.raises(WorkflowError):
        normalize_loops([])


@pytest.mark.parametrize(
    "plane, u, v, expected",
    [
        ("front_plane", 30, 10, {"x": 0.03, "y": 0.01}),
        ("top_plane", 30, 10, {"x": 0.03, "y": -0.01}),
        ("right_plane", 30, 10, {"x": -0.03, "y": 0.01}),
    ],
)
def test_base_plane_sketch_mapping_matches_measured_solidworks(plane, u, v, expected):
    frame = base_plane_frame(plane)
    assert frame.point_params(u, v) == pytest.approx(expected)


def test_face_frame_uses_model_coordinates():
    top = face_frame(_box_faces()[0], "top")
    assert (top.axis, top.normal_sign, top.u_axis, top.v_axis) == ("Y", 1, "X", "Z")
    assert top.offset_mm == pytest.approx(10.0)
    assert top.point_params(20, -15) == pytest.approx({"x": 0.02, "y": 0.01, "z": -0.015})
    right = face_frame(_box_faces()[2], "right")
    assert (right.u_axis, right.v_axis) == ("Z", "Y")
    assert right.boundary_uv() == pytest.approx({"type": "rect", "x1": -30, "y1": 0, "x2": 30, "y2": 10})


def test_face_frame_rejects_tilted_face():
    face = {**_box_faces()[0], "normal": {"x": 0.5, "y": 0.8660, "z": 0}}
    with pytest.raises(WorkflowError) as err:
        face_frame(face, "slope")
    assert err.value.code == "REF_NOT_AXIS_ALIGNED"


def test_draw_operations_batches_circles_and_polylines():
    frame = base_plane_frame("top_plane")
    loops = normalize_loops([
        {"type": "rect", "center": [0, 0], "size": [20, 10]},
        {"type": "circle", "center": [1, 1], "diameter": 2},
        {"type": "circle", "center": [-1, 1], "diameter": 2},
    ])
    operations = draw_operations(frame, loops)
    assert [op["operation"] for op in operations] == ["sketch_circles", "sketch_polyline"]
    assert len(operations[0]["params"]["circles"]) == 2
    assert operations[0]["params"]["noInference"] is True
    assert len(operations[1]["params"]["points"]) == 4


def test_extrude_direction_rules():
    top_plane = base_plane_frame("top_plane")
    assert WorkflowSession._extrude_flip(top_plane, None) == (False, False, "+Y")
    assert WorkflowSession._extrude_flip(top_plane, "-Y") == (True, False, "-Y")
    assert WorkflowSession._extrude_flip(top_plane, "both") == (False, True, "both")
    bottom = face_frame(_box_faces()[1], "bottom")
    assert WorkflowSession._extrude_flip(bottom, None) == (False, False, "-Y")
    with pytest.raises(WorkflowError):
        WorkflowSession._extrude_flip(top_plane, "+X")


def test_cut_direction_auto_follows_material():
    session = FakeSession()
    snapshot = session.snapshot()
    assert session._cut_direction(base_plane_frame("top_plane"), None, snapshot) == ("one", 1)
    assert session._cut_direction(base_plane_frame("front_plane"), None, snapshot) == ("both", 0)
    top = face_frame(_box_faces()[0], "top")
    assert session._cut_direction(top, "auto", snapshot) == ("one", -1)


def test_revolve_profile_must_stay_on_one_side_of_axis():
    loops = normalize_loops([{"type": "rect", "center": [0, 25], "size": [10, 10]}])
    with pytest.raises(WorkflowError) as err:
        WorkflowSession._check_axis_side(loops, [0, 20], [0, 40])
    assert err.value.code == "SKETCH_CROSSES_AXIS"
    touching = normalize_loops([{"type": "rect", "center": [5, 25], "size": [10, 10]}])
    WorkflowSession._check_axis_side(touching, [0, 20], [0, 40])


def test_overlapping_loops_rejected_before_solidworks():
    session = FakeSession(delta=100)
    result = session.call("extrude", {
        "plane": "top",
        "loops": [
            {"type": "circle", "center": [0, 0], "diameter": 20},
            {"type": "rect", "center": [15, 0], "size": [20, 5]},
        ],
        "depth": 5,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == "SKETCH_LOOPS_OVERLAP"
    assert result["error"]["hint"]
    assert session.ran == []


def test_cut_without_volume_change_is_rolled_back():
    session = FakeSession(delta=0.0)
    result = session.call("cut", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 5}], "depth": 2})
    assert result["error"]["code"] == "FEATURE_NO_EFFECT"
    assert session.deleted == ["特征2", "草图1"]
    assert session.features == []


def test_failed_cut_explains_that_nothing_may_be_left_to_remove():
    session = FakeSession(run_ok=False)
    result = session.call("cut", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 5}], "depth": 2})
    assert result["error"]["code"] == "FEATURE_FAILED"
    assert "已有的孔" in result["error"]["hint"]
    extrude = FakeSession(run_ok=False).call(
        "extrude", {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 5}], "depth": 2})
    assert "hint" not in extrude["error"]


def test_missing_reference_names_the_argument():
    result = FakeSession().call("ref_plane", {})
    assert result["error"]["code"] == "TOOL_ARGS"
    assert "reference" in result["error"]["message"]


def test_cut_missing_face_is_rejected():
    session = FakeSession(delta=-10)
    result = session.call("cut", {"plane": "top", "loops": [{"type": "circle", "center": [200, 0], "diameter": 5}], "depth": 2})
    assert result["error"]["code"] == "SKETCH_MISSES_FACE"
    assert session.ran == []


def test_successful_hole_uses_selected_face_and_inward_cut():
    session = FakeSession(delta=-392.7)
    result = session.call("hole", {"plane": "top", "centers": [[20, 15]], "diameter": 10, "depth": 5})
    assert result["ok"] is True
    assert result["direction"] == "-Y"
    operations = session.ran[0]
    assert operations[0] == {"operation": "select_observed_face_and_sketch", "params": {"faceIndex": 0}}
    assert operations[-1]["params"]["flipDirection"] is False
    assert operations[1]["params"]["circles"][0] == pytest.approx({"cx": 0.02, "cy": 0.01, "cz": 0.015, "radius": 0.005})


def _area(loop: dict) -> float:
    from planner.workflow.sketch_check import to_geometry
    return to_geometry(loop).area


def test_rounded_rect_slot_and_sector_areas_match_closed_form():
    rounded = normalize_loop({"type": "rect", "center": [0, 0], "size": [40, 20], "corner_radius": 5})
    assert rounded["type"] == "path"
    assert [s["kind"] for s in rounded["segments"]].count("arc") == 4
    assert _area(rounded) == pytest.approx(40 * 20 - (4 - math.pi) * 25, rel=2e-3)
    slot = normalize_loop({"type": "slot", "start": [0, 0], "end": [30, 0], "width": 10})
    assert _area(slot) == pytest.approx(30 * 10 + math.pi * 25, rel=2e-3)
    sector = normalize_loop({
        "type": "annular_sector", "center": [0, 0], "r_inner": 20, "r_outer": 50, "start_deg": 30, "end_deg": 90,
    })
    assert _area(sector) == pytest.approx(math.pi * (50 ** 2 - 20 ** 2) / 6, rel=2e-3)


def test_path_arcs_by_center_radius_through_and_tangent():
    half_disk = normalize_loop({"type": "path", "start": [-10, 0], "segments": [
        {"to": [10, 0]}, {"to": [-10, 0], "center": [0, 0], "ccw": True},
    ]})
    assert _area(half_disk) == pytest.approx(math.pi * 50, rel=2e-3)
    by_radius = normalize_loop({"type": "path", "start": [-10, 0], "segments": [
        {"to": [10, 0]}, {"to": [-10, 0], "radius": 10, "ccw": True},
    ]})
    assert _area(by_radius) == pytest.approx(math.pi * 50, rel=2e-3)
    through = normalize_loop({"type": "path", "start": [-10, 0], "segments": [
        {"to": [10, 0]}, {"to": [-10, 0], "through": [0, 10]},
    ]})
    assert _area(through) == pytest.approx(math.pi * 50, rel=2e-3)
    # 直线沿 +u 到 (20,0)，再相切圆弧转回 (20,20)：半圆凸向 +u，自动补直线闭合
    tangent = normalize_loop({"type": "path", "start": [0, 0], "segments": [
        {"to": [20, 0]}, {"to": [20, 20], "tangent": True}, {"to": [0, 20]},
    ]})
    assert _area(tangent) == pytest.approx(20 * 20 + math.pi * 50, rel=2e-3)


def test_corner_radius_that_does_not_fit_is_rejected():
    with pytest.raises(WorkflowError) as err:
        normalize_loop({"type": "rect", "center": [0, 0], "size": [10, 10], "corner_radius": 6})
    assert err.value.code == "SKETCH_DEGENERATE"


def test_path_is_drawn_as_one_profile_with_three_point_arcs():
    frame = base_plane_frame("top_plane")
    loop = normalize_loop({"type": "slot", "start": [0, 0], "end": [30, 0], "width": 10})
    (operation,) = draw_operations(frame, [loop])
    assert operation["operation"] == "sketch_profile" and operation["route"] == "/api/sketch/profile"
    segments = operation["params"]["segments"]
    assert [s["type"] for s in segments] == ["line", "arc", "line", "arc"]
    arc = segments[1]
    # 上视面 sketch = (X, -Z)：右端半圆的中点在 u=35, v=0
    assert (arc["xm"], arc["ym"]) == pytest.approx((0.035, 0.0))
    assert (arc["x1"], arc["y1"]) == pytest.approx((0.03, 0.005))


class PlaneSession(FakeSession):
    """在 FakeSession 上模拟 /api/ref-plane/offset：首次总放在参考的反侧，验证翻转重建。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []

    def _request(self, method, path, body=None):
        self.requests.append((path, body))
        if path == "/api/ref-plane/offset":
            name = f"基准面{len(self.features) + 1}"
            self.features.append(name)
            flip = body["flip"]
            position = (10 + body["distance"] * 1000) if flip else (10 - body["distance"] * 1000)
            return {"success": True, "result": {
                "featureName": name, "origin": [0, position / 1000, 0], "rotation": [1, 0, 0, 0, 0, -1, 0, 1, 0],
            }}
        if path == "/api/delete-feature":
            self.features.remove(body["featureName"])
            return {"success": True}
        raise AssertionError(path)


def test_ref_plane_checks_position_and_is_usable_as_sketch_plane():
    session = PlaneSession(delta=100)
    result = session.call("ref_plane", {"reference": "top", "offset": 15})
    assert result["ok"] is True
    name = result["name"]
    assert [body["flip"] for path, body in session.requests if path == "/api/ref-plane/offset"] == [False, True]
    assert session.features == [name]
    frame = session.ref_planes[name]
    assert (frame.axis, frame.normal_sign, frame.offset_mm) == ("Y", 1, pytest.approx(25.0))
    assert session.state_summary()["reference_planes"][0]["at"] == "Y=25.000"
    extruded = session.call("extrude", {
        "plane": name, "loops": [{"type": "circle", "center": [5, 0], "diameter": 8}], "depth": 5, "direction": "-Y",
    })
    assert extruded["ok"] is True
    operations = session.ran[0]
    assert operations[0]["route"] == "/api/select-ref-plane-and-sketch"
    assert operations[1]["params"]["circles"][0] == pytest.approx({"cx": 0.005, "cy": 0.025, "cz": 0.0, "radius": 0.004})
    assert operations[-1]["params"]["flipDirection"] is True
    assert session._cut_direction(frame, None, session.snapshot()) == ("one", -1)


def test_mirror_validates_features_and_uses_plane_reference():
    session = FakeSession(delta=500)
    session.features = ["凸台-拉伸1"]
    missing = session.call("mirror", {"plane": "right_plane", "features": ["不存在"]})
    assert missing["error"]["code"] == "TOOL_ARGS"
    result = session.call("mirror", {"plane": "right_plane", "features": ["凸台-拉伸1"]})
    assert result["ok"] is True
    (operation,) = session.ran[0]
    assert operation["route"] == "/api/feature/mirror"
    assert operation["params"] == {"plane": "plane:2", "features": ["凸台-拉伸1"], "mirrorBody": False}


def test_parameters_drive_tool_arguments():
    session = FakeSession(delta=100)
    assert session.call("set_parameters", {"values": {"W": 40, "wall": "=W/10"}})["ok"]
    result = session.call("extrude", {
        "plane": "top", "loops": [{"type": "rect", "center": [0, 0], "size": ["=W", "=wall*2"]}], "depth": "=wall",
    })
    assert result["ok"] is True
    params = session.ran[0][-1]["params"]
    assert params["depth"] == pytest.approx(0.004)
    assert session.call("set_parameters", {"values": {"bad": "=missing*2"}})["error"]["code"] == "PARAM_UNDEFINED"


def test_new_tool_names_are_registered():
    for name in ("edit_feature", "rib", "counterbore", "sweep", "loft", "shell", "draft", "sheet_metal"):
        assert name in TOOL_NAMES


def test_open_path_is_not_closed():
    frame = base_plane_frame("front_plane")
    (operation,) = draw_open_path(frame, [0, 0], [{"to": [20, 15]}])
    assert operation["params"]["closed"] is False
    assert len(operation["params"]["segments"]) == 1


def test_extrude_up_to_uses_until_route():
    session = FakeSession(delta=100)
    result = session.call("extrude", {
        "plane": "right_plane",
        "loops": [{"type": "circle", "center": [0, 15], "diameter": 16}],
        "up_to": "left",
        "direction": "-X",
    })
    assert result["ok"] is True
    feature = session.ran[0][-1]
    assert feature["route"] == "/api/feature/until"
    assert feature["params"]["until"].startswith("face:")
    assert feature["params"]["cut"] is False


def test_rib_emits_open_path_and_rib_route():
    session = FakeSession(delta=200)
    result = session.call("rib", {
        "plane": "front_plane", "start": [10, 6], "end": [24, 24], "thickness": 4,
    })
    assert result["ok"] is True
    operations = session.ran[0]
    assert operations[1]["params"]["closed"] is False
    assert operations[-1]["route"] == "/api/feature/rib"
    assert operations[-1]["params"]["thickness"] == pytest.approx(0.004)


def test_loft_requires_two_sections_and_closes_intermediate_sketch():
    session = FakeSession(delta=300)
    bad = session.call("loft", {"sections": [{"plane": "top_plane", "loops": [{"type": "circle", "center": [0, 0], "diameter": 20}]}]})
    assert bad["error"]["code"] == "TOOL_ARGS"
    result = session.call("loft", {"sections": [
        {"plane": "top_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [30, 30]}]},
        {"plane": "top", "loops": [{"type": "circle", "center": [0, 0], "diameter": 24}]},
    ]})
    assert result["ok"] is True
    operations = session.ran[0]
    assert operations[2]["route"] == "/api/close-sketch-named"
    assert operations[-1]["route"] == "/api/feature/loft"
    assert operations[-1]["params"]["sectionCount"] == 2


def test_sheet_metal_rejects_existing_body():
    session = FakeSession(volume=1000, delta=10)
    result = session.call("sheet_metal", {
        "plane": "top_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [70, 40]}], "thickness": 1.5,
    })
    assert result["error"]["code"] == "TOOL_ARGS"
    empty = FakeSession(volume=0, delta=4200)
    ok = empty.call("sheet_metal", {
        "plane": "top_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [70, 40]}], "thickness": 1.5,
    })
    assert ok["ok"] is True
    assert empty.ran[0][-1]["route"] == "/api/feature/sheet-metal-base"


def test_counterbore_rejects_smaller_head():
    session = FakeSession(delta=-100)
    result = session.call("counterbore", {
        "plane": "top", "centers": [[0, 0]],
        "through_diameter": 10, "head_diameter": 8, "head_depth": 3,
    })
    assert result["error"]["code"] == "TOOL_ARGS"


def test_edit_feature_validates_name():
    session = PlaneSession(delta=0)
    session.features = ["凸台-拉伸1"]

    def request(method, path, body=None):
        if path == "/api/modify-dimension":
            unit = "deg" if session.angular else "mm"
            return {"success": True, "unit": unit, "oldValueUser": 10.0, "newValueUser": body["newValueUser"]}
        return PlaneSession._request(session, method, path, body)

    session._request = request
    session.angular = False
    missing = session.call("edit_feature", {"name": "没有", "value": 20})
    assert missing["error"]["code"] == "TOOL_ARGS"
    ok = session.call("edit_feature", {"name": "凸台-拉伸1", "value": 20})
    assert ok["ok"] is True
    assert ok["new_value_mm"] == 20.0 and ok["old_value_mm"] == 10.0
    session.angular = True
    angle = session.call("edit_feature", {"name": "凸台-拉伸1", "value": 288})
    assert angle["new_value_deg"] == 288.0 and "new_value_mm" not in angle
