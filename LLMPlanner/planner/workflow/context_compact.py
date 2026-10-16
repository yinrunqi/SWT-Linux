"""L1_v2 可选的请求级上下文压缩。

这里只构造发给模型的消息副本；调用者持有的完整 messages 不会被修改，
所以 trace、checkpoint、SolidWorks replay 和 compact verifier 的回退路径
仍然保留原始历史。
"""

from __future__ import annotations

import copy
import json
from typing import Any


CONTINUE_FULL_CONTEXT = "continue_full_context"
VERIFY_TOOLS = {"get_model_state", "render_views", "view_model"}

COMPACT_DISCIPLINE = (
    "控制推理长度，直接返回本轮需要的工具调用；不要只输出分析。"
    "互不依赖的操作尽量在同一回复中调用。"
)

VERIFIER_SYSTEM_PROMPT = """你是 CAD 建模结果核对器。只根据原始需求/图纸、当前零件状态和最新截图判断：
- 已满足要求：调用 finish。
- 仍需修改或证据不足：调用 continue_full_context，简明列出缺失或错误的特征及建议修改。
不要重新规划整个零件，不要输出工具调用以外的长篇分析。"""

CONTINUE_FULL_CONTEXT_SPEC = {
    "type": "function",
    "function": {
        "name": CONTINUE_FULL_CONTEXT,
        "description": "当前零件仍需修改；退出紧凑核对，恢复完整建模历史继续工作。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "简明说明不满足需求的几何、尺寸、数量或证据，以及下一步修改建议",
                },
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
}

_SUCCESS_KEYS = (
    "created",
    "volume_mm3",
    "volume_delta_mm3",
    "bbox_mm",
    "bodies",
    "plane",
    "direction",
    "extent_mm",
    "new_cylinders",
    "warnings",
    "notes",
    "also_removed",
    "old_value_mm",
    "old_value_deg",
    "new_value_mm",
    "new_value_deg",
    "llm_calls_left",
)
_STATE_KEYS = ("bbox_mm", "volume_mm3", "bodies", "body_list", "cylinder_groups")
_ERROR_KEYS = ("code", "message", "hint", "rolled_back", "also_removed", "bodies")


def _json_object(text: Any) -> dict | None:
    if not isinstance(text, str) or not text.startswith("{"):
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def compact_state(state: dict | None) -> dict:
    """核对请求只保留全局尺寸、实体和圆柱分组；feature 只保留名字/类型。"""
    state = state or {}
    out = {key: copy.deepcopy(state[key]) for key in _STATE_KEYS if key in state}
    features = state.get("features")
    if isinstance(features, list):
        out["features"] = [
            {key: item[key] for key in ("name", "type") if key in item}
            for item in features
            if isinstance(item, dict)
        ]
    return out


def compact_tool_result(tool: str, content: Any) -> str:
    """把旧 tool content 压成可继续建模的短 ledger；非 JSON/占位内容原样保留。"""
    payload = _json_object(content)
    if payload is None:
        return content
    if not payload.get("ok"):
        error = payload.get("error") or {}
        compact_error = {
            key: copy.deepcopy(error[key])
            for key in _ERROR_KEYS
            if key in error
        }
        return json.dumps(
            {"ledger": True, "tool": tool, "ok": False, "error": compact_error},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    compact: dict[str, Any] = {"ledger": True, "tool": tool, "ok": True}
    if isinstance(payload.get("state"), dict):
        compact["state"] = compact_state(payload["state"])
    for key in _SUCCESS_KEYS:
        if key in payload:
            compact[key] = copy.deepcopy(payload[key])
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def compact_history(messages: list[dict], *, keep_recent_rounds: int = 2) -> list[dict]:
    """保留最近 N 个工具轮完整，其余 tool 结果换成 ledger；不改 assistant 参数。"""
    out = copy.deepcopy(messages)
    call_to_tool: dict[str, str] = {}
    rounds: list[set[str]] = []
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
    kept_ids = set().union(*rounds[-keep_recent_rounds:]) if rounds and keep_recent_rounds > 0 else set()
    for message in out:
        if message.get("role") != "tool":
            continue
        call_id = str(message.get("tool_call_id") or "")
        if call_id in kept_ids:
            continue
        message["content"] = compact_tool_result(call_to_tool.get(call_id, "unknown"), message.get("content"))
    return out


def _verification_user_message(original: dict) -> dict:
    message = copy.deepcopy(original)
    content = message.get("content")
    old = "当前是一个空零件，请开始建模。"
    new = "当前零件已经建模，请结合最新状态和截图核对是否满足需求。"
    if isinstance(content, str):
        message["content"] = content.replace(old, new)
    elif isinstance(content, list):
        for part in content:
            if part.get("type") == "text":
                part["text"] = str(part.get("text") or "").replace(old, new)
    return message


def verifier_messages(
    original_user_message: dict,
    state: dict | None,
    latest_image_message: dict | None = None,
    *,
    expected_bodies: int = 1,
) -> list[dict]:
    messages = [
        {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
        _verification_user_message(original_user_message),
        {
            "role": "user",
            "content": f"要求的实体数量：{expected_bodies}。\n当前零件的精简状态：\n"
            + json.dumps(compact_state(state), ensure_ascii=False, separators=(",", ":")),
        },
    ]
    if latest_image_message is not None and isinstance(latest_image_message.get("content"), list):
        messages.append(copy.deepcopy(latest_image_message))
    return messages


def verifier_tools(all_tools: list[dict]) -> list[dict]:
    finish = next(
        (copy.deepcopy(spec) for spec in all_tools if (spec.get("function") or {}).get("name") == "finish"),
        None,
    )
    if finish is None:
        raise ValueError("工具列表缺少 finish")
    return [finish, copy.deepcopy(CONTINUE_FULL_CONTEXT_SPEC)]


def with_recovery_instruction(messages: list[dict]) -> list[dict]:
    out = compact_history(messages)
    out.append({
        "role": "user",
        "content": "上一条回复因长度耗尽，没有形成可执行工具调用。" + COMPACT_DISCIPLINE,
    })
    return out


def with_semantic_recovery(
    messages: list[dict],
    state: dict | None,
    *,
    tool: str,
    error_code: str,
    count: int,
) -> list[dict]:
    out = compact_history(messages)
    out.append({
        "role": "user",
        "content": (
            f"{tool} 已累计 {count} 次出现 {error_code}。本轮暂不提供 {tool}，"
            "不要通过微调相同参数继续碰运气；请根据当前状态改用另一种建模策略，"
            "或先调用查询工具取得缺失证据。\n当前精简状态："
            + json.dumps(compact_state(state), ensure_ascii=False, separators=(",", ":"))
        ),
    })
    return out


__all__ = [
    "COMPACT_DISCIPLINE",
    "CONTINUE_FULL_CONTEXT",
    "VERIFY_TOOLS",
    "compact_history",
    "compact_state",
    "compact_tool_result",
    "verifier_messages",
    "verifier_tools",
    "with_recovery_instruction",
    "with_semantic_recovery",
]
