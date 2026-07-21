"""Compare Single-turn and Multi-turn on one complex everyday part."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planner.observer import SwObserver


SPEC_PATH = Path(__file__).with_name("phone_stand_spec.json")
DEFAULT_OUTPUT = Path(__file__).with_name(
    "phone_stand_modes_result.json"
)

PHONE_STAND_PROMPT = (
    "创建一个一体式桌面手机支架，所有结构必须合并为单一实体。"
    "底座长110mm、宽78mm、厚8mm；78mm宽的倾斜背板厚6mm，"
    "与水平面成74°，从底座上表面升起，成品总高度118mm。"
    "底座前端设置贯穿全宽的挡唇，前后厚10mm，高出底座上表面12mm，"
    "使成品前端总高度20mm。背板左右两侧各有一个三角加强筋。"
    "背板下部中央开一个宽16mm、向下开口高度28mm的充电线U形通槽；"
    "背板中上部开3个36×8mm的水平贯穿散热槽，槽中心竖向间距18mm。"
    "底面加工两个24×10mm、深1.5mm的防滑垫浅凹槽。"
    "最后尽可能对适合的外轮廓做R4mm圆角，同时保持底面平整、支架左右对称。"
)


def _feature_count(features: list[dict[str, Any]], keyword: str) -> int:
    return sum(
        keyword
        in f"{item.get('name', '')} {item.get('type', '')}".lower()
        for item in features
    )


def score_observation(
    observation: dict[str, Any],
    *,
    status: str,
    successful_actions: int,
    total_actions: int,
    spec: dict[str, Any],
) -> dict[str, Any]:
    acceptance = spec["acceptance"]
    bbox = observation.get("bounding_box_mm") or {}
    actual_dimensions = sorted(
        float(bbox.get(key, 0))
        for key in ("sizeX", "sizeY", "sizeZ")
    )
    expected_dimensions = sorted(float(value) for value in spec["overall_size"])
    tolerance = float(acceptance["bbox_tolerance_mm"])
    dimension_checks = [
        abs(actual - expected) <= tolerance
        for actual, expected in zip(
            actual_dimensions,
            expected_dimensions,
        )
    ]
    dimension_points = 10.0 * sum(dimension_checks)

    volume = observation.get("volume_mm3")
    volume_ok = (
        isinstance(volume, (int, float))
        and float(acceptance["minimum_volume_mm3"])
        <= float(volume)
        <= float(acceptance["maximum_volume_mm3"])
    )
    volume_points = 10.0 if volume_ok else 0.0

    features = observation.get("features") or []
    counts = {
        "extrusion": _feature_count(features, "extr"),
        "cut": _feature_count(features, "ice"),
        "fillet": _feature_count(features, "fillet"),
        "total": len(features),
        "faces": len(observation.get("faces") or []),
    }
    valid_entity = (
        bool(bbox)
        and isinstance(volume, (int, float))
        and float(volume) > 0
    )
    topology_points = (
        (10.0 if valid_entity else 0.0)
        + (
            5.0
            if counts["faces"] >= int(acceptance["minimum_faces"])
            else 0.0
        )
        + (
            5.0
            if counts["total"]
            >= int(acceptance["minimum_total_features"])
            else 0.0
        )
    )
    detail_points = (
        (5.0 if counts["extrusion"] >= 1 else 0.0)
        + 15.0
        * min(
            counts["cut"]
            / int(acceptance["minimum_cut_features"]),
            1.0,
        )
        + (
            5.0
            if counts["fillet"]
            >= int(acceptance["minimum_fillet_features"])
            else 0.0
        )
    )
    action_rate = (
        successful_actions / total_actions
        if total_actions > 0
        else 0.0
    )
    execution_points = (
        (5.0 if status == "completed" else 0.0)
        + 10.0 * action_rate
    )
    score = (
        dimension_points
        + volume_points
        + topology_points
        + detail_points
        + execution_points
    )
    acceptance_ok = (
        status == "completed"
        and all(dimension_checks)
        and volume_ok
        and counts["extrusion"] >= 1
        and counts["cut"] >= int(acceptance["minimum_cut_features"])
        and counts["fillet"] >= int(
            acceptance["minimum_fillet_features"]
        )
    )
    return {
        "score": round(score, 2),
        "acceptance_ok": acceptance_ok,
        "dimension_points": round(dimension_points, 2),
        "volume_points": round(volume_points, 2),
        "topology_points": round(topology_points, 2),
        "detail_points": round(detail_points, 2),
        "execution_points": round(execution_points, 2),
        "bbox_mm": bbox or None,
        "expected_dimensions_mm": expected_dimensions,
        "dimension_checks": dimension_checks,
        "volume_mm3": volume,
        "volume_ok": volume_ok,
        "feature_counts": counts,
        "action_success_rate": round(action_rate, 6),
    }


def _close_documents(sw_url: str) -> None:
    requests.post(
        f"{sw_url.rstrip('/')}/api/close-all-documents",
        json={},
        timeout=120,
    ).raise_for_status()


def evaluate_mode(
    *,
    mode: str,
    web_url: str,
    sw_url: str,
    plan_model: str,
    max_turns: int,
    spec: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    observer = SwObserver(sw_url.rstrip("/"))
    try:
        _close_documents(sw_url)
        response = requests.post(
            f"{web_url.rstrip('/')}/api/run",
            json={
                "instruction": PHONE_STAND_PROMPT,
                "mode": mode,
                "max_turns": max_turns if mode == "multi_turn" else None,
                "plan_model": plan_model,
                "vision_model": "GLM-4.6V",
            },
            timeout=1800,
        )
        payload = response.json()
        if not response.ok or not payload.get("success"):
            raise RuntimeError(
                payload.get("error") or f"HTTP {response.status_code}"
            )

        summary = payload.get("summary") or {}
        if mode == "multi_turn":
            run = payload["multi_turn"]
            observation = run.get("final_observation") or {}
            status = str(run.get("status") or "blocked")
            turns = len(run.get("turns") or [])
            llm_calls = int(run.get("llm_calls") or 0)
            execution_results = [
                item
                for turn in run.get("turns") or []
                for item in turn.get("execution_results") or []
            ]
        else:
            observation = observer.collect().model_dump(mode="json")
            status = "completed" if summary.get("all_ok") else "failed"
            turns = 1
            llm_calls = int(
                (payload.get("token_usage") or {}).get("calls") or 1
            )
            execution_results = payload.get("results") or []
        total_actions = int(summary.get("total") or 0)
        successful_actions = int(summary.get("succeeded") or 0)
        scored = score_observation(
            observation,
            status=status,
            successful_actions=successful_actions,
            total_actions=total_actions,
            spec=spec,
        )
        return {
            "mode": mode,
            "status": status,
            "duration_seconds": round(
                time.perf_counter() - started,
                3,
            ),
            "turns": turns,
            "llm_calls": llm_calls,
            "total_actions": total_actions,
            "successful_actions": successful_actions,
            "token_usage": (
                payload.get("token_usage")
                or summary.get("token_usage")
                or {}
            ),
            "failed_actions": [
                {
                    "operation": item.get("operation"),
                    "message": item.get("message"),
                }
                for item in execution_results
                if not item.get("success", False)
            ],
            **scored,
            "error": None,
        }
    except Exception as exc:
        observation = observer.collect().model_dump(mode="json")
        scored = score_observation(
            observation,
            status="error",
            successful_actions=0,
            total_actions=0,
            spec=spec,
        )
        return {
            "mode": mode,
            "status": "error",
            "duration_seconds": round(
                time.perf_counter() - started,
                3,
            ),
            "turns": 0,
            "llm_calls": 0,
            "total_actions": 0,
            "successful_actions": 0,
            "token_usage": {},
            **scored,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        observer.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument(
        "--plan-model",
        default="Qwen3.5-397B-A17B",
    )
    parser.add_argument("--max-turns", type=int, default=24)
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("single_turn", "multi_turn"),
        default=["single_turn", "multi_turn"],
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    report = {
        "evaluation_version": 1,
        "run_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "provider": "AI Ping",
        "plan_model": args.plan_model,
        "case": spec,
        "prompt": PHONE_STAND_PROMPT,
        "results": [],
    }
    if args.resume and args.output.is_file():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("evaluation_version") == 1:
            report["results"] = previous.get("results", [])
    completed = {
        item.get("mode")
        for item in report["results"]
        if item.get("status") != "error"
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for mode in args.modes:
        if mode in completed:
            print(f"[{mode}] 已有结果，跳过", flush=True)
            continue
        print(f"[{mode}] 开始", flush=True)
        result = evaluate_mode(
            mode=mode,
            web_url=args.web_url,
            sw_url=args.sw_url,
            plan_model=args.plan_model,
            max_turns=args.max_turns,
            spec=spec,
        )
        report["results"].append(result)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"[{mode}] status={result['status']} "
            f"score={result['score']} "
            f"acceptance={result['acceptance_ok']} "
            f"duration={result['duration_seconds']}s",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
