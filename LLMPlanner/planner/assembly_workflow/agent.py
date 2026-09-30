"""装配闭环 Agent：调用装配 workflow 工具直到 check_motion 通过后 finish。"""

from __future__ import annotations

import json
import time
from typing import Any

from planner.workflow.tools import dumps_result

from .session import AssemblySession
from .tools import TOOL_SPECS

SYSTEM_PROMPT = """你是一名资深装配工程师，正在 SolidWorks 里用工具把已有零件装配成能运动的机构。

## 原则
- 长度单位 mm，角度单位度。不要猜测插入坐标去“摆到位”；先把基准件固定，再用配合约束出位置。
- 插入零件时 at 只是大致落点，真正的同轴、贴合、中心距由 mate / mount_on_shaft / mesh_gears / crank_slider 决定。
- 齿轮啮合相位必须在加齿轮配合之前用 insert_component 的 rotate_deg 或 mesh_gears（它会自动转从动轮）处理。
- 外啮合齿轮配合 reverse=true；同轴当键用的 1:1 同步 reverse=false。
- 每次配合后看 get_assembly_state：过定义就 delete_mate 再换一种约束；欠约束就补约束。
- 做完结构后必须 check_motion。若某一帧干涉，说明中心距、相位或孔轴方向不对，改配合或重插零件，不要只保存。
- 确认运动合理、无穿模后 save_assembly，再 finish。

## 推荐顺序
1. insert 机体/底板并 ground_component。
2. 轴系：轴与孔同心 + 轴向定位（mount_on_shaft 或 revolute_joint）。
3. 套齿轮/曲柄/连杆，再 mesh_gears 或 crank_slider。
4. check_interference → check_motion → 按返回的 interference_frames / stuck 修改。
5. save_assembly → finish。
"""


def _tool_calls_payload(message: Any) -> list[dict]:
    calls = []
    for call in message.tool_calls or []:
        calls.append({
            "id": call.id,
            "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"},
        })
    return calls


class AssemblyAgent:
    def __init__(
        self,
        client: Any,
        model: str,
        session: AssemblySession,
        *,
        max_llm_calls: int = 50,
        max_tokens: int = 8000,
        temperature: float = 0.0,
        max_stalls: int = 2,
        tools: list[dict] | None = None,
    ):
        self.client = client
        self.model = model
        self.session = session
        self.tools = tools or TOOL_SPECS
        self.max_llm_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_stalls = max_stalls

    def run(self, goal: str) -> dict:
        started = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"装配需求：\n{goal}\n\n当前是空装配体，零件路径已在需求里给出，请开始装配。"},
        ]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        tool_log: list[dict] = []
        failed_signatures: dict[str, int] = {}
        status, summary, stalls, error = "max_llm_calls", "", 0, None

        for _ in range(self.max_llm_calls):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto",
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )
            except Exception as exc:
                status, error = "llm_error", f"{type(exc).__name__}: {exc}"
                break
            usage["calls"] += 1
            if response.usage is not None:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += int(getattr(response.usage, key, 0) or 0)
            message = response.choices[0].message
            calls = _tool_calls_payload(message)
            assistant = {"role": "assistant", "content": message.content or ""}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)

            if not calls:
                stalls += 1
                if stalls > self.max_stalls:
                    status = "stalled"
                    break
                messages.append({
                    "role": "user",
                    "content": "请继续调用工具推进装配；check_motion 通过并保存后调用 finish。",
                })
                continue
            stalls = 0

            finished = False
            for call in calls:
                name = call["function"]["name"]
                raw_args = call["function"]["arguments"]
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                    if not isinstance(args, dict):
                        raise ValueError("参数必须是 JSON 对象")
                except ValueError as exc:
                    result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"参数不是合法 JSON: {exc}"}}
                    args = {"_raw": raw_args}
                else:
                    result = self.session.call(name, args)
                    if name == "finish" and result.get("ok"):
                        finished = True
                        summary = str(args.get("summary", ""))
                if not result.get("ok"):
                    signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                    failed_signatures[signature] = failed_signatures.get(signature, 0) + 1
                    if failed_signatures[signature] > 1:
                        result["repeat_warning"] = "这个调用与之前失败的调用完全相同，请换一种做法。"
                tool_log.append({
                    "tool": name,
                    "args": args,
                    "ok": bool(result.get("ok")),
                    "error_code": (result.get("error") or {}).get("code"),
                    "message": (result.get("error") or {}).get("message", ""),
                })
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": dumps_result(result)})
            if finished:
                status = "finished"
                break

        return {
            "status": status,
            "summary": summary,
            "error": error,
            "llm_calls": usage["calls"],
            "tool_calls": tool_log,
            "token_usage": usage,
            "last_motion": self.session.last_motion,
            "aliases": dict(self.session.aliases),
            "saved": self.session.saved,
            "messages": messages,
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
