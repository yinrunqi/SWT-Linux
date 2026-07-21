import json
from pathlib import Path

from eval.eval_cordless_drill_planetary_multiturn import (
    SPEC_PATH,
    _chuck_payload,
    _jaw_radius_for_control,
    _planetary_payload,
    score_result,
)
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.schemas import OperationType, SwOperation


def _spec() -> dict:
    return json.loads(Path(SPEC_PATH).read_text(encoding="utf-8"))


def test_planetary_operation_aliases_and_endpoint() -> None:
    operation = SwOperation(
        operation="assembly_set_planetary_train_pose",
        params={
            "center": [0, 0, 0],
            "axis": "x",
            "motorAngleDeg": 360,
            "gearStages": [{
                "sun": "sun-1",
                "carrier": "carrier-1",
                "sun_teeth": 12,
                "ring_teeth": 48,
                "planet_teeth": 18,
                "planetOrbitRadius": 0.018,
                "planets": ["p1-1", "p2-1", "p3-1"],
            }],
            "outputs": ["spindle-1", "chuck-1"],
        },
    )
    payload = operation.params
    assert payload["centerX"] == 0
    assert payload["axisX"] == 1
    assert payload["inputAngleDeg"] == 360
    assert payload["stages"][0]["sunComponent"] == "sun-1"
    assert payload["stages"][0]["planetComponents"][1] == {
        "componentName": "p2-1",
        "phaseDeg": 120.0,
    }
    assert payload["outputComponents"] == [
        {"componentName": "spindle-1"},
        {"componentName": "chuck-1"},
    ]
    assert OPERATION_ENDPOINT_MAP[
        OperationType.ASSEMBLY_SET_PLANETARY_TRAIN_POSE
    ] == ("POST", "/api/assembly/mechanism/planetary-train-pose")


def test_radial_slider_aliases_and_endpoint() -> None:
    operation = SwOperation(
        operation="assembly_set_radial_sliders_pose",
        params={
            "chuckCenter": [0.082, 0, 0],
            "axis": "x",
            "jawCenterRadius": 0.017,
            "jaws": ["jaw-1", "jaw-2", "jaw-3"],
        },
    )
    payload = operation.params
    assert payload["centerX"] == 0.082
    assert payload["radialDistance"] == 0.017
    assert [item["phaseDeg"] for item in payload["sliders"]] == [
        0,
        120,
        240,
    ]
    assert OPERATION_ENDPOINT_MAP[
        OperationType.ASSEMBLY_SET_RADIAL_SLIDERS_POSE
    ] == ("POST", "/api/assembly/mechanism/radial-sliders-pose")


def test_drill_payload_declares_two_stage_twenty_to_one_train() -> None:
    names = {role: f"{role}-1" for role in (
        "sun_stage1",
        "carrier_stage1",
        "planet1_a",
        "planet1_b",
        "planet1_c",
        "sun_stage2",
        "carrier_stage2",
        "planet2_a",
        "planet2_b",
        "planet2_c",
        "motor_rotor",
        "spindle",
        "chuck_body",
        "drill_bit",
    )}
    payload = _planetary_payload(names, _spec(), 400)
    assert len(payload["stages"]) == 2
    assert payload["stages"][0]["ringTeeth"] == 48
    assert payload["stages"][1]["ringTeeth"] == 45
    first = payload["stages"][0]["sunTeeth"] + payload["stages"][0][
        "ringTeeth"
    ]
    second = payload["stages"][1]["sunTeeth"] + payload["stages"][1][
        "ringTeeth"
    ]
    assert first / payload["stages"][0]["sunTeeth"] == 5
    assert second / payload["stages"][1]["sunTeeth"] == 4


def test_chuck_control_and_payload_keep_threefold_symmetry() -> None:
    spec = _spec()
    assert _jaw_radius_for_control(spec, 0) == 13.5
    assert _jaw_radius_for_control(spec, 25) == 15.25
    assert _jaw_radius_for_control(spec, 100) == 22
    names = {f"jaw_{index}": f"jaw-{index}" for index in range(1, 4)}
    payload = _chuck_payload(
        names,
        spec,
        17,
        phase_offset_deg=18,
    )
    assert [item["phaseDeg"] for item in payload["sliders"]] == [
        18,
        138,
        258,
    ]
    assert all(item["bodyLength"] == 0.02 for item in payload["sliders"])


def test_full_drill_result_scores_one_hundred() -> None:
    score = score_result(
        status="completed",
        static_passed=True,
        mechanisms={
            "planetary_samples": [{"passed": True}] * 4,
            "chuck_samples": [{"passed": True}] * 3,
            "jaw_motion_monotonic": True,
        },
        successful_actions=32,
        total_actions=32,
    )
    assert score == 100
