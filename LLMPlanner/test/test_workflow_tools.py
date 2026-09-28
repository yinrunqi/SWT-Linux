"""workflow 工具层的纯逻辑测试：不连接 SolidWorks。"""

from __future__ import annotations

import math

import pytest

from planner.workflow.errors import WorkflowError
from planner.workflow.frames import base_plane_frame, face_frame
from planner.workflow.profiles import draw_operations, normalize_loop, normalize_loops
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
