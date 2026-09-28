"""planner.workflow 纯 Python 模块的单测（不需要 SolidWorks）。"""

from __future__ import annotations

import math

import pytest

from planner.workflow import ParamTable, RefRegistry, WorkflowError, check_profile, check_within, resolve_ref


# ---------------------------------------------------------------- params

def test_param_table_resolves_dependencies_and_units():
    table = ParamTable.from_dict({
        "hole_pitch": "L - 2 * margin",
        "L": 120,
        "margin": {"expr": "wall * 4", "unit": "mm"},
        "wall": 3,
        "plate": {"expr": "0.8", "unit": "cm"},
        "angle": {"expr": "atand(10 / 10)", "unit": "deg"},
    })
    values = table.resolve()
    assert values["hole_pitch"] == pytest.approx(96.0)
    assert values["plate"] == pytest.approx(8.0)
    assert values["angle"] == pytest.approx(45.0)
    assert table.value("hole_pitch / 2", unit="m") == pytest.approx(0.048)


def test_param_substitute_converts_only_length_keys_to_meters():
    table = ParamTable.from_dict({"W": 40, "n": {"expr": 6, "unit": "count"}})
    params = table.substitute(
        {"depth": "=W / 4", "count": "=n", "flip": False},
        length_keys={"depth"},
    )
    assert params == {"depth": pytest.approx(0.01), "count": 6.0, "flip": False}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"a": "b + 1", "b": "a * 2"}, "PARAM_CYCLE"),
        ({"a": "missing + 1"}, "PARAM_UNDEFINED"),
        ({"a": "__import__('os')"}, "PARAM_FORBIDDEN"),
        ({"a": "(1 +"}, "PARAM_SYNTAX"),
        ({"a": "x.y"}, "PARAM_FORBIDDEN"),
    ],
)
def test_param_errors_have_codes(data, code):
    with pytest.raises(WorkflowError) as info:
        ParamTable.from_dict(data).resolve()
    assert info.value.code == code


def test_param_division_by_zero_is_domain_error():
    with pytest.raises(WorkflowError) as info:
        ParamTable.from_dict({"a": 0, "b": "10 / a"}).resolve()
    assert info.value.code == "PARAM_DOMAIN"


# ---------------------------------------------------------------- refs

def _plane(index, center, normal, area):
    return {
        "index": index,
        "surfaceType": "plane",
        "center": dict(zip("xyz", center)),
        "normal": dict(zip("xyz", normal)),
        "area": area,
    }


# 40x20x5mm 底板（Y 向上，厚度沿 Y），顶面中央有 10x10x6mm 凸台。单位米。
BASE_FACES = [
    _plane(0, (0, 0.005, 0), (0, 1, 0), 0.0008 - 0.0001),   # 底板顶面（去掉凸台脚印）
    _plane(1, (0, 0.0, 0), (0, -1, 0), 0.0008),            # 底面
    _plane(2, (0.02, 0.0025, 0), (1, 0, 0), 0.0001),       # 右侧
    _plane(3, (-0.02, 0.0025, 0), (-1, 0, 0), 0.0001),     # 左侧
    _plane(4, (0, 0.0025, 0.01), (0, 0, 1), 0.0002),       # 前
    _plane(5, (0, 0.0025, -0.01), (0, 0, -1), 0.0002),     # 后
    _plane(6, (0, 0.011, 0), (0, 1, 0), 0.0001),           # 凸台顶面
    {"index": 7, "surfaceType": "cylinder", "center": {"x": 0, "y": 0.008, "z": 0},
     "normal": {"x": 0, "y": 0, "z": 0}, "area": 0.0001},
]


def test_side_refs_pick_outermost_face_and_ignore_normal_sign():
    assert resolve_ref("top", BASE_FACES).face_index == 6
    assert resolve_ref("base.bottom", BASE_FACES).face_index == 1
    assert resolve_ref("right", BASE_FACES).face_index == 2
    assert resolve_ref("left", BASE_FACES).face_index == 3
    assert resolve_ref("front", BASE_FACES).face_index == 4


def test_face_call_selectors():
    assert resolve_ref("face(+Y, largest)", BASE_FACES).face_index == 0
    assert resolve_ref("face(+Y, innermost)", BASE_FACES).face_index == 0
    assert resolve_ref("face(+Y)", BASE_FACES).face_index == 6
    assert resolve_ref("face#5", BASE_FACES).face_index == 5
    op = resolve_ref("top", BASE_FACES).to_operation()
    assert op["operation"] == "select_observed_face_and_sketch"
    assert op["params"] == {"faceIndex": 6}


def test_ref_errors():
    with pytest.raises(WorkflowError) as info:
        resolve_ref("diagonal", BASE_FACES)
    assert info.value.code == "REF_SYNTAX"
    with pytest.raises(WorkflowError) as info:
        resolve_ref("face#99", BASE_FACES)
    assert info.value.code == "REF_NOT_FOUND"
    twins = [_plane(0, (-0.01, 0.01, 0), (0, 1, 0), 0.0001), _plane(1, (0.01, 0.01, 0), (0, 1, 0), 0.0001),
             _plane(2, (0, 0, 0), (0, -1, 0), 0.0004)]
    with pytest.raises(WorkflowError) as info:
        resolve_ref("top", twins)
    assert info.value.code == "REF_AMBIGUOUS"
    assert sorted(info.value.context["candidates"]) == [0, 1]


def test_named_ref_survives_reindexing_after_rebuild():
    registry = RefRegistry()
    registry.bind_ref("boss.top", "top", BASE_FACES)
    rebuilt = list(reversed([dict(face, index=10 + i) for i, face in enumerate(BASE_FACES)]))
    resolved = resolve_ref("boss.top", rebuilt, registry)
    assert resolved.face["center"]["y"] == pytest.approx(0.011)
    assert resolved.face_index != 6
    with pytest.raises(WorkflowError) as info:
        resolve_ref("H9", BASE_FACES, registry)
    assert info.value.code == "REF_UNBOUND"


# ---------------------------------------------------------------- sketch_check

def test_plate_with_holes_passes_and_nesting_is_inferred():
    result = check_profile([
        {"type": "rect", "x1": -50, "y1": -30, "x2": 50, "y2": 30},
        {"type": "circle", "cx": -35, "cy": 0, "r": 4},
        {"type": "circle", "cx": 35, "cy": 0, "r": 4},
        {"type": "regular_polygon", "cx": 0, "cy": 0, "r": 8, "sides": 6},
    ], min_wall_mm=2)
    assert result.ok, result.to_dict()
    assert result.outer_loops == [0]
    assert set(result.inner_loops) == {1, 2, 3}
    hexagon = 1.5 * math.sqrt(3) * 8 ** 2
    expected = 100 * 60 - 2 * math.pi * 16 - hexagon
    assert result.region_area_mm2 == pytest.approx(expected, rel=1e-3)


@pytest.mark.parametrize(
    ("loops", "code"),
    [
        ([], "SKETCH_EMPTY"),
        ([{"type": "polyline", "points": [[0, 0], [10, 0], [10, 10]]}], "SKETCH_OPEN_LOOP"),
        ([{"type": "polygon", "points": [[0, 0], [10, 10], [10, 0], [0, 10]]}], "SKETCH_SELF_INTERSECT"),
        ([{"type": "circle", "cx": 0, "cy": 0, "r": 0}], "SKETCH_DEGENERATE"),
        ([{"type": "rect", "x1": 0, "y1": 0, "x2": 10}], "SKETCH_DEGENERATE"),
        ([{"type": "rect", "x1": 0, "y1": 0, "x2": 10, "y2": 10},
          {"type": "rect", "x1": 5, "y1": 5, "x2": 15, "y2": 15}], "SKETCH_LOOPS_OVERLAP"),
    ],
)
def test_sketch_error_codes(loops, code):
    assert code in check_profile(loops).codes


def test_thin_wall_detected_for_hole_near_edge_and_between_holes():
    result = check_profile([
        {"type": "rect", "x1": 0, "y1": 0, "x2": 40, "y2": 20},
        {"type": "circle", "cx": 5, "cy": 10, "r": 4.5},
        {"type": "circle", "cx": 15, "cy": 10, "r": 4.5},
    ], min_wall_mm=1.5)
    messages = [issue.message for issue in result.issues if issue.code == "SKETCH_THIN_WALL"]
    assert len(messages) == 2


def test_closed_polyline_with_repeated_start_point_is_accepted():
    result = check_profile([{"type": "polyline", "points": [[0, 0], [10, 0], [10, 5], [0, 0]]}])
    assert result.ok


def test_check_within_face_boundary():
    face = {"type": "rect", "x1": -20, "y1": -10, "x2": 20, "y2": 10}
    inside = check_within(face, [{"type": "circle", "cx": 0, "cy": 0, "r": 3}], min_margin_mm=1)
    assert inside.ok
    outside = check_within(face, [{"type": "circle", "cx": 19, "cy": 0, "r": 3}])
    assert outside.codes == ["SKETCH_OUTSIDE_BOUNDARY"]
    tight = check_within(face, [{"type": "slot", "x1": -10, "y1": 8, "x2": 10, "y2": 8, "width": 3}],
                         min_margin_mm=1)
    assert tight.codes == ["SKETCH_THIN_WALL"]
