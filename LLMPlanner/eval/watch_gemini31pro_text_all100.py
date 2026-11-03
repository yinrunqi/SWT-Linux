#!/usr/bin/env python3
"""Keep a short Score / core_form / pass@1 / token summary next to the running result."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/yrq/SolidWorksTutorial/LLMPlanner")
RESULT = ROOT / "eval" / "text_workflow_l1v2_compact_gemini31pro_all100_en_result.json"
SUMMARY = ROOT / "eval" / "text_workflow_l1v2_compact_gemini31pro_all100_en_summary.txt"
RUBRIC_OUT = ROOT / "eval" / "rubric_l1v2_compact_gemini31pro_all100_live.json"
ARTIFACTS = ROOT / "eval" / "artifacts" / "text_workflow_l1v2_compact_gemini31pro_all100_en_result"
OCC = ROOT / "venv-occ" / "bin" / "python"
RUBRIC_DIRS = (
    ROOT / "eval" / "rubrics_hard",
    ROOT / "eval" / "rubrics_extreme",
    ROOT / "eval" / "rubrics",
)
WIN_ARTIFACTS = (
    "C:/Users/38409/Desktop/Workflow-CAD/SolidWorksTutorial/"
    "LLMPlanner/eval/artifacts/text_workflow_l1v2_compact_gemini31pro_all100_en_result"
)


def load_result() -> dict | None:
    if not RESULT.is_file():
        return None
    try:
        return json.loads(RESULT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_rubrics() -> dict[str, dict]:
    if not RUBRIC_OUT.is_file():
        return {}
    try:
        rows = json.loads(RUBRIC_OUT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {row["case_id"]: row for row in rows if row.get("case_id")}


def save_rubrics(rows: dict[str, dict]) -> None:
    RUBRIC_OUT.write_text(
        json.dumps(list(rows.values()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def write_summary(data: dict | None, rubrics: dict[str, dict], note: str) -> None:
    results = (data or {}).get("results") or []
    n = len(results)
    scores = [float(item["score"]) for item in results]
    passed = [bool(item.get("acceptance_ok")) for item in results]
    prompt = [int((item.get("token_usage") or {}).get("prompt_tokens") or 0) for item in results]
    completion = [int((item.get("token_usage") or {}).get("completion_tokens") or 0) for item in results]
    total = [int((item.get("token_usage") or {}).get("total_tokens") or 0) for item in results]
    graded = [rubrics[item["case_id"]] for item in results if item["case_id"] in rubrics and "score" in rubrics[item["case_id"]]]
    lines = [
        "gemini-3.1-pro  L1_v2 + compact-context  all100  en  repeats=1",
        f"updated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"finished: {n}/100",
        "",
        f"Score (几何在线均分): {fmt(mean(scores))}",
        f"pass@1 (几何验收): {fmt(mean([1.0 if item else 0.0 for item in passed]), 4)}",
        "Tokens（已完成题的平均）:",
        f"  输入: {fmt(mean(prompt), 1)}",
        f"  输出: {fmt(mean(completion), 1)}",
        f"  总计: {fmt(mean(total), 1)}",
        "",
        f"Form / 核心形体率: {fmt(mean([1.0 if item.get('hard_ok') else 0.0 for item in graded]), 3)}"
        f"  ({sum(bool(item.get('hard_ok')) for item in graded)}/{len(graded)} 已做 rubric)",
        f"rubric Score: {fmt(mean([float(item['score']) for item in graded]))}",
        f"rubric pass@1: {fmt(mean([1.0 if item.get('accepted') else 0.0 for item in graded]), 4)}",
        note,
        "",
    ]
    SUMMARY.write_text("\n".join(lines), encoding="utf-8")


def rubric_yaml(case_id: str) -> Path | None:
    for directory in RUBRIC_DIRS:
        path = directory / f"{case_id}.yaml"
        if path.is_file():
            return path
    return None


def fetch_step(case_id: str) -> Path | None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    local = ARTIFACTS / f"{case_id}_r1.step"
    if local.is_file() and local.stat().st_size > 0:
        return local
    remote = f"{WIN_ARTIFACTS}/{case_id}_r1.step"
    proc = subprocess.run(
        [
            "scp", "-P", "2225",
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
            "-o", "StrictHostKeyChecking=accept-new",
            f"38409@127.0.0.1:/{remote}",
            str(local),
        ],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 or not local.is_file() or local.stat().st_size == 0:
        local.unlink(missing_ok=True)
        return None
    return local


def score_one(case_id: str, rubrics: dict[str, dict]) -> str:
    if case_id in rubrics and "score" in rubrics[case_id]:
        return ""
    yaml_path = rubric_yaml(case_id)
    if yaml_path is None:
        rubrics[case_id] = {"case_id": case_id, "error": "no rubric yaml"}
        save_rubrics(rubrics)
        return f"no rubric yaml for {case_id}"
    step = fetch_step(case_id)
    if step is None:
        return f"STEP not copied yet: {case_id}"
    out = step.with_suffix(".rubric.json")
    proc = subprocess.run(
        [str(OCC), "-X", "utf8", "-m", "geomcheck.rubric",
         "--step", str(step), "--rubric", str(yaml_path), "--output", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=1800,
    )
    if proc.returncode != 0:
        rubrics[case_id] = {"case_id": case_id, "error": (proc.stderr or proc.stdout)[-300:]}
        save_rubrics(rubrics)
        return f"rubric failed: {case_id}"
    row = json.loads(out.read_text(encoding="utf-8"))[0]
    rubrics[case_id] = {
        "case_id": case_id,
        "score": row["score"],
        "accepted": row["accepted"],
        "hard_ok": row["hard_ok"],
        "failed": [item["id"] for item in row["rubrics"] if not item["ok"]],
    }
    save_rubrics(rubrics)
    return ""


def main() -> None:
    note = ""
    while True:
        data = load_result()
        rubrics = load_rubrics()
        results = (data or {}).get("results") or []
        pending = [item["case_id"] for item in results if "score" not in rubrics.get(item["case_id"], {})]
        if pending and OCC.is_file():
            try:
                note = score_one(pending[0], rubrics)
            except Exception as exc:
                note = f"{type(exc).__name__}: {exc}"
        elif not OCC.is_file():
            note = "venv-occ python missing; core_form not scored"
        write_summary(data, rubrics, note)
        if data and len(results) >= 100 and not pending:
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
