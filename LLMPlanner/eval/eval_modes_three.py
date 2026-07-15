"""Compare single-turn and multi-turn on three representative Web Demo prompts."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.observer import SwObserver


@dataclass(frozen=True)
class EvalCase:
    name: str
    prompt: str
    expected_dimensions_mm: tuple[float, float, float]
    feature_checks: tuple[tuple[str, int], ...]
    tolerance_mm: float = 3.0


CASES = [
    EvalCase(
        name="简化骰子",
        prompt=(
            "创建一个骰子的基础外形：先做一个边长 20mm 的正方体，然后对正方体的"
            "所有 12 条棱边做 R2mm 的圆角，使其成为一个边角圆润的立方块。"
        ),
        expected_dimensions_mm=(20, 20, 20),
        feature_checks=(("extr", 1), ("fillet", 1)),
    ),
    EvalCase(
        name="标准骰子",
        prompt=(
            "请帮我建模一颗标准的六面骰子（色子），要求如下：\n"
            "1. 骰子主体是一个边长 20mm 的正方体；\n"
            "2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；\n"
            "3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个"
            "直径约 3mm、深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；\n"
            "4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、"
            "3 对 4）；\n"
            "5. 各面点数按常规排布。请自行计算各面和凹点位置，完成完整建模。"
        ),
        expected_dimensions_mm=(20, 20, 20),
        # SolidWorks reports cut-extrude Feature.GetTypeName2() as "ICE".
        feature_checks=(("extr", 1), ("fillet", 1), ("ice", 6)),
    ),
    EvalCase(
        name="收纳盒",
        prompt=(
            "创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，"
            "盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），"
            "底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。"
        ),
        expected_dimensions_mm=(50, 80, 120),
        feature_checks=(("extr", 1), ("ice", 1), ("fillet", 1)),
    ),
]


def feature_count(features: list[dict[str, Any]], keyword: str) -> int:
    return sum(
        keyword in f"{item.get('name', '')} {item.get('type', '')}".lower()
        for item in features
    )


def evaluate(
    case: EvalCase,
    mode: str,
    web_url: str,
    observer: SwObserver,
    plan_model: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = requests.post(
            f"{web_url}/api/run",
            json={
                "instruction": case.prompt,
                "mode": mode,
                "max_turns": 8,
                "plan_model": plan_model,
                "vision_model": "GLM-4.6V",
            },
            timeout=1800,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(payload.get("error", "unknown Web API error"))

        if mode == "multi_turn":
            run = payload["multi_turn"]
            observation = run["final_observation"]
            turns = len(run["turns"])
            llm_calls = run["llm_calls"]
            status = run["status"]
            failed = [
                item
                for turn in run["turns"]
                for item in turn["execution_results"]
                if not item.get("success")
            ]
        else:
            observation = observer.collect().model_dump(mode="json")
            turns = 1
            llm_calls = 1
            status = "completed" if payload["summary"]["all_ok"] else "failed"
            failed = [
                item for item in payload["results"] if not item.get("success")
            ]

        bbox = observation.get("bounding_box_mm")
        actual_dimensions = (
            sorted(float(bbox[key]) for key in ("sizeX", "sizeY", "sizeZ"))
            if bbox
            else []
        )
        expected = sorted(case.expected_dimensions_mm)
        dimensions_ok = len(actual_dimensions) == 3 and all(
            abs(actual - wanted) <= case.tolerance_mm
            for actual, wanted in zip(actual_dimensions, expected)
        )
        features = observation.get("features") or []
        counts = {
            keyword: feature_count(features, keyword)
            for keyword, _minimum in case.feature_checks
        }
        checks = {
            keyword: counts[keyword] >= minimum
            for keyword, minimum in case.feature_checks
        }
        return {
            "case": case.name,
            "mode": mode,
            "status": status,
            "all_ok": payload["summary"]["all_ok"],
            "duration_seconds": round(time.perf_counter() - started, 2),
            "turns": turns,
            "llm_calls": llm_calls,
            "total_actions": payload["summary"]["total"],
            "successful_actions": payload["summary"]["succeeded"],
            "bbox_mm": bbox,
            "dimensions_ok": dimensions_ok,
            "feature_counts": counts,
            "feature_checks_ok": checks,
            "acceptance_ok": (
                status == "completed"
                and dimensions_ok
                and all(checks.values())
            ),
            "failed_actions": [
                {
                    "operation": item.get("operation"),
                    "message": item.get("message"),
                }
                for item in failed
            ],
            "error": None,
        }
    except Exception as exc:
        return {
            "case": case.name,
            "mode": mode,
            "status": "error",
            "duration_seconds": round(time.perf_counter() - started, 2),
            "acceptance_ok": False,
            "error": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/modes_three_result.json"),
    )
    args = parser.parse_args()
    observer = SwObserver(args.sw_url.rstrip("/"))
    report = {
        "evaluation_version": 2,
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": "AI Ping",
        "plan_model": args.plan_model,
        "max_turns": 8,
        "cases": [asdict(case) for case in CASES],
        "results": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("evaluation_version") == report["evaluation_version"]:
            report["results"] = previous.get("results", [])
            report["resumed_from"] = previous.get("run_at")
    completed = {
        (result.get("case"), result.get("mode"))
        for result in report["results"]
        if result.get("status") != "error"
    }

    for case in CASES:
        for mode in ("single_turn", "multi_turn"):
            if (case.name, mode) in completed:
                print(f"\n[{case.name}] {mode} — 已有结果，跳过", flush=True)
                continue
            print(f"\n[{case.name}] {mode}", flush=True)
            result = evaluate(
                case,
                mode,
                args.web_url.rstrip("/"),
                observer,
                args.plan_model,
            )
            report["results"].append(result)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(
                f"status={result['status']}; "
                f"acceptance={result['acceptance_ok']}; "
                f"duration={result['duration_seconds']}s",
                flush=True,
            )

    observer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
