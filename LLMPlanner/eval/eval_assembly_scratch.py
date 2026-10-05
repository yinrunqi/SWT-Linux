"""从零装配试点：标准零件 / 生成零件 / 端到端，输出零件合格率、装配、运动、端到端四项指标。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import openai
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
# 第二批 23 个测例由 bench_cases2.py 生成；--cases bench2 表示这一批
from eval.assembly_scratch.bench_cases2 import CASES2  # noqa: E402
CASE_FILES.update({name: c.case_id for name, c in CASES2.items()})
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
    # v3：v2 的配方原样保留，加一份 v3 工具差异说明（增量状态、自动闭环、层位、秩法自由度、位姿自检、预算）
    "v3": (
        "assembly_topology.md",
        "assembly_joint_mapping.md",
        "assembly_coupling.md",
        "assembly_linkage.md",
        "assembly_motion_check.md",
        "assembly_v3_tools.md",
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
    import uuid
    from openai import OpenAI

    # 每个智能体运行一个新 client：TT Switch 只在同一 X-Session-Id 下复用 prompt 缓存，其他服务忽略这个头。
    client = OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=180,
        max_retries=2,
        default_headers={"X-Session-Id": f"assembly-eval-{uuid.uuid4().hex}"},
    )
    # 网关上游偶发以 400 model_param_invalid 拒收同一请求，重放即可通过；SDK 不重试 400。
    create = client.chat.completions.create

    def logged(**kwargs):
        response = create(**kwargs)
        choice = response.choices[0]
        details = getattr(response.usage, "completion_tokens_details", None)
        LLM_CALL_LOG.append({
            "finish_reason": choice.finish_reason,
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "prompt_cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", None),
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
                if isinstance(exc, openai.BadRequestError) and delay > 5:
                    raise
                time.sleep(delay)
        return logged(**kwargs)

    client.chat.completions.create = create_with_retry
    return client


ASSEMBLY_RUBRIC_DIR = CASES_DIR / "assembly_rubrics"
# 由参考装配体导出的关节轴线锚点与导程（derive_joint_anchors.py），按 joints 下标合并进 rubric
JOINT_ANCHORS = ASSEMBLY_RUBRIC_DIR / "joint_anchors.json"
# 还没有参考装配体的测例用 layout 解析出的锚点（bench_cases2.py write）
JOINT_ANCHORS_ANALYTIC = ASSEMBLY_RUBRIC_DIR / "joint_anchors_analytic.json"


def load_assembly_rubric(case_id: str) -> dict | None:
    path = ASSEMBLY_RUBRIC_DIR / f"{case_id}.yaml"
    if not path.is_file():
        return None
    rubric = yaml.safe_load(path.read_text(encoding="utf-8"))
    for source in (JOINT_ANCHORS, JOINT_ANCHORS_ANALYTIC):
        extra = json.loads(source.read_text(encoding="utf-8")).get(case_id) if source.is_file() else None
        if extra:
            for index, item in extra.items():
                rubric["joints"][int(index)].update(item)
            break
    return rubric


def score_assembly_rubric(session, case: dict, inputs: dict | None = None) -> dict | None:
    """装配级 rubric（关节 / 活动度 / 浮动件 / 过定义 / 齿轮中心距），替代旧的“保存且 ≥3 件”静态分。
    inputs 非空时写入打分用的原始组件与配合，之后改 rubric 可以离线重算。"""
    from geomcheck.assembly_rubric import evaluate

    rubric = load_assembly_rubric(case["case_id"])
    if rubric is None:
        return None
    try:
        components = session.asmwf("components").get("components") or []
        mates = session.asmwf("mates").get("mates") or []
        if inputs is not None:
            inputs.update({"components": components, "mates": mates, "aliases": dict(session.aliases)})
        return evaluate(components, mates, session.aliases, rubric)
    except Exception as exc:
        return {"score": 0.0, "error": str(exc)}


# 评测过程中 SolidWorks 崩溃 / 服务卡死的痕迹；出现即判为基础设施污染
INFRA_MARKERS = ("SW_UNAVAILABLE", "0x800706BA", "RPC", "timed out", "ConnectionError", "Max retries",
                 "Connection aborted", "RemoteDisconnected")


def infra_flags(assembly: dict) -> list[str]:
    flags = []
    lost = sum(1 for c in assembly.get("tool_calls") or [] if c.get("error_code") == "SW_UNAVAILABLE")
    if lost:
        flags.append(f"agent 工具调用 SW_UNAVAILABLE ×{lost}")
    for key in ("motion", "rubric"):
        err = str((assembly.get(key) or {}).get("error") or "")
        if any(m in err for m in INFRA_MARKERS):
            flags.append(f"{key}: {err[:160]}")
    if assembly.get("infra_error"):
        flags.append(str(assembly["infra_error"])[:160])
    if assembly.get("status") in ("sw_lost", "resume_failed"):
        flags.append(f"agent {assembly['status']}: {str(assembly.get('error') or '')[:160]}")
    return flags


# 这些状态说明 LLM 或 SolidWorks 中途出了问题、结果不完整，--resume 时从 checkpoint 接着跑
RERUN_STATUSES = {"llm_error", "sw_lost", "resume_failed"}


def _grade_step(step: Path, rubric_path: Path) -> dict:
    try:
        from geomcheck.occ_part import Part
        from geomcheck.rubric import evaluate, load_rubric
    except ImportError as exc:  # 未装 cadquery-ocp，或其 DLL 被系统策略拦截
        return {
            "accepted": False,
            "score": None,
            "skipped": True,
            "error": f"OCC 不可用，零件未打分，用 eval/assembly_scratch/regrade_offline.py 补算: {exc}",
        }

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
    reasoning_effort: str | None = None,
    resume: bool = False,
    view_tool: bool = True,
) -> dict:
    from planner.workflow.agent import WorkflowAgent
    from planner.workflow.tools_v2 import WorkflowSessionV2

    dest.parent.mkdir(parents=True, exist_ok=True)
    result_path, checkpoint = dest.with_suffix(".result.json"), dest.with_suffix(".checkpoint.json")
    part_workflow = {"version": "L1_v2", "view_tool": bool(view_tool)}
    if resume and result_path.is_file():
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        if previous.get("part_workflow") != part_workflow:
            # 旧结果来自 L1 或另一种截图设置，checkpoint 也对不上，必须从头生成
            resume = False
        elif previous.get("status") not in RERUN_STATUSES and Path(str(previous["artifacts"].get("sldprt", ""))).is_file():
            previous["reused"] = True
            return previous
    if not resume:
        checkpoint.unlink(missing_ok=True)
    _close(sw)
    extra = spec.get("extra_tools") or []
    session = WorkflowSessionV2(base_url=sw, extra_tools=extra)
    started = time.perf_counter()
    step, sldprt = dest.with_suffix(".step"), dest.with_suffix(".SLDPRT")
    try:
        session.start_part()
        outcome = WorkflowAgent(
            _openai(), model, session, max_llm_calls=max_llm_calls, extra_tools=extra,
            reasoning_effort=reasoning_effort, checkpoint_path=checkpoint, view_tool=view_tool,
        ).run(spec["prompt"], resume=resume)
        artifacts = {}
        try:
            requests.post(f"{sw}/api/export-step", json={"stepPath": str(step)}, timeout=120)
            requests.post(f"{sw}/api/save-part-as", json={"savePath": str(sldprt)}, timeout=120)
            artifacts = {"step": str(step), "sldprt": str(sldprt)}
        except Exception as exc:
            artifacts = {"error": str(exc)}
    finally:
        _close(sw)
    trace = dest.with_suffix(".agent.json")
    _write_json(trace, outcome)
    grade = None
    if step.is_file():
        grade = _grade_step(step, RUBRIC_DIR / f"{spec['id']}.yaml")
    result = {
        "part_id": spec["id"],
        "part_workflow": part_workflow,
        "status": outcome["status"],
        "accepted": bool(grade and grade.get("accepted")),
        "score": None if not grade else grade["score"],
        "grade": grade,
        "token_usage": outcome["token_usage"],
        "llm_calls": outcome["llm_calls"],
        "error": outcome.get("error"),
        "finish_reasons": outcome.get("finish_reasons"),
        "tool_calls": outcome["tool_calls"],
        "agent_summary": outcome.get("summary"),
        "resumed": outcome.get("resumed"),
        "duration_seconds": outcome.get("duration_seconds", round(time.perf_counter() - started, 2)),
        "artifacts": artifacts,
        "trace": str(trace),
        "checkpoint": str(checkpoint),
    }
    _write_json(result_path, result)
    return result


def assemble(case: dict, parts: dict[str, Path], dest: Path, *, sw: str, **kwargs) -> dict:
    try:
        return _assemble(case, parts, dest, sw=sw, **kwargs)
    finally:
        _close(sw)


def _assemble(
    case: dict,
    parts: dict[str, Path],
    dest: Path,
    *,
    sw: str,
    model: str,
    max_llm_calls: int,
    workflow: str = "v1",
    max_tokens: int = 8000,
    reasoning_effort: str | None = None,
    resume: bool = False,
    view_tool: bool = True,
) -> dict:
    from planner.assembly_workflow.agent import AssemblyAgent
    from planner.assembly_workflow.session import AssemblySession
    from planner.assembly_workflow.tools import tool_names, tool_specs

    dest.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = dest.with_name("agent.checkpoint.json")
    if not resume:
        checkpoint.unlink(missing_ok=True)
    _close(sw)
    session = AssemblySession(base_url=sw, workflow=workflow, allowed_tools=tool_names(workflow, view_tool=view_tool))
    session.start_assembly(str(dest))
    listing = "\n".join(f"- {role}: {path}" for role, path in parts.items())
    goal = _recipes(workflow) + "\n\n" + case["assembly"]["prompt"] + "\n\n零件文件：\n" + listing
    started = time.perf_counter()
    LLM_CALL_LOG.clear()
    outcome = AssemblyAgent(
        _openai(), model, session, max_llm_calls=max_llm_calls, max_tokens=max_tokens,
        tools=tool_specs(workflow, view_tool=view_tool), reasoning_effort=reasoning_effort,
        checkpoint_path=checkpoint, view_tool=view_tool,
    ).run(goal, resume=resume)
    messages = outcome.get("messages") or []
    _write_json(dest.with_name("agent_messages.json"), messages)
    _write_json(dest.with_name("agent_llm_responses.json"), outcome.get("llm_responses") or [])
    agent_texts = [
        {"turn": i, "text": m.get("content") or "", "with_tools": bool(m.get("tool_calls"))}
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and (m.get("content") or "").strip()
    ]
    static = {"component_count": len(session.aliases), "saved": session.saved}
    # 先存检查点：存下的是智能体结束时的状态（运动评测会加马达、临时配合），之后可离线重评
    # SolidWorks 卡死后装配文档可能已丢失：记为基础设施错误，不让整批评测中断
    infra_error = None
    try:
        session.save_assembly({})
    except Exception as exc:
        infra_error = f"save_assembly: {exc}"
    rubric_inputs: dict = {}
    rubric = score_assembly_rubric(session, case, rubric_inputs)
    _write_json(dest.with_name("rubric_inputs.json"), rubric_inputs)
    try:
        motion = _motion(session, case["motion"], parts, sw)
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "score_v1": 0.0, "error": str(exc)}
    return {
        "status": outcome["status"],
        "error": outcome.get("error"),
        "infra_error": infra_error,
        "static": static,
        "rubric": rubric,
        "static_score": (rubric or {}).get("score", 0.0),
        "motion": motion,
        "token_usage": outcome["token_usage"],
        "llm_calls": outcome["llm_calls"],
        "resumed": outcome.get("resumed"),
        "checkpoint": str(checkpoint),
        "agent_duration_seconds": outcome.get("duration_seconds"),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "aliases": outcome.get("aliases"),
        "tool_calls": outcome["tool_calls"],
        "agent_texts": agent_texts,
        "llm_calls_detail": list(LLM_CALL_LOG),
        "agent_last_motion": outcome.get("last_motion"),
        "agent_last_pose": session.last_pose,
        "truncations": outcome.get("truncations"),
        "joints": session.joints,
    }


def _motion(session, spec: dict, parts: dict[str, Path], sw: str) -> dict:
    """mode: motor（默认，马达驱动整周回转）或 poses（逐位姿临时配合）。"""
    from geomcheck.assembly_motion import (
        analyze_frames, clearance_mm, run_poses, sample_motion,
    )

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
    if sampled.get("sampling"):
        motion["sampling"] = sampled["sampling"]
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
                         "ok": gap is not None and lo <= gap <= hi, "range_mm": [lo, hi],
                         "steps": [str(pa), str(pb)], "transforms": [frame0[sa], frame0[sb]]})
        # 只记录不计分：planner/gears.py 的外啮合齿轮不留侧隙（齿厚正好半个齿距），标准零件实测只有
        # 约 0.006–0.009 mm，低于 0.02 mm 下限，这一项过不了阳性对照。首帧位姿一并记下，OCC 不可用时可离线补量
        motion["clearance"] = gaps
    return motion


def run_condition(case: dict, condition: str, repeat: int, args, workflow: str = "v1",
                  resume: bool = False) -> dict:
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
                reasoning_effort=args.reasoning_effort, resume=resume,
                view_tool=getattr(args, "part_view_tool", True),
            )
            skipped = bool((result.get("grade") or {}).get("skipped"))
            print(
                f"  part {spec['id']}: "
                + ("[沿用上次结果] " if result.get("reused") else "[续跑] " if result.get("resumed") else "")
                + ("grade=skipped(OCC) " if skipped else f"accepted={result['accepted']} score={result['score']} ")
                + f"step={'yes' if Path(str(result['artifacts'].get('step', ''))).is_file() else 'NO'} "
                f"status={result['status']} calls={result['llm_calls']}"
                + (f" error={result['error']}" if result.get("error") else ""),
                flush=True,
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
    budget = asm_call_budget(case, args)
    retries = max(0, int(getattr(args, "infra_retries", 0) or 0))
    for attempt in range(retries + 1):
        # 重试时从 checkpoint 接着跑：已有的 LLM 回复不重新请求；上次重放就对不上（resume_failed）则从头来
        if attempt:
            resume = assembly.get("status") != "resume_failed"
        try:
            assembly = assemble(
                case, parts, out / f"{case_id}.SLDASM",
                sw=args.sw, model=args.model, max_llm_calls=budget, workflow=workflow,
                max_tokens=args.asm_max_tokens, reasoning_effort=args.reasoning_effort, resume=resume,
                view_tool=not getattr(args, "no_view_tool", False),
            )
        except Exception as exc:  # SolidWorks 已经死掉时连新建装配体都会失败
            assembly = {"status": "infra_exception", "infra_error": f"{type(exc).__name__}: {exc}",
                        "static": {}, "rubric": None, "static_score": 0.0, "motion": {"score": 0.0},
                        "tool_calls": []}
        flags = infra_flags(assembly)
        if not flags or attempt == retries:
            break
        print(f"  基础设施污染，重启 SolidWorks 后重跑装配（第 {attempt + 1} 次）：{flags}", flush=True)
        from eval.assembly_scratch.sw_restart import restart_solidworks
        restart_solidworks()
    assembly["infra_flags"] = flags
    assembly["infra_attempts"] = attempt + 1
    assembly["call_budget"] = budget
    part_rate = 1.0 if condition == "gold" else _rate(part_results)
    motion = assembly.get("motion") or {}
    motion_score = float(motion.get("score") or 0)
    rubric = assembly.get("rubric") or {}
    ok = structure_ok(rubric)
    e2e = part_rate == 1.0 and bool(motion.get("passed"))
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
        "assembly_score_v1": rubric.get("score_v1"),
        "motion_score": motion_score,
        "motion_score_v1": motion.get("score_v1"),
        "structure_ok": ok,
        "motion_score_gated": motion_score if ok else 0.0,
        "infra_contaminated": bool(flags),
        "e2e": e2e,
    }


def structure_ok(rubric: dict | None) -> bool:
    """结构成立 = 所有角色装上并连到机架、活动度正确；结构不成立时运动分另记为 0（motion_score_gated）。"""
    rubric = rubric or {}
    return bool(rubric) and not rubric.get("error") and rubric.get("completeness") == 1 \
        and bool((rubric.get("mobility") or {}).get("ok"))


def asm_call_budget(case: dict, args) -> int:
    """装配 LLM 调用预算：固定值，或在跑之前按角色数 × 系数冻结（取两者较大者）。"""
    per_role = float(getattr(args, "asm_calls_per_role", 0) or 0)
    if per_role <= 0:
        return int(args.max_asm_calls)
    return max(int(args.max_asm_calls), int(round(per_role * len(case.get("gold_roles") or {}))))


def _rate(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for item in results if item.get("accepted")) / len(results)


def _mean(values: list[float], digits: int = 2) -> float | None:
    values = [float(v) for v in values if v is not None]
    return round(sum(values) / len(values), digits) if values else None


def _std(values: list[float], digits: int = 2) -> float | None:
    values = [float(v) for v in values if v is not None]
    if len(values) < 2:
        return None
    mu = sum(values) / len(values)
    return round((sum((v - mu) ** 2 for v in values) / (len(values) - 1)) ** 0.5, digits)


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    asm = [r.get("assembly") or {} for r in rows]
    return {
        "n": len(rows),
        "part_pass_rate": _mean([r["part_pass_rate"] for r in rows], 3),
        "assembly_score": _mean([r["assembly_score"] or 0 for r in rows]),
        "assembly_score_std": _std([r["assembly_score"] or 0 for r in rows]),
        "motion_score": _mean([r["motion_score"] or 0 for r in rows]),
        "motion_score_std": _std([r["motion_score"] or 0 for r in rows]),
        "motion_score_gated": _mean([r.get("motion_score_gated", r["motion_score"]) or 0 for r in rows]),
        "assembly_score_v1": _mean([r.get("assembly_score_v1") for r in rows]),
        "motion_score_v1": _mean([r.get("motion_score_v1") for r in rows]),
        "structure_ok_rate": _mean([float(bool(r.get("structure_ok"))) for r in rows], 3),
        "e2e": _mean([float(bool(r["e2e"])) for r in rows], 3),
        "finish_rate": _mean([float(a.get("status") == "finished") for a in asm], 3),
        "saved_by_agent_rate": _mean([float(bool((a.get("static") or {}).get("saved"))) for a in asm], 3),
        "budget_exhausted_rate": _mean([float(a.get("status") == "max_llm_calls") for a in asm], 3),
        "llm_calls_mean": _mean([a.get("llm_calls") for a in asm], 1),
    }


def summarize(rows: list[dict]) -> dict:
    """主统计只用没有基础设施污染的行；被污染的行单独计数。"""
    clean = [r for r in rows if not r.get("infra_contaminated")]
    out = _summarize(clean)
    if len(clean) != len(rows):
        out["n_infra_contaminated"] = len(rows) - len(clean)
        out["all_rows"] = _summarize(rows)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="gearbox,engine")
    parser.add_argument("--condition", default="gold", choices=["gold", "gen", "e2e", "all"])
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--workflow", default="v1", choices=["v1", "v2", "v3", "both"],
                        help="v1=手写场景配方与原工具集；v2=数据导出的通用配方与扩展工具集；"
                             "v3=v2 + 增量状态、秩法自由度、自动闭环、位姿自检、截断重试与预算提示")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--sw", default="http://localhost:5100")
    parser.add_argument("--max-part-calls", type=int, default=40)
    parser.add_argument("--max-asm-calls", type=int, default=100)
    parser.add_argument("--asm-calls-per-role", type=float, default=0,
                        help=">0 时装配预算 = max(--max-asm-calls, 系数 × 角色数)，在跑之前冻结")
    parser.add_argument("--infra-retries", type=int, default=1,
                        help="装配阶段出现 SolidWorks 崩溃 / 超时痕迹时，重启 SolidWorks 后重跑的次数")
    parser.add_argument("--asm-max-tokens", type=int, default=8000)
    parser.add_argument("--no-view-tool", action="store_true",
                        help="不提供截图工具：v3 装配不带 view_assembly，gen/e2e 的零件阶段不带 view_model"
                             "（骨干模型不支持图片输入时必须加）")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
        default=None,
        help="sent as reasoning_effort on every part and assembly agent call; omitted = provider default",
    )
    parser.add_argument("--output-dir", default=str(ROOT / "eval" / "artifacts" / "assembly_scratch"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "assembly_scratch_result.json"))
    parser.add_argument(
        "--resume", action="store_true",
        help="keep finished rows of an existing --output and run only the missing ones; rows with infra_error "
        "or an interrupted agent (llm_error / sw_lost / resume_failed) are rerun, continuing from the part and "
        "assembly checkpoints saved under --output-dir (finished parts are reused)",
    )
    args = parser.parse_args()
    names = []
    tokens = [t.strip() for t in args.cases.split(",")]
    groups = {"bench": list(BENCH_CASES), "bench2": list(CASES2)}
    tokens = [n for t in tokens for n in groups.get(t, [t])]
    for token in tokens:
        name = CASE_ALIASES.get(token, token)
        if not (CASES_DIR / f"{name}.yaml").is_file():
            parser.error(f"未知测例 {token}，可选：{', '.join(CASE_FILES)}")
        names.append(name)
    conditions = ["gold", "gen", "e2e"] if args.condition == "all" else [args.condition]
    if "e2e" in conditions:
        try:
            import geomcheck.occ_part  # noqa: F401
        except ImportError as exc:
            parser.error(f"e2e 条件要在建模时给零件打分才能决定是否装配，但 OCC 不可用（{exc}）；改用 --condition gen")
    workflows = ["v1", "v2"] if args.workflow == "both" else [args.workflow]
    from planner.workflow.agent import default_reasoning_effort
    args.reasoning_effort = default_reasoning_effort(args.model, args.reasoning_effort)
    # 只有 v3 装配工具集带截图工具；之前的结果没有这个字段，都是不带截图的
    args.view_tool = "v3" in workflows and not args.no_view_tool
    args.part_view_tool = not args.no_view_tool
    part_workflow = {"version": "L1_v2", "view_tool": args.part_view_tool}
    rows = []
    if args.resume and Path(args.output).is_file():
        previous = json.loads(Path(args.output).read_text(encoding="utf-8"))
        previous.setdefault("view_tool", False)
        for key in ("model", "reasoning_effort", "asm_max_tokens", "view_tool"):
            if previous.get(key) != getattr(args, key):
                parser.error(f"--resume：已有结果的 {key}={previous.get(key)!r} 与本次 {getattr(args, key)!r} 不一致")
        for row in previous.get("results") or []:
            asm = row.get("assembly") or {}
            broken_parts = [
                p["part_id"] for p in row.get("parts") or []
                if p.get("status") in RERUN_STATUSES or p.get("part_workflow") != part_workflow
            ]
            if asm.get("infra_error") or asm.get("status") in RERUN_STATUSES or broken_parts:
                print(f"[resume] 续跑 {row['case_id']} {row['condition']} r{row['repeat']} {row['workflow']}"
                      f"（{asm.get('infra_error') or asm.get('error') or '零件中断: ' + ','.join(broken_parts)}）",
                      flush=True)
                continue
            rows.append(row)
        print(f"[resume] 保留已完成的 {len(rows)} 行", flush=True)
    done = {(r["case_id"], r["condition"], r["repeat"], r["workflow"]) for r in rows}

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
            "part_grading_skipped": any(
                (part.get("grade") or {}).get("skipped") for row in rows for part in row.get("parts") or []
            ),
            "base_url": os.environ.get("OPENAI_BASE_URL"),
            "reasoning_effort": args.reasoning_effort,
            "prompt_lang": "zh",
            "asm_max_tokens": args.asm_max_tokens,
            "view_tool": args.view_tool,
            "part_workflow": part_workflow,
            "max_asm_calls": args.max_asm_calls,
            "asm_calls_per_role": args.asm_calls_per_role,
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
                    if (case["case_id"], condition, repeat, workflow) in done:
                        print(f"=== {case['case_id']} {condition} r{repeat} {workflow} === 已完成，跳过", flush=True)
                        continue
                    print(f"=== {case['case_id']} {condition} r{repeat} {workflow} ===", flush=True)
                    row = run_condition(case, condition, repeat, args, workflow, resume=args.resume)
                    rows.append(row)
                    asm = row.get("assembly") or {}
                    print(
                        f"  parts={row['part_pass_rate']} static={row['assembly_score']} "
                        f"motion={row['motion_score']} e2e={row['e2e']} "
                        f"asm_status={asm.get('status')} asm_calls={asm.get('llm_calls')}"
                        + (" [infra]" if row.get("infra_contaminated") else "")
                        + (f" error={asm.get('error') or asm.get('infra_error')}"
                           if asm.get("error") or asm.get("infra_error") else ""),                        flush=True,
                    )
                    write()
    report = write()
    print(json.dumps(report["by_workflow"], ensure_ascii=False))


if __name__ == "__main__":
    main()
