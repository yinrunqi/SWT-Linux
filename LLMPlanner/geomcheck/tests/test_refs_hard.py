"""复杂综合件（showcase）8 道题：参考正例满分且体积与 rubric 一致；另一种读法满分；每个反例恰好在预期条目上失败。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs_hard as R

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "rubrics_hard"

HARD = ["quad_frame", "connecting_rod", "finned_housing", "sprocket", "handwheel", "camshaft",
        "planet_carrier", "impeller"]

ALTERNATIVE = [
    ("impeller", "radial_tip", lambda: R.impeller(tip="radial")),
]

MUTANTS = [
    ("quad_frame", "no_fillet", lambda: R.quad_frame(fillet_r=0), {"fillets"}),
    ("quad_frame", "motor_offset_8", lambda: R.quad_frame(motor_offset=8), {"motor_holes"}),
    ("quad_frame", "slot_40_75", lambda: R.quad_frame(slot=(40, 75)), {"diagonal", "slots", "volume"}),
    ("connecting_rod", "pocket_5", lambda: R.connecting_rod(pocket_depth=5), {"web", "volume"}),
    ("connecting_rod", "no_oil_hole", lambda: R.connecting_rod(oil_hole=False), {"centerline", "oil_hole"}),
    ("connecting_rod", "no_chamfer", lambda: R.connecting_rod(chamfer=False), {"chamfers"}),
    ("finned_housing", "fins_rotated_15", lambda: R.finned_housing(fin_phase=15),
     {"outer", "tube", "flange", "fins", "fin_extent", "fin_phase", "screw_holes"}),
    ("finned_housing", "fins_stop_at_80", lambda: R.finned_housing(fin_top=80), {"fin_extent", "volume"}),
    ("finned_housing", "bottom_10", lambda: R.finned_housing(bottom=10), {"bottom", "volume"}),
    ("sprocket", "holes_between_seats", lambda: R.sprocket(hole_phase=30), {"outer", "disc", "phasing"}),
    ("sprocket", "screw_at_10", lambda: R.sprocket(screw_z=10), {"set_screw"}),
    ("sprocket", "keyway_12", lambda: R.sprocket(keyway_bottom=12), {"phasing"}),
    ("handwheel", "handle_60", lambda: R.handwheel(handle_top=60), {"outer", "handle", "knob", "volume"}),
    ("handwheel", "four_spokes", lambda: R.handwheel(n_spokes=4), {"outer", "spokes", "volume"}),
    ("camshaft", "counterclockwise", lambda: R.camshaft(phases=(0, 270, 180, 90)), {"lobe_profile", "phasing"}),
    ("camshaft", "firing_1342", lambda: R.camshaft(phases=(0, 180, 90, 270)), {"lobe_profile", "phasing"}),
    ("camshaft", "dowel_6_deep", lambda: R.camshaft(dowel_depth=6), {"dowel"}),
    ("planet_carrier", "pins_on_pillar_side", lambda: R.planet_carrier(pin_phase=30), {"pillars"}),
    ("planet_carrier", "tap_15", lambda: R.planet_carrier(tap_depth=15), {"tapped_hole"}),
    ("planet_carrier", "gap_22", lambda: R.planet_carrier(gap=22),
     {"outer", "discs", "pillars", "sun_hole", "tapped_hole"}),
    ("impeller", "forward_curved", lambda: R.impeller(center=(18, 40)), {"blade_arc", "blade_height"}),
    ("impeller", "no_chamfer", lambda: R.impeller(chamfer=False), {"hub_chamfer"}),
    ("impeller", "blades_18", lambda: R.impeller(height=18), {"blade_arc", "blade_height", "blade_tip", "volume"}),
]


def _failed(res):
    return {r["id"] for r in res["rubrics"] if not r["ok"]}


def test_every_case_has_rubric():
    assert {p.stem for p in RUBRICS.glob("*.yaml")} == set(HARD)


@pytest.mark.parametrize("case", HARD)
def test_reference(case):
    rubric = load_rubric(RUBRICS / f"{case}.yaml")
    part = Part(getattr(R, case)())
    res = evaluate(rubric, part)
    assert _failed(res) == set()
    assert res["score"] == 100.0 and res["accepted"] and res["hard_ok"]
    for item in rubric["rubrics"]:
        for chk in item["check"]:
            if "volume" in chk:
                assert abs(part.volume() - chk["volume"]) <= 5e-4 * chk["volume"]


@pytest.mark.parametrize("case,label,build", ALTERNATIVE, ids=[f"{c[0]}-{c[1]}" for c in ALTERNATIVE])
def test_alternative_reading(case, label, build):
    assert _failed(evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))) == set()


@pytest.mark.parametrize("case,label,build,expected", MUTANTS, ids=[f"{c[0]}-{c[1]}" for c in MUTANTS])
def test_mutant(case, label, build, expected):
    assert _failed(evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))) == expected
