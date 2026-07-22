"""Build and evaluate a household sewing machine with four mechanisms."""

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

from eval.eval_single_cylinder_engine_multiturn import (
    SwApi,
    _build_rectangles,
    _build_round_part,
    _save_extrusion,
    _start_sketch,
)
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


SPEC_PATH = Path(__file__).with_name("household_sewing_machine_spec.json")
ARTIFACT_DIR = (
    Path(__file__).parent / "artifacts" / "household_sewing_machine"
)
ASSEMBLY_NAME = "household_sewing_machine.SLDASM"

GROUPS = (
    "foundation",
    "drive",
    "needle_train",
    "hook_bobbin",
    "feed",
    "takeup_presser",
    "controls",
)

ROLES = (
    "base_plate",
    "column",
    "arm_body",
    "bed_cover",
    "stitch_plate",
    "arm_cover",
    "hand_wheel",
    "main_shaft",
    "motor_pulley",
    "motor_rotor",
    "upper_timing_gear",
    "lower_timing_gear",
    "needle_crank",
    "needle_connecting_rod",
    "needle_bar",
    "needle_bar_guide",
    "needle_clamp",
    "needle",
    "face_plate",
    "lower_shaft",
    "hook_drive_gear",
    "rotary_hook",
    "bobbin_case",
    "bobbin",
    "hook_cover",
    "feed_crank",
    "feed_coupler",
    "feed_rocker",
    "feed_dog_bar",
    "feed_dog",
    "feed_adjuster",
    "takeup_eccentric",
    "takeup_link",
    "takeup_lever",
    "presser_cam",
    "presser_bar",
    "presser_foot",
    "tension_dial",
    "reverse_lever",
    "spool_pin",
)


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {role: output_dir / f"{role}.SLDPRT" for role in ROLES}


def _build_link(
    api: SwApi,
    path: Path,
    *,
    length: float = 0.055,
    width: float = 0.009,
    depth: float = 0.006,
) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/rectangle",
        {
            "x1": -length / 2,
            "y1": -width / 2,
            "x2": length / 2,
            "y2": width / 2,
        },
    )
    _save_extrusion(api, path, depth)


def _build_needle(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.0012, "y": 0.018},
                {"x": 0.0012, "y": 0.018},
                {"x": 0.0012, "y": -0.016},
                {"x": 0, "y": -0.022},
                {"x": -0.0012, "y": -0.016},
            ],
            "closed": True,
        },
    )
    _save_extrusion(api, path, 0.002)


def _build_hook(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.018, "y": -0.012},
                {"x": 0.014, "y": -0.012},
                {"x": 0.023, "y": 0},
                {"x": 0.010, "y": 0.004},
                {"x": 0.004, "y": 0.016},
                {"x": -0.018, "y": 0.012},
            ],
            "closed": True,
        },
    )
    api.call(
        "/api/sketch/circle",
        {"cx": -0.004, "cy": 0, "radius": 0.006},
    )
    _save_extrusion(api, path, 0.006)


def _build_feed_dog(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.018, "y": -0.006},
                {"x": 0.018, "y": -0.006},
                {"x": 0.018, "y": 0.006},
                {"x": 0.015, "y": 0.010},
                {"x": 0.012, "y": 0.006},
                {"x": 0.009, "y": 0.010},
                {"x": 0.006, "y": 0.006},
                {"x": 0.003, "y": 0.010},
                {"x": 0.000, "y": 0.006},
                {"x": -0.003, "y": 0.010},
                {"x": -0.006, "y": 0.006},
                {"x": -0.009, "y": 0.010},
                {"x": -0.012, "y": 0.006},
                {"x": -0.015, "y": 0.010},
                {"x": -0.018, "y": 0.006},
            ],
            "closed": True,
        },
    )
    _save_extrusion(api, path, 0.004)


def _build_presser_foot(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.014, "y": -0.010},
                {"x": 0.014, "y": -0.010},
                {"x": 0.014, "y": 0.006},
                {"x": 0.004, "y": 0.006},
                {"x": 0.004, "y": 0.012},
                {"x": -0.004, "y": 0.012},
                {"x": -0.004, "y": 0.006},
                {"x": -0.014, "y": 0.006},
            ],
            "closed": True,
        },
    )
    _save_extrusion(api, path, 0.006)


def _build_lever(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.006, "y": -0.025},
                {"x": 0.006, "y": -0.025},
                {"x": 0.006, "y": 0.010},
                {"x": 0.018, "y": 0.020},
                {"x": 0.012, "y": 0.027},
                {"x": -0.006, "y": 0.014},
            ],
            "closed": True,
        },
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.0025},
    )
    _save_extrusion(api, path, 0.005)


def _build_by_kind(api: SwApi, path: Path, kind: str) -> None:
    """Build a deterministic, simple silhouette appropriate to a part kind."""
    rectangles = {
        "base_plate": (-0.125, -0.055, 0.125, 0.055, 0.010),
        "column": (-0.025, -0.070, 0.025, 0.070, 0.030),
        "arm_body": (-0.095, -0.025, 0.095, 0.025, 0.035),
        "bed_cover": (-0.100, -0.040, 0.100, 0.040, 0.005),
        "stitch_plate": (-0.032, -0.022, 0.032, 0.022, 0.003),
        "arm_cover": (-0.085, -0.021, 0.085, 0.021, 0.004),
        "motor": (-0.024, -0.018, 0.024, 0.018, 0.045),
        "guide": (-0.012, -0.020, 0.012, 0.020, 0.008),
        "clamp": (-0.010, -0.007, 0.010, 0.007, 0.008),
        "cover": (-0.028, -0.022, 0.028, 0.022, 0.003),
        "feed_bar": (-0.035, -0.006, 0.035, 0.006, 0.006),
        "presser_foot": (-0.014, -0.010, 0.014, 0.012, 0.006),
    }
    round_parts = {
        "wheel": (0.040, 0.008, 0.007),
        "shaft": (0.006, 0.090, None),
        "pulley": (0.018, 0.010, 0.005),
        "gear": (0.021, 0.008, 0.005),
        "crank": (0.018, 0.007, 0.004),
        "needle_bar": (0.004, 0.055, None),
        "bobbin_case": (0.018, 0.008, 0.006),
        "bobbin": (0.013, 0.010, 0.005),
        "cam": (0.016, 0.007, 0.004),
        "presser_bar": (0.0045, 0.050, None),
        "control": (0.012, 0.008, 0.004),
        "pin": (0.0035, 0.038, None),
    }
    if kind == "needle":
        _build_needle(api, path)
    elif kind == "hook":
        _build_hook(api, path)
    elif kind == "feed_dog":
        _build_feed_dog(api, path)
    elif kind == "presser_foot":
        _build_presser_foot(api, path)
    elif kind == "lever":
        _build_lever(api, path)
    elif kind == "link":
        _build_link(api, path)
    elif kind in round_parts:
        radius, depth, bore = round_parts[kind]
        _build_round_part(
            api,
            path,
            radius=radius,
            depth=depth,
            bore_radius=bore,
        )
    else:
        x1, y1, x2, y2, depth = rectangles.get(
            kind,
            (-0.015, -0.010, 0.015, 0.010, 0.006),
        )
        _build_rectangles(api, path, [(x1, y1, x2, y2)], depth=depth)


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    components = {item["role"]: item for item in spec["components"]}
    if tuple(components) != ROLES:
        raise ValueError("Spec component roles/order do not match harness roles")
    log: list[dict] = []
    api.close_all()
    for role in ROLES:
        path = paths[role]
        started = time.perf_counter()
        try:
            if force or not path.is_file():
                _build_by_kind(api, path, str(components[role]["kind"]))
                status = "built"
            else:
                status = "reused"
            log.append({
                "role": role,
                "kind": components[role]["kind"],
                "path": str(path),
                "status": status,
                "duration_seconds": round(
                    time.perf_counter() - started,
                    3,
                ),
            })
        finally:
            # A part is never left open, including after a failed build.
            api.close_all()
    return paths, log


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
        "创建一台外观完整、机构可演示的家用机械缝纫机装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下 40 个参数化零件已经建好。按坐标插入，mm 必须换算为 m：",
    ]
    for role in ROLES:
        item = by_role[role]
        lines.append(
            f"- {role}: {paths[role]}，位置 {item['translation']} mm，"
            f"分组 {item['group']}"
        )
    lines.extend([
        "产品必须包含底座、立柱和机臂外壳，手轮与上下轴传动，"
        "针杆曲柄滑块、旋梭梭芯、四杆送布牙、挑线杆、压脚和操作件。",
        "针驱动中心 [45,105,0] mm，+Z 旋转，针杆沿 -Y；"
        "曲柄半径 18 mm、连杆 65 mm、针行程 36 mm。",
        "手轮与主轴同向 1:1，下轴反向 1:1，旋梭反向二倍速且相位 25°，"
        "挑线偏心轮同向 1:1 且相位 90°。",
        "送布四杆固定铰 A=[10,25,0] mm、B=[70,25,0] mm，"
        "杆长 22/58/28 mm，开式装配；连杆点沿杆 0.72、法向偏置 9 mm。",
        "压脚谐波凸轮中心 [25,82,0] mm，基圆 10 mm、升程 6 mm、"
        "相位 30°，从动件沿 +Y。",
        "使用 observation 中真实组件实例名设置四类通用机构的中性姿态，"
        "固定 base_plate，重建并保存。不要保留零件窗口。",
    ])
    return "\n".join(lines)


def _milestone(
    identifier: str,
    title: str,
    exact_count: int,
    included_groups: set[str],
    spec: dict,
    *,
    depends_on: list[str] | None = None,
    assembly_path: Path | None = None,
) -> Milestone:
    postconditions = [
        PostconditionSpec(
            kind="component_count",
            params={"exact": exact_count},
        ),
    ]
    if assembly_path is None:
        postconditions.append(PostconditionSpec(
            kind="component_positions",
            params={
                "expected": _positions(spec, groups=included_groups),
                "tolerance_mm": float(
                    spec["acceptance"]["translation_tolerance_mm"]
                ),
            },
        ))
    if assembly_path is not None:
        postconditions.extend([
            PostconditionSpec(kind="valid_assembly"),
            PostconditionSpec(
                kind="assembly_saved",
                params={"path": str(assembly_path)},
            ),
        ])
    return Milestone(
        id=identifier,
        title=title,
        depends_on=depends_on or [],
        postconditions=postconditions,
        max_actions=5,
        max_failures=7,
    )


def _task_plan(
    goal: str,
    spec: dict,
    assembly_path: Path,
) -> TaskPlan:
    counts = []
    running = 0
    for group in GROUPS:
        running += sum(
            item["group"] == group for item in spec["components"]
        )
        counts.append(running)
    milestones: list[Milestone] = []
    titles = (
        "新建装配体并插入底座、立柱、机臂及盖板",
        "插入手轮、电机、上下同步齿轮和主轴",
        "插入针曲柄、连杆、针杆、导套、针夹和机针",
        "插入下轴、旋梭驱动、旋梭、梭壳和梭芯",
        "插入四杆送布机构、送布牙和调节件",
        "插入挑线与压脚凸轮从动机构",
        "插入控制件，设置四机构中性姿态，固定底座、重建并保存",
    )
    for index, group in enumerate(GROUPS):
        included = set(GROUPS[: index + 1])
        identifier = f"sewing_{group}"
        milestones.append(
            _milestone(
                identifier,
                titles[index],
                counts[index],
                included,
                spec,
                depends_on=(
                    [f"sewing_{GROUPS[index - 1]}"] if index else None
                ),
                assembly_path=assembly_path if index == 6 else None,
            )
        )
    return TaskPlan(
        user_goal=goal,
        milestones=milestones,
        notes=(
            "产品需求、参数和评分只位于 eval；不向生产 Prompt 提供黄金操作序列。"
        ),
        recovery_buffer=16,
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
            kind="component_positions",
            params={
                # Components driven by the four mechanisms intentionally leave
                # their insertion coordinates. Verify the stationary frame and
                # controls here; dynamic components are checked from transforms
                # at every mechanism sample below.
                "expected": _positions(
                    spec,
                    groups={"foundation", "controls"},
                ),
                "tolerance_mm": float(
                    spec["acceptance"]["translation_tolerance_mm"]
                ),
            },
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
        haystack = (
            f"{component.get('name', '')}|{component.get('path', '')}"
        ).lower()
        for role in ROLES:
            if role.lower() in haystack:
                resolved[role] = str(component.get("name", ""))
    missing = sorted(set(ROLES) - set(resolved))
    if missing:
        raise RuntimeError(f"无法解析缝纫机组件实例名: {missing}")
    return resolved


def _crank_slider_payload(
    names: dict[str, str],
    spec: dict,
    angle_deg: float,
) -> dict:
    drive = spec["needle_drive"]
    center = [float(value) / 1000 for value in drive["crank_center"]]
    return {
        "crankComponent": names["needle_crank"],
        "rodComponent": names["needle_connecting_rod"],
        "sliderComponent": names["needle_bar"],
        "centerX": center[0],
        "centerY": center[1],
        "centerZ": center[2],
        "crankRadius": float(drive["crank_radius"]) / 1000,
        "rodLength": float(drive["connecting_rod_length"]) / 1000,
        "sliderAxisX": drive["slider_axis"][0],
        "sliderAxisY": drive["slider_axis"][1],
        "sliderAxisZ": drive["slider_axis"][2],
        "rotationAxisX": drive["rotation_axis"][0],
        "rotationAxisY": drive["rotation_axis"][1],
        "rotationAxisZ": drive["rotation_axis"][2],
        "angleDeg": angle_deg,
        "synchronizedComponents": [],
    }


def _synchronized_rotary_payload(
    names: dict[str, str],
    spec: dict,
    input_angle_deg: float,
) -> dict:
    timing = spec["rotary_timing"]
    positions = {
        item["role"]: [float(value) / 1000 for value in item["translation"]]
        for item in spec["components"]
    }
    return {
        "inputAngleDeg": input_angle_deg,
        "axisX": 0,
        "axisY": 0,
        "axisZ": 1,
        "rotors": [
            {
                "componentName": names[item["role"]],
                "ratio": float(item["ratio"]),
                "reverse": bool(item["reverse"]),
                "phaseDeg": float(item["phase_deg"]),
                "x": positions[item["role"]][0],
                "y": positions[item["role"]][1],
                "z": positions[item["role"]][2],
            }
            for item in timing["rotors"]
        ],
    }


def _four_bar_payload(
    names: dict[str, str],
    spec: dict,
    input_angle_deg: float,
) -> dict:
    linkage = spec["feed_four_bar"]
    a = [float(value) / 1000 for value in linkage["ground_pivot_a"]]
    b = [float(value) / 1000 for value in linkage["ground_pivot_b"]]
    point = linkage["coupler_point"]
    return {
        "inputComponent": names["feed_crank"],
        "couplerComponent": names["feed_coupler"],
        "outputComponent": names["feed_rocker"],
        "groundAX": a[0],
        "groundAY": a[1],
        "groundAZ": a[2],
        "groundBX": b[0],
        "groundBY": b[1],
        "groundBZ": b[2],
        "rotationAxisX": linkage["rotation_axis"][0],
        "rotationAxisY": linkage["rotation_axis"][1],
        "rotationAxisZ": linkage["rotation_axis"][2],
        "inputLength": float(linkage["input_length"]) / 1000,
        "couplerLength": float(linkage["coupler_length"]) / 1000,
        "outputLength": float(linkage["output_length"]) / 1000,
        "couplerPoint": {
            "fraction": float(point["along"]),
            "normalOffset": float(point["offset"]) / 1000,
        },
        "couplerPointComponents": [
            {"componentName": names["feed_dog_bar"]},
            {
                "componentName": names["feed_dog"],
                "axisOffset": 0.004,
            },
        ],
        "assemblyMode": linkage["assembly_mode"],
        "inputAngleDeg": input_angle_deg,
    }


def _cam_follower_payload(
    names: dict[str, str],
    spec: dict,
    input_angle_deg: float,
) -> dict:
    cam = spec["presser_cam"]
    center = [float(value) / 1000 for value in cam["center"]]
    return {
        "driverComponent": names["presser_cam"],
        "followerComponent": names["presser_bar"],
        "centerX": center[0],
        "centerY": center[1],
        "centerZ": center[2],
        "rotationAxisX": cam["rotation_axis"][0],
        "rotationAxisY": cam["rotation_axis"][1],
        "rotationAxisZ": cam["rotation_axis"][2],
        "followerAxisX": cam["follower_axis"][0],
        "followerAxisY": cam["follower_axis"][1],
        "followerAxisZ": cam["follower_axis"][2],
        "baseRadius": float(cam["base_radius"]) / 1000,
        "lift": float(cam["lift"]) / 1000,
        "phaseDeg": float(cam["phase_deg"]),
        "profile": cam["profile"],
        "inputAngleDeg": input_angle_deg,
    }


def _needle_distance_mm(spec: dict, angle_deg: float) -> float:
    drive = spec["needle_drive"]
    radius = float(drive["crank_radius"])
    rod = float(drive["connecting_rod_length"])
    angle = math.radians(angle_deg)
    return radius * math.cos(angle) + math.sqrt(
        rod * rod - radius * radius * math.sin(angle) ** 2
    )


def _rotor_angle_deg(rotor: dict, input_angle_deg: float) -> float:
    direction = -1.0 if rotor["reverse"] else 1.0
    return (
        direction * input_angle_deg / float(rotor["ratio"])
        + float(rotor["phase_deg"])
    )


def _four_bar_geometry_mm(spec: dict, angle_deg: float) -> dict:
    linkage = spec["feed_four_bar"]
    ax, ay, _ = map(float, linkage["ground_pivot_a"])
    bx, by, _ = map(float, linkage["ground_pivot_b"])
    input_length = float(linkage["input_length"])
    coupler_length = float(linkage["coupler_length"])
    output_length = float(linkage["output_length"])
    angle = math.radians(angle_deg)
    crank = (
        ax + input_length * math.cos(angle),
        ay + input_length * math.sin(angle),
    )
    dx, dy = bx - crank[0], by - crank[1]
    distance = math.hypot(dx, dy)
    if distance == 0 or not (
        abs(coupler_length - output_length)
        <= distance
        <= coupler_length + output_length
    ):
        raise ValueError(f"Four-bar has no real closure at {angle_deg} degrees")
    along = (
        coupler_length**2 - output_length**2 + distance**2
    ) / (2 * distance)
    height = math.sqrt(max(coupler_length**2 - along**2, 0.0))
    ux, uy = dx / distance, dy / distance
    normal = (-uy, ux)
    candidates = (
        (
            crank[0] + along * ux + height * normal[0],
            crank[1] + along * uy + height * normal[1],
        ),
        (
            crank[0] + along * ux - height * normal[0],
            crank[1] + along * uy - height * normal[1],
        ),
    )
    crank_side = crank[1] - ay
    same_side = (
        candidates[0][1] - ay
    ) * crank_side >= 0
    choose_same_side = linkage["assembly_mode"] == "open"
    rocker = candidates[0] if same_side == choose_same_side else candidates[1]
    point_spec = linkage["coupler_point"]
    fraction = float(point_spec["along"])
    offset = float(point_spec["offset"])
    coupler_dx = rocker[0] - crank[0]
    coupler_dy = rocker[1] - crank[1]
    coupler_unit = (
        coupler_dx / coupler_length,
        coupler_dy / coupler_length,
    )
    coupler_normal = (-coupler_unit[1], coupler_unit[0])
    point = (
        crank[0] + fraction * coupler_dx + offset * coupler_normal[0],
        crank[1] + fraction * coupler_dy + offset * coupler_normal[1],
    )
    return {
        "crank_joint": crank,
        "rocker_joint": rocker,
        "coupler_point": point,
    }


def _cam_lift_mm(spec: dict, angle_deg: float) -> float:
    cam = spec["presser_cam"]
    phase_angle = math.radians(angle_deg + float(cam["phase_deg"]))
    return 0.5 * float(cam["lift"]) * (1.0 - math.cos(phase_angle))


def _xyz_mm(value: object) -> tuple[float, float] | None:
    if isinstance(value, dict):
        if "x" in value and "y" in value:
            return float(value["x"]) * 1000, float(value["y"]) * 1000
        if "X" in value and "Y" in value:
            return float(value["X"]) * 1000, float(value["Y"]) * 1000
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return float(value[0]) * 1000, float(value[1]) * 1000
    return None


def validate_mechanisms(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    acceptance = spec["acceptance"]

    needle_samples = []
    for angle in spec["needle_drive"]["sample_angles_deg"]:
        result = api.call(
            "/api/assembly/mechanism/crank-slider-pose",
            _crank_slider_payload(names, spec, float(angle)),
        )["result"]
        expected = _needle_distance_mm(spec, float(angle))
        actual = float(result["sliderDistance"]) * 1000
        error = abs(actual - expected)
        needle_samples.append({
            "angle_deg": angle,
            "expected_position_mm": expected,
            "actual_position_mm": actual,
            "position_error_mm": error,
            "passed": error <= float(
                acceptance["needle_position_tolerance_mm"]
            ),
        })
    actual_stroke = max(
        sample["actual_position_mm"] for sample in needle_samples
    ) - min(sample["actual_position_mm"] for sample in needle_samples)
    needle_cycle_closed = math.isclose(
        needle_samples[0]["actual_position_mm"],
        needle_samples[-1]["actual_position_mm"],
        abs_tol=float(acceptance["needle_position_tolerance_mm"]),
    )
    needle_passed = (
        len(needle_samples) >= int(acceptance["minimum_needle_samples"])
        and all(sample["passed"] for sample in needle_samples)
        and abs(
            actual_stroke - float(spec["needle_drive"]["expected_stroke"])
        ) <= float(acceptance["stroke_tolerance_mm"])
        and needle_cycle_closed
    )

    rotary_samples = []
    rotors = spec["rotary_timing"]["rotors"]
    for angle in spec["rotary_timing"]["sample_angles_deg"]:
        result = api.call(
            "/api/assembly/mechanism/synchronized-rotary-pose",
            _synchronized_rotary_payload(names, spec, float(angle)),
        )["result"]
        actual_rotors = result.get("rotors", result.get("synchronized", []))
        actual_by_role = {
            str(item.get("role", item.get("componentName", ""))): item
            for item in actual_rotors
        }
        checks = []
        for index, rotor in enumerate(rotors):
            expected = _rotor_angle_deg(rotor, float(angle))
            actual_item = actual_rotors[index] if index < len(
                actual_rotors
            ) else {}
            matched = actual_by_role.get(
                rotor["role"],
                actual_by_role.get(names[rotor["role"]], actual_item),
            ) or {}
            actual = float(
                matched.get(
                    "requestedAngleDeg",
                    matched.get("angleDeg", math.inf),
                )
            )
            pose_error = abs(float(matched.get("angleErrorDeg", 0.0)))
            checks.append({
                "role": rotor["role"],
                "expected_angle_deg": expected,
                "actual_angle_deg": actual,
                "error_deg": abs(actual - expected),
                "pose_error_deg": pose_error,
            })
        passed = (
            len(checks) == len(rotors)
            and all(
                item["error_deg"]
                <= float(acceptance["angle_tolerance_deg"])
                and item["pose_error_deg"]
                <= float(acceptance["angle_tolerance_deg"])
                for item in checks
            )
        )
        rotary_samples.append({
            "input_angle_deg": angle,
            "rotors": checks,
            "passed": passed,
        })
    rotary_cycle_closed = all(
        math.isclose(
            (
                rotary_samples[-1]["rotors"][index]["actual_angle_deg"]
                - rotary_samples[0]["rotors"][index]["actual_angle_deg"]
            ),
            (
                _rotor_angle_deg(rotor, 720)
                - _rotor_angle_deg(rotor, 0)
            ),
            abs_tol=float(acceptance["angle_tolerance_deg"]),
        )
        for index, rotor in enumerate(rotors)
    )
    rotary_passed = (
        len(rotary_samples) >= int(acceptance["minimum_rotary_samples"])
        and all(sample["passed"] for sample in rotary_samples)
        and rotary_cycle_closed
    )

    four_bar_samples = []
    for angle in spec["feed_four_bar"]["sample_angles_deg"]:
        expected = _four_bar_geometry_mm(spec, float(angle))
        result = api.call(
            "/api/assembly/mechanism/four-bar-pose",
            _four_bar_payload(names, spec, float(angle)),
        )["result"]
        joints = result.get("joints", {})
        actual_crank = _xyz_mm(
            joints.get(
                "C",
                result.get("crankJoint", result.get("inputJoint")),
            )
        )
        actual_rocker = _xyz_mm(
            joints.get(
                "D",
                result.get("rockerJoint", result.get("outputJoint")),
            )
        )
        actual_point = _xyz_mm(
            result.get(
                "couplerPointXYZ",
                result.get("couplerPoint", result.get("followerPoint")),
            )
        )
        if None in (actual_crank, actual_rocker, actual_point):
            closure_error = feed_error = math.inf
        else:
            assert actual_crank is not None
            assert actual_rocker is not None
            assert actual_point is not None
            closure_error = max(
                math.dist(actual_crank, expected["crank_joint"]),
                math.dist(actual_rocker, expected["rocker_joint"]),
                float(result.get("closureError", math.inf)) * 1000,
            )
            feed_error = math.dist(
                actual_point,
                expected["coupler_point"],
            )
        passed = (
            closure_error
            <= float(acceptance["four_bar_closure_tolerance_mm"])
            and feed_error
            <= float(acceptance["feed_position_tolerance_mm"])
        )
        four_bar_samples.append({
            "input_angle_deg": angle,
            "expected": expected,
            "closure_error_mm": closure_error,
            "coupler_point_error_mm": feed_error,
            "passed": passed,
        })
    first_four_bar = _four_bar_geometry_mm(
        spec,
        float(spec["feed_four_bar"]["sample_angles_deg"][0]),
    )
    repeated_four_bar = _four_bar_geometry_mm(
        spec,
        float(spec["feed_four_bar"]["sample_angles_deg"][0]) + 360,
    )
    four_bar_cycle_closed = math.dist(
        first_four_bar["coupler_point"],
        repeated_four_bar["coupler_point"],
    ) <= float(acceptance["feed_position_tolerance_mm"])
    four_bar_passed = (
        len(four_bar_samples) >= int(acceptance["minimum_feed_samples"])
        and all(sample["passed"] for sample in four_bar_samples)
        and four_bar_cycle_closed
    )

    cam_samples = []
    for angle in spec["presser_cam"]["sample_angles_deg"]:
        expected = _cam_lift_mm(spec, float(angle))
        result = api.call(
            "/api/assembly/mechanism/cam-follower-pose",
            _cam_follower_payload(names, spec, float(angle)),
        )["result"]
        actual = float(
            result.get(
                "followerLift",
                result.get("lift", result.get("displacement", math.inf)),
            )
        ) * 1000
        error = abs(actual - expected)
        cam_samples.append({
            "input_angle_deg": angle,
            "expected_lift_mm": expected,
            "actual_lift_mm": actual,
            "lift_error_mm": error,
            "passed": error <= float(
                acceptance["cam_lift_tolerance_mm"]
            ),
        })
    cam_cycle_closed = math.isclose(
        cam_samples[0]["actual_lift_mm"],
        cam_samples[-1]["actual_lift_mm"],
        abs_tol=float(acceptance["cam_lift_tolerance_mm"]),
    )
    cam_passed = (
        len(cam_samples) >= int(acceptance["minimum_cam_samples"])
        and all(sample["passed"] for sample in cam_samples)
        and cam_cycle_closed
    )

    # Restore a deterministic neutral pose before saving the evaluated model.
    api.call(
        "/api/assembly/mechanism/crank-slider-pose",
        _crank_slider_payload(names, spec, 0),
    )
    api.call(
        "/api/assembly/mechanism/synchronized-rotary-pose",
        _synchronized_rotary_payload(names, spec, 0),
    )
    api.call(
        "/api/assembly/mechanism/four-bar-pose",
        _four_bar_payload(
            names,
            spec,
            float(spec["feed_four_bar"]["sample_angles_deg"][0]),
        ),
    )
    api.call(
        "/api/assembly/mechanism/cam-follower-pose",
        _cam_follower_payload(names, spec, 0),
    )
    api.call("/api/assembly/save")

    return {
        "passed": all((
            needle_passed,
            rotary_passed,
            four_bar_passed,
            cam_passed,
        )),
        "needle": {
            "passed": needle_passed,
            "samples": needle_samples,
            "actual_stroke_mm": actual_stroke,
            "cycle_closed": needle_cycle_closed,
        },
        "rotary": {
            "passed": rotary_passed,
            "samples": rotary_samples,
            "cycle_closed": rotary_cycle_closed,
        },
        "four_bar": {
            "passed": four_bar_passed,
            "samples": four_bar_samples,
            "cycle_closed": four_bar_cycle_closed,
        },
        "cam": {
            "passed": cam_passed,
            "samples": cam_samples,
            "cycle_closed": cam_cycle_closed,
        },
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
    functional_points = 0.0
    for mechanism in ("needle", "rotary", "four_bar", "cam"):
        result = mechanisms.get(mechanism) or {}
        samples = result.get("samples") or []
        sample_rate = (
            sum(bool(item.get("passed")) for item in samples) / len(samples)
            if samples
            else 0.0
        )
        functional_points += 8.0 * sample_rate
        if result.get("cycle_closed"):
            functional_points += 2.0
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
    assembly_path = output_dir / ASSEMBLY_NAME
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
    report: dict
    client.begin_trace_capture()
    try:
        result = agent.run(goal, task_plan=task_plan)
        try:
            if result.status == "completed":
                # AddComponent closes each inactive source part and checkpoints
                # large assemblies periodically, so validation can safely retain
                # the in-memory assembly and avoid a costly close/reopen cycle.
                api.call("/api/assembly/save")
                validation_observation = _observation(api)
            else:
                validation_observation = result.final_observation
            static = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                validation_observation,
            )
            mechanisms = (
                validate_mechanisms(api, spec, validation_observation)
                if result.status == "completed" and static.passed
                else {
                    "passed": False,
                    "needle": {"samples": []},
                    "rotary": {"samples": []},
                    "four_bar": {"samples": []},
                    "cam": {"samples": []},
                }
            )
            passed = (
                result.status == "completed"
                and static.passed
                and mechanisms["passed"]
            )
            report = {
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
        except Exception as validation_exc:
            mechanisms = {
                "passed": False,
                "needle": {"samples": []},
                "rotary": {"samples": []},
                "four_bar": {"samples": []},
                "cam": {"samples": []},
            }
            report = {
                "status": result.status,
                "passed": False,
                "score": score_result(
                    status=result.status,
                    static_passed=False,
                    mechanisms=mechanisms,
                    successful_actions=result.successful_actions,
                    total_actions=result.total_actions,
                ),
                "static_acceptance": {
                    "passed": False,
                    "details": [],
                },
                "mechanism_acceptance": mechanisms,
                "validation_error": (
                    f"{type(validation_exc).__name__}: {validation_exc}"
                ),
                "duration_seconds": round(time.perf_counter() - started, 3),
                "token_usage": client.usage_snapshot(),
                "result": result.model_dump(),
                "task_plan": task_plan.model_dump(),
            }
    except Exception as exc:
        report = {
            "status": "error",
            "passed": False,
            "score": 0.0,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "token_usage": client.usage_snapshot(),
            "error": f"{type(exc).__name__}: {exc}",
            "task_plan": task_plan.model_dump(),
        }
    finally:
        # Capture is ended on success and error, preserving provider diagnostics.
        report["raw_llm_trace"] = client.end_trace_capture()
        report["token_usage"] = client.usage_snapshot()
        client.close()
        executor.close()
        try:
            api.close_inactive()
        except Exception:
            pass
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=42)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
    )
    args = parser.parse_args()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    api = SwApi(args.sw_url)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    assembly_path = ARTIFACT_DIR / ASSEMBLY_NAME
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
        payload = {
            "run_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "case_id": spec["case_id"],
            "preparation": preparation,
            "agent_run": result,
        }
        result_path = args.output or (
            ARTIFACT_DIR
            / (
                "validation_result.json"
                if args.validate_existing
                else "result.json"
            )
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"status={result.get('status')} "
            f"passed={result.get('passed')} "
            f"score={result.get('score')} "
            f"tokens={(result.get('token_usage') or {}).get('total_tokens')}"
        )
        if not result.get("passed"):
            raise SystemExit(1)
    finally:
        try:
            api.close_all()
        except Exception:
            pass


if __name__ == "__main__":
    main()
