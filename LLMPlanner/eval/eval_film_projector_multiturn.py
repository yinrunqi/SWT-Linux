"""Build and evaluate a vintage film projector with intermittent indexing."""

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
    _build_crank,
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


SPEC_PATH = Path(__file__).with_name("film_projector_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "film_projector"


ROLES = (
    "projector_base",
    "projector_body",
    "supply_reel",
    "takeup_reel",
    "intermittent_driver",
    "geneva_wheel",
    "feed_sprocket",
    "two_blade_shutter",
    "film_gate",
    "pressure_plate",
    "lens_barrel",
    "focus_carriage",
    "lamp_housing",
    "reflector",
    "cooling_fan",
)


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {
        role: output_dir / f"{role}.SLDPRT"
        for role in ROLES
    }


def _circle_pattern(
    api: SwApi,
    *,
    count: int,
    center_radius: float,
    hole_radius: float,
    phase_deg: float = 0,
) -> None:
    for index in range(count):
        angle = math.radians(phase_deg + index * 360 / count)
        api.call(
            "/api/sketch/circle",
            {
                "cx": center_radius * math.cos(angle),
                "cy": center_radius * math.sin(angle),
                "radius": hole_radius,
            },
        )


def _build_reel(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.032},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.006},
    )
    _circle_pattern(
        api,
        count=6,
        center_radius=0.020,
        hole_radius=0.005,
        phase_deg=30,
    )
    _save_extrusion(api, path, 0.006)


def _build_geneva_wheel(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.026},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.004},
    )
    for rectangle in (
        (-0.003, 0.009, 0.003, 0.022),
        (-0.003, -0.022, 0.003, -0.009),
        (0.009, -0.003, 0.022, 0.003),
        (-0.022, -0.003, -0.009, 0.003),
    ):
        api.call(
            "/api/sketch/rectangle",
            dict(zip(("x1", "y1", "x2", "y2"), rectangle)),
        )
    _save_extrusion(api, path, 0.008)


def _build_sprocket(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.014},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.004},
    )
    _circle_pattern(
        api,
        count=8,
        center_radius=0.010,
        hole_radius=0.0015,
        phase_deg=22.5,
    )
    _save_extrusion(api, path, 0.008)


def _build_shutter(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.028},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.004},
    )
    for cy in (-0.014, 0.014):
        api.call(
            "/api/sketch/circle",
            {"cx": 0, "cy": cy, "radius": 0.009},
        )
    _save_extrusion(api, path, 0.004)


def _build_fan(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.020},
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.004},
    )
    _circle_pattern(
        api,
        count=6,
        center_radius=0.012,
        hole_radius=0.0035,
    )
    _save_extrusion(api, path, 0.006)


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    del spec
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    builders = {
        "projector_base": lambda p: _build_rectangles(
            api, p, [(-0.110, -0.090, 0.110, 0.090)], depth=0.008
        ),
        "projector_body": lambda p: _build_rectangles(
            api,
            p,
            [(-0.065, -0.075, 0.065, 0.075)],
            depth=0.010,
            circles=None,
        ),
        "supply_reel": lambda p: _build_reel(api, p),
        "takeup_reel": lambda p: _build_reel(api, p),
        "intermittent_driver": lambda p: _build_crank(api, p),
        "geneva_wheel": lambda p: _build_geneva_wheel(api, p),
        "feed_sprocket": lambda p: _build_sprocket(api, p),
        "two_blade_shutter": lambda p: _build_shutter(api, p),
        "film_gate": lambda p: _build_rectangles(
            api,
            p,
            [(-0.012, -0.017, 0.012, 0.017)],
            depth=0.004,
            circles=None,
        ),
        "pressure_plate": lambda p: _build_rectangles(
            api, p, [(-0.010, -0.015, 0.010, 0.015)], depth=0.003
        ),
        "lens_barrel": lambda p: _build_round_part(
            api, p, radius=0.016, bore_radius=0.010, depth=0.045
        ),
        "focus_carriage": lambda p: _build_rectangles(
            api,
            p,
            [(-0.022, -0.022, 0.022, 0.022)],
            depth=0.008,
            circles=[(0, 0, 0.018)],
        ),
        "lamp_housing": lambda p: _build_rectangles(
            api,
            p,
            [(-0.028, -0.036, 0.028, 0.036)],
            depth=0.012,
            circles=[(0, 0, 0.020)],
        ),
        "reflector": lambda p: _build_round_part(
            api, p, radius=0.024, bore_radius=0.006, depth=0.004
        ),
        "cooling_fan": lambda p: _build_fan(api, p),
    }
    log: list[dict] = []
    api.close_all()
    for role, builder in builders.items():
        path = paths[role]
        started = time.perf_counter()
        if force or not path.is_file():
            builder(path)
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


def _positions(spec: dict) -> list[dict]:
    return [
        {
            "path_contains": item["role"],
            "translation_mm": item["translation"],
        }
        for item in spec["components"]
    ]


def _user_goal(
    paths: dict[str, Path],
    spec: dict,
    assembly_path: Path,
) -> str:
    positions = {
        item["role"]: item["translation"]
        for item in spec["components"]
    }
    lines = [
        "创建一台带四槽间歇送片机构的复古 16mm 电影放映机装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下零件已经参数化建好。按给定装配体坐标插入，mm 必须换算为 m：",
    ]
    for role, path in paths.items():
        lines.append(f"- {role}: {path}，位置 {positions[role]} mm")
    lines.extend([
        "主动拨盘连续旋转，四槽分度轮每个输入周期间歇前进 90°；"
        "啮合区为输入相位 120° 到 240°，其余相位锁止。",
        "建立五个传动关系：主动拨盘与双叶快门 1:1；主动拨盘与冷却风扇 1:2；"
        "分度轮与送片齿轮 1:1；送片齿轮分别以 4:1 带动供片卷盘和收片卷盘。",
        "快门相对输入固定超前 90°。供片卷盘与收片卷盘方向相反。",
        "使用 observation 中的真实组件实例名。建立传动关系后调用"
        " assembly_set_indexed_rotary_pose 检查 0° 初始姿态，最后重建并保存。",
    ])
    return "\n".join(lines)


def _task_plan(
    goal: str,
    spec: dict,
    assembly_path: Path,
) -> TaskPlan:
    return TaskPlan(
        user_goal=goal,
        milestones=[
            Milestone(
                id="projector_structure",
                title="新建装配体并按参数位置插入十五个放映机组件",
                postconditions=[
                    PostconditionSpec(
                        kind="component_count",
                        params={"exact": 15},
                    ),
                    PostconditionSpec(
                        kind="component_positions",
                        params={
                            "expected": _positions(spec),
                            "tolerance_mm": 1.0,
                        },
                    ),
                ],
                max_actions=5,
                max_failures=8,
            ),
            Milestone(
                id="transport_relations",
                title=(
                    "建立快门、风扇、送片轮和双卷盘共五个 Gear Mate"
                ),
                depends_on=["projector_structure"],
                postconditions=[
                    PostconditionSpec(
                        kind="mate_type_count",
                        params={"keyword": "gear", "minimum": 5},
                    ),
                    PostconditionSpec(
                        kind="gear_ratios",
                        params={"expected": [1, 1, 2, 4, 4]},
                    ),
                ],
                max_actions=5,
                max_failures=7,
            ),
            Milestone(
                id="indexed_pose",
                title="设置四槽间歇分度机构的 0 度初始姿态",
                depends_on=["transport_relations"],
                postconditions=[
                    PostconditionSpec(
                        kind="component_positions",
                        params={
                            "expected": _positions(spec),
                            "tolerance_mm": 1.0,
                        },
                    ),
                ],
                max_actions=2,
                max_failures=5,
            ),
            Milestone(
                id="final_validation",
                title="重建并保存电影放映机装配体",
                depends_on=["indexed_pose"],
                postconditions=[
                    PostconditionSpec(kind="valid_assembly"),
                    PostconditionSpec(
                        kind="assembly_saved",
                        params={"path": str(assembly_path)},
                    ),
                ],
                max_actions=3,
                max_failures=4,
            ),
        ],
        notes=(
            "尺寸、组件和采样点仅位于评测层；生产 Skill 只描述通用间歇分度关系。"
        ),
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
            kind="mate_type_count",
            params={
                "keyword": "gear",
                "minimum": int(
                    spec["acceptance"]["minimum_gear_mate_count"]
                ),
            },
        ),
        PostconditionSpec(
            kind="gear_ratios",
            params={"expected": [1, 1, 2, 4, 4]},
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
        raise RuntimeError(f"无法解析组件实例名: {missing}")
    return resolved


def _pose_payload(
    names: dict[str, str],
    spec: dict,
    input_angle_deg: float,
) -> dict:
    indexer = spec["indexer"]
    return {
        "driverComponent": names["intermittent_driver"],
        "indexedComponent": names["geneva_wheel"],
        "inputAngleDeg": input_angle_deg,
        "slotCount": indexer["slot_count"],
        "engagementStartDeg": indexer["engagement_start_deg"],
        "engagementEndDeg": indexer["engagement_end_deg"],
        "axisX": 0,
        "axisY": 0,
        "axisZ": 1,
        "continuousComponents": [
            {
                "componentName": names["two_blade_shutter"],
                "ratio": 1,
                "phaseDeg": spec["film_transport"]["shutter_phase_deg"],
            },
            {
                "componentName": names["cooling_fan"],
                "ratio": 0.5,
            },
        ],
        "indexedComponents": [
            {
                "componentName": names["feed_sprocket"],
                "ratio": 1,
            },
            {
                "componentName": names["takeup_reel"],
                "ratio": 4,
            },
            {
                "componentName": names["supply_reel"],
                "ratio": 4,
                "reverse": True,
            },
        ],
    }


def _expected_output(spec: dict, input_angle_deg: float) -> tuple[float, bool]:
    indexer = spec["indexer"]
    cycles = math.floor(input_angle_deg / 360)
    phase = input_angle_deg - cycles * 360
    start = float(indexer["engagement_start_deg"])
    end = float(indexer["engagement_end_deg"])
    if phase <= start:
        progress = 0.0
        engaged = False
    elif phase >= end:
        progress = 1.0
        engaged = False
    else:
        normalized = (phase - start) / (end - start)
        progress = normalized * normalized * (3 - 2 * normalized)
        engaged = True
    return (
        (cycles + progress) * float(indexer["step_angle_deg"]),
        engaged,
    )


def validate_indexing(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    tolerance = float(spec["acceptance"]["index_angle_tolerance_deg"])
    samples: list[dict] = []
    for input_angle in spec["indexer"]["sample_angles_deg"]:
        result = api.call(
            "/api/assembly/mechanism/indexed-rotary-pose",
            _pose_payload(names, spec, float(input_angle)),
        )["result"]
        expected, expected_engaged = _expected_output(
            spec,
            float(input_angle),
        )
        actual = float(result["outputAngleDeg"])
        continuous = result.get("continuous", [])
        indexed = result.get("indexed", [])
        expected_shutter = (
            float(input_angle)
            + float(spec["film_transport"]["shutter_phase_deg"])
        )
        phase_ok = (
            len(continuous) == 2
            and math.isclose(
                float(continuous[0]["angleDeg"]),
                expected_shutter,
                abs_tol=tolerance,
            )
            and math.isclose(
                float(continuous[1]["angleDeg"]),
                float(input_angle) * 2,
                abs_tol=tolerance,
            )
            and len(indexed) == 3
            and math.isclose(
                float(indexed[0]["angleDeg"]),
                expected,
                abs_tol=tolerance,
            )
            and math.isclose(
                float(indexed[1]["angleDeg"]),
                expected / 4,
                abs_tol=tolerance,
            )
            and math.isclose(
                float(indexed[2]["angleDeg"]),
                -expected / 4,
                abs_tol=tolerance,
            )
        )
        passed = (
            abs(actual - expected) <= tolerance
            and bool(result["engaged"]) == expected_engaged
            and phase_ok
        )
        samples.append({
            "input_angle_deg": input_angle,
            "expected_output_deg": round(expected, 9),
            "actual_output_deg": round(actual, 9),
            "output_error_deg": round(abs(actual - expected), 12),
            "engaged": result["engaged"],
            "dwell": result["dwell"],
            "phase_ok": phase_ok,
            "passed": passed,
        })
    dwell_pairs = [
        (0, 1),
        (4, 5),
        (5, 6),
    ]
    dwell_locked = all(
        math.isclose(
            samples[left]["actual_output_deg"],
            samples[right]["actual_output_deg"],
            abs_tol=tolerance,
        )
        for left, right in dwell_pairs
    )
    full_cycle_output = samples[-1]["actual_output_deg"]
    full_cycle_closed = math.isclose(
        full_cycle_output,
        360.0,
        abs_tol=tolerance,
    )
    api.call(
        "/api/assembly/mechanism/indexed-rotary-pose",
        _pose_payload(names, spec, 0),
    )
    api.call("/api/assembly/save")
    passed = (
        all(sample["passed"] for sample in samples)
        and dwell_locked
        and full_cycle_closed
    )
    return {
        "passed": passed,
        "samples": samples,
        "dwell_locked": dwell_locked,
        "four_input_cycles_output_deg": full_cycle_output,
        "full_cycle_closed": full_cycle_closed,
    }


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
    indexing = validate_indexing(api, spec, observation)
    passed = static.passed and indexing["passed"]
    return {
        "status": "completed" if passed else "blocked",
        "passed": passed,
        "static_acceptance": static.model_dump(),
        "indexing_acceptance": indexing,
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
    assembly_path = output_dir / "vintage_film_projector.SLDASM"
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
        indexing = (
            validate_indexing(api, spec, result.final_observation)
            if result.status == "completed" and static.passed
            else {"passed": False, "samples": []}
        )
        passed = (
            result.status == "completed"
            and static.passed
            and indexing["passed"]
        )
        return {
            "status": result.status,
            "passed": passed,
            "static_acceptance": static.model_dump(),
            "indexing_acceptance": indexing,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "token_usage": client.usage_snapshot(),
            "result": result.model_dump(),
            "task_plan": task_plan.model_dump(),
        }
    except Exception as exc:
        return {
            "status": "error",
            "passed": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "token_usage": client.usage_snapshot(),
            "error": f"{type(exc).__name__}: {exc}",
            "task_plan": task_plan.model_dump(),
        }
    finally:
        executor.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=28)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args()

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    api = SwApi(args.sw_url)
    started = time.perf_counter()
    paths = _component_paths(ARTIFACT_DIR)
    if args.validate_existing:
        build_log = []
    else:
        paths, build_log = prepare_parts(
            api,
            spec,
            ARTIFACT_DIR,
            force=args.force_parts,
        )
    payload: dict = {
        "case_id": spec["case_id"],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "build_log": build_log,
    }
    assembly_path = ARTIFACT_DIR / "vintage_film_projector.SLDASM"
    if args.validate_existing:
        payload["agent_run"] = validate_existing(
            api,
            spec,
            assembly_path,
        )
        payload["window_cleanup"] = api.close_inactive()
    elif not args.prepare_only:
        payload["agent_run"] = run_agent(
            args.sw_url,
            paths,
            spec,
            ARTIFACT_DIR,
            model=args.model,
            use_cache=args.use_cache,
            max_turns=args.max_turns,
        )
        payload["window_cleanup"] = api.close_inactive()
    payload["total_duration_seconds"] = round(
        time.perf_counter() - started,
        3,
    )
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    result_path = ARTIFACT_DIR / (
        "validation_result.json"
        if args.validate_existing
        else "result.json"
    )
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"result: {result_path}")


if __name__ == "__main__":
    main()
