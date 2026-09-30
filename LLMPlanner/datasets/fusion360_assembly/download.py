"""下载 Fusion 360 Gallery Assembly Dataset（a1.0.0），只保留每个装配的 assembly.json。

许可：仅限非商业研究，不得整体再分发（见数据集 LICENSE.md）。下载内容只放在 raw/（已 gitignore），
入库的只有派生统计。完整数据 11 个 7z 包共 18.8GB，每包约 750 个装配；解压时只取 JSON，
几何（smt / step / obj / png）不落盘，处理完的 7z 默认删除。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\fusion360_assembly\\download.py --archives 4
    venv\\Scripts\\python.exe -X utf8 datasets\\fusion360_assembly\\download.py            # 全部 11 包
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import py7zr
import requests

BASE = "https://fusion-360-gallery-dataset.s3-us-west-2.amazonaws.com/assembly/a1.0.0"
RAW_DIR = Path(__file__).resolve().parent / "raw"
KEEP_NAMES = {"assembly.json", "train_test.json", "LICENSE.md", "tags.json"}


def download_ranged(url: str, dest: Path, workers: int, chunk: int = 32 << 20) -> None:
    size = int(requests.head(url, timeout=60).headers["Content-Length"])
    if dest.exists() and dest.stat().st_size == size:
        return
    chunk_dir = dest.parent / f".{dest.name}.chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    ranges = [(i, s, min(s + chunk, size) - 1) for i, s in enumerate(range(0, size, chunk))]

    def fetch(i: int, s: int, e: int) -> None:
        out = chunk_dir / f"{i:05d}"
        if out.exists() and out.stat().st_size == e - s + 1:
            return
        for attempt in range(5):
            try:
                resp = requests.get(url, headers={"Range": f"bytes={s}-{e}"}, timeout=180)
                resp.raise_for_status()
                if len(resp.content) != e - s + 1:
                    raise requests.RequestException(f"short chunk {i}")
                out.write_bytes(resp.content)
                return
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(5 * (attempt + 1))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for fut in as_completed([pool.submit(fetch, *r) for r in ranges]):
            fut.result()
    with dest.open("wb") as fh:
        for i, _, _ in ranges:
            fh.write((chunk_dir / f"{i:05d}").read_bytes())
    for f in chunk_dir.iterdir():
        f.unlink()
    chunk_dir.rmdir()


def extract_json(archive: Path, out_dir: Path) -> int:
    with py7zr.SevenZipFile(archive, "r") as z:
        targets = [n for n in z.getnames() if Path(n).name in KEEP_NAMES]
        z.extract(path=out_dir, targets=targets)
    return len(targets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archives", type=int, nargs="*", default=list(range(11)))
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--keep-archives", action="store_true")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for k in args.archives:
        name = f"a1.0.0_{k:02d}.7z"
        done = RAW_DIR / f".{name}.done"
        if done.exists():
            print(f"[fusion] {name} cached", flush=True)
            continue
        archive = RAW_DIR / name
        t0 = time.time()
        download_ranged(f"{BASE}/{name}", archive, args.workers)
        t1 = time.time()
        n = extract_json(archive, RAW_DIR)
        done.write_text(str(n), encoding="utf-8")
        if not args.keep_archives:
            archive.unlink()
        print(f"[fusion] {name} download {t1 - t0:.0f}s extract {time.time() - t1:.0f}s json={n}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
