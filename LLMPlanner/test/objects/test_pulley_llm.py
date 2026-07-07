"""
test_pulley_llm.py — 复杂物件（LLM 路径）：V 型槽皮带轮（简化）

考验能力：
  - 顶面同心双圆一次切除形成环形槽
  - 中心贯穿孔与环槽分步独立
  - 外缘倒角（feature_chamfer，非全边）

用法：
  set OPENAI_API_KEY=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_pulley_llm.py

Prompt 已同步至 object_test_yrq.md。
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL, format_volume_mm3
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请建模一个简化的 V 型槽皮带轮，严格按以下顺序：\n"
    "1. 先创建轮体：在上视基准面画直径 100mm 的圆，向上拉伸 20mm，形成实心圆盘；\n"
    "2. 在轮体顶面（Y=20mm 的面）加工环形 V 槽：画两个同心圆，外圆直径 90mm、内圆直径 70mm，"
    "向下盲切 10mm 深，形成宽度 10mm 的环形槽（槽底为平底，暂不建 V 形斜面）；\n"
    "3. 在轮体几何中心打直径 25mm 的贯穿孔（用于穿轴），此孔应切穿整个 20mm 厚度；\n"
    "4. 最后对轮缘顶部外圆边（顶面外轮廓与侧面交接的外圈边）做 C1mm 等距倒角，"
    "不要对所有边倒角。\n"
    "轮体底面落在 Y=0，顶面在 Y=20mm（不居中）。所有尺寸使用米制。"
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
        return {
            "sizeX": b["sizeX"] * 1000,
            "sizeY": b["sizeY"] * 1000,
            "sizeZ": b["sizeZ"] * 1000,
        }
    except Exception:
        return None


def soft_check_cuts(plan: SwModelingPlan) -> None:
    """槽深与中心孔终止条件软检。"""
    cuts = [s for s in plan.steps if s.operation.value == "feature_cut_extrusion"]
    for i, s in enumerate(cuts, 1):
        depth = float(s.params.get("depth", 0)) * 1000
        end = str(s.params.get("endCondition", "blind"))
        print(f"  [软检] 切除{i}: depth≈{depth:.1f}mm, endCondition={end}")


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：V 型槽皮带轮")
    print("=" * 60)
    print(f"用户 Prompt:\n{PROMPT}\n")

    client = SwLLMClient(use_cache=True)
    try:
        plan: SwModelingPlan = client.plan(PROMPT)
    finally:
        client.close()

    print(f"\nLLM 生成计划：用户意图={plan.user_intent}，共 {len(plan.steps)} 步")
    op_counts: dict[str, int] = {}
    for s in plan.steps:
        op_counts[s.operation.value] = op_counts.get(s.operation.value, 0) + 1
    print(f"操作统计: {op_counts}")
    for i, s in enumerate(plan.steps, 1):
        print(f"  {i:2d}. {s.operation.value:<22s} {s.params if s.params else '{}'}")
    if plan.notes:
        print(f"备注: {plan.notes}")

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

    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {format_volume_mm3(vol)} mm³ (理论 ≈122129 mm³)")

    soft_check_cuts(plan)

    all_ok = ok == len(results)
    ext_ok = op_counts.get("feature_extrusion", 0) >= 1
    cut_ok = op_counts.get("feature_cut_extrusion", 0) >= 2
    circle_ok = op_counts.get("sketch_circle", 0) >= 4
    chamfer_ok = op_counts.get("feature_chamfer", 0) >= 1

    sizeY_ok = bb is not None and abs(bb["sizeY"] - 20) < 2
    sizeXZ_ok = False
    if bb is not None:
        sizeXZ_ok = abs(bb["sizeX"] - 100) < 3 and abs(bb["sizeZ"] - 100) < 3

    volume_ok = vol is not None and 115000 < vol < 128000

    passed = (
        all_ok and ext_ok and cut_ok and circle_ok and chamfer_ok
        and sizeY_ok and sizeXZ_ok and volume_ok
    )
    print(f"\n判定: 全步骤成功={all_ok}, 拉伸≥1={ext_ok}, 切除≥2={cut_ok}, "
          f"画圆≥4={circle_ok}, 倒角≥1={chamfer_ok}")
    print(f"      sizeY≈20={sizeY_ok}, sizeX/Z≈100={sizeXZ_ok}, "
          f"体积∈[115k,128k]={volume_ok}")
    print(f"{'🎉 LLM 自动建皮带轮通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
