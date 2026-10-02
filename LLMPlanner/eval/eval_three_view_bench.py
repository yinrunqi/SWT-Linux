"""Run the Workflow-CAD-Bench three-view track against the Web Demo and keep one STEP per run.

Each task posts the benchmark drawing (eval/three_view/drawings/<case>.png) and the neutral
prompt from eval/rubrics_three_view/<case>.yaml to /api/run, then exports the final part as
<out-dir>/<case>_r<k>.step. Scoring is offline and needs no SolidWorks:

    venv-occ\\Scripts\\python.exe -X utf8 -m geomcheck.three_view --artifacts <out-dir> --output scores.json
    venv-occ\\Scripts\\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view \\
        summary scores.json --repeats 2
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
RUBRIC_DIR = ROOT / "eval" / "rubrics_three_view"
PROMPT_FIELDS = {"zh": "prompt", "en": "prompt_en"}


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


def run_one(task: dict, k: int, args) -> dict:
    started = time.perf_counter()
    stem = args.out_dir / f"{task['case']}_r{k}"
    _close_documents(args.sw_url)
    time.sleep(1.0)
    payload = {
        "instruction": task["prompt"],
        "image": base64.b64encode(task["image"].read_bytes()).decode("ascii"),
        "mode": args.mode,
        "provider": "aiping",
        "vision_model": args.vision_model,
        "plan_model": args.plan_model,
        "use_cache": False,
    }
    row: dict[str, Any] = {"case": task["case"], "repeat": k}
    try:
        response = requests.post(f"{args.web_url}/api/run", json=payload, timeout=3600)
        try:
            body = response.json()
        except ValueError:
            body = {"error": response.text[:500]}
        row["token_usage"] = body.get("token_usage")
        row["status"] = (body.get("multi_turn") or {}).get("status") or (body.get("summary") or {}).get("status")
        if not response.ok or not body.get("success"):
            row["error"] = body.get("error") or f"HTTP {response.status_code}"
        # 失败的运行也导出当前零件：部分完成的几何同样按 rubric 计分
        row["artifacts"] = _save_artifacts(args.sw_url, stem)
    except Exception as exc:
        row["error"] = str(exc)
    finally:
        try:
            _close_documents(args.sw_url)
        except Exception as exc:
            row["cleanup_error"] = str(exc)
    row["duration_seconds"] = round(time.perf_counter() - started, 2)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", nargs="*", help="case ids (default: all 13)")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--lang", choices=sorted(PROMPT_FIELDS), default="zh")
    parser.add_argument("--mode", choices=["multi_turn", "single_turn"], default="multi_turn")
    parser.add_argument("--vision-model", default="GLM-4.6V")
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--name", default=datetime.now().strftime("three_view_%Y%m%d_%H%M"))
    parser.add_argument("--resume", action="store_true", help="skip runs whose STEP already exists")
    args = parser.parse_args()
    args.out_dir = ROOT / "eval" / "artifacts" / args.name
    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "runs.json"
    rows = json.loads(log_path.read_text(encoding="utf-8")) if args.resume and log_path.exists() else []

    for task in load_tasks(args.lang, args.ids):
        for k in range(1, args.repeats + 1):
            if args.resume and (args.out_dir / f"{task['case']}_r{k}.step").exists():
                continue
            print(f"[{task['case']} r{k}]", flush=True)
            row = run_one(task, k, args)
            print(f"    status={row.get('status')} error={row.get('error')} {row['duration_seconds']}s", flush=True)
            rows.append(row)
            log_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"STEP files and run log in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
