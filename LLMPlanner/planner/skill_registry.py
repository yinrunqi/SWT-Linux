"""
skill_registry.py — 文件驱动的 Skill 动态加载

设计目标：
- Prompt 从 400 行硬编码 SYSTEM_PROMPT 拆分为 planner/skills/ 下的多个 Markdown 文件
- 加一个物体的建模知识 = 新增一个 md，零 Python 改动
- 每个 skill 用 YAML frontmatter 声明元数据（name/category/description/triggers/applies_to），正文是喂给 LLM 的内容

目录约定：
    planner/skills/
        core/            # 稳定的通用手册（操作/单位/规则/编辑），恒定全量注入
        recipes/         # 各物体的具体示例（板凳/骰子/钥匙/螺母…），按 triggers 关键词命中注入
        input_modes/     # 输入模态 skill（三视图/工程图/手绘草图），按 image_meta 命中注入

Frontmatter 语法（极小 YAML 子集，无需 pyyaml 依赖）：
    ---
    name: stool
    category: recipe          # 可选，通常由所在目录推断
    description: 四脚板凳/桌子 —— 凳面+四腿+圆角
    triggers: [板凳, 凳子, 桌子, stool, 四脚]
    applies_to: [text, three_view]     # 可选，默认 [text]
    ---
    （正文……）

零新增依赖：不引入 pyyaml。frontmatter 用 --- 分隔，键值行按简单规则解析：
    "key: value"                → 字符串
    "key: [a, b, c]"            → 列表
    "key: true/false/yes/no"    → 布尔
    其它均按字符串处理
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

# ============================================================
# 数据结构
# ============================================================


@dataclass
class Skill:
    """加载后的一条 Skill。"""

    name: str
    category: str  # core / recipe / input_mode
    description: str
    triggers: list[str] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=lambda: ["text"])
    body: str = ""
    source_path: Optional[Path] = None

    def matches_trigger(self, text: str) -> int:
        """返回命中的 trigger 关键词数量（用于打分）。大小写不敏感。"""
        if not text or not self.triggers:
            return 0
        lower = text.lower()
        return sum(1 for kw in self.triggers if kw and kw.lower() in lower)

    def applies(self, input_mode: str) -> bool:
        """该 skill 是否适用于给定的输入模式（text / three_view / …）。"""
        return not self.applies_to or input_mode in self.applies_to


# ============================================================
# 极小 Frontmatter 解析（不引 pyyaml）
# ============================================================

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_scalar(raw: str):
    """把 'value' / 'true' / '[a, b]' 解析成对应的 Python 值。"""
    s = raw.strip()
    if not s:
        return ""
    # 列表：[a, b, c]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        items = [it.strip().strip("'").strip('"') for it in inner.split(",")]
        return [it for it in items if it]
    # 布尔
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    # 去掉两端引号
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    解析 Markdown 顶部的 --- ... --- YAML 子集 frontmatter。

    返回 (metadata_dict, body_text)。
    如果没有 frontmatter，metadata 为空 dict，body 为原文。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    front_raw = m.group(1)
    body = text[m.end():]

    meta: dict = {}
    for line in front_raw.splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = _parse_scalar(value)

    return meta, body


# ============================================================
# SkillRegistry — 扫描并加载所有 skills
# ============================================================


class SkillRegistry:
    """
    扫描 planner/skills/ 目录，加载全部 Markdown skill 到内存。

    用法：
        reg = SkillRegistry(Path("planner/skills"))
        reg.load()
        cores = reg.get_core()          # 恒定注入的 core skills（按文件名排序）
        recipes = reg.get_recipes()     # 所有 recipe 类 skill
        modes = reg.get_input_modes()   # 所有 input_mode 类 skill
    """

    CATEGORY_DIRS = {
        "core": "core",
        "recipe": "recipes",
        "input_mode": "input_modes",
    }

    def __init__(self, root: Path):
        self.root = Path(root)
        self._skills: list[Skill] = []

    def load(self) -> None:
        """扫描目录，重新加载全部 skills。"""
        self._skills = []
        if not self.root.exists():
            return

        for category, subdir_name in self.CATEGORY_DIRS.items():
            subdir = self.root / subdir_name
            if not subdir.exists():
                continue
            # 按文件名字典序加载（core 里 00_/01_/02_ 数字前缀决定顺序）
            for md_path in sorted(subdir.glob("*.md")):
                skill = self._load_file(md_path, default_category=category)
                if skill is not None:
                    self._skills.append(skill)

    def _load_file(self, path: Path, default_category: str) -> Optional[Skill]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[SkillRegistry] 读取 {path} 失败: {e}")
            return None

        meta, body = parse_frontmatter(text)

        name = str(meta.get("name") or path.stem)
        category = str(meta.get("category") or default_category)
        description = str(meta.get("description") or "")
        triggers = meta.get("triggers") or []
        if isinstance(triggers, str):
            triggers = [triggers]
        applies_to = meta.get("applies_to") or ["text"]
        if isinstance(applies_to, str):
            applies_to = [applies_to]

        return Skill(
            name=name,
            category=category,
            description=description,
            triggers=list(triggers),
            applies_to=list(applies_to),
            body=body.strip("\n"),
            source_path=path,
        )

    # ---- 查询接口 ----

    @property
    def all_skills(self) -> list[Skill]:
        return list(self._skills)

    def get_by_category(self, category: str) -> list[Skill]:
        return [s for s in self._skills if s.category == category]

    def get_core(self) -> list[Skill]:
        return self.get_by_category("core")

    def get_recipes(self) -> list[Skill]:
        return self.get_by_category("recipe")

    def get_input_modes(self) -> list[Skill]:
        return self.get_by_category("input_mode")

    def get_by_name(self, name: str) -> Optional[Skill]:
        for s in self._skills:
            if s.name == name:
                return s
        return None


# ============================================================
# SkillSelector — 路由（关键词打分挑 top-K）
# ============================================================


class SkillSelector:
    """
    根据用户指令和输入模式，从 registry 中挑选相关的 recipe/input_mode skills。

    路由策略（轻量优先，不调 LLM）：
    - core skills 恒定全量注入（不参与筛选）
    - recipes：按 triggers 关键词命中数打分，取 top-K（默认 K=3）
    - input_modes：根据 input_mode 参数精确匹配 applies_to
    - 若指令为空或无 recipe 命中，返回空列表（core 就够用）
    """

    def __init__(self, registry: SkillRegistry, top_k_recipes: int = 3):
        self.registry = registry
        self.top_k_recipes = top_k_recipes

    def select(
        self,
        instruction: str = "",
        input_mode: str = "text",
    ) -> list[Skill]:
        """
        返回一个有序列表：命中的 recipe skills（按分数降序）+ 命中的 input_mode skills。
        core skills 由 build_system_prompt() 直接从 registry 取，不在这里返回。
        """
        picked: list[Skill] = []

        # 1) recipe 路由：关键词打分
        recipe_scored: list[tuple[int, Skill]] = []
        for s in self.registry.get_recipes():
            if not s.applies(input_mode):
                continue
            score = s.matches_trigger(instruction)
            if score > 0:
                recipe_scored.append((score, s))
        recipe_scored.sort(key=lambda x: (-x[0], x[1].name))
        picked.extend([s for _, s in recipe_scored[: self.top_k_recipes]])

        # 2) input_mode 路由：精确匹配
        for s in self.registry.get_input_modes():
            if input_mode in s.applies_to or (
                input_mode != "text" and s.name == input_mode
            ):
                picked.append(s)

        return picked


# ============================================================
# build_system_prompt — 组装最终 System Prompt
# ============================================================


def build_system_prompt(
    registry: SkillRegistry,
    selected: Iterable[Skill] = (),
    *,
    include_all_recipes: bool = False,
    header: Optional[str] = None,
) -> str:
    """
    把 core skills（全量）+ selected skills（recipe/input_mode）拼装成完整的 System Prompt。

    Args:
        registry: 已 load() 的 SkillRegistry
        selected: SkillSelector.select() 返回的命中列表
        include_all_recipes: 若为 True，忽略 selected，把 registry 中所有 recipe 也全量注入
            —— 用于"零回归验证阶段"，此时组装出的 prompt 应与迁移前的整段 SYSTEM_PROMPT 等价
        header: 可选的顶部说明（默认使用一段固定说明）

    Returns:
        拼装后的 System Prompt 纯文本
    """
    parts: list[str] = []

    # 可选 header（默认不注入 —— 由 core/_00_header.md 提供开场白）
    if header:
        parts.append(header)

    # 1) core skills：全量注入，按文件名（load 时已排好序）保持稳定顺序
    for s in registry.get_core():
        parts.append(s.body)

    # 2) recipes / input_modes：按 selected 或全量
    if include_all_recipes:
        for s in registry.get_recipes():
            parts.append(s.body)
        for s in registry.get_input_modes():
            parts.append(s.body)
    else:
        seen: set[str] = set()
        for s in selected:
            key = f"{s.category}:{s.name}"
            if key in seen:
                continue
            seen.add(key)
            parts.append(s.body)

    # 用两个换行连接，保持段落清晰；去掉多余空行
    return "\n\n".join(p.strip("\n") for p in parts if p and p.strip())


# ============================================================
# 便捷函数
# ============================================================


_DEFAULT_ROOT = Path(__file__).resolve().parent / "skills"


def default_registry() -> SkillRegistry:
    """返回加载好的默认 registry（planner/skills/）。"""
    reg = SkillRegistry(_DEFAULT_ROOT)
    reg.load()
    return reg
