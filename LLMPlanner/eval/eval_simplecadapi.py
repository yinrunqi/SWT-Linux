"""SimpleCADAPI baseline：用 LLM 写 simplecadapi 脚本完成 Workflow-CAD-Bench 的 text-to-CAD 任务。

与 Workflow-CAD 无关，只依赖 venv-scad（simplecadapi + openai）和一个 OpenAI 兼容端点。
每题每次运行：独立工作目录、独立 SimpleCAD 缓存目录（且缓存关闭）、全新对话。
LLM 可用三个工具：read_doc（读 simplecadapi 自带的 skill 文档）、run_python（在独立进程里
执行完整脚本）、finish（交卷）。交卷物是工作目录里的 model.step，复制为
eval/artifacts/<输出名>/<case>_r<k>.step，之后用 geomcheck.rubric 打分。

用法（在 LLMPlanner 目录）：
    venv-occ/bin/python -X utf8 -m geomcheck.bench tasks --output tasks.jsonl
    OPENAI_BASE_URL=http://127.0.0.1:15721/tencent/v1 OPENAI_API_KEY=ttsw-... \\
        venv-scad/bin/python -X utf8 -u eval/eval_simplecadapi.py --tasks tasks.jsonl \\
        --model claude-sonnet-5 --repeats 2 --workers 4 --output eval/simplecad_claude_sonnet_5_result.json
    venv-occ/bin/python -X utf8 -m geomcheck.rubric --artifacts eval/artifacts/simplecad_claude_sonnet_5_result \\
        --output eval/rubric_simplecad_claude_sonnet_5.json
    venv-occ/bin/python -X utf8 -m geomcheck.bench summary eval/rubric_simplecad_claude_sonnet_5.json --repeats 2
"""

from __future__ import annotations

import argparse
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
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

import simplecadapi
from openai import APIConnectionError, APIStatusError, OpenAI

LLM_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = Path(simplecadapi.__file__).resolve().parent / "skill" / "resources" / "docs" / "skill"
DOC_LIMIT = 40000
OUTPUT_LIMIT = 8000
CALL_DEADLINE_S = 1200
SANDBOX_RO = sorted({"/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc",
                     sys.prefix, sys.base_prefix})
if shutil.which("bwrap") is None:
    raise SystemExit("需要 bubblewrap（bwrap）隔离智能体脚本：sudo apt install bubblewrap")
NOISE = re.compile(r"(\x1b\[[0-9;]*m)|^\s*(\*{2,}|Step File Name|\*\* WorkSession)", re.M)
# 输入 + 推理 + 输出共用上下文窗口的模型；与 planner.workflow.agent.CONTEXT_LIMITS 一致
CONTEXT_LIMITS = {"qwen3.8-flash-next-fp8": 73728}
MIN_OUTPUT_TOKENS = 2048
OVERFLOW_STEP = 2048
_CONTEXT_OVERFLOW = re.compile(r"maximum context length is \d+ tokens.*?prompt contains at least (\d+) input tokens", re.S)


class ContextExceeded(RuntimeError):
    pass

PREAMBLE = """你是一个 CAD 建模 agent，使用 SimpleCADAPI（Python 包 simplecadapi，已安装）为一道 text-to-CAD 评测题建模。

## 评测模式（覆盖下面 skill 中与之冲突的规定）
- 没有用户可以回答问题。不要提问、不要等待确认；题面没写清的地方，选最常见的合理理解，并在代码注释里写明假设。
- 没有子代理、没有渲染评审、不需要写 REQUIREMENTS.md / BUILD_PLAN.md 等过程文件。可以借鉴 workflows/single-part-modeling.md
  的分阶段验证思路，但验证方式是在脚本里打印体积、包围盒、选中的面/边数量等几何数值并自行核对。
- 只交一个零件：单一封闭实体，单位 mm。位置和朝向不限，除非题面给了世界坐标。
- 交付物：当前工作目录下的 `model.step`。推荐写法：

```python
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    ...  # 建模
    return solid

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
```

## 工具
- read_doc(path)：读取 skill 文档（路径相对 skill 根目录，例如 `SKILL.md`、`references/docs/api/extrude_rsolid.md`）；传目录则列出内容。
- run_python(code)：把 code 作为完整脚本在独立 Python 进程中执行（不保留上次的变量），返回退出码和输出。工作目录跨调用保留。
- finish()：确认 model.step 已写好后调用，结束本题。调用次数有上限，请尽早产出可交付的 model.step，再逐步修正。

## 可用 API 页面（references/docs/api/<名称>.md）
{api_names}

下面是 SimpleCADAPI skill 的路由文件 SKILL.md 原文，以及单件建模领域文档 references/domains/part-modeling.md 原文。

===== SKILL.md =====
{skill}

===== references/domains/part-modeling.md =====
{part_modeling}
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "read_doc",
        "description": "读取 simplecadapi skill 文档或列出目录。path 相对 skill 根目录。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "run_python",
        "description": "在独立进程中执行一段完整的 Python 脚本（cwd 为本题工作目录），返回退出码、stdout、stderr。",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "交卷。要求工作目录中已存在 model.step。",
        "parameters": {"type": "object", "properties": {}}}},
]

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def system_prompt() -> str:
    api_names = " ".join(sorted(p.stem for p in (SKILL_ROOT / "references/docs/api").glob("*.md") if p.stem != "README"))
    return PREAMBLE.format(
        api_names=api_names,
        skill=(SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8"),
        part_modeling=(SKILL_ROOT / "references/domains/part-modeling.md").read_text(encoding="utf-8"),
    )


def tool_read_doc(path: str) -> str:
    target = (SKILL_ROOT / path.strip().lstrip("/")).resolve()
    if SKILL_ROOT not in target.parents and target != SKILL_ROOT:
        return "错误：路径必须在 skill 根目录内"
    if target.is_dir():
        return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir()))
    if not target.is_file():
        return f"错误：{path} 不存在"
    text = target.read_text(encoding="utf-8", errors="replace")
    return text if len(text) <= DOC_LIMIT else text[:DOC_LIMIT] + f"\n…（截断，全文 {len(text)} 字符）"


def _tail(text: str) -> str:
    text = "\n".join(line for line in text.splitlines() if not NOISE.search(line))
    text = NOISE.sub("", text)
    return text if len(text) <= OUTPUT_LIMIT else "…（前略）\n" + text[-OUTPUT_LIMIT:]


def sandbox_argv(work: Path, argv: list[str]) -> list[str]:
    """智能体脚本只能看到本题工作目录（可写）、Python 环境与系统库（只读），且断网。
    否则它会全盘搜索，读到参考答案、rubric、评测代码和其他测例的产物。"""
    work = work.resolve()
    cmd = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all",
           "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for path in SANDBOX_RO:
        if os.path.islink(path):
            cmd += ["--symlink", os.readlink(path), path]
        elif os.path.exists(path):
            cmd += ["--ro-bind", path, path]
    cmd += ["--bind", str(work), str(work), "--chdir", str(work), "--setenv", "HOME", "/tmp", "--"]
    return cmd + argv


def tool_run_python(code: str, work: Path, n: int, timeout: float, deliverable: str = "model.step") -> str:
    script = work / f"step_{n:02d}.py"
    script.write_text(code, encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "SIMPLECAD_CACHE_MODE": "off",
           "SIMPLECAD_CACHE_DIR": str(work.resolve() / ".simplecad_cache"),
           "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
    try:
        p = subprocess.run(sandbox_argv(work, [sys.executable, "-X", "utf8", script.name]), cwd=work, env=env,
                           capture_output=True, text=True, timeout=timeout)
        out = f"exit_code={p.returncode}\n--- stdout ---\n{_tail(p.stdout)}\n--- stderr ---\n{_tail(p.stderr)}"
    except subprocess.TimeoutExpired:
        out = f"超时：脚本运行超过 {timeout:.0f}s 被终止"
    step = work / deliverable
    if step.exists():
        out += f"\n[{deliverable} 存在，{step.stat().st_size} 字节，修改于 {time.strftime('%H:%M:%S', time.localtime(step.stat().st_mtime))}]"
    else:
        out += f"\n[{deliverable} 尚不存在]"
    return out


def chat(client: OpenAI, model: str, messages: list, session: str, temperature: float | None,
         reasoning_effort: str | None = None, max_tokens: int | None = None) -> object:
    kw = dict(model=model, messages=messages, tools=TOOLS, extra_headers={"X-Session-Id": session})
    if temperature is not None:
        kw["temperature"] = temperature
    if reasoning_effort:
        kw["reasoning_effort"] = reasoning_effort
    if max_tokens:
        kw["max_tokens"] = max_tokens
    attempt = 0
    while attempt < 6:
        attempt += 1
        # 网关会发保活字节，httpx 的读超时因此不会触发，只能在外面加硬截止时间
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(client.chat.completions.create, **kw)
        pool.shutdown(wait=False)
        try:
            return future.result(timeout=CALL_DEADLINE_S)
        except FuturesTimeout:
            log(f"LLM 请求超过 {CALL_DEADLINE_S}s 未返回，重试")
            continue
        except APIStatusError as e:
            if e.status_code == 400 and "temperature" in str(e).lower() and "temperature" in kw:
                kw.pop("temperature")
                continue
            limit = CONTEXT_LIMITS.get(model.rsplit("/", 1)[-1].lower())
            overflow = _CONTEXT_OVERFLOW.search(str(e)) if e.status_code == 400 and limit else None
            if overflow:
                # vLLM 报的输入长度只是下界（= 上限 + 1 − max_tokens），真实输入未知，只能逐步压缩输出上限
                # 这类 400 立即返回、不产生生成，不计入重试次数
                attempt -= 1
                current = kw.get("max_tokens") or limit
                if current <= MIN_OUTPUT_TOKENS:
                    raise ContextExceeded(f"输入超过 {limit - MIN_OUTPUT_TOKENS} token，"
                                          f"上下文上限 {limit} 已留不出 {MIN_OUTPUT_TOKENS} 输出") from None
                room = max(MIN_OUTPUT_TOKENS, current - OVERFLOW_STEP)
                log(f"上下文接近上限：输入至少 {overflow.group(1)} token，本轮 max_tokens {current} → {room}")
                kw["max_tokens"] = room
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


def run_one(client: OpenAI, task: dict, k: int, args, out_dir: Path, sys_prompt: str) -> dict:
    case = task["case"]
    stem = f"{case}_r{k}"
    work = out_dir / "work" / stem
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    session = uuid.uuid4().hex  # 上游 prompt_cache_key 最长 64 字符
    messages = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": task["prompt"]}]
    usage: dict = {}
    counts = {"read_doc": 0, "run_python": 0, "finish": 0, "no_tool": 0}
    status, error, n_script = "budget_exhausted", None, 0
    t0 = time.time()
    calls = 0
    try:
        while calls < args.max_calls:
            calls += 1
            r = chat(client, args.model, messages, session, args.temperature)
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
                messages.append({"role": "user", "content": "继续：用 run_python 建模并写出 model.step，完成后调用 finish。不要提问。"})
                continue
            done = False
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
                    result = tool_read_doc(str(a.get("path", "")))
                elif name == "run_python":
                    counts["run_python"] += 1
                    n_script += 1
                    result = tool_run_python(str(a.get("code", "")), work, n_script, args.script_timeout)
                elif name == "finish":
                    counts["finish"] += 1
                    if (work / "model.step").exists():
                        result, done = "已交卷。", True
                    else:
                        result = "错误：model.step 不存在，请先导出。"
                else:
                    result = f"错误：未知工具 {name}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
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
    record = {
        "case": case, "repeat": k, "model": args.model, "status": status, "error": error,
        "llm_calls": calls, "tool_counts": counts, "token_usage": usage,
        "elapsed_s": round(time.time() - t0, 1), "step": str(submitted) if submitted else None,
    }
    (work / "transcript.json").write_text(json.dumps({**record, "messages": messages}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    (out_dir / f"{stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"{stem:<24} {status:<16} calls={calls:<3} scripts={counts['run_python']:<3} "
        f"tokens={usage.get('total_tokens', 0):<8} {record['elapsed_s']}s step={'yes' if submitted else 'NO'}"
        + (f" err={error[:120]}" if error else ""))
    return record


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="tasks.jsonl", help="geomcheck.bench tasks 导出的 JSONL")
    ap.add_argument("--ids", nargs="*", help="只跑这些 case")
    ap.add_argument("--model", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-calls", type=int, default=40, help="每次运行的 LLM 调用上限")
    ap.add_argument("--script-timeout", type=float, default=300)
    ap.add_argument("--temperature", type=float, default=0.0, help="模型不支持时自动去掉")
    ap.add_argument("--output", required=True, help="汇总 JSON；STEP 存到 eval/artifacts/<输出文件名>/")
    ap.add_argument("--rerun", action="store_true", help="忽略已有结果重新跑")
    args = ap.parse_args()

    tasks = [json.loads(line) for line in Path(args.tasks).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.ids:
        tasks = [t for t in tasks if t["case"] in set(args.ids)]
    output = Path(args.output)
    out_dir = LLM_ROOT / "eval" / "artifacts" / output.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:15721/tencent/v1"),
                    api_key=os.environ["OPENAI_API_KEY"], timeout=900, max_retries=0)
    sys_prompt = system_prompt()

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
        futs = [pool.submit(run_one, client, t, k, args, out_dir, sys_prompt) for t, k in jobs]
        for f in as_completed(futs):
            records.append(f.result())

    records.sort(key=lambda r: (r["case"], r["repeat"]))
    tot = {k: sum(int(r["token_usage"].get(k, 0)) for r in records) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")}
    summary = {
        "model": args.model, "runs": len(records),
        "submitted": sum(1 for r in records if r["step"]),
        "finished": sum(1 for r in records if r["status"] == "finished"),
        "errors": sum(1 for r in records if r["status"] == "error"),
        "mean_llm_calls": round(sum(r["llm_calls"] for r in records) / max(1, len(records)), 1),
        "mean_elapsed_s": round(sum(r["elapsed_s"] for r in records) / max(1, len(records)), 1),
        "mean_total_tokens": round(tot["total_tokens"] / max(1, len(records))),
        "token_totals": tot,
        "credit_total": round(sum(float(r["token_usage"].get("credit", 0)) for r in records), 2),
    }
    output.write_text(json.dumps({"summary": summary, "runs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
