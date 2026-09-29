"""rubric 检查器的正反例测试：正例全部通过，每个反例恰好在预期的条目上失败。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import fixtures as fx

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "rubrics"
ARTIFACTS = Path(__file__).resolve().parents[2] / "eval" / "artifacts"

# (case, 变体名, 构造函数, 预期失败的条目 id)
CASES = [
    ("flange", "ok", fx.flange, set()),
    ("flange", "5_holes", lambda: fx.flange(n_holes=5), {"bolt_holes"}),
    ("flange", "blind_hole", lambda: fx.flange(blind_first=True), {"bolt_holes"}),
    ("flange", "pcd_76", lambda: fx.flange(pcd_r=38), {"bolt_pattern"}),
    ("lidless_housing", "ok", fx.housing, set()),
    ("lidless_housing", "wall_4", lambda: fx.housing(wall=4), {"open_top", "walls", "volume"}),
    ("lidless_housing", "floor_4", lambda: fx.housing(floor=4), {"bottom", "volume"}),
    ("flanged_elbow", "ok", fx.elbow, set()),
    ("flanged_elbow", "flanges_inward", lambda: fx.elbow(inward=True), {"tube_path", "flange_placement", "bbox"}),
    ("flanged_elbow", "hollow_tube", lambda: fx.elbow(hollow=True), {"tube_solid", "volume"}),
    ("socket_bolt", "ok", fx.bolt, set()),
    ("socket_bolt", "run1_fillet_head_bottom", lambda: fx.bolt("head_bottom"), {"neck_fillet"}),
    ("socket_bolt", "run2_fillet_head_top", lambda: fx.bolt("head_top"), {"neck_fillet"}),
    ("mug", "ok", fx.mug, set()),
    ("mug", "run1_half_ring_into_cavity", lambda: fx.mug("run1"),
     {"handle_ring", "cavity_clear", "handle_orientation"}),
    ("mug", "run2_ring_point_contact", lambda: fx.mug("run2"), {"solid", "handle_ring", "handle_orientation"}),
    ("phone_stand", "ok", fx.phone_stand, set()),
    ("phone_stand", "run2_horizontal_thickness", lambda: fx.phone_stand("run2"), {"plate"}),
    ("phone_stand", "run1_vertical_fin", lambda: fx.phone_stand("run1"),
     {"plate", "lip", "ribs", "u_slot", "symmetry", "plate_leans_back"}),
]


@pytest.mark.parametrize("case,label,build,expected", CASES, ids=[f"{c[0]}-{c[1]}" for c in CASES])
def test_fixture(case, label, build, expected):
    res = evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))
    failed = {r["id"] for r in res["rubrics"] if not r["ok"]}
    assert failed == expected
    assert res["accepted"] == (not {r["id"] for r in res["rubrics"] if r["basis"] == "explicit"} & expected)


def test_deterministic():
    rubric = load_rubric(RUBRICS / "phone_stand.yaml")
    shape = fx.phone_stand("run2")
    assert evaluate(rubric, Part(shape)) == evaluate(rubric, Part(shape))


@pytest.mark.parametrize("run", ["r1", "r2"])
def test_real_step_lidless_housing(run):
    step = ARTIFACTS / "text_workflow_l1_qwen3_5_397b_a17b_heldout3_tools24_result" / f"lidless_housing_{run}.step"
    if not step.exists():
        pytest.skip("未找到本机保存的 STEP")
    res = evaluate(load_rubric(RUBRICS / "lidless_housing.yaml"), Part.from_step(step))
    assert res["accepted"] and res["score"] == 100.0
