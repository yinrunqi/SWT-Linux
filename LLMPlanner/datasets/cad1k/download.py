"""下载 markov-ai/cad-1000-hours 的 SolidWorks 子集（跳过 clip.mp4 与 frame_events.json）。

数据集未声明 license，下载内容只放在 raw/（已 gitignore），不得再分发。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\download.py            # 全部 114 个任务
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\download.py --limit 5
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\download.py --with-events  # 额外下载 frame_events.json（每个约 75MB）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

REPO = "markov-ai/cad-1000-hours"
SUBSET = "solidworks"
RAW_DIR = Path(__file__).resolve().parent / "raw"
DEFAULT_SKIP = {"clip.mp4", "frame_events.json"}


def api_tree(endpoint: str, path: str) -> list[dict]:
    url = f"{endpoint}/api/datasets/{REPO}/tree/main/{path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def list_task_files(endpoint: str, task_path: str) -> list[dict]:
    files: list[dict] = []
    for item in api_tree(endpoint, task_path):
        if item["type"] == "directory":
            files.extend(list_task_files(endpoint, item["path"]))
        else:
            files.append(item)
    return files


def download_file(endpoint: str, repo_path: str, dest: Path, size: int) -> str:
    if dest.exists() and dest.stat().st_size == size:
        return "cached"
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"{endpoint}/datasets/{REPO}/resolve/main/{repo_path}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        fh.write(chunk)
            tmp.replace(dest)
            return "downloaded"
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return "failed"


def download_task(endpoint: str, task_path: str, skip: set[str]) -> dict:
    task_id = task_path.rsplit("/", 1)[-1]
    task_dir = RAW_DIR / task_id
    stats = {"task_id": task_id, "files": 0, "bytes": 0, "cached": 0}
    for item in list_task_files(endpoint, task_path):
        rel = item["path"][len(task_path) + 1:]
        if Path(rel).name in skip:
            continue
        status = download_file(endpoint, item["path"], task_dir / rel, int(item.get("size") or 0))
        stats["files"] += 1
        stats["bytes"] += int(item.get("size") or 0)
        stats["cached"] += status == "cached"
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--endpoint", default="https://huggingface.co", help="可改为 https://hf-mirror.com")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--ids", nargs="*", default=None)
    parser.add_argument("--with-events", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    skip = set(DEFAULT_SKIP)
    if args.with_events:
        skip.discard("frame_events.json")

    tasks = [it["path"] for it in api_tree(args.endpoint, SUBSET) if it["type"] == "directory"]
    if args.ids:
        wanted = set(args.ids)
        tasks = [t for t in tasks if t.rsplit("/", 1)[-1] in wanted]
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"[cad1k] {len(tasks)} tasks -> {RAW_DIR}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_task, args.endpoint, t, skip): t for t in tasks}
        for n, fut in enumerate(as_completed(futures), 1):
            task = futures[fut]
            try:
                stats = fut.result()
                results.append(stats)
                print(f"  [{n}/{len(tasks)}] {stats['task_id']} files={stats['files']} "
                      f"{stats['bytes'] / 1e6:.1f}MB cached={stats['cached']}")
            except Exception as exc:  # noqa: BLE001
                failures.append({"task": task, "error": str(exc)})
                print(f"  [{n}/{len(tasks)}] FAIL {task}: {exc}")

    manifest = {
        "repo": REPO,
        "subset": SUBSET,
        "skipped": sorted(skip),
        "tasks": sorted(results, key=lambda s: s["task_id"]),
        "failures": failures,
    }
    (RAW_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(s["bytes"] for s in results) / 1e6
    print(f"[cad1k] done: ok={len(results)} fail={len(failures)} total={total:.1f}MB")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
