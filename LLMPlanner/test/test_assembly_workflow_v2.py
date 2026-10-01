"""装配 workflow v2（机械配合、关节宏、自由度预算）的离线单测：用假 HTTP 层代替 SolidWorks。"""

from __future__ import annotations

import pytest

from planner.assembly_workflow import refs
from planner.assembly_workflow.session import AssemblySession
from planner.assembly_workflow.tools import TOOL_SPECS, TOOL_SPECS_V2, tool_names


EDGES = [
    {"index": 0, "curveType": "line", "start": [-0.06, 0, 0], "end": [0.06, 0, 0], "length": 0.12,
     "direction": [1, 0, 0]},
    {"index": 1, "curveType": "line", "start": [0, 0, 0], "end": [0, 0.01, 0], "length": 0.01,
     "direction": [0, 1, 0]},
    {"index": 2, "curveType": "circle", "center": [0, 0, 0], "axis": [0, 0, 1], "radius": 0.005},
    {"index": 3, "curveType": "circle", "center": [0.02, 0, 0], "axis": [0, 0, 1], "radius": 0.004},
]


def test_edge_and_sphere_selectors():
    assert refs.parse_selector("edge:7") == {"kind": "raw", "ref": "edge:7"}
    assert refs.parse_selector("edge#3")["ref"] == "edge:3"
    assert refs.parse_selector("component")["ref"] == "component"
    assert refs.resolve_on_edges("edge(line, longest)", EDGES) == "edge:0"
    assert refs.resolve_on_edges("edge(line, +Y)", EDGES) == "edge:1"
    assert refs.resolve_on_edges("edge(circle, d=8)", EDGES) == "edge:3"
    assert refs.resolve_on_edges("edge(circle, near=[0,0])", EDGES) == "edge:2"
    assert refs.needs_edges("edge(line)") and not refs.needs_edges("cyl(d=10)")
    with pytest.raises(refs.RefError):
        refs.resolve_on_edges("edge(line, +Z)", EDGES)
    faces = [{"index": 4, "surfaceType": "sphere", "radius": 0.008, "center": [0, 0, 0]}]
    assert refs.resolve_on_faces("sphere", faces) == "face:4"
    assert refs.resolve_on_faces("sphere(d=16)", faces) == "face:4"


def test_tool_sets_are_nested_and_v1_unchanged():
    v1 = {s["function"]["name"] for s in TOOL_SPECS}
    v2 = {s["function"]["name"] for s in TOOL_SPECS_V2}
    assert v1 < v2
    assert v2 - v1 == {"joint", "close_loop", "rack_pinion", "screw_pair", "linear_coupler", "check_dof"}
    v1_mate = next(s for s in TOOL_SPECS if s["function"]["name"] == "mate")
    assert "lock" not in v1_mate["function"]["parameters"]["properties"]["type"]["enum"]
    v2_mate = next(s for s in TOOL_SPECS_V2 if s["function"]["name"] == "mate")
    assert {"slot", "rack_pinion", "screw", "lock"} <= set(v2_mate["function"]["parameters"]["properties"]["type"]["enum"])
    assert tool_names("v1") == v1


class FakeSession(AssemblySession):
    """记录发给 C# 的 asmwf 调用，按组件返回预设的面/边/约束状态。"""

    def __init__(self, workflow: str = "v2"):
        super().__init__(workflow=workflow, allowed_tools=tool_names(workflow))
        self.has_assembly = True
        self.sent: list[tuple[str, dict]] = []
        self.components = {
            "base-1": {"isFixed": True, "status": 3},
            "shaft-1": {"isFixed": False, "status": 2},
            "gear-1": {"isFixed": False, "status": 2},
            "rack-1": {"isFixed": False, "status": 2},
            "nut-1": {"isFixed": False, "status": 2},
        }
        self.aliases = {name.split("-")[0]: name for name in self.components}
        self._count = 0
        self.created: dict[str, dict] = {}

    def asmwf(self, op: str, body: dict | None = None) -> dict:
        body = body or {}
        self.sent.append((op, body))
        if op == "components":
            return {"components": [
                {"name": n, "isFixed": c["isFixed"], "constrainedStatus": c["status"], "transform": [0] * 12}
                for n, c in self.components.items()
            ]}
        if op == "component-faces":
            return {"faces": [
                {"index": 0, "surfaceType": "cylinder", "radius": 0.005, "axisOrigin": [0, 0, 0], "axis": [0, 0, 1]},
                {"index": 1, "surfaceType": "plane", "area": 0.001, "normal": [0, 0, 1], "point": [0, 0, 0]},
            ]}
        if op == "component-edges":
            return {"edges": EDGES}
        if op == "mate":
            self._count += 1
            self.created[f"m{self._count}"] = body
            return {"name": f"m{self._count}", "type": body["type"]}
        if op == "delete-mate":
            self.created.pop(body["name"], None)
            return {"deleted": body["name"]}
        if op == "mates":
            # 和实测的 SolidWorks 一样：距离配合不翻转时 a 落在 b 面法向的负侧
            out = []
            for name, mate in self.created.items():
                d = float(mate.get("distance") or 0) * (1 if mate.get("flip") else -1)
                out.append({"name": name, "entities": [
                    {"refType": 3, "params": [0, 0, d, 0, 0, 1]}, {"refType": 3, "params": [0, 0, 0, 0, 0, 1]},
                ]})
            return {"mates": out}
        raise AssertionError(op)

    def mates_sent(self) -> list[dict]:
        return [body for op, body in self.sent if op == "mate"]


def test_joint_mapping_expansions():
    s = FakeSession()
    out = s.call("joint", {"type": "revolute", "a": "shaft:cyl(d=10)", "b": "base:cyl(d=10)"})
    assert out["ok"], out
    assert [m["type"] for m in s.mates_sent()] == ["concentric", "coincident"]
    s.sent.clear()
    out = s.call("joint", {"type": "fixed", "a": "gear:cyl(d=10)", "b": "shaft:cyl(d=10)", "offset": 15})
    sent = s.mates_sent()
    assert out["ok"] and sent[0]["type"] == "concentric" and sent[0]["lockRotation"] is True
    assert sent[1]["type"] == "distance" and sent[1]["distance"] == pytest.approx(0.015)
    # 落在负侧 → 撤销后翻转重建
    assert sent[2]["type"] == "distance" and sent[2]["flip"] is True
    assert "m4" not in s.created and out["mates"][1]["name"] == "m5"
    s.sent.clear()
    out = s.call("joint", {"type": "revolute", "a": "shaft:cyl(d=10)", "b": "base:cyl(d=10)", "offset": -26})
    assert out["ok"] and [m["type"] for m in s.mates_sent()] == ["concentric", "distance"]
    assert s.mates_sent()[1]["distance"] == pytest.approx(0.026) and "flip" not in s.mates_sent()[1]
    s.sent.clear()
    out = s.call("joint", {"type": "fixed", "a": "gear", "b": "shaft"})
    assert out["ok"] and s.mates_sent()[0]["type"] == "lock"
    assert s.mates_sent()[0]["a"]["ref"] == "component"
    s.sent.clear()
    out = s.call("joint", {
        "type": "slider", "a": "rack:face(+Z, largest)", "b": "base:face(+Z, largest)",
        "guide_a": "rack:plane(top)", "guide_b": "base:plane(top)",
        "travel_a": "rack:plane(right)", "travel_b": "base:plane(right)", "limit_min": 0, "limit_max": 30,
    })
    sent = s.mates_sent()
    assert out["ok"], out
    assert [m["type"] for m in sent] == ["coincident", "coincident", "distance"]
    assert sent[2]["distanceMax"] == pytest.approx(0.03) and sent[2]["distanceMin"] == 0


def test_slider_without_guide_rolls_back():
    s = FakeSession()
    out = s.call("joint", {"type": "slider", "a": "rack:face(+Z)", "b": "base:face(+Z)"})
    assert not out["ok"]
    assert [op for op, _ in s.sent if op in ("mate", "delete-mate")] == ["mate", "delete-mate"]
    assert s.joints == []


def test_couplings_and_dof_budget():
    s = FakeSession()
    s.call("joint", {"type": "revolute", "a": "shaft:cyl(d=10)", "b": "base:cyl(d=10)"})
    s.call("joint", {"type": "fixed", "a": "gear:cyl(d=10)", "b": "shaft:cyl(d=10)"})
    s.call("joint", {"type": "slider", "a": "rack:face(+Z)", "b": "base:face(+Z)",
                     "guide_a": "rack:plane(top)", "guide_b": "base:plane(top)"})
    dof = s.call("check_dof", {"expected_mobility": 1})
    # base、shaft+gear、rack、nut 四个构件；nut 悬空
    assert dof["bodies"] == 4
    assert dof["floating"] == ["nut-1"]
    out = s.call("rack_pinion", {"rack": "rack", "pinion": "gear", "module": 3, "teeth": 12})
    assert out["ok"], out
    sent = s.mates_sent()[-1]
    assert sent["type"] == "rackpinion" and sent["pitchDiameter"] == pytest.approx(0.036)
    assert sent["a"]["ref"] == "edge:0"
    s.components.pop("nut-1")
    s.aliases.pop("nut")
    dof = s.call("check_dof", {"expected_mobility": 1})
    assert dof["mobility_planar"] == 1 and dof["matches_expected"], dof
    assert dof["couplings"] == ["rack_pinion"]


def test_coupling_refuses_locked_end():
    s = FakeSession()
    s.components["rack-1"]["status"] = 3
    out = s.call("rack_pinion", {"rack": "rack", "pinion": "gear", "pitch_diameter": 36})
    assert not out["ok"] and out["error"]["code"] == "FEATURE_NO_EFFECT"


def test_screw_pair_matches_diameters_and_close_loop_falls_back():
    s = FakeSession()
    out = s.call("screw_pair", {"screw": "shaft", "nut": "nut", "lead": 2})
    assert out["ok"], out
    assert s.mates_sent()[-1]["lead"] == pytest.approx(0.002)
    s.call("joint", {"type": "revolute", "a": "shaft:cyl(d=10)", "b": "base:cyl(d=10)"})
    s.call("joint", {"type": "revolute", "a": "gear:cyl(d=10)", "b": "shaft:cyl(d=10)"})
    s.sent.clear()
    out = s.call("close_loop", {"type": "revolute", "a": "gear:cyl(d=10)", "b": "base:cyl(d=10)"})
    assert out["ok"] and out["minimal"] is True
    assert [m["type"] for m in s.mates_sent()] == ["concentric"]
    assert s.joints[-1]["loop"] is True


def test_v1_session_keeps_gear_key_and_rejects_v2_tools():
    s = FakeSession(workflow="v1")
    out = s.call("mount_on_shaft", {"part": "gear", "shaft": "shaft", "lock_rotation": True})
    assert out["ok"], out
    assert [m["type"] for m in s.mates_sent()] == ["concentric", "coincident", "gear"]
    assert "lockRotation" not in s.mates_sent()[0]
    assert not s.call("joint", {"type": "fixed", "a": "gear", "b": "shaft"})["ok"]
    v2 = FakeSession(workflow="v2")
    v2.call("mount_on_shaft", {"part": "gear", "shaft": "shaft", "lock_rotation": True})
    assert [m["type"] for m in v2.mates_sent()] == ["concentric", "coincident"]
    assert v2.mates_sent()[0]["lockRotation"] is True
