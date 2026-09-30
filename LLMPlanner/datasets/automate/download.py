"""下载 AutoMate（Jones et al., SIGGRAPH Asia 2021）的元数据，不含几何。

数据来自 Zenodo record 7776208，许可 CC0。几何（step.zip 13GB、parasolid.zip 20GB）
和逐装配 JSON（assemblies.zip 0.9GB）默认不下载。下载内容只放在 raw/（已 gitignore）。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\automate\\download.py
    venv\\Scripts\\python.exe -X utf8 datasets\\automate\\download.py --with-assemblies
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

RECORD = "7776208"
API = f"https://zenodo.org/api/records/{RECORD}"
RAW_DIR = Path(__file__).resolve().parent / "raw"
META_FILES = {"README.md", "mates.parquet", "assemblies.parquet", "parts.parquet", "config_encodings.json"}


def download(url: str, dest: Path, size: int) -> str:
    if dest.exists() and dest.stat().st_size == size:
        return "cached"
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            return "downloaded"
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return "failed"


def download_ranged(url: str, dest: Path, size: int, workers: int, chunk: int = 16 << 20) -> str:
    """按 Range 分块并行下载，分块可续传；已有的顺序下载 .part 前缀直接复用。"""
    if dest.exists() and dest.stat().st_size == size:
        return "cached"
    part = dest.with_suffix(dest.suffix + ".part")
    chunk_dir = dest.parent / f".{dest.name}.chunks"
    chunk_dir.mkdir(exist_ok=True)
    ranges = [(i, s, min(s + chunk, size) - 1) for i, s in enumerate(range(0, size, chunk))]
    if part.exists():
        prefix = part.stat().st_size
        with part.open("rb") as fh:
            for i, s, e in ranges:
                out = chunk_dir / f"{i:05d}"
                if e < prefix and not out.exists():
                    fh.seek(s)
                    out.write_bytes(fh.read(e - s + 1))

    def fetch(i: int, s: int, e: int) -> None:
        out = chunk_dir / f"{i:05d}"
        if out.exists() and out.stat().st_size == e - s + 1:
            return
        for attempt in range(5):
            try:
                resp = requests.get(url, headers={"Range": f"bytes={s}-{e}"}, timeout=120)
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
        futures = [pool.submit(fetch, *r) for r in ranges]
        for n, fut in enumerate(as_completed(futures), 1):
            fut.result()
            if n % 8 == 0 or n == len(ranges):
                print(f"    chunks {n}/{len(ranges)}", flush=True)
    with dest.open("wb") as fh:
        for i, _, _ in ranges:
            fh.write((chunk_dir / f"{i:05d}").read_bytes())
    for f in chunk_dir.iterdir():
        f.unlink()
    chunk_dir.rmdir()
    part.unlink(missing_ok=True)
    return "downloaded"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-assemblies", action="store_true", help="额外下载 assemblies.zip（0.9GB）")
    parser.add_argument("--workers", type=int, default=8, help="大文件按 Range 分块并行下载的连接数")
    args = parser.parse_args()

    record = requests.get(API, timeout=120).json()
    license_id = record["metadata"].get("license", {}).get("id")
    print(f"[automate] {record['metadata']['title']} license={license_id}")
    wanted = set(META_FILES) | ({"assemblies.zip"} if args.with_assemblies else set())

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for f in record["files"]:
        if f["key"] not in wanted:
            continue
        size = int(f["size"])
        if size > 64 << 20:
            status = download_ranged(f["links"]["self"], RAW_DIR / f["key"], size, args.workers)
        else:
            status = download(f["links"]["self"], RAW_DIR / f["key"], size)
        print(f"  {f['key']:<24} {int(f['size']) / 1e6:8.1f}MB {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
