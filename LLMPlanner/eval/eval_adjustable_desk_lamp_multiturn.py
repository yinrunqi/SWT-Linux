"""Build, pose, and score an adjustable two-arm desk lamp assembly."""

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


SPEC_PATH = Path(__file__).with_name("adjustable_desk_lamp_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "adjustable_desk_lamp"
ROLES = (
    "base_plate",
    "base_weight",
    "rotary_post",
    "shoulder_joint",
    "lower_arm_left",
    "lower_arm_right",
    "elbow_joint",
    "lower_spring_left",
    "lower_spring_right",
    "upper_arm_left",
    "upper_arm_right",
    "head_joint",
    "upper_spring_left",
    "upper_spring_right",
    "lamp_shade",
    "bulb",
    "rocker_switch",
    "cable_grommet",
)


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {role: output_dir / f"{role}.SLDPRT" for role in ROLES}


def _build_ellipse(
    api: SwApi,
    path: Path,
    *,
    major_radius: float,
    minor_radius: float,
    depth: float,
) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/ellipse",
        {
            "cx": 0,
            "cy": 0,
            "majorRadius": major_radius,
            "minorRadius": minor_radius,
        },
    )
    _save_extrusion(api, path, depth)


def _build_link(
    api: SwApi,
    path: Path,
    *,
    length: float,
    width: float = 0.012,
    depth: float = 0.006,
) -> None:
    pivot_overhang = width / 2
    _build_rectangles(
        api,
        path,
        [(
            -length / 2 - pivot_overhang,
            -width / 2,
            length / 2 + pivot_overhang,
            width / 2,
        )],
        depth=depth,
        circles=[
            (-length / 2, 0, 0.0035),
            (length / 2, 0, 0.0035),
        ],
    )


def _build_spring(
    api: SwApi,
    path: Path,
    *,
    length: float,
) -> None:
    _build_rectangles(
        api,
        path,
        [(-length / 2, -0.002, length / 2, 0.002)],
        depth=0.003,
    )


def _build_shade(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.020, "y": -0.036},
                {"x": 0.020, "y": -0.028},
                {"x": 0.050, "y": -0.014},
                {"x": 0.050, "y": 0.014},
                {"x": 0.020, "y": 0.028},
                {"x": -0.020, "y": 0.036},
            ],
            "closed": True,
        },
    )
    _save_extrusion(api, path, 0.020)


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    mechanism = spec["mechanism"]
    lower_length = float(mechanism["lower_arm_length"]) / 1000
    upper_length = float(mechanism["upper_arm_length"]) / 1000
    builders = {
        "base_plate": lambda p: _build_ellipse(
            api, p, major_radius=0.080, minor_radius=0.018, depth=0.022
        ),
        "base_weight": lambda p: _build_ellipse(
            api, p, major_radius=0.064, minor_radius=0.014, depth=0.016
        ),
        "rotary_post": lambda p: _build_rectangles(
            api, p, [(-0.009, -0.035, 0.009, 0.035)], depth=0.020
        ),
        "shoulder_joint": lambda p: _build_round_part(
            api, p, radius=0.014, bore_radius=0.005, depth=0.020
        ),
        "lower_arm_left": lambda p: _build_link(
            api, p, length=lower_length
        ),
        "lower_arm_right": lambda p: _build_link(
            api, p, length=lower_length
        ),
        "elbow_joint": lambda p: _build_round_part(
            api, p, radius=0.013, bore_radius=0.0045, depth=0.022
        ),
        "lower_spring_left": lambda p: _build_spring(
            api, p, length=0.095
        ),
        "lower_spring_right": lambda p: _build_spring(
            api, p, length=0.095
        ),
        "upper_arm_left": lambda p: _build_link(
            api, p, length=upper_length
        ),
        "upper_arm_right": lambda p: _build_link(
            api, p, length=upper_length
        ),
        "head_joint": lambda p: _build_round_part(
            api, p, radius=0.012, bore_radius=0.004, depth=0.022
        ),
        "upper_spring_left": lambda p: _build_spring(
            api, p, length=0.082
        ),
        "upper_spring_right": lambda p: _build_spring(
            api, p, length=0.082
        ),
        "lamp_shade": lambda p: _build_shade(api, p),
        "bulb": lambda p: _build_ellipse(
            api, p, major_radius=0.022, minor_radius=0.013, depth=0.012
        ),
        "rocker_switch": lambda p: _build_rectangles(
            api, p, [(-0.012, -0.006, 0.012, 0.006)], depth=0.008
        ),
        "cable_grommet": lambda p: _build_round_part(
            api, p, radius=0.007, bore_radius=0.003, depth=0.006
        ),
    }
    log: list[dict] = []
    api.close_all()
    for role in ROLES:
        path = paths[role]
        started = time.perf_counter()
        if force or not path.is_file():
            builders[role](path)
            status = "built"
        else:
            status = "reused"
        log.append({
            "role": role,
            "path": str(path),
            "status": status,
            "duration_seconds": round(time.perf_counter() - started, 3),
        })
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
        "创建一台外观完整的双节可调节金属台灯装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下零件已经参数化建好。按给定装配体坐标插入，mm 必须换算为 m：",
    ]
    for role in ROLES:
        item = by_role[role]
        lines.append(
            f"- {role}: {paths[role]}，位置 {item['translation']} mm，"
            f"分组 {item['group']}"
        )
    lines.extend([
        "四个 foundation 零件构成椭圆加重底座、立柱和肩部转轴；"
        "左右双杆与四根细弹簧形成上下两段灯臂，灯罩内有灯泡，底座有开关和出线套。",
        "初始工作姿态：基准转轴 [0,60,0] mm，旋转轴 +Z；"
        "下臂长 120 mm、绝对角 60°，上臂长 105 mm、相对下臂 -35°，"
        "灯头绝对角 -15°。",
        "全部组件插入后调用 assembly_set_planar_chain_pose。每段用"
        " componentNames/axisOffsets 同时设置左右杆和对应弹簧；"
        "elbow_joint/head_joint 放 jointComponents，lamp_shade/bulb 放 endComponents。",
        "使用 observation 中的真实组件实例名。固定 base_plate，最后重建、保存；"
        "装配过程中不要打开并保留零件窗口。",
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
                "lamp_foundation",
                "新建装配体并插入底座、配重、立柱和肩部转轴",
                4,
                {"foundation"},
                spec,
            ),
            _milestone(
                "lamp_lower_linkage",
                "插入下臂双杆、肘部转轴和下部双弹簧",
                9,
                {"foundation", "lower_linkage"},
                spec,
                depends_on=["lamp_foundation"],
            ),
            _milestone(
                "lamp_upper_linkage",
                "插入上臂双杆、灯头转轴和上部双弹簧",
                14,
                {"foundation", "lower_linkage", "upper_linkage"},
                spec,
                depends_on=["lamp_lower_linkage"],
            ),
            _milestone(
                "lamp_head_details",
                "插入灯罩、灯泡、开关和电源线出线套",
                18,
                {
                    "foundation",
                    "lower_linkage",
                    "upper_linkage",
                    "head_and_details",
                },
                spec,
                depends_on=["lamp_upper_linkage"],
            ),
            Milestone(
                id="lamp_save",
                title=(
                    "用通用多关节链设置工作姿态，固定底座、"
                    "重建并保存最终台灯装配体"
                ),
                depends_on=["lamp_head_details"],
                postconditions=[
                    PostconditionSpec(kind="valid_assembly"),
                    PostconditionSpec(
                        kind="assembly_saved",
                        params={"path": str(assembly_path)},
                    ),
                ],
                max_actions=4,
                max_failures=4,
            ),
        ],
        notes="尺寸与评分只存在于 eval；生产技能仅描述通用平面连杆原则。",
        recovery_buffer=12,
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
        raise RuntimeError(f"无法解析台灯组件实例名: {missing}")
    return resolved


def _pose_payload(
    names: dict[str, str],
    spec: dict,
    lower_angle_deg: float,
    upper_relative_angle_deg: float,
) -> dict:
    mechanism = spec["mechanism"]
    base = [float(value) / 1000 for value in mechanism["base_pivot"]]
    axis = mechanism["rotation_axis"]
    return {
        "baseX": base[0],
        "baseY": base[1],
        "baseZ": base[2],
        "rotationAxisX": axis[0],
        "rotationAxisY": axis[1],
        "rotationAxisZ": axis[2],
        "baseAngleDeg": 0,
        "segments": [
            {
                "length": mechanism["lower_arm_length"] / 1000,
                "angleDeg": lower_angle_deg,
                "componentNames": [
                    names["lower_arm_left"],
                    names["lower_arm_right"],
                    names["lower_spring_left"],
                    names["lower_spring_right"],
                ],
                "axisOffsets": [-0.008, 0.008, -0.013, 0.013],
                "bodyLengths": [
                    mechanism["lower_arm_length"] / 1000,
                    mechanism["lower_arm_length"] / 1000,
                    0.095,
                    0.095,
                ],
            },
            {
                "length": mechanism["upper_arm_length"] / 1000,
                "angleDeg": upper_relative_angle_deg,
                "componentNames": [
                    names["upper_arm_left"],
                    names["upper_arm_right"],
                    names["upper_spring_left"],
                    names["upper_spring_right"],
                ],
                "axisOffsets": [-0.008, 0.008, -0.013, 0.013],
                "bodyLengths": [
                    mechanism["upper_arm_length"] / 1000,
                    mechanism["upper_arm_length"] / 1000,
                    0.082,
                    0.082,
                ],
            },
        ],
        "jointComponents": [
            {
                "componentName": names["shoulder_joint"],
                "anchorIndex": 0,
            },
            {
                "componentName": names["elbow_joint"],
                "anchorIndex": 1,
            },
            {
                "componentName": names["head_joint"],
                "anchorIndex": 2,
            },
        ],
        "endComponents": [
            {
                "componentName": names["lamp_shade"],
                "alongOffset": 0.020,
                "angleDeg": mechanism["head_angle_deg"],
            },
            {
                "componentName": names["bulb"],
                "alongOffset": 0.040,
                "angleDeg": mechanism["head_angle_deg"],
            },
        ],
    }


def _angles_for_control(spec: dict, control_percent: float) -> tuple[float, float]:
    poses = sorted(
        spec["mechanism"]["poses"],
        key=lambda item: float(item["control_percent"]),
    )
    control = max(0.0, min(float(control_percent), 100.0))
    for left, right in zip(poses, poses[1:]):
        left_control = float(left["control_percent"])
        right_control = float(right["control_percent"])
        if left_control <= control <= right_control:
            ratio = (
                (control - left_control) / (right_control - left_control)
                if right_control > left_control
                else 0.0
            )
            lower = float(left["lower_angle_deg"]) + ratio * (
                float(right["lower_angle_deg"])
                - float(left["lower_angle_deg"])
            )
            upper = float(left["upper_relative_angle_deg"]) + ratio * (
                float(right["upper_relative_angle_deg"])
                - float(left["upper_relative_angle_deg"])
            )
            return lower, upper
    last = poses[-1]
    return (
        float(last["lower_angle_deg"]),
        float(last["upper_relative_angle_deg"]),
    )


def validate_kinematics(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    mechanism = spec["mechanism"]
    tolerance = float(spec["acceptance"]["link_length_tolerance_mm"])
    endpoint_tolerance = float(
        spec["acceptance"]["body_endpoint_tolerance_mm"]
    )
    orientation_tolerance = float(
        spec["acceptance"]["orientation_tolerance_deg"]
    )
    angle_tolerance = float(spec["acceptance"]["angle_tolerance_deg"])
    limits = mechanism["joint_limits_deg"]
    samples: list[dict] = []
    for pose in mechanism["poses"]:
        lower = float(pose["lower_angle_deg"])
        upper_relative = float(pose["upper_relative_angle_deg"])
        payload = _pose_payload(names, spec, lower, upper_relative)
        result = api.call(
            "/api/assembly/mechanism/planar-chain-pose",
            payload,
        )["result"]
        anchors = result["anchors"]
        measured_lengths = []
        for start, end in zip(anchors, anchors[1:]):
            measured_lengths.append(
                math.dist(start, end) * 1000
            )
        expected_lengths = [
            float(mechanism["lower_arm_length"]),
            float(mechanism["upper_arm_length"]),
        ]
        length_errors = [
            abs(actual - expected)
            for actual, expected in zip(measured_lengths, expected_lengths)
        ]
        segment_angles = [
            float(item["angleDeg"])
            for item in result["segments"]
        ]
        orientation_errors = [
            float(item.get("maximumOrientationErrorDeg", math.inf))
            for item in result["segments"]
        ]
        body_endpoint_errors = [
            float(item.get("maximumBodyEndpointError", math.inf)) * 1000
            for item in result["segments"]
        ]
        expected_angles = [lower, lower + upper_relative]
        angles_ok = all(
            math.isclose(actual, expected, abs_tol=angle_tolerance)
            for actual, expected in zip(segment_angles, expected_angles)
        )
        limits_ok = (
            limits["lower_absolute"][0]
            <= lower
            <= limits["lower_absolute"][1]
            and limits["upper_relative"][0]
            <= upper_relative
            <= limits["upper_relative"][1]
            and limits["head_absolute"][0]
            <= mechanism["head_angle_deg"]
            <= limits["head_absolute"][1]
        )
        end_xyz_mm = [
            round(float(value) * 1000, 6)
            for value in result["endXYZ"]
        ]
        passed = (
            all(error <= tolerance for error in length_errors)
            and all(
                error <= orientation_tolerance
                for error in orientation_errors
            )
            and all(
                error <= endpoint_tolerance
                for error in body_endpoint_errors
            )
            and angles_ok
            and limits_ok
        )
        samples.append({
            "name": pose["name"],
            "control_percent": pose["control_percent"],
            "lower_angle_deg": lower,
            "upper_relative_angle_deg": upper_relative,
            "segment_angles_deg": segment_angles,
            "measured_lengths_mm": measured_lengths,
            "length_errors_mm": length_errors,
            "orientation_errors_deg": orientation_errors,
            "body_endpoint_errors_mm": body_endpoint_errors,
            "end_xyz_mm": end_xyz_mm,
            "head_angle_deg": mechanism["head_angle_deg"],
            "limits_ok": limits_ok,
            "passed": passed,
        })
    working = next(
        pose for pose in mechanism["poses"] if pose["name"] == "working"
    )
    api.call(
        "/api/assembly/mechanism/planar-chain-pose",
        _pose_payload(
            names,
            spec,
            float(working["lower_angle_deg"]),
            float(working["upper_relative_angle_deg"]),
        ),
    )
    api.call("/api/assembly/save")
    return {
        "passed": all(sample["passed"] for sample in samples),
        "samples": samples,
        "pose_count": len(samples),
        "head_angle_constant": all(
            math.isclose(
                sample["head_angle_deg"],
                mechanism["head_angle_deg"],
                abs_tol=angle_tolerance,
            )
            for sample in samples
        ),
    }


def score_result(
    *,
    status: str,
    static_passed: bool,
    kinematics: dict,
    succeeded_actions: int,
    total_actions: int,
) -> float:
    static_points = 40.0 if static_passed else 0.0
    samples = kinematics.get("samples") or []
    pose_rate = (
        sum(bool(item.get("passed")) for item in samples) / len(samples)
        if samples
        else 0.0
    )
    kinematic_points = 40.0 * pose_rate
    action_rate = (
        succeeded_actions / total_actions if total_actions else 0.0
    )
    execution_points = 15.0 * action_rate
    if status == "completed":
        execution_points += 5.0
    return round(
        static_points + kinematic_points + execution_points,
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
    kinematics = validate_kinematics(api, spec, observation)
    passed = static.passed and kinematics["passed"]
    return {
        "status": "completed" if passed else "blocked",
        "passed": passed,
        "score": score_result(
            status="completed" if passed else "blocked",
            static_passed=static.passed,
            kinematics=kinematics,
            succeeded_actions=0,
            total_actions=0,
        ),
        "static_acceptance": static.model_dump(),
        "kinematic_acceptance": kinematics,
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
    assembly_path = output_dir / "adjustable_desk_lamp.SLDASM"
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
        kinematics = (
            validate_kinematics(api, spec, result.final_observation)
            if result.status == "completed" and static.passed
            else {"passed": False, "samples": []}
        )
        succeeded = int(result.successful_actions)
        total = int(result.total_actions)
        passed = (
            result.status == "completed"
            and static.passed
            and kinematics["passed"]
        )
        return {
            "status": result.status,
            "passed": passed,
            "score": score_result(
                status=result.status,
                static_passed=static.passed,
                kinematics=kinematics,
                succeeded_actions=succeeded,
                total_actions=total,
            ),
            "static_acceptance": static.model_dump(),
            "kinematic_acceptance": kinematics,
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
    parser.add_argument("--max-turns", type=int, default=28)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name(
            "adjustable_desk_lamp_result.json"
        ),
    )
    args = parser.parse_args()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    api = SwApi(args.sw_url)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    assembly_path = ARTIFACT_DIR / "adjustable_desk_lamp.SLDASM"
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
