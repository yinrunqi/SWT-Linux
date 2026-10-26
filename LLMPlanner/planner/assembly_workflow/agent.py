"""装配闭环 Agent：调用装配 workflow 工具直到 check_motion 通过后 finish。"""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from planner.workflow.agent import (
    CONTEXT_LIMITS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MIN_OUTPUT_TOKENS,
    _image_part,
    _model_key,
    _without_images,
    complete_request,
    complete_within_context,
    default_reasoning_effort,
    raw_response,
)
from planner.workflow.tools import IMAGE_KEY, dumps_result

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
NO_REPLAY_TOOLS = {"get_assembly_state", "check_interference", "view_assembly", "finish"}
IMAGE_PLACEHOLDER = "（此前的截图已省略，只保留最新一张）"
IMAGE_CAPTION = "view_assembly：SolidWorks 中当前装配体的截图如下，每格上方标有视向与坐标方向。"
RESUMABLE_STATUSES = {"running", "llm_error", "sw_lost"}
ENVIRONMENT_ERRORS = {"NO_PART", "SW_UNAVAILABLE"}
ASM_COMPACT_DISCIPLINE = (
    "较早的装配工具历史已压缩为 ledger，完整历史仍保存在 checkpoint。"
    "请依据 ledger、最近完整轮次和当前状态直接调用工具推进，不要复述历史。"
)
SPEED_OPTIMIZATION_DISCIPLINE = """

## 加速执行规则
- 每轮都会附带增量维护的 `current_state_digest`；它比旧 ledger 更新，优先据此判断当前结构。
- 同一轮可以批量给出彼此独立的工具调用，例如多个不同零件的 insert_component，或互不依赖的初始放置。
- 工具仍按你给出的顺序执行。mate/delete/check/finish 等依赖前一步结果的操作不要盲目批量或重排。
- check_motion 在构建期使用低帧数快速预检；最终评测会独立使用完整帧数，不要为此重复调用。
"""
_ASM_LEDGER_KEYS = (
    "name", "alias", "component", "created", "deleted", "saved", "path", "after",
    "mobility", "redundant", "floating", "overdefined", "free", "passed", "stuck",
    "interference_pairs", "interference_frames", "score", "warnings",
)


def _compact_assembly_tool_result(tool: str, content: Any) -> Any:
    """把旧装配工具结果压成足以继续规划的 ledger；最近轮次仍保留完整结果。"""
    if not isinstance(content, str) or not content.startswith("{"):
        return content
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(payload, dict):
        return content
    compact: dict[str, Any] = {"ledger": True, "tool": tool, "ok": bool(payload.get("ok"))}
    if not payload.get("ok"):
        error = payload.get("error") or {}
        compact["error"] = {
            key: copy.deepcopy(error[key])
            for key in ("code", "message", "hint", "rolled_back")
            if key in error
        }
        if payload.get("repeat_warning"):
            compact["repeat_warning"] = payload["repeat_warning"]
    elif tool == "get_assembly_state":
        compact["note"] = "旧状态快照已省略；以后续 ledger 和最近状态为准"
    else:
        for key in _ASM_LEDGER_KEYS:
            if key in payload:
                compact[key] = copy.deepcopy(payload[key])
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def compact_assembly_history(
    messages: list[dict],
    *,
    keep_recent_rounds: int = 2,
    drop_old_rounds: bool = False,
) -> list[dict]:
    """构造请求副本：旧工具结果改为 ledger，旧 assistant 分析清空，主历史不变。"""
    out = copy.deepcopy(messages)
    call_to_tool: dict[str, str] = {}
    rounds: list[set[str]] = []
    assistant_calls: list[tuple[dict, set[str]]] = []
    for message in out:
        if message.get("role") != "assistant":
            continue
        ids: set[str] = set()
        for call in message.get("tool_calls") or []:
            call_id = str(call.get("id") or "")
            function = call.get("function") or {}
            if call_id:
                ids.add(call_id)
                call_to_tool[call_id] = str(function.get("name") or "unknown")
        if ids:
            rounds.append(ids)
            assistant_calls.append((message, ids))
    kept_ids = set().union(*rounds[-keep_recent_rounds:]) if rounds and keep_recent_rounds > 0 else set()
    if drop_old_rounds:
        # emergency 模式必须有严格上界：旧轮次即使每条只剩 ledger，百轮后仍会挤满上下文。
        # 仅保留 system、最初需求，以及最近完整工具轮次；精确当前状态由调用方另行附加。
        first_user_kept = False
        bounded = []
        for message in out:
            role = message.get("role")
            if role == "system":
                bounded.append(message)
            elif role == "user" and not first_user_kept:
                bounded.append(message)
                first_user_kept = True
            elif role == "assistant":
                ids = {
                    str(call.get("id") or "")
                    for call in message.get("tool_calls") or []
                    if call.get("id")
                }
                if ids & kept_ids:
                    bounded.append(message)
            elif role == "tool" and str(message.get("tool_call_id") or "") in kept_ids:
                bounded.append(message)
        out = bounded
        out.append({"role": "user", "content": ASM_COMPACT_DISCIPLINE})
        return out
    for message, ids in assistant_calls:
        if ids.isdisjoint(kept_ids):
            message["content"] = ""
    for message in out:
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        if call_id not in kept_ids:
            message["content"] = _compact_assembly_tool_result(
                call_to_tool.get(call_id, "unknown"), message.get("content"),
            )
    out.append({"role": "user", "content": ASM_COMPACT_DISCIPLINE})
    return out


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
        max_llm_calls: int = 100,
        max_tokens: int = 16000,
        temperature: float = 0.0,
        max_stalls: int = 2,
        tools: list[dict] | None = None,
        reasoning_effort: str | None = None,
        checkpoint_path: str | Path | None = None,
        view_tool: bool = True,
        stream: bool = False,
        log: Callable[[str], None] | None = None,
        context_limit: int | None = None,
        system_prompt: str | None = None,
        agent_kind: str = "workflow",
        compact_context: bool = False,
        speed_optimizations: bool = False,
        expected_components: int | None = None,
        early_stop_min_calls: int = 60,
        stagnation_window: int = 15,
    ):
        """view_tool=False 时去掉 view_assembly 截图工具（骨干模型不支持图片输入时用）。
        stream / log / context_limit 与零件 WorkflowAgent 相同：流式请求、逐步进度输出、小上下文模型超长时
        压缩本轮输出上限，仍装不下则以 context_exceeded 结束。
        compact_context=True 时只压缩发给模型的旧装配历史，完整 checkpoint/trace 不变；常规压缩仍超限时
        携带当前装配状态做一次更强压缩重试。"""
        self.client = client
        self.model = model
        self.stream = stream
        self.log = log
        self.context_limit = context_limit or CONTEXT_LIMITS.get(_model_key(model))
        self.max_output_tokens = MAX_OUTPUT_TOKENS.get(_model_key(model), DEFAULT_MAX_OUTPUT_TOKENS)
        self.session = session
        self.view_tool = view_tool
        self.tools = [spec for spec in tools or TOOL_SPECS
                      if view_tool or spec["function"]["name"] != "view_assembly"]
        self.system_prompt = system_prompt
        self.agent_kind = agent_kind
        self.compact_context = bool(compact_context)
        self.speed_optimizations = bool(speed_optimizations and getattr(session, "v3", False))
        self.expected_components = int(expected_components) if expected_components is not None else None
        self.early_stop_min_calls = max(1, int(early_stop_min_calls))
        self.stagnation_window = max(1, int(stagnation_window))
        self.max_llm_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_stalls = max_stalls
        self.reasoning_effort = default_reasoning_effort(model, reasoning_effort)
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None

    def run(self, goal: str, *, resume: bool = False) -> dict:
        """设置了 checkpoint_path 时，每次拿到 LLM 回复、每轮工具执行完都把对话与中间结果落盘；
        resume=True 且存在未完成的 checkpoint 时，在当前空装配体上按顺序重放已执行的工具，
        再接着原对话继续，已拿到但还没执行的回复直接使用，不重新请求。"""
        started = time.perf_counter()
        v3 = bool(getattr(self.session, "v3", False))
        base_system_prompt = self.system_prompt or (SYSTEM_PROMPT_V3 if v3 else SYSTEM_PROMPT)
        if self.speed_optimizations:
            base_system_prompt += SPEED_OPTIMIZATION_DISCIPLINE
        messages: list[dict] = [
            {"role": "system", "content": base_system_prompt},
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
        image_at: int | None = None
        pending: dict | None = None
        prior_seconds, resumed = 0.0, None
        request_modes: list[str] = []
        compact_retries = 0
        timings: dict[str, Any] = {"llm_seconds": 0.0, "tool_seconds": 0.0, "llm_calls": [], "tools": {}}
        best_progress: dict | None = None
        best_key: tuple | None = None
        best_snapshot_path: str | None = None
        last_improvement_call = 0
        stagnation_evidence = 0
        early_stop_reason: str | None = None
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
            request_modes = list(saved.get("request_modes") or [])
            compact_retries = int(saved.get("compact_retries") or 0)
            timings = copy.deepcopy(saved.get("timings") or timings)
            best_progress = copy.deepcopy(saved.get("best_progress"))
            best_key = tuple(saved.get("best_key") or ()) or None
            best_snapshot_path = saved.get("best_snapshot_path")
            last_improvement_call = int(saved.get("last_improvement_call") or 0)
            stagnation_evidence = int(saved.get("stagnation_evidence") or 0)
            early_stop_reason = saved.get("early_stop_reason")
            if saved.get("image_at") is not None:
                messages[saved["image_at"]]["content"] = IMAGE_PLACEHOLDER
            prior_seconds = float(saved.get("duration_seconds") or 0)
            mismatch = self._replay(tool_log)
            resumed = {"from_llm_calls": usage["calls"], "replayed_tools": len(tool_log), "mismatch": mismatch}
            self._log(f"续跑：重放 {len(tool_log)} 个工具，从第 {usage['calls']} 次 LLM 调用继续"
                      + (f"，重放不一致：{mismatch}" if mismatch else ""))
            if mismatch:
                status, error, pending, done = "resume_failed", mismatch, None, True
            elif saved.get("status") not in (
                RESUMABLE_STATUSES | ({"context_exceeded"} if self.compact_context else set())
            ):
                # 上次已经正常结束，只是结果没送回评测端：恢复装配体后直接返回原结果
                status, summary, error, pending, done = saved["status"], saved["summary"], saved["error"], None, True

        def checkpoint(current_status: str = "running") -> None:
            self._save_checkpoint({
                "goal": goal, "model": self.model, "status": current_status, "summary": summary, "error": error,
                "agent_kind": self.agent_kind,
                "tool_names": [spec["function"]["name"] for spec in self.tools],
                "messages": _without_images(messages), "image_at": image_at, "usage": usage, "tool_log": tool_log,
                "llm_responses": llm_responses,
                "finish_reasons": finish_reasons, "failed_signatures": failed_signatures, "stalls": stalls,
                "truncations": truncations, "max_tokens": max_tokens, "state_messages": state_messages,
                "pending": pending, "resumed": resumed, "reasoning_effort": self.reasoning_effort,
                "compact_context": self.compact_context, "request_modes": request_modes,
                "compact_retries": compact_retries,
                "speed_optimizations": self.speed_optimizations,
                "timings": timings, "best_progress": best_progress,
                "best_key": list(best_key) if best_key is not None else None,
                "best_snapshot_path": best_snapshot_path,
                "last_improvement_call": last_improvement_call,
                "stagnation_evidence": stagnation_evidence,
                "early_stop_reason": early_stop_reason,
                "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
            })

        while not done and (pending is not None or usage["calls"] < self.max_llm_calls):
            if pending is None:
                if v3 and len(state_messages) > 1:
                    for index in state_messages[:-1]:
                        messages[index]["content"] = STATE_PLACEHOLDER
                    state_messages = state_messages[-1:]
                request_mode = "compact_history" if self.compact_context else "full"
                request_messages = (
                    compact_assembly_history(messages) if self.compact_context else messages
                )
                if self.speed_optimizations and self.compact_context:
                    request_messages.append({
                        "role": "user",
                        "content": "current_state_digest:\n" + json.dumps(
                            self.session.compact_state_digest(),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    })
                request = {"model": self.model, "messages": request_messages, "tools": self.tools, "tool_choice": "auto",
                           "temperature": self.temperature, "max_tokens": max_tokens, **extra}
                if self.speed_optimizations:
                    request["parallel_tool_calls"] = True
                request_modes.append(request_mode)
                llm_started = time.perf_counter()
                try:
                    response, prompt_tokens = complete_within_context(
                        lambda body: complete_request(self.client, body, self.stream),
                        request, self.context_limit, self._log)
                    if response is None and self.compact_context:
                        compact_retries += 1
                        try:
                            current_state = self.session.call("get_assembly_state", {})
                        except Exception as exc:
                            current_state = {"ok": False, "error": {
                                "code": "STATE_FAILED", "message": f"{type(exc).__name__}: {exc}",
                            }}
                        if _environment_lost(current_state):
                            status = "sw_lost"
                            error = "get_assembly_state: " + str(
                                (current_state.get("error") or {}).get("message") or "",
                            )
                            break
                        emergency_messages = compact_assembly_history(
                            messages,
                            keep_recent_rounds=1,
                            drop_old_rounds=True,
                        )
                        emergency_messages.append({
                            "role": "user",
                            "content": "当前装配状态：\n" + json.dumps(
                                current_state, ensure_ascii=False, separators=(",", ":"), default=str,
                            ),
                        })
                        request = {
                            **request,
                            "messages": emergency_messages,
                            "max_tokens": max(self.max_tokens, MIN_OUTPUT_TOKENS),
                        }
                        request_modes[-1] = "emergency_compact"
                        self._log("常规装配压缩仍超出上下文，改用单轮历史和当前状态重试")
                        response, prompt_tokens = complete_within_context(
                            lambda body: complete_request(self.client, body, self.stream),
                            request, self.context_limit, self._log,
                        )
                    if response is None:
                        status = "context_exceeded"
                        error = f"输入 {prompt_tokens} token，上下文上限 {self.context_limit}，剩余不足 {MIN_OUTPUT_TOKENS}"
                        self._log(f"上下文用尽：{error}")
                        elapsed = time.perf_counter() - llm_started
                        timings["llm_seconds"] = round(float(timings["llm_seconds"]) + elapsed, 4)
                        timings["llm_calls"].append({
                            "duration_ms": round(elapsed * 1000, 1),
                            "request_mode": request_modes[-1],
                            "status": "context_exceeded",
                        })
                        break
                except Exception as exc:
                    elapsed = time.perf_counter() - llm_started
                    timings["llm_seconds"] = round(float(timings["llm_seconds"]) + elapsed, 4)
                    timings["llm_calls"].append({
                        "duration_ms": round(elapsed * 1000, 1),
                        "request_mode": request_modes[-1],
                        "status": "error",
                    })
                    status, error = "llm_error", f"{type(exc).__name__}: {exc}"
                    self._log(f"LLM 错误：{error[:300]}")
                    break
                elapsed = time.perf_counter() - llm_started
                timings["llm_seconds"] = round(float(timings["llm_seconds"]) + elapsed, 4)
                timings["llm_calls"].append({
                    "duration_ms": round(elapsed * 1000, 1),
                    "request_mode": request_modes[-1],
                    "status": "ok",
                })
                llm_responses.append(raw_response(response))
                usage["calls"] += 1
                if response.usage is not None:
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                        usage[key] += int(getattr(response.usage, key, 0) or 0)
                choice = response.choices[0]
                self._log(
                    f"LLM #{usage['calls']}/{self.max_llm_calls} finish={getattr(choice, 'finish_reason', None)} "
                    f"累计 {usage['total_tokens']} token，{time.perf_counter() - started + prior_seconds:.0f} s"
                )
                finish_reasons.append(getattr(choice, "finish_reason", None))
                pending = {"finish_reason": getattr(choice, "finish_reason", None),
                           "content": choice.message.content or "",
                           "tool_calls": _tool_calls_payload(choice.message)}
                checkpoint()
            reply, pending = pending, None
            calls = reply["tool_calls"]
            # 输出被截断（推理模型常把额度用在思考上）不算停滞：丢掉残缺回复，放宽长度再要一次，并提示直接给工具调用
            if reply["finish_reason"] == "length" and not _all_args_parse(calls):
                truncations += 1
                max_tokens = min(max(max_tokens, self.max_tokens) * 2, self.max_output_tokens)
                if truncations == 1:
                    messages.append({"role": "user", "content": "上一条回复太长被截断了。少写分析，直接输出下一步的工具调用。"})
                checkpoint()
                continue
            # 放宽后的上限保留到本次装配结束：后续步骤的思考量相近，重置会让每步都先白白截断几次
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
            image: str | None = None
            for call in calls:
                tool_started = time.perf_counter()
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
                    if name == "view_assembly" and not self.view_tool:
                        result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"未知工具 {name}"}}
                    else:
                        result = self.session.call(name, args)
                    if name == "finish" and result.get("ok"):
                        finished = True
                        summary = str(args.get("summary", ""))
                tool_elapsed = time.perf_counter() - tool_started
                timings["tool_seconds"] = round(float(timings["tool_seconds"]) + tool_elapsed, 4)
                tool_bucket = timings["tools"].setdefault(name, {"calls": 0, "seconds": 0.0})
                tool_bucket["calls"] = int(tool_bucket["calls"]) + 1
                tool_bucket["seconds"] = round(float(tool_bucket["seconds"]) + tool_elapsed, 4)
                if IMAGE_KEY in result:
                    image = result.pop(IMAGE_KEY)
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
                    "duration_ms": round(tool_elapsed * 1000, 1),
                    **({"sampling_profile": result["sampling_profile"]}
                       if result.get("sampling_profile") else {}),
                    **({"dense_frames": result["dense_frames"]}
                       if result.get("dense_frames") is not None else {}),
                    **({"state_cache_hit": bool(result["state_cache_hit"])}
                       if "state_cache_hit" in result else {}),
                })
                entry = tool_log[-1]
                if entry["ok"]:
                    self._log(f"  {name} ok")
                else:
                    self._log(f"  {name} 失败 {entry['error_code']}: {str(entry['message'])[:200]}")
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
            if self.speed_optimizations:
                progress = self.session.progress_snapshot()
                progress_key = self._progress_key(progress)
                improved = best_key is None or progress_key > best_key
                if improved:
                    best_key = progress_key
                    best_progress = copy.deepcopy(progress)
                    last_improvement_call = usage["calls"]
                    stagnation_evidence = 0
                else:
                    # 完整度、验证和已知错误都没有改善；失败调用或反复改结构均属于循环证据。
                    round_tools = tool_log[round_start[1]:]
                    if round_tools:
                        stagnation_evidence += 1
                if self._components_complete(progress) and (improved or not best_snapshot_path):
                    candidate = self._default_best_path()
                    try:
                        best_snapshot_path = self.session.save_best_snapshot(candidate)
                        self._log(
                            f"  已保存最佳状态快照：components={progress['components']} "
                            f"mates={progress['mates']}"
                        )
                    except Exception as exc:
                        self._log(f"  最佳状态快照保存失败，将继续评测：{type(exc).__name__}: {exc}")
                stagnant_calls = usage["calls"] - last_improvement_call
                if (
                    self._components_complete(progress)
                    and best_snapshot_path
                    and usage["calls"] >= self.early_stop_min_calls
                    and stagnant_calls >= self.stagnation_window
                    and stagnation_evidence >= self.stagnation_window
                ):
                    early_stop_reason = (
                        f"全部 {self.expected_components} 个组件已插入；连续 {stagnant_calls} 次 LLM 调用"
                        "未改善最佳结构/验证状态，且持续出现失败或循环操作"
                    )
                    try:
                        final_path = self.session.assembly_path
                        self.session.restore_best_snapshot(best_snapshot_path, final_path)
                    except Exception as exc:
                        error = f"恢复最佳状态失败：{type(exc).__name__}: {exc}"
                        status = "sw_lost"
                        self._log(error)
                        checkpoint(status)
                        break
                    else:
                        status = "stalled_best_restored"
                        self._log(f"保守早停并恢复最佳状态：{early_stop_reason}")
                        checkpoint(status)
                        break
            if image is not None:
                # 截图很占 token，历史里只保留最新一张
                if image_at is not None:
                    messages[image_at]["content"] = IMAGE_PLACEHOLDER
                image_at = len(messages)
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": IMAGE_CAPTION}, _image_part(image, "image/png")]})
            checkpoint()

        if (
            self.speed_optimizations
            and status == "max_llm_calls"
            and best_snapshot_path
            and best_key is not None
            and self._progress_key(self.session.progress_snapshot()) < best_key
        ):
            try:
                final_path = self.session.assembly_path
                self.session.restore_best_snapshot(best_snapshot_path, final_path)
            except Exception as exc:
                error = f"恢复最佳状态失败：{type(exc).__name__}: {exc}"
                status = "sw_lost"
                self._log(error)
            else:
                status = "max_llm_calls_best_restored"
                self._log("预算结束，已恢复本次运行的最佳状态")
        if status != "resume_failed":
            checkpoint(status)
        self._log(f"结束：status={status}，{usage['calls']} 次 LLM 调用，{len(tool_log)} 个工具调用，"
                  f"{usage['total_tokens']} token" + (f"，error={str(error)[:200]}" if error else ""))
        return {
            "status": status,
            "summary": summary,
            "error": error,
            "truncations": truncations,
            "context_limit": self.context_limit,
            "llm_calls": usage["calls"],
            "reasoning_effort": self.reasoning_effort,
            "view_tool": self.view_tool,
            "finish_reasons": finish_reasons,
            "tool_calls": tool_log,
            "token_usage": usage,
            "last_motion": self.session.last_motion,
            "aliases": dict(self.session.aliases),
            "saved": self.session.saved,
            "messages": _without_images(messages),
            "llm_responses": llm_responses,
            "resumed": resumed,
            "compact_context": self.compact_context,
            "request_modes": request_modes,
            "compact_retries": compact_retries,
            "speed_optimizations": self.speed_optimizations,
            "timings": timings,
            "best_progress": best_progress,
            "best_snapshot_path": best_snapshot_path,
            "last_improvement_call": last_improvement_call,
            "early_stop_reason": early_stop_reason,
            "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
        }

    def _log(self, line: str) -> None:
        if self.log:
            self.log(line)

    def _progress_key(self, progress: dict) -> tuple:
        complete = (
            self.expected_components is not None
            and int(progress.get("components") or 0) >= self.expected_components
        )
        validated = bool(
            progress.get("motion_passed")
            or (progress.get("pose_passed") and progress.get("dof_passed"))
        )
        # 验证 > 显式保存 > 组件齐全 > 错误/悬空更少 > 结构更完整。
        return (
            int(validated),
            int(bool(progress.get("saved"))),
            int(complete),
            -int(progress.get("broken") or 0),
            -int(progress.get("over_defined") or 0),
            -int(progress.get("floating") or 0),
            int(progress.get("components") or 0),
            int(progress.get("mates") or 0),
            int(progress.get("fixed") or 0),
        )

    def _components_complete(self, progress: dict) -> bool:
        return (
            self.expected_components is not None
            and int(progress.get("components") or 0) >= self.expected_components
        )

    def _default_best_path(self) -> Path:
        if self.checkpoint_path:
            return self.checkpoint_path.with_name(self.checkpoint_path.stem + ".best.SLDASM")
        assembly_path = Path(self.session.assembly_path)
        return assembly_path.with_name(assembly_path.stem + ".best.SLDASM")

    def _load_checkpoint(self, goal: str) -> dict | None:
        if not self.checkpoint_path or not self.checkpoint_path.is_file():
            return None
        saved = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if saved.get("goal") != goal or saved.get("model") != self.model:
            raise ValueError(f"checkpoint {self.checkpoint_path} 属于另一个需求或模型，不能续跑")
        if saved.get("agent_kind", "workflow") != self.agent_kind:
            raise ValueError(
                f"checkpoint {self.checkpoint_path} 属于 {saved.get('agent_kind', 'workflow')} agent，"
                f"不能由 {self.agent_kind} agent 续跑"
            )
        saved_compact = bool(saved.get("compact_context", False))
        if saved_compact and not self.compact_context:
            raise ValueError(f"checkpoint {self.checkpoint_path} 使用了装配 compact_context，不能按完整上下文续跑")
        if bool(saved.get("speed_optimizations", False)) != self.speed_optimizations:
            raise ValueError(
                f"checkpoint {self.checkpoint_path} 的 asm_speed_optimizations 设置不同，不能续跑"
            )
        current_tools = [spec["function"]["name"] for spec in self.tools]
        if saved.get("tool_names") is not None and saved["tool_names"] != current_tools:
            raise ValueError(f"checkpoint {self.checkpoint_path} 的工具集合不同，不能续跑")
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
