"""闭环建模 Agent：调用工具 → 读取真实结果 → 思考 → 调整 → 继续，直到验证通过后 finish。

使用 OpenAI 兼容的原生 tool calling；每个工具结果（成功的体积/包围盒变化，
失败的错误码与回滚信息）都回到对话里，由模型自己决定下一步。
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .tools import IMAGE_KEY, TOOL_SPECS, WorkflowSession, dumps_result, tool_specs
from .tools_v2 import WORKFLOW_VERSIONS, dumps_result_v2, tool_specs_v2

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

SYSTEM_PROMPT_V2 = """你是一名资深机械设计工程师，正在 SolidWorks 中用工具逐步建出用户描述的零件。

## 坐标与单位
- 所有长度单位 mm，角度单位度。SolidWorks 基准面：front_plane = XY（法向 +Z），top_plane = XZ（法向 +Y），right_plane = YZ（法向 +X）。
- 需求给了坐标系或坐标（如 "Z 轴向上"、"x ∈ [−50, 50]"、"底面在 z = 0"）时，严格按需求的坐标建模，不要换轴、不要平移。
  需求没有说明时：+Y 向上，底面放在 Y=0，X、Z 方向以原点为中心，回转体轴线沿 Y。
- 每个草图平面都有固定的 (u,v) 坐标，数值就是模型坐标：法向沿 Z → u=X, v=Y；法向沿 Y → u=X, v=Z；法向沿 X → u=Z, v=Y。
- 平面最稳定的写法是 "Z=10"、"Y=-12.5"、"X=0"：垂直于该轴、位于该坐标处的平面，法向为 +轴，自动创建或复用基准面。
  实体面用 face(+Z, outermost) 这类方向写法；top/bottom/front/back/left/right 固定指 ±Y/±Z/±X，Z 向上的零件不要用 top/bottom。
  face#index 每个特征之后都可能重新编号，只能用最新一次 get_model_state 里的。

## 草图与特征规则
- 一次 extrude/cut 画一个区域：外环加上它内部的内环（孔）。部分重叠的轮廓会被拒绝，需要拆成先后两个特征（实体会自动合并）或合并成一个 polygon/path。
- 零件必须是单一实体：每个新凸台都要和已有实体有面接触或伸入其中（建议伸入 ≥0.5mm）；只在一条线或一个点上相切（圆柱贴平面、两圆柱相切、棱对棱）会生成独立实体并被拒绝。
- 拉伸方向：基准面和 "Z=…" 平面默认沿 +法向，实体面默认沿外法向；direction=both 时 depth 是总厚度（两侧各一半）。
- cut 默认朝实体内部切。单向 through_all 只切穿草图平面一侧；草图平面（含实体面）背后还有凸台、轮毂等材料时，
  要整根贯穿就在零件端面上画，或用 direction=both。
- L 形、U 形、T 形、台阶等截面不变的形状，用一个 polygon/path 画出整个截面再拉伸一次，比多块拼接可靠。
- 外形上的圆角（板的竖直棱、耳片端部、槽口）直接在轮廓里表达：rect/polygon 的 corner_radius、slot、hull、annular_sector 或 path 的圆弧段；
  fillet/chamfer 只用于轮廓无法表达的棱（如顶面一圈的边），并放在最后。
- path 的半圆弧用 through 给出弧上一点；只给 center 的 180° 圆弧必须写 ccw。两圆外公切线外形（连杆、耳片）用 hull。
  两圆相交的 "8" 字形（齿轮腔等）不是 hull 也不是长圆形：两个圆分别 cut 一次，或用 path 画两段大圆弧。
- 圆周均布的孔用 hole 的 polar（center、radius 或 pcd、count、start_deg）；角度在草图 (u,v) 内从 +u 转向 +v。
  圆周阵列其它特征用 circular_pattern，并总是给 center；线性重复用 linear_pattern。
- revolve 的轮廓是截面的一半，必须整体位于轴线一侧，可以贴着轴线。
- 侧凸台、支管接到已有实体时用 extrude(..., up_to=面或 body)，不要估一个 depth。
- 沉头孔用 counterbore / countersink，螺纹底孔用 tapped_hole；筋用 rib；弯管用 sweep；变截面用 loft；空心壳体用 shell；斜度用 draft；钣金用 sheet_metal。
- sweep 一般省略 profile_plane：路径第一段沿坐标轴时，截面自动放在路径起点并垂直于路径，loops 写相对路径起点的坐标（截面中心通常是 [0,0]）。
  圆截面（管、丝、弹簧）直接用 profile_diameter（空心管加 inner_diameter），不需要截面平面；
  空间弯管用 path_3d（折线 + corner_radius），弹簧/螺旋槽用 helix。
- loft 首尾截面可以收成一点（{plane, point}）；需要控制中间形状时加 guides，要和相邻段平滑相接时用 start_tangent/end_tangent=normal。
- 改已有特征的尺寸用 edit_feature。delete_feature 会连带删除依赖它的后续特征（结果的 also_removed），删除前想清楚。

## 每步核对
- 每个特征的结果都有 volume_delta_mm3、bbox_mm、extent_mm（本特征在模型坐标中的范围）、removal_ratio/fill_ratio（实际体积变化 ÷ 轮廓面积×深度）
  和 new_cylinders（新出现的孔/凸台圆柱面及其轴线位置）。逐项和预期比较；出现 warnings 时先处理再继续。
- 倒圆/倒角结果的 estimated_edge_length_mm 是按 90° 棱估算的被处理总棱长，和预期差很多说明边选错了。
- 阵列结果的 expected_delta_mm3 是种子×份数，和实际相差大说明有实例没生效。

## 工作方式
1. 先读懂需求，列出总体尺寸、各特征的坐标与数量；可以用 set_parameters 定义驱动参数，之后用 "=表达式" 引用。
   "相距 / 距轴线 / 偏心 / 中心距 X mm" 是中心到中心的距离，直接用 X，不要除以 2；只有直径换半径才除以 2。
   有方位要求的特征（"沿某条槽的方向"、"与销孔错开 30°"），先写出每个参照特征的实际方位角，再算这个特征的方位。
2. 先建基体，再逐个添加特征。互不依赖的特征可以在一次回复里并行调用多个工具，节省调用次数。
3. 工具失败时会自动回滚，模型保持调用前的状态。阅读错误码和提示，修改参数或换一种做法；同一个失败的调用不会被执行第三次。
4. 每个工具结果里有 llm_calls_left（剩余的模型调用次数），据此安排节奏，留出核对和 finish 的次数。
5. 全部完成后调用 get_model_state，逐项核对：总体尺寸、孔/凸台的数量与直径（cylinder_groups）、只有一个实体；不满足就继续修改。
6. 核对无误后调用 finish，并在 summary 中说明零件的组成与关键尺寸。
"""
STATE_PLACEHOLDER = "（此前的 get_model_state 结果已省略，以最新一次为准）"
BUDGET_WARNING_FRACTION = 0.25
MAX_IDENTICAL_FAILURES = 2
BLOCKED_CODES = {"DUPLICATE_CALL", "REPEATED_FAILURE"}


DRAWING_NOTE = (
    "附图是零件的三视图（第三角画法）。图纸坐标与模型坐标一致：主视图沿 -Z 方向看（图上右 = +X、上 = +Y）；"
    "俯视图在主视图上方，从 +Y 往下看（图上右 = +X、下 = +Z）；右视图在主视图右方，从 +X 看（图上右 = -Z、上 = +Y）。"
    "实线是可见边，虚线是隐藏边，点画线是中心线。全部尺寸从图纸读取。"
)
RENDERED_PLACEHOLDER = "（此前 render_views 的三视图已省略，只保留最新一张）"
IMAGE_PLACEHOLDER_V2 = "（此前的图片已省略，只保留最新一张）"
IMAGE_CAPTIONS = {
    "render_views": "render_views：当前零件的三视图如下，请与输入图纸逐个视图对照。",
    "view_model": "view_model：SolidWorks 中当前零件的截图如下，每格上方标有视向与坐标方向。",
}
# 续跑时不需要重放的工具：只读查询，不改变零件
NO_REPLAY_TOOLS = {"get_model_state", "render_views", "view_model", "finish"}
RESUMABLE_STATUSES = {"running", "llm_error", "sw_lost"}
ENVIRONMENT_ERRORS = {"NO_PART", "SW_UNAVAILABLE"}


def _image_part(image_b64: str, mime: str) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}}


# 未显式指定 reasoning_effort 时按模型取默认值；键是去掉 "basic/" 之类前缀后的完整模型名
DEFAULT_REASONING_EFFORTS = {"claude-sonnet-5": "medium"}
# 上下文窗口较小的模型（输入 + 推理 + 输出共用）；未列出的模型不做处理
CONTEXT_LIMITS = {"qwen3.8-flash-next-fp8": 73728}
# 截断重试时输出上限翻倍的封顶值。推理模型单次思考可能超过 8K，不能封得太低；
# 有 CONTEXT_LIMITS 的模型在输入变长时会按服务端报告的输入长度自动压缩本轮输出
MAX_OUTPUT_TOKENS: dict[str, int] = {}
DEFAULT_MAX_OUTPUT_TOKENS = 32000
CONTEXT_MARGIN = 64
MIN_OUTPUT_TOKENS = 2048
_CONTEXT_OVERFLOW = re.compile(r"maximum context length is \d+ tokens.*?prompt contains at least (\d+) input tokens", re.S)


def _model_key(model: str) -> str:
    return str(model or "").rsplit("/", 1)[-1].strip().lower()


def default_reasoning_effort(model: str, reasoning_effort: str | None = None) -> str | None:
    if reasoning_effort:
        return reasoning_effort
    return DEFAULT_REASONING_EFFORTS.get(_model_key(model))


def _context_overflow_prompt_tokens(exc: Exception) -> int | None:
    """vLLM 式的超长报错（400）里给出的输入 token 数；不是这类错误时返回 None。"""
    match = _CONTEXT_OVERFLOW.search(str(exc))
    return int(match.group(1)) if match else None


def raw_response(response: Any) -> dict:
    """完整的 provider 响应（含 reasoning_content、finish_reason、逐次 usage），只用于记录。"""
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            return json.loads(json.dumps(dump(mode="json"), ensure_ascii=False, default=str))
        except Exception:
            pass
    return {"repr": repr(response)}


FINISH_REASONS = {"stop", "length", "tool_calls", "content_filter", "function_call"}
STREAM_ATTEMPTS = 3


class StreamFailed(RuntimeError):
    """网关在流中途以非标准 finish_reason（如 error）结束：上游临时故障，可原样重试。"""


def _streamed_completion(client: Any, **kwargs: Any) -> Any:
    """流式请求并拼回完整的 ChatCompletion：长回复不会因为上游等整段响应而读超时。"""
    from openai.types.chat import ChatCompletion

    head: dict = {}
    content, reasoning, finish_reason, usage = [], [], None, None
    calls: dict[int, dict] = {}
    for chunk in client.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kwargs):
        head = head or {"id": chunk.id, "created": chunk.created, "model": chunk.model}
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage.model_dump(mode="json")
        for choice in chunk.choices or []:
            delta = choice.delta
            if delta.content:
                content.append(delta.content)
            if getattr(delta, "reasoning_content", None):
                reasoning.append(delta.reasoning_content)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": None, "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                slot["id"] = slot["id"] or tc.id
                if tc.function is not None:
                    if tc.function.name and not slot["function"]["name"]:
                        slot["function"]["name"] = tc.function.name
                    slot["function"]["arguments"] += tc.function.arguments or ""
            if choice.finish_reason:
                finish_reason = choice.finish_reason
    if finish_reason is not None and finish_reason not in FINISH_REASONS:
        raise StreamFailed(f"流以 finish_reason={finish_reason} 结束: {''.join(content)[-300:]}")
    message: dict = {"role": "assistant", "content": "".join(content) or None,
                     "tool_calls": [calls[i] for i in sorted(calls)] or None}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    return ChatCompletion.model_validate({
        **head, "object": "chat.completion", "usage": usage,
        "choices": [{"index": 0, "finish_reason": finish_reason or "stop", "message": message}],
    })


def _first_user_message(goal: str, image_b64: str | None, image_mime: str) -> dict:
    text = f"建模需求：\n{goal}\n\n当前是一个空零件，请开始建模。"
    if not image_b64:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [{"type": "text", "text": f"{text}\n\n{DRAWING_NOTE}"}, _image_part(image_b64, image_mime)],
    }


def _without_images(messages: list[dict]) -> list[dict]:
    """记录用的消息副本：图片 base64 换成占位文字，避免 trace 文件膨胀。"""
    out = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            content = [
                {"type": "text", "text": "[image]"} if part.get("type") == "image_url" else part for part in content
            ]
            message = {**message, "content": content}
        out.append(message)
    return out


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
        try:
            if not isinstance(json.loads(call["function"]["arguments"]), dict):
                return False
        except ValueError:
            return False
    return True


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
        stream: bool = False,
        checkpoint_path: str | Path | None = None,
        version: str | None = None,
        view_tool: bool = True,
        log: Callable[[str], None] | None = None,
        context_limit: int | None = None,
    ):
        """version：v1 = L1，v2 = L1_v2；缺省跟随 session.version。
        view_tool：L1_v2 是否提供 view_model 截图工具（骨干模型不支持图片输入时关掉）。
        log：逐步进度输出（每次 LLM 调用、每个工具调用一行），评测时供终端监控。
        context_limit：模型上下文 token 数，缺省查 CONTEXT_LIMITS；超长时压缩本轮输出上限，仍装不下则以
        context_exceeded 结束。"""
        self.log = log
        self.context_limit = context_limit or CONTEXT_LIMITS.get(_model_key(model))
        self.max_output_tokens = MAX_OUTPUT_TOKENS.get(_model_key(model), DEFAULT_MAX_OUTPUT_TOKENS)
        self.version = version or getattr(session, "version", "v1")
        if self.version not in WORKFLOW_VERSIONS:
            raise ValueError(f"未知的 workflow 版本 {self.version}")
        self.client = client
        self.stream = stream
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.model = model
        self.session = session
        self.max_llm_calls = max_llm_calls
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning_effort = default_reasoning_effort(model, reasoning_effort)
        self.max_stalls = max_stalls
        self.v2 = self.version == "v2"
        self.view_tool = self.v2 and view_tool
        self.tools = tool_specs_v2(extra_tools, view_tool=view_tool) if self.v2 else tool_specs(extra_tools)
        self.image_placeholder = IMAGE_PLACEHOLDER_V2 if self.v2 else RENDERED_PLACEHOLDER
        self.system_prompt = SYSTEM_PROMPT_V2 if self.v2 else SYSTEM_PROMPT
        self._dumps = dumps_result_v2 if self.v2 else dumps_result

    def run(
        self, goal: str, image_b64: str | None = None, image_mime: str = "image/png", *, resume: bool = False,
    ) -> dict:
        """image_b64：看图建模时的输入图纸，放在首条用户消息里，之后每次调用模型都能看到。

        设置了 checkpoint_path 时，每次拿到 LLM 回复、每轮工具执行完都把对话与中间结果落盘；
        resume=True 且存在未完成的 checkpoint 时，在当前空零件上按顺序重放已执行的工具恢复模型，
        再接着原对话继续，已拿到但还没执行的回复直接使用，不重新请求。
        """
        started = time.perf_counter()
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            _first_user_message(goal, image_b64, image_mime),
        ]
        rendered_at: int | None = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        tool_log: list[dict] = []
        llm_responses: list[dict] = []
        finish_reasons: list[str | None] = []
        failed_signatures: dict[str, int] = {}
        status, summary, stalls, error = "max_llm_calls", "", 0, None
        max_tokens, truncations = self.max_tokens, 0
        pending: dict | None = None
        prior_seconds, resumed = 0.0, None

        # 未指定时不发送该字段，保持与历史结果相同的请求体（由服务端默认值决定推理强度）。
        extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        saved = self._load_checkpoint(goal) if resume else None
        done = False
        if saved:
            messages = saved["messages"]
            messages[1] = _first_user_message(goal, image_b64, image_mime)
            rendered_at = saved.get("rendered_at")
            if rendered_at is not None:
                messages[rendered_at]["content"] = self.image_placeholder
                rendered_at = None
            usage, tool_log, llm_responses = saved["usage"], saved["tool_log"], saved["llm_responses"]
            finish_reasons, failed_signatures = saved.get("finish_reasons", []), saved["failed_signatures"]
            stalls, truncations, max_tokens = saved["stalls"], saved["truncations"], saved["max_tokens"]
            pending, prior_seconds = saved.get("pending"), float(saved.get("duration_seconds") or 0)
            mismatch = self._replay(tool_log)
            resumed = {"from_llm_calls": usage["calls"], "replayed_tools": len(tool_log), "mismatch": mismatch}
            self._log(f"续跑：重放 {len(tool_log)} 个工具，从第 {usage['calls']} 次 LLM 调用继续"
                      + (f"，重放不一致：{mismatch}" if mismatch else ""))
            if mismatch:
                status, error, pending, done = "resume_failed", mismatch, None, True
            elif saved.get("status") not in RESUMABLE_STATUSES:
                # 上次已经正常结束，只是结果没送回评测端：恢复模型后直接返回原结果
                status, summary, error, pending, done = saved["status"], saved["summary"], saved["error"], None, True

        def checkpoint(current_status: str = "running") -> None:
            self._save_checkpoint({
                "goal": goal, "model": self.model, "status": current_status, "summary": summary,
                "error": error, "messages": _without_images(messages), "rendered_at": rendered_at,
                "usage": usage, "tool_log": tool_log, "llm_responses": llm_responses,
                "finish_reasons": finish_reasons, "failed_signatures": failed_signatures, "stalls": stalls,
                "truncations": truncations, "max_tokens": max_tokens, "pending": pending, "resumed": resumed,
                "reasoning_effort": self.reasoning_effort,
                "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
            })

        while not done and (pending is not None or usage["calls"] < self.max_llm_calls):
            if pending is None:
                request = {"model": self.model, "messages": messages, "tools": self.tools, "tool_choice": "auto",
                           "temperature": self.temperature, "max_tokens": max_tokens, **extra}
                try:
                    try:
                        response = self._complete(request)
                    except Exception as exc:
                        prompt_tokens = _context_overflow_prompt_tokens(exc)
                        if prompt_tokens is None or not self.context_limit:
                            raise
                        # 上下文装不下这一轮的 max_tokens：按服务端报告的输入长度压缩输出上限再试一次
                        room = self.context_limit - prompt_tokens - CONTEXT_MARGIN
                        if room < MIN_OUTPUT_TOKENS:
                            status = "context_exceeded"
                            error = f"输入 {prompt_tokens} token，上下文上限 {self.context_limit}，剩余不足 {MIN_OUTPUT_TOKENS}"
                            self._log(f"上下文用尽：{error}")
                            break
                        self._log(f"上下文接近上限：输入 {prompt_tokens} token，本轮 max_tokens {max_tokens} → {room}")
                        response = self._complete({**request, "max_tokens": room})
                except Exception as exc:
                    status, error = "llm_error", f"{type(exc).__name__}: {exc}"
                    self._log(f"LLM 错误：{error[:300]}")
                    break
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
                # 推理 token 计入 max_tokens；截断（length）时部分网关会把 usage 置 0，需单独记录。
                finish_reasons.append(getattr(choice, "finish_reason", None))
                pending = {"finish_reason": getattr(choice, "finish_reason", None),
                           "content": choice.message.content or "",
                           "tool_calls": _tool_calls_payload(choice.message)}
                checkpoint()
            reply, pending = pending, None
            calls = reply["tool_calls"]
            # 输出被截断（推理模型常把额度用在思考上）不算停滞：丢掉残缺回复，放宽长度再要一次
            if reply["finish_reason"] == "length" and not _all_args_parse(calls):
                truncations += 1
                max_tokens = min(max(max_tokens, self.max_tokens) * 2, self.max_output_tokens)
                if truncations == 1:
                    messages.append({"role": "user", "content": "上一条回复太长被截断了。少写分析，直接输出下一步的工具调用。"})
                checkpoint()
                continue
            # 放宽后的上限保留到本题结束：同一题后续步骤的思考量相近，重置会让每步都先白白截断几次
            round_start = (len(messages), len(tool_log), dict(failed_signatures))
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
                    "content": "请继续通过调用工具推进建模；确认零件满足需求后调用 finish。",
                })
                checkpoint()
                continue
            stalls = 0

            finished = False
            images: list[tuple[str, str]] = []
            seen_in_reply: set[str] = set()
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
                    blocked = self._blocked(name, args, seen_in_reply, failed_signatures) if self.v2 else None
                    if blocked:
                        result = {"ok": False, "error": blocked}
                    elif name == "view_model" and not self.view_tool:
                        result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"未知工具 {name}"}}
                    elif name == "finish":
                        result = self._finish(args)
                        finished = result["ok"]
                        if finished:
                            summary = str(args.get("summary", ""))
                    else:
                        result = self.session.call(name, args)
                code = (result.get("error") or {}).get("code")
                if code in ENVIRONMENT_ERRORS and getattr(self.session, "has_part", True):
                    # 零件文档或 SolidWorks 服务丢了，不是模型的错：撤回这一轮，留给 --resume 重放后重做
                    messages[round_start[0]:] = []
                    del tool_log[round_start[1]:]
                    failed_signatures = round_start[2]
                    status, error, pending = "sw_lost", f"{name}: {result['error'].get('message')}", reply
                    break
                if IMAGE_KEY in result:
                    images.append((name, result.pop(IMAGE_KEY)))
                was_blocked = self.v2 and (result.get("error") or {}).get("code") in BLOCKED_CODES
                if not result.get("ok") and not was_blocked:
                    signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
                    failed_signatures[signature] = failed_signatures.get(signature, 0) + 1
                    if failed_signatures[signature] > 1:
                        result["repeat_warning"] = "这个调用与之前失败的调用完全相同，请换一种做法。"
                entry = {
                    "tool": name,
                    "args": args,
                    "ok": bool(result.get("ok")),
                    "error_code": (result.get("error") or {}).get("code"),
                    "message": (result.get("error") or {}).get("message", ""),
                }
                if was_blocked:
                    entry["blocked"] = True
                tool_log.append(entry)
                if entry["ok"]:
                    self._log(f"  {name} ok")
                else:
                    self._log(f"  {name} 失败 {entry['error_code']}: {str(entry['message'])[:200]}")
                if self.v2:
                    self._annotate_v2(name, result, messages, usage["calls"])
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": self._dumps(result)})
            if status == "sw_lost":
                break
            if images and not finished:
                # 图片很占 token，历史里只保留最新一张
                if rendered_at is not None:
                    messages[rendered_at]["content"] = self.image_placeholder
                rendered_at = len(messages)
                tool, image = images[-1]
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_CAPTIONS.get(tool, f"{tool}：图片如下。")},
                        _image_part(image, "image/png"),
                    ],
                })
            if finished:
                status = "finished"
                break
            checkpoint()

        final_state = None
        try:
            final_state = self.session.state_summary()
        except Exception as exc:  # 最终状态只用于记录
            error = error or f"final_state: {exc}"
        if status != "resume_failed":
            checkpoint(status)
        self._log(f"结束：status={status}，{usage['calls']} 次 LLM 调用，{len(tool_log)} 个工具调用，"
                  f"{usage['total_tokens']} token" + (f"，error={str(error)[:200]}" if error else ""))
        return {
            "status": status,
            "summary": summary,
            "error": error,
            "llm_calls": usage["calls"],
            "reasoning_effort": self.reasoning_effort,
            "view_tool": self.view_tool,
            "finish_reasons": finish_reasons,
            "tool_calls": tool_log,
            "token_usage": usage,
            "final_state": final_state,
            "messages": _without_images(messages),
            "llm_responses": llm_responses,
            "resumed": resumed,
            "duration_seconds": round(prior_seconds + time.perf_counter() - started, 2),
        }

    def _complete(self, request: dict) -> Any:
        if not self.stream:
            return self.client.chat.completions.create(**request)
        for attempt in range(1, STREAM_ATTEMPTS + 1):
            try:
                return _streamed_completion(self.client, **request)
            except StreamFailed:
                if attempt == STREAM_ATTEMPTS:
                    raise

    def _log(self, line: str) -> None:
        if self.log:
            self.log(line)

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
        """按原顺序重放会改动零件的工具（失败的也重放，保证特征编号一致）；结果与记录不符时返回说明。"""
        for index, item in enumerate(tool_log, 1):
            if item["tool"] in NO_REPLAY_TOOLS or "_raw" in item["args"] or item.get("blocked"):
                continue
            result = self.session.call(item["tool"], item["args"])
            result.pop(IMAGE_KEY, None)
            code = (result.get("error") or {}).get("code")
            if bool(result.get("ok")) != item["ok"] or code != item["error_code"]:
                return (f"重放第 {index} 个工具调用 {item['tool']} 时结果与记录不一致："
                        f"记录 ok={item['ok']} code={item['error_code']}，重放 ok={bool(result.get('ok'))} code={code}")
        return None

    def _finish(self, args: dict) -> dict:
        state = self.session.state_summary()
        if not state.get("volume_mm3"):
            return {"ok": False, "error": {"code": "NO_BODY", "message": "零件中还没有实体，不能结束"}}
        if self.v2 and int(state.get("bodies") or 1) > 1:
            return {"ok": False, "error": {
                "code": "MULTI_BODY",
                "message": f"零件现在有 {state['bodies']} 个实体，需求要求单一实体",
                "hint": "用 get_model_state 查看各实体的范围，把没连上的部分伸入主体或删掉重做。",
                "bodies": state.get("body_list"),
            }}
        return {"ok": True, "final_state": {"bbox_mm": state["bbox_mm"], "volume_mm3": state["volume_mm3"]}}

    @staticmethod
    def _blocked(name: str, args: dict, seen: set[str], failed: dict[str, int]) -> dict | None:
        """v2：同一回复里的重复调用只执行一次；已经原样失败两次的调用不再执行。"""
        signature = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if signature in seen:
            return {"code": "DUPLICATE_CALL", "message": "与本次回复中前面的调用完全相同，已跳过，结果见前一个调用"}
        seen.add(signature)
        if failed.get(signature, 0) >= MAX_IDENTICAL_FAILURES:
            return {"code": "REPEATED_FAILURE",
                    "message": f"这个调用已经以完全相同的参数失败了 {failed[signature]} 次，不再执行；请换一种做法"}
        return None

    def _annotate_v2(self, name: str, result: dict, messages: list[dict], calls_used: int) -> None:
        left = max(self.max_llm_calls - calls_used, 0)
        result["llm_calls_left"] = left
        if left <= self.max_llm_calls * BUDGET_WARNING_FRACTION:
            result["budget_note"] = (
                f"只剩 {left} 次模型调用：优先完成缺失的主要特征，合并互不依赖的调用，留 1~2 次核对并 finish。"
            )
        if name == "get_model_state" and result.get("ok"):
            for message in messages:
                content = message.get("content")
                if message.get("role") == "tool" and isinstance(content, str) and content.startswith('{"ok": true, "state": '):
                    message["content"] = STATE_PLACEHOLDER
