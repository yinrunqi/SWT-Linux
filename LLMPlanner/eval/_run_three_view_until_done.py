"""Wait for self-served Qwen, then resume three-view eval until all cases finish.

Loops: poll /models -> run eval_three_view_bench.py --resume -> if any llm_error /
sw_lost / resume_failed remain, wait again. Does not score (offline geomcheck is separate).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from load_env import ensure_test_env_loaded

ensure_test_env_loaded()

BASE = os.environ["QWENLOCAL_BASE_URL"].rstrip("/")
KEY = os.environ.get("QWENLOCAL_API_KEY", "")
MODEL = "Qwen3.8-Flash-Next-FP8"
NAME = "three_view_l1v2_qwen38_full_20261008"
OUT = ROOT / "eval" / "artifacts" / NAME
RUNS = OUT / "runs.json"
RERUN = {"llm_error", "sw_lost", "resume_failed"}
POLL_SEC = 20
COOLDOWN_SEC = 60


def qwen_ready() -> tuple[bool, str]:
    try:
        response = requests.get(
            f"{BASE}/models",
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=20,
        )
        ids = [item.get("id") for item in (response.json().get("data") or [])] if response.ok else []
        ok = response.ok and MODEL in ids
        return ok, f"status={response.status_code} has_model={MODEL in ids} n={len(ids)}"
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def wait_qwen() -> None:
    attempt = 0
    while True:
        attempt += 1
        ok, detail = qwen_ready()
        print(f"wait attempt={attempt} {detail}", flush=True)
        if ok:
            print("Qwen_ready", flush=True)
            return
        time.sleep(POLL_SEC)


def unfinished_count() -> int | None:
    if not RUNS.is_file():
        return None
    rows = json.loads(RUNS.read_text(encoding="utf-8"))
    bad = sum(1 for row in rows if row.get("status") in RERUN)
    print(
        f"runs n={len(rows)} finished={sum(1 for r in rows if r.get('status') == 'finished')} "
        f"rerun={bad}",
        flush=True,
    )
    return bad


def run_eval() -> int:
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "-u",
        str(ROOT / "eval" / "eval_three_view_bench.py"),
        "--action-level",
        "L1_v2",
        "--provider",
        "qwenlocal",
        "--plan-model",
        MODEL,
        "--reasoning-effort",
        "medium",
        "--max-llm-calls",
        "40",
        "--repeats",
        "1",
        "--name",
        NAME,
        "--resume",
        "--sw-restarts",
        "3",
        "--sw-max-private-gb",
        "12",
    ]
    print("starting eval:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(ROOT))


def main() -> int:
    cycle = 0
    while True:
        cycle += 1
        print(f"=== cycle {cycle}: wait for Qwen ===", flush=True)
        wait_qwen()
        # Brief settle in case the server just came up.
        time.sleep(5)
        code = run_eval()
        print(f"eval exit={code}", flush=True)
        bad = unfinished_count()
        if bad == 0 and RUNS.is_file():
            # Still may be missing cases; a clean exit with only finished rows means
            # the harness walked the full task list. Verify via a dry recount after load.
            from eval_three_view_bench import load_tasks

            tasks = load_tasks("en", None)
            rows = json.loads(RUNS.read_text(encoding="utf-8"))
            done = {
                (row["case"], row["repeat"])
                for row in rows
                if row.get("status") not in RERUN and not row.get("error")
            }
            expected = {(t["case"], 1) for t in tasks}
            missing = sorted(expected - done)
            print(f"expected={len(expected)} finished={len(done)} missing={len(missing)}", flush=True)
            if not missing:
                print("ALL_FINISHED", flush=True)
                return 0
            print("missing cases remain; will retry after Qwen check", flush=True)
        print(f"cooldown {COOLDOWN_SEC}s before next cycle", flush=True)
        time.sleep(COOLDOWN_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
