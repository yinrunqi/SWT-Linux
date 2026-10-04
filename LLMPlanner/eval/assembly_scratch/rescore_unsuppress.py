"""旧版评测器在运动评测之后才保存装配体，运动评测中 SolidWorks 压缩了部分配合，存下的文件因此与智能体
结束时的状态不同（当时评分没有压缩配合）。对这类行，用重评时存下的 rubric_inputs.json，把被压缩的配合
当作有效配合离线重算 rubric；运动分无法离线重放，记为 None 并标出 file_suppressed_mates。

    python eval/assembly_scratch/rescore_unsuppress.py eval/assembly_v2_rescore_new.json

只处理“现在有压缩配合、当初评分时没有”的行；当初评分记录在结果 JSON 的 assembly.rubric 里。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.eval_assembly_scratch import load_assembly_rubric, structure_ok  # noqa: E402
from geomcheck.assembly_motion import analyze_poses, make_check, score_checks  # noqa: E402
from geomcheck.assembly_rubric import evaluate  # noqa: E402


def motion_from_record(motion: dict, spec: dict) -> float | None:
    """按新口径从已记录的运动结果重算分数（不含齿侧间隙项，与 rescore_assemblies 一致）。"""
    if motion.get("mode") == "poses":
        return analyze_poses(motion.get("poses") or [], spec)["score"]
    kin = motion.get("kinematics")
    if not kin or not kin.get("frames"):
        return None
    moving = spec.get("expect_moving") or []
    moving_frac = 1.0 - len(motion.get("stuck") or []) / len(moving) if moving else 1.0
    n_frames = max(3, int(spec.get("frames") or 13))  # 干涉只在稀疏帧上测，见 sample_motion
    checks = [make_check("interference", (1.0 - len(motion.get("interference_frames") or []) / n_frames) * moving_frac)]
    if moving:
        checks.append(make_check("moving", moving_frac))
    if spec.get("expect_fixed"):
        checks.append(make_check("fixed", (1.0 - len(motion.get("drifted_fixed") or []) / len(spec["expect_fixed"]))
                                 * moving_frac))
    items = [bool(c["ok"]) for key in ("ratios", "leads", "strokes") for c in kin.get(key) or []]
    if "output_ratio" in kin:
        ok = abs(kin["output_ratio"] - kin["expected_ratio"]) <= 0.08
        items.append(ok and kin.get("same_direction", True) if spec.get("expect_same_direction") else ok)
    if "timing_ratio_measured" in kin:
        ok = abs(kin["timing_ratio_measured"] - kin["timing_ratio_expected"]) <= 0.08
        if "timing_opposite" in kin:
            ok = ok and kin["timing_opposite"] == bool(spec.get("timing_opposite"))
        items.append(ok)
    if "stroke_mm" in kin:
        items.append(abs(kin["stroke_mm"] - float(kin["expected_stroke_mm"])) <= 2.0)
    if items:
        checks.append(make_check("kinematics", sum(items) / len(items)))
    return score_checks(checks)


def source_row(row: dict) -> dict | None:
    source = Path(row["source"])
    data = json.loads((source if source.is_absolute() else ROOT / source).read_text(encoding="utf-8"))
    for r in data["results"]:
        if r["case_id"] == row["case_id"] and r["workflow"] == row["workflow"] and r["repeat"] == row["repeat"]:
            return r
    return None


def live_suppressed(row: dict) -> int | None:
    r = source_row(row)
    if r is None:
        return None
    return len(((r.get("assembly") or {}).get("rubric") or {}).get("suppressed_mates") or [])


def main() -> None:
    path = Path(sys.argv[1])
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    for row in rows:
        now = (row.get("rubric") or {}).get("suppressed_mates") or []
        if not now or row.get("file_suppressed_mates") or live_suppressed(row) != 0:
            continue
        inputs = ROOT / Path(row["assembly"]).with_name("rubric_inputs.json")
        data = json.loads(inputs.read_text(encoding="utf-8"))
        mates = [{**m, "suppressed": False} for m in data["mates"]]
        rubric = evaluate(data["components"], mates, data["aliases"], load_assembly_rubric(row["case_id"]))
        row.update({
            "file_suppressed_mates": now,
            "assembly_score_file": row.get("assembly_score"),
            "assembly_score": rubric["score"],
            "assembly_score_v1": rubric["score_v1"],
            "structure_ok": structure_ok(rubric),
            "rubric": rubric,
            "motion_score_file": row.get("motion_score"),
            "motion_score": None,
            "motion_score_v1": None,
            "note": "保存的文件里有旧评测压缩的配合：rubric 按解除压缩离线重算，运动分无法重放",
        })
        print(row["case_id"], "压缩", len(now), "→ rubric", rubric["score"], "v1", rubric["score_v1"],
              "旧", row.get("old_assembly_score"))
    from eval.assembly_scratch.rescore_assemblies import CASE_FILES
    from eval.eval_assembly_scratch import _load_case
    for row in rows:
        if not row.get("file_suppressed_mates") or row.get("motion_score") is not None:
            continue
        original = ((source_row(row) or {}).get("assembly") or {}).get("motion") or {}
        score = motion_from_record(original, _load_case(CASE_FILES[row["case_id"]])["motion"])
        if score is None:
            continue
        row.update({"motion_score": score, "motion_score_v1": original.get("score"),
                    "motion_source": "原始运行的运动记录按新口径离线重算"})
        print(row["case_id"], "运动分（原始记录离线重算）", score, "旧", original.get("score"))
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
