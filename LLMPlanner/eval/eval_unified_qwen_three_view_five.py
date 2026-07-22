"""Cold-cache five-case benchmark using one Qwen model for vision and planning."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

try:
    from .eval_pen_holder_three_view import score_pen_holder
    from .eval_three_view_multiturn import (
        DATASET,
        ROOT,
        _load_first_samples,
        _query_balance,
        run_one,
    )
except ImportError:
    from eval_pen_holder_three_view import score_pen_holder
    from eval_three_view_multiturn import (
        DATASET,
        ROOT,
        _load_first_samples,
        _query_balance,
        run_one,
    )


MODEL = "Qwen3.5-397B-A17B"
DEFAULT_IDS = ("B01", "B07", "B11", "B22", "C4")


def _feature_metrics(observation: dict) -> dict:
    links = observation.get("feature_sketch_links") or []
    features = observation.get("features") or []
    sketches = {
        item.get("name"): item
        for item in observation.get("sketches") or []
        if isinstance(item, dict)
    }
    cut_links = [
        item
        for item in links
        if str(item.get("operationKind", "")).lower() == "cut"
    ]
    return {
        "cuts": len(cut_links),
        "extrusions": sum(
            str(item.get("operationKind", "")).lower() == "extrusion"
            for item in links
        ),
        "fillets": sum(
            "fillet" in str(item.get("type", "")).lower()
            or "圆角" in str(item.get("name", ""))
            for item in features
        ),
        "cut_circles": sum(
            int(sketches.get(item.get("sketchName"), {}).get(
                "circleCount",
                0,
            ))
            for item in cut_links
        ),
        "cut_depths_mm": sorted(
            round(float(item.get("depth") or 0) * 1000, 3)
            for item in cut_links
        ),
        "faces": len(observation.get("faces") or []),
    }


def _expected_detail_counts(groundtruth: dict) -> dict:
    operations = groundtruth.get("operations") or []
    hole_count = len(groundtruth.get("holes") or [])
    cut_groups = 1 if hole_count else 0
    fillets = 0
    for operation in operations:
        kind = str(operation.get("type", ""))
        if kind == "cutout_on_face":
            cut_groups += 1
            if (
                operation.get("shape") == "circle"
                and operation.get("through_all")
            ):
                positions = operation.get("positions") or []
                hole_count += len(positions) if positions else 1
        elif kind == "fillet_edges":
            fillets += 1
    return {
        "holes": hole_count,
        "cuts": cut_groups,
        "fillets": fillets,
    }


def score_generic(result: dict, groundtruth: dict) -> dict:
    bbox = result.get("bbox_mm") or {}
    actual_dims = sorted(
        float(bbox.get(key) or 0)
        for key in ("sizeX", "sizeY", "sizeZ")
    )
    expected_bbox = groundtruth["expected_bbox_mm"]
    expected_dims = sorted(
        float(expected_bbox[key])
        for key in ("length", "height", "depth")
    )
    dimension_errors = [
        abs(actual - expected)
        for actual, expected in zip(actual_dims, expected_dims)
    ]
    dimension_points = round(
        25.0
        * sum(max(0.0, 1.0 - error / 10.0) for error in dimension_errors)
        / 3.0,
        2,
    )

    actual_volume = float(result.get("volume_mm3") or 0)
    expected_volume = float(groundtruth["expected_volume_mm3"])
    volume_error = (
        abs(actual_volume - expected_volume) / expected_volume
        if actual_volume > 0 and expected_volume > 0
        else 1.0
    )
    volume_points = (
        20.0
        if volume_error <= 0.05
        else 20.0 * max(0.0, 1.0 - (volume_error - 0.05) / 0.20)
    )

    metrics = _feature_metrics(result.get("final_observation") or {})
    expected = _expected_detail_counts(groundtruth)
    valid_body = actual_volume > 0 and metrics["faces"] > 0
    detail_points = 0.0
    detail_points += (
        (15.0 if valid_body else 0.0)
        if expected["holes"] == 0
        else 15.0 * min(metrics["cut_circles"] / expected["holes"], 1.0)
    )
    detail_points += (
        (15.0 if valid_body else 0.0)
        if expected["cuts"] == 0
        else 15.0 * min(metrics["cuts"] / expected["cuts"], 1.0)
    )
    detail_points += (
        (10.0 if valid_body else 0.0)
        if expected["fillets"] == 0
        else 10.0 * min(metrics["fillets"] / expected["fillets"], 1.0)
    )

    actions = str(result.get("actions") or "0/0").split("/", 1)
    succeeded = int(actions[0]) if actions[0].isdigit() else 0
    total = (
        int(actions[1])
        if len(actions) == 2 and actions[1].isdigit()
        else 0
    )
    action_rate = succeeded / total if total else 0.0
    execution_points = (
        10.0 * action_rate
        + (5.0 if result.get("status") == "completed" else 0.0)
    )
    score = round(
        dimension_points
        + volume_points
        + detail_points
        + execution_points,
        2,
    )
    return {
        "score": score,
        "dimension_points": dimension_points,
        "volume_points": round(volume_points, 2),
        "detail_points": round(detail_points, 2),
        "execution_points": round(execution_points, 2),
        "dimension_errors_mm": [
            round(value, 3) for value in dimension_errors
        ],
        "volume_relative_error": round(volume_error, 6),
        "expected_details": expected,
        "actual_details": metrics,
        "action_success_rate": round(action_rate, 6),
    }


def _compact(result: dict) -> dict:
    kept = {
        key: value
        for key, value in result.items()
        if key not in {
            "blueprint",
            "task_plan",
            "turn_diagnostics",
            "final_observation",
            "vision_ir",
        }
    }
    kept["vision_ir_summary"] = {
        key: value
        for key, value in (result.get("vision_ir") or {}).items()
        if key in {
            "part_type",
            "overall_size_mm",
            "base_view",
            "confidence",
        }
    }
    return kept


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--ids", nargs="+", default=list(DEFAULT_IDS))
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "eval" / "unified_qwen_three_view_five_result.json",
    )
    args = parser.parse_args()

    samples_by_id = {
        item["part_id"]: item
        for item in _load_first_samples(10_000)
    }
    env_path = ROOT / ".env"
    balance_before = _query_balance(env_path)
    results = []
    for part_id in args.ids:
        sample = samples_by_id[part_id]
        groundtruth = json.loads(
            (
                DATASET
                / sample["subdir"]
                / "groundtruth.json"
            ).read_text(encoding="utf-8")
        )
        print(f"[{part_id}] {sample['name']}", flush=True)
        result = run_one(
            sample,
            web_url=args.web_url,
            sw_url=args.sw_url,
            vision_model=MODEL,
            plan_model=MODEL,
            requirements_hint=True,
            use_cache=False,
        )
        scoring = (
            score_pen_holder(result, groundtruth)
            if part_id == "C4"
            else score_generic(result, groundtruth)
        )
        scored = _compact({**result, **scoring})
        results.append(scored)
        print(
            f"  status={scored.get('status')} "
            f"score={scored['score']} "
            f"accepted={scored.get('acceptance_ok')} "
            f"tokens={(scored.get('token_usage') or {}).get('total_tokens')}",
            flush=True,
        )

    balance_after = _query_balance(env_path)
    spent_yuan = None
    if balance_before and balance_after:
        spent_yuan = round(
            float(balance_after["consumed_yuan"])
            - float(balance_before["consumed_yuan"]),
            6,
        )
    report = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "multi_turn",
        "provider": "AI Ping",
        "vision_model": MODEL,
        "plan_model": MODEL,
        "cache_enabled": False,
        "input_policy": "natural_requirements_hint",
        "sample_ids": list(args.ids),
        "average_score": round(
            sum(float(item["score"]) for item in results) / len(results),
            2,
        ),
        "passed": sum(
            bool(item.get("acceptance_ok")) for item in results
        ),
        "total": len(results),
        "token_totals": {
            key: sum(
                int((item.get("token_usage") or {}).get(key) or 0)
                for item in results
            )
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "calls",
            )
        },
        "balance_before": balance_before,
        "balance_after": balance_after,
        "spent_yuan": spent_yuan,
        "results": results,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"summary score={report['average_score']} "
        f"passed={report['passed']}/{report['total']} "
        f"tokens={report['token_totals']['total_tokens']} "
        f"spent_yuan={spent_yuan}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
