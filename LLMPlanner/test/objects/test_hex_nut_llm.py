"""
test_hex_nut_llm.py — 日常物体（LLM 路径）：六角螺母（M20 标准）

考验能力：
  - LLM 能否用新的 sketch_polygon 操作画正六边形（而非试图用 6 段 sketch_line 拼接）
  - 会不会正确换算"对边距↔外接圆半径"（正六边形：外接圆半径 = 对边距 / √3）
  - 中心贯穿孔用 feature_cut_extrusion + endCondition="through_all"
  - midPlane 居中拉伸让螺母以原点为几何中心（便于后续标注/装配）

用法：
  set OPENAI_API_KEY=...
  set OPENAI_BASE_URL=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_hex_nut_llm.py

Prompt: （已同步至 TEST.md 第九节）
    "创建一个 M20 六角螺母：正六边形外形（对边距 30mm，即两条平行边之间的距离为 30mm），
     厚度 8mm，中心有一个直径 10mm 的贯穿圆孔。螺母沿厚度方向以原点居中。"
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "创建一个 M20 六角螺母：正六边形外形（对边距 30mm，即两条平行边之间的距离为 30mm），"
    "厚度 8mm，中心有一个直径 10mm 的贯穿圆孔。螺母沿厚度方向以原点居中。"
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
    print("LLM 自动建模：六角螺母（M20）")
    print("=" * 60)
    print(f"用户 Prompt:\n{PROMPT}\n")

    # 1. LLM 规划
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
    #    正六边形对边距 s=30mm、外接圆半径 R = s/√3 ≈ 17.32mm
    #    - 若"尖朝上"（顶点在正上方）：包围盒宽（对边距方向 X）=30mm、包围盒高（顶点方向 Z）=2R≈34.64mm
    #    - 若"平放"（一条边水平在下方）：包围盒宽（顶点方向 X）=2R≈34.64mm、包围盒高（对边距方向 Z）=30mm
    #    厚度 8mm 沿拉伸轴，若居中则包围盒该轴=8mm。
    #    体积理论 = (3√3/2)·R²·h − π·5²·8 ≈ 2.598·17.32²·8 − 200π
    #             = 2.598·300·8 − 628 = 6235 − 628 = 5607 mm³
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论 ≈5607 mm³)")

    # 判定：全步骤成功 + 有 polygon + 有 cut_extrusion + 三个轴中最小的应≈8（厚度）
    polygon_count = op_counts.get("sketch_polygon", 0)
    cut_count = op_counts.get("feature_cut_extrusion", 0)

    thickness_ok = False
    outer_ok = False
    if bb is not None:
        # 找出厚度轴：应 ≈ 8mm，容差 1mm
        thickness_ok = any(abs(s - 8) < 1 for s in bb)
        # 剩余两轴应为对边距和 2R 的组合（分别 30mm 与 ≈34.64mm）
        # 允许 ±3mm 容差
        others = sorted(bb)
        # 排除最小的厚度轴之后，剩下两轴中一个≈30、一个≈34.64
        others_no_thickness = [s for s in others if abs(s - 8) >= 1]
        if len(others_no_thickness) >= 2:
            outer_ok = (any(abs(s - 30) < 3 for s in others_no_thickness)
                        and any(abs(s - 34.64) < 3 for s in others_no_thickness))

    volume_ok = (vol is not None and 4500 < vol < 6500)

    passed = (ok == len(results)) and (polygon_count >= 1) and (cut_count >= 1) \
             and thickness_ok and outer_ok and volume_ok
    print(f"\n判定: 全步骤成功={ok == len(results)}, sketch_polygon 步数={polygon_count}, "
          f"cut_extrusion 步数={cut_count}, 厚度≈8mm={thickness_ok}, 六边形外接尺寸对={outer_ok}, "
          f"体积∈[4500,6500]={volume_ok}")
    print(f"{'🎉 LLM 自动建六角螺母通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
