"""
test_stepped_shaft_llm.py — 复杂物件（LLM 路径）：三段阶梯轴（旋转凸台）

考验能力：
  - 前视面半剖轮廓（X=半径，Y=轴），7 段直线 + 中心线
  - 一次 feature_revolve 360°，禁止三次圆柱拉伸拼接

用法：
  set OPENAI_API_KEY=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_stepped_shaft_llm.py

Prompt 已同步至 object_test_yrq.md。
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL, format_volume_mm3
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请用旋转法创建一根三段阶梯轴，**严格按以下顺序**执行。\n"
    "做法与「在前视面画矩形半截面 → 绕 Y 轴旋转 360° 得到圆柱」相同，只是半截面换成三级台阶。\n"
    "【坐标约定】在前视基准面上：Y 轴是轴线（竖直），X 轴是半径方向（水平）。"
    "半剖轮廓只画在 X≥0（中心线右侧），左边由旋转中心线自动封闭。\n"
    "【三段尺寸】（沿 Y 轴从下到上，总长 90mm，以原点居中）\n"
    "· 最下段：直径 35mm（半径 17.5mm），高度 40mm，占据 Y=-45mm ~ Y=-5mm\n"
    "· 中间段：直径 50mm（半径 25mm），高度 30mm，占据 Y=-5mm ~ Y=+25mm\n"
    "· 最上段：直径 25mm（半径 12.5mm），高度 20mm，占据 Y=+25mm ~ Y=+45mm\n"
    "1. 先画旋转中心线（构造线）：沿 Y 轴从 (X=0, Y=-45mm) 到 (X=0, Y=+45mm)。\n"
    "2. 再在中心线右侧画 7 条轮廓直线（单位 mm，格式为「半径 X, 轴线 Y」），按顺序首尾相连：\n"
    "   ① 底边：(0, -45) → (17.5, -45)\n"
    "   ② 下段竖边：(17.5, -45) → (17.5, -5)\n"
    "   ③ 下→中台阶：(17.5, -5) → (25, -5)\n"
    "   ④ 中段竖边：(25, -5) → (25, +25)\n"
    "   ⑤ 中→上台阶：(25, +25) → (12.5, +25)\n"
    "   ⑥ 上段竖边：(12.5, +25) → (12.5, +45)\n"
    "   ⑦ 顶边：(12.5, +45) → (0, +45)\n"
    "   完成后左边已贴中心线，**不要再画**沿中心线的额外竖线；**不要用圆**画轮廓。\n"
    "3. 最后旋转 360° 生成实心阶梯轴（**不要用三次圆柱拉伸拼接**）。\n"
    "常见错误（会导致旋转失败）：\n"
    "· 轮廓画到 X<0（中心线左侧）—— 半剖必须全在 X≥0；\n"
    "· 在台阶中间沿轴线画竖线（如 (0,-15)→(0,+15)）—— 会把轮廓切成无效区域；\n"
    "· 台阶处从外径斜连回中心（如 (-17.5,-15)→(0,-15)）—— 轮廓自交；\n"
    "· 中心线只画 Y=0~90mm 而非 Y=-45~+45mm —— 轴未居中；\n"
    "· 用画圆或三次拉伸代替旋转 —— 本题必须一次旋转完成。\n"
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
        return {
            "sizeX": b["sizeX"] * 1000,
            "sizeY": b["sizeY"] * 1000,
            "sizeZ": b["sizeZ"] * 1000,
        }
    except Exception:
        return None


def soft_check_profile(plan: SwModelingPlan) -> None:
    """轮廓与中心线软检。"""
    lines = [s for s in plan.steps if s.operation.value == "sketch_line"]
    for i, s in enumerate(lines, 1):
        xs = [float(s.params.get(k, 0)) for k in ("x1", "x2")]
        neg = [x for x in xs if x < -0.0001]
        if neg:
            print(f"  [软检] 轮廓线{i} 出现 X<0: {xs}")

    centerlines = [s for s in plan.steps if s.operation.value == "sketch_centerline"]
    for s in centerlines:
        y1 = float(s.params.get("y1", 0)) * 1000
        y2 = float(s.params.get("y2", 0)) * 1000
        print(f"  [软检] 中心线 Y 范围: {y1:.1f} ~ {y2:.1f} mm (期望 -45 ~ +45)")


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：三段阶梯轴")
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
    print(f"体积: {format_volume_mm3(vol)} mm³ (理论 ≈107207 mm³)")

    soft_check_profile(plan)

    all_ok = ok == len(results)
    revolve_steps = [s for s in plan.steps if s.operation.value == "feature_revolve"]
    revolve_ok = len(revolve_steps) >= 1 and all(
        not s.params.get("isCut", False) for s in revolve_steps
    )
    centerline_ok = op_counts.get("sketch_centerline", 0) >= 1
    line_ok = op_counts.get("sketch_line", 0) >= 7
    no_extrusion = op_counts.get("feature_extrusion", 0) == 0

    max_diameter_ok = False
    length_ok = False
    if bb is not None:
        radial = sorted([bb["sizeX"], bb["sizeZ"]], reverse=True)
        max_diameter_ok = abs(radial[0] - 50) < 3
        length_ok = abs(bb["sizeY"] - 90) < 3

    # 实测旋转体体积可略低于理论（圆角/数值误差），放宽下限
    volume_ok = vol is not None and 102000 < vol < 111500

    passed = (
        all_ok and revolve_ok and centerline_ok and line_ok
        and max_diameter_ok and length_ok and volume_ok and no_extrusion
    )
    print(f"\n判定: 全步骤成功={all_ok}, 旋转凸台={revolve_ok}, 中心线≥1={centerline_ok}, "
          f"轮廓线≥7={line_ok}, 无拉伸={no_extrusion}, 最大径≈50={max_diameter_ok}, "
          f"总长≈90={length_ok}, 体积∈[102k,111.5k]={volume_ok}")
    print(f"{'🎉 LLM 自动建阶梯轴通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
