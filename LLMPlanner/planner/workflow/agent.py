"""闭环建模 Agent：调用工具 → 读取真实结果 → 思考 → 调整 → 继续，直到验证通过后 finish。

使用 OpenAI 兼容的原生 tool calling；每个工具结果（成功的体积/包围盒变化，
失败的错误码与回滚信息）都回到对话里，由模型自己决定下一步。
"""

from __future__ import annotations

import json
import time
from typing import Any

from .tools import TOOL_SPECS, WorkflowSession, dumps_result, tool_specs

SYSTEM_PROMPT = """你是一名资深机械设计工程师，正在 SolidWorks 中用工具逐步建出用户描述的零件。

## 坐标与单位
- 所有长度单位 mm，角度单位度。模型坐标：+Y 向上（顶），+Z 向前，+X 向右。
- 除非需求另有说明，零件底面放在 Y=0、高度沿 +Y，并在 X、Z 方向上以原点为中心；回转体的轴线沿 Y。
- 每个草图平面都有固定的 (u,v) 坐标，数值就是模型坐标：法向沿 Z → u=X, v=Y；法向沿 Y → u=X, v=Z；法向沿 X → u=Z, v=Y。
  例如在顶面（法向 +Y）上 [10, -5] 表示 X=10, Z=-5。

## 草图与特征规则
- 一次 extrude/cut 调用画一个区域：外环加上它内部的内环（孔）。部分重叠的轮廓会被拒绝，需要拆成先后两个特征（实体会自动合并）或合并成一个 polygon。
- 在已有实体上加特征时，用语义面引用（top/bottom/left/right/front/back、face(+X, innermost) 等）或 get_model_state 返回的 face#index，不要猜测。
- cut 默认朝实体内部切；hole 可以一次打多个同径孔；重复特征优先用 circular_pattern / linear_pattern。
- revolve 的轮廓是截面的一半，必须整体位于轴线一侧，可以贴着轴线。
- 带圆角的外形（耳片、圆角板、腰形孔、扇形窗口、带圆弧的截面）直接在轮廓里表达：rect/polygon 的 corner_radius、slot、annular_sector 或 path 的圆弧段，而不是先画尖角再补 fillet。
- 需要在没有现成平面的位置画草图时（悬空的凸台、离开实体表面一段距离的特征），先用 ref_plane 从已有平面偏移出基准面，再把它的名称作为 plane。
- 对称的特征建好一侧后用 mirror 镜像；整个零件左右对称时也可以先建一半再 mirror(body=true)。
- 侧凸台、支管接到已有实体时用 extrude(..., up_to=面或 body)，不要估一个 depth。
- 沉头孔用 counterbore / countersink，螺纹底孔用 tapped_hole；不要拆成两次普通 cut。
- 筋用 rib（开放线段，连接已有两个面）；弯管、沿路径的实体用 sweep；变截面用 loft。
- 空心壳体用 shell 抽掉开口面；侧面斜度用 draft，不要靠猜拉伸深度冒充。
- 钣金平板用 sheet_metal（必须是零件的第一特征）。改已有特征的尺寸用 edit_feature，不要删除重做。
- fillet/chamfer 一般放在最后，edges 不确定时先用 get_model_state 查看几何。

## 工作方式
1. 先读懂需求，列出总体尺寸和关键特征；可以用 set_parameters 定义驱动参数，之后用 "=表达式" 引用。
2. 先建基体，再逐个添加特征。每次调用后检查返回的 volume_delta_mm3 与 bbox_mm 是否符合预期。
3. 工具失败时会自动回滚，模型保持调用前的状态。阅读错误码和提示，修改参数或换一种做法再试；不要重复完全相同的失败调用。做错的特征可以用 delete_feature 删除。
4. 全部完成后调用 get_model_state，逐项核对总体尺寸、孔数、特征是否满足需求；不满足就继续修改。
5. 核对无误后调用 finish，并在 summary 中说明零件的组成与关键尺寸。
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


class WorkflowAgent:
    def __init__(
        self,
        client: Any,
        model: str,
        session: WorkflowSession,
        *,
        max_llm_calls: int = 40,
        max_tokens: int = 8000,
        temperature: float = 0.0,
        max_stalls: int = 2,
        extra_tools: list[str] | None = None,
        reasoning_effort: str | None = None,
    ):
        self.client = client
        self.model = model
        self.session = session
        self.max_llm_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.max_stalls = max_stalls
        self.tools = tool_specs(extra_tools)

    def run(self, goal: str) -> dict:
        started = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"建模需求：\n{goal}\n\n当前是一个空零件，请开始建模。"},
        ]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        tool_log: list[dict] = []
        finish_reasons: list[str | None] = []
        # 原始响应只做记录（含 reasoning_content、缓存命中 usage），不回填进 messages，以免改变后续请求体。
        llm_responses: list[Any] = []
        failed_signatures: dict[str, int] = {}
        status, summary, stalls, error = "max_llm_calls", "", 0, None

        # 未指定时不发送该字段，保持与历史结果相同的请求体（由服务端默认值决定推理强度）。
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        for _ in range(self.max_llm_calls):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=self.tools,
                    tool_choice="auto",
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    **extra,
                )
            except Exception as exc:
                status, error = "llm_error", f"{type(exc).__name__}: {exc}"
                break
            usage["calls"] += 1
            if response.usage is not None:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += int(getattr(response.usage, key, 0) or 0)
            # 推理 token 计入 max_tokens；截断（length）时部分网关会把 usage 置 0，需单独记录。
            finish_reasons.append(response.choices[0].finish_reason)
            llm_responses.append(
                response.model_dump(mode="json") if hasattr(response, "model_dump") else repr(response)
            )
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
                    "content": "请继续通过调用工具推进建模；确认零件满足需求后调用 finish。",
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
                    if name == "finish":
                        result = self._finish(args)
                        finished = result["ok"]
                        if finished:
                            summary = str(args.get("summary", ""))
                    else:
                        result = self.session.call(name, args)
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

        final_state = None
        try:
            final_state = self.session.state_summary()
        except Exception as exc:  # 最终状态只用于记录
            error = error or f"final_state: {exc}"
        return {
            "status": status,
            "summary": summary,
            "error": error,
            "llm_calls": usage["calls"],
            "reasoning_effort": self.reasoning_effort,
            "finish_reasons": finish_reasons,
            "llm_responses": llm_responses,
            "tool_calls": tool_log,
            "token_usage": usage,
            "final_state": final_state,
            "messages": messages,
            "duration_seconds": round(time.perf_counter() - started, 2),
        }

    def _finish(self, args: dict) -> dict:
        state = self.session.state_summary()
        if not state.get("volume_mm3"):
            return {"ok": False, "error": {"code": "NO_BODY", "message": "零件中还没有实体，不能结束"}}
        return {"ok": True, "final_state": {"bbox_mm": state["bbox_mm"], "volume_mm3": state["volume_mm3"]}}
