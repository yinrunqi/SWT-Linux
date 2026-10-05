"""按评测结果里保存的 LLM 输出，在 SolidWorks 里逐步重放建模过程（不调用 LLM），用于录制演示视频。

每一步打印模型这一轮的文字与推理摘要、调用的工具和参数，再把同一个工具调用发给与评测时相同的会话
（零件：WorkflowSession，装配：AssemblySession），执行后把视图切到等轴测并缩放到全屏。

    # text-to-CAD（L1）：结果来自 eval_text_singleturn_qwen.py
    venv\\Scripts\\python.exe -X utf8 -u eval\\replay_demo.py text eval\\text_workflow_l1_xxx_result.json --case camshaft

    # 装配（gen / gold）：结果来自 eval_assembly_scratch.py；零件用评测时保存的 .SLDPRT
    venv\\Scripts\\python.exe -X utf8 -u eval\\replay_demo.py assembly eval\\assembly_gen_xxx.json --case pumpjack_drive \\
        --artifacts eval\\artifacts\\assembly_gen_xxx --animate

默认原样重放全部调用，包括评测时失败并已回滚的调用：跳过它们可能改变 SolidWorks 自动编号的特征名，
后续按名称引用特征的调用就会对不上。--skip-failed 只建议在确认没有这类引用时使用。
需要 C# 服务的 /api/view/fit（没有时只跳过视图调整，不影响重放）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEXT_QUERY_TOOLS = {"get_model_state", "set_parameters", "finish"}
ASSEMBLY_QUERY_TOOLS = {"get_assembly_state", "check_dof", "finish"}
SCREENSHOT_TOOLS = {"view_model", "view_assembly", "render_views"}


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_steps(llm_responses: list[Any] | None, tool_log: list[dict]) -> list[dict]:
    """把原始响应里的工具调用与评测时的工具日志逐个对齐；没有原始响应时只用工具日志。"""
    steps: list[dict] = []
    log = iter(tool_log)
    if llm_responses:
        for turn, response in enumerate(llm_responses, 1):
            if not isinstance(response, dict):
                continue
            message = (response.get("choices") or [{}])[0].get("message") or {}
            calls = message.get("tool_calls") or []
            text = {"content": message.get("content") or "", "reasoning": message.get("reasoning_content") or ""}
            if not calls:
                steps.append({"turn": turn, "tool": None, **text})
            for call in calls:
                function = call.get("function") or {}
                original = next(log, None)
                if original is None:
                    # 评测时智能体在 finish 后结束，同一轮后面的调用没有执行
                    break
                if original.get("tool") != function.get("name"):
                    raise RuntimeError(
                        f"第 {turn} 轮原始响应调用 {function.get('name')}，工具日志却是 {original.get('tool')}"
                    )
                steps.append({
                    "turn": turn,
                    "tool": function.get("name"),
                    "raw_args": function.get("arguments") or "{}",
                    "args": original.get("args"),
                    "original_ok": original.get("ok"),
                    "original_error": original.get("error_code"),
                    **text,
                })
                text = {"content": "", "reasoning": ""}
        return steps
    return [
        {"turn": i, "tool": t["tool"], "args": t["args"], "original_ok": t["ok"],
         "original_error": t.get("error_code"), "content": "", "reasoning": ""}
        for i, t in enumerate(tool_log, 1)
    ]


def _parse_args(step: dict) -> dict | None:
    if isinstance(step.get("args"), dict) and "_raw" not in step["args"]:
        return step["args"]
    try:
        value = json.loads(step.get("raw_args") or "{}")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _fit(sw: str, target: str, view: str) -> None:
    if view == "off":
        return
    try:
        requests.post(f"{sw}/api/view/fit", json={"target": target, "view": view}, timeout=30)
    except requests.RequestException:
        pass


def replay(steps: list[dict], session, *, sw: str, target: str, query_tools: set[str], args: argparse.Namespace,
           before_call=None) -> tuple[int, int]:
    mismatches = 0
    executed = 0
    for index, step in enumerate(steps, 1):
        if args.show_text and (step["reasoning"] or step["content"]):
            if step["reasoning"]:
                print(f"  [思考] {_shorten(step['reasoning'], args.text_chars)}", flush=True)
            if step["content"]:
                print(f"  [模型] {_shorten(step['content'], args.text_chars)}", flush=True)
        name = step["tool"]
        if not name:
            continue
        call_args = _parse_args(step)
        tag = "" if step["original_ok"] in (None, True) else f"（评测时失败: {step['original_error']}）"
        print(f"[{index}/{len(steps)}] 第 {step['turn']} 轮 {name} {_shorten(json.dumps(call_args, ensure_ascii=False), 160)}{tag}",
              flush=True)
        if name == "finish":
            print(f"  finish: {_shorten((call_args or {}).get('summary', ''), args.text_chars)}", flush=True)
            continue
        if name in SCREENSHOT_TOOLS:
            print("  截图 / 投影只给模型看，不改模型，跳过", flush=True)
            continue
        if call_args is None:
            print("  参数不是合法 JSON，评测时同样未执行，跳过", flush=True)
            continue
        if args.skip_failed and step["original_ok"] is False:
            print("  --skip-failed：跳过", flush=True)
            continue
        if before_call is not None:
            call_args = before_call(name, call_args)
        result = session.call(name, call_args)
        executed += 1
        ok = bool(result.get("ok"))
        if step["original_ok"] is not None and ok != step["original_ok"]:
            mismatches += 1
        detail = "" if ok else f" {(result.get('error') or {}).get('code')}: {_shorten((result.get('error') or {}).get('message'), 120)}"
        print(f"  -> {'ok' if ok else '失败'}{detail}", flush=True)
        if name not in query_tools:
            _fit(sw, target, args.view)
            if args.delay > 0:
                time.sleep(args.delay)
        if args.pause:
            input("  回车继续…")
    return executed, mismatches


def _close_all(sw: str) -> None:
    requests.post(f"{sw}/api/close-all-documents", json={}, timeout=120)


def run_text(args: argparse.Namespace) -> int:
    from planner.workflow.tools import WorkflowSession

    report = json.loads(Path(args.result).read_text(encoding="utf-8"))
    rows = [r for r in report["results"] if r["case_id"] == args.case]
    if not rows:
        raise SystemExit(f"结果里没有 {args.case}")
    row = rows[min(args.run, len(rows) - 1)]
    workflow = ((row.get("trace") or {}).get("response") or {}).get("workflow") or {}
    steps = build_steps(workflow.get("llm_responses"), workflow.get("tool_calls") or [])
    print(f"重放 {args.case}（{report.get('model')}，{len(steps)} 步，评测得分 v2={row.get('score')}）", flush=True)
    _close_all(args.sw)
    if report.get("action_level") == "L1_v2":
        from planner.workflow.tools_v2 import WorkflowSessionV2 as WorkflowSession
    session = WorkflowSession(base_url=args.sw)
    session.start_part()
    _fit(args.sw, "part", args.view)
    executed, mismatches = replay(steps, session, sw=args.sw, target="part", query_tools=TEXT_QUERY_TOOLS, args=args)
    state = session.state_summary()
    original = (workflow.get("final_state") or {})
    print(f"完成：执行 {executed} 个调用，成败与评测不一致 {mismatches} 个；体积 {state.get('volume_mm3')} mm³"
          f"（评测时 {original.get('volume_mm3')}），包围盒 {(state.get('bbox_mm') or {}).get('size')}", flush=True)
    _fit(args.sw, "part", args.view)
    if args.save_as:
        Path(args.save_as).parent.mkdir(parents=True, exist_ok=True)
        requests.post(f"{args.sw}/api/save-part-as", json={"savePath": str(Path(args.save_as).resolve())}, timeout=120)
    return 0 if mismatches == 0 else 1


def _animate(session, spec: dict, args: argparse.Namespace) -> None:
    motion = spec.get("motion") or {}
    if motion.get("mode") == "poses":
        from geomcheck.assembly_motion import run_poses

        print("多位姿测例：依次摆到评测用的位姿（临时配合，摆完删除）", flush=True)
        result = run_poses(session, motion)
        print(f"  位姿通过 {sum(1 for p in result.get('poses', []) if p.get('ok'))}/{len(result.get('poses', []))}", flush=True)
        return
    from planner.assembly_workflow.refs import resolve_on_faces

    role = motion["driver"]
    driver = session.aliases.get(role) or session.resolve_name(role)
    ref = resolve_on_faces("cyl(i=0)", session.faces(driver))
    duration = abs(args.sweep_deg) / (args.rpm * 6)
    session.asmwf("motion-drive", {"component": driver, "ref": ref, "rpm": args.rpm, "durationSeconds": duration})
    print(f"在 {role}（{driver}）上加旋转马达 {args.rpm} rpm，播放 {duration:.1f} s 的 Motion Study", flush=True)
    _fit(args.sw, "assembly", args.view)
    requests.post(f"{args.sw}/api/assembly/motion/play", json={}, timeout=60)
    deadline = time.time() + duration * 3 + 30
    while time.time() < deadline:
        time.sleep(1)
        try:
            status = requests.get(f"{args.sw}/api/assembly/motion/status", timeout=30).json()
        except (requests.RequestException, ValueError):
            break
        if not ((status.get("result") or status).get("isPlaying")):
            break


def run_assembly(args: argparse.Namespace) -> int:
    import yaml

    from planner.assembly_workflow.session import AssemblySession
    from planner.assembly_workflow.tools import tool_names

    report = json.loads(Path(args.result).read_text(encoding="utf-8"))
    rows = [r for r in report["results"] if r["case_id"] == args.case]
    if not rows:
        raise SystemExit(f"结果里没有 {args.case}")
    row = rows[min(args.run, len(rows) - 1)]
    run_dir = Path(args.artifacts) / row["workflow"] / row["case_id"] / f"{row['condition']}_r{row['repeat']}"
    responses_path = run_dir / "llm_responses.json"
    llm_responses = json.loads(responses_path.read_text(encoding="utf-8")) if responses_path.is_file() else None
    steps = build_steps(llm_responses, row.get("tool_calls") or [])
    dest = Path(args.save_as or ROOT / "eval" / "artifacts" / "replay" / f"{row['case_id']}.SLDASM").resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"重放 {args.case}（{report.get('model')}，{len(steps)} 步；装配 rubric {row.get('assembly_score')}，"
          f"运动分 {row.get('motion_score')}）→ {dest}", flush=True)
    if llm_responses is None:
        print(f"  未找到 {responses_path}，只按工具日志重放（不显示模型文字）", flush=True)

    def before_call(name: str, call_args: dict) -> dict:
        # 不覆盖评测产物：保存一律写到重放用的路径
        return {k: v for k, v in call_args.items() if k != "path"} if name == "save_assembly" else call_args

    _close_all(args.sw)
    session = AssemblySession(base_url=args.sw, workflow=row["workflow"], allowed_tools=tool_names(row["workflow"]))
    session.start_assembly(str(dest))
    executed, mismatches = replay(steps, session, sw=args.sw, target="assembly", query_tools=ASSEMBLY_QUERY_TOOLS,
                                  args=args, before_call=before_call)
    print(f"完成：执行 {executed} 个调用，成败与评测不一致 {mismatches} 个", flush=True)
    _fit(args.sw, "assembly", args.view)
    if args.animate:
        from eval.eval_assembly_scratch import CASE_ALIASES, CASES_DIR

        spec = yaml.safe_load((CASES_DIR / f"{CASE_ALIASES[row['case_id']]}.yaml").read_text(encoding="utf-8"))
        _animate(session, spec, args)
    session.save_assembly({})
    return 0 if mismatches == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=["text", "assembly"])
    parser.add_argument("result", help="评测结果 JSON")
    parser.add_argument("--case", required=True, help="case_id")
    parser.add_argument("--run", type=int, default=0, help="同一 case 有多次运行时取第几次（从 0 起）")
    parser.add_argument("--artifacts", help="装配：eval_assembly_scratch.py 的 --output-dir（读 llm_responses.json）")
    parser.add_argument("--sw", default="http://localhost:5100")
    parser.add_argument("--delay", type=float, default=1.5, help="每个建模调用后的停顿（秒），方便录屏")
    parser.add_argument("--pause", action="store_true", help="每步后等待回车")
    parser.add_argument("--view", default="isometric",
                        help="每步后的视向：isometric/trimetric/dimetric/front/top/right/none（只缩放）/off（不动视图）")
    parser.add_argument("--show-text", action=argparse.BooleanOptionalAction, default=True,
                        help="打印模型每轮的文字与推理摘要")
    parser.add_argument("--text-chars", type=int, default=300)
    parser.add_argument("--skip-failed", action="store_true", help="跳过评测时失败的调用（见文件头说明）")
    parser.add_argument("--save-as", help="重放结果另存路径（零件 .SLDPRT / 装配 .SLDASM）")
    parser.add_argument("--animate", action="store_true", help="装配：重放后在驱动件上加马达播放 Motion Study；多位姿测例依次摆位姿")
    parser.add_argument("--rpm", type=float, default=5)
    parser.add_argument("--sweep-deg", type=float, default=720, help="动画中驱动件转过的角度")
    args = parser.parse_args()
    if args.kind == "assembly" and not args.artifacts:
        parser.error("assembly 需要 --artifacts")
    return run_text(args) if args.kind == "text" else run_assembly(args)


if __name__ == "__main__":
    raise SystemExit(main())
