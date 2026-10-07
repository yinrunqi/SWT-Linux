"""Run the Workflow-CAD-Bench three-view track against the Web Demo and keep one STEP per run.

Each task posts the benchmark drawing (eval/three_view/drawings/<case>.png) and the neutral
prompt from eval/rubrics_three_view/<case>.yaml to /api/run, then exports the final part as
<out-dir>/<case>_r<k>.step. The default --action-level L1 is end to end: the L1 workflow agent sees
the drawing itself (so --plan-model must accept images) and may call render_views to compare the
current part against it. Scoring is offline and needs no SolidWorks:

    venv-occ\\Scripts\\python.exe -X utf8 -m geomcheck.three_view --artifacts <out-dir> --output scores.json
    venv-occ\\Scripts\\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view \\
        summary scores.json --repeats 2

L1 runs save the agent state to <out-dir>/<case>_r<k>.checkpoint.json after every LLM reply and tool
round (full conversation, raw LLM responses, tool log); --resume continues interrupted runs from it.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

try:
    from . import sw_watchdog
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sw_watchdog

ROOT = Path(__file__).resolve().parent.parent
RUBRIC_DIR = ROOT / "eval" / "rubrics_three_view"
PROMPT_FIELDS = {"zh": "prompt", "en": "prompt_en"}
WORKFLOW_LEVELS = ("L1", "L1_v2")
# 请求异常、LLM 接口出错、SolidWorks 文档丢失或续跑重放失败：都不是模型能力的结果，--resume 时续跑
RERUN_STATUSES = {"llm_error", "sw_lost", "resume_failed"}


def load_tasks(lang: str, ids: list[str] | None) -> list[dict]:
    tasks = []
    for path in sorted(RUBRIC_DIR.glob("*.yaml")):
        rubric = yaml.safe_load(path.read_text(encoding="utf-8"))
        if ids and rubric["case"] not in ids:
            continue
        tasks.append({"case": rubric["case"], "prompt": rubric[PROMPT_FIELDS[lang]], "image": ROOT / rubric["image"]})
    return tasks


def _post_json(url: str, payload: dict, timeout: int) -> dict:
    response = requests.post(url, json=payload, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {"error": response.text[:500]}
    if not response.ok or not body.get("success"):
        raise RuntimeError(body.get("error") or f"HTTP {response.status_code}")
    return body


def _close_documents(sw_url: str) -> None:
    _post_json(f"{sw_url}/api/close-all-documents", {}, timeout=60)


def _save_artifacts(sw_url: str, stem: Path) -> dict:
    stem.parent.mkdir(parents=True, exist_ok=True)
    saved: dict[str, Any] = {}
    for key, route, field, suffix in (
        ("step", "/api/export-step", "stepPath", ".step"),
        ("sldprt", "/api/save-part-as", "savePath", ".SLDPRT"),
    ):
        path = stem.with_suffix(suffix).resolve()
        try:
            _post_json(f"{sw_url}{route}", {field: str(path)}, timeout=120)
            saved[key] = str(path)
        except Exception as exc:
            saved[f"{key}_error"] = str(exc)
    return saved


def strip_images(value):
    """trace 里的图片 data URI 换成占位：输入图纸已在 drawings/ 下，投影图可由 STEP 重新生成。"""
    if isinstance(value, dict):
        return {key: strip_images(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_images(item) for item in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return "[image]"
    return value


def _write_trace(path: Path, request: dict, status: int | None, body: Any, error: str | None) -> None:
    trace = {"request": strip_images(request), "response_status": status, "response": strip_images(body), "error": error}
    path.write_text(json.dumps(trace, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def needs_rerun(row: dict) -> bool:
    return bool(row.get("error")) or row.get("status") in RERUN_STATUSES


def run_one(task: dict, k: int, args, resume: bool = False) -> dict:
    started = time.perf_counter()
    stem = args.out_dir / f"{task['case']}_r{k}"
    checkpoint = stem.parent / f"{stem.name}.checkpoint.json"
    if not resume:
        checkpoint.unlink(missing_ok=True)
    _close_documents(args.sw_url)
    time.sleep(1.0)
    payload = {
        "instruction": task["prompt"],
        "image": "data:image/png;base64," + base64.b64encode(task["image"].read_bytes()).decode("ascii"),
        "provider": args.provider,
        "plan_model": args.plan_model,
        "action_level": args.action_level,
        "use_cache": False,
        "capture_trace": True,
    }
    if args.action_level in WORKFLOW_LEVELS:
        # 端到端：图纸直接交给 plan_model（须为多模态模型），没有单独的读图阶段
        payload.update({"max_turns": args.max_llm_calls, "views_tool": not args.no_views_tool,
                        "checkpoint_path": str(checkpoint), "resume": resume})
        if args.action_level == "L1_v2":
            payload["view_tool"] = not args.no_view_tool
        if args.reasoning_effort:
            payload["reasoning_effort"] = args.reasoning_effort
    else:
        payload.update({"mode": args.mode, "vision_model": args.vision_model})
    trace_path = stem.parent / f"{stem.name}.trace.json"
    row: dict[str, Any] = {"case": task["case"], "repeat": k, "action_level": args.action_level,
                           "plan_model": args.plan_model, "trace": str(trace_path)}
    if args.action_level in WORKFLOW_LEVELS:
        row["checkpoint"] = str(checkpoint)
    status_code, body = None, None
    try:
        response = requests.post(f"{args.web_url}/api/run", json=payload, timeout=3600)
        status_code = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text[:500], "raw_text": response.text}
        _write_trace(trace_path, payload, status_code, body, None)
        row["token_usage"] = body.get("token_usage")
        row["status"] = (body.get("multi_turn") or {}).get("status") or (body.get("summary") or {}).get("status")
        if args.action_level in WORKFLOW_LEVELS:
            summary, workflow = body.get("summary") or {}, body.get("workflow") or {}
            row["workflow_error"] = workflow.get("error")
            row["resumed"] = workflow.get("resumed")
            row["reasoning_effort"] = workflow.get("reasoning_effort")
            row["view_tool"] = workflow.get("view_tool")
            row["context_limit"] = workflow.get("context_limit")
            row["llm_calls"] = summary.get("llm_calls")
            row["tool_calls"] = summary.get("tool_calls")
            row["failed_modeling_calls"] = sum(1 for item in body.get("results") or [] if not item.get("success"))
            row["render_views_calls"] = sum(
                1 for item in (body.get("workflow") or {}).get("tool_calls") or [] if item.get("tool") == "render_views"
            )
        if not response.ok or not body.get("success"):
            row["error"] = body.get("error") or f"HTTP {response.status_code}"
        # 失败的运行也导出当前零件：部分完成的几何同样按 rubric 计分
        row["artifacts"] = _save_artifacts(args.sw_url, stem)
    except Exception as exc:
        row["error"] = str(exc)
        if body is None:
            _write_trace(trace_path, payload, status_code, None, str(exc))
    finally:
        try:
            _close_documents(args.sw_url)
        except Exception as exc:
            row["cleanup_error"] = str(exc)
    row["duration_seconds"] = round(time.perf_counter() - started, 2)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", nargs="*", help="case ids (default: all)")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--lang", choices=sorted(PROMPT_FIELDS), default="zh")
    parser.add_argument("--action-level", choices=["L0", *WORKFLOW_LEVELS], default="L1",
                        help="L1: end-to-end workflow agent that sees the drawing (plan model must accept images); "
                             "L1_v2: L1 with the text-to-CAD failure-analysis improvements and view_model; "
                             "L0: legacy vision-IR + blueprint pipeline")
    parser.add_argument(
        "--provider",
        choices=["aiping", "ttswitch", "juheai", "qwenlocal"],
        default="aiping",
        help="LLM service the Web backend uses: ttswitch reads OPENAI_* from .env, others read <PROVIDER>_*",
    )
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="L1/L1_v2 only; sent on every agent call. Omitted = the same model-specific default as text-to-CAD",
    )
    parser.add_argument("--max-llm-calls", type=int, default=40, help="L1 LLM call budget")
    parser.add_argument("--no-views-tool", action="store_true", help="L1: do not offer render_views (ablation)")
    parser.add_argument(
        "--no-view-tool",
        action="store_true",
        help="L1_v2: do not offer the view_model SolidWorks screenshot tool",
    )
    parser.add_argument("--mode", choices=["multi_turn", "single_turn"], default="multi_turn", help="L0 only")
    parser.add_argument("--vision-model", default="GLM-4.6V", help="L0 only")
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--name", default=datetime.now().strftime("three_view_%Y%m%d_%H%M"))
    parser.add_argument(
        "--resume", action="store_true",
        help="keep finished runs from <out-dir>/runs.json; runs that errored or were interrupted (llm_error / "
        "sw_lost / resume_failed) and missing runs continue from their L1 checkpoints when one exists",
    )
    parser.add_argument(
        "--sw-restarts", type=int, default=0,
        help="SolidWorks watchdog: before each run restart SolidWorks and the HTTP service when either is down "
        "or SolidWorks exceeds --sw-max-private-gb; a run that loses SolidWorks is restarted and continued from "
        "its checkpoint up to this many times. 0 = off",
    )
    parser.add_argument("--sw-max-private-gb", type=float, default=12.0)
    args = parser.parse_args()
    if args.reasoning_effort and args.action_level not in WORKFLOW_LEVELS:
        parser.error("--reasoning-effort requires --action-level L1 or L1_v2")
    if args.action_level in WORKFLOW_LEVELS:
        from planner.workflow.agent import default_reasoning_effort
        args.reasoning_effort = default_reasoning_effort(args.plan_model, args.reasoning_effort)
    args.out_dir = ROOT / "eval" / "artifacts" / args.name
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "runs.json"
    rows = json.loads(log_path.read_text(encoding="utf-8")) if args.resume and log_path.exists() else []
    if rows:
        kept = {(row["case"], row["repeat"]): row for row in rows if not needs_rerun(row)}
        rows = list(kept.values())
        print(f"resume: keeping {len(rows)} finished runs from {log_path}", flush=True)
    done = {(row["case"], row["repeat"]) for row in rows}
    sw_url = args.sw_url.rstrip("/")

    for task in load_tasks(args.lang, args.ids):
        for k in range(1, args.repeats + 1):
            if (task["case"], k) in done:
                continue
            checkpoint = args.out_dir / f"{task['case']}_r{k}.checkpoint.json"
            resuming = args.resume and args.action_level in WORKFLOW_LEVELS and checkpoint.is_file()
            print(f"[{task['case']} r{k}]{' resume from checkpoint' if resuming else ''}", flush=True)
            for attempt in range(1 + args.sw_restarts):
                if args.sw_restarts:
                    reason = sw_watchdog.check(sw_url, args.sw_max_private_gb)
                    if reason:
                        print(f"  [watchdog] {reason}", flush=True)
                        sw_watchdog.restart(sw_url)
                row = run_one(task, k, args, resume=resuming)
                lost = row.get("status") == "sw_lost" or (
                    bool(row.get("error")) and sw_watchdog.check(sw_url, 0) is not None
                )
                if not args.sw_restarts or not lost or attempt == args.sw_restarts:
                    break
                print(f"  [watchdog] SolidWorks lost during the run ({row.get('status') or row.get('error')}); "
                      "restarting and continuing from the checkpoint", flush=True)
                resuming = args.action_level in WORKFLOW_LEVELS and checkpoint.is_file()
            print(f"    status={row.get('status')} error={row.get('error') or row.get('workflow_error')} "
                  f"{row['duration_seconds']}s", flush=True)
            rows.append(row)
            log_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"STEP files and run log in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
