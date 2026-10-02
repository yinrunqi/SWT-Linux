"""Workflow-CAD-Bench 三视图 track 的评分：rubric 逐条几何检查（主分）+ 与参考实体的三视图投影相似度。

rubric 只检查图纸上标注或画出的内容，每条注明出自哪个视图的哪个标注；图像分把提交实体与参考实体
沿三个正交方向投影比较（见 geomcheck.views），对图纸上任何可见差异都敏感，作为辅助指标单独报告。

用法（在 LLMPlanner 目录、venv-occ 中）：
    python -m geomcheck.three_view --artifacts <STEP 目录> --output scores.json [--diff-dir diffs]
    python -m geomcheck.three_view --step part.step --case b01_l_bracket
    python -m geomcheck.bench --rubric-dir eval/rubrics_three_view summary scores.json --repeats 2

STEP 文件命名为 ``<case>_r<k>.step``；参考 STEP 与图纸路径写在 rubric 的 reference / image 字段。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .occ_part import Part
from .rubric import _print, evaluate, load_rubric
from .views import Reference, save_diff

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUBRIC_DIR = ROOT / "eval" / "rubrics_three_view"

_refs: dict[str, Reference] = {}


def reference(rubric: dict) -> Reference:
    case = rubric["case"]
    if case not in _refs:
        _refs[case] = Reference(Part.from_step(ROOT / rubric["reference"]).shape)
    return _refs[case]


def score_step(step: Path, rubric: dict, diff_dir: Path | None = None) -> dict:
    part = Part.from_step(step)
    res = evaluate(rubric, part)
    try:
        img = reference(rubric).score(part.shape)
    except ValueError as exc:
        img = {"image_score": 0.0, "error": str(exc)}
    if diff_dir is not None and "_masks" in img:
        diff_dir.mkdir(parents=True, exist_ok=True)
        save_diff(img, diff_dir / f"{step.stem}_diff.png")
    img.pop("_masks", None)
    return {"step": str(step), **res, "image": img}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step")
    ap.add_argument("--case", help="与 --step 同用：题目 id")
    ap.add_argument("--artifacts", help="目录，内含 <case>_r<k>.step")
    ap.add_argument("--rubric-dir", default=str(DEFAULT_RUBRIC_DIR))
    ap.add_argument("--diff-dir", help="输出每个 STEP 的三视图差异图")
    ap.add_argument("--output")
    args = ap.parse_args()
    rubric_dir = Path(args.rubric_dir)
    diff_dir = Path(args.diff_dir) if args.diff_dir else None
    if args.step:
        if not args.case:
            ap.error("--step 需要同时给出 --case")
        jobs = [(Path(args.step), args.case)]
    elif args.artifacts:
        jobs = [(s, re.sub(r"_r\d+$", "", s.stem)) for s in sorted(Path(args.artifacts).glob("*.step"))]
    else:
        ap.error("需要 --step 或 --artifacts")
    results = []
    for step, case in jobs:
        path = rubric_dir / f"{case}.yaml"
        if not path.exists():
            continue
        try:
            row = score_step(step, load_rubric(path), diff_dir)
        except ValueError as exc:
            print(f"{step.name}: {exc}")
            continue
        _print(row, step.name)
        print(f"    image_score={row['image']['image_score']} views={row['image'].get('views')}")
        results.append(row)
    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
