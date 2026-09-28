"""Build and evaluate a parameterized two-stage gearbox assembly."""

from __future__ import annotations

import argparse
import json
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


SPEC_PATH = Path(__file__).with_name("two_stage_gearbox_spec.json")
ARTIFACT_DIR = Path(__file__).parent / "artifacts" / "two_stage_gearbox"


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
        "housing_base": output_dir / "housing_base.SLDPRT",
        "input_pinion": output_dir / "input_pinion_z12.SLDPRT",
        "intermediate_wheel":
            output_dir / "intermediate_wheel_z24.SLDPRT",
        "intermediate_pinion":
            output_dir / "intermediate_pinion_z12.SLDPRT",
        "output_wheel": output_dir / "output_wheel_z36.SLDPRT",
    }


def _build_housing_base(api: SwApi, path: Path) -> None:
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 0})
    api.call(
        "/api/sketch/rectangle",
        {"x1": -0.090, "y1": -0.065, "x2": 0.140, "y2": 0.065},
    )
    api.call("/api/feature/extrusion", {"depth": 0.010})
    api.call("/api/save-part-as", {"savePath": str(path)})


def _build_gear(
    api: SwApi,
    path: Path,
    *,
    teeth: int,
    phase_angle_deg: float,
    module_m: float,
    pressure_angle_deg: float,
    thickness_m: float,
    bore_radius_m: float,
) -> None:
    outline, _ = generate_involute_outline(
        tooth_count=teeth,
        phase_angle_deg=phase_angle_deg,
        module_m=module_m,
        pressure_angle_deg=pressure_angle_deg,
    )
    api.call("/api/new-part")
    api.call("/api/select-plane", {"index": 0})
    api.call(
        "/api/sketch/polyline",
        {
            "points": [
                {"x": x, "y": y}
                for x, y in outline[:-1]
            ],
            "closed": True,
        },
        timeout=300,
    )
    api.call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": bore_radius_m},
    )
    api.call("/api/feature/extrusion", {"depth": thickness_m})
    api.call("/api/save-part-as", {"savePath": str(path)})


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
    parameters = {
        "module_m": float(standard["module"]) / 1000,
        "pressure_angle_deg": float(standard["pressure_angle"]),
        "thickness_m": float(standard["face_width"]) / 1000,
        "bore_radius_m": float(standard["bore_diameter"]) / 2000,
    }
    build_log: list[dict] = []
    definitions = [
        ("housing_base", None, 0.0),
        ("input_pinion", 12, 0.0),
        ("intermediate_wheel", 24, 7.5),
        ("intermediate_pinion", 12, 0.0),
        ("output_wheel", 36, 5.0),
    ]
    api.close_all()
    for role, teeth, phase in definitions:
        path = paths[role]
        started = time.perf_counter()
        if force or not path.is_file():
            if teeth is None:
                _build_housing_base(api, path)
            else:
                _build_gear(
                    api,
                    path,
                    teeth=teeth,
                    phase_angle_deg=phase,
                    **parameters,
                )
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


def _user_goal(paths: dict[str, Path], assembly_path: Path) -> str:
    lines = [
        "创建一个参数化双级圆柱齿轮减速装配体。",
        f"最终装配体保存到：{assembly_path}",
        "以下零件已经建好，请按给定装配体坐标插入（API 坐标单位换算为米）：",
        f"- 安装底板：{paths['housing_base']}，位置 (0, 0, -20) mm",
        f"- 输入 12 齿小齿轮：{paths['input_pinion']}，位置 (-54, 0, 0) mm",
        f"- 中间轴 24 齿大齿轮：{paths['intermediate_wheel']}，位置 (0, 0, 0) mm",
        f"- 中间轴 12 齿小齿轮：{paths['intermediate_pinion']}，位置 (0, 0, 20) mm",
        f"- 输出 36 齿大齿轮：{paths['output_wheel']}，位置 (72, 0, 20) mm",
        "第一级为 12:24 外啮合，第二级为 12:36 外啮合；"
        "中间轴两个齿轮保持 1:1 同向同步，总减速比应为 6:1。",
        "同层齿轮前视基准面要重合。使用 observation 中的真实组件实例名，"
        "完成后重建、确认无实体干涉并保存。",
    ]
    return "\n".join(lines)


def _task_plan(
    goal: str,
    assembly_path: Path,
) -> TaskPlan:
    positions = [
        ("housing_base", [0, 0, -20]),
        ("input_pinion_z12", [-54, 0, 0]),
        ("intermediate_wheel_z24", [0, 0, 0]),
        ("intermediate_pinion_z12", [0, 0, 20]),
        ("output_wheel_z36", [72, 0, 20]),
    ]
    return TaskPlan(
        user_goal=goal,
        milestones=[
            Milestone(
                id="assembly_structure",
                title="新建装配体并按参数位置插入五个组件",
                postconditions=[
                    PostconditionSpec(
                        kind="component_count",
                        params={"exact": 5},
                    ),
                    PostconditionSpec(
                        kind="component_positions",
                        params={
                            "expected": [
                                {
                                    "path_contains": role,
                                    "translation_mm": translation,
                                }
                                for role, translation in positions
                            ],
                            "tolerance_mm": 1.0,
                        },
                    ),
                ],
                max_actions=5,
                max_failures=6,
            ),
            Milestone(
                id="drive_relations",
                title=(
                    "建立 3 个 Gear Mate（两级外啮合 + 中间轴 1:1 同向同步）"
                    "和 2 个 Coincident Mate（两个层面的轴向对齐）"
                ),
                depends_on=["assembly_structure"],
                postconditions=[
                    PostconditionSpec(
                        kind="mate_type_count",
                        params={"keyword": "gear", "minimum": 3},
                    ),
                    PostconditionSpec(
                        kind="mate_type_count",
                        params={"keyword": "coincident", "minimum": 2},
                    ),
                    PostconditionSpec(
                        kind="gear_ratios",
                        params={"expected": [1.0, 2.0, 3.0]},
                    ),
                ],
                max_actions=5,
                max_failures=6,
            ),
            Milestone(
                id="final_validation",
                title="重建、干涉检查并保存最终装配体",
                depends_on=["drive_relations"],
                postconditions=[
                    PostconditionSpec(kind="valid_assembly"),
                    PostconditionSpec(
                        kind="no_interference",
                        params={"maximum": 0},
                    ),
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
            "评测只提供用户可见的参数、零件路径和功能关系；"
            "Agent 必须通过观察获取真实组件名并逐里程碑完成装配。"
        ),
        recovery_buffer=8,
    )


def _acceptance_specs(assembly_path: Path) -> list[PostconditionSpec]:
    return [
        PostconditionSpec(kind="valid_assembly"),
        PostconditionSpec(
            kind="component_count",
            params={"exact": 5},
        ),
        PostconditionSpec(
            kind="component_positions",
            params={
                "expected": [
                    {
                        "path_contains": "housing_base",
                        "translation_mm": [0, 0, -20],
                    },
                    {
                        "path_contains": "input_pinion_z12",
                        "translation_mm": [-54, 0, 0],
                    },
                    {
                        "path_contains": "intermediate_wheel_z24",
                        "translation_mm": [0, 0, 0],
                    },
                    {
                        "path_contains": "intermediate_pinion_z12",
                        "translation_mm": [0, 0, 20],
                    },
                    {
                        "path_contains": "output_wheel_z36",
                        "translation_mm": [72, 0, 20],
                    },
                ],
                "tolerance_mm": 1.0,
            },
        ),
        PostconditionSpec(
            kind="mate_type_count",
            params={"keyword": "gear", "minimum": 3},
        ),
        PostconditionSpec(
            kind="mate_type_count",
            params={"keyword": "coincident", "minimum": 2},
        ),
        PostconditionSpec(
            kind="gear_ratios",
            params={"expected": [1.0, 2.0, 3.0]},
        ),
        PostconditionSpec(
            kind="no_interference",
            params={"maximum": 0},
        ),
        PostconditionSpec(
            kind="assembly_saved",
            params={"path": str(assembly_path)},
        ),
    ]


def validate_existing(api: SwApi, assembly_path: Path) -> dict:
    api.call("/api/assembly/open", {"assemblyPath": str(assembly_path)})
    result = api.get("/api/assembly/observation")["result"]
    interference = api.call("/api/assembly/check-interference")["result"]
    observation = ModelObservation(
        document_type="assembly",
        assembly_path=str(result.get("assemblyPath", "")),
        components=result.get("components", []),
        mates=result.get("mates", []),
        interference_summary=interference,
    )
    validation = ValidatorRegistry.validate(
        _acceptance_specs(assembly_path),
        observation,
    )
    return {
        "status": "completed" if validation.passed else "blocked",
        "passed": validation.passed,
        "acceptance": validation.model_dump(),
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
    output_dir: Path,
    *,
    model: str | None,
    use_cache: bool,
    max_turns: int,
) -> dict:
    ensure_test_env_loaded()
    assembly_path = output_dir / "two_stage_gearbox.SLDASM"
    goal = _user_goal(paths, assembly_path)
    task_plan = _task_plan(goal, assembly_path)
    client = SwLLMClient(
        plan_model=model,
        use_cache=use_cache,
        skill_mode="routed",
    )
    executor = SwExecutor(base_url=sw_url)
    agent = MultiTurnAgent(
        client,
        executor,
        max_turns=max_turns,
        hide_targets=True,
    )
    started = time.perf_counter()
    try:
        result = agent.run(goal, task_plan=task_plan)
        acceptance = ValidatorRegistry.validate(
            _acceptance_specs(assembly_path),
            result.final_observation,
        )
        return {
            "status": result.status,
            "acceptance": acceptance.model_dump(),
            "passed": result.status == "completed" and acceptance.passed,
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
    parser.add_argument("--max-turns", type=int, default=18)
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--force-parts", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--validate-existing", action="store_true")
    args = parser.parse_args()

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    output_dir = ARTIFACT_DIR
    api = SwApi(args.sw_url)
    started = time.perf_counter()
    paths = _component_paths(output_dir)
    if args.validate_existing:
        build_log = []
    else:
        paths, build_log = prepare_parts(
            api,
            spec,
            output_dir,
            force=args.force_parts,
        )
    payload: dict = {
        "case_id": spec["case_id"],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "build_log": build_log,
    }
    if args.validate_existing:
        payload["agent_run"] = validate_existing(
            api,
            output_dir / "two_stage_gearbox.SLDASM",
        )
        payload["window_cleanup"] = api.close_inactive()
    elif not args.prepare_only:
        payload["agent_run"] = run_agent(
            args.sw_url,
            paths,
            output_dir,
            model=args.model,
            use_cache=args.use_cache,
            max_turns=args.max_turns,
        )
        payload["window_cleanup"] = api.close_inactive()
    payload["total_duration_seconds"] = round(
        time.perf_counter() - started,
        3,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / (
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
