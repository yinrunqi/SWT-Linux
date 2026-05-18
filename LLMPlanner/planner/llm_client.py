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

from .schemas import SwModelingPlan

# ============================================================
# 默认配置
# ============================================================

# 默认模型：gpt-4o 支持结构化输出（Structured Outputs）
DEFAULT_MODEL = "gpt-4o"

# 缓存目录：planner 同级的 .llm_cache 文件夹
CACHE_DIR = Path(__file__).resolve().parent / ".llm_cache"

# System Prompt：告知 LLM 可用的 SolidWorks API 及其参数
SYSTEM_PROMPT = """\
你是一个 SolidWorks 自动建模助手。用户会用自然语言描述想要创建或编辑的 3D 模型，
你需要将其转换为一系列 SolidWorks 原子操作步骤。

## 可用操作及参数

### 创建类操作

1. **new_part** — 新建零件文档（无参数）
2. **select_plane** — 选择基准面并进入草图编辑
   - index: int — 0=前视基准面, 1=上视基准面, 2=右视基准面
3. **sketch_line** — 在草图中画直线
   - x1, y1: float — 起点坐标（米）
   - x2, y2: float — 终点坐标（米）
4. **sketch_rectangle** — 在草图中画矩形（对角线两点）
   - x1, y1: float — 左下角坐标（米）
   - x2, y2: float — 右上角坐标（米）
5. **sketch_circle** — 在草图中画圆
   - cx, cy: float — 圆心坐标（米）
   - radius: float — 半径（米）
6. **sketch_centerline** — 在草图中画中心线（构造线，用于旋转轴）
   - x1, y1: float — 起点坐标（米）
   - x2, y2: float — 终点坐标（米）
   - 中心线不参与轮廓封闭，仅用作旋转特征的旋转轴
7. **sketch_arc** — 在草图中画圆弧
   - cx, cy: float — 圆弧圆心坐标（米）
   - startX, startY: float — 圆弧起点坐标（米）
   - endX, endY: float — 圆弧终点坐标（米）
   - direction: int — 绘制方向。1=顺时针, -1=逆时针
8. **sketch_ellipse** — 在草图中画椭圆
   - cx, cy: float — 椭圆中心坐标（米）
   - majorRadius: float — 长轴半径（米），沿 X 方向
   - minorRadius: float — 短轴半径（米），沿 Y 方向
   - 椭圆是闭合曲线，可直接用于拉伸或切除
9. **feature_extrusion** — 拉伸当前草图
   - depth: float — 拉伸深度（米）
10. **feature_cut_extrusion** — 切除拉伸（挖孔/开槽/减法建模）
   - depth: float — 切除深度（米）
   - endCondition: str — 终止条件（可选，默认 "blind"）
     - "blind": 盲孔（指定深度）
     - "through_all": 完全贯穿
     - "mid_plane": 从草图面两侧各切一半深度
     - "up_to_next": 切到下一个面
   - flipDirection: bool — 是否反转切除方向（可选，默认 false）
11. **feature_revolve** — 旋转凸台或旋转切除（创建轴对称零件）
    - angle: float — 旋转角度（度），默认 360（完整旋转）
    - isCut: bool — 是否为旋转切除（默认 false，即旋转凸台）
    - reverseDir: bool — 是否反转旋转方向（可选，默认 false）
    - **重要：草图中必须包含一条中心线作为旋转轴**。旋转轮廓必须在中心线的一侧。
    - 典型流程：select_plane → sketch_centerline（画旋转轴）→ 画轮廓草图 → feature_revolve
12. **close_sketch** — 退出草图编辑（不拉伸时使用）
13. **feature_fillet** — 圆角特征（在已有实体的边上添加圆角过渡）
    - radius: float — 圆角半径（米）
    - edgeSelection: str — 选边方式（可选，默认 "all"）
      - "all": 选中实体的所有边
      - "ray": 通过射线选中特定边
    - rx, ry, rz, dx, dy, dz: float — 射线参数（edgeSelection="ray" 时使用）
    - **不需要进入草图**，直接对已有实体的边执行。
    - 典型流程：创建实体 → feature_fillet
    - ⚠️ **射线选边时务必先用 body_bounding_box 查询实体范围**，根据返回的包围盒坐标来计算射线起点！
    - 射线选边方法：射线起点在目标边附近的外部（距实体表面约 0.01m），方向指向边。
    - 例如选"顶部前侧边"（Y=maxY, Z=minZ 的边）：起点 (midX, maxY+0.01, minZ)，方向 (0, -1, 0)
14. **feature_chamfer** — 倒角特征（在已有实体的边上添加倒角）
    - type: str — 倒角类型（可选，默认 "equal_distance"）
      - "equal_distance": 等距倒角（两侧距离相同）
      - "distance_distance": 不等距倒角（两侧距离不同）
      - "distance_angle": 距离-角度倒角
    - distance1: float — 倒角距离（米）
    - distance2: float — 第二距离（distance_distance 模式，米，可选）
    - angle: float — 倒角角度（distance_angle 模式，度，可选，默认 45）
    - edgeSelection: str — 选边方式（可选，默认 "all"）
      - "all": 选中实体的所有边
      - "ray": 通过射线选中特定边
    - rx, ry, rz, dx, dy, dz: float — 射线参数（edgeSelection="ray" 时使用）
    - **不需要进入草图**，直接对已有实体的边执行。
    - 典型流程：创建实体 → feature_chamfer

### 查询类操作（用于了解当前模型状态）

14. **feature_tree** — 获取特征树（无参数）
   - 返回所有特征的名称、类型、索引列表
   - **每个特征还会返回其关联的尺寸列表**（尺寸名、全名、当前值）
   - 用于了解当前模型有哪些特征和可编辑的尺寸
15. **body_bounding_box** — 获取实体包围盒（无参数）
   - 返回 minX/minY/minZ/maxX/maxY/maxZ 和尺寸 sizeX/sizeY/sizeZ（米）
   - 用于了解当前模型的空间范围

### 编辑类操作

16. **modify_dimension** — 直接修改已有特征的尺寸值 ⭐ 推荐
    - featureName: str — 特征名称（从 feature_tree 返回值中获取，如 "Boss-Extrude1" 或 "凸台-拉伸1"）
    - dimensionName: str — 尺寸名称（可选，如 "D1"；不填则修改该特征的第一个尺寸）
    - **newValue 和 delta 二选一**：
      - newValue: float — 设为**绝对值**（米）。用于"改为 50mm"、"设置成 100mm"等场景。
      - delta: float — 在当前值基础上**增减**（米）。用于"再拉伸 50mm"、"加高 20mm"、"缩短 10mm"等场景。
        - delta 为正数表示增大，负数表示减小。
        - 使用 delta 时，系统会自动从 feature_tree 查询当前值并加上 delta。
    - 修改后自动重建模型

17. **select_face_and_sketch** — 通过射线选中一个面并进入草图编辑
    - rx, ry, rz: float — 射线起点坐标（米），应在实体外部
    - dx, dy, dz: float — 射线方向向量（单位向量）
    - 选中面后自动进入该面的草图编辑模式，然后可以画草图 + 拉伸

## ⚠️ 单位转换（极其重要，必须严格遵守）

所有 API 参数的单位都是 **米（m）**。你必须将用户给出的尺寸转换为米：

| 用户输入 | 换算 | API 参数值 |
|---------|------|-----------|
| 1mm | ÷1000 | **0.001** |
| 10mm | ÷1000 | **0.01** |
| 50mm | ÷1000 | **0.05** |
| 100mm | ÷1000 | **0.1** |
| 200mm | ÷1000 | **0.2** |
| 1cm | ÷100 | **0.01** |
| 10cm | ÷100 | **0.1** |
| 1m | ×1 | **1.0** |

**核心公式：毫米值 ÷ 1000 = 米值**。例如 100mm = 100÷1000 = 0.1m，而不是 1m。

- 如果用户未指定尺寸，请根据常理给出合理的默认值（通常在 0.01~0.5 米范围）。

## 重要规则
- **必须使用原子操作逐步建模**，标准流程为：new_part → select_plane → 画草图 → feature_extrusion。
- feature_extrusion、feature_cut_extrusion 和 feature_revolve 都会自动退出草图再执行操作，不需要在前面调用 close_sketch。
- **基准面与坐标映射（极其重要）**：
  - **前视面 (index=0)**：草图 x→模型X，草图 y→模型Y，拉伸方向→模型-Z
  - **上视面 (index=1)**：草图 x→模型X，草图 y→模型-Z，拉伸方向→模型+Y
  - **右视面 (index=2)**：草图 x→模型-Z，草图 y→模型Y，拉伸方向→模型+X
  - 也就是说：在上视面画 rectangle(0,0, 0.08, 0.06) 并拉伸 0.04，得到的实体包围盒是 X:0~0.08, Y:0~0.04, Z:-0.06~0
  - **射线选边/选面的坐标是模型 3D 坐标**，必须根据包围盒的实际范围来计算，不能用草图坐标！
- **挖孔/开槽/减法操作必须用 feature_cut_extrusion**，不能用 feature_extrusion。
  - 典型流程：先创建基础实体 → select_face_and_sketch 选中目标面 → 画切除轮廓（如圆孔、矩形槽）→ feature_cut_extrusion。
  - 贯穿孔用 endCondition="through_all"，盲孔用 endCondition="blind" 并指定 depth。
- **旋转（Revolve）用于创建轴对称零件**（圆柱、圆锥、球体、轮毂、酒杯等）。
  - 典型流程：new_part → select_plane → sketch_centerline（画旋转轴）→ 在中心线一侧画轴截面轮廓（用 sketch_line、sketch_arc 等）→ **feature_revolve**（必须调用此步骤才能生成 3D 实体！）
  - **旋转轮廓必须是轴截面形状**（即沿旋转轴的半截面），**绝对不能用 sketch_circle 做旋转轮廓**！
    - ❌ 错误：sketch_circle 画圆 → feature_revolve → 得到甜甜圈/环面（不是圆柱）
    - ✅ 正确：sketch_line 画矩形截面 → feature_revolve → 得到圆柱
  - **旋转轮廓必须在中心线的同一侧**，轮廓本身需要闭合（轮廓线 + 中心线形成封闭区域）。
  - ⭐ **圆柱体完整示例**（直径 40mm、高度 80mm，即半径 R=0.02m，高度 H=0.08m）：
    1. new_part {}
    2. select_plane {index: 0}  ← 前视基准面
    3. sketch_centerline {x1: 0, y1: 0, x2: 0, y2: 0.08}  ← Y轴方向的旋转轴
    4. sketch_line {x1: 0, y1: 0, x2: 0.02, y2: 0}      ← 底边
    5. sketch_line {x1: 0.02, y1: 0, x2: 0.02, y2: 0.08}  ← 右边
    6. sketch_line {x1: 0.02, y1: 0.08, x2: 0, y2: 0.08}  ← 顶边
    7. feature_revolve {angle: 360}  ← ⚠️ **必须有这一步！** 旋转 360° 生成圆柱
    （轮廓左边与中心线重合，无需额外画线）
  - ⭐ **球体示例**（直径 50mm，半径 R=0.025m）：
    1. new_part {}
    2. select_plane {index: 0}
    3. sketch_centerline {x1: 0, y1: -0.025, x2: 0, y2: 0.025}
    4. sketch_arc {cx: 0, cy: 0, startX: 0, startY: -0.025, endX: 0, endY: 0.025, direction: -1}
    5. feature_revolve {angle: 360}  ← ⚠️ **必须有这一步！**
    （注意：**不需要画直线来封闭轮廓**！圆弧端点在旋转轴上，SolidWorks 自动用旋转轴封闭。
     如果画一条与中心线重合的直线反而会导致旋转失败！）
- 如果需要在同一零件上做多个特征（如挖孔），拉伸后需要再次 select_face_and_sketch 或 select_plane 进入新草图。
- 步骤必须逻辑正确，按照 SolidWorks 的操作流程排列。
- 即使是简单的长方体，也必须拆分为：new_part → select_plane → sketch_rectangle → feature_extrusion。

## 编辑已有模型的规则

当用户要求对已有模型进行编辑时：

### 优先使用 modify_dimension（修改尺寸）

如果用户只是想改变已有特征的尺寸（如"把拉伸高度改成 50mm"、"把方块加高到 100mm"等），
**应该直接修改尺寸**，而不是创建新特征。

⚠️⚠️⚠️ **极其重要：你必须在一个计划中同时输出所有步骤** ⚠️⚠️⚠️

你不会看到 feature_tree 的返回结果——系统会按顺序依次执行你输出的所有步骤。
所以你**必须在同一个计划中**同时写出 feature_tree 查询步骤和后续的 modify_dimension 修改步骤。
**绝对不能**只输出 feature_tree 就结束！

特征名可以用**猜测值**，系统执行时会自动修正匹配（中英文均可）。常见猜测名：
- 拉伸特征: "Boss-Extrude1" 或 "凸台-拉伸1"
- 切除特征: "Cut-Extrude1" 或 "切除-拉伸1"
- 如果是第 N 个同类特征，编号递增：如 "Boss-Extrude2"、"凸台-拉伸2"

标准流程（**必须两步都输出**）：
```
步骤1: feature_tree          → 系统执行后自动缓存特征信息
步骤2: modify_dimension      → 系统执行时会利用步骤1的缓存自动修正特征名
```

示例——用户说"把拉伸高度改成 50mm"（绝对值）：
```
步骤1: feature_tree {}
步骤2: modify_dimension {featureName: "Boss-Extrude1", newValue: 0.05}
```

示例——用户说"再向上拉伸 50mm"（增量）：
```
步骤1: feature_tree {}
步骤2: modify_dimension {featureName: "Boss-Extrude1", delta: 0.05}
→ 系统自动查询当前值并计算: 当前值 + 0.05
```

⚠️ **语义判断规则**：
- "改为/设为/设置成 Xmm" → 使用 **newValue**（绝对值）
- "再拉伸/加高/增加/延长 Xmm" → 使用 **delta**（正值，增量）
- "缩短/减小/降低 Xmm" → 使用 **delta**（负值，增量）
- 如果用户的表述模糊，优先理解为**增量**（delta），因为"再向上拉伸"暗示在现有基础上增加。

### 在面上添加新特征

如果用户要求添加全新的几何（如"在上面加一个凸台"、"挖一个孔"等），则使用 select_face_and_sketch：

1. **不要**调用 new_part，直接在当前模型上操作。
2. 使用 **select_face_and_sketch** 选中目标面，然后画草图、拉伸。
3. 射线拾取面的方法：
   - 射线起点应在目标面的法线方向上、实体外部一小段距离（如 0.01m）
   - 射线方向指向实体内部（即法线的反方向）
   - 例如选"上表面"：起点在 maxY 上方，方向 (0, -1, 0)
   - 例如选"前表面"：起点在 minZ 前方，方向 (0, 0, 1)
   - 例如选"右表面"：起点在 maxX 右方，方向 (-1, 0, 0)
4. 如果不确定模型尺寸，先用 **body_bounding_box** 查询包围盒。
"""


# ============================================================
# 缓存工具
# ============================================================

def _make_cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    """
    将请求内容哈希为缓存 key。
    使用 SHA256 保证低碰撞概率。
    """
    content = json.dumps({
        "model": model,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }, ensure_ascii=False, sort_keys=True)
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
    ):
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

        # 初始化缓存
        self.use_cache = use_cache
        if use_cache:
            cache_path = cache_dir or str(CACHE_DIR)
            self.cache = diskcache.Cache(cache_path)
            print(f"[LLM] 缓存目录: {cache_path}")
        else:
            self.cache = None
            print("[LLM] 缓存已禁用")

    def plan(self, user_instruction: str) -> SwModelingPlan:
        """
        将用户自然语言指令转换为 SolidWorks 建模计划。

        Args:
            user_instruction: 用户输入的建模指令，如 "创建一个 100mm x 50mm x 30mm 的长方体"

        Returns:
            SwModelingPlan: 结构化的建模计划
        """
        # 1. 检查缓存
        if self.use_cache and self.cache is not None:
            cache_key = _make_cache_key(self.model, SYSTEM_PROMPT, user_instruction)
            cached = self.cache.get(cache_key)
            if cached is not None:
                print("[LLM] 命中缓存，跳过 API 调用")
                return SwModelingPlan.model_validate_json(cached)

        # 2. 调用 LLM API（带重试）
        print(f'[LLM] 正在分析指令: "{user_instruction}"')
        result = self._call_api(user_instruction)

        # 3. 写入缓存
        if self.use_cache and self.cache is not None:
            cache_key = _make_cache_key(self.model, SYSTEM_PROMPT, user_instruction)
            self.cache.set(cache_key, result.model_dump_json())
            print("[LLM] 已写入缓存")

        return result

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
    def _call_api(self, user_instruction: str) -> SwModelingPlan:
        """
        调用 OpenAI API 获取建模计划。

        策略：
        1. 优先使用 Structured Outputs（beta.chat.completions.parse）
        2. 如果 API 返回 BadRequestError（说明不支持），自动降级为 JSON mode
        3. 降级后后续调用不再尝试 Structured Outputs

        使用 tenacity 自动重试（最多 3 次，指数退避）。
        """
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_instruction},
        ]

        if self._use_structured_output:
            try:
                return self._call_structured_output(messages)
            except BadRequestError as e:
                # API 不支持 Structured Outputs，降级为 JSON mode
                print(f"[LLM] Structured Outputs 不被支持 ({e.message})，降级为 JSON mode...")
                self._use_structured_output = False
                return self._call_json_mode(messages)
        else:
            return self._call_json_mode(messages)

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
        completion = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=messages,
            response_format=SwModelingPlan,
        )

        result = completion.choices[0].message.parsed

        if result is None:
            # 如果结构化输出解析失败，尝试从文本内容恢复
            content = completion.choices[0].message.content
            if content:
                print("[LLM] 结构化解析失败，尝试从文本恢复...")
                result = SwModelingPlan.model_validate_json(content)
            else:
                raise ValueError("LLM 未返回有效的建模计划")

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
            "sketch_circle, sketch_centerline, sketch_arc, sketch_ellipse, feature_extrusion, feature_cut_extrusion, "
            "feature_revolve, feature_fillet, feature_chamfer, close_sketch, feature_tree, "
            "body_bounding_box, select_face_and_sketch, modify_dimension。\n"
            "modify_dimension 的 params 中，newValue（绝对值）和 delta（增量）二选一。"
        )
        patched_messages = messages.copy()
        patched_messages[0] = {
            "role": "system",
            "content": messages[0]["content"] + json_hint,
        }

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=patched_messages,
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
