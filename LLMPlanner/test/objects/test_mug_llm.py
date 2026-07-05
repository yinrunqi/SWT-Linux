"""
test_mug_llm.py — 日常物体（LLM 路径）：马克杯（含内腔和圆环把手）

考验能力：
  - 多次进入草图（基准面 + 实体面 + 另一个基准面）
  - 圆柱杯体的一次拉伸
  - 实体面上切除内腔（顶面开口的"抽壳替代法"）
  - 前视基准面上画同心圆双轮廓（拉伸自动挖空 → 圆环形凸台把手）
  - 把手位置需与杯壁**相交**（内圆嵌入杯身，不是相切）才能合并成单一实体

用法：
  set OPENAI_API_KEY=...
  set OPENAI_BASE_URL=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_mug_llm.py

Prompt 已同步至 TEST.md 第九节。
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请建模一个马克杯，**严格按以下顺序**执行，因为顺序会影响最终形状是否干净：\n"
    "1. 先创建杯体：用上视基准面画外径 70mm 的圆，向上拉伸 90mm，形成实心圆柱；\n"
    "2. 接着**加**把手（**必须在挖空腔之前加**，否则把手内嵌部分会伸入未来的空腔里，"
    "在杯内壁形成难看的圆环凸起）：在前视基准面上画两个同心圆（外径 40mm、内径 24mm，"
    "即把手条粗 8mm），然后**用 feature_extrusion（加料操作，不是 feature_cut_extrusion！）**沿前后（Z）方向 midPlane 拉伸 8mm 厚度，"
    "把这个环形轮廓变成实心的把手（外圆做外形、内圆做中间的空洞）。\n"
    "   把手位置要求（**必须与杯壁相交合并**，不能只是相切）：\n"
    "   - 同心圆中心 X 应把内圆左沿嵌入杯壁至少 2mm。杯外壁在 X=35mm，"
    "取把手中心 X≈48mm，则内圆左沿到 X=48−12=36mm，勉强嵌入 1mm——不够；\n"
    "   - 正确做法：把手中心取 X=48mm 时最外沿 X=68mm。但为了确保内圆稳固地嵌入实体杯壁至少 5mm，"
    "建议把手中心 X 取 40mm（内圆左沿到 X=28mm，深入实心杯身 7mm），"
    "此时把手最外沿到 X=60mm；\n"
    "   - 把手垂直方向 Y 位置居中于杯身中部，即 Y=45mm；\n"
    "3. 最后再挖空腔：从杯体顶面向下挖，用射线选顶面，画一个直径 60mm 的圆（即壁厚 5mm），"
    "切除 80mm 深（即底厚 10mm），顶部敞开、底部封闭。此步会同时把嵌入杯身的把手内圆那段一起挖掉，"
    "使杯内壁保持光滑。\n"
    "4. 杯子沿 Y 方向从 0 延伸到 90mm（不居中）。\n"
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
            "maxX": b["maxX"] * 1000,
        }
    except Exception:
        return None


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：马克杯（含把手）")
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
    #    完整马克杯的包围盒（把手已合并）：
    #      X: 杯身 ±35mm，把手向外突出到约 X≈62mm（35 + 20 = 55 + 7 附近 → 保守用范围 [95, 110]）
    #      Y: 0 ~ 90mm 即 sizeY≈90
    #      Z: ±35mm 即 sizeZ≈70
    #    体积：外圆柱 π·35²·90 ≈ 346361，减去内腔 π·30²·80 ≈ 226195 ≈ 120166 杯身
    #         加上把手圆环（π·(20²−12²)·8）≈ 6434 → 合计约 126000
    #         （若不完全合并，可能减去几立方毫米，但差异<1000）
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论 ≈126000 mm³)")

    circle_count = op_counts.get("sketch_circle", 0)
    ext_count = op_counts.get("feature_extrusion", 0)
    cut_count = op_counts.get("feature_cut_extrusion", 0)

    # 判定条件
    sizeY_ok = bb is not None and abs(bb["sizeY"] - 90) < 3
    sizeZ_ok = bb is not None and abs(bb["sizeZ"] - 70) < 3
    # 把手成功合并：sizeX 应该 > 杯身直径 70（会突出到 ≈100mm）
    handle_merged = bb is not None and bb["sizeX"] > 90 and bb["maxX"] > 50
    volume_ok = vol is not None and 100000 < vol < 145000

    passed = (ok == len(results)) and (circle_count >= 3) and (ext_count >= 2) \
             and (cut_count >= 1) and sizeY_ok and sizeZ_ok and handle_merged and volume_ok

    print(f"\n判定: 全步骤成功={ok == len(results)}, 圆步数={circle_count}(≥3), "
          f"拉伸步数={ext_count}(≥2), 切除步数={cut_count}(≥1)")
    print(f"      sizeY≈90={sizeY_ok}, sizeZ≈70={sizeZ_ok}, "
          f"把手合并(sizeX>90)={handle_merged}, 体积∈[100k,145k]={volume_ok}")
    print(f"{'🎉 LLM 自动建马克杯通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
