"""
_TEMPLATE_new_object.py — 新增一个"自动建模物体"测试脚本的骨架模板

⚠️ 本文件仅作参考模板，**不会**被运行。真实新增物体时，请：
    1. 复制本文件为 test_<your_name>_llm.py
    2. 修改 PROMPT / OBJECT_NAME / 几何断言
    3. 按 TEST.md 顶部"强约定"完成剩余四步（TEST.md 分类表 + 完整 Prompt + Web demo 按钮 + commit）

用法：
    set OPENAI_API_KEY=<your-key>
    set OPENAI_BASE_URL=<your-endpoint>
    venv\\Scripts\\python.exe -X utf8 test\\objects\\test_<your_name>_llm.py

Prompt: （必须与 TEST.md 中收录的一模一样）
    "…这里写用户提交给 LLM 的完整自然语言指令…"
"""
from __future__ import annotations

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


# ============================================================
# 【1/5】改这些常量：物体名字、Prompt、几何预期
# ============================================================

OBJECT_NAME = "示例物体"

# 用户 Prompt —— 必须与 TEST.md 里收录的完全一致
PROMPT = (
    "创建一个 …（在这里描述物体的形状、尺寸、特征、约束）"
)

# 期望的包围盒（mm）—— 用于自动几何断言
EXPECTED_BBOX_MM = (100.0, 50.0, 30.0)  # 三个轴任意排列后应命中
BBOX_TOLERANCE_MM = 3.0

# 期望的体积（mm³）—— 用于自动几何断言
EXPECTED_VOLUME_MM3 = 100 * 50 * 30  # = 150000
VOLUME_TOLERANCE_RATIO = 0.05  # ±5%


# ============================================================
# 通用几何查询（沿用其他 test_*_llm 的模式，无需修改）
# ============================================================


def bbox_mm():
    try:
        r = requests.get(f"{BASE_URL}/api/body-bounding-box", timeout=60).json()
        if not r.get("success"):
            return None
        b = r["boundingBox"]
        return (b["sizeX"] * 1000, b["sizeY"] * 1000, b["sizeZ"] * 1000)
    except Exception:
        return None


def volume_mm3():
    try:
        r = requests.get(f"{BASE_URL}/api/body-volume", timeout=60).json()
        return r.get("volumeMm3") if r.get("success") else None
    except Exception:
        return None


# ============================================================
# 主流程
# ============================================================


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print(f"LLM 自动建模：{OBJECT_NAME}")
    print("=" * 60)
    print(f"用户 Prompt:\n{PROMPT}\n")

    # 1. LLM 规划
    client = SwLLMClient(use_cache=True)
    try:
        plan: SwModelingPlan = client.plan(PROMPT)
    finally:
        client.close()

    print(f"\nLLM 生成计划：intent={plan.user_intent}，共 {len(plan.steps)} 步")
    for i, s in enumerate(plan.steps, 1):
        print(f"  {i:2d}. {s.operation.value:<22s} {s.params if s.params else '{}'}")
    if plan.notes:
        print(f"备注: {plan.notes}")

    # 2. 执行
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    ok = sum(1 for r in results if r.get("success"))
    print(f"\n执行结果: {ok}/{len(results)} 步成功")
    for r in results:
        if not r.get("success", False):
            print(f"  ❌ 失败步骤: {r.get('operation')} — {r.get('message')}")

    # 3. 【2/5】几何断言 —— 至少要有包围盒/体积/特征数中的一项
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm  (期望 {EXPECTED_BBOX_MM} ±{BBOX_TOLERANCE_MM}mm 三轴任意排列)")
    vol_str = f"{vol:.1f}" if vol else "None"
    print(f"体积: {vol_str} mm³  (期望 {EXPECTED_VOLUME_MM3} ±{VOLUME_TOLERANCE_RATIO*100:.0f}%)")

    if bb is None or vol is None:
        print("❌ 无法读取几何")
        return False

    sizes_sorted = sorted(bb)
    expected_sorted = sorted(EXPECTED_BBOX_MM)
    bbox_ok = all(abs(a - b) < BBOX_TOLERANCE_MM for a, b in zip(sizes_sorted, expected_sorted))
    volume_ok = abs(vol - EXPECTED_VOLUME_MM3) / EXPECTED_VOLUME_MM3 < VOLUME_TOLERANCE_RATIO

    passed = (ok == len(results)) and bbox_ok and volume_ok
    print(
        f"\n判定: 步骤成功={ok == len(results)}, "
        f"包围盒±{BBOX_TOLERANCE_MM}mm={bbox_ok}, "
        f"体积±{VOLUME_TOLERANCE_RATIO*100:.0f}%={volume_ok}"
    )
    print("🎉 通过！" if passed else "⚠️ 未完全通过")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)


# ============================================================
# 【提醒】完成本测试脚本后，还有 4 件事：
#
# 3/5  → 在 TEST.md 分类表新增一行 + 合适章节写完整 Prompt: 原文
# 4/5  → web/static/index.html 加示例按钮 <button onclick="fill<Name>Prompt()">
#        web/static/app.js    加同名函数 window.fill<Name>Prompt = () => { inputEl.value = "..."; }
# 5/5  → git add . && git commit -m "feat(<name>): 端到端 X/X 步通过, 体积 Y mm³" && git push
#
# 完整清单见 CODEBUDDY.md 项目约定 / TEST.md 顶部"强约定"。
# ============================================================
