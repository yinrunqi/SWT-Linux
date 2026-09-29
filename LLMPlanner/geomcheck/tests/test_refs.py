"""其余 43 道题：参考正例满分且体积与 rubric 推导值一致；每个反例恰好在预期条目上失败。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "rubrics"

POSITIVE = [
    "stool", "rounded_stool", "storage_box", "standard_die", "hex_nut", "wrench", "vase", "key", "gear_disc",
    "pulley", "stepped_shaft", "l_bracket", "spacer_ring", "mounting_plate", "stepped_block", "knob",
    "hex_standoff", "u_channel", "flanged_bushing", "wedge_block", "spoked_wheel", "end_cap", "link_bar",
    "fork_end", "three_lug_housing", "pillow_block", "twin_arm_rocker", "clevis_bracket", "diamond_flange",
    "support_seat", "crank", "gusset_bracket", "corner_rib_stand", "open_box", "counterbore_plate", "csk_cover",
    "side_pad", "through_boss", "sheet_angle", "sheet_channel", "square_to_round", "rect_to_circle", "bent_tube",
]

# 题面有两种合理读法的，另一种读法也应满分
ALTERNATIVE = [
    ("standard_die", "flat_pits", lambda: refs.standard_die(pit="cyl")),
    ("counterbore_plate", "countersink", lambda: refs.counterbore_plate(kind="csk")),
    ("csk_cover", "counterbore", lambda: refs.csk_cover(kind="cbore")),
    ("gusset_bracket", "upright_from_bottom", lambda: refs.gusset_bracket(total_h=30)),
    ("corner_rib_stand", "upright_from_bottom", lambda: refs.corner_rib_stand(total_h=36)),
    ("storage_box", "outer_loop_only", lambda: refs.storage_box(loops="outer")),
    ("vase", "straight_lower_half", lambda: refs.vase(lower="straight")),
    ("key", "last_notch_cut_by_tip", lambda: refs.key(centers=[18, 26, 34, 42, 50])),
]

# (case, 变体名, 构造函数, 预期失败的条目 id)
MUTANTS = [
    ("stool", "legs_25_from_edge", lambda: refs.stool(edge=25), {"leg_position"}),
    ("rounded_stool", "no_fillet", lambda: refs.stool(), {"top_fillet"}),
    ("storage_box", "wall_7", lambda: refs.storage_box(wall=7), {"open_top", "walls", "bottom", "volume"}),
    ("standard_die", "opposite_not_7", lambda: refs.standard_die(faces=(1, 2, 6, 5, 3, 4)), {"pips"}),
    ("hex_nut", "not_centered", lambda: refs.hex_nut(z0=0), {"centered"}),
    ("wrench", "handle_12_wide", lambda: refs.wrench(handle_w=12), {"handle", "handle_centered", "volume"}),
    ("vase", "neck_44", lambda: refs.vase(neck_r=22), {"profile"}),
    ("vase", "max_only_at_bottom", lambda: refs.vase(lower="bottom_max"), {"profile"}),
    ("key", "4_teeth", lambda: refs.key(n_teeth=4), {"teeth"}),
    ("key", "uneven_pitch", lambda: refs.key(centers=[20, 26, 32, 40, 44]), {"teeth"}),
    ("gear_disc", "10_slots", lambda: refs.gear_disc(n_slots=10), {"slots"}),
    ("pulley", "no_chamfer", lambda: refs.pulley(chamfer=False), {"chamfer"}),
    ("stepped_shaft", "shifted", lambda: refs.stepped_shaft(shift=5), {"centered"}),
    ("l_bracket", "hole_at_36", lambda: refs.l_bracket(hole2=36), {"h_holes"}),
    ("spacer_ring", "one_chamfer", lambda: refs.spacer_ring(chamfers=1), {"chamfers"}),
    ("mounting_plate", "window_30x20", lambda: refs.mounting_plate(window=(30, 20)), {"window", "volume"}),
    ("stepped_block", "hole_from_bottom", lambda: refs.stepped_block(hole_from_bottom=True), {"hole"}),
    ("knob", "10_grooves", lambda: refs.knob(n_grooves=10), {"grooves"}),
    ("hex_standoff", "holes_8_deep", lambda: refs.hex_standoff(depth=8), {"holes", "volume"}),
    ("u_channel", "pitch_25", lambda: refs.u_channel(pitch=25), {"holes", "holes_centered"}),
    ("flanged_bushing", "flange_6", lambda: refs.flanged_bushing(flange_t=6), {"sleeve_and_flange", "volume"}),
    ("wedge_block", "low_end_8", lambda: refs.wedge_block(low=8), {"ends", "slope", "volume"}),
    ("spoked_wheel", "sectors_50deg", lambda: refs.spoked_wheel(span=50), {"sectors", "volume"}),
    ("end_cap", "top_4", lambda: refs.end_cap(top=4), {"cavity", "volume"}),
    ("link_bar", "bar_12_wide", lambda: refs.link_bar(width=12), {"bar", "volume"}),
    ("fork_end", "ear_center_60", lambda: refs.fork_end(ear_center=60), {"outer", "ear_tips", "pin_hole", "volume"}),
    ("three_lug_housing", "cbore_12", lambda: refs.three_lug_housing(cbore_depth=12), {"counterbore", "volume"}),
    ("pillow_block", "slots_at_40", lambda: refs.pillow_block(slot_offset=40), {"slots"}),
    ("twin_arm_rocker", "plates_10", lambda: refs.twin_arm_rocker(plate_t=10), {"plates", "volume"}),
    ("clevis_bracket", "gap_34", lambda: refs.clevis_bracket(inner_gap=34), {"ears"}),
    ("diamond_flange", "lobes_40", lambda: refs.diamond_flange(lobe=40), {"outer", "plate", "volume"}),
    ("support_seat", "center_45", lambda: refs.support_seat(center_z=45), {"outer", "plate", "bore", "volume"}),
    ("gusset_bracket", "upright_33", lambda: refs.gusset_bracket(total_h=33), {"upright_height"}),
    ("corner_rib_stand", "rib_4", lambda: refs.corner_rib_stand(rib_t=4), {"rib"}),
    ("open_box", "wall_3", lambda: refs.open_box(wall=3), {"open_top", "walls", "volume"}),
    ("counterbore_plate", "one_flipped", lambda: refs.counterbore_plate(flip_first=True), {"recess"}),
    ("csk_cover", "one_flipped", lambda: refs.csk_cover(flip_first=True), {"recess"}),
    ("side_pad", "boss_on_left", lambda: refs.side_pad(side=-1), {"boss"}),
    ("side_pad", "boss_overhangs_face", lambda: refs.side_pad(center=(15, 0)), {"boss_on_face"}),
    ("through_boss", "boss_on_left", lambda: refs.through_boss(side=-1), {"boss"}),
    ("sheet_angle", "t_2", lambda: refs.sheet_angle(t=2), {"plate", "volume"}),
    ("square_to_round", "top_d26", lambda: refs.square_to_round(top_r=13), {"top"}),
    ("rect_to_circle", "off_axis", lambda: refs.rect_to_circle(offset=3), {"coaxial"}),
    ("bent_tube", "flanges_inward", lambda: refs.bent_tube(inward=True),
     {"tube_path", "flange_placement", "bbox", "volume"}),
]


def _failed(res):
    return {r["id"] for r in res["rubrics"] if not r["ok"]}


@pytest.mark.parametrize("case", POSITIVE)
def test_reference(case):
    rubric = load_rubric(RUBRICS / f"{case}.yaml")
    part = Part(getattr(refs, case)())
    res = evaluate(rubric, part)
    assert _failed(res) == set()
    assert res["score"] == 100.0 and res["accepted"] and res["hard_ok"]
    for item in rubric["rubrics"]:
        for chk in item["check"]:
            if "volume" in chk:  # 参考实体体积与推导值相差不超过 0.05%
                assert abs(part.volume() - chk["volume"]) <= 5e-4 * chk["volume"]
            if "volume_range" in chk:
                lo, hi = chk["volume_range"]
                assert lo * (1 - 5e-4) <= part.volume() <= hi * (1 + 5e-4)


@pytest.mark.parametrize("case,label,build", ALTERNATIVE, ids=[f"{c[0]}-{c[1]}" for c in ALTERNATIVE])
def test_alternative_reading(case, label, build):
    res = evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))
    assert _failed(res) == set()


@pytest.mark.parametrize("case,label,build,expected", MUTANTS, ids=[f"{c[0]}-{c[1]}" for c in MUTANTS])
def test_mutant(case, label, build, expected):
    res = evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))
    assert _failed(res) == expected


def test_every_case_has_rubric():
    names = {p.stem for p in RUBRICS.glob("*.yaml")}
    assert names == set(POSITIVE) | {"flange", "flanged_elbow", "lidless_housing", "socket_bolt", "mug", "phone_stand"}
    assert len(names) == 49
