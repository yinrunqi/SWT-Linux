"""汇总 v1/v2 前后对比：合并若干份 eval_assembly_scratch.py 输出，按 (案例, workflow) 列出每次运行。

    python eval/assembly_scratch/summarize_workflow_compare.py OUT.json IN1.json [IN2.json ...]

评测器自身的 COM 故障（RPC_E_*）记为 infra_error，不计入该 workflow 的平均分，由重跑结果补上。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def row(result: dict) -> dict:
    assembly = result["assembly"]
    motion = assembly.get("motion") or {}
    kin = motion.get("kinematics") or {}
    error = str(motion.get("error") or "")
    tools = [t.get("tool") for t in result.get("tool_calls") or []]
    return {
        "case": result["case_id"],
        "workflow": result["workflow"],
        "repeat": result["repeat"],
        "status": assembly.get("status"),
        "llm_calls": assembly.get("llm_calls"),
        "tool_calls": len(tools),
        "failed_tool_calls": sum(1 for t in result.get("tool_calls") or [] if not t.get("ok")),
        "saved": (assembly.get("static") or {}).get("saved"),
        "static_score": assembly.get("static_score"),
        "motion_score": motion.get("score"),
        "motion_passed": motion.get("passed"),
        "interference_frames": len(motion.get("interference_frames") or []),
        "stuck": motion.get("stuck"),
        "output_ratio": kin.get("output_ratio"),
        "expected_ratio": kin.get("expected_ratio"),
        "stroke_mm": kin.get("stroke_mm"),
        "expected_stroke_mm": kin.get("expected_stroke_mm"),
        "motion_error": error[:120] or None,
        "infra_error": "RPC_E_" in error,
        "v2_tools_used": sorted({t for t in tools if t in {
            "joint", "close_loop", "rack_pinion", "screw_pair", "linear_coupler", "check_dof"}}),
    }


def main() -> None:
    out, inputs = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    rows = []
    for path in inputs:
        rows += [row(r) for r in json.loads(path.read_text(encoding="utf-8"))["results"]]
    groups: dict[tuple, list] = defaultdict(list)
    for item in rows:
        if not item["infra_error"]:
            groups[(item["case"], item["workflow"])].append(item)
    summary = {}
    for (case, workflow), items in sorted(groups.items()):
        n = len(items)
        summary[f"{case}/{workflow}"] = {
            "runs": n,
            "motion_pass": sum(bool(i["motion_passed"]) for i in items),
            "mean_motion_score": round(sum(float(i["motion_score"] or 0) for i in items) / n, 2),
            "mean_static_score": round(sum(float(i["static_score"] or 0) for i in items) / n, 2),
            "saved": sum(bool(i["saved"]) for i in items),
            "hit_call_budget": sum(i["status"] == "max_llm_calls" for i in items),
        }
    payload = {"summary": summary, "runs": rows, "inputs": [p.name for p in inputs]}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
