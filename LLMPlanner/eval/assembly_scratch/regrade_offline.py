"""离线补算装配评测里依赖 OCC 的部分：零件接口 rubric、齿隙，以及由它们决定的运动分与 e2e。

用于建模机器上 OCC 不可用（例如 Windows 智能应用控制拦截 OCP 的 DLL）的情况：
装配 rubric 与其余运动检查在建模时已经算好，这里只读拷过来的零件 STEP 重新打分，不需要 SolidWorks。

    venv-occ/bin/python -X utf8 eval/assembly_scratch/regrade_offline.py \\
        eval/assembly_gen_xxx.json --artifacts eval/artifacts/assembly_gen_xxx --output eval/assembly_gen_xxx_regraded.json

只依赖 geomcheck（OCP、numpy）和 PyYAML，可以直接用检查器环境 venv-occ 运行。
e2e 条件下因零件不合格而跳过装配的行无法离线补出装配分，会列在 skipped_assembly 里。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUBRIC_DIR = ROOT / "eval" / "assembly_scratch" / "rubrics"


def _grade(step: Path, rubric_path: Path) -> dict:
    from geomcheck.occ_part import Part
    from geomcheck.rubric import evaluate, load_rubric

    try:
        return evaluate(load_rubric(rubric_path), Part.from_step(step))
    except Exception as exc:
        return {"accepted": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}


def _rate(parts: list[dict]) -> float:
    return sum(1 for item in parts if item.get("accepted")) / len(parts) if parts else 0.0


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return {
        "n": len(rows),
        "part_pass_rate": round(sum(r["part_pass_rate"] for r in rows) / len(rows), 3),
        "assembly_score": round(sum(r["assembly_score"] or 0 for r in rows) / len(rows), 2),
        "motion_score": round(sum(r["motion_score"] or 0 for r in rows) / len(rows), 2),
        "e2e": round(sum(1 for r in rows if r["e2e"]) / len(rows), 3),
    }


def _local_name(path: str) -> str:
    """结果里是建模机器上的绝对路径（通常是 Windows 路径），这里只取文件名。"""
    return PureWindowsPath(path).name if "\\" in path else Path(path).name


def regrade_row(row: dict, artifacts: Path) -> dict:
    from geomcheck.assembly_motion import clearance_mm

    parts_dir = artifacts / row["workflow"] / row["case_id"] / f"{row['condition']}_r{row['repeat']}" / "parts"
    log: dict = {"parts": [], "clearance": None}
    for part in row.get("parts") or []:
        step = parts_dir / f"{part['part_id']}.step"
        before = part.get("accepted")
        if step.is_file():
            grade = _grade(step, RUBRIC_DIR / f"{part['part_id']}.yaml")
        else:
            grade = {"accepted": False, "score": 0.0, "error": f"STEP 不存在: {step}"}
        part.update({"accepted": bool(grade.get("accepted")), "score": grade.get("score"), "grade": grade})
        log["parts"].append({"part_id": part["part_id"], "before": before, "after": part["accepted"]})
    if row.get("parts") is not None and row["condition"] != "gold":
        row["part_pass_rate"] = _rate(row["parts"])

    motion = (row.get("assembly") or {}).get("motion") or {}
    pre = motion.get("pre_clearance")
    if motion.get("clearance") and pre:
        gaps = []
        for item in motion["clearance"]:
            steps = [parts_dir / _local_name(p) for p in item["steps"]]
            gap = clearance_mm(steps[0], steps[1], *item["transforms"]) if all(p.is_file() for p in steps) else None
            lo, hi = item.get("range_mm") or [0.02, 2.0]
            item.update({"mm": None if gap is None else round(gap, 4), "ok": gap is not None and lo <= gap <= hi})
            gaps.append(item)
        all_ok = all(item["ok"] for item in gaps)
        motion["score"] = pre["score"] if all_ok else max(0.0, pre["score"] - 10)
        motion["passed"] = bool(pre["passed"]) and all_ok
        row["motion_score"] = float(motion["score"])
        log["clearance"] = [{"pair": item["pair"], "mm": item["mm"], "ok": item["ok"]} for item in gaps]
    elif motion.get("clearance"):
        log["clearance"] = "缺少 pre_clearance/transforms（旧版结果），无法补算齿隙"

    if row.get("assembly") is not None:
        row["e2e"] = row["part_pass_rate"] == 1.0 and bool(motion.get("passed"))
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("result", type=Path, help="eval_assembly_scratch.py 的结果 JSON")
    parser.add_argument("--artifacts", type=Path, required=True, help="拷过来的 --output-dir 目录")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = json.loads(args.result.read_text(encoding="utf-8"))
    rows = report["results"]
    logs, skipped = [], []
    for row in rows:
        if row["condition"] == "e2e" and row.get("assembly") is None:
            skipped.append(row["case_id"])
        log = regrade_row(row, args.artifacts)
        logs.append({"case_id": row["case_id"], "condition": row["condition"], "repeat": row["repeat"], **log})
        print(
            f"{row['case_id']:28s} parts={row['part_pass_rate']:.3f} asm={row['assembly_score']} "
            f"motion={row['motion_score']} e2e={row['e2e']}"
            + (f" clearance={log['clearance']}" if log["clearance"] else ""),
            flush=True,
        )

    workflows = sorted({r["workflow"] for r in rows})
    report["summary"] = _summarize(rows)
    report["by_workflow"] = {
        wf: {
            "all": _summarize([r for r in rows if r["workflow"] == wf]),
            **{cid: _summarize([r for r in rows if r["workflow"] == wf and r["case_id"] == cid])
               for cid in sorted({r["case_id"] for r in rows})},
        }
        for wf in workflows
    }
    report["regraded_offline"] = {"source": str(args.result), "artifacts": str(args.artifacts),
                                  "rows": logs, "skipped_assembly": skipped}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
