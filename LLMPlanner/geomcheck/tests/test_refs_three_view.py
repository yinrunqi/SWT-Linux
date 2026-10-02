"""三视图 track 33 道题：参考 STEP 满分且与构造函数一致；每个反例恰好在预期条目上失败；
图像分对参考件的任意摆放为 100，对反例与镜像件低于 100。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests -q
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs_three_view as R
from geomcheck.views import Reference

ROOT = Path(__file__).resolve().parents[2]
RUBRICS = ROOT / "eval" / "rubrics_three_view"

MUTANTS = [
    ("b01_l_bracket", "notch_25", lambda: R.b01_l_bracket(notch=(25, 20)), {"notch", "volume"}),
    ("b01_l_bracket", "depth_25", lambda: R.b01_l_bracket(depth=25), {"outer", "prismatic", "volume"}),
    ("b02_plate_with_holes", "spacing_36", lambda: R.b02_plate_with_holes(spacing=36), {"hole_positions"}),
    ("b02_plate_with_holes", "d_8", lambda: R.b02_plate_with_holes(d=8), {"holes", "hole_positions", "volume"}),
    ("b03_t_bracket", "stem_24", lambda: R.b03_t_bracket(stem_w=24), {"stem", "volume"}),
    ("b03_t_bracket", "stem_off_center", lambda: R.b03_t_bracket(stem_x0=10), {"stem"}),
    ("b04_keycap", "top_12", lambda: R.b04_keycap(top=12), {"top_face", "taper", "volume"}),
    ("b05_keycap_full", "pocket_4", lambda: R.b05_keycap_full(pocket_depth=4), {"pocket", "volume"}),
    ("b05_keycap_full", "no_fillet", lambda: R.b05_keycap_full(fillet_r=0), {"fillet"}),
    ("b06_lock_striker", "holes_at_30", lambda: R.b06_lock_striker(hole_y=30), {"holes"}),
    ("b06_lock_striker", "slot_24", lambda: R.b06_lock_striker(slot_len=24), {"slot", "volume"}),
    ("b06_lock_striker", "sharp_corners", lambda: R.b06_lock_striker(corner_r=0), {"corners"}),
    ("b07_switch_panel", "pocket_5", lambda: R.b07_switch_panel(pocket_depth=5), {"pocket", "volume"}),
    ("b07_switch_panel", "holes_at_33", lambda: R.b07_switch_panel(hole_c=33), {"holes"}),
    ("b11_gear_disc", "pcd_44", lambda: R.b11_gear_disc(pcd=44), {"holes"}),
    ("b11_gear_disc", "eight_holes", lambda: R.b11_gear_disc(n_holes=8), {"holes", "volume"}),
    ("b22_round_bowl", "depth_36", lambda: R.b22_round_bowl(depth=36), {"cavity", "volume"}),
    # 12 列居中排布时中间没有一列，按中间列探测的行检查也失败
    ("c1_mech_keyboard", "cols_12", lambda: R.c1_mech_keyboard(cols=12),
     {"columns", "rows", "key_shape", "volume"}),
    ("c1_mech_keyboard", "no_draft", lambda: R.c1_mech_keyboard(key_top=15),
     {"columns", "rows", "key_shape", "volume"}),
    ("c1_mech_keyboard", "pitch_17", lambda: R.c1_mech_keyboard(pitch=17), {"columns", "rows"}),
    ("c2_gearbox", "wall_10", lambda: R.c2_gearbox(wall=10), {"box", "volume"}),
    ("c2_gearbox", "bearing_at_50", lambda: R.c2_gearbox(bearing_y=50), {"bearing"}),
    ("c2_gearbox", "no_mount_holes", lambda: R.c2_gearbox(mount_holes=False), {"mount_holes"}),
    ("c3_gear_pair", "center_72", lambda: R.c3_gear_pair(center_dist=72), {"gears", "tips_on_center_line"}),
    ("c3_gear_pair", "small_12_teeth", lambda: R.c3_gear_pair(small_teeth=12), {"small_teeth"}),
    ("c4_desktop_pen_holder", "rear_depth_100", lambda: R.c4_desktop_pen_holder(rear_depth=100), {"floors"}),
    ("c4_desktop_pen_holder", "no_charge_hole", lambda: R.c4_desktop_pen_holder(charge_hole=False),
     {"charge_hole"}),
    ("c4_desktop_pen_holder", "no_fillet", lambda: R.c4_desktop_pen_holder(fillet_r=0), {"fillets"}),
    # D 组：自行设计、由参考件投影出图的 20 道题
    ("d01_v_block", "v_width_36", lambda: R.d01_v_block(v_width=36), {"v_groove", "volume"}),
    # 没有让刀槽时 V 形斜面延伸到槽底，斜面面积随之变化
    ("d01_v_block", "no_relief", lambda: R.d01_v_block(relief=False), {"relief", "v_groove"}),
    ("d01_v_block", "groove_y_28", lambda: R.d01_v_block(groove_y=28), {"side_grooves"}),
    ("d02_bearing_block", "bore_30", lambda: R.d02_bearing_block(bore_d=30), {"bore", "volume"}),
    ("d02_bearing_block", "no_oil_hole", lambda: R.d02_bearing_block(oil_hole=False), {"oil_hole"}),
    ("d02_bearing_block", "hole_dx_40", lambda: R.d02_bearing_block(hole_dx=40), {"mount_holes"}),
    ("d02_bearing_block", "no_recess", lambda: R.d02_bearing_block(recess=False), {"recess", "volume"}),
    ("d03_rib_bracket", "rib_top_40", lambda: R.d03_rib_bracket(rib_top=40), {"gussets", "volume"}),
    ("d03_rib_bracket", "hole_y_40", lambda: R.d03_rib_bracket(hole_y=40), {"hole"}),
    ("d03_rib_bracket", "no_fillet", lambda: R.d03_rib_bracket(fillet_r=0), {"corners"}),
    ("d04_flange_cover", "four_holes", lambda: R.d04_flange_cover(n_holes=4), {"holes", "volume"}),
    ("d04_flange_cover", "recess_30", lambda: R.d04_flange_cover(recess_r=30), {"recess", "volume"}),
    ("d05_stepped_shaft", "key_42_68", lambda: R.d05_stepped_shaft(key=(42, 68)), {"keyway"}),
    ("d05_stepped_shaft", "hole_x_20", lambda: R.d05_stepped_shaft(hole_x=20), {"cross_hole"}),
    ("d06_u_clevis", "slot_18", lambda: R.d06_u_clevis(slot_w=18), {"slot", "volume"}),
    # 耳板顶部圆弧与销孔同心，销孔下移时耳板外形一起变
    ("d06_u_clevis", "pin_y_36", lambda: R.d06_u_clevis(pin_y=36), {"pin_hole", "ears"}),
    ("d06_u_clevis", "tap_depth_20", lambda: R.d06_u_clevis(tap_depth=20), {"boss"}),
    # 键槽开在偏心孔上，孔心移动时键槽位置一起变
    ("d07_eccentric_cam", "offset_12", lambda: R.d07_eccentric_cam(offset=12), {"bore", "keyway"}),
    ("d07_eccentric_cam", "no_keyway", lambda: R.d07_eccentric_cam(keyway=False), {"keyway"}),
    ("d07_eccentric_cam", "hub_x_0", lambda: R.d07_eccentric_cam(hub_x=0), {"hub"}),
    ("d08_pipe_tee", "branch_bore_12", lambda: R.d08_pipe_tee(branch_bore=12), {"branch", "volume"}),
    ("d08_pipe_tee", "flange_t_10", lambda: R.d08_pipe_tee(flange_t=10), {"flanges", "volume"}),
    ("d08_pipe_tee", "hole_deg_0", lambda: R.d08_pipe_tee(hole_deg=0), {"flange_holes"}),
    ("d09_angle_bracket", "slot_25_51", lambda: R.d09_angle_bracket(slot=(25, 51)), {"slots"}),
    ("d09_angle_bracket", "hole_y_24", lambda: R.d09_angle_bracket(hole_y=24), {"holes"}),
    ("d09_angle_bracket", "no_fillet", lambda: R.d09_angle_bracket(fillet_r=0), {"fillet"}),
    ("d10_clamp_plate", "cbore_6", lambda: R.d10_clamp_plate(cbore_depth=6), {"counterbores", "volume"}),
    ("d10_clamp_plate", "rebate_4", lambda: R.d10_clamp_plate(rebate_depth=4), {"rebate"}),
    ("d10_clamp_plate", "window_40_24", lambda: R.d10_clamp_plate(window=(40, 24)), {"window", "volume"}),
    ("d11_dovetail_slide", "angle_50", lambda: R.d11_dovetail_slide(angle=50), {"dovetail", "volume"}),
    ("d11_dovetail_slide", "hole_depth_15", lambda: R.d11_dovetail_slide(hole_depth=15), {"holes"}),
    ("d11_dovetail_slide", "no_top_slot", lambda: R.d11_dovetail_slide(top_slot=False), {"top_slot", "volume"}),
    ("d12_cut_block", "chamfer_25", lambda: R.d12_cut_block(chamfer_z=25), {"chamfer", "volume"}),
    ("d12_cut_block", "notch_20", lambda: R.d12_cut_block(notch_w=20), {"notch", "volume"}),
    ("d12_cut_block", "hole_z_20", lambda: R.d12_cut_block(hole_z=20), {"hole"}),
    ("d13_v_pulley", "groove_10", lambda: R.d13_v_pulley(groove_depth=10), {"grooves", "volume"}),
    ("d13_v_pulley", "six_holes", lambda: R.d13_v_pulley(n_holes=6), {"holes", "volume"}),
    ("d13_v_pulley", "no_keyway", lambda: R.d13_v_pulley(keyway=False), {"keyway"}),
    ("d14_link_rod", "pocket_5", lambda: R.d14_link_rod(pocket_depth=5), {"pockets", "volume"}),
    ("d14_link_rod", "small_t_20", lambda: R.d14_link_rod(small_t=20), {"thickness", "volume"}),
    ("d15_motor_mount", "bore_20", lambda: R.d15_motor_mount(bore_d=20), {"motor_bore"}),
    ("d15_motor_mount", "square_30", lambda: R.d15_motor_mount(square=30), {"screw_holes"}),
    ("d15_motor_mount", "no_gussets", lambda: R.d15_motor_mount(gussets=False), {"gussets", "volume"}),
    ("d16_heat_sink", "six_fins", lambda: R.d16_heat_sink(n_fins=6), {"fins", "volume"}),
    ("d16_heat_sink", "hole_dz_12", lambda: R.d16_heat_sink(hole_dz=12), {"holes"}),
    # R28 时没有 Ø60 外圆，总高也随之变
    ("d17_pipe_clamp", "outer_28", lambda: R.d17_pipe_clamp(outer_r=28), {"outer", "strap", "bores", "volume"}),
    # 无间隙时两件相贴，螺栓孔在两件上的孔壁合成一段
    ("d17_pipe_clamp", "no_gap", lambda: R.d17_pipe_clamp(gap=0), {"ears_gap", "bolt_holes", "volume"}),
    ("d17_pipe_clamp", "hole_x_38", lambda: R.d17_pipe_clamp(hole_x=38), {"bolt_holes"}),
    # 11 齿居中排布时 x=30、90 处正好是齿，沉孔上方被齿挡住
    ("d18_gear_rack", "eleven_teeth", lambda: R.d18_gear_rack(n_teeth=11),
     {"teeth", "holes", "counterbores", "volume"}),
    ("d18_gear_rack", "cbore_4", lambda: R.d18_gear_rack(cbore_depth=4), {"counterbores"}),
    ("d19_hinge_leaf", "notch_3", lambda: R.d19_hinge_leaf(notch=3), {"plate", "volume"}),
    ("d19_hinge_leaf", "hole_x_35", lambda: R.d19_hinge_leaf(hole_x=35), {"holes"}),
    ("d20_ribbed_base", "rib_h_12", lambda: R.d20_ribbed_base(rib_h=12), {"ribs", "volume"}),
    ("d20_ribbed_base", "center_depth_25", lambda: R.d20_ribbed_base(center_depth=25), {"center_boss"}),
    ("d20_ribbed_base", "boss_x_50", lambda: R.d20_ribbed_base(boss_x=50), {"bosses"}),
]

_refs: dict[str, Reference] = {}


def _rubric(case):
    return load_rubric(RUBRICS / f"{case}.yaml")


def _reference(case) -> Reference:
    if case not in _refs:
        _refs[case] = Reference(Part.from_step(ROOT / _rubric(case)["reference"]).shape)
    return _refs[case]


def _failed(res):
    return {r["id"] for r in res["rubrics"] if not r["ok"]}


def _transform(shape, trsf):
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def _rotated(shape):
    t1, t2 = gp_Trsf(), gp_Trsf()
    t1.SetRotation(gp_Ax1(gp_Pnt(3, 4, 5), gp_Dir(1, 0, 0)), math.pi / 2)
    t2.SetRotation(gp_Ax1(gp_Pnt(-7, 2, 1), gp_Dir(0, 0, 1)), -math.pi / 2)
    t2.Multiply(t1)
    return _transform(shape, t2)


def test_every_case_has_rubric_and_assets():
    assert {p.stem for p in RUBRICS.glob("*.yaml")} == set(R.CASES)
    for case in R.CASES:
        rubric = _rubric(case)
        assert rubric["case"] == case
        assert (ROOT / rubric["image"]).is_file() and (ROOT / rubric["reference"]).is_file()
        assert rubric["prompt"].startswith("根据所附三视图") and rubric["prompt_en"].startswith("Model the")
        assert all(r.get("source") for r in rubric["rubrics"])


@pytest.mark.parametrize("case", R.CASES)
def test_reference(case):
    rubric = _rubric(case)
    part = Part.from_step(ROOT / rubric["reference"])
    res = evaluate(rubric, part)
    assert _failed(res) == set()
    assert res["score"] == 100.0 and res["accepted"] and res["hard_ok"]
    built = Part(getattr(R, case)())
    assert abs(part.volume() - built.volume()) <= 1e-6 * built.volume()
    for item in rubric["rubrics"]:
        for chk in item["check"]:
            if "volume" in chk:
                assert abs(part.volume() - chk["volume"]) <= 5e-4 * chk["volume"]


@pytest.mark.parametrize("case,label,build,expected", MUTANTS, ids=[f"{c[0]}-{c[1]}" for c in MUTANTS])
def test_mutant(case, label, build, expected):
    assert _failed(evaluate(_rubric(case), Part(build()))) == expected


@pytest.mark.parametrize("case", R.CASES)
def test_image_score_reference_any_pose(case):
    ref = _reference(case)
    shape = getattr(R, case)()
    assert ref.score(shape)["image_score"] == 100.0
    assert ref.score(_rotated(shape))["image_score"] >= 99.9


@pytest.mark.parametrize("case,label,build,expected", MUTANTS, ids=[f"{c[0]}-{c[1]}" for c in MUTANTS])
def test_image_score_mutant(case, label, build, expected):
    assert _reference(case).score(build())["image_score"] < 99.5


def test_image_score_rejects_mirror():
    """镜像件不是任何旋转：C4 的充电线孔、提手槽在前壁，镜像后跑到背面一侧。"""
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)))
    mirrored = _transform(R.c4_desktop_pen_holder(), t)
    assert _reference("c4_desktop_pen_holder").score(mirrored)["image_score"] < 99.5
