"""Score the complex C4 three-view desktop pen-holder challenge."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

try:
    from .eval_three_view_multiturn import (
        DATASET,
        ROOT,
        _load_first_samples,
        run_one,
    )
except ImportError:
    from eval_three_view_multiturn import (
        DATASET,
        ROOT,
        _load_first_samples,
        run_one,
    )


def _close(value: float, target: float, tolerance: float) -> bool:
    return abs(float(value) - target) <= tolerance


def _feature_counts(observation: dict) -> dict:
    features = observation.get("features") or []
    links = observation.get("feature_sketch_links") or []
    return {
        "cuts": sum(
            str(item.get("operationKind", "")).lower() == "cut"
            for item in links
        ),
        "extrusions": sum(
            str(item.get("operationKind", "")).lower() == "extrusion"
            for item in links
        ),
        "fillets": sum(
            "fillet" in str(item.get("type", "")).lower()
            or "圆角" in str(item.get("name", ""))
            for item in features
        ),
        "faces": len(observation.get("faces") or []),
        "total": len(features),
    }


def score_pen_holder(result: dict, groundtruth: dict) -> dict:
    observation = result.get("final_observation") or {}
    bbox = result.get("bbox_mm") or {}
    expected = groundtruth["expected_bbox_mm"]
    actual_sizes = sorted(
        float(bbox.get(key) or 0)
        for key in ("sizeX", "sizeY", "sizeZ")
    )
    expected_sizes = sorted(
        float(expected[key])
        for key in ("length", "height", "depth")
    )
    dimension_checks = [
        _close(actual, target, 4.0)
        for actual, target in zip(actual_sizes, expected_sizes)
    ]
    dimension_points = round(
        25.0 * sum(dimension_checks) / 3.0,
        2,
    )

    volume = float(result.get("volume_mm3") or 0)
    expected_volume = float(groundtruth["expected_volume_mm3"])
    relative_error = (
        abs(volume - expected_volume) / expected_volume
        if volume > 0 and expected_volume > 0
        else 1.0
    )
    volume_points = round(
        15.0 * max(0.0, 1.0 - relative_error / 0.15),
        2,
    )

    counts = _feature_counts(observation)
    topology_points = 0.0
    topology_points += 8.0 if volume > 0 else 0.0
    topology_points += 6.0 if counts["faces"] >= 24 else 0.0
    topology_points += 6.0 if counts["total"] >= 12 else 0.0

    cut_depths = sorted(
        round(float(item.get("depth", 0)) * 1000, 2)
        for item in observation.get("feature_sketch_links") or []
        if str(item.get("operationKind", "")).lower() == "cut"
    )
    expected_cavity_depths = (36.0, 70.0, 102.0)
    cavity_depth_checks = [
        any(_close(actual, expected_depth, 2.0) for actual in cut_depths)
        for expected_depth in expected_cavity_depths
    ]
    detail_points = 0.0
    detail_points += 9.0 * sum(cavity_depth_checks) / 3.0
    detail_points += 8.0 if counts["cuts"] >= 6 else (
        8.0 * counts["cuts"] / 6.0
    )
    detail_points += 4.0 if any(
        _close(depth, 9.0, 2.0) for depth in cut_depths
    ) else 0.0
    detail_points += 2.0 if any(
        _close(depth, 1.5, 0.75) for depth in cut_depths
    ) else 0.0
    detail_points += 2.0 if counts["fillets"] >= 1 else 0.0
    detail_points = round(detail_points, 2)

    actions = str(result.get("actions") or "0/0").split("/", 1)
    succeeded = int(actions[0]) if actions[0].isdigit() else 0
    total = int(actions[1]) if len(actions) == 2 and actions[1].isdigit() else 0
    action_rate = succeeded / total if total else 0.0
    execution_points = round(
        10.0 * action_rate
        + (5.0 if result.get("status") == "completed" else 0.0),
        2,
    )

    score = round(
        dimension_points
        + volume_points
        + topology_points
        + detail_points
        + execution_points,
        2,
    )
    # status 来自模型自己生成的 blueprint 验证，只计入 execution_points，不参与验收。
    acceptance = (
        all(dimension_checks)
        and relative_error <= 0.10
        and counts["cuts"] >= 6
        and all(cavity_depth_checks)
        and counts["fillets"] >= 1
    )
    return {
        "score": score,
        "acceptance_ok": acceptance,
        "process_completed": result.get("status") == "completed",
        "dimension_points": dimension_points,
        "volume_points": volume_points,
        "topology_points": topology_points,
        "detail_points": detail_points,
        "execution_points": execution_points,
        "dimension_checks": dimension_checks,
        "volume_relative_error": round(relative_error, 6),
        "feature_counts": counts,
        "cut_depths_mm": cut_depths,
        "cavity_depth_checks": cavity_depth_checks,
        "action_success_rate": round(action_rate, 6),
    }


def compact_result(result: dict) -> dict:
    """Keep reproducible evidence without embedding full face snapshots."""
    payload = dict(result)
    turns = payload.pop("turn_diagnostics", []) or []
    blueprint = payload.pop("blueprint", {}) or {}
    task_plan = payload.pop("task_plan", {}) or {}
    final_observation = payload.pop("final_observation", {}) or {}
    payload["blueprint_step_count"] = len(blueprint.get("steps") or [])
    payload["milestone_count"] = len(
        task_plan.get("milestones") or []
    )
    payload["turn_summary"] = [
        {
            "turn": turn.get("turn"),
            "operations": [
                action.get("operation")
                for action in (
                    (turn.get("decision") or {}).get("actions") or []
                )
            ],
            "succeeded": sum(
                bool(item.get("success"))
                for item in turn.get("execution_results") or []
            ),
            "total": len(turn.get("execution_results") or []),
            "validation_passed": bool(
                (turn.get("validation") or {}).get("passed")
            ),
        }
        for turn in turns
    ]
    payload["final_observation_summary"] = {
        "bounding_box_mm": final_observation.get("bounding_box_mm"),
        "volume_mm3": final_observation.get("volume_mm3"),
        "features": len(final_observation.get("features") or []),
        "faces": len(final_observation.get("faces") or []),
        "feature_sketch_links": len(
            final_observation.get("feature_sketch_links") or []
        ),
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--vision-model", default="GLM-4.6V")
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "eval" / "pen_holder_three_view_result.json",
    )
    args = parser.parse_args()

    sample = next(
        item
        for item in _load_first_samples(10_000)
        if item["part_id"] == "C4"
    )
    groundtruth = json.loads(
        (
            DATASET
            / sample["subdir"]
            / "groundtruth.json"
        ).read_text(encoding="utf-8")
    )
    result = run_one(
        sample,
        web_url=args.web_url,
        sw_url=args.sw_url,
        vision_model=args.vision_model,
        plan_model=args.plan_model,
        requirements_hint=True,
    )
    scored = compact_result({
        **result,
        **score_pen_holder(result, groundtruth),
    })
    report = {
        "run_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "case_id": "C4_desktop_pen_holder",
        "mode": "multi_turn",
        "vision_model": args.vision_model,
        "plan_model": args.plan_model,
        "result": scored,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"status={scored.get('status')} "
        f"score={scored['score']} "
        f"acceptance={scored['acceptance_ok']} "
        f"tokens={(scored.get('token_usage') or {}).get('total_tokens')}"
    )
    return 0 if scored["acceptance_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
