"""Evaluate the first N three-view Web Demo samples in multi-turn mode.

Calls the same /api/run endpoint as the browser, with mode=multi_turn and
the sample drawing attached. Records geometry acceptance and token usage.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "datasets" / "manual_three_view"
SAMPLES_JSON = DATASET / "samples.json"


def _load_first_samples(n: int) -> list[dict]:
    data = json.loads(SAMPLES_JSON.read_text(encoding="utf-8"))
    return data["samples"][:n]


def _query_balance(env_path: Path) -> dict | None:
    script = Path.home() / ".cursor" / "skills" / "query-aiping-balance" / "scripts" / "query_balance.py"
    if not script.exists():
        return None
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(script), str(env_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _bbox_ok(actual: dict | None, expected: dict, tol_mm: float = 3.0) -> bool:
    if not actual:
        return False
    sizes = sorted(
        [
            float(actual["sizeX"]),
            float(actual["sizeY"]),
            float(actual["sizeZ"]),
        ]
    )
    expected_sizes = sorted(
        [
            float(expected["length"]),
            float(expected["height"]),
            float(expected["depth"]),
        ]
    )
    return all(abs(a - b) <= tol_mm for a, b in zip(sizes, expected_sizes))


def _volume_ok(actual: float | None, expected: float, rel_tol: float = 0.05) -> bool:
    if actual is None or expected <= 0:
        return False
    return abs(actual - expected) / expected <= rel_tol


def _count_cut_circles(observation: dict) -> int:
    sketches = {
        sketch.get("name"): sketch
        for sketch in observation.get("sketches", [])
        if isinstance(sketch, dict)
    }
    total = 0
    for link in observation.get("feature_sketch_links", []):
        if not isinstance(link, dict):
            continue
        feature_type = str(link.get("featureType", "")).lower()
        if "ice" not in feature_type and "cut" not in feature_type:
            continue
        sketch = sketches.get(link.get("sketchName"), {})
        total += int(sketch.get("circleCount") or 0)
    return total


def _close_test_documents(sw_url: str) -> dict:
    """Discard temporary documents before each isolated evaluation case."""
    request = urllib.request.Request(
        f"{sw_url.rstrip('/')}/api/close-all-documents",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError(payload.get("message") or "document cleanup failed")
    return payload


def run_one(
    sample: dict,
    *,
    web_url: str,
    sw_url: str,
    vision_model: str,
    plan_model: str,
    minimal_hint: bool = False,
    requirements_hint: bool = False,
) -> dict:
    part_id = sample["part_id"]
    subdir = sample["subdir"]
    if minimal_hint:
        prompt = "请仅根据三视图读取全部标注尺寸、轮廓和特征并精确建模。"
    elif requirements_hint:
        prompt = (
            sample.get("test", {}).get("requirements")
            or sample.get("notes")
            or "请根据三视图和用户几何规格精确建模。"
        )
    else:
        prompt = (
            sample.get("ui", {}).get("prompt")
            or sample.get("test", {}).get("text_hint", "")
        )
    drawing = DATASET / subdir / "drawing.png"
    gt_path = DATASET / subdir / "groundtruth.json"
    started = time.perf_counter()
    if not drawing.exists() or not gt_path.exists():
        return {
            "part_id": part_id,
            "status": "error",
            "acceptance_ok": False,
            "error": "missing drawing.png or groundtruth.json",
            "duration_seconds": 0,
        }

    try:
        cleanup = _close_test_documents(sw_url)
    except Exception as exc:
        return {
            "part_id": part_id,
            "name": sample.get("name"),
            "status": "error",
            "acceptance_ok": False,
            "error": f"pre-test document cleanup failed: {exc}",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    image_b64 = base64.b64encode(drawing.read_bytes()).decode("ascii")
    payload = {
        "instruction": prompt,
        "image": image_b64,
        "mode": "multi_turn",
        "provider": "aiping",
        "vision_model": vision_model,
        "plan_model": plan_model,
    }
    req = urllib.request.Request(
        f"{web_url.rstrip('/')}/api/run",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1200) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        error_usage = None
        try:
            err_body = json.loads(detail)
            error_usage = err_body.get("token_usage")
            detail = err_body.get("error") or detail
        except Exception:
            pass
        return {
            "part_id": part_id,
            "name": sample.get("name"),
            "status": "error",
            "acceptance_ok": False,
            "error": f"HTTP {exc.code}: {detail[:500]}",
            "token_usage": error_usage,
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup,
        }
    except Exception as exc:
        return {
            "part_id": part_id,
            "name": sample.get("name"),
            "status": "error",
            "acceptance_ok": False,
            "error": str(exc),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "cleanup_before": cleanup,
        }

    if not body.get("success"):
        return {
            "part_id": part_id,
            "name": sample.get("name"),
            "status": "error",
            "acceptance_ok": False,
            "error": body.get("error"),
            "duration_seconds": round(time.perf_counter() - started, 2),
            "token_usage": body.get("token_usage"),
            "cleanup_before": cleanup,
        }

    mt = body.get("multi_turn") or {}
    summary = body.get("summary") or {}
    final_obs = body.get("final_observation") or {}
    bbox = final_obs.get("bounding_box_mm")
    volume = final_obs.get("volume_mm3")

    # Prefer live SW read if observation incomplete
    if bbox is None or volume is None:
        try:
            with urllib.request.urlopen(
                f"{sw_url.rstrip('/')}/api/body-bounding-box",
                timeout=60,
            ) as resp:
                bb_payload = json.loads(resp.read().decode("utf-8"))
            if bb_payload.get("success"):
                b = bb_payload["boundingBox"]
                bbox = {
                    "sizeX": b["sizeX"] * 1000,
                    "sizeY": b["sizeY"] * 1000,
                    "sizeZ": b["sizeZ"] * 1000,
                }
        except Exception:
            pass
        try:
            with urllib.request.urlopen(
                f"{sw_url.rstrip('/')}/api/body-volume",
                timeout=60,
            ) as resp:
                vol_payload = json.loads(resp.read().decode("utf-8"))
            if vol_payload.get("success"):
                volume = vol_payload.get("volumeMm3")
        except Exception:
            pass

    dims_ok = _bbox_ok(bbox, gt["expected_bbox_mm"])
    vol_ok = _volume_ok(volume, float(gt["expected_volume_mm3"]))
    expected_holes = len(gt.get("holes") or [])
    actual_holes = _count_cut_circles(final_obs)
    holes_ok = expected_holes == 0 or actual_holes == expected_holes
    status = mt.get("status") or summary.get("status")
    acceptance = (
        status == "completed"
        and bool(summary.get("all_ok"))
        and dims_ok
        and vol_ok
        and holes_ok
    )
    return {
        "part_id": part_id,
        "name": sample.get("name"),
        "status": status,
        "acceptance_ok": acceptance,
        "dimensions_ok": dims_ok,
        "volume_ok": vol_ok,
        "holes_ok": holes_ok,
        "actual_holes": actual_holes,
        "expected_holes": expected_holes,
        "bbox_mm": bbox,
        "expected_bbox_mm": gt["expected_bbox_mm"],
        "volume_mm3": volume,
        "expected_volume_mm3": gt["expected_volume_mm3"],
        "turns": summary.get("turn_count"),
        "llm_calls": summary.get("llm_calls"),
        "actions": f"{summary.get('succeeded')}/{summary.get('total')}",
        "token_usage": body.get("token_usage") or summary.get("token_usage"),
        "vision_ir": body.get("vision_ir"),
        "blueprint": body.get("blueprint"),
        "task_plan": body.get("task_plan"),
        "turn_diagnostics": body.get("turns"),
        "final_observation": final_obs,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "cleanup_before": cleanup,
        "error": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument(
        "--ids",
        nargs="*",
        help="Run explicit sample IDs instead of the first --count samples",
    )
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--vision-model", default="GLM-4.6V")
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument(
        "--minimal-hint",
        action="store_true",
        help="Use one generic instruction for every image; no case-specific hints",
    )
    parser.add_argument(
        "--requirements-hint",
        action="store_true",
        help="Use case-specific natural geometry requirements without API steps",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "eval" / "three_view_multiturn_result.json",
    )
    args = parser.parse_args()
    if args.minimal_hint and args.requirements_hint:
        parser.error("--minimal-hint and --requirements-hint are mutually exclusive")

    env_path = ROOT / ".env"
    balance_before = _query_balance(env_path)
    all_samples = _load_first_samples(10_000)
    if args.ids:
        wanted = set(args.ids)
        samples = [
            sample for sample in all_samples
            if sample["part_id"] in wanted
        ]
    else:
        samples = all_samples[:args.count]
    results = []
    for sample in samples:
        print(f"\n[{sample['part_id']}] {sample.get('name')}", flush=True)
        result = run_one(
            sample,
            web_url=args.web_url,
            sw_url=args.sw_url,
            vision_model=args.vision_model,
            plan_model=args.plan_model,
            minimal_hint=args.minimal_hint,
            requirements_hint=args.requirements_hint,
        )
        results.append(result)
        usage = result.get("token_usage") or {}
        print(
            f"  status={result.get('status')}; "
            f"acceptance={result.get('acceptance_ok')}; "
            f"tokens={usage.get('total_tokens')}; "
            f"duration={result.get('duration_seconds')}s",
            flush=True,
        )
        args.output.write_text(
            json.dumps(
                {
                    "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "mode": "multi_turn",
                    "provider": "AI Ping",
                    "vision_model": args.vision_model,
                    "plan_model": args.plan_model,
                    "input_policy": (
                        "minimal_generic_hint"
                        if args.minimal_hint
                        else (
                            "natural_requirements_hint"
                            if args.requirements_hint
                            else "case_specific_hint"
                        )
                    ),
                    "balance_before": balance_before,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    balance_after = _query_balance(env_path)
    total_tokens = sum(
        int((r.get("token_usage") or {}).get("total_tokens") or 0) for r in results
    )
    prompt_tokens = sum(
        int((r.get("token_usage") or {}).get("prompt_tokens") or 0) for r in results
    )
    completion_tokens = sum(
        int((r.get("token_usage") or {}).get("completion_tokens") or 0) for r in results
    )
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
        "vision_model": args.vision_model,
        "plan_model": args.plan_model,
        "input_policy": (
            "minimal_generic_hint"
            if args.minimal_hint
            else (
                "natural_requirements_hint"
                if args.requirements_hint
                else "case_specific_hint"
            )
        ),
        "sample_ids": [s["part_id"] for s in samples],
        "passed": sum(1 for r in results if r.get("acceptance_ok")),
        "total": len(results),
        "token_totals": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
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
        f"\n总结: {report['passed']}/{report['total']} 通过; "
        f"tokens={total_tokens}; spent_yuan={spent_yuan}",
        flush=True,
    )
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
