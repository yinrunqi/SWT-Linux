"""Cold-cache Qwen Single-turn benchmark for untested Web text examples."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

LLM_ROOT = Path(__file__).resolve().parent.parent
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

from planner.observer import SwObserver

try:
    from .eval_three_view_multiturn import ROOT, _query_balance
except ImportError:
    from eval_three_view_multiturn import ROOT, _query_balance


MODEL = "Qwen3.5-397B-A17B"


@dataclass(frozen=True)
class TextCase:
    case_id: str
    name: str
    prompt: str
    expected_bbox_mm: tuple[float, float, float]
    bbox_tolerance_mm: float
    volume_range_mm3: tuple[float, float]
    expected_details: tuple[tuple[str, int], ...]


CASES = (
    TextCase(
        "hex_nut",
        "六角螺母",
        "创建对边距 30mm、厚 8mm 的正六角螺母，中心有直径 10mm 的贯穿孔，厚度方向关于原点居中。",
        (8.0, 30.0, 34.641),
        3.0,
        (4500.0, 6500.0),
        (("extrusions", 1), ("polygons", 1), ("circles", 1)),
    ),
    TextCase(
        "mug",
        "马克杯",
        "创建高 90mm、外径 70mm、壁厚 5mm、底厚 10mm 的圆柱形马克杯，并添加外径 40mm、条粗 8mm、厚 8mm 的环形把手。",
        (70.0, 90.0, 100.0),
        15.0,
        (100000.0, 145000.0),
        (("extrusions", 2), ("cuts", 1), ("circles", 3)),
    ),
    TextCase(
        "wrench",
        "扳手",
        "创建厚 8mm 的单头扳手：头部外径 40mm、中心正六角卡口对边距 13mm；手柄长 120mm、宽 15mm，并与头部可靠合并。",
        (8.0, 40.0, 140.0),
        8.0,
        (17000.0, 24000.0),
        (("extrusions", 2), ("polygons", 1)),
    ),
    TextCase(
        "vase",
        "花瓶",
        "创建高 150mm 的轴对称空心花瓶：底部平坦，中部最大直径 90mm，瓶颈直径 40mm，瓶口直径 55mm，底厚 10mm。",
        (90.0, 90.0, 150.0),
        10.0,
        (300000.0, 500000.0),
        (("revolves", 1), ("centerlines", 1)),
    ),
    TextCase(
        "key",
        "钥匙",
        "创建厚 2mm 的小型钥匙：外径 20mm 的钥匙圈头、10×5mm 圈孔、42×8mm 钥匙杆，并在杆的一侧均布 5 个齿槽。",
        (2.0, 20.0, 59.0),
        8.0,
        (800.0, 1500.0),
        (("extrusions", 2), ("cuts", 1)),
    ),
    TextCase(
        "gear_disc",
        "齿轮盘",
        "创建直径 60mm、厚 8mm 的圆盘，中心有直径 12mm 贯穿孔，并在接近外缘处均布 12 个相同的小贯穿槽。",
        (8.0, 60.0, 60.0),
        4.0,
        (17500.0, 22000.0),
        (("extrusions", 1), ("cuts", 1)),
    ),
    TextCase(
        "flange",
        "法兰盘",
        "创建直径 100mm、厚 12mm 的圆形法兰，中心贯穿孔直径 30mm，并在节圆直径 80mm 上均布 6 个直径 8mm 的贯穿孔。",
        (12.0, 100.0, 100.0),
        3.0,
        (78000.0, 86000.0),
        (("extrusions", 1), ("circles", 8)),
    ),
    TextCase(
        "pulley",
        "皮带轮",
        "创建直径 100mm、厚 20mm 的简化皮带轮，中心贯穿孔直径 25mm，顶部有外径 90mm、内径 70mm、深 10mm 的环槽，外轮缘做 C1mm 倒角。",
        (20.0, 100.0, 100.0),
        3.0,
        (115000.0, 128000.0),
        (("extrusions", 1), ("cuts", 1), ("chamfers", 1)),
    ),
    TextCase(
        "stepped_shaft",
        "三段阶梯轴",
        "使用一次旋转创建总长 90mm 的三段阶梯轴：各段直径和长度依次为 35×40mm、50×30mm、25×20mm，轴向关于原点居中。",
        (50.0, 50.0, 90.0),
        3.0,
        (102000.0, 111500.0),
        (("revolves", 1), ("centerlines", 1)),
    ),
    TextCase(
        "socket_bolt",
        "内六角螺栓",
        "创建简化内六角圆柱头螺栓：杆径 10mm、杆长 50mm；头部直径 18mm、高 6mm；顶部内六角对边距 8mm、盲孔深 4mm；头杆交界 R0.5mm。",
        (18.0, 18.0, 56.0),
        2.0,
        (5200.0, 7200.0),
        (
            ("extrusions", 2),
            ("cuts", 1),
            ("polygons", 1),
            ("fillets", 1),
        ),
    ),
)


HISTORICAL_CASES = (
    {
        "case_id": "phone_stand",
        "name": "一体式手机支架",
        "score": 100.0,
        "status": "completed",
        "source": "PERFORMANCE.md 2026-07-21 final Single-turn rerun",
        "cold_tokens": 19945,
    },
    {
        "case_id": "stool",
        "name": "四脚板凳",
        "score": 80.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 16/20 legacy score",
    },
    {
        "case_id": "rounded_stool",
        "name": "圆角板凳",
        "score": 90.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 18/20 legacy score",
    },
    {
        "case_id": "storage_box",
        "name": "收纳盒",
        "score": 90.0,
        "status": "completed",
        "source": "eval/text_first5_result.json 18/20 legacy score",
    },
    {
        "case_id": "standard_die",
        "name": "标准骰子",
        "score": 100.0,
        "status": "completed",
        "source": "PERFORMANCE.md 2026-07-16 accepted Single-turn rerun",
    },
)


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    response = requests.post(url, json=payload, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text[:500]}
    if not response.ok or not body.get("success"):
        raise RuntimeError(body.get("error") or f"HTTP {response.status_code}")
    return body


def _close_documents(sw_url: str) -> dict:
    return _post_json(
        f"{sw_url}/api/close-all-documents",
        {},
        timeout=60,
    )


def _observation(sw_url: str) -> dict:
    observer = SwObserver(base_url=sw_url)
    try:
        return observer.collect().model_dump(mode="json")
    finally:
        observer.close()


def _feature_metrics(
    observation: dict,
    successful_operations: list[str],
) -> dict[str, int]:
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
    operation_counts = {
        name: successful_operations.count(name)
        for name in set(successful_operations)
    }
    return {
        "extrusions": sum(
            str(item.get("operationKind", "")).lower() == "extrusion"
            for item in links
        ),
        "cuts": len(cut_links),
        "fillets": sum(
            "fillet" in str(item.get("type", "")).lower()
            or "圆角" in str(item.get("name", ""))
            for item in features
        ),
        "chamfers": sum(
            "chamfer" in str(item.get("type", "")).lower()
            or "倒角" in str(item.get("name", ""))
            for item in features
        ),
        "revolves": operation_counts.get("feature_revolve", 0),
        "polygons": operation_counts.get("sketch_polygon", 0),
        "centerlines": operation_counts.get("sketch_centerline", 0),
        "circles": sum(
            int(item.get("circleCount") or 0)
            for item in sketches.values()
        ),
        "slots": sum(
            int(item.get("slotCount") or 0)
            for item in sketches.values()
        ),
        "cut_circles": sum(
            int(sketches.get(item.get("sketchName"), {}).get(
                "circleCount",
                0,
            ))
            for item in cut_links
        ),
        "faces": len(observation.get("faces") or []),
    }


def _dimension_score(
    actual: list[float],
    expected: tuple[float, float, float],
    tolerance: float,
) -> tuple[float, list[float], list[bool]]:
    actual_sorted = sorted(actual)
    expected_sorted = sorted(expected)
    errors = [
        abs(value - target)
        for value, target in zip(actual_sorted, expected_sorted)
    ]
    if not any(value > 0 for value in actual_sorted):
        return 0.0, [round(value, 3) for value in errors], [False] * 3
    passed = [error <= tolerance for error in errors]
    points = [
        10.0
        if ok
        else 10.0 * max(0.0, 1.0 - (error - tolerance) / (2 * tolerance))
        for error, ok in zip(errors, passed)
    ]
    return (
        round(sum(points), 2),
        [round(value, 3) for value in errors],
        passed,
    )


def _volume_score(
    actual: float,
    expected_range: tuple[float, float],
) -> tuple[float, bool]:
    low, high = expected_range
    if low <= actual <= high:
        return 20.0, True
    if actual <= 0:
        return 0.0, False
    distance = low - actual if actual < low else actual - high
    return (
        round(20.0 * max(0.0, 1.0 - distance / (high - low)), 2),
        False,
    )


def score_case(
    case: TextCase,
    payload: dict,
    observation: dict,
) -> dict:
    plan_steps = (payload.get("plan") or {}).get("steps") or []
    results = payload.get("results") or []
    successful = [
        str(item.get("operation", ""))
        for item in results
        if item.get("success")
    ]
    success_count = len(successful)
    execution_rate = success_count / len(plan_steps) if plan_steps else 0.0
    all_ok = bool(plan_steps) and success_count == len(plan_steps)
    execution_points = round(
        20.0 * execution_rate + (5.0 if all_ok else 0.0),
        2,
    )

    bbox = observation.get("bounding_box_mm") or {}
    actual_dims = [
        float(bbox.get(key) or 0)
        for key in ("sizeX", "sizeY", "sizeZ")
    ]
    dimension_points, dimension_errors, dimension_checks = _dimension_score(
        actual_dims,
        case.expected_bbox_mm,
        case.bbox_tolerance_mm,
    )
    volume = float(observation.get("volume_mm3") or 0)
    volume_points, volume_ok = _volume_score(
        volume,
        case.volume_range_mm3,
    )

    metrics = _feature_metrics(observation, successful)
    detail_checks = {
        name: metrics.get(name, 0) >= minimum
        for name, minimum in case.expected_details
    }
    detail_points = round(
        25.0
        * sum(
            min(metrics.get(name, 0) / minimum, 1.0)
            for name, minimum in case.expected_details
        )
        / len(case.expected_details),
        2,
    )
    score = round(
        execution_points
        + dimension_points
        + volume_points
        + detail_points,
        2,
    )
    acceptance = (
        all_ok
        and all(dimension_checks)
        and volume_ok
        and all(detail_checks.values())
    )
    return {
        "score": score,
        "acceptance_ok": acceptance,
        "status": "completed" if all_ok else "failed",
        "plan_steps": len(plan_steps),
        "successful_steps": success_count,
        "execution_points": execution_points,
        "dimension_points": dimension_points,
        "volume_points": volume_points,
        "detail_points": detail_points,
        "bbox_mm": bbox or None,
        "expected_bbox_mm": list(case.expected_bbox_mm),
        "dimension_errors_mm": dimension_errors,
        "dimension_checks": dimension_checks,
        "volume_mm3": round(volume, 3) if volume else None,
        "expected_volume_range_mm3": list(case.volume_range_mm3),
        "volume_ok": volume_ok,
        "actual_details": metrics,
        "expected_details": dict(case.expected_details),
        "detail_checks": detail_checks,
        "failed_steps": [
            {
                "step": item.get("step"),
                "operation": item.get("operation"),
                "message": item.get("message"),
            }
            for item in results
            if not item.get("success")
        ],
    }


def run_one(
    case: TextCase,
    *,
    web_url: str,
    sw_url: str,
) -> dict:
    started = time.perf_counter()
    cleanup_before = _close_documents(sw_url)
    time.sleep(1.0)
    payload: dict[str, Any] = {}
    try:
        response = requests.post(
            f"{web_url}/api/run",
            json={
                "instruction": case.prompt,
                "provider": "aiping",
                "vision_model": MODEL,
                "plan_model": MODEL,
                "mode": "single_turn",
                "use_cache": False,
                "dry_run": False,
            },
            timeout=900,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text[:500]}
        if not response.ok or not payload.get("success"):
            raise RuntimeError(
                payload.get("error") or f"HTTP {response.status_code}"
            )
        observation = _observation(sw_url)
        scoring = score_case(case, payload, observation)
        return {
            "case_id": case.case_id,
            "name": case.name,
            **scoring,
            "token_usage": payload.get("token_usage"),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup_before,
            "error": None,
        }
    except Exception as exc:
        return {
            "case_id": case.case_id,
            "name": case.name,
            "score": 0.0,
            "acceptance_ok": False,
            "status": "error",
            "token_usage": payload.get("token_usage"),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup_before,
            "error": str(exc),
        }
    finally:
        try:
            _close_documents(sw_url)
        except Exception:
            pass


def _write_report(
    output: Path,
    results: list[dict],
    balance_before: dict | None,
    balance_after: dict | None = None,
) -> dict:
    spent_yuan = None
    if balance_before and balance_after:
        spent_yuan = round(
            float(balance_after["consumed_yuan"])
            - float(balance_before["consumed_yuan"]),
            6,
        )
    report = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": "AI Ping",
        "model": MODEL,
        "mode": "single_turn",
        "cache_enabled": False,
        "historical_skipped": list(HISTORICAL_CASES),
        "new_case_ids": [case.case_id for case in CASES],
        "average_score": round(
            sum(float(item["score"]) for item in results) / len(results),
            2,
        ) if results else 0.0,
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
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "eval" / "text_singleturn_qwen_result.json",
    )
    parser.add_argument(
        "--pause-every",
        type=int,
        default=3,
        help="Pause briefly after this many sequential SolidWorks cases",
    )
    args = parser.parse_args()
    selected = [
        case
        for case in CASES
        if not args.ids or case.case_id in set(args.ids)
    ]
    balance_before = _query_balance(ROOT / ".env")
    results: list[dict] = []
    for index, case in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {case.name}", flush=True)
        result = run_one(
            case,
            web_url=args.web_url.rstrip("/"),
            sw_url=args.sw_url.rstrip("/"),
        )
        results.append(result)
        usage = result.get("token_usage") or {}
        print(
            f"  score={result['score']} "
            f"accepted={result['acceptance_ok']} "
            f"status={result['status']} "
            f"tokens={usage.get('total_tokens')} "
            f"duration={result['duration_seconds']}s",
            flush=True,
        )
        _write_report(args.output, results, balance_before)
        if (
            args.pause_every > 0
            and index < len(selected)
            and index % args.pause_every == 0
        ):
            print("  cooling pause 5s", flush=True)
            time.sleep(5.0)

    balance_after = _query_balance(ROOT / ".env")
    report = _write_report(
        args.output,
        results,
        balance_before,
        balance_after,
    )
    print(
        f"summary score={report['average_score']} "
        f"passed={report['passed']}/{report['total']} "
        f"tokens={report['token_totals']['total_tokens']} "
        f"spent_yuan={report['spent_yuan']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
