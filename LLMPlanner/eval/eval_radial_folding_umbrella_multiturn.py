"""Build and score an eight-bay radial folding umbrella skeleton."""

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

from eval.eval_adjustable_desk_lamp_multiturn import (
    SwApi,
    _build_rectangles,
    _build_round_part,
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


SPEC_PATH = Path(__file__).with_name("radial_folding_umbrella_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "radial_folding_umbrella"
RIB_ROLES = tuple(f"rib_{index}" for index in range(1, 9))
STRETCHER_ROLES = tuple(f"stretcher_{index}" for index in range(1, 9))
ROLES = (
    "umbrella_handle",
    "umbrella_shaft",
    "top_hub",
    "sliding_runner",
    *RIB_ROLES,
    *STRETCHER_ROLES,
    "canopy_cap",
    "release_button",
)


def _build_rod(
    api: SwApi,
    path: Path,
    *,
    length: float,
    width: float,
    depth: float,
) -> None:
    """Build a robust centered rod whose local +X is its pin line."""
    _build_rectangles(
        api,
        path,
        [(-length / 2, -width / 2, length / 2, width / 2)],
        depth=depth,
    )


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {role: output_dir / f"{role}.SLDPRT" for role in ROLES}


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    mechanism = spec["mechanism"]
    rib_length = float(mechanism["rib_length"]) / 1000
    stretcher_length = float(mechanism["stretcher_length"]) / 1000
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    builders = {
        "umbrella_handle": lambda p: _build_round_part(
            api, p, radius=0.018, depth=0.080
        ),
        "umbrella_shaft": lambda p: _build_round_part(
            api, p, radius=0.0045, depth=0.420
        ),
        "top_hub": lambda p: _build_round_part(
            api, p, radius=0.022, bore_radius=0.005, depth=0.012
        ),
        "sliding_runner": lambda p: _build_round_part(
            api, p, radius=0.020, bore_radius=0.006, depth=0.012
        ),
        "canopy_cap": lambda p: _build_round_part(
            api, p, radius=0.010, bore_radius=0.0045, depth=0.008
        ),
        "release_button": lambda p: _build_rectangles(
            api, p, [(-0.006, -0.004, 0.006, 0.004)], depth=0.008
        ),
    }
    for role in RIB_ROLES:
        builders[role] = lambda p, length=rib_length: _build_rod(
            api,
            p,
            length=length,
            width=0.010,
            depth=0.004,
        )
    for role in STRETCHER_ROLES:
        builders[role] = (
            lambda p, length=stretcher_length: _build_rod(
                api,
                p,
                length=length,
                width=0.009,
                depth=0.003,
            )
        )
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
        "创建一把带八根主伞骨、八根撑杆和中心滑套的折叠伞骨架装配体。",
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
        "伞轴沿 +Z；顶巢中心 [0,0,340] mm。八个方位从 +X 开始每隔 45°。",
        "主肋长 300 mm，顶巢铰点半径 18 mm；撑杆连接在主肋 120 mm 处，"
        "撑杆长 160 mm，滑套铰点半径 15 mm。",
        "初始全开姿态的主肋相对径向水平面向下 12°。使用 observation 中的真实"
        "组件实例名，调用 assembly_set_radial_deployable_pose 设置全开姿态。",
        "固定 umbrella_handle，重建并保存。装配过程中不要保留不用的零件窗口。",
    ])
    return "\n".join(lines)


def _group_milestone(
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
            _group_milestone(
                "umbrella_foundation",
                "新建装配体并插入手柄、伞轴、顶巢和滑套",
                4,
                {"foundation"},
                spec,
            ),
            _group_milestone(
                "umbrella_ribs_a",
                "插入第一至第四根主伞骨",
                8,
                {"foundation", "ribs_a"},
                spec,
                depends_on=["umbrella_foundation"],
            ),
            _group_milestone(
                "umbrella_ribs_b",
                "插入第五至第八根主伞骨",
                12,
                {"foundation", "ribs_a", "ribs_b"},
                spec,
                depends_on=["umbrella_ribs_a"],
            ),
            _group_milestone(
                "umbrella_stretchers_a",
                "插入第一至第四根滑套撑杆",
                16,
                {
                    "foundation",
                    "ribs_a",
                    "ribs_b",
                    "stretchers_a",
                },
                spec,
                depends_on=["umbrella_ribs_b"],
            ),
            _group_milestone(
                "umbrella_stretchers_b",
                "插入第五至第八根滑套撑杆",
                20,
                {
                    "foundation",
                    "ribs_a",
                    "ribs_b",
                    "stretchers_a",
                    "stretchers_b",
                },
                spec,
                depends_on=["umbrella_stretchers_a"],
            ),
            _group_milestone(
                "umbrella_details",
                "插入顶部伞帽和滑套释放按钮",
                22,
                {
                    "foundation",
                    "ribs_a",
                    "ribs_b",
                    "stretchers_a",
                    "stretchers_b",
                    "details",
                },
                spec,
                depends_on=["umbrella_stretchers_b"],
            ),
            Milestone(
                id="umbrella_pose_save",
                title="设置八方位全开姿态，固定手柄、重建并保存",
                depends_on=["umbrella_details"],
                postconditions=[
                    PostconditionSpec(kind="valid_assembly"),
                    PostconditionSpec(
                        kind="assembly_saved",
                        params={"path": str(assembly_path)},
                    ),
                ],
                max_actions=4,
                max_failures=5,
            ),
        ],
        notes="产品尺寸和三姿态评分仅存在于 eval；生产技能只含通用径向机构规则。",
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
        raise RuntimeError(f"无法解析折叠伞组件实例名: {missing}")
    return resolved


def _pose_for_openness(spec: dict, openness_percent: float) -> float:
    poses = sorted(
        spec["mechanism"]["poses"],
        key=lambda item: float(item["openness_percent"]),
    )
    openness = max(0.0, min(float(openness_percent), 100.0))
    for left, right in zip(poses, poses[1:]):
        left_value = float(left["openness_percent"])
        right_value = float(right["openness_percent"])
        if left_value <= openness <= right_value:
            ratio = (
                (openness - left_value) / (right_value - left_value)
                if right_value > left_value
                else 0.0
            )
            return float(left["rib_down_angle_deg"]) + ratio * (
                float(right["rib_down_angle_deg"])
                - float(left["rib_down_angle_deg"])
            )
    return float(poses[-1]["rib_down_angle_deg"])


def _pose_payload(
    names: dict[str, str],
    spec: dict,
    rib_down_angle_deg: float,
) -> dict:
    mechanism = spec["mechanism"]
    center = [float(value) / 1000 for value in mechanism["hub_center"]]
    axis = mechanism["deployment_axis"]
    radial = mechanism["reference_radial"]
    return {
        "centerX": center[0],
        "centerY": center[1],
        "centerZ": center[2],
        "deploymentAxisX": axis[0],
        "deploymentAxisY": axis[1],
        "deploymentAxisZ": axis[2],
        "referenceRadialX": radial[0],
        "referenceRadialY": radial[1],
        "referenceRadialZ": radial[2],
        "hubRadius": mechanism["hub_radius"] / 1000,
        "runnerRadius": mechanism["runner_radius"] / 1000,
        "ribLength": mechanism["rib_length"] / 1000,
        "ribAttachmentDistance": (
            mechanism["rib_attachment_distance"] / 1000
        ),
        "stretcherLength": mechanism["stretcher_length"] / 1000,
        "ribDownAngleDeg": rib_down_angle_deg,
        "runnerComponent": names["sliding_runner"],
        "bays": [
            {
                "ribComponent": names[f"rib_{index}"],
                "stretcherComponent": names[f"stretcher_{index}"],
                "phaseDeg": (index - 1) * 45,
            }
            for index in range(1, 9)
        ],
    }


def validate_radial_kinematics(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    acceptance = spec["acceptance"]
    length_tolerance = float(acceptance["rod_length_tolerance_mm"])
    endpoint_tolerance = float(
        acceptance["body_endpoint_tolerance_mm"]
    )
    orientation_tolerance = float(
        acceptance["orientation_tolerance_deg"]
    )
    symmetry_tolerance = float(
        acceptance["symmetry_tolerance_mm"]
    )
    center = [
        float(value) / 1000
        for value in spec["mechanism"]["hub_center"]
    ]
    samples: list[dict] = []
    for pose in spec["mechanism"]["poses"]:
        result = api.call(
            "/api/assembly/mechanism/radial-deployable-pose",
            _pose_payload(
                names,
                spec,
                float(pose["rib_down_angle_deg"]),
            ),
        )["result"]
        bays = result["bays"]
        rib_errors = [
            abs(
                float(item["ribLength"]) * 1000
                - float(spec["mechanism"]["rib_length"])
            )
            for item in bays
        ]
        stretcher_errors = [
            abs(
                float(item["stretcherLength"]) * 1000
                - float(spec["mechanism"]["stretcher_length"])
            )
            for item in bays
        ]
        tip_radii = [
            math.hypot(
                float(item["ribTipXYZ"][0]) - center[0],
                float(item["ribTipXYZ"][1]) - center[1],
            )
            * 1000
            for item in bays
        ]
        symmetry_error = max(tip_radii) - min(tip_radii)
        orientation_error = float(
            result["maximumOrientationErrorDeg"]
        )
        endpoint_error = (
            float(result["maximumBodyEndpointError"]) * 1000
        )
        passed = (
            max(rib_errors, default=math.inf) <= length_tolerance
            and max(stretcher_errors, default=math.inf)
            <= length_tolerance
            and orientation_error <= orientation_tolerance
            and endpoint_error <= endpoint_tolerance
            and symmetry_error <= symmetry_tolerance
            and len(bays) == int(spec["mechanism"]["bay_count"])
        )
        samples.append({
            "name": pose["name"],
            "openness_percent": pose["openness_percent"],
            "rib_down_angle_deg": pose["rib_down_angle_deg"],
            "runner_drop_mm": round(
                float(result["runnerDrop"]) * 1000,
                6,
            ),
            "tip_radius_mm": round(sum(tip_radii) / len(tip_radii), 6),
            "symmetry_error_mm": round(symmetry_error, 9),
            "maximum_rib_length_error_mm": max(rib_errors, default=math.inf),
            "maximum_stretcher_length_error_mm": max(
                stretcher_errors,
                default=math.inf,
            ),
            "maximum_orientation_error_deg": orientation_error,
            "maximum_body_endpoint_error_mm": endpoint_error,
            "passed": passed,
        })
    runner_monotonic = all(
        float(left["runner_drop_mm"]) > float(right["runner_drop_mm"])
        for left, right in zip(samples, samples[1:])
    )
    radius_monotonic = all(
        float(left["tip_radius_mm"]) < float(right["tip_radius_mm"])
        for left, right in zip(samples, samples[1:])
    )
    open_pose = spec["mechanism"]["poses"][-1]
    api.call(
        "/api/assembly/mechanism/radial-deployable-pose",
        _pose_payload(
            names,
            spec,
            float(open_pose["rib_down_angle_deg"]),
        ),
    )
    api.call("/api/assembly/save")
    return {
        "passed": (
            all(sample["passed"] for sample in samples)
            and runner_monotonic
            and radius_monotonic
        ),
        "samples": samples,
        "pose_count": len(samples),
        "runner_motion_monotonic": runner_monotonic,
        "canopy_radius_monotonic": radius_monotonic,
    }


def score_result(
    *,
    status: str,
    static_passed: bool,
    kinematics: dict,
    successful_actions: int,
    total_actions: int,
) -> float:
    static_points = 40.0 if static_passed else 0.0
    samples = kinematics.get("samples") or []
    sample_rate = (
        sum(bool(item.get("passed")) for item in samples) / len(samples)
        if samples
        else 0.0
    )
    functional_points = 30.0 * sample_rate
    if (
        kinematics.get("runner_motion_monotonic")
        and kinematics.get("canopy_radius_monotonic")
    ):
        functional_points += 10.0
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
    kinematics = validate_radial_kinematics(api, spec, observation)
    passed = static.passed and kinematics["passed"]
    status = "completed" if passed else "blocked"
    return {
        "status": status,
        "passed": passed,
        "score": score_result(
            status=status,
            static_passed=static.passed,
            kinematics=kinematics,
            successful_actions=0,
            total_actions=0,
        ),
        "static_acceptance": static.model_dump(),
        "radial_acceptance": kinematics,
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
    assembly_path = output_dir / "radial_folding_umbrella.SLDASM"
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
            validate_radial_kinematics(
                api,
                spec,
                result.final_observation,
            )
            if result.status == "completed" and static.passed
            else {"passed": False, "samples": []}
        )
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
                successful_actions=result.successful_actions,
                total_actions=result.total_actions,
            ),
            "static_acceptance": static.model_dump(),
            "radial_acceptance": kinematics,
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
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name(
            "radial_folding_umbrella_result.json"
        ),
    )
    args = parser.parse_args()
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    api = SwApi(args.sw_url)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    assembly_path = ARTIFACT_DIR / "radial_folding_umbrella.SLDASM"
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
