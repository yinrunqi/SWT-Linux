"""把 rescore_assemblies.py 的输出整理成旧口径 / 新口径并排的 Markdown 表。

    python eval/assembly_scratch/rescore_table.py eval/assembly_v2_rescore_new.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _fmt(value) -> str:
    return "—" if value is None else f"{float(value):.1f}"


def _mean(values) -> float | None:
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None


def main() -> None:
    rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["rows"]
    print("| case | workflow | rubric 旧 | rubric 新 | 完整度 | 结构成立 | 运动 旧 | 运动 新 | 运动 新(门控) |")
    print("|---|---|---|---|---|---|---|---|---|")
    cols = {k: [] for k in ("old_a", "new_a", "old_m", "new_m", "gated")}
    for r in sorted(rows, key=lambda r: (r["workflow"], r["case_id"])):
        rubric = r.get("rubric") or {}
        gated = None if r.get("motion_score") is None else (r["motion_score"] if r.get("structure_ok") else 0.0)
        # 旧口径 = 同一个装配体按旧公式重算；运动分无法重放的行退回原始记录（原始记录是旧公式）
        old_a = r.get("assembly_score_v1")
        old_m = r.get("motion_score_v1")
        if old_m is None and r.get("file_suppressed_mates"):
            old_m = r.get("old_motion_score")
        for key, value in zip(cols, (old_a, r.get("assembly_score"), old_m, r.get("motion_score"), gated)):
            cols[key].append(value)
        print(f"| {r['case_id']} | {r['workflow']} | {_fmt(old_a)} | {_fmt(r.get('assembly_score'))} | "
              f"{_fmt(None if rubric.get('completeness') is None else 100 * rubric['completeness'])} | "
              f"{'是' if r.get('structure_ok') else '否'} | {_fmt(old_m)} | {_fmt(r.get('motion_score'))} | {_fmt(gated)} |")
    print(f"| **均值** | | {' | '.join(_fmt(_mean(cols[k])) for k in ('old_a', 'new_a'))} | | | "
          f"{' | '.join(_fmt(_mean(cols[k])) for k in ('old_m', 'new_m', 'gated'))} |")
    both = [i for i, v in enumerate(cols["new_m"]) if v is not None and cols["old_m"][i] is not None]
    pick = lambda k: _mean(cols[k][i] for i in both)  # noqa: E731
    print(f"| **均值（运动新旧都有的 {len(both)} 行）** | | | | | | "
          f"{_fmt(pick('old_m'))} | {_fmt(pick('new_m'))} | {_fmt(pick('gated'))} |")


if __name__ == "__main__":
    main()
