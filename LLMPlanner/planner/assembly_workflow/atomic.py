"""装配 L0：单轮生成完整原子计划，再按顺序执行且不闭环反馈。"""

from __future__ import annotations

import copy
import json
from typing import Any

from openai import BadRequestError

from planner.schemas import (
    ASSEMBLY_ATOMIC_TOOLS,
    AssemblyAtomicPlan,
)

from .session import AssemblySession


ASSEMBLY_ATOMIC_API_NAME = "atomic_api_complete"
ASSEMBLY_ATOMIC_API_VERSION = "assembly-api-v1"

SYSTEM_PROMPT = """\
你是 SolidWorks 装配规划器。你只能输出一份 JSON 原子操作计划，程序会从第一步开始顺序执行；
执行期间你看不到任何结果，也没有第二轮纠错机会。因此必须一次性给出完整且顺序正确的计划。

## 允许的原子操作
- insert_component {part, name, at?}：插入一个 SLDPRT；at 是装配坐标 [x,y,z]，单位 mm。
- set_component_pose {component, at, axis?, angle_deg?}：只设置一个组件初始位姿。
- ground_component {component}：只固定一个组件。
- unfix_component {component}：只解除一个组件固定。
- mate {type, a, b, ...}：只创建一条 SolidWorks 配合。
- delete_mate {name}：只删除一条配合。
- save_assembly {path?}：保存装配体，必须是最后一步。

禁止使用或模拟任何聚合宏：joint、close_loop、mount_on_shaft、revolute_joint、slider_joint、
mesh_gears、crank_slider、rack_pinion、screw_pair、linear_coupler、check_dof、check_motion、
check_pose、finish。一个转动副通常必须显式拆成 concentric + coincident 两个 mate。

## mate 参数
type 可为 coincident / concentric / parallel / perpendicular / distance / angle /
gear / lock / slot / rack_pinion / screw / linear_coupler。a、b 是“组件别名:选择器”：
- cyl(d=10) 或 cyl(d=10, near=[x,y])：按直径和局部位置找圆柱面；
- face(+Y, largest)、face(-Z, outermost)：按法向和面积/位置找平面；
- plane(front|top|right)：默认基准面；
- face:12：明确的面序号；
- lock 可用“组件别名:component”。
线性尺寸 distance/distance_min/distance_max/pitch_diameter/travel_per_rev/lead 用 mm，角度用 degree。
gear 使用 ratio_numerator、ratio_denominator、reverse；concentric 可用 lock_rotation。
rack_pinion 使用 pitch_diameter 或 travel_per_rev；screw 使用 lead；linear_coupler
使用 ratio_numerator / ratio_denominator，并可用 reference_a / reference_b 指定参考组件。

## 纪律
1. 先逐个 insert_component；需要初始位姿时再逐个 set_component_pose；随后固定机架，再逐条 mate。
2. 只使用用户列出的绝对零件路径和角色名。后续 component/ref 一律使用 insert 时声明的 name。
3. 不要为了“完全约束”消除机构设计自由度；必须保留题目要求的运动副。
4. 不可把多条配合压缩成一步，不可调用动作列表之外的名字。
5. 最后必须 save_assembly。输出只能是符合 schema 的 JSON，不要 markdown。
"""


def _patch_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            schema["additionalProperties"] = False
            schema.setdefault("required", list(schema["properties"]))
        for value in schema.values():
            _patch_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _patch_schema(value)
    return schema


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        json.loads(text)
        return text
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return text[start:end + 1]
        raise ValueError("装配 Atomic Plan 未返回 JSON 对象")


class AssemblyAtomicPlanner:
    def __init__(
        self,
        client,
        model: str,
        *,
        max_tokens: int = 16000,
        reasoning_effort: str | None = None,
    ):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def plan(self, goal: str) -> tuple[AssemblyAtomicPlan, dict]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": goal},
        ]
        schema = _patch_schema(copy.deepcopy(AssemblyAtomicPlan.model_json_schema()))
        common = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            **({"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}),
        }
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        budgets = list(dict.fromkeys((self.max_tokens, min(self.max_tokens * 2, 32000))))
        for attempt, budget in enumerate(budgets):
            try:
                response = self.client.chat.completions.create(
                    **common,
                    max_tokens=budget,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "AssemblyAtomicPlan",
                            "strict": False,
                            "schema": schema,
                        },
                    },
                )
            except BadRequestError:
                fallback = copy.deepcopy(messages)
                fallback[0]["content"] += (
                    '\nJSON 顶层格式：{"user_intent":"...",'
                    '"steps":[{"operation":"insert_component","params":{},"description":"..."}],'
                    '"notes":"..."}。'
                )
                response = self.client.chat.completions.create(
                    **{**common, "messages": fallback},
                    max_tokens=budget,
                    response_format={"type": "json_object"},
                )
            usage["calls"] += 1
            if response.usage is not None:
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    usage[key] += int(getattr(response.usage, key, 0) or 0)
            choice = response.choices[0]
            content = choice.message.content
            try:
                if not content:
                    raise ValueError(
                        f"装配 Atomic Plan 没有内容（finish_reason={choice.finish_reason!r}）"
                    )
                return AssemblyAtomicPlan.model_validate_json(_extract_json(content)), usage
            except Exception:
                if choice.finish_reason == "length" and attempt + 1 < len(budgets):
                    continue
                raise
        raise RuntimeError("装配 Atomic Plan 重试结束但没有结果")


class AssemblyAtomicExecutor:
    """严格顺序执行；第一步失败即停止，结果不反馈给 LLM。"""

    def __init__(self, session: AssemblySession):
        self.session = session

    def execute(self, plan: AssemblyAtomicPlan) -> list[dict]:
        results: list[dict] = []
        for index, step in enumerate(plan.steps, 1):
            handler = ASSEMBLY_ATOMIC_TOOLS[step.operation]
            result = self.session.call(handler, step.params)
            row = {
                "step": index,
                "operation": step.operation.value,
                "params": step.params,
                "description": step.description,
                "success": bool(result.get("ok")),
                "result": result,
            }
            if not row["success"]:
                row["error_code"] = (result.get("error") or {}).get("code")
            results.append(row)
            if not row["success"]:
                break
        return results


def atomic_api_metadata() -> dict:
    return {
        "name": ASSEMBLY_ATOMIC_API_NAME,
        "version": ASSEMBLY_ATOMIC_API_VERSION,
        "operations": sorted(operation.value for operation in ASSEMBLY_ATOMIC_TOOLS),
    }
