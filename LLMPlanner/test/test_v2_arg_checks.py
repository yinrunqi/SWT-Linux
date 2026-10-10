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


class AxisFake(FakeV2):
    """在快照里放一个孔底平面（中心 bottom_mm，法向 bottom_normal），模拟扫描切除留下的孔底。"""

    bottom_mm = None
    bottom_normal = None

    def snapshot(self):
        data = super().snapshot()
        if self.bottom_mm is not None:
            data["faces"] = [*data.get("faces", []), {
                "index": 999, "surfaceType": "plane", "area": 1e-4,
                "center": {k: v / 1000 for k, v in zip("xyz", self.bottom_mm)},
                "normal": dict(zip("xyz", self.bottom_normal)),
            }]
        return data


def test_axis_cylinder_radial_nozzle_from_latitude_and_longitude():
    session = AxisFake(delta=3000.0)
    result = session.call("axis_cylinder", {
        "origin": [0, 0, 0], "direction": {"lat_deg": 40, "lon_deg": 30}, "from": 60, "to": 110,
        "diameter": 30, "inner_diameter": 20,
    })
    assert result["ok"] is True, result
    unit = [math.cos(math.radians(40)) * math.cos(math.radians(30)),
            math.cos(math.radians(40)) * math.sin(math.radians(30)), math.sin(math.radians(40))]
    path = session.ran[0][0]["params"]["points"]
    assert path[0] == pytest.approx([0.06 * u for u in unit], abs=1e-8)
    assert path[1] == pytest.approx([0.11 * u for u in unit], abs=1e-8)
    assert result["axis"]["lat_deg"] == pytest.approx(40) and result["axis"]["lon_deg"] == pytest.approx(30)


def test_axis_cylinder_inclined_drill_to_a_z_level_with_a_point():
    d = [math.tan(math.radians(20)), -math.tan(math.radians(15)), -1]
    norm = math.sqrt(sum(v * v for v in d))
    end = [40 + 52 * d[0], 55 + 52 * d[1], 8]
    session = AxisFake(delta=-120.0)
    session.bottom_mm, session.bottom_normal = end, [-v / norm for v in d]
    result = session.call("axis_cylinder", {
        "origin": [40, 55, 60], "direction": ["=tand(20)", "=-tand(15)", -1], "from": -12, "to": {"z": 8},
        "diameter": 12, "cut": True, "tip_angle": 118,
    })
    assert result["ok"] is True, result
    assert result["axis"]["end"] == pytest.approx(end, abs=1e-5)
    assert session.ran[-1][0]["params"]["faceIndex"] == 999
    cone = session.ran[-1][-1]
    assert cone["params"]["draftAngleDeg"] == pytest.approx(59)
    assert "118" in result["drill_point"]
    no_floor = AxisFake(delta=-120.0).call("axis_cylinder", {
        "origin": [40, 55, 60], "direction": d, "from": -12, "to": {"z": 8}, "diameter": 12, "cut": True,
        "tip_angle": 118,
    })
    assert no_floor["ok"] is True and any("没有生成钻尖" in note for note in no_floor["notes"])


def test_axis_cylinder_rejects_an_empty_range():
    result = AxisFake(delta=-10.0).call("axis_cylinder", {
        "origin": [0, 0, 0], "direction": [0, 0, 1], "from": 10, "to": {"z": 5}, "diameter": 4, "cut": True,
    })
    assert result["ok"] is False and "to 必须大于 from" in result["error"]["message"]


def test_shell_echoes_open_faces_and_warns_about_top_bottom():
    session = FakeV2(delta=-500.0)
    result = session.call("shell", {"thickness": 2, "open_faces": ["bottom"]})
    assert result["ok"] is True, result
    assert any("法向 -Y" in text for text in result["open_faces"])
    assert any("face(+Z)" in text for text in result["warnings"])
    plain = FakeV2(delta=-500.0).call("shell", {"thickness": 2, "open_faces": ["face(+Z)"]})
    assert plain["ok"] is True and not plain.get("warnings")
