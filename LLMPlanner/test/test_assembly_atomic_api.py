"""装配 L0 Atomic API 的离线契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from planner.assembly_workflow.atomic import (
    ASSEMBLY_ATOMIC_API_VERSION,
    AssemblyAtomicExecutor,
    AssemblyAtomicPlanner,
    atomic_api_metadata,
)
from planner.assembly_workflow.agent import AssemblyAgent
from planner.assembly_workflow.session import AssemblySession
from planner.assembly_workflow.tools import (
    ATOMIC_MUTATOR_NAMES,
    atomic_tool_names,
    atomic_tool_specs,
)
from planner.schemas import (
    ASSEMBLY_ATOMIC_PARAM_SCHEMAS,
    ASSEMBLY_ATOMIC_TOOLS,
    PART_ATOMIC_ENDPOINTS,
    AssemblyAtomicOperation,
    AssemblyAtomicPlan,
)

ROOT = Path(__file__).resolve().parent.parent


def test_assembly_atomic_catalog_is_complete_and_isolated_from_part_l0():
    assert set(ASSEMBLY_ATOMIC_TOOLS) == set(ASSEMBLY_ATOMIC_PARAM_SCHEMAS)
    assert set(ASSEMBLY_ATOMIC_TOOLS.values()) == {
        "insert_component",
        "set_component_pose",
        "ground_component",
        "unfix_component",
        "mate",
        "delete_mate",
        "save_assembly",
    }
    assert not {
        "joint", "close_loop", "mount_on_shaft", "mesh_gears", "crank_slider",
    } & set(ASSEMBLY_ATOMIC_TOOLS.values())
    assert all(not path.startswith("/api/assembly/") for _, path in PART_ATOMIC_ENDPOINTS.values())
    assert atomic_api_metadata()["version"] == ASSEMBLY_ATOMIC_API_VERSION


def test_multi_turn_atomic_tools_are_exact_and_exclude_workflow_macros():
    expected = {
        "insert_component", "set_component_pose", "ground_component", "unfix_component",
        "mate", "delete_mate", "save_assembly", "get_assembly_state", "view_assembly",
        "check_interference", "check_dof", "check_motion", "check_pose", "finish",
    }
    assert atomic_tool_names() == expected
    assert atomic_tool_names(view_tool=False) == expected - {"view_assembly"}
    assert {spec["function"]["name"] for spec in atomic_tool_specs()} == expected
    assert ATOMIC_MUTATOR_NAMES == {
        "insert_component", "set_component_pose", "ground_component", "unfix_component",
        "mate", "delete_mate", "save_assembly",
    }
    assert not {
        "joint", "close_loop", "mount_on_shaft", "revolute_joint", "slider_joint",
        "mesh_gears", "crank_slider", "rack_pinion", "screw_pair", "linear_coupler",
    } & expected


def test_multi_turn_atomic_session_rejects_macro_and_invalidates_checks():
    session = AssemblySession(workflow="v3", allowed_tools=atomic_tool_names(view_tool=False))
    assert session.call("joint", {})["error"]["code"] == "TOOL_ARGS"

    session.has_assembly = True
    session.last_motion = session.last_pose = session.last_dof = {"old": True}
    session._handlers["set_component_pose"] = lambda args: {}
    session._delta = lambda: {}
    assert session.call("set_component_pose", {"component": "a", "at": [0, 0, 0]})["ok"]
    assert session.last_motion is None and session.last_pose is None and session.last_dof is None


def test_agent_checkpoint_isolated_by_agent_kind_and_old_workflow_is_compatible(tmp_path):
    checkpoint = tmp_path / "agent.json"
    old = {"goal": "g", "model": "m"}
    checkpoint.write_text(json.dumps(old), encoding="utf-8")
    workflow = AssemblyAgent(None, "m", SimpleNamespace(), checkpoint_path=checkpoint)
    assert workflow._load_checkpoint("g") == old

    atomic_mt = AssemblyAgent(
        None, "m", SimpleNamespace(), checkpoint_path=checkpoint,
        tools=atomic_tool_specs(view_tool=False), view_tool=False, agent_kind="atomic_mt",
    )
    with pytest.raises(ValueError, match="workflow agent"):
        atomic_mt._load_checkpoint("g")

    checkpoint.write_text(json.dumps({
        "goal": "g", "model": "m", "agent_kind": "atomic_mt",
        "tool_names": [spec["function"]["name"] for spec in atomic_mt.tools],
    }), encoding="utf-8")
    assert atomic_mt._load_checkpoint("g")["agent_kind"] == "atomic_mt"


@pytest.mark.parametrize(
    ("operation", "params"),
    [
        ("insert_component", {"part": r"C:\tmp\a.SLDPRT", "name": "a"}),
        ("set_component_pose", {"component": "a", "at": [1, 2, 3]}),
        ("ground_component", {"component": "frame"}),
        ("unfix_component", {"component": "frame"}),
        ("mate", {"type": "concentric", "a": "a:cyl(d=10)", "b": "b:cyl(d=10)"}),
        ("delete_mate", {"name": "Concentric1"}),
        ("save_assembly", {}),
    ],
)
def test_assembly_atomic_operations_accept_minimal_params(operation, params):
    item = AssemblyAtomicOperation(operation=operation, params=params)
    assert item.operation.value == operation


@pytest.mark.parametrize(
    ("operation", "params"),
    [
        ("insert_component", {"part": "a.SLDPRT"}),
        ("set_component_pose", {"component": "a", "at": [1, 2]}),
        ("mate", {"type": "concentric", "a": "a:cyl(d=10)"}),
        ("ground_component", {}),
    ],
)
def test_assembly_atomic_operations_reject_invalid_params(operation, params):
    with pytest.raises(ValidationError):
        AssemblyAtomicOperation(operation=operation, params=params)


def test_assembly_atomic_plan_rejects_workflow_macros():
    with pytest.raises(ValidationError):
        AssemblyAtomicPlan.model_validate({
            "user_intent": "bad",
            "steps": [{"operation": "joint", "params": {}, "description": ""}],
        })


def test_atomic_executor_stops_at_first_failure():
    class FakeSession:
        def __init__(self):
            self.calls = []

        def call(self, name, params):
            self.calls.append((name, params))
            return {"ok": name != "mate"}

    plan = AssemblyAtomicPlan.model_validate({
        "user_intent": "test",
        "steps": [
            {
                "operation": "insert_component",
                "params": {"part": "a.SLDPRT", "name": "a"},
            },
            {
                "operation": "mate",
                "params": {"type": "lock", "a": "a:component", "b": "b:component"},
            },
            {"operation": "save_assembly", "params": {}},
        ],
    })
    session = FakeSession()
    rows = AssemblyAtomicExecutor(session).execute(plan)
    assert [name for name, _ in session.calls] == ["insert_component", "mate"]
    assert [row["success"] for row in rows] == [True, False]


def test_atomic_planner_makes_one_structured_request():
    content = (
        '{"user_intent":"save empty fixture","steps":['
        '{"operation":"save_assembly","params":{},"description":"save"}],"notes":""}'
    )

    class Completions:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15,
                ),
            )

    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    plan, usage = AssemblyAtomicPlanner(client, "model").plan("goal")
    assert plan.steps[-1].operation.value == "save_assembly"
    assert usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "calls": 1,
    }
    assert len(completions.requests) == 1
    assert completions.requests[0]["response_format"]["type"] == "json_schema"


def test_atomic_planner_retries_truncated_output_with_32k_budget():
    valid = (
        '{"user_intent":"save","steps":['
        '{"operation":"save_assembly","params":{},"description":""}],"notes":""}'
    )

    class Completions:
        def __init__(self):
            self.requests = []

        def create(self, **kwargs):
            self.requests.append(kwargs)
            truncated = len(self.requests) == 1
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="" if truncated else valid),
                    finish_reason="length" if truncated else "stop",
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=kwargs["max_tokens"], total_tokens=10 + kwargs["max_tokens"],
                ),
            )

    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    _plan, usage = AssemblyAtomicPlanner(client, "model", max_tokens=16000).plan("goal")
    assert [request["max_tokens"] for request in completions.requests] == [16000, 32000]
    assert usage["calls"] == 2
    assert usage["completion_tokens"] == 48000


def test_assembly_eval_has_atomic_part_and_assembly_branches():
    source = (ROOT / "eval/eval_assembly_scratch.py").read_text(encoding="utf-8")
    assert 'choices=["L0", "L0_mt", "L1"]' in source
    assert "SwLLMClient(" in source
    assert "SwExecutor(base_url=sw).execute_plan(plan)" in source
    assert "AssemblyAtomicPlanner" in source
    assert 'agent_kind="atomic_mt"' in source
    assert '["atomic_mt"]' in source
    assert '"assembly_atomic_api"' in source
