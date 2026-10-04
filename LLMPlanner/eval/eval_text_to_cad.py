"""text-to-cad baseline：用 earthtojake/text-to-cad 的 CAD skill（cadgen + build123d）完成 Workflow-CAD-Bench 的 text-to-CAD 任务。

与 Workflow-CAD 无关，只依赖 venv-t2c（skills/cad/requirements.txt 锁定的 cadgen + openai）和一个 OpenAI 兼容端点。
skill 原文取自 third_party/text-to-cad/skills/cad（--skill-dir 可改），系统提示 = 评测模式说明 + SKILL.md
+ references/build123d-modeling.md，其余 references 由 LLM 按需 read_doc。
每题每次运行：独立工作目录、独立 cadgen store（CADGEN_CACHE_DIR）、不用常驻 daemon、全新对话。
LLM 可用工具：read_doc、run_python（写入工作目录内的脚本并在独立进程里执行）、snapshot（cadgen step snapshot
渲染 model.step 并把图片回传，--no-snapshots 关闭）、finish。交卷物是工作目录里的 model.step，复制为
eval/artifacts/<输出名>/<case>_r<k>.step，之后用 geomcheck.rubric 打分。

用法（在 LLMPlanner 目录）：
    venv-occ/bin/python -X utf8 -m geomcheck.bench tasks --output tasks.jsonl
    OPENAI_BASE_URL=http://127.0.0.1:15721/tencent/v1 OPENAI_API_KEY=ttsw-... \\
        venv-t2c/bin/python -X utf8 -u eval/eval_text_to_cad.py --tasks tasks.jsonl \\
        --model claude-sonnet-5 --repeats 2 --workers 4 --output eval/t2c_claude_sonnet_5_result.json
    venv-occ/bin/python -X utf8 -m geomcheck.rubric --artifacts eval/artifacts/t2c_claude_sonnet_5_result \\
        --output eval/rubric_t2c_claude_sonnet_5.json
    venv-occ/bin/python -X utf8 -m geomcheck.bench summary eval/rubric_t2c_claude_sonnet_5.json --repeats 2
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import APIConnectionError, APIStatusError, OpenAI

LLM_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILL = LLM_ROOT / "third_party" / "text-to-cad" / "skills" / "cad"
VENV_BIN = Path(sys.executable).parent
DOC_LIMIT = 40000
OUTPUT_LIMIT = 8000
PROGRESS = re.compile(r'^\{"model":.*"state":"building".*\}$', re.M)

PREAMBLE = """你是一个 CAD 建模 agent，使用 text-to-cad 的 CAD skill（Python 包 cadgen + build123d，已安装）为一道 text-to-CAD 评测题建模。

## 评测模式（覆盖下面 skill 中与之冲突的规定）
- 没有用户可以回答问题。不要提问、不要等待确认；题面没写清的地方，选最常见的合理理解，并在代码注释里写明假设。
- 没有 CAD Viewer、cad_show、$step-parts 或子代理；不要启动 `cadgen viewer`。不需要 README、模型目录等项目文件。
  “Show the model” 一节不适用；snapshot 工具就是你的视觉自查手段{snapshot_note}。
- 只交一个零件：单一封闭实体，单位 mm。位置和朝向不限，除非题面给了世界坐标。
- 交付物：工作目录下的 `model.step`，由工作目录下的模型脚本 `model.py` 生成，即
  `@step(out="model.step")` 装饰的无参函数 + `if __name__ == "__main__": <函数>()`。
  几何检查脚本放在 `tmp/` 下，用 `read_step("model.step")` 或 `read_scene` 读已保存的 STEP 再打印体积、包围盒、面/边数量等。

## 工具
- read_doc(path)：读取 skill 文档（路径相对 skill 根目录，例如 `references/inspection-and-validation.md`）；传目录则列出内容。
- run_python(file, code)：把 code 写入工作目录下的 file（如 `model.py`、`tmp/check.py`），再以 `python <file>` 在独立进程中执行，
  返回退出码和输出。工作目录跨调用保留；`cadgen` 命令在 PATH 上，可在脚本里用 subprocess 调用。
{snapshot_tool}- finish()：确认 model.step 已由最新的 model.py 生成后调用，结束本题。调用次数有上限，请尽早产出可交付的 model.step，再逐步修正。

## references/ 下可读文档
{ref_names}

下面是 CAD skill 的路由文件 SKILL.md 原文，以及 references/build123d-modeling.md 原文。

===== SKILL.md =====
{skill}

===== references/build123d-modeling.md =====
{modeling}
"""

SNAPSHOT_TOOL_DOC = ("- snapshot(camera)：用 `cadgen step snapshot` 渲染当前 model.step，图片会作为下一条用户消息回传。"
                     "camera 为 front/back/left/right/top/bottom/iso 或 \"方位角:仰角\"，缺省 iso。\n")

TOOLS = [
    {"type": "function", "function": {
        "name": "read_doc",
        "description": "读取 CAD skill 文档或列出目录。path 相对 skill 根目录。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "把 code 写入工作目录下的 file 并以 `python <file>` 在独立进程中执行（cwd 为本题工作目录），"
                       "返回退出码、stdout、stderr。模型脚本用 model.py，检查脚本用 tmp/*.py。",
        "parameters": {"type": "object", "properties": {"file": {"type": "string"}, "code": {"type": "string"}},
                       "required": ["file", "code"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "交卷。要求工作目录中已存在 model.step。",
        "parameters": {"type": "object", "properties": {}}}},
]
SNAPSHOT_SPEC = {"type": "function", "function": {
    "name": "snapshot",
    "description": "渲染工作目录中的 model.step，图片在下一条消息回传。",
    "parameters": {"type": "object", "properties": {"camera": {"type": "string"}}}}}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def system_prompt(skill_root: Path, snapshots: bool) -> str:
    refs = " ".join(sorted(p.name for p in (skill_root / "references").glob("*.md")))
    return PREAMBLE.format(
        snapshot_note="" if snapshots else "（本次评测已关闭 snapshot，改用数值检查自查）",
        snapshot_tool=SNAPSHOT_TOOL_DOC if snapshots else "",
        ref_names=refs,
        skill=(skill_root / "SKILL.md").read_text(encoding="utf-8"),
        modeling=(skill_root / "references" / "build123d-modeling.md").read_text(encoding="utf-8"),
    )


def tool_read_doc(skill_root: Path, path: str) -> str:
    target = (skill_root / path.strip().lstrip("/")).resolve()
    if skill_root not in target.parents and target != skill_root:
        return "错误：路径必须在 skill 根目录内"
    if target.is_dir():
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir()))
    if not target.is_file():
        return f"错误：{path} 不存在"
    text = target.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= DOC_LIMIT else text[:DOC_LIMIT] + f"\n…（截断，全文 {len(text)} 字符）"


def _tail(text: str) -> str:
    text = PROGRESS.sub("", text)
    text = "\n".join(line for line in text.splitlines() if line.strip())
    return text if len(text) <= OUTPUT_LIMIT else "…（前略）\n" + text[-OUTPUT_LIMIT:]


def _env(work: Path) -> dict:
    return dict(os.environ, CADGEN_DAEMON="0", CADGEN_CACHE_DIR=str(work / ".cadgen_cache"),
                PATH=f"{VENV_BIN}{os.pathsep}{os.environ.get('PATH', '')}",
                PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")


def _step_status(work: Path) -> str:
    step = work / "model.step"
    if not step.exists():
        return "\n[model.step 尚不存在]"
    mtime = time.strftime("%H:%M:%S", time.localtime(step.stat().st_mtime))
    return f"\n[model.step 存在，{step.stat().st_size} 字节，修改于 {mtime}]"


def tool_run_python(file: str, code: str, work: Path, timeout: float) -> str:
    script = (work / file.strip().lstrip("/")).resolve()
    if work not in script.parents or script.suffix != ".py":
        return "错误：file 必须是工作目录内的 .py 路径"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(code, encoding="utf-8")
    try:
        p = subprocess.run([sys.executable, "-X", "utf8", str(script.relative_to(work))], cwd=work, env=_env(work),
                           capture_output=True, text=True, timeout=timeout)
        out = f"exit_code={p.returncode}\n--- stdout ---\n{_tail(p.stdout)}\n--- stderr ---\n{_tail(p.stderr)}"
    except subprocess.TimeoutExpired:
        out = f"超时：脚本运行超过 {timeout:.0f}s 被终止"
    return out + _step_status(work)


def tool_snapshot(camera: str, work: Path, n: int, timeout: float) -> tuple[str, str | None]:
    if not (work / "model.step").exists():
        return "错误：model.step 不存在，先运行 model.py。", None
    out = work / "tmp" / f"snapshot_{n:02d}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(VENV_BIN / "cadgen"), "step", "snapshot", "model.step", str(out.relative_to(work)),
           "--camera", camera or "iso", "--width", "768", "--height", "576"]
    try:
        p = subprocess.run(cmd, cwd=work, env=_env(work), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"超时：snapshot 超过 {timeout:.0f}s", None
    if p.returncode != 0 or not out.exists():
        return f"snapshot 失败 exit_code={p.returncode}\n{_tail(p.stdout + p.stderr)}", None
    return f"已渲染 {out.name}（camera={camera or 'iso'}），图片见下一条消息。", base64.b64encode(out.read_bytes()).decode()


def chat(client: OpenAI, model: str, messages: list, tools: list, session: str, temperature: float | None) -> object:
    kw = dict(model=model, messages=messages, tools=tools, extra_headers={"X-Session-Id": session})
    if temperature is not None:
        kw["temperature"] = temperature
    for attempt in range(6):
        try:
            return client.chat.completions.create(**kw)
        except APIStatusError as e:
            if e.status_code == 400 and "temperature" in str(e).lower() and "temperature" in kw:
                kw.pop("temperature")
                continue
            if e.status_code in (408, 409, 429) or e.status_code >= 500:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            raise
        except APIConnectionError:
            time.sleep(min(60, 5 * 2 ** attempt))
    raise RuntimeError("LLM 请求重试 6 次仍失败")


def _usage_add(total: dict, usage) -> None:
    if not usage:
        return
    u = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[k] = total.get(k, 0) + int(u.get(k) or 0)
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
    total["cached_tokens"] = total.get("cached_tokens", 0) + int(cached)
    if u.get("credit") is not None:
        total["credit"] = round(total.get("credit", 0.0) + float(u["credit"]), 4)


def _strip_images(messages: list) -> list:
    out = []
    for m in messages:
        if isinstance(m.get("content"), list):
            m = {**m, "content": [c if c.get("type") != "image_url" else {"type": "text", "text": "[snapshot 图片已省略]"}
                                  for c in m["content"]]}
        out.append(m)
    return out


def run_one(client: OpenAI, task: dict, k: int, args, out_dir: Path, sys_prompt: str, tools: list) -> dict:
    case = task["case"]
    stem = f"{case}_r{k}"
    work = (out_dir / "work" / stem).resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    session = f"t2c-{stem}-{uuid.uuid4()}"
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": task["prompt"]}]
    usage: dict = {}
    counts = {"read_doc": 0, "run_python": 0, "snapshot": 0, "finish": 0, "no_tool": 0}
    status, error = "budget_exhausted", None
    t0 = time.time()
    calls = 0
    try:
        while calls < args.max_calls:
            calls += 1
            r = chat(client, args.model, messages, tools, session, args.temperature)
            _usage_add(usage, r.usage)
            msg = r.choices[0].message
            entry = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                entry["tool_calls"] = [{"id": tc.id, "type": "function",
                                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                       for tc in msg.tool_calls]
            messages.append(entry)
            if not msg.tool_calls:
                counts["no_tool"] += 1
                messages.append({"role": "user", "content": "继续：用 run_python 写 model.py 并生成 model.step，完成后调用 finish。不要提问。"})
                continue
            done, images = False, []
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    a = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    a = None
                if a is None:
                    result = "错误：参数不是合法 JSON"
                elif name == "read_doc":
                    counts["read_doc"] += 1
                    result = tool_read_doc(args.skill_dir, str(a.get("path", "")))
                elif name == "run_python":
                    counts["run_python"] += 1
                    result = tool_run_python(str(a.get("file", "model.py")), str(a.get("code", "")), work,
                                             args.script_timeout)
                elif name == "snapshot" and not args.no_snapshots:
                    counts["snapshot"] += 1
                    result, img = tool_snapshot(str(a.get("camera") or "iso"), work, counts["snapshot"],
                                                args.script_timeout)
                    if img:
                        images.append(img)
                elif name == "finish":
                    counts["finish"] += 1
                    if (work / "model.step").exists():
                        result, done = "已交卷。", True
                    else:
                        result = "错误：model.step 不存在，请先运行 model.py。"
                else:
                    result = f"错误：未知工具 {name}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            if images and not done:
                messages.append({"role": "user", "content": [{"type": "text", "text": "snapshot 渲染结果："}] + [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b}"}} for b in images]})
            if done:
                status = "finished"
                break
    except Exception as e:  # noqa: BLE001
        status, error = "error", f"{type(e).__name__}: {e}"
    step = work / "model.step"
    submitted = None
    if step.exists():
        submitted = out_dir / f"{stem}.step"
        shutil.copyfile(step, submitted)
    shutil.rmtree(work / ".cadgen_cache", ignore_errors=True)
    record = {
        "case": case, "repeat": k, "model": args.model, "status": status, "error": error,
        "llm_calls": calls, "tool_counts": counts, "token_usage": usage,
        "elapsed_s": round(time.time() - t0, 1), "step": str(submitted) if submitted else None,
    }
    (work / "transcript.json").write_text(
        json.dumps({**record, "messages": _strip_images(messages)}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{stem:<24} {status:<16} calls={calls:<3} scripts={counts['run_python']:<3} snaps={counts['snapshot']:<2} "
        f"tokens={usage.get('total_tokens', 0):<8} {record['elapsed_s']}s step={'yes' if submitted else 'NO'}"
        + (f" err={error[:120]}" if error else ""))
    return record


def _skill_revision(skill_dir: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(skill_dir), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="tasks.jsonl", help="geomcheck.bench tasks 导出的 JSONL")
    ap.add_argument("--ids", nargs="*", help="只跑这些 case")
    ap.add_argument("--model", required=True)
    ap.add_argument("--skill-dir", type=Path, default=DEFAULT_SKILL, help="text-to-cad 的 skills/cad 目录")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=40, help="每次运行的 LLM 调用上限")
    ap.add_argument("--script-timeout", type=float, default=300)
    ap.add_argument("--temperature", type=float, default=0.0, help="模型不支持时自动去掉")
    ap.add_argument("--no-snapshots", action="store_true", help="不提供 snapshot 工具（非多模态模型或与无渲染基线对齐时用）")
    ap.add_argument("--output", required=True, help="汇总 JSON；STEP 存到 eval/artifacts/<输出文件名>/")
    ap.add_argument("--rerun", action="store_true", help="忽略已有结果重新跑")
    args = ap.parse_args()
    args.skill_dir = args.skill_dir.resolve()

    tasks = [json.loads(line) for line in Path(args.tasks).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        tasks = [t for t in tasks if t["case"] in set(args.ids)]
    output = Path(args.output)
    out_dir = LLM_ROOT / "eval" / "artifacts" / output.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:15721/tencent/v1"),
                    api_key=os.environ["OPENAI_API_KEY"], timeout=900, max_retries=0)
    sys_prompt = system_prompt(args.skill_dir, not args.no_snapshots)
    tools = TOOLS if args.no_snapshots else TOOLS[:2] + [SNAPSHOT_SPEC] + TOOLS[2:]

    jobs, records = [], []
    for t in tasks:
        for k in range(1, args.repeats + 1):
            done = out_dir / f"{t['case']}_r{k}.json"
            if done.exists() and not args.rerun:
                records.append(json.loads(done.read_text(encoding="utf-8")))
            else:
                jobs.append((t, k))
    log(f"model={args.model} tasks={len(tasks)} runs={len(jobs)} (已有 {len(records)}) -> {out_dir}")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(run_one, client, t, k, args, out_dir, sys_prompt, tools) for t, k in jobs]
        for f in as_completed(futs):
            records.append(f.result())

    records.sort(key=lambda r: (r["case"], r["repeat"]))
    tot = {k: sum(int(r["token_usage"].get(k, 0)) for r in records) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")}
    summary = {
        "model": args.model, "runs": len(records),
        "skill_revision": _skill_revision(args.skill_dir), "snapshots": not args.no_snapshots,
        "submitted": sum(1 for r in records if r["step"]),
        "finished": sum(1 for r in records if r["status"] == "finished"),
        "errors": sum(1 for r in records if r["status"] == "error"),
        "mean_llm_calls": round(sum(r["llm_calls"] for r in records) / max(1, len(records)), 1),
        "mean_snapshots": round(sum(r["tool_counts"].get("snapshot", 0) for r in records) / max(1, len(records)), 1),
        "mean_elapsed_s": round(sum(r["elapsed_s"] for r in records) / max(1, len(records)), 1),
        "mean_total_tokens": round(tot["total_tokens"] / max(1, len(records))),
        "token_totals": tot,
        "credit_total": round(sum(float(r["token_usage"].get("credit", 0)) for r in records), 2),
    }
    output.write_text(json.dumps({"summary": summary, "runs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
