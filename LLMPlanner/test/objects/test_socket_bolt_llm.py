"""
test_socket_bolt_llm.py — 复杂物件（LLM 路径）：内六角圆柱头螺栓

考验能力：
  - sketch_polygon 画内六角（对边距 8mm → 外接圆半径 8/√3）
  - 螺杆 + 头部两次加料，内六角盲切
  - 头杆台阶圆角（圆柱零件须用 ray 选边，非 bottom_front 等方盒语义）

用法：
  set OPENAI_API_KEY=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_socket_bolt_llm.py

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
    "请建模一颗简化的内六角圆柱头螺栓（不需要螺纹），**严格按以下顺序**执行，因为顺序会影响形状是否正确。\n"
    "【坐标约定】在上视基准面建模：Y 轴向上为螺栓轴线，原点在螺杆底面中心。\n"
    "【整体尺寸一览】（总高 56mm，Y=0 ~ Y=56mm）\n"
    "· 螺杆：直径 10mm（半径 5mm），长 50mm，Y=0 ~ Y=50mm\n"
    "· 圆柱头：直径 18mm（半径 9mm），高 6mm，Y=50 ~ Y=56mm（与螺杆同轴，台阶在 Y=50mm）\n"
    "· 内六角：在头部顶面正中心，正六边形对边距 8mm，向下盲挖 4mm（挖到 Y=52mm 即止）\n"
    "· 头杆圆角：Y=50mm 台阶圈边 R0.5mm\n"
    "1. 先建螺杆：在上视基准面画直径 10mm 的圆（圆心在原点），向上拉伸 50mm，形成杆部（Y=0 ~ Y=50mm）。\n"
    "2. 再加圆柱头（**必须在挖内六角之前加**，且是**加料**而不是切除）：\n"
    "   在螺杆顶面（Y=50mm）画直径 18mm 的圆（圆心仍在轴线上），向上拉伸 6mm，\n"
    "   形成比螺杆粗的圆柱头（Y=50 ~ Y=56mm）。\n"
    "3. 再挖内六角盲孔（**必须是盲孔，深度 4mm，不要贯穿 6mm 高的头部**）：\n"
    "   从头部顶面（Y=56mm）正中心向下挖，画正六边形内六角，对边距 8mm，深度 4mm。\n"
    "   · 对边距 8mm 的正六边形，外接圆半径 = 8/√3 ≈ 4.619mm（**不是** 半对边距 4mm）；\n"
    "   · 只向下挖 4mm 到 Y=52mm，不要用贯穿方式切穿整个头部。\n"
    "4. 最后做头杆过渡圆角：仅在 Y=50mm 处、头部底面与螺杆顶面交界的那圈棱边做 R0.5mm 圆角，"
    "不要对所有边圆角。\n"
    "常见错误：\n"
    "· 顺序颠倒（先挖内六角再加头部）—— 无法正确建模；\n"
    "· 把加圆柱头写成切除 —— 头部做不出来；\n"
    "· 内六角用贯穿切除 —— 6mm 高的头部会被切穿；\n"
    "· 六边形外接圆半径算成 4mm —— 应为 4.619mm；\n"
    "· 用 6 条独立线段拼六边形 —— 应一次画出正六边形。\n"
    "所有尺寸使用米制，注意换算（1mm = 0.001m）。"
)

EXPECTED_HEX_R_MM = 8 / math.sqrt(3)


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


def soft_check_hex(plan: SwModelingPlan) -> bool:
    """内六角外接圆半径与圆角选边软检。"""
    hex_ok = False
    for s in plan.steps:
        if s.operation.value != "sketch_polygon":
            continue
        r_m = float(s.params.get("circumradius", 0))
        r_mm = r_m * 1000
        hex_ok = abs(r_mm - EXPECTED_HEX_R_MM) < 0.5
        print(f"  [软检] sketch_polygon circumradius={r_mm:.3f}mm "
              f"(期望≈{EXPECTED_HEX_R_MM:.3f}mm) → {'✓' if hex_ok else '✗'}")

    for s in plan.steps:
        if s.operation.value != "feature_fillet":
            continue
        sel = str(s.params.get("edgeSelection", "all"))
        box_semantic = sel.lower() in {
            "bottom_front", "bottom_back", "top_front", "top_back",
            "top_left", "top_right", "bottom_left", "bottom_right",
        }
        if box_semantic:
            print(f"  [软检] ⚠️ 圆柱台阶圆角误用方盒语义 edgeSelection={sel!r}，应改用 ray")
        elif sel.lower() == "ray":
            print(f"  [软检] feature_fillet 使用 ray 选边 ✓")
        else:
            print(f"  [软检] feature_fillet edgeSelection={sel!r}")

    return hex_ok


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：内六角圆柱头螺栓")
    print("=" * 60)
    print(f"用户 Prompt:\n{PROMPT}\n")

    client = SwLLMClient(use_cache=True, skill_mode="routed")
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
    print(f"体积: {format_volume_mm3(vol)} mm³ (理论 ≈6492 mm³)")

    soft_check_hex(plan)

    all_ok = ok == len(results)
    failed_ops = [r.get("operation") for r in results if not r.get("success")]
    exec_ok = all_ok

    ext_count = op_counts.get("feature_extrusion", 0)
    ext_ok = ext_count >= 2
    cut_ok = op_counts.get("feature_cut_extrusion", 0) >= 1
    polygon_ok = op_counts.get("sketch_polygon", 0) >= 1
    fillet_planned = op_counts.get("feature_fillet", 0) >= 1

    height_ok = bb is not None and abs(bb["sizeY"] - 56) < 2
    width_ok = False
    if bb is not None:
        width_ok = abs(max(bb["sizeX"], bb["sizeZ"]) - 18) < 2

    # 无头杆圆角时体积略低于含圆角理论值
    volume_ok = vol is not None and 5200 < vol < 7200

    passed = (
        exec_ok and ext_ok and cut_ok and polygon_ok and fillet_planned
        and height_ok and width_ok and volume_ok
    )
    print(f"\n判定: 全步骤成功={exec_ok}, 拉伸≥2={ext_ok}(步数={ext_count}), "
          f"盲切≥1={cut_ok}, 多边形≥1={polygon_ok}, 计划含圆角={fillet_planned}")
    print(f"      总高≈56={height_ok}, 最大宽≈18={width_ok}, 体积∈[5.2k,7.2k]={volume_ok}")
    if failed_ops:
        print(f"      失败步骤: {failed_ops}")
    print(f"{'🎉 LLM 自动建内六角螺栓通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
