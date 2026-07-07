"""
test_flange_llm.py — 复杂物件（LLM 路径）：圆形法兰盘（6 孔螺栓连接盘）

考验能力：
  - 中心孔打通后顶面圆心为空洞，螺栓孔须落在环形实体区域选面
  - 极坐标 → 笛卡尔坐标（节圆 40mm，6 孔均布）
  - 7 次贯穿切除（1 中心 + 6 螺栓），不宜偷懒少打

用法：
  call setup_test_env.bat
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_flange_llm.py

Prompt 已同步至 object_test_yrq.md。
"""

import math
import sys

import requests

from _common import check_prerequisites, BASE_URL, format_volume_mm3
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请建模一个圆形法兰盘，**严格按以下顺序**执行，因为顺序会影响选面是否正确：\n"
    "1. 先创建盘体：在上视基准面画直径 100mm 的圆，沿厚度方向居中拉伸 12mm，形成圆盘。\n"
    "2. 接着打中心贯穿孔（**必须在打螺栓孔之前做**）：\n"
    "   在盘顶面中心打直径 30mm 的贯穿圆孔（用于穿轴）。\n"
    "3. 最后打 6 个螺栓贯穿孔（中心孔打通后，顶面圆心处已是空洞，**不能再从圆心处选面**）：\n"
    "   · 错误做法：中心孔打通后仍从盘顶圆心向下操作 —— 会误选到孔内壁而非顶面；\n"
    "   · 正确做法：每个螺栓孔应单独在顶面环形实体区域上操作（孔位在中心孔与外圆之间的环带上），\n"
    "     孔心位于节圆直径 80mm 上（到盘中心距离 40mm），孔径 8mm，全部贯穿；\n"
    "   · 6 个孔沿圆周均匀分布，相邻孔夹角 60°，第一个孔圆心在正 X 轴上（X=40mm, Z=0）；\n"
    "   · 请自行计算其余 5 个孔的圆心位置。\n"
    "4. 法兰沿厚度方向以原点居中。\n"
    "所有尺寸使用米制，注意换算（1mm = 0.001m）。"
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
        sizes = sorted([b["sizeX"] * 1000, b["sizeY"] * 1000, b["sizeZ"] * 1000])
        return sizes  # [thickness, mid, max diameter approx]
    except Exception:
        return None


def soft_check_bolt_holes(plan: SwModelingPlan) -> None:
    """螺栓孔位与选面射线抽查（软检，仅打印）。"""
    circles = [s for s in plan.steps if s.operation.value == "sketch_circle"]
    if len(circles) < 2:
        print("  [软检] 螺栓孔圆不足，跳过孔位检查")
        return

    bolt_circles = circles[1:]  # 跳过拉伸用外圆
    if len(bolt_circles) > 7:
        bolt_circles = bolt_circles[-6:]  # 取最后 6 个视为螺栓孔

    print("  [软检] 螺栓孔 sketch_circle 孔心距原点:")
    for i, s in enumerate(bolt_circles[-6:], 1):
        cx = float(s.params.get("cx", 0))
        cz = float(s.params.get("cz", s.params.get("cy", 0)))
        if "cz" not in s.params and "cy" in s.params:
            # 上视面草图可能用 cy 表示 Z
            cz = float(s.params.get("cy", 0)) if abs(float(s.params.get("cy", 0))) > 0.01 else cz
        dist = math.hypot(cx, cz) * 1000
        print(f"    孔{i}: dist≈{dist:.1f}mm (期望≈40mm)")

    face_steps = [s for s in plan.steps if s.operation.value == "select_face_and_sketch"]
    print("  [软检] select_face_and_sketch 射线起点 |rx|/|rz|:")
    for i, s in enumerate(face_steps[1:], 1):  # 跳过首次（若有）
        rx = abs(float(s.params.get("rx", 0)))
        rz = abs(float(s.params.get("rz", 0)))
        ok_ray = max(rx, rz) >= 0.02
        print(f"    第{i}次: rx={rx:.4f}, rz={rz:.4f} → 离开圆心={'✓' if ok_ray else '✗'}")


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：圆形法兰盘（6 孔）")
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
    print(f"\n包围盒(排序 mm): {bb}")
    print(f"体积: {format_volume_mm3(vol)} mm³ (理论 ≈82146 mm³)")

    soft_check_bolt_holes(plan)

    all_ok = ok == len(results)
    ext_ok = op_counts.get("feature_extrusion", 0) >= 1
    cut_ok = op_counts.get("feature_cut_extrusion", 0) >= 7
    circle_ok = op_counts.get("sketch_circle", 0) >= 7
    thickness_ok = bb is not None and abs(bb[0] - 12) < 1
    diameter_ok = bb is not None and abs(bb[2] - 100) < 3 and abs(bb[1] - 100) < 3
    volume_ok = vol is not None and 78000 < vol < 86000

    passed = all_ok and ext_ok and cut_ok and circle_ok and thickness_ok and diameter_ok and volume_ok
    print(f"\n判定: 全步骤成功={all_ok}, 拉伸≥1={ext_ok}, 切除≥7={cut_ok}, "
          f"画圆≥7={circle_ok}, 厚度≈12={thickness_ok}, 盘径≈100={diameter_ok}, "
          f"体积∈[78k,86k]={volume_ok}")
    print(f"{'🎉 LLM 自动建法兰盘通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
