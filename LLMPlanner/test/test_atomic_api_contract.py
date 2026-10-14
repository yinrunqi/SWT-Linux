"""L0 完整 Atomic API 的离线契约、参数和 L1 隔离测试。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from planner.executor import OPERATION_ENDPOINT_MAP
from planner.skill_registry import build_system_prompt, default_registry
from planner.schemas import (
    ATOMIC_PARAM_SCHEMAS,
    PART_ATOMIC_ENDPOINTS,
    OperationType,
    SwOperation,
)
from planner.workflow.tools_v2 import TOOL_SPECS_V2


ROOT = Path(__file__).resolve().parent.parent
PROGRAM = ROOT.parent / "Chapter1.ConnectToSw/Program.cs"


def _csharp_part_routes() -> set[str]:
    routes = set(re.findall(r'case "(/api/[^"]+)":', PROGRAM.read_text(encoding="utf-8")))
    return {
        route for route in routes
        if not route.startswith("/api/assembly/")
        and not route.startswith("/api/view/")
        and route not in {
            "/api/ping", "/api/close-all-documents", "/api/close-inactive-documents",
        }
    }


def test_l0_part_catalog_exactly_covers_csharp_part_routes():
    """新增 C# 零件域端点时，必须同步更新 L0 catalog，而不是悄悄漏掉。"""
    catalog_routes = {path for _method, path in PART_ATOMIC_ENDPOINTS.values()}
    assert catalog_routes == _csharp_part_routes()
    assert set(ATOMIC_PARAM_SCHEMAS) == set(PART_ATOMIC_ENDPOINTS)
    assert all(
        OPERATION_ENDPOINT_MAP[operation] == endpoint
        for operation, endpoint in PART_ATOMIC_ENDPOINTS.items()
    )


def test_l0_catalog_never_exposes_assembly_view_or_service_routes():
    assert all(
        not path.startswith(("/api/assembly/", "/api/view/"))
        for _method, path in PART_ATOMIC_ENDPOINTS.values()
    )
    assert "/api/close-all-documents" not in {
        path for _method, path in PART_ATOMIC_ENDPOINTS.values()
    }


@pytest.mark.parametrize(("operation", "params"), [
    ("open_part", {"partPath": r"C:\tmp\part.SLDPRT"}),
    ("delete_feature", {"featureName": "Boss-Extrude1"}),
    ("export_step", {"stepPath": r"C:\tmp\part.step"}),
    ("create_box", {"length": .02, "width": .01, "height": .005}),
    ("ref_plane_offset", {"reference": "PLANE:0", "distance": .01}),
    ("ref_axis_planes", {"first": "PLANE:0", "second": "PLANE:1"}),
    ("select_ref_plane_and_sketch", {"planeName": "Plane1"}),
    ("sketch_profile", {"segments": [{"type": "line", "x1": 0, "y1": 0, "x2": .01, "y2": 0}]}),
    ("sketch3d_path", {"points": [[0, 0, 0], [.01, 0, 0]]}),
    ("sketch_point", {"x": 0, "y": 0}),
    ("feature_mirror", {"plane": "PLANE:0", "mirrorBody": True}),
    ("feature_until", {"until": "face:1"}),
    ("feature_rib", {"thickness": .002}),
    ("feature_sweep", {}),
    ("feature_loft", {}),
    ("feature_shell", {"thickness": .001}),
    ("feature_draft", {"angleDeg": 3, "neutral": "face:1", "faces": ["face:2"]}),
    ("feature_sheet_metal_base", {"thickness": .001}),
    ("feature_cosmetic_thread", {"face": "face:1", "diameter": .006, "depth": .01}),
    ("model_observation", {"maxFaces": 200}),
])
def test_new_atomic_operations_accept_minimal_valid_params(operation, params):
    item = SwOperation(operation=operation, params=params)
    assert item.operation.value == operation
    assert item.params == ATOMIC_PARAM_SCHEMAS[item.operation].model_validate(params).model_dump(
        exclude_none=True, exclude_unset=True
    )


@pytest.mark.parametrize(("operation", "params"), [
    ("create_box", {"length": .02, "width": .01}),
    ("ref_plane_offset", {"reference": "PLANE:0"}),
    ("sketch3d_path", {"points": [[0, 0]]}),
    ("feature_draft", {"angleDeg": 3, "neutral": "face:1", "faces": []}),
    ("feature_cosmetic_thread", {"face": "face:1", "diameter": .006}),
    ("model_observation", {"maxFaces": 5001}),
])
def test_new_atomic_operations_reject_incomplete_or_invalid_params(operation, params):
    with pytest.raises(ValidationError):
        SwOperation(operation=operation, params=params)


def test_legacy_l0_shape_and_api_route_aliases_remain_compatible():
    item = SwOperation.model_validate({
        "endpoint": "/api/ref-plane/offset",
        "arguments": {"reference": "PLANE:0", "distance": .01},
    })
    assert item.operation is OperationType.REF_PLANE_OFFSET
    assert item.params["distance"] == .01


def test_l1_workflow_tool_set_does_not_import_or_expose_l0_catalog():
    """L1 的 function-calling 工具仍只由 workflow/tools_v2.py 定义。"""
    names = {spec["function"]["name"] for spec in TOOL_SPECS_V2}
    assert "feature_sweep" not in names
    assert "sketch3d_path" not in names
    source = (ROOT / "planner/workflow/tools_v2.py").read_text(encoding="utf-8")
    assert "PART_ATOMIC_ENDPOINTS" not in source


def test_web_l1_branches_before_l0_planner_and_keeps_versions_stable():
    """Web 的 L1/L1_v2 请求在构造 L0 planner/client 之前直接返回。"""
    source = (ROOT / "web/app.py").read_text(encoding="utf-8")
    assert 'WORKFLOW_VERSIONS = {"L1": "v1", "L1_v2": "v2"}' in source
    assert source.index("if action_level in WORKFLOW_VERSIONS:") < source.index(
        "llm_client = None"
    )
    assert "PART_ATOMIC_ENDPOINTS" not in source


def test_l0_prompt_describes_the_complete_atomic_part_api():
    prompt = build_system_prompt(default_registry(), [])
    assert "L0 完整 Atomic API" in prompt
    for operation in ("feature_sweep", "feature_shell", "feature_loft", "sketch3d_path"):
        assert operation in prompt


def test_eval_report_labels_complete_atomic_baseline():
    source = (ROOT / "eval/eval_text_singleturn_qwen.py").read_text(encoding="utf-8")
    assert '"name": "atomic_api_complete"' in source
    assert '"version": "part-api-v1"' in source
