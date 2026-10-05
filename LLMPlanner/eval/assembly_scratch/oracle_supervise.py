"""oracle_replay 的守护进程：逐例起子进程，崩溃 / 卡死 / 服务不通时重启 SolidWorks 并重跑该例；
进度写 progress.json，被打断后重新运行会跳过已完成的测例。

  python eval/assembly_scratch/oracle_supervise.py name [name ...]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.sw_restart import SW, restart_solidworks  # noqa: E402

OUT = ROOT / "eval" / "artifacts" / "bench2_oracle"
PROGRESS = OUT / "progress.json"
LOG = OUT / "run.log"
CASE_TIMEOUT_S = 90 * 60
ATTEMPTS = 3


def _alive() -> bool:
    try:
        return requests.get(f"{SW}/api/ping", timeout=10).ok
    except requests.RequestException:
        return False


def _say(text: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    names = sys.argv[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    progress = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.is_file() else {}
    for name in names:
        if progress.get(name, {}).get("done"):
            continue
        for attempt in range(1, ATTEMPTS + 1):
            if not _alive():
                _say(f"SUPERVISOR {name}: 服务不通，重启 SolidWorks")
                restart_solidworks()
            result = OUT / name / "result.json"
            started = time.time()
            _say(f"SUPERVISOR {name}: 第 {attempt} 次")
            with LOG.open("a", encoding="utf-8") as log:
                proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(Path(__file__).with_name("oracle_replay.py")),
                                         name], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
                try:
                    proc.wait(timeout=CASE_TIMEOUT_S)
                    why = None if proc.returncode == 0 else f"退出码 {proc.returncode}"
                except subprocess.TimeoutExpired:
                    proc.kill()
                    why = f"超过 {CASE_TIMEOUT_S // 60} 分钟"
            fresh = result.is_file() and result.stat().st_mtime >= started
            row = json.loads(result.read_text(encoding="utf-8")) if fresh else None
            if row and not why and not (row.get("failed_call") or {}).get("error", {}).get("code") == "SW_UNAVAILABLE":
                progress[name] = {"done": True, "attempts": attempt, "rubric": row.get("rubric_score"),
                                  "motion": row.get("motion_score"), "failed_call": row.get("failed_call"),
                                  "seconds": row.get("seconds")}
                PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                _say(f"SUPERVISOR {name}: 完成 rubric={row.get('rubric_score')} motion={row.get('motion_score')}")
                break
            _say(f"SUPERVISOR {name}: 不正常（{why or '没有新结果或 SolidWorks 不可用'}），重启后重跑")
            restart_solidworks()
        else:
            progress[name] = {"done": True, "gave_up": True, "attempts": ATTEMPTS}
            PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            _say(f"SUPERVISOR {name}: GIVEUP，{ATTEMPTS} 次都不正常")
    _say("SUPERVISOR ALL DONE")


if __name__ == "__main__":
    main()
