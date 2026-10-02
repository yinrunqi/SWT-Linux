"""复杂综合件（showcase）30 道题：参考正例满分且体积与 rubric 一致；另一种读法满分；每个反例恰好在预期条目上失败。

运行：venv-occ\\Scripts\\python.exe -m pytest geomcheck/tests -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs_hard as R
from geomcheck.tests import refs_hard2 as R2

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "rubrics_hard"

HARD = ["quad_frame", "connecting_rod", "finned_housing", "sprocket", "handwheel", "camshaft",
        "planet_carrier", "impeller"]
HARD2 = ["crankshaft", "brake_disc", "wheel_rim", "geneva_wheel", "spiral_staircase", "engine_piston",
         "pipe_manifold", "gearbox_housing", "heat_sink", "honeycomb_panel", "gear_pump_body", "cold_plate",
         "truss_beam", "turbine_disk", "bt40_holder", "hydraulic_manifold", "rocket_fin_can", "ratchet_wheel",
         "injector_plate", "robot_arm_link", "menger_sponge", "u_joint_yoke"]

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
    ("crankshaft", "throws_in_phase", lambda: R2.crankshaft(phase_b=0),
     {"counterweights", "crankpins", "oil_holes", "phasing", "web_profile"}),
    ("crankshaft", "no_counterweight", lambda: R2.crankshaft(counterweight=False),
     {"counterweights", "phasing", "volume"}),
    ("crankshaft", "keyway_flipped", lambda: R2.crankshaft(keyway_side=0), {"keyway"}),
    ("crankshaft", "stroke_70", lambda: R2.crankshaft(pin_offset=35), {"crankpins", "oil_holes", "volume", "web_profile"}),
    ("brake_disc", "vanes_36", lambda: R2.brake_disc(n_vanes=36),
     {"cross_holes", "rings", "vane_phase", "vanes", "volume"}),
    ("brake_disc", "holes_on_vanes", lambda: R2.brake_disc(hole_phase=0), {"cross_holes", "vane_phase", "vanes"}),
    ("wheel_rim", "windows_40", lambda: R2.wheel_rim(window_deg=40), {"volume", "windows"}),
    ("wheel_rim", "no_fillet", lambda: R2.wheel_rim(fillet_r=0), {"window_fillets"}),
    ("wheel_rim", "valve_on_window", lambda: R2.wheel_rim(valve_deg=0),
     {"disc_and_hub", "spokes", "valve_hole", "windows"}),
    ("geneva_wheel", "lock_r_30", lambda: R2.geneva_wheel(lock_r=30), {"lightening_holes", "locking_arcs", "volume"}),
    ("geneva_wheel", "slot_end_36", lambda: R2.geneva_wheel(slot_end=36), {"slot_tips", "slots", "volume"}),
    ("spiral_staircase", "clockwise", lambda: R2.spiral_staircase(ccw=False),
     {"posts", "step_shape", "steps_high", "steps_low"}),
    ("spiral_staircase", "rise_18", lambda: R2.spiral_staircase(rise=18),
     {"height", "posts", "step_shape", "steps_high", "steps_low"}),
    ("engine_piston", "no_relief", lambda: R2.engine_piston(relief_depth=0), {"valve_reliefs", "volume"}),
    ("engine_piston", "pin_z_33", lambda: R2.engine_piston(pin_z=33), {"pin_bore", "pin_bosses"}),
    ("pipe_manifold", "branches_blind", lambda: R2.pipe_manifold(branch_open=False), {"branch_bores", "drain"}),
    ("pipe_manifold", "flange_bolts_on_axis", lambda: R2.pipe_manifold(flange_phase=0),
     {"end_flanges", "flange_bolts"}),
    ("gearbox_housing", "wall_6", lambda: R2.gearbox_housing(wall=6), {"bosses", "volume", "walls"}),
    ("gearbox_housing", "bore_spacing_90", lambda: R2.gearbox_housing(bores=((-45, 23.5), (45, 26))),
     {"bearing_bores", "bosses", "ribs"}),
    ("heat_sink", "cbore_2", lambda: R2.heat_sink(cbore_depth=2), {"counterbores"}),
    ("heat_sink", "pad_50", lambda: R2.heat_sink(pad=50), {"pad", "volume"}),
    ("honeycomb_panel", "hex_rotated", lambda: R2.honeycomb_panel(rot=0),
     {"hex_orientation", "middle_row", "offset_rows", "outer_rows"}),
    ("honeycomb_panel", "floor_5", lambda: R2.honeycomb_panel(floor=5), {"pocket_depth", "volume"}),
    ("gear_pump_body", "center_distance_44", lambda: R2.gear_pump_body(center=22),
     {"chamber", "dowels", "port_bosses", "ports", "shaft_holes", "tapped_holes"}),
    ("gear_pump_body", "inlet_flipped", lambda: R2.gear_pump_body(inlet_side=1), {"ports"}),
    ("cold_plate", "no_seal_groove", lambda: R2.cold_plate(groove=False), {"seal_groove", "volume"}),
    ("cold_plate", "depth_5", lambda: R2.cold_plate(depth=5), {"channel_depth", "volume"}),
    ("truss_beam", "no_fillet", lambda: R2.truss_beam(fillet_r=0), {"corner_fillets", "volume"}),
    ("truss_beam", "no_ribs", lambda: R2.truss_beam(rib=False), {"outer", "ribs", "volume"}),
    ("turbine_disk", "straight_slots", lambda: R2.turbine_disk(slot_bottom_w=10), {"dovetail", "outer", "volume"}),
    ("turbine_disk", "slots_on_holes", lambda: R2.turbine_disk(slot_phase=0),
     {"lightening_holes", "rim", "slots", "web"}),
    ("turbine_disk", "no_fillet", lambda: R2.turbine_disk(fillet_r=0), {"fillets", "volume"}),
    ("bt40_holder", "groove_5", lambda: R2.bt40_holder(groove_depth=5), {"flange", "v_groove"}),
    ("bt40_holder", "slot_bottom_20", lambda: R2.bt40_holder(slot_bottom=20), {"drive_slots", "volume"}),
    ("hydraulic_manifold", "gallery_short", lambda: R2.hydraulic_manifold(gallery_end=-10), {"a_port", "gallery"}),
    ("hydraulic_manifold", "a_port_x0", lambda: R2.hydraulic_manifold(a_port_x=0), {"a_port"}),
    ("rocket_fin_can", "fins_on_vents", lambda: R2.rocket_fin_can(fin_phase=0), {"fin_planform", "fins"}),
    ("rocket_fin_can", "span_50", lambda: R2.rocket_fin_can(tip_r=90), {"fin_planform", "fins", "volume"}),
    ("ratchet_wheel", "clockwise", lambda: R2.ratchet_wheel(ccw=False), {"tooth_direction"}),
    ("ratchet_wheel", "hex_vertex_to_hole", lambda: R2.ratchet_wheel(hex_rot=0), {"hex_bore", "tooth_direction"}),
    ("injector_plate", "groove_6", lambda: R2.injector_plate(groove_depth=6), {"grooves", "orifice_phase", "volume"}),
    ("injector_plate", "dowel_r50", lambda: R2.injector_plate(dowel_r=50), {"dowel", "orifice_rings"}),
    ("robot_arm_link", "no_slit", lambda: R2.robot_arm_link(slit=False), {"clamp", "outer"}),
    ("robot_arm_link", "taps_rotated", lambda: R2.robot_arm_link(tap_phase=0), {"shoulder", "taps"}),
    ("robot_arm_link", "pocket_8", lambda: R2.robot_arm_link(pocket_depth=8), {"pockets", "volume"}),
    ("menger_sponge", "level1_only", lambda: R2.menger_sponge(level2=False),
     {"level2_holes", "mixed_levels", "volume"}),
    ("menger_sponge", "two_axes", lambda: R2.menger_sponge(axes=(0, 1)),
     {"level1", "level2_holes", "mixed_levels", "volume"}),
    ("u_joint_yoke", "gap_40", lambda: R2.u_joint_yoke(gap=40), {"arms"}),
    ("u_joint_yoke", "no_keyway", lambda: R2.u_joint_yoke(keyway=False), {"bore_keyway"}),
]


def _failed(res):
    return {r["id"] for r in res["rubrics"] if not r["ok"]}


def test_every_case_has_rubric():
    assert {p.stem for p in RUBRICS.glob("*.yaml")} == set(HARD + HARD2)


@pytest.mark.parametrize("case", HARD + HARD2)
def test_reference(case):
    rubric = load_rubric(RUBRICS / f"{case}.yaml")
    part = Part((getattr(R, case, None) or getattr(R2, case))())
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
