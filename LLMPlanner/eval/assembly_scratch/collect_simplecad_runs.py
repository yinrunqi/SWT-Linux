"""把已跑完的测例（有 run.json 的运行目录）汇总成 score_simplecad.py 能读的结果 JSON，供边跑边打分。

用法（在 LLMPlanner 目录）：
    python eval/assembly_scratch/collect_simplecad_runs.py eval/artifacts/assembly_simplecad_sonnet5 \\
        eval/assembly_simplecad_sonnet5.partial.json
打印已完成的 case_id，逗号分隔，便于同步。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    art, out = Path(sys.argv[1]), Path(sys.argv[2])
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(art.glob("*/r*/run.json"))]
    model = next((r.get("model") for r in runs if r.get("model")), None)
    rel = (art.resolve()).relative_to(ROOT).as_posix()
    out.write_text(json.dumps({"model": model, "artifacts": rel, "runs": runs}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(",".join(r["case_id"] for r in runs))


if __name__ == "__main__":
    main()
