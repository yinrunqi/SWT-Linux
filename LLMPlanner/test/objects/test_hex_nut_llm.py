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
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论 ≈5607 mm³)")

    # 额外验证：孔必须在几何中心（六边形中心线上）。
    # 通过 LLM 计划里的 sketch_circle 参数直接检查——命令行/网页都会调用它。
    # 螺母 midPlane 拉伸后顶面 Y=+0.004，底面 Y=-0.004；圆心的面内坐标 (cx, cz) 必须都 ≈0。
    circle_step = next((s for s in plan.steps if s.operation.value == "sketch_circle"), None)
    hole_centered = False
    if circle_step is not None:
        cx = float(circle_step.params.get("cx", 0))
        cz = float(circle_step.params.get("cz", 0))
        cy = float(circle_step.params.get("cy", 0))
        # 顶面 Y 法向：法向轴 cy 应≈±0.004，面内轴 cx、cz 都必须≈0（容差 0.5mm）
        # 若螺母居中，则接受 cy=±0.004；若非居中拉伸则 cy=0.008 或 0（也允许）
        hole_centered = (abs(cx) < 0.0005) and (abs(cz) < 0.0005)
        print(f"孔中心 sketch_circle 参数: cx={cx}, cy={cy}, cz={cz} → 中心对齐(面内 cx,cz≈0)={hole_centered}")

    polygon_count = op_counts.get("sketch_polygon", 0)
    cut_count = op_counts.get("feature_cut_extrusion", 0)

    thickness_ok = False
    outer_ok = False
    if bb is not None:
        thickness_ok = any(abs(s - 8) < 1 for s in bb)
        others_no_thickness = [s for s in bb if abs(s - 8) >= 1]
        if len(others_no_thickness) >= 2:
            outer_ok = (any(abs(s - 30) < 3 for s in others_no_thickness)
                        and any(abs(s - 34.64) < 3 for s in others_no_thickness))

    volume_ok = (vol is not None and 4500 < vol < 6500)

    passed = (ok == len(results)) and (polygon_count >= 1) and (cut_count >= 1) \
             and thickness_ok and outer_ok and volume_ok and hole_centered
    print(f"\n判定: 全步骤成功={ok == len(results)}, sketch_polygon 步数={polygon_count}, "
          f"cut_extrusion 步数={cut_count}, 厚度≈8mm={thickness_ok}, 六边形外接尺寸对={outer_ok}, "
          f"体积∈[4500,6500]={volume_ok}, 孔在中心={hole_centered}")
    print(f"{'🎉 LLM 自动建六角螺母通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
