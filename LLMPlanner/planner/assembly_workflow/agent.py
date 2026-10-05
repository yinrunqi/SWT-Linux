"""装配闭环 Agent：调用装配 workflow 工具直到 check_motion 通过后 finish。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from planner.workflow.agent import raw_response
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

SYSTEM_PROMPT_V3 = """你是一名资深装配工程师，正在 SolidWorks 里用工具把已有零件装配成能运动的机构。

## 原则
- 长度单位 mm，角度单位度。先把基准件固定，再用配合约束出位置；插入坐标 at 只是大致落点。
- 先想关节（转动、滑动、固定、螺旋、齿轮耦合），再用 joint 等宏工具落到配合；层位用 at_z 或 offset 写明。
- 每个改结构的工具成功后都会返回 after：活动度 mobility、冗余 redundant、过定义件、悬空件。
  据此判断下一步，不必每次都调 get_assembly_state；insert_component 已返回该零件的面摘要。
- 过定义时先想清楚是哪条约束重复了，不要反复删了再加同一种配合；check_dof 的 free 会指出哪个零件多了自由度。
- 齿轮啮合相位在加齿轮配合之前处理（rotate_deg 或 mesh_gears）。外啮合 reverse=true。

## 验证与收尾
- 整周回转的机构：check_motion 驱动输入件一整圈，看 interference_pairs、stuck。
- 有限行程、多自由度或直线输入的机构：check_pose 把机构摆到几个典型位形，核对关键点与干涉；
  再 check_dof(expected_mobility=设计活动度)。
- 每条工具结果里有剩余调用次数；预算不多时先 save_assembly，保存的结果才会被评估。
- 验证通过后 save_assembly，再 finish。改了结构要重新验证。
"""

STATE_PLACEHOLDER = '{"ok": true, "note": "较早的状态快照已省略，以后面最新一次为准"}'

# 续跑重放时跳过的只读工具；check_motion / check_pose / check_dof 会更新 finish 的门槛状态，要重放
NO_REPLAY_TOOLS = {"get_assembly_state", "check_interference", "finish"}
RESUMABLE_STATUSES = {"running", "llm_error", "sw_lost"}
ENVIRONMENT_ERRORS = {"NO_PART", "SW_UNAVAILABLE"}


def _environment_lost(result: dict) -> bool:
    error = result.get("error") or {}
    return error.get("code") in ENVIRONMENT_ERRORS or "没有活动的装配体文档" in str(error.get("message") or "")


def _tool_calls_payload(message: Any) -> list[dict]:
    calls = []
    for call in message.tool_calls or []:
        calls.append({
            "id": call.id,
            "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"},
        })
    return calls


def _all_args_parse(calls: list[dict]) -> bool:
    if not calls:
        return False
    for call in calls:
        raw = (call["function"]["arguments"] or "").strip()
        if not raw:
            return False
        try:
            if not isinstance(json.loads(raw), dict):
                return False
        except ValueError:
            return False
    return True


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
        reasoning_effort: str | None = None,
        checkpoint_path: str | Path | None = None,
    ):
        self.client = client
        self.model = model
        self.session = session
        self.tools = tools or TOOL_SPECS
        self.max_llm_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_stalls = max_stalls
        self.reasoning_effort = reasoning_effort
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    def run(self, goal: str, *, resume: bool = False) -> dict:
        """设置了 checkpoint_path 时，每次拿到 LLM 回复、每轮工具执行完都把对话与中间结果落盘；
        resume=True 且存在未完成的 checkpoint 时，在当前空装配体上按顺序重放已执行的工具，
        再接着原对话继续，已拿到但还没执行的回复直接使用，不重新请求。"""
        started = time.perf_counter()
        v3 = bool(getattr(self.session, "v3", False))
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT_V3 if v3 else SYSTEM_PROMPT},
            {"role": "user", "content": f"装配需求：\n{goal}\n\n当前是空装配体，零件路径已在需求里给出，请开始装配。"},
        ]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        tool_log: list[dict] = []
        llm_responses: list[dict] = []
        finish_reasons: list[str | None] = []
        failed_signatures: dict[str, int] = {}
        status, summary, stalls, error = "max_llm_calls", "", 0, None
        max_tokens, truncations = self.max_tokens, 0
        state_messages: list[int] = []
        pending: dict | None = None
        prior_seconds, resumed = 0.0, None
        # 未指定时不发送该字段，保持与历史结果相同的请求体。
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}

        saved = self._load_checkpoint(goal) if resume else None
        done = False
        if saved:
            messages, usage, tool_log = saved["messages"], saved["usage"], saved["tool_log"]
            llm_responses, finish_reasons = saved["llm_responses"], saved.get("finish_reasons", [])
            failed_signatures, stalls = saved["failed_signatures"], saved["stalls"]
            truncations, max_tokens = saved["truncations"], saved["max_tokens"]
            state_messages, pending = saved.get("state_messages", []), saved.get("pending")
            prior_seconds = float(saved.get("duration_seconds") or 0)
            mismatch = self._replay(tool_log)
            resumed = {"from_llm_calls": usage["calls"], "replayed_tools": len(tool_log), "mismatch": mismatch}
            if mismatch:
                status, error, pending, done = "resume_failed", mismatch, None, True
            elif saved.get("status") not in RESUMABLE_STATUSES:
                # 上次已经正常结束，只是结果没送回评测端：恢复装配体后直接返回原结果
                status, summary, error, pending, done = saved["status"], saved["summary"], saved["error"], None, True

        def checkpoint(current_status: str = "running") -> None:
            self._save_checkpoint({
                "goal": goal, "model": self.model, "status": current_status, "summary": summary, "error": error,
                "messages": messages, "usage": usage, "tool_log": tool_log, "llm_responses": llm_responses,
                "finish_reasons": finish_reasons, "failed_signatures": failed_signatures, "stalls": stalls,
                "truncations": truncations, "max_tokens": max_tokens, "state_messages": state_messages,
                "pending": pending, "resumed": resumed, "reasoning_effort": self.reasoning_effort,
                "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
            })

        while not done and (pending is not None or usage["calls"] < self.max_llm_calls):
            if pending is None:
                if v3 and len(state_messages) > 1:
                    for index in state_messages[:-1]:
                        messages[index]["content"] = STATE_PLACEHOLDER
                    state_messages = state_messages[-1:]
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        tools=self.tools,
                        tool_choice="auto",
                        temperature=self.temperature,
                        max_tokens=max_tokens,
                        **extra,
                    )
                except Exception as exc:
                    status, error = "llm_error", f"{type(exc).__name__}: {exc}"
                    break
                llm_responses.append(raw_response(response))
                usage["calls"] += 1
                if response.usage is not None:
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        usage[key] += int(getattr(response.usage, key, 0) or 0)
                choice = response.choices[0]
                finish_reasons.append(getattr(choice, "finish_reason", None))
                pending = {"finish_reason": getattr(choice, "finish_reason", None),
                           "content": choice.message.content or "",
                           "tool_calls": _tool_calls_payload(choice.message)}
                checkpoint()
            reply, pending = pending, None
            calls = reply["tool_calls"]
            # v3：输出被截断（常见于长篇推理）不算停滞；丢掉残缺回复，放宽长度再要一次，并提示直接给工具调用
            if v3 and reply["finish_reason"] == "length" and not _all_args_parse(calls):
                truncations += 1
                max_tokens = min(max(max_tokens, self.max_tokens) * 2, 32000)
                if truncations == 1:
                    messages.append({"role": "user", "content": "上一条回复太长被截断了。少写分析，直接输出下一步的工具调用。"})
                checkpoint()
                continue
            max_tokens = self.max_tokens
            round_start = (len(messages), len(tool_log), dict(failed_signatures), list(state_messages))
            assistant = {"role": "assistant", "content": reply["content"]}
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
                    "content": ("请继续调用工具推进装配；check_motion（或 check_pose + check_dof）通过并保存后调用 finish。"
                                if v3 else "请继续调用工具推进装配；check_motion 通过并保存后调用 finish。"),
                })
                checkpoint()
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
                    result = {"ok": False, "error": {
                        "code": "TOOL_ARGS", "message": f"参数不是合法 JSON: {exc}；收到的是: {raw_args[:300]}"}}
                    args = {"_raw": raw_args}
                    # 网关要把历史里的 arguments 解析成对象，非法 JSON 留在历史里会让之后每次请求都 400
                    call["function"]["arguments"] = "{}"
                else:
                    result = self.session.call(name, args)
                    if name == "finish" and result.get("ok"):
                        finished = True
                        summary = str(args.get("summary", ""))
                if _environment_lost(result):
                    # 装配文档或 SolidWorks 服务丢了，不是模型的错：撤回这一轮，留给 resume 重放后重做
                    messages[round_start[0]:] = []
                    del tool_log[round_start[1]:]
                    failed_signatures, state_messages = round_start[2], round_start[3]
                    status, error, pending = "sw_lost", f"{name}: {result['error'].get('message')}", reply
                    break
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
                if v3 and call is calls[-1]:
                    remaining = self.max_llm_calls - usage["calls"]
                    result["budget"] = {"remaining_llm_calls": remaining}
                    if remaining <= 5 and not self.session.saved:
                        result["budget"]["warning"] = "调用预算将尽：先 save_assembly 保存当前结果，再继续修改。"
                if v3 and name == "get_assembly_state" and result.get("ok"):
                    state_messages.append(len(messages))
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": dumps_result(result)})
            if status == "sw_lost":
                break
            if finished:
                status = "finished"
                break
            checkpoint()

        if status != "resume_failed":
            checkpoint(status)
        return {
            "status": status,
            "summary": summary,
            "error": error,
            "truncations": truncations,
            "llm_calls": usage["calls"],
            "reasoning_effort": self.reasoning_effort,
            "finish_reasons": finish_reasons,
            "tool_calls": tool_log,
            "token_usage": usage,
            "last_motion": self.session.last_motion,
            "aliases": dict(self.session.aliases),
            "saved": self.session.saved,
            "messages": messages,
            "llm_responses": llm_responses,
            "resumed": resumed,
            "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
        }

    def _load_checkpoint(self, goal: str) -> dict | None:
        if not self.checkpoint_path or not self.checkpoint_path.is_file():
            return None
        saved = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if saved.get("goal") != goal or saved.get("model") != self.model:
            raise ValueError(f"checkpoint {self.checkpoint_path} 属于另一个需求或模型，不能续跑")
        return saved

    def _save_checkpoint(self, state: dict) -> None:
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"version": 1, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), **state}
        tmp = self.checkpoint_path.with_name(self.checkpoint_path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.checkpoint_path)

    def _replay(self, tool_log: list[dict]) -> str | None:
        """按原顺序重放改动装配体的工具（失败的也重放，保证组件与配合的自动编号一致）；结果与记录不符时返回说明。"""
        for index, item in enumerate(tool_log, 1):
            if item["tool"] in NO_REPLAY_TOOLS or "_raw" in item["args"]:
                continue
            result = self.session.call(item["tool"], item["args"])
            code = (result.get("error") or {}).get("code")
            if bool(result.get("ok")) != item["ok"] or code != item["error_code"]:
                return (f"重放第 {index} 个工具调用 {item['tool']} 时结果与记录不一致："
                        f"记录 ok={item['ok']} code={item['error_code']}，重放 ok={bool(result.get('ok'))} code={code}")
        return None
