"""从零装配试点：标准零件 / 生成零件 / 端到端，输出零件合格率、装配、运动、端到端四项指标。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from load_env import ensure_test_env_loaded
from planner.workflow.tools import EXTRA_TOOLSET_VERSION

ensure_test_env_loaded()

CASES_DIR = ROOT / "eval" / "assembly_scratch"
RUBRIC_DIR = CASES_DIR / "rubrics"
RECIPES = ROOT / "planner" / "skills" / "recipes"
LLM_CALL_LOG: list[dict] = []
MODEL = os.environ.get("PLAN_MODEL") or os.environ.get("PLANNER_MODEL") or "gemini-3.5-flash"


# 测例 yaml 文件名 → case_id；命令行两种都认
CASE_FILES = {
    "gearbox": "two_stage_gearbox",
    "engine": "single_cylinder_engine",
    "desk_lamp": "adjustable_desk_lamp",
    "umbrella": "folding_umbrella",
    "drill_gearbox": "drill_planetary_gearbox",
    "sewing_machine": "sewing_machine_drive",
    "projector": "film_projector_drive",
}
CORE_CASES = list(CASE_FILES)
# 扩展的 20 个测例由 bench_cases.py 生成；--cases bench 表示全部扩展测例
from eval.assembly_scratch.bench_cases import CASES as BENCH_CASES  # noqa: E402
CASE_FILES.update({name: c.case_id for name, c in BENCH_CASES.items()})
CASE_ALIASES = {case_id: name for name, case_id in CASE_FILES.items()}


def _load_case(name: str) -> dict:
    path = CASES_DIR / f"{name}.yaml"
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    case.setdefault("gold_dir", name)
    return case


RECIPE_SETS = {
    # v1：手写的场景配方（齿轮系、曲柄滑块）
    "v1": (
        "assembly_ground_dof.md",
        "assembly_gear_train.md",
        "assembly_crank_slider.md",
        "assembly_motion_check.md",
    ),
    # v2：由 AutoMate / Fusion 360 统计导出的通用配方，运动验证沿用同一份
    "v2": (
        "assembly_topology.md",
        "assembly_joint_mapping.md",
        "assembly_coupling.md",
        "assembly_linkage.md",
        "assembly_motion_check.md",
    ),
}


def _recipes(workflow: str = "v1") -> str:
    return "\n\n".join((RECIPES / fname).read_text(encoding="utf-8") for fname in RECIPE_SETS[workflow])


def _close(sw: str) -> None:
    try:
        requests.post(f"{sw}/api/close-all-documents", timeout=60)
    except Exception:
        pass


def _openai():
    import os
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=180,
        max_retries=2,
    )
    # 网关上游偶发以 400 model_param_invalid 拒收同一请求，重放即可通过；SDK 不重试 400。
    create = client.chat.completions.create

    def logged(**kwargs):
        response = create(**kwargs)
        choice = response.choices[0]
        LLM_CALL_LOG.append({
            "finish_reason": choice.finish_reason,
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "n_tool_calls": len(choice.message.tool_calls or []),
            "text_chars": len(choice.message.content or ""),
        })
        return response

    def create_with_retry(**kwargs):
        for delay in (5, 15, 30, 60, 120, 180, 300):
            try:
                return logged(**kwargs)
            except Exception as exc:
                print(f"  [LLM retry in {delay}s] {type(exc).__name__}: {str(exc)[:120]}")
                if os.environ.get("EVAL_DUMP_REJECTED"):
                    Path(os.environ["EVAL_DUMP_REJECTED"]).write_text(
                        json.dumps(kwargs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
                time.sleep(delay)
        return logged(**kwargs)

    client.chat.completions.create = create_with_retry
    return client


ASSEMBLY_RUBRIC_DIR = CASES_DIR / "assembly_rubrics"


def score_assembly_rubric(session, case: dict) -> dict | None:
    """装配级 rubric（关节 / 活动度 / 浮动件 / 过定义 / 齿轮中心距），替代旧的“保存且 ≥3 件”静态分。"""
    from geomcheck.assembly_rubric import evaluate

    path = ASSEMBLY_RUBRIC_DIR / f"{case['case_id']}.yaml"
    if not path.is_file():
        return None
    try:
        components = session.asmwf("components").get("components") or []
        mates = session.asmwf("mates").get("mates") or []
        return evaluate(components, mates, session.aliases, yaml.safe_load(path.read_text(encoding="utf-8")))
    except Exception as exc:
        return {"score": 0.0, "error": str(exc)}


def _grade_step(step: Path, rubric_path: Path) -> dict:
    from geomcheck.occ_part import Part
    from geomcheck.rubric import evaluate, load_rubric

    try:
        return evaluate(load_rubric(rubric_path), Part.from_step(step))
    except Exception as exc:
        return {"accepted": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


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
    trace = dest.with_suffix(".agent.json")
    _write_json(trace, outcome)
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
        "trace": str(trace),
    }


def assemble(
    case: dict,
    parts: dict[str, Path],
    dest: Path,
    *,
    sw: str,
    model: str,
    max_llm_calls: int,
    workflow: str = "v1",
    max_tokens: int = 8000,
) -> dict:
    from planner.assembly_workflow.agent import AssemblyAgent
    from planner.assembly_workflow.session import AssemblySession
    from planner.assembly_workflow.tools import tool_names, tool_specs

    dest.parent.mkdir(parents=True, exist_ok=True)
    _close(sw)
    session = AssemblySession(base_url=sw, workflow=workflow, allowed_tools=tool_names(workflow))
    session.start_assembly(str(dest))
    listing = "\n".join(f"- {role}: {path}" for role, path in parts.items())
    goal = _recipes(workflow) + "\n\n" + case["assembly"]["prompt"] + "\n\n零件文件：\n" + listing
    started = time.perf_counter()
    LLM_CALL_LOG.clear()
    outcome = AssemblyAgent(
        _openai(), model, session, max_llm_calls=max_llm_calls, max_tokens=max_tokens,
        tools=tool_specs(workflow),
    ).run(goal)
    messages = outcome.get("messages") or []
    _write_json(dest.with_name("agent_messages.json"), messages)
    _write_json(dest.with_name("agent_llm_responses.json"), outcome.get("llm_responses") or [])
    agent_texts = [
        {"turn": i, "text": m.get("content") or "", "with_tools": bool(m.get("tool_calls"))}
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    static = {"component_count": len(session.aliases), "saved": session.saved}
    rubric = score_assembly_rubric(session, case)
    try:
        motion = _motion(session, case["motion"], parts, sw)
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "error": str(exc)}
    # SolidWorks 卡死后装配文档可能已丢失：记为基础设施错误，不让整批评测中断
    infra_error = None
    try:
        session.save_assembly({})
    except Exception as exc:
        infra_error = f"save_assembly: {exc}"
    _close(sw)
    return {
        "status": outcome["status"],
        "infra_error": infra_error,
        "static": static,
        "rubric": rubric,
        "static_score": (rubric or {}).get("score", 0.0),
        "motion": motion,
        "token_usage": outcome["token_usage"],
        "llm_calls": outcome["llm_calls"],
        "duration_seconds": round(time.perf_counter() - started, 2),
        "aliases": outcome.get("aliases"),
        "tool_calls": outcome["tool_calls"],
        "agent_texts": agent_texts,
        "llm_calls_detail": list(LLM_CALL_LOG),
        "agent_last_motion": outcome.get("last_motion"),
        "joints": session.joints,
    }


def _motion(session, spec: dict, parts: dict[str, Path], sw: str) -> dict:
    """mode: motor（默认，马达驱动整周回转）或 poses（逐位姿临时配合）。"""
    from geomcheck.assembly_motion import analyze_frames, clearance_mm, run_poses, sample_motion

    if spec.get("mode") == "poses":
        return run_poses(session, spec)
    driver_role = spec["driver"]
    driver = session.aliases.get(driver_role) or session.resolve_name(driver_role)
    faces = session.faces(driver)
    from planner.assembly_workflow.refs import resolve_on_faces
    ref = resolve_on_faces("cyl(i=0)", faces)
    sampled = sample_motion(sw, spec, driver, ref)
    motion = analyze_frames(sampled.get("frames") or [], spec, session.aliases,
                            sampled.get("interference_frames"))
    if spec.get("clearance_pairs"):
        lo, hi = spec.get("clearance_mm") or [0.02, 2.0]
        gaps = []
        frame0 = (sampled.get("frames") or [{}])[0].get("transforms") or {}
        for a, b in spec["clearance_pairs"]:
            if a not in parts or b not in parts:
                continue
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
    return motion


def run_condition(case: dict, condition: str, repeat: int, args, workflow: str = "v1") -> dict:
    case_id = case["case_id"]
    out = Path(args.output_dir).resolve() / workflow / case_id / f"{condition}_r{repeat}"
    out.mkdir(parents=True, exist_ok=True)
    part_results = []
    parts: dict[str, Path] = {}
    if condition == "gold":
        gold = CASES_DIR / "gold_parts" / case["gold_dir"]
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
                "case_id": case_id, "condition": condition, "repeat": repeat, "workflow": workflow,
                "parts": part_results, "assembly": None,
                "part_pass_rate": _rate(part_results),
                "assembly_score": 0, "motion_score": 0, "e2e": False,
            }
    assembly = assemble(
        case, parts, out / f"{case_id}.SLDASM",
        sw=args.sw, model=args.model, max_llm_calls=args.max_asm_calls, workflow=workflow,
        max_tokens=args.asm_max_tokens,
    )
    part_rate = 1.0 if condition == "gold" else _rate(part_results)
    motion_score = float((assembly.get("motion") or {}).get("score") or 0)
    e2e = part_rate == 1.0 and bool((assembly.get("motion") or {}).get("passed"))
    return {
        "case_id": case_id,
        "condition": condition,
        "repeat": repeat,
        "workflow": workflow,
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
    parser.add_argument("--workflow", default="v1", choices=["v1", "v2", "both"],
                        help="v1=手写场景配方与原工具集；v2=数据导出的通用配方与扩展工具集")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--sw", default="http://localhost:5100")
    parser.add_argument("--max-part-calls", type=int, default=40)
    parser.add_argument("--max-asm-calls", type=int, default=50)
    parser.add_argument("--asm-max-tokens", type=int, default=8000)
    parser.add_argument("--output-dir", default=str(ROOT / "eval" / "artifacts" / "assembly_scratch"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "assembly_scratch_result.json"))
    args = parser.parse_args()
    names = []
    tokens = [t.strip() for t in args.cases.split(",")]
    tokens = [n for t in tokens for n in (list(BENCH_CASES) if t == "bench" else [t])]
    for token in tokens:
        name = CASE_ALIASES.get(token, token)
        if not (CASES_DIR / f"{name}.yaml").is_file():
            parser.error(f"未知测例 {token}，可选：{', '.join(CASE_FILES)}")
        names.append(name)
    conditions = ["gold", "gen", "e2e"] if args.condition == "all" else [args.condition]
    workflows = ["v1", "v2"] if args.workflow == "both" else [args.workflow]
    rows = []

    def write() -> dict:
        report = {
            "summary": summarize(rows),
            "by_workflow": {
                wf: {
                    "all": summarize([r for r in rows if r["workflow"] == wf]),
                    **{
                        cid: summarize([r for r in rows if r["workflow"] == wf and r["case_id"] == cid])
                        for cid in sorted({r["case_id"] for r in rows})
                    },
                }
                for wf in workflows
            },
            "recipes": {wf: list(RECIPE_SETS[wf]) for wf in workflows},
            "results": rows,
            "model": args.model,
            "asm_max_tokens": args.asm_max_tokens,
            "extra_toolset": EXTRA_TOOLSET_VERSION,
        }
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    for name in names:
        case = _load_case(name)
        for condition in conditions:
            for repeat in range(1, args.repeats + 1):
                # 同一 case / 重复内交替 v1、v2，减少 SolidWorks 状态漂移造成的系统偏差
                for workflow in workflows:
                    print(f"=== {case['case_id']} {condition} r{repeat} {workflow} ===", flush=True)
                    row = run_condition(case, condition, repeat, args, workflow)
                    rows.append(row)
                    print(
                        f"  parts={row['part_pass_rate']} static={row['assembly_score']} "
                        f"motion={row['motion_score']} e2e={row['e2e']}",
                        flush=True,
                    )
                    write()
    report = write()
    print(json.dumps(report["by_workflow"], ensure_ascii=False))


if __name__ == "__main__":
    main()
