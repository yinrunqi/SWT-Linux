"""Workflow-CAD-Bench text-to-CAD 部分的题目导出与结果汇总，与建模系统无关。

用法（在 LLMPlanner 目录、venv-occ 中）：
    python -m geomcheck.bench tasks --output tasks.jsonl
    python -m geomcheck.rubric --artifacts <STEP 目录> --output scores.json
    python -m geomcheck.bench summary scores.json --repeats 2

STEP 文件命名为 ``<case>_r<k>.step``（k 从 1 开始）；缺失的运行记 0 分、不合格。
"""

from __future__ import annotations

import argparse
import json
import random
from math import comb
from pathlib import Path

import yaml

KINDS = {
    "Everyday objects": "phone_stand stool rounded_stool storage_box standard_die mug vase key soap_dish",
    "Common machined parts": "hex_nut wrench gear_disc flange pulley stepped_shaft socket_bolt l_bracket "
                             "spacer_ring mounting_plate stepped_block knob hex_standoff u_channel "
                             "flanged_bushing wedge_block spoked_wheel end_cap link_bar",
    "Multi-feature parts": "fork_end three_lug_housing pillow_block twin_arm_rocker clevis_bracket "
                           "diamond_flange support_seat crank",
    "Operation-specific parts": "gusset_bracket corner_rib_stand open_box lidless_housing counterbore_plate "
                                "csk_cover side_pad through_boss sheet_angle sheet_channel flanged_elbow "
                                "bent_tube square_to_round rect_to_circle",
}
ORDER = [c for v in KINDS.values() for c in v.split()]
KIND_OF = {c: k for k, v in KINDS.items() for c in v.split()}


def load_tasks(rubric_dir: Path) -> list[dict]:
    tasks = {}
    for p in rubric_dir.glob("*.yaml"):
        r = yaml.safe_load(p.read_text(encoding="utf-8"))
        tasks[r["case"]] = r
    return [
        {"case": c, "kind": KIND_OF.get(c, ""), "prompt": tasks[c]["prompt"]}
        for c in sorted(tasks, key=lambda c: (ORDER.index(c) if c in ORDER else len(ORDER), c))
    ]


def _boot(xs: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rnd = random.Random(seed)
    ms = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n) - 1]


def _pass_at_k(n: int, c: int, k: int) -> float:
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def _stats(runs: dict[str, list[dict]], repeats: int) -> dict:
    flat = [r for v in runs.values() for r in v]
    scores = [r["score"] for r in flat]
    lo, hi = _boot(scores)
    out = {
        "tasks": len(runs), "runs": len(flat),
        "score": round(sum(scores) / len(flat), 1), "ci95": [round(lo, 1), round(hi, 1)],
        "accepted": sum(r["accepted"] for r in flat),
        "core_form": round(sum(r["hard_ok"] for r in flat) / len(flat), 3),
    }
    for k in range(1, repeats + 1):
        out[f"pass@{k}"] = round(
            sum(_pass_at_k(len(v), sum(r["accepted"] for r in v), k) for v in runs.values()) / len(runs), 3)
    return out


def summarize(score_files: list[Path], rubric_dir: Path, repeats: int) -> dict:
    graded = {}
    for f in score_files:
        for x in json.loads(f.read_text(encoding="utf-8")):
            graded[Path(x["step"]).stem] = x
    runs = {}
    for t in load_tasks(rubric_dir):
        c = t["case"]
        runs[c] = []
        for k in range(1, repeats + 1):
            g = graded.get(f"{c}_r{k}")
            runs[c].append({"score": g["score"] if g else 0.0, "accepted": bool(g and g["accepted"]),
                            "hard_ok": bool(g and g["hard_ok"]), "missing": g is None})
    report = {"all": _stats(runs, repeats), "by_kind": {}, "per_task": {}}
    for kind in KINDS:
        sub = {c: v for c, v in runs.items() if KIND_OF.get(c) == kind}
        if sub:
            report["by_kind"][kind] = _stats(sub, repeats)
    for c, v in runs.items():
        report["per_task"][c] = {"scores": [r["score"] for r in v], "accepted": sum(r["accepted"] for r in v),
                                 "missing": sum(r["missing"] for r in v)}
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rubric-dir", default="eval/rubrics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tasks", help="导出题目 (case, kind, prompt) 为 JSONL")
    t.add_argument("--output", default="tasks.jsonl")
    s = sub.add_parser("summary", help="汇总 geomcheck.rubric 的打分结果")
    s.add_argument("scores", nargs="+", type=Path)
    s.add_argument("--repeats", type=int, default=2)
    s.add_argument("--output")
    args = ap.parse_args()
    rubric_dir = Path(args.rubric_dir)
    if args.cmd == "tasks":
        tasks = load_tasks(rubric_dir)
        Path(args.output).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in tasks),
                                     encoding="utf-8")
        print(f"{len(tasks)} tasks -> {args.output}")
        return
    report = summarize(args.scores, rubric_dir, args.repeats)
    a = report["all"]
    print(f"ALL  tasks={a['tasks']} runs={a['runs']} score={a['score']} ci95={a['ci95']} "
          + " ".join(f"{k}={a[k]}" for k in a if k.startswith("pass@")) + f" core_form={a['core_form']}")
    for kind, b in report["by_kind"].items():
        print(f"  {kind:<26} tasks={b['tasks']:>2} score={b['score']:>5} pass@1={b['pass@1']}")
    missing = sum(v["missing"] for v in report["per_task"].values())
    if missing:
        print(f"  missing STEP files scored 0: {missing}")
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
