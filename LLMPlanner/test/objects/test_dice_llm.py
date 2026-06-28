"""
test_dice_llm.py — LLM 路径：自然语言 → GPT-4o 规划 → 自动建一颗标准骰子

与 test_dice_full.py（人工写死坐标的确定性 API 序列）不同，本测试**完全依赖 LLM 的规划能力**：
  - prompt 中只给"规则/领域知识"（尺寸、圆角、凹陷点数、点数排布样式、对面和为7），
  - 不给任何 3D 坐标、射线参数、API 调用顺序——这些必须由 LLM 自己推算。

这考验 LLM 能否：
  1. 把 mm 换算成米
  2. 推算立方体包围盒，为每个面计算选面射线
  3. 在每个面上计算各凹点的模型 3D 坐标（含固定到面所在平面的那一维）
  4. 按标准点数样式布置 1~6 点
  5. 用 feature_cut_extrusion(flipDirection=false) 朝内挖出凹坑

验证：执行成功率 + 最终包围盒(≈20mm) + 体积（21 个凹点 + 棱边圆角应使体积比 8000 略小）。

用法：
  set OPENAI_API_KEY=...  &  set OPENAI_BASE_URL=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_dice_llm.py
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


# 仅描述"骰子是什么样、遵循什么规则"，不含任何坐标/射线/步骤——答案由 LLM 自己规划
PROMPT = (
    "请帮我建模一颗标准的六面骰子（色子），要求如下：\n"
    "1. 骰子主体是一个边长 20mm 的正方体；\n"
    "2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；\n"
    "3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个直径约 3mm、"
    "深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；\n"
    "4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、3 对 4）；\n"
    "5. 各面点数的常规排布：1 点在面中心；2 点和 3 点沿面的一条对角线均匀分布；"
    "4 点在面的四个角附近；5 点是四个角加面中心；6 点是两列、每列三个点。\n"
    "请自行计算每个面以及每个凹点在模型空间中的位置，完成完整建模。"
)


def volume_mm3():
    try:
        r = requests.get(f"{BASE_URL}/api/body-volume", timeout=60).json()
        return r.get("volumeMm3") if r.get("success") else None
    except Exception:
        return None


def bbox_mm():
    try:
        r = requests.get(f"{BASE_URL}/api/body-bounding-box", timeout=60).json()
        if not r.get("success"):
            return None
        b = r["boundingBox"]
        return (b["sizeX"] * 1000, b["sizeY"] * 1000, b["sizeZ"] * 1000)
    except Exception:
        return None


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：标准骰子（完全依赖 LLM 规划）")
    print("=" * 60)
    print(f"用户 Prompt:\n{PROMPT}\n")

    # 1. LLM 规划
    client = SwLLMClient(use_cache=True)
    try:
        plan: SwModelingPlan = client.plan(PROMPT)
    finally:
        client.close()

    print(f"\nLLM 生成计划：用户意图={plan.user_intent}，共 {len(plan.steps)} 步")
    # 统计各类操作数量，便于快速判断规划质量
    op_counts: dict[str, int] = {}
    for s in plan.steps:
        op_counts[s.operation.value] = op_counts.get(s.operation.value, 0) + 1
    print(f"操作统计: {op_counts}")
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

    # 3. 几何验证
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论参考: 8000 立方体 − 12棱圆角 − 21凹点≈ 7600~7900)")

    cut_count = op_counts.get("feature_cut_extrusion", 0)
    fillet_count = op_counts.get("feature_fillet", 0)
    bbox_ok = bb is not None and all(abs(v - 20) < 3 for v in bb)
    passed = (ok == len(results)) and bbox_ok and cut_count >= 1
    print(f"\n判定: 全步骤成功={ok == len(results)}, 包围盒≈20={bbox_ok}, "
          f"切除步数={cut_count}, 圆角步数={fillet_count}")
    print(f"{'🎉 LLM 自动建骰子通过！' if passed else '⚠️ 未完全通过，见上方失败步骤/几何'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
