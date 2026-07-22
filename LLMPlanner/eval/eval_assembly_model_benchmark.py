"""Compare multiple LLMs on the three multi-turn assembly challenges.

Quality is scored independently from token and wall-clock efficiency:

* 40 points: deterministic static assembly checks.
* 40 points: case-specific functional/mechanism checks.
* 20 points: completion and execution reliability.

The runner deliberately omits ``--use-cache``. Parameterized part files may be
reused because they are deterministic fixtures; every assembly and every LLM
decision is executed again.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
DEFAULT_MODELS = (
    "Qwen3.5-397B-A17B",
    "GLM-5",
    "MiniMax-M2.5",
)
MODEL_LIST_URL = "https://aiping.cn/api/v1/models"
BENCHMARK_ROOT = Path(__file__).parent / "artifacts" / (
    "assembly_model_benchmark"
)

CASE_CONFIG = {
    "gearbox": {
        "label": "双级减速箱",
        "script": "eval_two_stage_gearbox_multiturn.py",
        "artifact_dir": "two_stage_gearbox",
        "max_turns": 18,
        "dynamic_key": None,
        "functional_checks": (
            "component_positions",
            "mate_type_count",
            "gear_ratios",
            "no_interference",
        ),
    },
    "engine": {
        "label": "单缸曲柄滑块发动机",
        "script": "eval_single_cylinder_engine_multiturn.py",
        "artifact_dir": "single_cylinder_engine",
        "max_turns": 24,
        "dynamic_key": "kinematic_acceptance",
    },
    "projector": {
        "label": "复古 16mm 放映机",
        "script": "eval_film_projector_multiturn.py",
        "artifact_dir": "film_projector",
        "max_turns": 28,
        "dynamic_key": "indexing_acceptance",
    },
    "sewing_machine": {
        "label": "家用机械缝纫机",
        "script": "eval_household_sewing_machine_multiturn.py",
        "artifact_dir": "household_sewing_machine",
        "max_turns": 42,
        "dynamic_key": "mechanism_acceptance",
        "sample_groups": (
            "needle",
            "rotary",
            "four_bar",
            "cam",
        ),
        "global_checks": (
            "needle.cycle_closed",
            "rotary.cycle_closed",
            "four_bar.cycle_closed",
            "cam.cycle_closed",
        ),
    },
}


def _fraction(values: list[bool]) -> float:
    if not values:
        return 0.0
    return sum(bool(value) for value in values) / len(values)


def _static_report(agent_run: dict[str, Any]) -> dict[str, Any]:
    report = (
        agent_run.get("static_acceptance")
        or agent_run.get("acceptance")
        or {}
    )
    return report if isinstance(report, dict) else {}


def _execution_metrics(agent_run: dict[str, Any]) -> dict[str, float]:
    result = agent_run.get("result")
    if not isinstance(result, dict):
        return {
            "action_success_rate": 0.0,
            "milestone_completion_rate": 0.0,
        }
    total_actions = int(result.get("total_actions") or 0)
    successful_actions = int(result.get("successful_actions") or 0)
    action_rate = (
        successful_actions / total_actions
        if total_actions > 0
        else (1.0 if result.get("status") == "completed" else 0.0)
    )
    milestones = result.get("milestones") or []
    completed = [
        item
        for item in milestones
        if (
            isinstance(item, dict)
            and item.get("status") in {"completed", "done"}
        )
    ]
    milestone_rate = (
        len(completed) / len(milestones)
        if milestones
        else 0.0
    )
    return {
        "action_success_rate": action_rate,
        "milestone_completion_rate": milestone_rate,
    }


def _dynamic_functional_score(
    case_id: str,
    agent_run: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    config = CASE_CONFIG[case_id]
    dynamic_key = config.get("dynamic_key")
    if dynamic_key:
        dynamic = agent_run.get(dynamic_key) or {}
        sample_groups = config.get("sample_groups")
        if sample_groups:
            samples = [
                sample
                for group in sample_groups
                for sample in (
                    (dynamic.get(group) or {}).get("samples") or []
                )
                if isinstance(sample, dict)
            ]
        else:
            samples = [
                sample
                for sample in (dynamic.get("samples") or [])
                if isinstance(sample, dict)
            ]
        sample_rate = _fraction([
            bool(sample.get("passed"))
            for sample in samples
        ])
        configured_globals = config.get("global_checks")
        if configured_globals:
            global_checks = []
            for key in configured_globals:
                value: Any = dynamic
                for part in str(key).split("."):
                    value = (
                        value.get(part)
                        if isinstance(value, dict)
                        else None
                    )
                global_checks.append(bool(value))
        elif case_id == "engine":
            global_checks = [
                bool(dynamic.get("cycle_closed")),
                (
                    "actual_stroke_mm" in dynamic
                    and "expected_stroke_mm" in dynamic
                    and abs(
                        float(dynamic["actual_stroke_mm"])
                        - float(dynamic["expected_stroke_mm"])
                    ) <= 0.25
                ),
            ]
        else:
            global_checks = [
                bool(dynamic.get("dwell_locked")),
                bool(dynamic.get("full_cycle_closed")),
            ]
        global_rate = _fraction(global_checks)
        score = 30.0 * sample_rate + 10.0 * global_rate
        return score, {
            "sample_pass_rate": sample_rate,
            "global_check_rate": global_rate,
            "sample_count": len(samples),
        }

    static = _static_report(agent_run)
    checks = static.get("checks") or {}
    relevant = []
    for key, passed in checks.items():
        kind = str(key).split(":", 1)[-1]
        if kind in config["functional_checks"]:
            relevant.append(bool(passed))
    rate = _fraction(relevant)
    return 40.0 * rate, {
        "functional_check_rate": rate,
        "functional_check_count": len(relevant),
    }


def score_agent_run(
    case_id: str,
    agent_run: dict[str, Any],
) -> dict[str, Any]:
    """Return a deterministic 0-100 score for one assembly run."""
    if case_id not in CASE_CONFIG:
        raise ValueError(f"unknown assembly case: {case_id}")

    static = _static_report(agent_run)
    static_checks = static.get("checks") or {}
    static_rate = _fraction([
        bool(value) for value in static_checks.values()
    ])
    static_points = 40.0 * static_rate

    functional_points, functional_details = (
        _dynamic_functional_score(case_id, agent_run)
    )
    execution = _execution_metrics(agent_run)
    status = str(agent_run.get("status") or "error")
    status_points = {
        "completed": 10.0,
        "blocked": 5.0,
    }.get(status, 0.0)
    execution_points = (
        status_points
        + 5.0 * execution["action_success_rate"]
        + 5.0 * execution["milestone_completion_rate"]
    )
    total = static_points + functional_points + execution_points
    return {
        "score": round(max(0.0, min(100.0, total)), 2),
        "static_points": round(static_points, 2),
        "functional_points": round(functional_points, 2),
        "execution_points": round(execution_points, 2),
        "static_check_rate": round(static_rate, 6),
        **{
            key: round(value, 6) if isinstance(value, float) else value
            for key, value in functional_details.items()
        },
        **{
            key: round(value, 6)
            for key, value in execution.items()
        },
    }


def fetch_aiping_models() -> dict[str, dict[str, Any]]:
    with urllib.request.urlopen(MODEL_LIST_URL, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {
        item["id"]: item
        for item in payload.get("data", [])
        if item.get("status") is True
    }


def _estimated_cost(
    model_info: dict[str, Any] | None,
    usage: dict[str, Any],
) -> dict[str, float] | None:
    if not model_info:
        return None
    price = model_info.get("price") or {}
    input_range = price.get("input_price_range") or []
    output_range = price.get("output_price_range") or []
    if len(input_range) < 2 or len(output_range) < 2:
        return None
    prompt = float(usage.get("prompt_tokens") or 0) / 1_000_000
    completion = float(usage.get("completion_tokens") or 0) / 1_000_000
    return {
        "minimum": round(
            prompt * float(input_range[0])
            + completion * float(output_range[0]),
            6,
        ),
        "maximum": round(
            prompt * float(input_range[1])
            + completion * float(output_range[1]),
            6,
        ),
    }


def _close_solidworks_documents(sw_url: str) -> None:
    request = urllib.request.Request(
        f"{sw_url.rstrip('/')}/api/close-all-documents",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120):
        pass


def _result_path(case_id: str) -> Path:
    artifact = CASE_CONFIG[case_id]["artifact_dir"]
    return Path(__file__).parent / "artifacts" / artifact / "result.json"


def _validation_result_path(case_id: str) -> Path:
    artifact = CASE_CONFIG[case_id]["artifact_dir"]
    return (
        Path(__file__).parent
        / "artifacts"
        / artifact
        / "validation_result.json"
    )


def merge_machine_evidence(
    primary_run: dict[str, Any],
    recovered_run: dict[str, Any],
) -> dict[str, Any]:
    """Attach post-run machine evidence without hiding agent failure."""
    merged = dict(primary_run)
    for key in (
        "acceptance",
        "static_acceptance",
        "kinematic_acceptance",
        "indexing_acceptance",
        "final_observation",
    ):
        if key in recovered_run:
            merged[key] = recovered_run[key]
    merged["post_run_machine_passed"] = bool(
        recovered_run.get("passed")
    )
    if str(primary_run.get("status")) == "completed":
        merged["passed"] = bool(recovered_run.get("passed"))
    return merged


def _recover_machine_evidence(
    *,
    case_id: str,
    command: list[str],
    output_dir: Path,
    stem: str,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    validation_path = _validation_result_path(case_id)
    validation_path.unlink(missing_ok=True)
    recovery_log = output_dir / f"{stem}.validation.log"
    validation_command = [
        command[0],
        command[1],
        "--sw-url",
        command[3],
        "--validate-existing",
    ]
    with recovery_log.open("w", encoding="utf-8") as log_file:
        try:
            subprocess.run(
                validation_command,
                cwd=ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=min(timeout_seconds, 300),
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            log_file.write("\n[BENCHMARK] validation timed out\n")
    if not validation_path.is_file():
        return None
    payload = json.loads(validation_path.read_text(encoding="utf-8"))
    recovered = payload.get("agent_run")
    return recovered if isinstance(recovered, dict) else None


def run_one(
    *,
    case_id: str,
    model: str,
    output_dir: Path,
    sw_url: str,
    timeout_seconds: int,
    model_info: dict[str, Any] | None,
) -> dict[str, Any]:
    config = CASE_CONFIG[case_id]
    safe_model = "".join(
        character if character.isalnum() else "_"
        for character in model
    )
    stem = f"{case_id}__{safe_model}"
    result_copy = output_dir / f"{stem}.json"
    log_path = output_dir / f"{stem}.log"
    started = time.perf_counter()
    _close_solidworks_documents(sw_url)
    source_result = _result_path(case_id)
    source_result.unlink(missing_ok=True)
    command = [
        sys.executable,
        str(Path(__file__).with_name(config["script"])),
        "--sw-url",
        sw_url,
        "--model",
        model,
        "--max-turns",
        str(config["max_turns"]),
    ]
    timed_out = False
    return_code: int | None = None
    with log_path.open("w", encoding="utf-8") as log_file:
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
            return_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            log_file.write(
                f"\n[BENCHMARK] timed out after {timeout_seconds}s\n"
            )

    if source_result.is_file():
        shutil.copy2(source_result, result_copy)
        payload = json.loads(result_copy.read_text(encoding="utf-8"))
        agent_run = payload.get("agent_run") or {}
    else:
        payload = {}
        agent_run = {
            "status": "error",
            "passed": False,
            "error": "eval did not produce result.json",
            "token_usage": {},
        }
    if not agent_run.get("passed"):
        recovered = _recover_machine_evidence(
            case_id=case_id,
            command=command,
            output_dir=output_dir,
            stem=stem,
            timeout_seconds=timeout_seconds,
        )
        if recovered is not None:
            agent_run = merge_machine_evidence(agent_run, recovered)
            payload["agent_run"] = agent_run
            result_copy.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    scored = score_agent_run(case_id, agent_run)
    usage = agent_run.get("token_usage") or {}
    return {
        "case_id": case_id,
        "case_label": config["label"],
        "model": model,
        "status": agent_run.get("status", "error"),
        "passed": bool(agent_run.get("passed")),
        **scored,
        "duration_seconds": round(
            float(
                agent_run.get("duration_seconds")
                or time.perf_counter() - started
            ),
            3,
        ),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "token_usage": usage,
        "estimated_cost": _estimated_cost(model_info, usage),
        "return_code": return_code,
        "timed_out": timed_out,
        "error": agent_run.get("error"),
        "result_path": str(result_copy),
        "log_path": str(log_path),
    }


def summarize(
    results: list[dict[str, Any]],
    models: list[str],
    cases: list[str],
) -> dict[str, Any]:
    model_summaries = []
    for model in models:
        rows = [row for row in results if row["model"] == model]
        model_summaries.append({
            "model": model,
            "average_score": round(
                sum(row["score"] for row in rows) / len(rows),
                2,
            ) if rows else 0.0,
            "passed_cases": sum(bool(row["passed"]) for row in rows),
            "case_count": len(rows),
            "total_tokens": sum(
                int(row["token_usage"].get("total_tokens") or 0)
                for row in rows
            ),
            "token_usage_complete": all(
                "total_tokens" in row["token_usage"]
                for row in rows
            ),
            "total_duration_seconds": round(
                sum(float(row["duration_seconds"]) for row in rows),
                3,
            ),
        })
    model_summaries.sort(
        key=lambda item: (
            -item["average_score"],
            item["total_tokens"],
        )
    )
    return {
        "models": models,
        "cases": cases,
        "results": results,
        "model_summaries": model_summaries,
    }


def render_markdown(summary: dict[str, Any], run_id: str) -> str:
    lines = [
        f"## 三模型复杂装配体 Multi-turn 对比（{run_id}）",
        "",
        "评分：静态机器验收 40 分、机构/功能验收 40 分、"
        "执行完成度与可靠性 20 分；token 与耗时不计入质量分。",
        "所有运行禁用 LLM 缓存，复用的只有确定性零件文件。",
        "",
        "| 模型 | 测例 | 分数 | 通过 | 状态 | Tokens | Agent耗时(s) |",
        "|---|---|---:|:---:|---|---:|---:|",
    ]
    for row in summary["results"]:
        token_text = (
            f"{int(row['token_usage']['total_tokens']):,}"
            if "total_tokens" in row["token_usage"]
            else "未回传"
        )
        lines.append(
            f"| `{row['model']}` | {row['case_label']} | "
            f"**{row['score']:.2f}** | "
            f"{'是' if row['passed'] else '否'} | "
            f"{row['status']} | "
            f"{token_text} | "
            f"{row['duration_seconds']:.2f} |"
        )
    lines.extend([
        "",
        "| 模型 | 平均分 | 通过数 | 总 Tokens | 总耗时(s) |",
        "|---|---:|---:|---:|---:|",
    ])
    for item in summary["model_summaries"]:
        total_token_text = (
            f"{item['total_tokens']:,}"
            if item["token_usage_complete"]
            else f"≥{item['total_tokens']:,}（1 项未回传）"
        )
        lines.append(
            f"| `{item['model']}` | **{item['average_score']:.2f}** | "
            f"{item['passed_cases']}/{item['case_count']} | "
            f"{total_token_text} | "
            f"{item['total_duration_seconds']:.2f} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(DEFAULT_MODELS),
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=list(CASE_CONFIG),
        default=list(CASE_CONFIG),
    )
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--recover-result", type=Path)
    parser.add_argument(
        "--recover-case",
        choices=list(CASE_CONFIG),
    )
    parser.add_argument("--recover-model")
    args = parser.parse_args()

    output_dir = BENCHMARK_ROOT / args.run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        available = fetch_aiping_models()
    except Exception as exc:
        print(f"[WARN] 无法读取 AI Ping 模型列表: {exc}")
        available = {}
    if available:
        missing = [model for model in args.models if model not in available]
        if missing:
            raise SystemExit(f"AI Ping 当前不可用模型: {missing}")

    results: list[dict[str, Any]] = []
    total = len(args.models) * len(args.cases)
    position = 0
    for model in args.models:
        for case_id in args.cases:
            position += 1
            safe_model = "".join(
                character if character.isalnum() else "_"
                for character in model
            )
            previous = output_dir / f"{case_id}__{safe_model}.score.json"
            if args.resume and previous.is_file():
                row = json.loads(previous.read_text(encoding="utf-8"))
                copied_result = Path(row.get("result_path", ""))
                if copied_result.is_file():
                    payload = json.loads(
                        copied_result.read_text(encoding="utf-8")
                    )
                    agent_run = payload.get("agent_run") or {}
                    if (
                        args.recover_result
                        and args.recover_case == case_id
                        and args.recover_model == model
                    ):
                        recovered_payload = json.loads(
                            args.recover_result.read_text(
                                encoding="utf-8"
                            )
                        )
                        recovered_run = (
                            recovered_payload.get("agent_run") or {}
                        )
                        agent_run = merge_machine_evidence(
                            agent_run,
                            recovered_run,
                        )
                        payload["agent_run"] = agent_run
                        copied_result.write_text(
                            json.dumps(
                                payload,
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                    row.update(score_agent_run(case_id, agent_run))
                    previous.write_text(
                        json.dumps(row, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                print(
                    f"[{position}/{total}] resume {model} / {case_id}: "
                    f"{row['score']}"
                )
            else:
                print(f"[{position}/{total}] run {model} / {case_id}")
                row = run_one(
                    case_id=case_id,
                    model=model,
                    output_dir=output_dir,
                    sw_url=args.sw_url,
                    timeout_seconds=args.timeout_seconds,
                    model_info=available.get(model),
                )
                previous.write_text(
                    json.dumps(row, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(
                    f"[{position}/{total}] score={row['score']} "
                    f"passed={row['passed']} "
                    f"tokens={row['token_usage'].get('total_tokens', 0)}"
                )
            results.append(row)

    summary = summarize(results, list(args.models), list(args.cases))
    summary.update({
        "run_id": args.run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cache_enabled": False,
        "scoring": {
            "static": 40,
            "functional": 40,
            "execution": 20,
        },
    })
    summary_path = output_dir / "summary.json"
    markdown_path = output_dir / "summary.md"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_markdown(summary, args.run_id),
        encoding="utf-8",
    )
    print(f"summary: {summary_path}")
    print(f"markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
