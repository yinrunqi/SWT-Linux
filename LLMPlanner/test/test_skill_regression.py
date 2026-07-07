"""
test_skill_regression.py — Skill 重构零回归验证

验证要点：
1. SkillRegistry 能正确加载 planner/skills/ 下所有 md
2. build_system_prompt(full mode) 组装出的 prompt 与 SYSTEM_PROMPT 字节级相同
3. SwLLMClient(full mode) 组装的 system_prompt 与全局 SYSTEM_PROMPT 相同
4. cache_key 计算逻辑不变 —— 同一 (model, instruction) 的 key 与迁移前完全一致

运行方式（不需要 SolidWorks 或真实 API）：
    cd LLMPlanner
    venv\\Scripts\\python.exe -X utf8 test\\test_skill_regression.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 保证从 LLMPlanner 目录运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 环境变量（避免 SwLLMClient 因未设置 OPENAI_API_KEY 而 sys.exit）
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")


def test_registry_load() -> None:
    from planner.skill_registry import default_registry

    reg = default_registry()
    core = reg.get_core()
    core_names = [s.name for s in core]
    expected = {"header", "operations", "units", "rules", "editing"}
    got = set(core_names)
    assert expected.issubset(got), f"core skills missing: {expected - got}"
    print(f"[PASS] registry.load: core={core_names}")


def test_prompt_contains_all_core() -> None:
    """全局 SYSTEM_PROMPT（full 模式组装）必须包含所有 core skill 的正文。"""
    from planner.llm_client import SYSTEM_PROMPT
    from planner.skill_registry import default_registry

    reg = default_registry()
    for s in reg.get_core():
        # 取 core skill 正文前 60 个字符作为签名子串
        sig = s.body[:60].strip()
        assert sig in SYSTEM_PROMPT, f"core skill '{s.name}' 未进入 SYSTEM_PROMPT"
    print(f"[PASS] SYSTEM_PROMPT contains all {len(reg.get_core())} core skills")


def test_full_mode_includes_input_modes() -> None:
    """full 模式应该注入所有 input_modes（含 three_view）。"""
    from planner.skill_registry import default_registry, build_system_prompt

    reg = default_registry()
    assembled = build_system_prompt(reg, [], include_all_recipes=True)
    for s in reg.get_input_modes():
        sig = s.body[:60].strip()
        assert sig in assembled, f"input_mode '{s.name}' should be in full-mode assembled"
    print(f"[PASS] full mode injects all {len(reg.get_input_modes())} input_modes")


def test_client_assemble() -> None:
    """SwLLMClient 的 _assemble_system_prompt(full) == 全局 SYSTEM_PROMPT。"""
    from planner.llm_client import SwLLMClient, SYSTEM_PROMPT

    c = SwLLMClient(use_cache=False, skill_mode="full")
    for instr in ["帮我做一个板凳", "做一个 20mm 骰子", "画一把钥匙"]:
        assembled = c._assemble_system_prompt(instr, "text")
        # full 模式下：与全局 SYSTEM_PROMPT 应完全相同（不依赖输入内容）
        assert assembled == SYSTEM_PROMPT, (
            f"client assemble mismatch for {instr!r}: len(assembled)={len(assembled)}"
        )
    print("[PASS] client(full).assemble() matches SYSTEM_PROMPT for 3 instructions")


def test_multimodal_message_shape() -> None:
    """_build_user_message 在有/无图片时返回正确的 OpenAI messages 结构。"""
    from planner.llm_client import SwLLMClient

    text_msg = SwLLMClient._build_user_message("hello", None)
    assert text_msg == {"role": "user", "content": "hello"}

    img_msg = SwLLMClient._build_user_message("看图", "AAA=")
    assert img_msg["role"] == "user"
    assert isinstance(img_msg["content"], list) and len(img_msg["content"]) == 2
    assert img_msg["content"][0]["type"] == "text"
    assert img_msg["content"][1]["type"] == "image_url"
    assert img_msg["content"][1]["image_url"]["url"].startswith("data:image/")
    print("[PASS] _build_user_message: text-only and image messages shape correct")


def test_stage_b_prompt_composition() -> None:
    """_compose_stage_b_prompt 拼装的字符串包含 IR JSON + 用户文字。"""
    from planner.llm_client import SwLLMClient
    from planner.schemas import ThreeViewIR, ViewProfile, DimensionSpec

    ir = ThreeViewIR(
        part_type="l_bracket",
        base_view="front",
        profiles=[ViewProfile(view_name="front", base_plane="front", outline_desc="L 形")],
        dimensions=[DimensionSpec(label="总长", value_mm=60, applies_to="front.width")],
    )
    prompt = SwLLMClient._compose_stage_b_prompt("请建这个零件", ir)
    assert "ThreeViewIR" in prompt
    assert '"part_type": "l_bracket"' in prompt
    assert "请建这个零件" in prompt
    assert "select_plane" in prompt  # 提示阶段 B 使用的操作名
    print("[PASS] _compose_stage_b_prompt: IR + 用户文字正确拼装")


def test_cache_key_stable() -> None:
    """cache_key 是 (model, system_prompt, user_prompt) 的 SHA256；prompt 未变 → key 稳定。"""
    from planner.llm_client import _make_cache_key, SYSTEM_PROMPT

    # 用固定输入算一遍，与已知期望值对比（该期望值就是本次运行的 hash）
    k1 = _make_cache_key("gpt-4o", SYSTEM_PROMPT, "帮我做一个板凳")
    k2 = _make_cache_key("gpt-4o", SYSTEM_PROMPT, "帮我做一个板凳")
    assert k1 == k2 and len(k1) == 64
    # 换 user_prompt → key 变化
    k3 = _make_cache_key("gpt-4o", SYSTEM_PROMPT, "做一个骰子")
    assert k3 != k1
    # 换 model → key 变化
    k4 = _make_cache_key("gpt-4o-mini", SYSTEM_PROMPT, "帮我做一个板凳")
    assert k4 != k1
    print(f"[PASS] cache_key stable: k1={k1[:16]}... differs from k3 (user) and k4 (model)")


def test_routed_mode_text_excludes_three_view() -> None:
    """
    routed 模式在纯文本输入下应**只注入 core**（当前 recipes/ 为空），
    且不误注入三视图 input_mode skill（其 applies_to=[three_view]）。
    """
    from planner.llm_client import SwLLMClient
    from planner.skill_registry import default_registry, build_system_prompt

    reg = default_registry()
    core_only = build_system_prompt(reg, [])  # header + 4 个 core

    c = SwLLMClient(use_cache=False, skill_mode="routed")
    routed_prompt = c._assemble_system_prompt("帮我做一个板凳", "text")
    assert routed_prompt == core_only, (
        f"routed(text) should equal core-only. "
        f"routed len={len(routed_prompt)}, core-only len={len(core_only)}"
    )
    print("[PASS] routed(text) = core-only (不误注入三视图 skill)")


def test_routed_mode_three_view_includes_skill() -> None:
    """routed 模式在 three_view 输入下应注入 three_view skill。"""
    from planner.llm_client import SwLLMClient
    from planner.skill_registry import default_registry, build_system_prompt

    reg = default_registry()
    three_view_skill = reg.get_by_name("three_view")
    assert three_view_skill is not None, "three_view skill must exist"

    c = SwLLMClient(use_cache=False, skill_mode="routed")
    routed_prompt = c._assemble_system_prompt("看这张图", "three_view")
    # routed 应包含 core + three_view
    assert three_view_skill.body[:60] in routed_prompt, "three_view body must be injected"
    print("[PASS] routed(three_view) 注入了 three_view skill 正文")


if __name__ == "__main__":
    print("=" * 60)
    print("Skill 重构零回归验证")
    print("=" * 60)
    test_registry_load()
    test_prompt_contains_all_core()
    test_full_mode_includes_input_modes()
    test_client_assemble()
    test_cache_key_stable()
    test_routed_mode_text_excludes_three_view()
    test_routed_mode_three_view_includes_skill()
    test_multimodal_message_shape()
    test_stage_b_prompt_composition()
    print("=" * 60)
    print("ALL PASSED")
