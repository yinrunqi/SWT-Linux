"""Build and evaluate a crank-slider engine assembly with 2:1 timing gears."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly_smoke.phase1_build_two_gears import generate_involute_outline
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


SPEC_PATH = Path(__file__).with_name("single_cylinder_engine_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "single_cylinder_engine"


class SwApi:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def call(
        self,
        path: str,
        body: dict | None = None,
        *,
        timeout: int = 180,
    ) -> dict:
        response = self.session.post(
            f"{self.base_url}{path}",
            json=body or {},
            timeout=timeout,
        )
        payload = response.json()
        if not response.ok or not payload.get("success", False):
            raise RuntimeError(
                f"{path} failed: {payload.get('message', response.text)}"
            )
        return payload

    def get(self, path: str, *, timeout: int = 180) -> dict:
        response = self.session.get(
            f"{self.base_url}{path}",
            timeout=timeout,
        )
        payload = response.json()
        if not response.ok or not payload.get("success", False):
            raise RuntimeError(
                f"{path} failed: {payload.get('message', response.text)}"
            )
        return payload

    def close_all(self) -> dict:
        return self.call("/api/close-all-documents")

    def close_inactive(self) -> dict:
        return self.call("/api/close-inactive-documents")


def _component_paths(output_dir: Path) -> dict[str, Path]:
    return {
        role: output_dir / f"{role}.SLDPRT"
        for role in (
            "engine_base",
            "cylinder_block",
            "piston",
            "connecting_rod",
            "crank_disk",
            "flywheel",
            "crank_timing_gear",
            "cam_timing_gear",
            "camshaft",
            "cam_lobe",
        )
    }


def _start_sketch(api: SwApi) -> None:
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 0})


def _save_extrusion(
    api: SwApi,
    path: Path,
    depth: float,
) -> None:
    api.call("/api/feature/extrusion", {"depth": depth})
    api.call("/api/save-part-as", {"savePath": str(path)})


def _build_rectangles(
    api: SwApi,
    path: Path,
    rectangles: list[tuple[float, float, float, float]],
    *,
    depth: float,
    circles: list[tuple[float, float, float]] | None = None,
) -> None:
    _start_sketch(api)
    for x1, y1, x2, y2 in rectangles:
        api.call(
            "/api/sketch/rectangle",
            {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        )
    for cx, cy, radius in circles or []:
        api.call(
            "/api/sketch/circle",
            {"cx": cx, "cy": cy, "radius": radius},
        )
    _save_extrusion(api, path, depth)


def _build_round_part(
    api: SwApi,
    path: Path,
    *,
    radius: float,
    depth: float,
    bore_radius: float | None = None,
) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": radius},
    )
    if bore_radius:
        api.call(
            "/api/sketch/circle",
            {"cx": 0, "cy": 0, "radius": bore_radius},
        )
    _save_extrusion(api, path, depth)


def _build_crank(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.014, "y": 0},
                {"x": -0.010, "y": -0.010},
                {"x": 0, "y": -0.014},
                {"x": 0.010, "y": -0.010},
                {"x": 0.014, "y": 0},
                {"x": 0.008, "y": 0.025},
                {"x": 0.006, "y": 0.031},
                {"x": 0, "y": 0.033},
                {"x": -0.006, "y": 0.031},
                {"x": -0.008, "y": 0.025},
            ],
            "closed": True,
        },
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.004},
    )
    _save_extrusion(api, path, 0.006)


def _build_cylinder_block(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": -0.032, "y": -0.042},
                {"x": -0.021, "y": -0.042},
                {"x": -0.021, "y": 0.032},
                {"x": 0.021, "y": 0.032},
                {"x": 0.021, "y": -0.042},
                {"x": 0.032, "y": -0.042},
                {"x": 0.032, "y": 0.042},
                {"x": -0.032, "y": 0.042},
            ],
            "closed": True,
        },
    )
    _save_extrusion(api, path, 0.010)


def _build_cam_lobe(api: SwApi, path: Path) -> None:
    _start_sketch(api)
    api.call(
        "/api/sketch/ellipse",
        {
            "cx": 0,
            "cy": 0.004,
            "majorRadius": 0.018,
            "minorRadius": 0.012,
        },
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.003},
    )
    _save_extrusion(api, path, 0.008)


def _build_gear(
    api: SwApi,
    path: Path,
    *,
    teeth: int,
    module_m: float,
    pressure_angle_deg: float,
    phase_angle_deg: float,
) -> None:
    outline, _ = generate_involute_outline(
        tooth_count=teeth,
        phase_angle_deg=phase_angle_deg,
        module_m=module_m,
        pressure_angle_deg=pressure_angle_deg,
    )
    _start_sketch(api)
    api.call(
        "/api/sketch/polyline",
        {
            "points": [{"x": x, "y": y} for x, y in outline[:-1]],
            "closed": True,
        },
        timeout=300,
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": 0.005},
    )
    _save_extrusion(api, path, 0.010)


def prepare_parts(
    api: SwApi,
    spec: dict,
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[dict[str, Path], list[dict]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _component_paths(output_dir)
    timing = spec["timing_drive"]
    builders = {
        "engine_base": lambda p: _build_rectangles(
            api, p, [(-0.065, -0.045, 0.115, 0.155)], depth=0.008
        ),
        "cylinder_block": lambda p: _build_cylinder_block(api, p),
        "piston": lambda p: _build_rectangles(
            api, p, [(-0.018, -0.012, 0.018, 0.012)], depth=0.010
        ),
        "connecting_rod": lambda p: _build_rectangles(
            api,
            p,
            [(-0.009, -0.052, 0.009, 0.052)],
            depth=0.006,
            circles=[(0, -0.045, 0.004), (0, 0.045, 0.004)],
        ),
        "crank_disk": lambda p: _build_crank(api, p),
        "flywheel": lambda p: _build_round_part(
            api, p, radius=0.040, bore_radius=0.006, depth=0.010
        ),
        "crank_timing_gear": lambda p: _build_gear(
            api,
            p,
            teeth=int(timing["crank_gear_teeth"]),
            module_m=float(timing["module"]) / 1000,
            pressure_angle_deg=float(timing["pressure_angle"]),
            phase_angle_deg=0,
        ),
        "cam_timing_gear": lambda p: _build_gear(
            api,
            p,
            teeth=int(timing["cam_gear_teeth"]),
            module_m=float(timing["module"]) / 1000,
            pressure_angle_deg=float(timing["pressure_angle"]),
            phase_angle_deg=180 / int(timing["cam_gear_teeth"]),
        ),
        "camshaft": lambda p: _build_round_part(
            api, p, radius=0.007, bore_radius=0.002, depth=0.012
        ),
        "cam_lobe": lambda p: _build_cam_lobe(api, p),
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


def _user_goal(
    paths: dict[str, Path],
    spec: dict,
    assembly_path: Path,
) -> str:
    components = {
        item["role"]: item["translation"]
        for item in spec["components"]
    }
    lines = [
        "创建带 2:1 正时齿轮的单缸曲柄滑块发动机装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下参数化零件已经建好。按给定装配体坐标插入，坐标从 mm 换算为 m：",
    ]
    for role, path in paths.items():
        lines.append(f"- {role}: {path}，位置 {components[role]} mm")
    lines.extend([
        "曲柄半径 25 mm，连杆两孔中心距 90 mm，活塞沿 +Y 滑动，旋转轴为 +Z。",
        "建立曲柄与飞轮、曲柄正时齿轮的 1:1 同向同步关系；"
        "16 齿曲柄正时齿轮与 32 齿凸轮正时齿轮为 2:1 外啮合；"
        "凸轮齿轮、凸轮轴和凸轮保持 1:1 同向同步。",
        "使用 observation 中的真实组件实例名。建立传动关系后，"
        "用 assembly_set_crank_slider_pose 检查 0° 初始姿态；"
        "飞轮和曲柄齿轮同速，凸轮齿轮、凸轮轴和凸轮反向半速。",
        "最后重建、检查干涉并保存。",
    ])
    return "\n".join(lines)


def _positions(spec: dict) -> list[dict]:
    return [
        {
            "path_contains": item["role"],
            "translation_mm": item["translation"],
        }
        for item in spec["components"]
    ]


def _task_plan(
    goal: str,
    spec: dict,
    assembly_path: Path,
) -> TaskPlan:
    return TaskPlan(
        user_goal=goal,
        milestones=[
            Milestone(
                id="engine_structure",
                title="新建装配体并按参数位置插入十个组件",
                postconditions=[
                    PostconditionSpec(
                        kind="component_count",
                        params={"exact": 10},
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
                max_failures=7,
            ),
            Milestone(
                id="timing_relations",
                title=(
                    "建立曲柄系、2:1 正时齿轮和凸轮系共五个 Gear Mate"
                ),
                depends_on=["engine_structure"],
                postconditions=[
                    PostconditionSpec(
                        kind="mate_type_count",
                        params={"keyword": "gear", "minimum": 5},
                    ),
                    PostconditionSpec(
                        kind="gear_ratios",
                        params={"expected": [1.0, 1.0, 1.0, 1.0, 2.0]},
                    ),
                ],
                max_actions=5,
                max_failures=7,
            ),
            Milestone(
                id="mechanism_pose",
                title="设置并验证曲柄滑块 0 度初始姿态及同步旋转组件",
                depends_on=["timing_relations"],
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
                title="重建、干涉检查并保存最终装配体",
                depends_on=["mechanism_pose"],
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
            "测例只在评测层定义尺寸、组件和验收；生产 Prompt 只使用通用机构规则。"
        ),
        recovery_buffer=10,
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
            params={"keyword": "gear", "minimum": 5},
        ),
        PostconditionSpec(
            kind="gear_ratios",
            params={"expected": [1.0, 1.0, 1.0, 1.0, 2.0]},
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
        for role in _component_paths(Path(".")).keys():
            if role.lower() in path:
                resolved[role] = name
    missing = sorted(set(_component_paths(Path(".")).keys()) - set(resolved))
    if missing:
        raise RuntimeError(f"无法解析组件实例名: {missing}")
    return resolved


def _pose_payload(
    names: dict[str, str],
    spec: dict,
    angle_deg: float,
) -> dict:
    return {
        "crankComponent": names["crank_disk"],
        "rodComponent": names["connecting_rod"],
        "sliderComponent": names["piston"],
        "centerX": 0,
        "centerY": 0,
        "centerZ": 0,
        "crankRadius": spec["mechanism"]["crank_radius"] / 1000,
        "rodLength": spec["mechanism"]["connecting_rod_length"] / 1000,
        "sliderAxisX": 0,
        "sliderAxisY": 1,
        "sliderAxisZ": 0,
        "rotationAxisX": 0,
        "rotationAxisY": 0,
        "rotationAxisZ": 1,
        "crankAxisOffset": 0,
        "rodAxisOffset": 0.007,
        "sliderAxisOffset": 0.014,
        "angleDeg": angle_deg,
        "synchronizedComponents": [
            {
                "componentName": names["flywheel"],
                "x": 0,
                "y": 0,
                "z": -0.012,
                "ratio": 1,
            },
            {
                "componentName": names["crank_timing_gear"],
                "x": 0,
                "y": 0,
                "z": 0.030,
                "ratio": 1,
            },
            {
                "componentName": names["cam_timing_gear"],
                "x": 0.072,
                "y": 0,
                "z": 0.030,
                "ratio": 2,
                "reverse": True,
            },
            {
                "componentName": names["camshaft"],
                "x": 0.072,
                "y": 0,
                "z": 0,
                "ratio": 2,
                "reverse": True,
            },
            {
                "componentName": names["cam_lobe"],
                "x": 0.072,
                "y": 0,
                "z": -0.012,
                "ratio": 2,
                "reverse": True,
            },
        ],
    }


def validate_kinematics(
    api: SwApi,
    spec: dict,
    observation: ModelObservation | None = None,
) -> dict:
    observation = observation or _observation(api)
    names = _resolve_components(observation)
    radius = float(spec["mechanism"]["crank_radius"])
    rod = float(spec["mechanism"]["connecting_rod_length"])
    tolerance = float(spec["acceptance"]["kinematic_tolerance_mm"])
    samples: list[dict] = []
    for angle in spec["mechanism"]["sample_angles"]:
        result = api.call(
            "/api/assembly/mechanism/crank-slider-pose",
            _pose_payload(names, spec, float(angle)),
        )["result"]
        angle_rad = math.radians(float(angle))
        expected = (
            radius * math.cos(angle_rad)
            + math.sqrt(
                rod * rod
                - radius * radius * math.sin(angle_rad) ** 2
            )
        )
        actual = float(result["sliderDistance"]) * 1000
        synchronized = result.get("synchronized", [])
        cam_angles = [
            float(item["angleDeg"])
            for item in synchronized[2:]
        ]
        timing_ok = all(
            math.isclose(
                cam_angle,
                -float(angle) / 2,
                abs_tol=float(
                    spec["acceptance"]["timing_ratio_tolerance"]
                ),
            )
            for cam_angle in cam_angles
        )
        samples.append({
            "angle_deg": angle,
            "expected_slider_mm": round(expected, 6),
            "actual_slider_mm": round(actual, 6),
            "position_error_mm": round(abs(actual - expected), 9),
            "timing_angles_deg": cam_angles,
            "timing_ok": timing_ok,
            "passed": abs(actual - expected) <= tolerance and timing_ok,
        })
    actual_stroke = max(
        sample["actual_slider_mm"] for sample in samples
    ) - min(sample["actual_slider_mm"] for sample in samples)
    expected_stroke = float(spec["mechanism"]["stroke"])
    cycle_closed = math.isclose(
        samples[0]["actual_slider_mm"],
        samples[-1]["actual_slider_mm"],
        abs_tol=tolerance,
    )
    api.call(
        "/api/assembly/mechanism/crank-slider-pose",
        _pose_payload(names, spec, 0),
    )
    api.call("/api/assembly/save")
    passed = (
        all(sample["passed"] for sample in samples)
        and abs(actual_stroke - expected_stroke) <= tolerance
        and cycle_closed
    )
    return {
        "passed": passed,
        "samples": samples,
        "expected_stroke_mm": expected_stroke,
        "actual_stroke_mm": round(actual_stroke, 6),
        "cycle_closed": cycle_closed,
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
    kinematics = validate_kinematics(api, spec, observation)
    passed = static.passed and kinematics["passed"]
    return {
        "status": "completed" if passed else "blocked",
        "passed": passed,
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
    assembly_path = output_dir / "single_cylinder_engine.SLDASM"
    goal = _user_goal(paths, spec, assembly_path)
    task_plan = _task_plan(goal, spec, assembly_path)
    client = SwLLMClient(
        plan_model=model,
        use_cache=use_cache,
        skill_mode="routed",
    )
    executor = SwExecutor(base_url=sw_url)
    api = SwApi(sw_url)
    agent = MultiTurnAgent(client, executor, max_turns=max_turns, hide_targets=True)
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
        passed = (
            result.status == "completed"
            and static.passed
            and kinematics["passed"]
        )
        return {
            "status": result.status,
            "passed": passed,
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
    parser.add_argument("--max-turns", type=int, default=24)
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
    assembly_path = ARTIFACT_DIR / "single_cylinder_engine.SLDASM"
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
