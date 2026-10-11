"""离线测试：逐角弯管半径、标准钻头/螺纹查表、曲线法向偏移、倒圆凹凸回显、接管起点露面、旋转轮廓半径回显。"""

from __future__ import annotations

import math

import pytest

from planner.workflow import param_curves
from planner.workflow.errors import WorkflowError
from planner.workflow.standard_sizes import resolve_size
from planner.workflow.tools_v2 import path3d_operation, polyline_geometry
from test_workflow_v2 import FakeV2


def test_path_3d_takes_one_radius_per_corner():
    p0, p1, p2, p3 = [0, 0, 0], [150, 0, 0], [150, 120, 0], [150, 120, 100]
    line = polyline_geometry({"points": [p0, p1, p2, p3], "corner_radii": [45, 60]})
    assert line["corner_radii"] == [45, 60]
    quarter = math.pi / 2
    expected = 150 + 120 + 100 - 2 * 45 - 2 * 60 + (45 + 60) * quarter
    assert line["length_mm"] == pytest.approx(expected)
    assert "R45/R60" in line["describe"]
    op = path3d_operation(line["points"], corner_radius=line["corner_radius"], corner_radii=line["corner_radii"])
    assert op["params"]["cornerRadii"] == pytest.approx([0.045, 0.06])


def test_corner_radii_follow_their_points_through_collinear_merging():
    line = polyline_geometry({"points": [[0, 0, 0], [50, 0, 0], [100, 0, 0], [100, 80, 0], [100, 80, 90]],
                              "corner_radii": [5, 20, 30]})
    assert len(line["points"]) == 4
    assert line["corner_radii"] == [20, 30]
    same = polyline_geometry({"points": [[0, 0, 0], [100, 0, 0], [100, 80, 0]], "corner_radii": [20]})
    assert same["corner_radii"] is None and same["corner_radius"] == 20


def test_corner_radii_count_and_room_are_checked():
    with pytest.raises(WorkflowError, match="2 个数"):
        polyline_geometry({"points": [[0, 0, 0], [100, 0, 0], [100, 80, 0], [100, 80, 50]], "corner_radii": [10]})
    with pytest.raises(WorkflowError, match="放不下"):
        polyline_geometry({"points": [[0, 0, 0], [100, 0, 0], [100, 30, 0], [0, 30, 0]], "corner_radii": [5, 40]})


def test_sweep_sends_per_corner_radii():
    session = FakeV2(delta=5000.0)
    result = session.call("sweep", {
        "path_3d": {"points": [[0, 0, 0], [150, 0, 0], [150, 120, 0], [150, 120, 100]], "corner_radii": [45, 60]},
        "profile_diameter": 30,
    })
    assert result["ok"] is True, result
    path_op = next(op for op in session.ran[0] if op.get("route") == "/api/sketch3d/path")
    assert path_op["params"]["cornerRadii"] == pytest.approx([0.045, 0.06])


@pytest.mark.parametrize("text, mm", [
    ("#21", 0.159 * 25.4), ("#21 DRILL", 0.159 * 25.4), ("No. 7", 0.201 * 25.4),
    ("H", 0.266 * 25.4), ('"H" DRILL', 0.266 * 25.4), ("letter R", 0.339 * 25.4),
    ("17/64", 17 / 64 * 25.4), ("1 1/8", 1.125 * 25.4),
    ("10-32 UNF", 0.159 * 25.4), ("1/4-20 UNC-2B", 0.201 * 25.4), ("5/16-18", 0.257 * 25.4),
    ("M8", 6.8), ("M8x1", 7.0), ("M10 x 1.25", 8.75),
])
def test_drill_and_tap_drill_designations(text, mm):
    value, label = resolve_size(text)
    assert value == pytest.approx(mm, abs=1e-6), label


@pytest.mark.parametrize("text, mm", [
    ("1/4-20", 6.35), ("10-32 UNF", 0.19 * 25.4), ("#1-64", 0.073 * 25.4), ("1-8 UNC", 25.4), ("M8x1", 8.0),
])
def test_thread_nominal_diameter(text, mm):
    assert resolve_size(text, nominal=True)[0] == pytest.approx(mm, abs=1e-6)


def test_plain_numbers_and_expressions_are_not_designations():
    assert resolve_size("8") is None
    assert resolve_size("=d+1") is None
    assert resolve_size("6.5") is None
    with pytest.raises(WorkflowError):
        resolve_size("#95")


def test_hole_diameter_accepts_a_drill_designation_and_echoes_it():
    session = FakeV2(delta=-200.0)
    result = session.call("hole", {"plane": "top", "centers": [[0, 0]], "diameter": "#21", "depth": 9.525})
    assert result["ok"] is True, result
    assert any("4.0386" in note and "号码钻" in note for note in result["notes"])
    tapped = FakeV2(delta=-200.0).call("tapped_hole", {
        "plane": "top", "centers": [[0, 0]], "diameter": "M8", "thread_diameter": "M8", "depth": 12})
    assert tapped["ok"] is True, tapped
    assert any("6.8" in note for note in tapped["notes"])


def test_parameter_named_like_a_letter_drill_is_not_replaced():
    session = FakeV2(delta=-200.0)
    assert session.call("set_parameters", {"values": {"H": 5}})["ok"] is True
    result = session.call("hole", {"plane": "top", "centers": [[0, 0]], "diameter": "H", "depth": 3})
    assert result["ok"] is True, result
    assert not any("标准表" in note for note in result.get("notes", []))


def test_closed_curve_offset_is_outward_regardless_of_direction():
    for t in ([0, "2*pi"], ["2*pi", 0]):
        spec = {"u": "50*cos(t)", "v": "50*sin(t)", "t": [eval(str(v), {"pi": math.pi}) for v in t], "offset": -10}
        points = param_curves.sample(spec, ("u", "v"), {})
        assert all(math.hypot(*p) == pytest.approx(40, abs=1e-6) for p in points)


def test_offset_of_a_cam_pitch_curve_is_a_true_normal_offset():
    pitch = {"u": "(40 + 10*(1 - cos(t)))*cos(t)", "v": "(40 + 10*(1 - cos(t)))*sin(t)", "t": [0, 2 * math.pi]}
    base = param_curves.sample(pitch, ("u", "v"), {})
    cam = param_curves.sample({**pitch, "offset": -8}, ("u", "v"), {})

    def distance_to(polyline, point):
        best = math.inf
        for a, b in zip(polyline, polyline[1:]):
            ab = [b[0] - a[0], b[1] - a[1]]
            t = max(0.0, min(1.0, ((point[0] - a[0]) * ab[0] + (point[1] - a[1]) * ab[1]) / (ab[0] ** 2 + ab[1] ** 2)))
            best = min(best, math.dist(point, (a[0] + t * ab[0], a[1] + t * ab[1])))
        return best

    assert all(distance_to(base, p) == pytest.approx(8, abs=0.03) for p in cam[::7])
    assert cam[0][0] == pytest.approx(32, abs=1e-6)


def test_open_curve_offset_goes_left_and_too_large_offset_is_rejected():
    line = param_curves.sample({"u": "t", "v": "0", "t": [0, 10], "offset": 3}, ("u", "v"), {})
    assert all(p[1] == pytest.approx(3) for p in line)
    with pytest.raises(WorkflowError, match="曲率半径"):
        param_curves.sample({"u": "5*cos(t)", "v": "5*sin(t)", "t": [0, 2 * math.pi], "offset": -6}, ("u", "v"), {})


def test_fillet_reports_whether_the_edges_were_convex_or_concave():
    edge = {"origin": [10, 50, 0], "direction": [0, -1, 0]}
    convex = FakeV2(delta=-30.0).call("fillet", {"radius": 3, "edges": edge})
    assert convex["ok"] is True and convex["edge_kind"].startswith("convex")
    concave = FakeV2(delta=30.0).call("fillet", {"radius": 3, "edges": edge})
    assert concave["ok"] is True and concave["edge_kind"].startswith("concave")


class ExposedStartFake(FakeV2):
    """扫描之后在起点平面上多出一块朝 −轴向的平面，模拟接管起点截面露在壳体外。"""

    exposed = None

    def snapshot(self):
        data = super().snapshot()
        if self.exposed is not None and self.ran:
            center, normal = self.exposed
            data["faces"] = [*data.get("faces", []), {
                "index": 77, "surfaceType": "plane", "area": 1e-4,
                "center": {k: v / 1000 for k, v in zip("xyz", center)}, "normal": dict(zip("xyz", normal)),
            }]
        return data


def test_axis_cylinder_warns_when_the_nozzle_start_is_not_buried():
    args = {"origin": [0, -30, 40], "direction": [1, 0, 0], "from": 62.5, "to": 100, "diameter": 28}
    session = ExposedStartFake(delta=20000.0)
    session.exposed = ([62.5, -38, 48], [-1, 0, 0])
    result = session.call("axis_cylinder", args)
    assert result["ok"] is True, result
    assert any("face#77" in text for text in result.get("warnings", []))
    buried = ExposedStartFake(delta=20000.0).call("axis_cylinder", {**args, "from": 0})
    assert not buried.get("warnings")


def test_revolve_echoes_axial_position_and_radius_of_profile_vertices():
    session = FakeV2(delta=12000.0)
    result = session.call("revolve", {
        "plane": "top", "loops": [{"type": "polygon", "points": [[-40, 45], [-20, 45], [-19.65, 55], [-40, 55]]}],
        "axis_start": [-40, 0], "axis_end": [-40, 100],
    })
    assert result["ok"] is True, result
    assert result["profile_axial_radius"][0] == [[45, 0], [45, 20], [55, 20.35], [55, 0]]
    assert result["profile_radius_range_mm"] == [0, 20.35]
