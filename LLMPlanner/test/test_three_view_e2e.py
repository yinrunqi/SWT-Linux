"""
test_three_view_e2e.py — 三视图多模态建模端到端验证

流程：
  1. 读一张 B01 L 型支架三视图 PNG（含尺寸标注）
  2. base64 编码传给 SwLLMClient.plan(image_base64=...)
  3. 两阶段流水线：Vision → ThreeViewIR → SwModelingPlan
  4. Executor 执行 plan 到 SolidWorks
  5. 验证包围盒 ≈ 60 × 40 × 20mm、体积 ≈ 36000 mm³

用法：
  set OPENAI_API_KEY=...
  set OPENAI_BASE_URL=...
  cd LLMPlanner
  venv\\Scripts\\python.exe -X utf8 test\\test_three_view_e2e.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

# 保证从 LLMPlanner 目录运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from planner.llm_client import SwLLMClient  # noqa: E402
from planner.executor import SwExecutor  # noqa: E402


SW_URL = "http://localhost:5100"
DRAWING = ROOT / "datasets" / "manual_three_view" / "B01_l_bracket" / "drawing.png"
GT_PATH = ROOT / "datasets" / "manual_three_view" / "B01_l_bracket" / "groundtruth.json"


def check_sw() -> bool:
    try:
        r = requests.get(f"{SW_URL}/api/ping", timeout=3).json()
        return bool(r.get("success"))
    except Exception:
        return False


def bbox_mm() -> dict | None:
    try:
        r = requests.get(f"{SW_URL}/api/body-bounding-box", timeout=60).json()
        if r.get("success"):
            b = r["boundingBox"]
            return {
                "sizeX": b["sizeX"] * 1000,
                "sizeY": b["sizeY"] * 1000,
                "sizeZ": b["sizeZ"] * 1000,
            }
    except Exception:
        pass
    return None


def volume_mm3() -> float | None:
    try:
        r = requests.get(f"{SW_URL}/api/body-volume", timeout=60).json()
        return r.get("volumeMm3") if r.get("success") else None
    except Exception:
        return None


def main() -> bool:
    print("=" * 60)
    print("多模态三视图建模端到端验证 — B01 L 型支架")
    print("=" * 60)

    if not DRAWING.exists():
        print(f"[SKIP] 缺少三视图 PNG: {DRAWING}")
        return False
    if not GT_PATH.exists():
        print(f"[SKIP] 缺少 groundtruth: {GT_PATH}")
        return False
    if not check_sw():
        print("[SKIP] SolidWorks C# 服务未运行 (http://localhost:5100)")
        return False

    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    exp = gt["expected_bbox_mm"]
    exp_vol = gt["expected_volume_mm3"]
    print(f"预期包围盒: {exp}  预期体积: {exp_vol} mm³\n")

    # 1) 编码图片
    img_b64 = base64.b64encode(DRAWING.read_bytes()).decode("ascii")
    print(f"图片 base64 长度: {len(img_b64)} 字节")

    # 2) LLM 规划（两阶段）
    text_hint = (
        "这是一张 L 型支架的三视图。请根据图纸精确建模，注意主视图轮廓、"
        "俯视图/侧视图给出的深度，以及所有标注尺寸。"
    )
    client = SwLLMClient(use_cache=True, skill_mode="routed")
    try:
        plan = client.plan(text_hint, image_base64=img_b64)
    finally:
        client.close()

    print(f"\nLLM 计划: intent={plan.user_intent}, {len(plan.steps)} 步")
    for i, s in enumerate(plan.steps, 1):
        print(f"  {i:2d}. {s.operation.value:<24s} {s.params if s.params else '{}'}")
    if plan.notes:
        print(f"备注: {plan.notes}")

    # 3) 执行
    executor = SwExecutor(base_url=SW_URL, verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    ok = sum(1 for r in results if r.get("success"))
    print(f"\n执行结果: {ok}/{len(results)} 步成功")

    # 4) 几何验证
    bb = bbox_mm()
    vol = volume_mm3()
    print(f"\n实际包围盒: {bb} mm")
    print(f"实际体积: {vol} mm³")

    # 判定：三个轴任意排列应包含 60/40/20（因视图↔基准面映射有几种合理解读）
    if bb is None or vol is None:
        return False
    sizes = sorted([bb["sizeX"], bb["sizeY"], bb["sizeZ"]])
    expected_sizes = sorted([exp["length"], exp["height"], exp["depth"]])
    dims_ok = all(abs(a - b) < 3 for a, b in zip(sizes, expected_sizes))
    vol_ok = abs(vol - exp_vol) / exp_vol < 0.05  # ±5%
    all_ok = ok == len(results) and dims_ok and vol_ok
    print(
        f"\n判定: 步骤成功={ok == len(results)}, "
        f"包围盒尺寸±3mm 通过={dims_ok}, "
        f"体积±5% 通过={vol_ok}"
    )
    print("🎉 通过！" if all_ok else "⚠️ 未完全通过")
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
