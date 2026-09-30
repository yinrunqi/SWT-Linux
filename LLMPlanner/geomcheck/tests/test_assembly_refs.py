"""装配语义引用与运动分析的离线单测（不启动 SolidWorks）。"""

from __future__ import annotations

import math

import pytest

from geomcheck.assembly_motion import analyze_frames, unwrap
from geomcheck.tests._import_src import load_asm_refs, load_gears

_refs = load_asm_refs()
split_ref, parse_selector, resolve_on_faces = _refs.split_ref, _refs.parse_selector, _refs.resolve_on_faces
generate_involute_outline = load_gears().generate_involute_outline


def test_split_and_parse():
    assert split_ref("shaft1:cyl(d=10)") == ("shaft1", "cyl(d=10)")
    parsed = parse_selector("cyl(d=10, near=[0, 5])")
    assert parsed["kind"] == "cyl"
    assert parsed["d"] == 10
    assert parsed["near"] == [0, 5]
    assert parse_selector("plane(front)")["ref"] == "plane:front"
    assert parse_selector("face#3")["ref"] == "face:3"
    assert parse_selector("bore")["ref"] == "bore:auto"


def test_pick_cylinder_and_plane():
    faces = [
        {"index": 0, "surfaceType": "cylinder", "radius": 0.005, "axisOrigin": [0, 0, 0], "axis": [0, 0, 1]},
        {"index": 1, "surfaceType": "cylinder", "radius": 0.0052, "axisOrigin": [0.054, 0, 0], "axis": [0, 0, 1]},
        {"index": 2, "surfaceType": "plane", "area": 0.01, "normal": [0, 0, 1], "point": [0, 0, 0]},
        {"index": 3, "surfaceType": "plane", "area": 0.002, "normal": [0, 1, 0], "point": [0, 0.04, 0]},
    ]
    assert resolve_on_faces("cyl(d=10)", faces) == "face:0"
    assert resolve_on_faces("cyl(d=10.4, near=[54,0])", faces) == "face:1"
    assert resolve_on_faces("face(+Y, largest)", faces) == "face:3"
    assert resolve_on_faces("front", faces) == "plane:front"


def test_involute_12_teeth():
    pts, params = generate_involute_outline(12, module_m=0.003, pressure_angle_deg=30)
    assert params["outerRadius"] == pytest.approx(0.021)
    assert params["pitchRadius"] == pytest.approx(0.018)
    assert math.dist(pts[0], pts[-1]) == pytest.approx(0, abs=1e-9)
    assert len(pts) > 80


def _matrix(angle_deg: float, x=0.0, y=0.0, z=0.0) -> list[float]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [c, -s, 0, s, c, 0, 0, 0, 1, x, y, z]


def test_analyze_gear_ratio():
    frames = []
    for i in range(13):
        t = i / 12
        frames.append({
            "transforms": {
                "in-1": _matrix(360 * t),
                "out-1": _matrix(60 * t),
                "base-1": _matrix(0),
            },
            "interference": {"interferenceCount": 0, "totalVolume": 0},
        })
    spec = {
        "driver": "input",
        "expect_ratio": 6,
        "expect_same_direction": True,
        "expect_moving": ["input", "output"],
        "expect_fixed": ["housing"],
        "max_interference": 0,
    }
    aliases = {"input": "in-1", "output": "out-1", "housing": "base-1"}
    report = analyze_frames(frames, spec, aliases)
    assert report["passed"]
    assert report["kinematics"]["output_ratio"] == pytest.approx(1 / 6, abs=0.01)


def test_unwrap():
    assert unwrap([350, 10, 30])[1] == pytest.approx(370)
