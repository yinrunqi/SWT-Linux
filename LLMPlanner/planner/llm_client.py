"""
llm_client.py — LLM 客户端

参考 VideoTool/engine 架构，实现：
1. 从环境变量读取 OPENAI_BASE_URL 和 OPENAI_API_KEY
2. diskcache 磁盘缓存（SHA256 哈希，减少重复调用费用）
3. tenacity 自动重试（应对网络抖动和限流）
4. OpenAI 结构化输出（Structured Outputs via json_schema）
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Optional

import diskcache
from openai import OpenAI, BadRequestError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .schemas import SwModelingPlan, ThreeViewIR
from .skill_registry import SkillRegistry, SkillSelector, build_system_prompt, default_registry

# ============================================================
# 默认配置
# ============================================================

# 默认模型：gpt-4o 支持结构化输出（Structured Outputs）
DEFAULT_MODEL = "gpt-4o"

# 缓存目录：planner 同级的 .llm_cache 文件夹
CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"

# ------------------------------------------------------------
# System Prompt 由 Skill 文件驱动动态组装（planner/skills/）
# ------------------------------------------------------------
# 历史上这里是一个约 400 行的硬编码字符串。现在改为：
# - core/*.md   恒定注入（操作清单/单位/规则/编辑，等价于原 SYSTEM_PROMPT）
# - recipes/*.md   按用户指令 triggers 关键词命中注入（如板凳/骰子/齿轮）
# - input_modes/*.md   按输入模态（text/three_view/…）注入
#
# 保留下面这个 SYSTEM_PROMPT 常量以维持向后兼容：它现在是"registry 全量注入模式"
# 组装出的完整文本（与原硬编码 prompt 字节级等价）。运行时 _call_api 会用
# _build_system_prompt(instruction, input_mode) 组装当次调用真正的 system prompt。
# ------------------------------------------------------------

_DEFAULT_REGISTRY: Optional[SkillRegistry] = None


def _get_default_registry() -> SkillRegistry:
    """惰性单例：进程内共享一份 skill registry。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = default_registry()
    return _DEFAULT_REGISTRY


# 兼容常量：全量注入模式下组装出的完整 System Prompt。
# 供旧代码 / 测试直接引用（例如与迁移前的硬编码 prompt 做等价性 diff）。
SYSTEM_PROMPT = build_system_prompt(_get_default_registry(), [], include_all_recipes=True)

# 迁移前的原始 400 行硬编码 SYSTEM_PROMPT 已完整搬运到 planner/skills/core/*.md，
# 详见 planner/skills/{00_header, 01_operations, 02_units, 03_rules, 04_editing}.md
# 组装后与原 prompt 逐字符等价（已通过 _diff_check 验证）。



# ============================================================
# 缓存工具
# ============================================================

def _make_cache_key(
    model: str,
    system_prompt: str,
    user_prompt: str,
    image_base64: Optional[str] = None,
) -> str:
    """
    将请求内容哈希为缓存 key。使用 SHA256 保证低碰撞概率。

    - image_base64 若非空，其 SHA256 摘要也纳入 key（同一张图 + 同一段文字 → 命中缓存，
      避免重复调用昂贵的 Vision API；不同图片自动分开缓存）。
    - **向后兼容**：不传 image_base64 时，计算逻辑与旧版完全相同，历史缓存条目 key 不变。
    """
    payload: dict = {
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    if image_base64:
        payload["image_sha256"] = hashlib.sha256(image_base64.encode("utf-8")).hexdigest()
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ============================================================
# SwLLMClient
# ============================================================

class SwLLMClient:
    """
    SolidWorks LLM 客户端。

    功能：
    - 读取环境变量 OPENAI_BASE_URL / OPENAI_API_KEY
    - 使用 diskcache 缓存 LLM 响应
    - 使用 tenacity 自动重试
    - 使用 OpenAI Structured Outputs 解析为 SwModelingPlan
    - 如果 API 不支持 Structured Outputs，自动降级为 JSON mode
    """

    def __init__(
        self,
        model: Optional[str] = None,
        cache_dir: Optional[str] = None,
        use_cache: bool = True,
        skill_mode: str = "full",
        registry: Optional[SkillRegistry] = None,
    ):
        """
        Args:
            skill_mode: Skill 组装模式：
                - "full"（默认）：注入所有 core + 全部 recipes/input_modes。与迁移前的
                  硬编码 SYSTEM_PROMPT 逐字符等价，保证零回归。
                - "routed"：core 全量 + 仅命中 triggers 的 recipes/input_modes。用于减
                  少 prompt 体积；后续三视图/图片场景默认走此模式。
            registry: 可选自定义 SkillRegistry；缺省用默认（planner/skills/）。
        """
        # 读取环境变量
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")

        if not api_key:
            print("[错误] 未设置环境变量 OPENAI_API_KEY", file=sys.stderr)
            print("请在终端中设置：export OPENAI_API_KEY=your_key_here", file=sys.stderr)
            sys.exit(1)

        # 初始化 OpenAI 客户端
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
            print(f"[LLM] 使用自定义 API 地址: {base_url}")

        self.client = OpenAI(**client_kwargs)
        self.model = model or DEFAULT_MODEL
        print(f"[LLM] 使用模型: {self.model}")

        # 是否已检测到 API 不支持 Structured Outputs，自动降级
        self._use_structured_output = True

        # Skill 组装
        self.skill_mode = skill_mode
        self.registry = registry or _get_default_registry()
        self.selector = SkillSelector(self.registry)
        print(
            f"[LLM] Skill 模式: {self.skill_mode}"
            f" (core={len(self.registry.get_core())},"
            f" recipes={len(self.registry.get_recipes())},"
            f" input_modes={len(self.registry.get_input_modes())})"
        )

        # 初始化缓存
        self.use_cache = use_cache
        if use_cache:
            cache_path = cache_dir or str(CACHE_DIR)
            self.cache = diskcache.Cache(cache_path)
            print(f"[LLM] 缓存目录: {cache_path}")
        else:
            self.cache = None
            print("[LLM] 缓存已禁用")

    def _assemble_system_prompt(self, user_instruction: str, input_mode: str = "text") -> str:
        """
        根据 skill_mode 组装本次调用的 System Prompt。

        - "full": 全量注入 core + 所有 recipes/input_modes（默认，向后兼容零回归）
        - "routed": core 全量 + 命中的 recipes/input_modes（更省 token）
        """
        if self.skill_mode == "routed":
            picked = self.selector.select(user_instruction, input_mode=input_mode)
            return build_system_prompt(self.registry, picked)
        # default: full
        return build_system_prompt(self.registry, [], include_all_recipes=True)

    def plan(
        self,
        user_instruction: str,
        image_base64: Optional[str] = None,
    ) -> SwModelingPlan:
        """
        将用户自然语言指令（可选附带图纸图片）转换为 SolidWorks 建模计划。

        Args:
            user_instruction: 用户输入的建模指令文字。允许为空字符串（此时依赖图片）。
            image_base64: 可选，图片的 base64 字符串（不含 data:image/...;base64, 前缀）。
                提供图片时，会走**两阶段管线**：
                    阶段 A（Vision）：图片 + 文字 → ThreeViewIR（结构化中间表示）
                    阶段 B（Text） ：IR + 文字 → SwModelingPlan
                不提供图片时行为与旧版完全一致（纯文本路径）。

        Returns:
            SwModelingPlan: 结构化的建模计划
        """
        if image_base64:
            return self._plan_multimodal(user_instruction, image_base64)
        return self._plan_text(user_instruction)

    # -------------------- 纯文本路径（与旧行为完全等价） --------------------

    def _plan_text(self, user_instruction: str, input_mode: str = "text") -> SwModelingPlan:
        """
        纯文本 → SwModelingPlan。与迁移前一致。

        Args:
            input_mode: 影响 skill 路由。默认 "text"。多模态两阶段管线的阶段 B 会传
                "three_view"，从而把 three_view skill（含 L 型/带缺口的正确画法等
                特别指引）注入到系统 prompt 中。
        """
        # 0. 组装本次调用的 System Prompt（内容变了 cache key 自动变，无需手动清缓存）
        system_prompt = self._assemble_system_prompt(user_instruction, input_mode)

        # 1. 检查缓存
        if self.use_cache and self.cache is not None:
            cache_key = _make_cache_key(self.model, system_prompt, user_instruction)
            cached = self.cache.get(cache_key)
            if cached is not None:
                print("[LLM] 命中缓存，跳过 API 调用")
                return SwModelingPlan.model_validate_json(cached)

        # 2. 调用 LLM API（带重试）
        print(f'[LLM] 正在分析指令: "{user_instruction}"')
        result = self._call_api(user_instruction, system_prompt)

        # 3. 写入缓存
        if self.use_cache and self.cache is not None:
            cache_key = _make_cache_key(self.model, system_prompt, user_instruction)
            self.cache.set(cache_key, result.model_dump_json())
            print("[LLM] 已写入缓存")

        return result

    # -------------------- 多模态路径（三视图两阶段） --------------------

    def _plan_multimodal(
        self,
        user_instruction: str,
        image_base64: str,
    ) -> SwModelingPlan:
        """
        图片 → SwModelingPlan（两阶段）：
            阶段 A：Vision 模型看图 → ThreeViewIR（结构化 IR）
            阶段 B：把 IR 序列化后作为文字 user_prompt → 走 _plan_text 生成 SwModelingPlan
        """
        # 阶段 A：Vision 提取 IR
        ir = self._extract_three_view_ir(user_instruction, image_base64)
        print(
            f"[LLM] 阶段 A 完成: part_type={ir.part_type}, "
            f"dimensions={len(ir.dimensions)} 项, features={len(ir.features)} 项"
        )

        # 阶段 B：把 IR + 用户原始文字组合成新的 user_prompt，走文字路径
        # 显式传 input_mode="three_view" 让 skill 路由把 three_view.md 的
        # "阶段 B 特别指引"章节（L 型/带缺口的正确画法等）注入到 system prompt
        stage_b_prompt = self._compose_stage_b_prompt(user_instruction, ir)
        print("[LLM] 阶段 B: 将 IR 转换为 SwModelingPlan …")
        return self._plan_text(stage_b_prompt, input_mode="three_view")

    def _extract_three_view_ir(
        self,
        user_instruction: str,
        image_base64: str,
    ) -> ThreeViewIR:
        """阶段 A：调 Vision 模型输出 ThreeViewIR。"""
        # 系统 prompt：core + three_view input_mode（不注入所有 recipes，节省 token）
        picked = self.selector.select(user_instruction, input_mode="three_view")
        # 至少要有 three_view skill；若 selector 没命中，兜底手动加
        if not any(s.name == "three_view" for s in picked):
            tv = self.registry.get_by_name("three_view")
            if tv is not None:
                picked.append(tv)
        system_prompt = build_system_prompt(self.registry, picked)

        text_hint = user_instruction or "请根据这张三视图图纸生成结构化 IR。"
        user_msg = self._build_user_message(text_hint, image_base64)
        messages = [
            {"role": "system", "content": system_prompt},
            user_msg,
        ]

        # 缓存（同一张图 + 同一段文字 → 命中）
        cache_key = None
        if self.use_cache and self.cache is not None:
            cache_key = _make_cache_key(
                self.model, system_prompt, text_hint + "\n[STAGE=A]", image_base64
            )
            cached = self.cache.get(cache_key)
            if cached is not None:
                print("[LLM] 阶段 A 命中缓存，跳过 Vision 调用")
                return ThreeViewIR.model_validate_json(cached)

        ir = self._call_vision_ir(messages)

        if self.use_cache and self.cache is not None and cache_key is not None:
            self.cache.set(cache_key, ir.model_dump_json())

        return ir

    @staticmethod
    def _build_user_message(text: str, image_base64: Optional[str] = None) -> dict:
        """构造 OpenAI messages 中的 user 消息，支持纯文本 / 图文混合。"""
        if not image_base64:
            return {"role": "user", "content": text}
        # 判断格式：默认按 JPEG data URI；如果调用方已给了完整 data URI 则透传
        if image_base64.startswith("data:"):
            url = image_base64
        else:
            url = f"data:image/jpeg;base64,{image_base64}"
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text or "请根据这张图纸生成结构化 IR。"},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: print(
            f"[LLM] Vision 调用失败: {retry_state.outcome.exception()}\n"
            f"[LLM] {retry_state.next_action.sleep} 秒后重试... "
            f"(第 {retry_state.attempt_number} 次)"
        ),
    )
    def _call_vision_ir(self, messages: list[dict]) -> ThreeViewIR:
        """
        阶段 A 的实际 API 调用：Vision → Structured Output(ThreeViewIR)。
        与 _call_structured_output 相同的 schema-patch + strict=False 策略，
        但目标 pydantic 模型换成 ThreeViewIR。JSON mode 也作为降级。
        """
        raw_schema = ThreeViewIR.model_json_schema()
        patched_schema = self._patch_schema_additional_properties(raw_schema)

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=8000,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ThreeViewIR",
                        "strict": False,
                        "schema": patched_schema,
                    },
                },
            )
            content = completion.choices[0].message.content
            if not content:
                raise ValueError("Vision 未返回有效 IR")
            return ThreeViewIR.model_validate_json(content)
        except BadRequestError as e:
            print(f"[LLM] 阶段 A Structured Outputs 不支持({str(e)[:100]}), 降级为 JSON mode")

        # 降级：JSON mode
        json_hint = (
            "\n\n请以纯 JSON 输出，字段严格符合 ThreeViewIR schema:\n"
            "{part_type, base_view, overall_size_mm, profiles[], dimensions[], features[], assumptions}"
        )
        # 修改 system message
        patched_msgs = messages.copy()
        patched_msgs[0] = {
            "role": "system",
            "content": messages[0]["content"] + json_hint,
        }
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=patched_msgs,
            temperature=0,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("Vision 未返回任何内容")
        text = content.strip()
        if text.startswith("```"):
            lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
            text = "\n".join(lines)
        return ThreeViewIR.model_validate_json(text)

    @staticmethod
    def _compose_stage_b_prompt(user_instruction: str, ir: ThreeViewIR) -> str:
        """
        把阶段 A 的 IR 与用户原始文字拼成阶段 B 的 user_prompt。
        阶段 B 就是把这段富文本喂给纯文本建模 LLM。
        """
        ir_json = ir.model_dump_json(indent=2)
        user_prefix = (user_instruction or "").strip()
        head = (
            f"用户上传了一张三视图图纸。阶段 A 已完成看图并输出结构化 IR（见下方 JSON）。"
            f"你现在的任务是**根据 IR 精确生成 SwModelingPlan**。\n\n"
        )
        if user_prefix:
            head += f"用户附加的文字说明：{user_prefix}\n\n"

        # 根据 part_type 给出显式提醒（放在 user_prompt 头部，LLM 一定会看到）
        part_hints = {
            "l_bracket": (
                "⚠️⚠️ 该零件是 **L 形（part_type=l_bracket）**！绝对禁止在同一草图里画"
                "外接矩形 + 缺口矩形（相交但不嵌套会导致 SolidWorks 报'轮廓无效/未闭合'）。"
                "必须**用 sketch_line 逐段画出 L 形闭合轮廓的每一条边**（6 段直线组成 L 形），"
                "再一次 feature_extrusion。详见 three_view skill 的'阶段 B 特别指引' §1。\n\n"
            ),
        }
        head += part_hints.get(ir.part_type, "")

        return (
            head
            + "ThreeViewIR (阶段 A 输出):\n```json\n"
            + ir_json
            + "\n```\n\n"
            + "严格按 IR 中的 overall_size_mm 和 dimensions 换算成米制，"
            + "按 base_view 决定 select_plane index (front→0, top→1, right→2)。"
            + "features 里的每一个孔/圆角/倒角都要有对应的 operation 步骤。"
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: print(
            f"[LLM] API 调用失败: {retry_state.outcome.exception()}\n"
            f"[LLM] {retry_state.next_action.sleep} 秒后重试... "
            f"(第 {retry_state.attempt_number} 次)"
        ),
    )
    def _call_api(self, user_instruction: str, system_prompt: Optional[str] = None) -> SwModelingPlan:
        """
        调用 OpenAI API 获取建模计划。

        策略：
        1. 优先使用 Structured Outputs（json_schema with strict=False）
        2. 如果 API 返回 BadRequestError（说明不支持），自动降级为 JSON mode（json_object）
        3. 降级后后续调用不再尝试 Structured Outputs

        使用 tenacity 自动重试（最多 3 次，指数退避）。

        Args:
            system_prompt: 本次调用使用的 system prompt。为 None 时回退到全量注入
                （兼容旧调用点）。
        """
        if system_prompt is None:
            system_prompt = self._assemble_system_prompt(user_instruction)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_instruction},
        ]

        if self._use_structured_output:
            try:
                return self._call_structured_output(messages)
            except (BadRequestError, Exception) as e:
                error_msg = str(e)
                if "additionalProperties" in error_msg or "json_schema" in error_msg.lower() or "response_format" in error_msg.lower():
                    # API 不支持当前 schema 格式，降级为 JSON mode
                    print(f"[LLM] Structured Outputs 不被支持 ({error_msg[:100]})，降级为 JSON mode...")
                    self._use_structured_output = False
                    return self._call_json_mode(messages)
                else:
                    raise
        else:
            return self._call_json_mode(messages)

    @staticmethod
    def _patch_schema_additional_properties(schema: dict) -> dict:
        """
        递归给 JSON schema 中所有 object 类型（含 properties 字段）添加
        "additionalProperties": false，以满足 OpenAI Structured Outputs 的严格要求。
        同时确保所有 properties 中的字段都被列为 required。
        """
        if isinstance(schema, dict):
            if schema.get("type") == "object" and "properties" in schema:
                schema["additionalProperties"] = False
                # OpenAI 要求所有属性都是 required
                if "required" not in schema:
                    schema["required"] = list(schema["properties"].keys())
            for value in schema.values():
                if isinstance(value, dict):
                    SwLLMClient._patch_schema_additional_properties(value)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            SwLLMClient._patch_schema_additional_properties(item)
        return schema

    def _call_structured_output(self, messages: list[dict]) -> SwModelingPlan:
        """
        使用 OpenAI Structured Outputs（beta.chat.completions.parse）。
        仅适用于支持 response_format=json_schema 的模型和 API。

        支持的模型（OpenAI 官方）：
        - gpt-4o-2024-08-06 及之后版本
        - gpt-4o-mini-2024-07-18 及之后版本
        - gpt-4.5-preview
        - o1, o3-mini 等推理模型
        """
        print("[LLM] 使用 Structured Outputs 模式")

        # 手动构建 JSON schema；使用非严格模式因为 params 是动态 dict
        raw_schema = SwModelingPlan.model_json_schema()
        patched_schema = self._patch_schema_additional_properties(raw_schema)

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,     # 建模是确定性任务，降到 0 提升稳定性，避免时而完整时而截断
            max_tokens=16000,  # 复杂模型（如骰子 21 点≈50 步）计划较长，放大上限避免输出被截断
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SwModelingPlan",
                    "strict": False,
                    "schema": patched_schema,
                },
            },
        )

        content = completion.choices[0].message.content
        if not content:
            raise ValueError("LLM 未返回有效的建模计划")

        result = SwModelingPlan.model_validate_json(content)
        print("[LLM] ✅ Structured Outputs 解析成功")
        return result

    def _call_json_mode(self, messages: list[dict]) -> SwModelingPlan:
        """
        降级模式：使用 JSON mode（response_format=json_object）+ 手动 Pydantic 解析。
        兼容所有 OpenAI 兼容的第三方 API。

        注意：JSON mode 需要在 system prompt 中明确要求输出 JSON 格式，
        这里通过追加提示来实现。
        """
        print("[LLM] 使用 JSON mode 降级模式")

        # 在 system prompt 后追加 JSON 输出格式要求
        json_hint = (
            "\n\n## 输出格式\n"
            "你必须以纯 JSON 格式返回，不要包含任何 markdown 代码块标记。"
            "JSON 结构：{\"user_intent\": \"...\", \"steps\": [{\"operation\": \"...\", "
            "\"params\": {...}, \"description\": \"...\"}], \"notes\": \"...\"}\n"
            "operation 可选值：new_part, select_plane, sketch_line, sketch_rectangle, "
            "sketch_circle, sketch_centerline, sketch_arc, sketch_ellipse, sketch_polygon, sketch_slot, "
            "feature_extrusion, feature_cut_extrusion, feature_revolve, feature_fillet, feature_chamfer, "
            "feature_circular_pattern, close_sketch, feature_tree, body_bounding_box, "
            "select_face_and_sketch, modify_dimension。\n"
            "modify_dimension 的 params 中，newValue（绝对值）和 delta（增量）二选一。\n\n"
            "⚠️ **极其重要：你必须一次性输出所有步骤，不能截断！** 如果模型需要4条腿，就必须有4个 sketch_circle 步骤（或者在同一草图中画4个圆）。"
            "**每个 steps 数组元素必须是一个对象（Object），包含 operation、params、description 三个字段。绝对不能是字符串或其他类型！**"
        )
        patched_messages = messages.copy()
        patched_messages[0] = {
            "role": "system",
            "content": messages[0]["content"] + json_hint,
        }

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=patched_messages,
            temperature=0,     # 建模是确定性任务，降到 0 提升稳定性，避免时而完整时而截断
            max_tokens=16000,  # 复杂模型计划较长，放大上限避免输出被截断
            response_format={"type": "json_object"},
        )

        content = completion.choices[0].message.content
        if not content:
            raise ValueError("LLM 未返回任何内容")

        # 清理可能的 markdown 代码块标记
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        result = SwModelingPlan.model_validate_json(text)
        print("[LLM] ✅ JSON mode 解析成功")
        return result

    def clear_cache(self):
        """清空所有缓存。"""
        if self.cache is not None:
            self.cache.clear()
            print("[LLM] 缓存已清空")

    def close(self):
        """关闭缓存连接。"""
        if self.cache is not None:
            self.cache.close()
