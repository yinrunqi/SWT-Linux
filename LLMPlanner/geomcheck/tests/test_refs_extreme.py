"""极难子集（extreme）20 道题：参考正例满分且体积与 rubric 一致；每个反例恰好在预期条目上失败。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests/test_refs_extreme.py -q
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs_extreme as E
from geomcheck.tests.fixtures import box, cut, cyl

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "rubrics_extreme"

BUILDERS = {
    "worm_shaft": E.worm_shaft,
    "die_cast_cover": E.die_cast_cover,
    "compound_drill_block": E.compound_block,
    "variable_fillet_bracket": E.variable_fillet_bracket,
    "roller_cam": E.roller_cam,
    "helical_ring_gear": E.ring_gear,
    "naca_wing_rib": E.naca_rib,
    "scroll_wrap_plate": E.scroll_plate,
    "variable_pitch_spring": E.spring,
    "tangent_link_lever": E.link_lever,
    "pipe_route_3d": E.pipe_route,
    "spherical_dome_ports": E.dome_head,
    "sheet_metal_kfactor": E.sheet_bracket,
    "cast_manifold_fillets": E.manifold,
    "filleted_shell_enclosure": E.shell_enclosure,
    "balanced_flywheel": E.flywheel,
    "inch_callout_plate": E.inch_plate,
    "clipped_hex_perforation": E.hex_plate,
    "twisted_flute_knob": E.flute_knob,
    "tricylinder_node": E.tricyl_node,
}
EXTREME = list(BUILDERS)

SPRING_SLOW_TRANSITION = [(0.0, 5.0), (0.5, 5.0), (1.5, 12.0), (5.5, 12.0), (6.5, 5.0), (7.0, 5.0)]


def _rot_seq_a():
    """先绕 Y 转 20°、再绕 X 转 −15° 得到的孔 A 方向（把两个投影角误当成两次旋转）。"""
    a, b = np.radians(20), np.radians(-15)
    d = np.array([np.sin(a), 0.0, -np.cos(a)])
    return np.array([d[0], d[1] * np.cos(b) - d[2] * np.sin(b), d[1] * np.sin(b) + d[2] * np.cos(b)])


MUTANTS = [
    ("worm_shaft", "right_hand", lambda: E.worm_shaft(lefthand=False), {"left_hand", "thread_ends"}),
    ("worm_shaft", "single_start", lambda: E.worm_shaft(starts=1), {"left_hand", "thread_ends", "two_starts"}),
    ("worm_shaft", "flank_14_5", lambda: E.worm_shaft(flank_deg=14.5), {"flank"}),
    ("worm_shaft", "phase_90", lambda: E.worm_shaft(phase=90),
     {"flank", "left_hand", "thread_ends", "tip_root", "two_starts"}),
    ("die_cast_cover", "no_outer_draft", lambda: E.die_cast_cover(outer_draft=0),
     {"bosses", "cavity_draft", "corners", "outer_draft", "ribs", "volume"}),
    ("die_cast_cover", "cavity_draft_reversed", lambda: E.die_cast_cover(cavity_draft=-3),
     {"bosses", "cavity_draft", "corners", "ribs", "volume"}),
    ("die_cast_cover", "straight_ribs", lambda: E.die_cast_cover(rib_taper=False),
     {"bosses", "cavity_draft", "corners", "ribs"}),
    ("die_cast_cover", "straight_bosses", lambda: E.die_cast_cover(boss_draft=0), {"bosses", "corners", "volume"}),
    ("compound_drill_block", "rotations_not_projections", lambda: E.compound_block(a_dir=_rot_seq_a()),
     {"hole_a_depth", "hole_a_direction", "hole_b_depth", "hole_b_meets_a"}),
    ("compound_drill_block", "b_at_x60", lambda: E.compound_block(b_x=60.0), {"hole_a_depth", "hole_b_depth", "hole_b_meets_a"}),
    ("variable_fillet_bracket", "constant_r6", lambda: E.variable_fillet_bracket(r0=6, r1=6),
     {"fillet_large_end", "fillet_small_end"}),
    ("variable_fillet_bracket", "chamfer_swapped", lambda: E.variable_fillet_bracket(chamfer=(3.0, 6.0)),
     {"slot", "top_chamfer"}),
    ("variable_fillet_bracket", "csk_82", lambda: E.variable_fillet_bracket(csk_deg=82), {"countersinks"}),
    ("roller_cam", "radial_offset", lambda: E.roller_cam(normal=False), {"envelope", "return", "rise"}),
    ("roller_cam", "clockwise", lambda: E.roller_cam(ccw=False), {"dwells", "keyway", "return", "rise"}),
    ("helical_ring_gear", "left_hand", lambda: E.ring_gear(lefthand=True), {"helix_flank", "helix_hand", "tip_chamfer"}),
    ("helical_ring_gear", "transverse_module", lambda: E.ring_gear(transverse=True),
     {"bolt_holes", "helix_flank", "helix_hand", "involute_flank", "lube_hole", "tip_chamfer", "tip_root", "volume"}),
    ("helical_ring_gear", "holes_on_spaces", lambda: E.ring_gear(holes_on="spaces"), {"bolt_holes"}),
    ("helical_ring_gear", "no_chamfer", lambda: E.ring_gear(chamfer=False), {"tip_chamfer"}),
    ("naca_wing_rib", "vertical_thickness", lambda: E.naca_rib(perpendicular=False),
     {"closed_te", "envelope", "lightening_holes", "spar_notch", "thickness_normal"}),
    ("naca_wing_rib", "open_te", lambda: E.naca_rib(closed=False), {"aft_profile", "closed_te", "spar_notch"}),
    ("naca_wing_rib", "fixed_holes", lambda: E.naca_rib(hole_rule=False), {"lightening_holes", "spar_notch", "volume"}),
    ("naca_wing_rib", "no_notch", lambda: E.naca_rib(notch=False),
     {"aft_profile", "closed_te", "envelope", "lightening_holes", "spar_notch", "thickness_normal", "volume"}),
    ("scroll_wrap_plate", "clockwise", lambda: E.scroll_plate(ccw=False),
     {"involute_walls", "tip_seal_groove", "wrap_ends", "wrap_height"}),
    ("scroll_wrap_plate", "thickness_doubled", lambda: E.scroll_plate(alpha=1.5),
     {"involute_walls", "tip_seal_groove", "volume", "wrap_ends"}),
    ("scroll_wrap_plate", "short_wrap", lambda: E.scroll_plate(phi_end=5.5 * math.pi),
     {"involute_walls", "tip_seal_groove", "volume", "wrap_ends"}),
    ("scroll_wrap_plate", "no_groove", lambda: E.scroll_plate(groove=False), {"tip_seal_groove"}),
    ("variable_pitch_spring", "right_hand", lambda: E.spring(lefthand=False), {"left_hand", "top_end"}),
    ("variable_pitch_spring", "slow_transition", lambda: E.spring(table=SPRING_SLOW_TRANSITION), {"end_pitch", "top_end"}),
    ("variable_pitch_spring", "constant_pitch", lambda: E.spring(table=[(0.0, 10.0), (7.0, 10.0)]),
     {"active_pitch", "end_pitch", "ground_ends", "left_hand", "top_end", "volume", "wire_coil"}),
    ("variable_pitch_spring", "not_ground", lambda: E.spring(ground=False),
     {"active_pitch", "end_pitch", "envelope", "ground_ends", "left_hand", "top_end", "volume", "wire_coil"}),
    ("tangent_link_lever", "radii_swapped", lambda: E.link_lever(r_bc=50.0, r_ca=80.0),
     {"arc_slot", "concave_bc", "concave_ca", "holes_keyway", "tangent_line", "volume"}),
    ("tangent_link_lever", "no_keyway", lambda: E.link_lever(keyway=False), {"holes_keyway"}),
    ("tangent_link_lever", "no_slot", lambda: E.link_lever(slot=False), {"arc_slot", "volume"}),
    ("pipe_route_3d", "radii_swapped", lambda: E.pipe_route(radii=(60.0, 45.0)),
     {"bend_p1", "bend_p2", "branch", "flange_p0", "flange_p3", "straights"}),
    ("pipe_route_3d", "branch_up", lambda: E.pipe_route(branch_dir=1), {"branch", "envelope"}),
    ("pipe_route_3d", "end_holes_on_plane", lambda: E.pipe_route(end_holes_straddle=False), {"flange_p3"}),
    ("spherical_dome_ports", "n1_angles", lambda: E.dome_head(n1=(50.0, 30.0)), {"nozzle_n1"}),
    ("spherical_dome_ports", "n3_radial", lambda: E.dome_head(n3_radial=True),
     {"base_flange", "nozzle_n1", "nozzle_n2", "nozzle_n3", "volume"}),
    ("spherical_dome_ports", "flange_holes_0deg", lambda: E.dome_head(flange_holes_deg=0.0), {"base_flange"}),
    ("spherical_dome_ports", "no_top_boss", lambda: E.dome_head(top_boss=False), {"top_nozzle", "volume"}),
    ("sheet_metal_kfactor", "k_0.5", lambda: E.sheet_bracket(k=0.5), {"lip_hole", "wall_holes"}),
    ("sheet_metal_kfactor", "k_0", lambda: E.sheet_bracket(k=0.0), {"lip_hole", "wall_holes"}),
    ("sheet_metal_kfactor", "lip_60deg", lambda: E.sheet_bracket(lip_angle=60.0), {"envelope", "lip", "lip_hole"}),
    ("cast_manifold_fillets", "no_saddle", lambda: E.manifold(saddle_r=0.0), {"saddle_side", "saddle_vertical"}),
    ("cast_manifold_fillets", "saddle_r3", lambda: E.manifold(saddle_r=3.0), {"saddle_side", "saddle_vertical"}),
    ("cast_manifold_fillets", "side_mirrored", lambda: E.manifold(side_x=-40.0), {"bores", "flanges", "saddle_side"}),
    ("cast_manifold_fillets", "no_flange_fillet", lambda: E.manifold(flange_r=0.0), {"flange_fillets"}),
    ("filleted_shell_enclosure", "sharp_top", lambda: E.shell_enclosure(top_r=0.0), {"top_fillets", "volume"}),
    ("filleted_shell_enclosure", "no_lip", lambda: E.shell_enclosure(lip=False), {"rim_step", "volume"}),
    ("filleted_shell_enclosure", "no_vents", lambda: E.shell_enclosure(vents=False), {"vents", "volume"}),
    ("filleted_shell_enclosure", "no_bosses", lambda: E.shell_enclosure(bosses=False), {"bosses", "volume"}),
    ("balanced_flywheel", "keyway_ignored", lambda: E.flywheel(balance_d=E.flywheel_balance_d(keyway=False)), {"balance"}),
    ("balanced_flywheel", "no_balance_hole", lambda: E.flywheel(balance_d=0), {"balance"}),
    ("balanced_flywheel", "sharp_windows", lambda: E.flywheel(corner_r=0), {"window_corners"}),
    ("balanced_flywheel", "no_web_fillet", lambda: E.flywheel(web_fillet=0), {"web_fillets"}),
    ("balanced_flywheel", "no_keyway", lambda: E.flywheel(keyway=False), {"keyway"}),
    ("inch_callout_plate", "letter_h_as_quarter", lambda: E.inch_plate(corner_d=0.25 * E.INCH), {"corner_holes"}),
    ("inch_callout_plate", "tap_drill_7", lambda: E.inch_plate(tap_d=0.201 * E.INCH), {"tapped_holes"}),
    ("inch_callout_plate", "csk_90", lambda: E.inch_plate(csk_deg=90.0), {"countersink"}),
    ("inch_callout_plate", "no_chamfer", lambda: E.inch_plate(chamfer=0), {"plate"}),
    ("inch_callout_plate", "bc_from_x", lambda: E.inch_plate(bc_start=0.0), {"tapped_holes"}),
    ("clipped_hex_perforation", "flat_top", lambda: E.hex_plate(pointy=False),
     {"clip_hub", "hex_orientation", "volume"}),
    ("clipped_hex_perforation", "center_rule", lambda: E.hex_plate(rule="center"),
     {"clip_flat", "clip_hub", "clip_outer", "hex_orientation", "volume"}),
    ("clipped_hex_perforation", "no_flat_clip", lambda: E.hex_plate(flat_clip=False), {"clip_flat", "volume"}),
    ("twisted_flute_knob", "right_hand", lambda: E.flute_knob(twist_deg=40.0), {"flute_section", "left_twist"}),
    ("twisted_flute_knob", "untwisted", lambda: E.flute_knob(twist_deg=0.0), {"flute_section", "left_twist"}),
    ("twisted_flute_knob", "phase_0", lambda: E.flute_knob(phase=0.0),
     {"d_bore", "flute_section", "left_twist", "set_screw"}),
    ("tricylinder_node", "union_core", lambda: E.tricyl_node(core="union"), {"tricylinder_core", "tubes", "volume"}),
    ("tricylinder_node", "strut_up", lambda: E.tricyl_node(strut_dir=(-1.0, -1.0, 1.0)), {"envelope", "strut"}),
    ("tricylinder_node", "no_bores", lambda: E.tricyl_node(bores=False),
     {"bores", "strut", "tricylinder_core", "tubes", "volume"}),
]


def _failed(res):
    return {r["id"] for r in res["rubrics"] if not r["ok"]}


def test_every_case_has_rubric():
    assert {p.stem for p in RUBRICS.glob("*.yaml")} == set(EXTREME)


@pytest.mark.parametrize("case", EXTREME)
def test_reference(case):
    rubric = load_rubric(RUBRICS / f"{case}.yaml")
    part = Part(BUILDERS[case]())
    res = evaluate(rubric, part)
    assert _failed(res) == set()
    assert res["score"] == 100.0 and res["accepted"] and res["hard_ok"]
    for item in rubric["rubrics"]:
        for chk in item["check"]:
            if "volume" in chk:
                assert abs(part.volume() - chk["volume"]) <= 5e-4 * chk["volume"]


def test_center_of_mass_check():
    rubric = {"case": "com_box", "entities": {"W": {"world_frame": {}}},
              "rubrics": [{"id": "com", "check": [{"center_of_mass": {"frame": "W", "expect": [10, None, 5]}}]}]}
    solid = box(0, 0, 0, 20, 10, 10)
    assert _failed(evaluate(rubric, Part(solid))) == set()
    assert _failed(evaluate(rubric, Part(cut(solid, cyl((15, 5, -1), (0, 0, 1), 3, 12))))) == {"com"}


@pytest.mark.parametrize("case,label,build,expected", MUTANTS, ids=[f"{c[0]}-{c[1]}" for c in MUTANTS])
def test_mutant(case, label, build, expected):
    assert _failed(evaluate(load_rubric(RUBRICS / f"{case}.yaml"), Part(build()))) == expected
