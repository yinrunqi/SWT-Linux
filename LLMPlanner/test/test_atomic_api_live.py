"""L0 Atomic API 真机 smoke：直接走 SwExecutor，不经过 workflow 聚合工具或 LLM。

每个测试只验证 C# 原子端点能完成一个最小有效操作；conftest 会在每例结束后
调用 close-all-documents。所有长度单位均为米。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from planner.executor import SwExecutor
from planner.schemas import OperationType, SwOperation


pytestmark = pytest.mark.solidworks
BASE = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")


def run(*steps: tuple[str, dict]) -> list[dict]:
    executor = SwExecutor(base_url=BASE, verbose=False)
    results = executor.execute_operations(
        [SwOperation(operation=operation, params=params) for operation, params in steps]
    )
    assert results and all(item["success"] for item in results), results
    return results


def face_ref(result: dict, *, normal_axis: str | None = None) -> str:
    faces = result["faces"]
    if normal_axis:
        for face in faces:
            normal = face.get("normal") or {}
            if abs(float(normal.get(normal_axis, 0))) > .99:
                return f"face:{face['index']}"
    return f"face:{faces[0]['index']}"


def make_box() -> list[dict]:
    return run(
        ("new_part", {}),
        ("create_box", {"length": .04, "width": .03, "height": .02}),
        ("model_observation", {"maxFaces": 100}),
    )


def test_document_lifecycle_and_readonly_part_endpoints(tmp_path: Path):
    results = make_box()
    assert results[-1]["volumeMm3"] == pytest.approx(24_000, abs=2)
    part = tmp_path / "atomic_box.SLDPRT"
    step = tmp_path / "atomic_box.step"
    results = run(
        ("save_part_as", {"savePath": str(part)}),
        ("export_step", {"stepPath": str(step)}),
        ("open_part", {"partPath": str(part)}),
        ("feature_tree", {}),
        ("body_bounding_box", {}),
        ("body_volume", {}),
        ("sketch_stats", {}),
    )
    assert part.exists() and step.exists()
    assert results[-2]["volumeMm3"] == pytest.approx(24_000, abs=2)


def test_reference_plane_axis_named_sketch_point_and_delete_feature():
    initial = make_box()
    body_feature = next(
        feature["name"] for feature in initial[-1]["features"]
        if feature.get("type") not in {"OriginProfileFeature", "ProfileFeature"}
    )
    results = run(
        ("ref_plane_offset", {"reference": "plane:0", "distance": .025}),
        ("ref_axis_planes", {"first": "plane:0", "second": "plane:1"}),
    )
    plane_name = results[0]["result"]["featureName"]
    run(
        ("select_ref_plane_and_sketch", {"planeName": plane_name}),
        ("sketch_point", {"x": 0, "y": 0, "z": .025}),
        ("close_sketch_named", {}),
        ("delete_feature", {"featureName": body_feature}),
    )


def test_profile_mirror_until_and_rib_endpoints():
    # `sketch_profile` 和 `feature_mirror` 直接产生实体；until/rib 在同一零件
    # 上至少走到 C# 特征创建入口，确保 L0 参数/路由/前置草图均可执行。
    results = run(
        ("new_part", {}),
        ("select_plane", {"index": 0}),
        ("sketch_profile", {"segments": [
            {"type": "line", "x1": -.02, "y1": -.015, "x2": .02, "y2": -.015},
            {"type": "line", "x1": .02, "y1": -.015, "x2": .02, "y2": .015},
            {"type": "line", "x1": .02, "y1": .015, "x2": -.02, "y2": .015},
            {"type": "line", "x1": -.02, "y1": .015, "x2": -.02, "y2": -.015},
        ]}),
        ("feature_extrusion", {"depth": .02}),
        ("feature_mirror", {"plane": "plane:0", "mirrorBody": True}),
        ("model_observation", {}),
    )
    assert results[-1]["bodyCount"] >= 1


def test_3d_path_and_circular_sweep_endpoint():
    results = run(
        ("new_part", {}),
        ("sketch3d_path", {"points": [[0, 0, 0], [0, 0, .03], [.02, 0, .03]], "cornerRadii": [.005]}),
        ("feature_sweep", {"circularDiameter": .006}),
        ("body_volume", {}),
    )
    assert results[-1]["volumeMm3"] > 0


def test_loft_and_sheet_metal_base_endpoints():
    # 两个独立零件分别验证 loft 和 sheet-metal base，避免 feature 语义互相干扰。
    results = run(
        ("new_part", {}),
        ("select_plane", {"index": 0}),
        ("sketch_circle", {"cx": 0, "cy": 0, "radius": .01}),
        ("close_sketch_named", {}),
        ("ref_plane_offset", {"reference": "plane:0", "distance": .03}),
    )
    plane_name = results[-1]["result"]["featureName"]
    results = run(
        ("select_ref_plane_and_sketch", {"planeName": plane_name}),
        ("sketch_circle", {"cx": 0, "cy": 0, "radius": .006}),
        ("feature_loft", {"sectionCount": 2}),
        ("body_volume", {}),
    )
    assert results[-1]["volumeMm3"] > 0

    results = run(
        ("new_part", {}),
        ("select_plane", {"index": 0}),
        ("sketch_rectangle", {"x1": -.02, "y1": -.01, "x2": .02, "y2": .01}),
        ("feature_sheet_metal_base", {"thickness": .001}),
        ("body_volume", {}),
    )
    assert results[-1]["volumeMm3"] > 0


def test_shell_draft_cosmetic_thread_and_observed_face_selection():
    # shell
    observation = make_box()[-1]
    opening = face_ref(observation, normal_axis="z")
    run(("feature_shell", {"thickness": .001, "openFaces": [opening]}))

    # draft + observed-face sketch on a fresh box
    observation = make_box()[-1]
    neutral = face_ref(observation, normal_axis="z")
    other = next(
        f"face:{face['index']}" for face in observation["faces"]
        if f"face:{face['index']}" != neutral and face.get("surfaceType") == "plane"
    )
    run(
        ("feature_draft", {"angleDeg": 3, "neutral": neutral, "faces": [other]}),
        ("select_observed_face_and_sketch", {"faceIndex": int(other.split(":")[1])}),
        ("close_sketch_named", {}),
    )

    # circular base → cosmetic thread on its observed inner cylindrical face
    results = run(
        ("new_part", {}),
        ("select_plane", {"index": 0}),
        ("sketch_circle", {"cx": 0, "cy": 0, "radius": .01}),
        ("feature_extrusion", {"depth": .02}),
        ("select_observed_face_and_sketch", {"faceIndex": 1}),
        # #8-32 nominal thread diameter = 0.0041656 m.
        ("sketch_circle", {"cx": 0, "cy": 0, "cz": .02, "radius": .0020828}),
        ("feature_cut_extrusion", {"depth": .015}),
        ("model_observation", {}),
    )
    cylinder = next(
        face for face in results[-1]["faces"]
        if face.get("surfaceType") == "cylinder" and float(face.get("radius") or 0) < .003
    )
    run((
        "feature_cosmetic_thread",
        {
            "face": f"face:{cylinder['index']}",
            "diameter": .0041656,
            "depth": .0062484,
            "standard": 0,
            "standardType": "Machine Threads",
            "size": "#8-32",
        },
    ))


def test_until_and_rib_raw_atomic_endpoints():
    """终止到面和筋均只经 L0 HTTP 原子端点执行。"""
    plane = run(
        ("new_part", {}),
        ("ref_plane_offset", {"reference": "plane:0", "distance": .03}),
    )[-1]["result"]["featureName"]
    run(
        ("select_plane", {"index": 0}),
        ("sketch_circle", {"cx": 0, "cy": 0, "radius": .01}),
        ("feature_until", {"until": f"refplane:{plane}"}),
    )

    rib = run(
        # L 形支架提供两个相交实体壁；斜线连接底板和竖板，形成真实三角加强筋。
        ("new_part", {}),
        ("select_plane", {"index": 1}),
        ("sketch_polyline", {"points": [
            {"x": -.03, "y": .02}, {"x": .03, "y": .02},
            {"x": .03, "y": -.02}, {"x": -.03, "y": -.02},
        ], "closed": True}),
        ("feature_extrusion", {"depth": .006}),
        ("select_observed_face_and_sketch", {"faceIndex": 2}),
        ("sketch_polyline", {"points": [
            {"x": .03, "y": .006, "z": -.02}, {"x": .03, "y": .006, "z": .02},
            {"x": .03, "y": .036, "z": .02}, {"x": .03, "y": .036, "z": -.02},
        ], "closed": True}),
        ("feature_extrusion", {"depth": .006, "flipDirection": True}),
        ("select_plane", {"index": 0}),
        ("sketch_line", {"x1": 0, "y1": .006, "x2": .019, "y2": .024}),
        ("feature_rib", {"thickness": .004}),
    )[-1]
    assert rib["result"]["featureType"] == "Rib"
