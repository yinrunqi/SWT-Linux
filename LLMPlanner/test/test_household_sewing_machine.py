from __future__ import annotations

import json
import math
from pathlib import Path

from eval.eval_household_sewing_machine_multiturn import (
    GROUPS,
    ROLES,
    SPEC_PATH,
    _cam_follower_payload,
    _cam_lift_mm,
    _crank_slider_payload,
    _four_bar_geometry_mm,
    _four_bar_payload,
    _needle_distance_mm,
    _rotor_angle_deg,
    _synchronized_rotary_payload,
    score_result,
)
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.schemas import OperationType


def _spec() -> dict:
    return json.loads(Path(SPEC_PATH).read_text(encoding="utf-8"))


def _names() -> dict[str, str]:
    return {role: f"{role}-1" for role in ROLES}


def test_spec_has_exactly_40_roles_in_seven_ordered_groups() -> None:
    spec = _spec()
    components = spec["components"]
    assert len(components) == 40
    assert tuple(item["role"] for item in components) == ROLES
    assert tuple(dict.fromkeys(item["group"] for item in components)) == GROUPS
    assert set(item["group"] for item in components) == set(GROUPS)
    assert spec["assembly"]["component_count"] == 40
    assert spec["acceptance"]["component_count"] == 40


def test_needle_payload_and_analytic_cycle_are_periodic() -> None:
    spec = _spec()
    payload = _crank_slider_payload(_names(), spec, 90)
    assert payload["centerX"] == 0.045
    assert payload["centerY"] == 0.105
    assert payload["crankRadius"] == 0.018
    assert payload["rodLength"] == 0.065
    assert payload["sliderAxisY"] == -1
    assert math.isclose(
        _needle_distance_mm(spec, 0)
        - _needle_distance_mm(spec, 180),
        36.0,
        abs_tol=1e-9,
    )
    assert math.isclose(
        _needle_distance_mm(spec, 0),
        _needle_distance_mm(spec, 720),
        abs_tol=1e-9,
    )


def test_synchronized_rotary_payload_preserves_ratio_phase() -> None:
    spec = _spec()
    payload = _synchronized_rotary_payload(_names(), spec, 360)
    by_name = {
        item["componentName"]: item for item in payload["rotors"]
    }
    hook = by_name["rotary_hook-1"]
    assert hook["componentName"] == "rotary_hook-1"
    assert hook["ratio"] == 0.5
    assert hook["reverse"] is True
    assert hook["phaseDeg"] == 25.0
    assert (hook["x"], hook["y"], hook["z"]) == (0.075, 0.02, 0)
    rotor = next(
        item
        for item in spec["rotary_timing"]["rotors"]
        if item["role"] == "rotary_hook"
    )
    assert _rotor_angle_deg(rotor, 0) == 25
    assert _rotor_angle_deg(rotor, 360) == -695
    assert (
        _rotor_angle_deg(rotor, 720)
        - _rotor_angle_deg(rotor, 0)
    ) == -1440


def test_four_bar_payload_and_circle_intersection_close() -> None:
    spec = _spec()
    payload = _four_bar_payload(_names(), spec, 120)
    assert payload["groundAX"] == 0.01
    assert payload["groundBX"] == 0.07
    assert payload["inputLength"] == 0.022
    assert payload["couplerLength"] == 0.058
    assert payload["outputLength"] == 0.028
    assert payload["couplerPoint"] == {
        "fraction": 0.72,
        "normalOffset": 0.009,
    }
    assert [item["componentName"] for item in payload[
        "couplerPointComponents"
    ]] == ["feed_dog_bar-1", "feed_dog-1"]
    for angle in spec["feed_four_bar"]["sample_angles_deg"]:
        geometry = _four_bar_geometry_mm(spec, angle)
        crank = geometry["crank_joint"]
        rocker = geometry["rocker_joint"]
        assert math.isclose(math.dist(crank, rocker), 58, abs_tol=1e-9)
        assert math.isclose(
            math.dist(rocker, (70, 25)),
            28,
            abs_tol=1e-9,
        )
    first = _four_bar_geometry_mm(spec, 20)
    repeated = _four_bar_geometry_mm(spec, 380)
    assert math.dist(
        first["coupler_point"],
        repeated["coupler_point"],
    ) < 1e-9


def test_harmonic_cam_payload_and_lift_are_periodic() -> None:
    spec = _spec()
    payload = _cam_follower_payload(_names(), spec, 0)
    assert payload["centerX"] == 0.025
    assert payload["centerY"] == 0.082
    assert payload["driverComponent"] == "presser_cam-1"
    assert payload["baseRadius"] == 0.01
    assert payload["lift"] == 0.006
    assert payload["profile"] == "harmonic"
    assert math.isclose(
        _cam_lift_mm(spec, 0),
        _cam_lift_mm(spec, 360),
        abs_tol=1e-9,
    )
    assert math.isclose(
        _cam_lift_mm(spec, 150),
        6.0,
        abs_tol=1e-9,
    )


def test_new_mechanism_operation_endpoints_when_protocol_is_present() -> None:
    expected = {
        "ASSEMBLY_SET_SYNCHRONIZED_ROTARY_POSE": (
            "POST",
            "/api/assembly/mechanism/synchronized-rotary-pose",
        ),
        "ASSEMBLY_SET_FOUR_BAR_POSE": (
            "POST",
            "/api/assembly/mechanism/four-bar-pose",
        ),
        "ASSEMBLY_SET_CAM_FOLLOWER_POSE": (
            "POST",
            "/api/assembly/mechanism/cam-follower-pose",
        ),
    }
    for member_name, endpoint in expected.items():
        operation = getattr(OperationType, member_name, None)
        if operation is not None:
            assert OPERATION_ENDPOINT_MAP[operation] == endpoint


def test_full_sewing_machine_result_scores_one_hundred() -> None:
    mechanisms = {
        name: {
            "passed": True,
            "samples": [{"passed": True}] * 5,
            "cycle_closed": True,
        }
        for name in ("needle", "rotary", "four_bar", "cam")
    }
    assert score_result(
        status="completed",
        static_passed=True,
        mechanisms=mechanisms,
        successful_actions=40,
        total_actions=40,
    ) == 100.0
