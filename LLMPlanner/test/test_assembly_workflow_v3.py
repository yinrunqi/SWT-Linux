"""装配 workflow v3 的离线单测：假 HTTP 层按零件位姿给出装配坐标下的配合实体，秩法诊断能真实运行。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from planner.assembly_workflow.agent import STATE_PLACEHOLDER, AssemblyAgent
from planner.assembly_workflow.session import AssemblySession
from planner.assembly_workflow.tools import tool_names, tool_specs

MATE_TYPES = {"coincident": 0, "concentric": 1, "distance": 5, "lock": 16, "screw": 17}


def _cyl(index, x, y, d=0.006):
    return {"index": index, "surfaceType": "cylinder", "radius": d / 2, "axisOrigin": [x, y, 0], "axis": [0, 0, 1]}


def _plane(index):
    return {"index": index, "surfaceType": "plane", "area": 0.002, "normal": [0, 0, 1], "point": [0, 0, 0]}


class GeomSession(AssemblySession):
    """零件只平移（无旋转）；面是零件局部几何，配合实体换算到装配坐标。"""

    def __init__(self, parts: dict[str, dict]):
        super().__init__(workflow="v3", allowed_tools=tool_names("v3"))
        self.has_assembly = True
        self.parts = parts
        self.aliases = {name.split("-")[0]: name for name in parts}
        self.created: dict[str, dict] = {}
        self.sent: list[tuple[str, dict]] = []
        self._count = 0

    def _entity(self, end: dict) -> dict:
        part = self.parts[end["component"]]
        pos = np.array(part["pos"], float)
        ref = end["ref"]
        if ref.startswith("plane:"):
            return {"component": end["component"], "refType": 3, "params": [*pos, 0, 0, 1, 0, 0]}
        face = next(f for f in part["faces"] if f["index"] == int(ref.split(":")[1]))
        if face["surfaceType"] == "cylinder":
            origin = pos + np.array(face["axisOrigin"], float)
            return {"component": end["component"], "refType": 4, "params": [*origin, *face["axis"], face["radius"], 0]}
        return {"component": end["component"], "refType": 3,
                "params": [*(pos + np.array(face["point"], float)), *face["normal"], 0, 0]}

    def asmwf(self, op: str, body: dict | None = None) -> dict:
        body = body or {}
        self.sent.append((op, body))
        if op == "components":
            return {"components": [
                {"name": n, "isFixed": p.get("fixed", False), "constrainedStatus": 3 if p.get("fixed") else 2,
                 "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, *p["pos"], 1, 0, 0, 0]}
                for n, p in self.parts.items()
            ]}
        if op == "component-faces":
            return {"faces": self.parts[body["component"]]["faces"]}
        if op == "mate":
            self._count += 1
            self.created[f"m{self._count}"] = body
            return {"name": f"m{self._count}", "type": body["type"]}
        if op == "delete-mate":
            self.created.pop(body["name"], None)
            return {"deleted": body["name"]}
        if op == "mates":
            out = []
            for name, mate in self.created.items():
                ents = [self._entity(mate["a"]), self._entity(mate["b"])]
                out.append({
                    "name": name, "mateType": MATE_TYPES[mate["type"]], "suppressed": False, "errorCode": 0,
                    "components": [mate["a"]["component"], mate["b"]["component"]], "entities": ents,
                    **({"lockRotation": True} if mate.get("lockRotation") else {}),
                    **({"revolutionValue": mate["lead"]} if "lead" in mate else {}),
                })
            return {"mates": out}
        raise AssertionError(op)

    def mates_sent(self) -> list[dict]:
        return [body for op, body in self.sent if op == "mate"]


def fourbar() -> GeomSession:
    """平行四杆：机架孔 (0,0)/(80,0)，曲柄竖直 20 mm，连杆水平 80 mm；层位 0/8/14/8 mm。"""
    holes = {
        "frame-1": ((0, 0, 0), [(0, 0), (0.08, 0)], True),
        "crank-1": ((0, 0, 0.008), [(0, 0), (0, 0.02)], False),
        "coupler-1": ((0, 0.02, 0.014), [(0, 0), (0.08, 0)], False),
        "rocker-1": ((0.08, 0, 0.008), [(0, 0), (0, 0.02)], False),
    }
    return GeomSession({
        name: {"pos": pos, "fixed": fixed, "faces": [_cyl(i, x, y) for i, (x, y) in enumerate(xy)] + [_plane(9)]}
        for name, (pos, xy, fixed) in holes.items()
    })


def test_v3_toolset_adds_check_pose():
    names = {s["function"]["name"] for s in tool_specs("v3")}
    assert names - tool_names("v2") == {"check_pose", "view_assembly"}
    assert tool_names("v3", view_tool=False) - tool_names("v2") == {"check_pose"}
    joint = next(s for s in tool_specs("v3") if s["function"]["name"] == "joint")
    assert "at_z" in joint["function"]["parameters"]["properties"]


def test_check_motion_deletes_study_after_sampling_failure():
    class FailingMotionSession(AssemblySession):
        def __init__(self):
            super().__init__(workflow="v3", allowed_tools=tool_names("v3"))
            self.aliases = {"driver": "driver-1"}
            self.operations = []

        def asmwf(self, op: str, body: dict | None = None) -> dict:
            self.operations.append(op)
            if op == "motion-drive":
                return {}
            if op == "motion-sample":
                raise RuntimeError("sample failed")
            if op == "motion-delete":
                return {"deleted": ["Motion Study 1"]}
            raise AssertionError(op)

    session = FailingMotionSession()
    with pytest.raises(RuntimeError, match="sample failed"):
        session.check_motion({"driver": "driver"})
    assert session.operations == ["motion-drive", "motion-sample", "motion-delete"]


def test_joint_skips_axial_mate_already_fixed_by_loop():
    s = fourbar()
    out = s.call("joint", {"type": "revolute", "a": "crank:cyl(near=[0,0])", "b": "frame:cyl(near=[0,0])", "at_z": 8})
    assert out["ok"], out
    assert [m["type"] for m in s.mates_sent()] == ["concentric", "distance"]
    assert s.mates_sent()[1]["distance"] == pytest.approx(0.008) and "flip" not in s.mates_sent()[1]
    # 曲柄 1 + 两个悬空件各 6
    assert out["after"]["mobility"] == 13 and out["after"]["floating"] == ["coupler", "rocker"]
    s.call("joint", {"type": "revolute", "a": "coupler:cyl(near=[0,0])", "b": "crank:cyl(near=[0,20])", "at_z": 14})
    s.call("joint", {"type": "revolute", "a": "rocker:cyl(near=[0,0])", "b": "frame:cyl(near=[80,0])", "at_z": 8})
    s.sent.clear()
    out = s.call("joint", {"type": "revolute", "a": "coupler:cyl(near=[80,0])", "b": "rocker:cyl(near=[0,20])"})
    assert out["ok"], out
    assert [m["type"] for m in s.mates_sent()] == ["concentric"]
    assert "note" in out and s.joints[-1]["loop"] is True
    dof = s.call("check_dof", {"expected_mobility": 1})
    assert dof["mobility"] == 1 and dof["redundant"] == 2 and dof["matches_expected"], dof
    assert dof["free"]["crank"] == {"dof": 1, "rotations": 1, "translations": 0}
    # 平行四杆的连杆只平动
    assert dof["free"]["coupler"] == {"dof": 1, "rotations": 0, "translations": 1}


def test_mate_distance_flip_and_side():
    s = fourbar()
    out = s.call("mate", {"type": "distance", "a": "crank:plane(front)", "b": "frame:plane(front)",
                          "distance": 8, "flip": True})
    assert out["ok"], out
    assert s.mates_sent()[-1]["flip"] is True and s.mates_sent()[-1]["distance"] == pytest.approx(0.008)
    assert out["side_mm"] == pytest.approx(8.0)

    from planner.workflow.errors import WorkflowError

    def over_defined(op, body=None):
        if op == "mate":
            raise WorkflowError("FEATURE_FAILED", "distance 配合失败: errStatus=5（装配过定义（与已有配合冲突））")
        return GeomSession.asmwf(s, op, body)

    s.asmwf = over_defined
    out = s.call("mate", {"type": "distance", "a": "rocker:plane(front)", "b": "frame:plane(front)", "distance": 8})
    assert not out["ok"] and "加上 flip=true" in out["error"]["message"]


def test_face_cache_and_ref_error_candidates():
    s = fourbar()
    s.call("joint", {"type": "revolute", "a": "crank:cyl(near=[0,0])", "b": "frame:cyl(near=[0,0])"})
    s.call("joint", {"type": "revolute", "a": "rocker:cyl(near=[0,0])", "b": "frame:cyl(near=[80,0])"})
    fetched = [b["component"] for op, b in s.sent if op == "component-faces"]
    assert fetched.count("frame-1") == 1
    out = s.call("joint", {"type": "revolute", "a": "crank:cyl(d=30)", "b": "frame:cyl(near=[0,0])"})
    assert not out["ok"]
    assert "face:0" in out["error"]["message"] and "near=[x,y]" in out["error"]["message"]


def test_screw_pair_reports_lead_from_null_space():
    s = GeomSession({
        "housing-1": {"pos": (0, 0, 0), "fixed": True, "faces": [_cyl(0, 0, 0, 0.01), _cyl(1, 0.03, 0, 0.004), _plane(9)]},
        "screw-1": {"pos": (0, 0, 0), "faces": [_cyl(0, 0, 0, 0.01), _plane(9)]},
        "nut-1": {"pos": (0, 0, 0.02), "faces": [_cyl(0, 0, 0, 0.01), _cyl(1, 0.03, 0, 0.004), _plane(9)]},
    })
    s.call("joint", {"type": "revolute", "a": "screw:cyl(d=10)", "b": "housing:cyl(d=10)"})
    assert s.call("mate", {"type": "concentric", "a": "nut:cyl(d=4)", "b": "housing:cyl(d=4)"})["ok"]
    out = s.call("screw_pair", {"screw": "screw", "nut": "nut", "screw_ref": "screw:cyl(d=10)",
                                "nut_ref": "nut:cyl(d=10)", "lead": 2})
    assert out["ok"], out
    assert out["verified_lead_mm"] == pytest.approx(2.0, rel=1e-3) and "verify" not in out


def _response(content="", calls=(), finish="tool_calls"):
    tool_calls = [SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=n, arguments=a))
                  for i, (n, a) in enumerate(calls)]
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=None)


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append({"max_tokens": kwargs["max_tokens"],
                              "messages": json.loads(json.dumps(kwargs["messages"], default=str))})
        return self.responses.pop(0)


def test_agent_retries_truncation_prunes_state_and_reports_budget():
    s = fourbar()
    client = ScriptedClient([
        _response("长篇推理", [("get_assembly_state", '{"compo')], finish="length"),
        _response(calls=[("get_assembly_state", "{}")]),
        _response(calls=[("get_assembly_state", "{}")]),
        _response("done"),
        _response("done"),
        _response("done"),
    ])
    agent = AssemblyAgent(client, "fake", s, max_llm_calls=6, max_tokens=1000, tools=tool_specs("v3"))
    result = agent.run("装配四杆")
    assert result["truncations"] == 1 and result["status"] == "stalled"
    assert [r["max_tokens"] for r in client.requests[:3]] == [1000, 2000, 1000]
    assert client.requests[1]["messages"][-1]["role"] == "user"
    last = client.requests[3]["messages"]
    states = [m for m in last if m["role"] == "tool"]
    assert states[0]["content"] == STATE_PLACEHOLDER and states[1]["content"] != STATE_PLACEHOLDER
    budget = json.loads(states[1]["content"])["budget"]
    assert budget["remaining_llm_calls"] == 3 and "warning" in budget


def test_agent_replaces_invalid_json_args_in_history():
    client = ScriptedClient([
        _response(calls=[("get_assembly_state", '{"compact": tru e}')]),
        _response("done"),
        _response("done"),
        _response("done"),
    ])
    agent = AssemblyAgent(client, "fake", fourbar(), max_llm_calls=4, max_tokens=1000, tools=tool_specs("v3"))
    agent.run("装配四杆")
    history = client.requests[1]["messages"]
    sent = [c["function"]["arguments"] for m in history for c in m.get("tool_calls") or []]
    assert sent == ["{}"]
    error = json.loads(next(m for m in history if m["role"] == "tool")["content"])["error"]
    assert error["code"] == "TOOL_ARGS" and "tru e" in error["message"]
