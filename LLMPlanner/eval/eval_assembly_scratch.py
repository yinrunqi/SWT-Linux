"""从零装配试点：标准零件 / 生成零件 / 端到端，输出零件合格率、装配、运动、端到端四项指标。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from load_env import ensure_test_env_loaded

ensure_test_env_loaded()

CASES_DIR = ROOT / "eval" / "assembly_scratch"
RUBRIC_DIR = CASES_DIR / "rubrics"
RECIPES = ROOT / "planner" / "skills" / "recipes"
MODEL = "Qwen3.5-397B-A17B"


def _load_case(name: str) -> dict:
    path = CASES_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _recipes() -> str:
    chunks = []
    for fname in (
        "assembly_ground_dof.md",
        "assembly_gear_train.md",
        "assembly_crank_slider.md",
        "assembly_motion_check.md",
    ):
        chunks.append((RECIPES / fname).read_text(encoding="utf-8"))
    return "\n\n".join(chunks)


def _close(sw: str) -> None:
    try:
        requests.post(f"{sw}/api/close-all-documents", timeout=60)
    except Exception:
        pass


def _openai():
    import os
    from openai import OpenAI

    return OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=180,
        max_retries=2,
    )


def _grade_step(step: Path, rubric_path: Path) -> dict:
    from geomcheck.occ_part import Part
    from geomcheck.rubric import evaluate, load_rubric

    return evaluate(load_rubric(rubric_path), Part.from_step(step))


def generate_part(
    spec: dict,
    dest: Path,
    *,
    sw: str,
    model: str,
    max_llm_calls: int,
) -> dict:
    from planner.workflow.agent import WorkflowAgent
    from planner.workflow.tools import WorkflowSession

    dest.parent.mkdir(parents=True, exist_ok=True)
    _close(sw)
    extra = spec.get("extra_tools") or []
    session = WorkflowSession(base_url=sw, extra_tools=extra)
    session.start_part()
    started = time.perf_counter()
    outcome = WorkflowAgent(
        _openai(), model, session, max_llm_calls=max_llm_calls, extra_tools=extra,
    ).run(spec["prompt"])
    step, sldprt = dest.with_suffix(".step"), dest.with_suffix(".SLDPRT")
    artifacts = {}
    try:
        requests.post(f"{sw}/api/export-step", json={"stepPath": str(step)}, timeout=120)
        requests.post(f"{sw}/api/save-part-as", json={"savePath": str(sldprt)}, timeout=120)
        artifacts = {"step": str(step), "sldprt": str(sldprt)}
    except Exception as exc:
        artifacts = {"error": str(exc)}
    grade = None
    if step.is_file():
        grade = _grade_step(step, RUBRIC_DIR / f"{spec['id']}.yaml")
    _close(sw)
    return {
        "part_id": spec["id"],
        "status": outcome["status"],
        "accepted": bool(grade and grade.get("accepted")),
        "score": None if not grade else grade["score"],
        "grade": grade,
        "token_usage": outcome["token_usage"],
        "llm_calls": outcome["llm_calls"],
        "duration_seconds": round(time.perf_counter() - started, 2),
        "artifacts": artifacts,
    }


def assemble(
    case: dict,
    parts: dict[str, Path],
    dest: Path,
    *,
    sw: str,
    model: str,
    max_llm_calls: int,
) -> dict:
    from planner.assembly_workflow.agent import AssemblyAgent
    from planner.assembly_workflow.session import AssemblySession
    from geomcheck.assembly_motion import analyze_frames, clearance_mm, sample_motion

    dest.parent.mkdir(parents=True, exist_ok=True)
    _close(sw)
    session = AssemblySession(base_url=sw)
    session.start_assembly(str(dest))
    listing = "\n".join(f"- {role}: {path}" for role, path in parts.items())
    goal = _recipes() + "\n\n" + case["assembly"]["prompt"] + "\n\n零件文件：\n" + listing
    started = time.perf_counter()
    outcome = AssemblyAgent(_openai(), model, session, max_llm_calls=max_llm_calls).run(goal)
    motion = None
    static = {"component_count": len(session.aliases), "saved": session.saved}
    try:
        spec = case["motion"]
        driver_role = spec["driver"]
        driver = session.aliases.get(driver_role) or session.resolve_name(driver_role)
        faces = session.faces(driver)
        from planner.assembly_workflow.refs import resolve_on_faces
        ref = resolve_on_faces("cyl(i=0)", faces)
        sampled = sample_motion(sw, spec, driver, ref)
        motion = analyze_frames(sampled.get("frames") or [], spec, session.aliases)
        if spec.get("clearance_pairs"):
            lo, hi = spec.get("clearance_mm") or [0.02, 2.0]
            gaps = []
            frame0 = (sampled.get("frames") or [{}])[0].get("transforms") or {}
            for a, b in spec["clearance_pairs"]:
                pa, pb = parts[a].with_suffix(".step"), parts[b].with_suffix(".step")
                sa, sb = session.aliases.get(a), session.aliases.get(b)
                if not (pa.is_file() and pb.is_file() and sa and sb):
                    continue
                gap = clearance_mm(pa, pb, frame0[sa], frame0[sb])
                gaps.append({"pair": [a, b], "mm": None if gap is None else round(gap, 4),
                             "ok": gap is not None and lo <= gap <= hi})
            motion["clearance"] = gaps
            if gaps and not all(item["ok"] for item in gaps):
                motion["passed"] = False
                motion["score"] = max(0.0, motion["score"] - 10)
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "error": str(exc)}
    session.save_assembly({})
    _close(sw)
    static_score = 50.0 if static["saved"] and static["component_count"] >= 3 else 20.0
    return {
        "status": outcome["status"],
        "static": static,
        "static_score": static_score,
        "motion": motion,
        "token_usage": outcome["token_usage"],
        "llm_calls": outcome["llm_calls"],
        "duration_seconds": round(time.perf_counter() - started, 2),
        "aliases": outcome.get("aliases"),
        "tool_calls": outcome["tool_calls"],
    }


def run_condition(case: dict, condition: str, repeat: int, args) -> dict:
    case_id = case["case_id"]
    out = Path(args.output_dir) / case_id / f"{condition}_r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    part_results = []
    parts: dict[str, Path] = {}
    if condition == "gold":
        gold = CASES_DIR / "gold_parts" / ("gearbox" if "gearbox" in case_id else "engine")
        for role, filename in case["gold_roles"].items():
            path = gold / f"{role}.SLDPRT"
            if not path.is_file():
                path = gold / f"{filename}.SLDPRT"
            parts[role] = path
    else:
        generated: dict[str, Path] = {}
        for spec in case["parts"]:
            dest = out / "parts" / spec["id"]
            result = generate_part(
                spec, dest, sw=args.sw, model=args.model, max_llm_calls=args.max_part_calls,
            )
            part_results.append(result)
            if result["artifacts"].get("sldprt"):
                generated[spec["id"]] = Path(result["artifacts"]["sldprt"])
        for role, part_id in case["gold_roles"].items():
            if part_id in generated:
                parts[role] = generated[part_id]
            elif role in generated:
                parts[role] = generated[role]
        if condition == "e2e" and not all(item["accepted"] for item in part_results):
            return {
                "case_id": case_id, "condition": condition, "repeat": repeat,
                "parts": part_results, "assembly": None,
                "part_pass_rate": _rate(part_results),
                "assembly_score": 0, "motion_score": 0, "e2e": False,
            }
    assembly = assemble(
        case, parts, out / f"{case_id}.SLDASM",
        sw=args.sw, model=args.model, max_llm_calls=args.max_asm_calls,
    )
    part_rate = 1.0 if condition == "gold" else _rate(part_results)
    motion_score = float((assembly.get("motion") or {}).get("score") or 0)
    e2e = part_rate == 1.0 and bool((assembly.get("motion") or {}).get("passed"))
    return {
        "case_id": case_id,
        "condition": condition,
        "repeat": repeat,
        "parts": part_results,
        "assembly": {k: v for k, v in assembly.items() if k != "tool_calls"},
        "tool_calls": assembly.get("tool_calls"),
        "part_pass_rate": part_rate,
        "assembly_score": assembly.get("static_score"),
        "motion_score": motion_score,
        "e2e": e2e,
    }


def _rate(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for item in results if item.get("accepted")) / len(results)


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return {
        "n": len(rows),
        "part_pass_rate": round(sum(r["part_pass_rate"] for r in rows) / len(rows), 3),
        "assembly_score": round(sum(r["assembly_score"] or 0 for r in rows) / len(rows), 2),
        "motion_score": round(sum(r["motion_score"] or 0 for r in rows) / len(rows), 2),
        "e2e": round(sum(1 for r in rows if r["e2e"]) / len(rows), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="gearbox,engine")
    parser.add_argument("--condition", default="gold", choices=["gold", "gen", "e2e", "all"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--sw", default="http://localhost:5100")
    parser.add_argument("--max-part-calls", type=int, default=40)
    parser.add_argument("--max-asm-calls", type=int, default=50)
    parser.add_argument("--output-dir", default=str(ROOT / "eval" / "artifacts" / "assembly_scratch"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "assembly_scratch_result.json"))
    args = parser.parse_args()
    names = []
    for token in args.cases.split(","):
        token = token.strip()
        if token in ("gearbox", "two_stage_gearbox"):
            names.append("gearbox")
        elif token in ("engine", "single_cylinder_engine"):
            names.append("engine")
    conditions = ["gold", "gen", "e2e"] if args.condition == "all" else [args.condition]
    rows = []
    for name in names:
        case = _load_case(name)
        for condition in conditions:
            for repeat in range(1, args.repeats + 1):
                print(f"=== {case['case_id']} {condition} r{repeat} ===", flush=True)
                row = run_condition(case, condition, repeat, args)
                rows.append(row)
                print(
                    f"  parts={row['part_pass_rate']} static={row['assembly_score']} "
                    f"motion={row['motion_score']} e2e={row['e2e']}",
                    flush=True,
                )
    report = {"summary": summarize(rows), "results": rows, "model": args.model}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
