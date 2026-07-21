"""Build and score a cordless drill with planetary gears and a 3-jaw chuck."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.eval_two_stage_gearbox_multiturn import SwApi
from load_env import ensure_test_env_loaded
from planner.agent_loop import MultiTurnAgent
from planner.executor import SwExecutor
from planner.llm_client import SwLLMClient
from planner.schemas import (
    Milestone,
    ModelObservation,
    PostconditionSpec,
    TaskPlan,
)
from planner.validators import ValidatorRegistry


SPEC_PATH = Path(__file__).with_name("cordless_drill_planetary_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "cordless_drill"
ROLES = (
    "shell_left",
    "shell_right",
    "battery_pack",
    "trigger",
    "direction_switch",
    "motor_stator",
    "motor_rotor",
    "ring_stage1",
    "sun_stage1",
    "planet1_a",
    "planet1_b",
    "planet1_c",
    "carrier_stage1",
    "ring_stage2",
    "sun_stage2",
    "planet2_a",
    "planet2_b",
    "planet2_c",
    "carrier_stage2",
    "spindle",
    "front_bearing",
    "torque_collar",
    "chuck_body",
    "jaw_1",
    "jaw_2",
    "jaw_3",
    "drill_bit",
)


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {role: output_dir / f"{role}.SLDPRT" for role in ROLES}


def _save_extrusion(
    api: SwApi,
    path: Path,
    depth: float,
) -> None:
    api.call("/api/feature/extrusion", {"depth": depth})
    api.call("/api/save-part-as", {"savePath": str(path)})


def _build_front_profile(
    api: SwApi,
    path: Path,
    points: list[tuple[float, float]],
    *,
    depth: float,
) -> None:
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 0})
    api.call(
        "/api/sketch/polyline",
        {
            "points": [{"x": x, "y": y} for x, y in points],
            "closed": True,
        },
    )
    _save_extrusion(api, path, depth)


def _build_front_rectangle(
    api: SwApi,
    path: Path,
    bounds: tuple[float, float, float, float],
    *,
    depth: float,
) -> None:
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 0})
    api.call(
        "/api/sketch/rectangle",
        {
            "x1": bounds[0],
            "y1": bounds[1],
            "x2": bounds[2],
            "y2": bounds[3],
        },
    )
    _save_extrusion(api, path, depth)


def _build_axis_x_round(
    api: SwApi,
    path: Path,
    *,
    radius: float,
    depth: float,
    bore_radius: float | None = None,
) -> None:
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 2})
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "cz": 0, "radius": radius},
    )
    if bore_radius:
        api.call(
            "/api/sketch/circle",
            {
                "cx": 0,
                "cy": 0,
                "cz": 0,
                "radius": bore_radius,
            },
        )
    _save_extrusion(api, path, depth)


def _build_axis_x_carrier(
    api: SwApi,
    path: Path,
    *,
    orbit_radius: float,
    depth: float,
) -> None:
    del orbit_radius
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 2})
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "cz": 0, "radius": 0.026},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "cz": 0, "radius": 0.003},
    )
    _save_extrusion(api, path, depth)


def _build_axis_x_gear(
    api: SwApi,
    path: Path,
    *,
    teeth: int,
    module_m: float,
    pressure_angle_deg: float,
    thickness_m: float,
    bore_radius_m: float,
    phase_angle_deg: float = 0.0,
) -> None:
    del pressure_angle_deg
    tip_radius = module_m * (teeth + 2) / 2
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 2})
    api.call(
        "/api/sketch/polygon",
        {
            "cx": 0,
            "cy": 0,
            "cz": 0,
            "circumradius": tip_radius,
            "sides": min(64, max(12, teeth)),
            "startAngleDeg": phase_angle_deg,
        },
    )
    api.call(
        "/api/sketch/circle",
        {
            "cx": 0,
            "cy": 0,
            "cz": 0,
            "radius": bore_radius_m,
        },
    )
    _save_extrusion(api, path, thickness_m)


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    standard = spec["gear_standard"]
    gear_args = {
        "module_m": float(standard["module"]) / 1000,
        "pressure_angle_deg": float(standard["pressure_angle"]),
        "thickness_m": float(standard["face_width"]) / 1000,
        "bore_radius_m": float(standard["bore_diameter"]) / 2000,
    }
    shell_profile = [
        (-0.125, 0.045),
        (0.065, 0.045),
        (0.095, 0.025),
        (0.095, -0.025),
        (-0.020, -0.035),
        (-0.030, -0.145),
        (-0.095, -0.145),
        (-0.085, -0.035),
        (-0.125, -0.025),
    ]
    builders = {
        "shell_left": lambda p: _build_front_profile(
            api, p, shell_profile, depth=0.005
        ),
        "shell_right": lambda p: _build_front_profile(
            api, p, shell_profile, depth=0.005
        ),
        "battery_pack": lambda p: _build_front_rectangle(
            api,
            p,
            (-0.105, -0.180, -0.015, -0.145),
            depth=0.060,
        ),
        "trigger": lambda p: _build_front_rectangle(
            api,
            p,
            (-0.035, -0.055, -0.012, -0.030),
            depth=0.024,
        ),
        "direction_switch": lambda p: _build_front_rectangle(
            api,
            p,
            (-0.045, -0.020, -0.015, -0.010),
            depth=0.016,
        ),
        "motor_stator": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.028,
            bore_radius=0.010,
            depth=0.050,
        ),
        "motor_rotor": lambda p: _build_axis_x_round(
            api, p, radius=0.008, depth=0.065
        ),
        "ring_stage1": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.034,
            bore_radius=0.029,
            depth=0.008,
        ),
        "sun_stage1": lambda p: _build_axis_x_gear(
            api, p, teeth=12, **gear_args
        ),
        "planet1_a": lambda p: _build_axis_x_gear(
            api, p, teeth=18, phase_angle_deg=2.0, **gear_args
        ),
        "planet1_b": lambda p: _build_axis_x_gear(
            api, p, teeth=18, phase_angle_deg=2.0, **gear_args
        ),
        "planet1_c": lambda p: _build_axis_x_gear(
            api, p, teeth=18, phase_angle_deg=2.0, **gear_args
        ),
        "carrier_stage1": lambda p: _build_axis_x_carrier(
            api, p, orbit_radius=0.018, depth=0.006
        ),
        "ring_stage2": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.032,
            bore_radius=0.027,
            depth=0.008,
        ),
        "sun_stage2": lambda p: _build_axis_x_gear(
            api, p, teeth=15, phase_angle_deg=1.0, **gear_args
        ),
        "planet2_a": lambda p: _build_axis_x_gear(
            api, p, teeth=15, phase_angle_deg=1.0, **gear_args
        ),
        "planet2_b": lambda p: _build_axis_x_gear(
            api, p, teeth=15, phase_angle_deg=1.0, **gear_args
        ),
        "planet2_c": lambda p: _build_axis_x_gear(
            api, p, teeth=15, phase_angle_deg=1.0, **gear_args
        ),
        "carrier_stage2": lambda p: _build_axis_x_carrier(
            api, p, orbit_radius=0.018, depth=0.006
        ),
        "spindle": lambda p: _build_axis_x_round(
            api, p, radius=0.006, depth=0.075
        ),
        "front_bearing": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.017,
            bore_radius=0.0065,
            depth=0.010,
        ),
        "torque_collar": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.031,
            bore_radius=0.018,
            depth=0.012,
        ),
        "chuck_body": lambda p: _build_axis_x_round(
            api,
            p,
            radius=0.026,
            bore_radius=0.006,
            depth=0.047,
        ),
        "jaw_1": lambda p: _build_front_rectangle(
            api, p, (-0.010, -0.004, 0.010, 0.004), depth=0.006
        ),
        "jaw_2": lambda p: _build_front_rectangle(
            api, p, (-0.010, -0.004, 0.010, 0.004), depth=0.006
        ),
        "jaw_3": lambda p: _build_front_rectangle(
            api, p, (-0.010, -0.004, 0.010, 0.004), depth=0.006
        ),
        "drill_bit": lambda p: _build_axis_x_round(
            api, p, radius=0.003, depth=0.120
        ),
    }
    build_log: list[dict] = []
    api.close_all()
    for role in ROLES:
        path = paths[role]
        started = time.perf_counter()
        if force or not path.is_file():
            builders[role](path)
            status = "built"
        else:
            status = "reused"
        build_log.append({
            "role": role,
            "path": str(path),
            "status": status,
            "duration_seconds": round(time.perf_counter() - started, 3),
        })
        api.close_all()
    return paths, build_log


def _positions(
    spec: dict,
    *,
    groups: set[str] | None = None,
) -> list[dict]:
    return [
        {
            "path_contains": item["role"],
            "translation_mm": item["translation"],
        }
        for item in spec["components"]
        if groups is None or item["group"] in groups
    ]


def _user_goal(
    paths: dict[str, Path],
    spec: dict,
    assembly_path: Path,
) -> str:
    by_role = {item["role"]: item for item in spec["components"]}
    lines = [
        "创建一台双级固定齿圈行星减速、三爪自定心夹头的充电式手电钻装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下 27 个零件已经参数化建好。按坐标插入，mm 必须换算为 m：",
    ]
    for role in ROLES:
        item = by_role[role]
        lines.append(
            f"- {role}: {paths[role]}，位置 {item['translation']} mm，"
            f"分组 {item['group']}"
        )
    lines.extend([
        "共同旋转轴为世界 +X，参考径向为 +Y。第一级 Ns/Nr/Np=12/48/18，"
        "轨道半径 18mm、轴向 -38mm、行星架 -27mm；第二级为 15/45/15，"
        "轨道半径 18mm、轴向 -20mm、行星架 -9mm，总减速比 20:1。",
        "最终使用 assembly_set_planetary_train_pose；params 顶层必须包含 "
        "centerX/centerY/centerZ、inputAngleDeg、stages、outputComponents。"
        "每级 stages 项使用 sunComponent、carrierComponent、sunTeeth、"
        "ringTeeth、planetTeeth、orbitRadius、axialOffset、"
        "carrierAxialOffset、planetComponents。",
        "三爪中心 [82,0,0]mm，初始中心半径 17mm，方位 0/120/240°，"
        "jaw bodyLength=20mm。使用 assembly_set_radial_sliders_pose；params "
        "顶层使用 centerX/centerY/centerZ、radialDistance、sliders。",
        "使用 observation 中的真实组件实例名；固定 shell_left，重建并保存。"
        "装配过程中关闭不用的零件窗口。",
    ])
    return "\n".join(lines)


def _milestone(
    identifier: str,
    title: str,
    exact_count: int,
    groups: set[str],
    spec: dict,
    *,
    depends_on: list[str] | None = None,
) -> Milestone:
    return Milestone(
        id=identifier,
        title=title,
        depends_on=depends_on or [],
        postconditions=[
            PostconditionSpec(
                kind="component_count",
                params={"exact": exact_count},
            ),
            PostconditionSpec(
                kind="component_positions",
                params={
                    "expected": _positions(spec, groups=groups),
                    "tolerance_mm": 1.0,
                },
            ),
        ],
        max_actions=5,
        max_failures=6,
    )


def _task_plan(
    goal: str,
    spec: dict,
    assembly_path: Path,
) -> TaskPlan:
    return TaskPlan(
        user_goal=goal,
        milestones=[
            _milestone(
                "drill_foundation",
                "新建装配体并插入左右壳体、电池和扳机",
                4,
                {"foundation"},
                spec,
            ),
            _milestone(
                "drill_drive",
                "插入换向开关、电机定转子和第一级齿圈",
                8,
                {"foundation", "drive"},
                spec,
                depends_on=["drill_foundation"],
            ),
            _milestone(
                "drill_stage1",
                "插入第一级太阳轮和三个行星轮",
                12,
                {"foundation", "drive", "stage1"},
                spec,
                depends_on=["drill_drive"],
            ),
            _milestone(
                "drill_transition",
                "插入一级行星架、二级齿圈太阳轮和首个行星轮",
                16,
                {"foundation", "drive", "stage1", "transition"},
                spec,
                depends_on=["drill_stage1"],
            ),
            _milestone(
                "drill_stage2",
                "完成第二级行星轮和输出行星架",
                19,
                {
                    "foundation",
                    "drive",
                    "stage1",
                    "transition",
                    "stage2",
                },
                spec,
                depends_on=["drill_transition"],
            ),
            _milestone(
                "drill_output",
                "插入主轴、前轴承、扭矩环和夹头本体",
                23,
                {
                    "foundation",
                    "drive",
                    "stage1",
                    "transition",
                    "stage2",
                    "output",
                },
                spec,
                depends_on=["drill_stage2"],
            ),
            _milestone(
                "drill_tooling",
                "插入三个夹爪和钻头",
                27,
                {
                    "foundation",
                    "drive",
                    "stage1",
                    "transition",
                    "stage2",
                    "output",
                    "tooling",
                },
                spec,
                depends_on=["drill_output"],
            ),
            Milestone(
                id="drill_pose_save",
                title="设置双级行星零位和三爪半开姿态，固定壳体、重建并保存",
                depends_on=["drill_tooling"],
                postconditions=[
                    PostconditionSpec(kind="valid_assembly"),
                    PostconditionSpec(
                        kind="assembly_saved",
                        params={"path": str(assembly_path)},
                    ),
                ],
                max_actions=5,
                max_failures=6,
            ),
        ],
        notes="产品参数和评分仅存在于 eval；生产 Skill 只含通用机构规则。",
        recovery_buffer=14,
    )


def _acceptance_specs(
    spec: dict,
    assembly_path: Path,
) -> list[PostconditionSpec]:
    return [
        PostconditionSpec(kind="valid_assembly"),
        PostconditionSpec(
            kind="component_count",
            params={"exact": int(spec["acceptance"]["component_count"])},
        ),
        PostconditionSpec(
            kind="assembly_saved",
            params={"path": str(assembly_path)},
        ),
    ]


def _observation(api: SwApi) -> ModelObservation:
    result = api.get("/api/assembly/observation")["result"]
    return ModelObservation(
        document_type="assembly",
        assembly_path=str(result.get("assemblyPath", "")),
        components=result.get("components", []),
        mates=result.get("mates", []),
        interference_summary={},
    )


def _resolve_components(observation: ModelObservation) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for component in observation.components:
        path = str(component.get("path", "")).lower()
        name = str(component.get("name", ""))
        for role in ROLES:
            if role in path:
                resolved[role] = name
    missing = sorted(set(ROLES) - set(resolved))
    if missing:
        raise RuntimeError(f"无法解析手电钻组件实例名: {missing}")
    return resolved


def _planetary_payload(
    names: dict[str, str],
    spec: dict,
    input_angle_deg: float,
) -> dict:
    mechanism = spec["planetary"]
    center = [value / 1000 for value in mechanism["center"]]
    stage_1, stage_2 = mechanism["stages"]
    return {
        "centerX": center[0],
        "centerY": center[1],
        "centerZ": center[2],
        "axisX": mechanism["axis"][0],
        "axisY": mechanism["axis"][1],
        "axisZ": mechanism["axis"][2],
        "referenceRadialX": mechanism["reference_radial"][0],
        "referenceRadialY": mechanism["reference_radial"][1],
        "referenceRadialZ": mechanism["reference_radial"][2],
        "inputAngleDeg": input_angle_deg,
        "stages": [
            {
                "sunComponent": names["sun_stage1"],
                "carrierComponent": names["carrier_stage1"],
                "sunTeeth": stage_1["sun_teeth"],
                "ringTeeth": stage_1["ring_teeth"],
                "planetTeeth": stage_1["planet_teeth"],
                "orbitRadius": stage_1["orbit_radius"] / 1000,
                "axialOffset": stage_1["axial_offset"] / 1000,
                "carrierAxialOffset": (
                    stage_1["carrier_axial_offset"] / 1000
                ),
                "planetComponents": [
                    {
                        "componentName": names[f"planet1_{letter}"],
                        "phaseDeg": phase,
                    }
                    for letter, phase in zip(
                        ("a", "b", "c"),
                        stage_1["planet_phases_deg"],
                    )
                ],
            },
            {
                "sunComponent": names["sun_stage2"],
                "carrierComponent": names["carrier_stage2"],
                "sunTeeth": stage_2["sun_teeth"],
                "ringTeeth": stage_2["ring_teeth"],
                "planetTeeth": stage_2["planet_teeth"],
                "orbitRadius": stage_2["orbit_radius"] / 1000,
                "axialOffset": stage_2["axial_offset"] / 1000,
                "carrierAxialOffset": (
                    stage_2["carrier_axial_offset"] / 1000
                ),
                "planetComponents": [
                    {
                        "componentName": names[f"planet2_{letter}"],
                        "phaseDeg": phase,
                    }
                    for letter, phase in zip(
                        ("a", "b", "c"),
                        stage_2["planet_phases_deg"],
                    )
                ],
            },
        ],
        "outputComponents": [
            {
                "componentName": names["motor_rotor"],
                "source": "input",
            },
            {"componentName": names["spindle"]},
            {"componentName": names["chuck_body"]},
            {"componentName": names["drill_bit"]},
        ],
    }


def _jaw_radius_for_control(spec: dict, opening_percent: float) -> float:
    poses = sorted(
        spec["chuck"]["poses"],
        key=lambda item: float(item["opening_percent"]),
    )
    value = max(0.0, min(float(opening_percent), 100.0))
    for left, right in zip(poses, poses[1:]):
        low = float(left["opening_percent"])
        high = float(right["opening_percent"])
        if low <= value <= high:
            ratio = (value - low) / (high - low) if high > low else 0
            return float(left["jaw_center_radius"]) + ratio * (
                float(right["jaw_center_radius"])
                - float(left["jaw_center_radius"])
            )
    return float(poses[-1]["jaw_center_radius"])


def _chuck_payload(
    names: dict[str, str],
    spec: dict,
    jaw_center_radius_mm: float,
    *,
    phase_offset_deg: float = 0.0,
) -> dict:
    chuck = spec["chuck"]
    center = [value / 1000 for value in chuck["center"]]
    return {
        "centerX": center[0],
        "centerY": center[1],
        "centerZ": center[2],
        "axisX": chuck["axis"][0],
        "axisY": chuck["axis"][1],
        "axisZ": chuck["axis"][2],
        "referenceRadialX": chuck["reference_radial"][0],
        "referenceRadialY": chuck["reference_radial"][1],
        "referenceRadialZ": chuck["reference_radial"][2],
        "radialDistance": jaw_center_radius_mm / 1000,
        "sliders": [
            {
                "componentName": names[f"jaw_{index}"],
                "phaseDeg": phase_offset_deg + (index - 1) * 120,
                "bodyLength": chuck["jaw_body_length"] / 1000,
            }
            for index in range(1, 4)
        ],
    }


def validate_mechanisms(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    acceptance = spec["acceptance"]
    planetary_samples = []
    for input_angle in spec["planetary"]["input_samples_deg"]:
        result = api.call(
            "/api/assembly/mechanism/planetary-train-pose",
            _planetary_payload(names, spec, float(input_angle)),
        )["result"]
        stage_1, stage_2 = result["stages"]
        expected_stage_1 = float(input_angle) / 5.0
        expected_output = float(input_angle) / 20.0
        ratio_error = max(
            abs(float(stage_1["carrierAngleDeg"]) - expected_stage_1),
            abs(float(stage_2["carrierAngleDeg"]) - expected_output),
            abs(float(result["overallReductionRatio"]) - 20.0),
        )
        orbit_error_mm = (
            float(result["maximumOrbitRadiusError"]) * 1000
        )
        orientation_error = float(
            result["maximumOrientationErrorDeg"]
        )
        passed = (
            ratio_error <= float(
                acceptance["gear_ratio_tolerance"]
            )
            and orbit_error_mm <= float(
                acceptance["orbit_radius_tolerance_mm"]
            )
            and orientation_error <= float(
                acceptance["orientation_tolerance_deg"]
            )
        )
        api.call(
            "/api/assembly/mechanism/radial-sliders-pose",
            _chuck_payload(
                names,
                spec,
                _jaw_radius_for_control(spec, 50),
                phase_offset_deg=float(result["outputAngleDeg"]),
            ),
        )
        planetary_samples.append({
            "input_angle_deg": input_angle,
            "stage1_carrier_angle_deg": stage_1["carrierAngleDeg"],
            "stage2_carrier_angle_deg": stage_2["carrierAngleDeg"],
            "output_angle_deg": result["outputAngleDeg"],
            "overall_reduction_ratio": result[
                "overallReductionRatio"
            ],
            "maximum_orbit_error_mm": orbit_error_mm,
            "maximum_orientation_error_deg": orientation_error,
            "passed": passed,
        })

    chuck_samples = []
    bit_radius = float(spec["chuck"]["drill_bit_radius"])
    for pose in spec["chuck"]["poses"]:
        result = api.call(
            "/api/assembly/mechanism/radial-sliders-pose",
            _chuck_payload(
                names,
                spec,
                float(pose["jaw_center_radius"]),
            ),
        )["result"]
        inner_radii_mm = [
            float(item["innerTipRadius"]) * 1000
            for item in result["sliders"]
        ]
        spread_mm = float(result["innerRadiusSpread"]) * 1000
        position_error_mm = (
            float(result["maximumPositionError"]) * 1000
        )
        orientation_error = float(
            result["maximumOrientationErrorDeg"]
        )
        clearance_mm = min(inner_radii_mm) - bit_radius
        passed = (
            len(result["sliders"]) == 3
            and spread_mm <= float(
                acceptance["jaw_symmetry_tolerance_mm"]
            )
            and position_error_mm <= float(
                acceptance["jaw_position_tolerance_mm"]
            )
            and orientation_error <= float(
                acceptance["orientation_tolerance_deg"]
            )
            and clearance_mm >= -1e-6
        )
        chuck_samples.append({
            "name": pose["name"],
            "opening_percent": pose["opening_percent"],
            "jaw_center_radius_mm": pose["jaw_center_radius"],
            "inner_tip_radius_mm": sum(inner_radii_mm) / 3,
            "bit_clearance_mm": clearance_mm,
            "symmetry_spread_mm": spread_mm,
            "maximum_position_error_mm": position_error_mm,
            "maximum_orientation_error_deg": orientation_error,
            "passed": passed,
        })
    jaw_motion_monotonic = all(
        float(left["inner_tip_radius_mm"])
        < float(right["inner_tip_radius_mm"])
        for left, right in zip(chuck_samples, chuck_samples[1:])
    )
    api.call(
        "/api/assembly/mechanism/planetary-train-pose",
        _planetary_payload(names, spec, 0.0),
    )
    api.call(
        "/api/assembly/mechanism/radial-sliders-pose",
        _chuck_payload(
            names,
            spec,
            _jaw_radius_for_control(spec, 50),
        ),
    )
    api.call("/api/assembly/save")
    return {
        "passed": (
            all(sample["passed"] for sample in planetary_samples)
            and all(sample["passed"] for sample in chuck_samples)
            and jaw_motion_monotonic
        ),
        "planetary_samples": planetary_samples,
        "chuck_samples": chuck_samples,
        "jaw_motion_monotonic": jaw_motion_monotonic,
        "expected_total_ratio": 20.0,
    }


def score_result(
    *,
    status: str,
    static_passed: bool,
    mechanisms: dict,
    successful_actions: int,
    total_actions: int,
) -> float:
    static_points = 40.0 if static_passed else 0.0
    planetary = mechanisms.get("planetary_samples") or []
    chuck = mechanisms.get("chuck_samples") or []
    all_samples = [*planetary, *chuck]
    sample_rate = (
        sum(bool(item.get("passed")) for item in all_samples)
        / len(all_samples)
        if all_samples
        else 0.0
    )
    functional_points = 35.0 * sample_rate
    if mechanisms.get("jaw_motion_monotonic"):
        functional_points += 5.0
    action_rate = (
        successful_actions / total_actions if total_actions else 0.0
    )
    execution_points = 15.0 * action_rate
    if status == "completed":
        execution_points += 5.0
    return round(
        static_points + functional_points + execution_points,
        2,
    )


def validate_existing(
    api: SwApi,
    spec: dict,
    assembly_path: Path,
) -> dict:
    api.call("/api/assembly/open", {"assemblyPath": str(assembly_path)})
    observation = _observation(api)
    static = ValidatorRegistry.validate(
        _acceptance_specs(spec, assembly_path),
        observation,
    )
    mechanisms = validate_mechanisms(api, spec, observation)
    passed = static.passed and mechanisms["passed"]
    status = "completed" if passed else "blocked"
    return {
        "status": status,
        "passed": passed,
        "score": score_result(
            status=status,
            static_passed=static.passed,
            mechanisms=mechanisms,
            successful_actions=0,
            total_actions=0,
        ),
        "static_acceptance": static.model_dump(),
        "mechanism_acceptance": mechanisms,
        "token_usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
        },
        "final_observation": observation.model_dump(),
    }


def run_agent(
    sw_url: str,
    paths: dict[str, Path],
    spec: dict,
    output_dir: Path,
    *,
    model: str | None,
    use_cache: bool,
    max_turns: int,
) -> dict:
    ensure_test_env_loaded()
    assembly_path = output_dir / "cordless_drill.SLDASM"
    goal = _user_goal(paths, spec, assembly_path)
    task_plan = _task_plan(goal, spec, assembly_path)
    client = SwLLMClient(
        plan_model=model,
        use_cache=use_cache,
        skill_mode="routed",
    )
    executor = SwExecutor(base_url=sw_url)
    api = SwApi(sw_url)
    agent = MultiTurnAgent(client, executor, max_turns=max_turns)
    started = time.perf_counter()
    try:
        result = agent.run(goal, task_plan=task_plan)
        static = ValidatorRegistry.validate(
            _acceptance_specs(spec, assembly_path),
            result.final_observation,
        )
        mechanisms = (
            validate_mechanisms(
                api,
                spec,
                result.final_observation,
            )
            if result.status == "completed" and static.passed
            else {
                "passed": False,
                "planetary_samples": [],
                "chuck_samples": [],
            }
        )
        passed = (
            result.status == "completed"
            and static.passed
            and mechanisms["passed"]
        )
        return {
            "status": result.status,
            "passed": passed,
            "score": score_result(
                status=result.status,
                static_passed=static.passed,
                mechanisms=mechanisms,
                successful_actions=result.successful_actions,
                total_actions=result.total_actions,
            ),
            "static_acceptance": static.model_dump(),
            "mechanism_acceptance": mechanisms,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "token_usage": client.usage_snapshot(),
            "result": result.model_dump(),
            "task_plan": task_plan.model_dump(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "passed": False,
            "score": 0.0,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "token_usage": client.usage_snapshot(),
            "error": f"{type(exc).__name__}: {exc}",
            "task_plan": task_plan.model_dump(),
        }
    finally:
        client.close()
        executor.close()
        api.close_inactive()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=34)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name(
            "cordless_drill_planetary_result.json"
        ),
    )
    args = parser.parse_args()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    api = SwApi(args.sw_url)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    assembly_path = ARTIFACT_DIR / "cordless_drill.SLDASM"
    try:
        api.close_all()
        if args.validate_existing:
            result = validate_existing(api, spec, assembly_path)
            preparation = []
        else:
            paths, preparation = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=args.force_parts,
            )
            if args.prepare_only:
                result = {"status": "prepared", "passed": True}
            else:
                result = run_agent(
                    args.sw_url,
                    paths,
                    spec,
                    ARTIFACT_DIR,
                    model=args.model,
                    use_cache=args.use_cache,
                    max_turns=args.max_turns,
                )
        report = {
            "run_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "case_id": spec["case_id"],
            "preparation": preparation,
            **result,
        }
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"status={report.get('status')} "
            f"passed={report.get('passed')} "
            f"score={report.get('score')} "
            f"tokens={(report.get('token_usage') or {}).get('total_tokens')}"
        )
        if not report.get("passed"):
            raise SystemExit(1)
    finally:
        try:
            api.close_all()
        except Exception:
            pass


if __name__ == "__main__":
    main()
