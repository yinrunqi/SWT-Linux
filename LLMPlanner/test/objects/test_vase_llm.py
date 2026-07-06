"""
test_vase_llm.py — 日常物体（LLM 路径）：花瓶（旋转生成的曲线瓶身 + 内腔）

考验能力：
  - **feature_revolve 用多段直线组成复杂轴截面**：8 段直线首尾相连构成"花瓶"轮廓（底部→收腰→膨大→颈部→瓶口）。
    考验 LLM 能否按精确坐标依次画 8 条 sketch_line 组成一个封闭的半截面。
  - **中心线（旋转轴）+ 轴截面轮廓** 的正确组合：轮廓端点必须落在旋转轴上，才能被 SolidWorks 用旋转轴自动封闭。
  - **旋转后再挖内腔**：用射线选顶面 + 画圆 + 盲孔切除，让花瓶变成空心。
  - 保持先加料后挖除的顺序（内腔在最后）。

用法：
  set OPENAI_API_KEY=...
  set OPENAI_BASE_URL=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_vase_llm.py

Prompt 已同步至 TEST.md 第九节。
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请建模一个花瓶：轴对称形状（用旋转特征生成），底部平坦、瓶身中部略膨大、瓶颈收窄、瓶口再略外扩。\n"
    "在**前视基准面**（select_plane {index:0}）上画一个位于旋转轴右侧的**半截面轮廓**并旋转 360°：\n"
    "1. 首先画中心线（旋转轴）：从 (x=0, y=0) 到 (x=0, y=0.15)，沿 Y 轴向上；\n"
    "2. 然后**按顺序依次画 8 段直线**首尾相连组成花瓶的右半轮廓（所有坐标单位：米）：\n"
    "   ① sketch_line (0, 0) → (0.03, 0)      ← 底面（旋转轴到底盘外沿）\n"
    "   ② sketch_line (0.03, 0) → (0.03, 0.01)  ← 底盘垂直段\n"
    "   ③ sketch_line (0.03, 0.01) → (0.045, 0.04)  ← 斜向外扩到最粗处\n"
    "   ④ sketch_line (0.045, 0.04) → (0.045, 0.07)  ← 中部最粗处垂直段\n"
    "   ⑤ sketch_line (0.045, 0.07) → (0.02, 0.1)   ← 收腰到瓶颈\n"
    "   ⑥ sketch_line (0.02, 0.1) → (0.0275, 0.13)  ← 瓶颈外扩形成瓶口\n"
    "   ⑦ sketch_line (0.0275, 0.13) → (0.0275, 0.15) ← 瓶口垂直段\n"
    "   ⑧ sketch_line (0.0275, 0.15) → (0, 0.15)   ← 顶面（回到旋转轴）\n"
    "3. 调用 feature_revolve {angle: 360}，形成实心花瓶（此时**不需要**画额外直线封闭轮廓，"
    "轮廓两端都在旋转轴上，SolidWorks 会用旋转轴自动封闭）；\n"
    "4. 花瓶应有内腔（空心）：**旋转完成后**用 select_face_and_sketch 选中顶面（射线起点 ry=0.2 大于瓶高 0.15，"
    "方向 dy=-1），在顶面上画一个直径 36mm 的圆（cx=0, cy=0.15, cz=0, radius=0.018），"
    "然后 feature_cut_extrusion 盲孔切除 140mm 深度（endCondition='blind'），"
    "留下 10mm 底厚（花瓶高 150mm，切 140mm 后剩底部 10mm）。\n"
    "所有尺寸均为米制。整个花瓶从底部 Y=0 延伸到顶部 Y=0.15。"
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
            "minY": b["minY"] * 1000,
            "maxY": b["maxY"] * 1000,
        }
    except Exception:
        return None


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：花瓶（旋转生成 + 内腔）")
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
    #    实心花瓶体积 ≈ 559 cm³；挖内腔 ⌀36×深 140mm 减去 142545 → 416504 mm³
    #    容差略宽松（LLM 可能选择不同的内腔尺寸）
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论实心≈559000, 挖内腔后≈416500)")

    revolve_count = op_counts.get("feature_revolve", 0)
    line_count = op_counts.get("sketch_line", 0)
    centerline_count = op_counts.get("sketch_centerline", 0)
    cut_count = op_counts.get("feature_cut_extrusion", 0)

    # 判定
    height_ok = bb is not None and abs(bb["sizeY"] - 150) < 5
    diam_ok = bb is not None and 85 < bb["sizeX"] < 95 and 85 < bb["sizeZ"] < 95
    # 挖了内腔的花瓶体积应显著小于实心花瓶
    hollow_ok = vol is not None and 300000 < vol < 500000
    line_ok = line_count >= 6

    passed = (ok == len(results)) and (revolve_count >= 1) and (cut_count >= 1) \
             and (centerline_count >= 1) and line_ok \
             and height_ok and diam_ok and hollow_ok

    print(f"\n判定: 全步骤成功={ok == len(results)}, revolve 步数={revolve_count}(≥1), "
          f"centerline={centerline_count}(≥1), line={line_count}(≥6), cut={cut_count}(≥1)")
    print(f"      高度≈150mm={height_ok}, 直径≈90mm={diam_ok}, "
          f"内腔挖空(300k<vol<500k)={hollow_ok}")
    print(f"{'🎉 LLM 自动建花瓶通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
