"""汇总 SimpleCADAPI 装配基线每个测例的调用次数、token、耗时与速度（只读产物，不调 LLM）。

用法（在 LLMPlanner 目录）：
    python eval/assembly_scratch/simplecad_run_stats.py eval/artifacts/assembly_simplecad_sonnet5 \\
        --cases two_stage_gearbox,hand_winch --out eval/assembly_simplecad_sonnet5_asm100_stats.json
--archive asm50_exhausted 同时统计 r1/<archive>/ 下的旧一轮装配，便于对比。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _usage(u: dict | None) -> dict:
    u = u or {}
    return {k: u.get(k, 0) or 0 for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "credit")}


def stage_stats(rec: dict, responses: list | None) -> dict:
    u = _usage(rec.get("token_usage"))
    calls, elapsed = rec.get("llm_calls") or 0, rec.get("elapsed_s") or 0.0
    out = {
        "status": rec.get("status"), "llm_calls": calls, "tool_counts": rec.get("tool_counts"),
        **u, "credit": round(u["credit"], 2), "elapsed_s": elapsed,
        "s_per_call": round(elapsed / calls, 1) if calls else None,
        "completion_tok_per_s": round(u["completion_tokens"] / elapsed, 1) if elapsed else None,
        "cache_hit_rate": round(u["cached_tokens"] / u["prompt_tokens"], 3) if u["prompt_tokens"] else None,
    }
    if responses:
        comp = [(r.get("usage") or {}).get("completion_tokens") or 0 for r in responses]
        out["max_completion_tokens_per_call"] = max(comp)
        out["calls_over_8k_completion"] = sum(1 for c in comp if c > 8000)
        out["finish_length"] = sum(1 for r in responses if (r.get("choices") or [{}])[0].get("finish_reason") == "length")
    return out


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def case_stats(run_dir: Path, archive: str | None) -> dict:
    run = _load(run_dir / "run.json")
    if run is None:
        return {"case_id": run_dir.parent.name, "pending": True}
    parts = run.get("parts") or []
    pu = [_usage(p.get("token_usage")) for p in parts]
    asm = run.get("assembly") or {}
    row = {
        "case_id": run["case_id"],
        "parts": {
            "n": len(parts), "accepted": sum(1 for p in parts if p.get("accepted")),
            "llm_calls": sum(p.get("llm_calls") or 0 for p in parts),
            **{k: round(sum(u[k] for u in pu), 2) for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "credit")},
            "elapsed_s_sum": round(sum(p.get("elapsed_s") or 0 for p in parts), 1),
        },
        "assembly": {**stage_stats(asm, _load(run_dir / "assembly" / "llm_responses.json")),
                     "delivered": asm.get("delivered"), "export_ok": asm.get("export_ok")},
    }
    if archive and (run_dir / archive / "run.json").is_file():
        old = (_load(run_dir / archive / "run.json") or {}).get("assembly") or {}
        row["assembly_" + archive] = stage_stats(old, _load(run_dir / archive / "assembly" / "llm_responses.json"))
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifacts")
    ap.add_argument("--cases", default="", help="case_id，逗号分隔；默认全部")
    ap.add_argument("--archive", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    art = Path(args.artifacts)
    wanted = [c for c in args.cases.split(",") if c] or sorted(p.name for p in art.iterdir() if (p / "r1").is_dir())
    rows = [case_stats(art / c / "r1", args.archive) for c in wanted]
    done = [r for r in rows if not r.get("pending")]
    a = [r["assembly"] for r in done]
    total = {
        "cases_done": len(done), "cases_pending": len(rows) - len(done),
        "asm_llm_calls": sum(x["llm_calls"] for x in a),
        "asm_prompt_tokens": sum(x["prompt_tokens"] for x in a),
        "asm_completion_tokens": sum(x["completion_tokens"] for x in a),
        "asm_credit": round(sum(x["credit"] for x in a), 2),
        "asm_elapsed_s": round(sum(x["elapsed_s"] for x in a), 1),
    }
    if a:
        total["asm_s_per_call"] = round(total["asm_elapsed_s"] / max(1, total["asm_llm_calls"]), 1)
        total["asm_completion_tok_per_s"] = round(total["asm_completion_tokens"] / max(1, total["asm_elapsed_s"]), 1)
    result = {"total": total, "rows": rows}
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(total, ensure_ascii=False))
    for r in done:
        x = r["assembly"]
        print(f"{r['case_id']:26} {x['status']:16} calls={x['llm_calls']:<3} prompt={x['prompt_tokens']:>9} "
              f"compl={x['completion_tokens']:>7} credit={x['credit']:>7} {x['elapsed_s']:>7}s "
              f"{x['s_per_call']}s/call {x['completion_tok_per_s']}tok/s delivered={x['delivered']}")


if __name__ == "__main__":
    main()
