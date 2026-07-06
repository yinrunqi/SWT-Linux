"""
test_wrench_llm.py — 日常物体（LLM 路径）：开口扳手/板手

考验能力：
  - **一个零件由两次拉伸合并而成**（圆盘头部 + 矩形手柄，头部与手柄末端重叠合并）
    这要求 LLM 理解: 一次 sketch+extrusion 只能画一个连续闭合轮廓，圆和矩形不能画在同一草图里；
    而要让"头部 + 手柄"成为一整块零件，需要两次拉伸，且矩形一端要嵌入圆盘内保证合并。
  - **sketch_polygon 用于「切除」**（而不是拉伸，扩展 polygon 的使用场景）：
    在扳手头部顶面画正六边形，切除贯穿形成螺母卡口。
  - 中心孔的坐标（顶面 Y 法向：cy=顶面Y、cx=cz=0）
  - midPlane 让扳手沿厚度方向居中

用法：
  set OPENAI_API_KEY=...
  set OPENAI_BASE_URL=...
  venv\\Scripts\\python.exe -X utf8 test\\objects\\test_wrench_llm.py

Prompt 已同步至 TEST.md 第九节。
"""

import sys

import requests

from _common import check_prerequisites, BASE_URL
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


PROMPT = (
    "请建模一把开口扳手（板手），由头部圆盘、手柄矩形梁、以及头部中心的六角螺母卡口组成：\n"
    "1. 扳手整体沿厚度方向（Y 轴）以原点居中，厚度 8mm；\n"
    "2. **头部**是一个圆盘：外径 40mm，圆心在原点。用上视基准面画圆，midPlane 拉伸 8mm；\n"
    "3. **手柄**是一个矩形梁：长 120mm（沿 +X 方向），宽 15mm（沿 Z 方向居中），厚度同样 8mm。"
    "手柄末端**必须嵌入头部圆盘内 5mm 以确保合并**成单一实体——"
    "即矩形沿 X 方向从 X=15mm（圆盘半径 20mm 减 5mm）延伸到 X=120mm，"
    "沿 Z 从 -7.5mm 到 +7.5mm。需要**用第二次 select_plane（上视面）+ sketch_rectangle + feature_extrusion**"
    "（不能和头部画在同一个草图里，因为 SolidWorks 不允许一个草图里画圆和矩形两个相交但不嵌套的轮廓）；\n"
    "4. **六角卡口**：在头部顶面用 select_face_and_sketch 选顶面，用 sketch_polygon 画一个"
    "**正六边形**（对边距 13mm、尖朝上），然后 feature_cut_extrusion 贯穿切除，形成螺母卡口。"
    "对边距 13mm 对应外接圆半径 = 13 / √3 ≈ 7.51mm；\n"
    "5. 六边形中心应对齐头部圆盘中心：cx=0、cz=0（面内两个方向对齐），"
    "cy=顶面 Y 坐标 (+0.004m)。\n"
    "所有尺寸使用米制（1mm = 0.001m）。"
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
            "minX": b["minX"] * 1000,
            "maxX": b["maxX"] * 1000,
        }
    except Exception:
        return None


def main() -> bool:
    if not check_prerequisites():
        return False

    print("=" * 60)
    print("LLM 自动建模：开口扳手（头部圆盘 + 手柄 + 六角卡口）")
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
    #    完整扳手包围盒：
    #      X: 头部半径 20mm 向 -X 延伸 20mm，手柄向 +X 延伸到 120mm → X∈[-20, 120]，sizeX=140mm
    #      Y: midPlane 拉伸 8mm，sizeY=8mm
    #      Z: 头部直径 40mm 决定 → sizeZ=40mm
    #    体积理论 = 圆盘 π·20²·8 + 手柄 105·15·8 − 头部重叠部分 (约5·15·8) − 六边形孔 (3√3/2)·7.51²·8
    #             ≈ 10053 + 12600 − 600 − 1173 ≈ 20880 mm³
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n包围盒: {bb} mm")
    print(f"体积: {vol:.1f} mm³ (理论 ≈20880 mm³)")

    # 计划特征
    ext_count = op_counts.get("feature_extrusion", 0)
    polygon_count = op_counts.get("sketch_polygon", 0)
    cut_count = op_counts.get("feature_cut_extrusion", 0)

    # 判定条件
    sizeY_ok = bb is not None and abs(bb["sizeY"] - 8) < 1
    sizeX_ok = bb is not None and abs(bb["sizeX"] - 140) < 8   # 容忍手柄长度 ±5mm
    sizeZ_ok = bb is not None and abs(bb["sizeZ"] - 40) < 3   # 头部直径 ±3mm
    volume_ok = vol is not None and 17000 < vol < 24000
    # 头部+手柄必须真正合并成一整块：minX 应 ≈ -20mm（头部左沿）、maxX 应 ≈ 120mm（手柄右端）
    merged_ok = bb is not None and bb["minX"] < -15 and bb["maxX"] > 100

    passed = (ok == len(results)) and (ext_count >= 2) and (polygon_count >= 1) \
             and (cut_count >= 1) and sizeY_ok and sizeX_ok and sizeZ_ok \
             and volume_ok and merged_ok

    print(f"\n判定: 全步骤成功={ok == len(results)}, 拉伸步数={ext_count}(≥2), "
          f"多边形步数={polygon_count}(≥1), 切除步数={cut_count}(≥1)")
    print(f"      sizeY≈8={sizeY_ok}, sizeX≈140={sizeX_ok}, sizeZ≈40={sizeZ_ok}, "
          f"合并OK(minX<-15且maxX>100)={merged_ok}, 体积∈[17k,24k]={volume_ok}")
    print(f"{'🎉 LLM 自动建扳手通过！' if passed else '⚠️ 未完全通过'}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
