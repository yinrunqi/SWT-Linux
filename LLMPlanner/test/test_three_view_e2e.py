"""
test_three_view_e2e.py — 三视图多模态建模端到端验证（可参数化任意样例）

流程（对每个样例都做一遍）：
  1. 读该样例的 drawing.png（含尺寸标注）+ groundtruth.json
  2. base64 编码传给 SwLLMClient.plan(image_base64=...)
  3. 两阶段流水线：Vision → ThreeViewIR → SwModelingPlan
  4. Executor 执行 plan 到 SolidWorks
  5. 验证包围盒与体积吻合 groundtruth（±3mm / ±5%）

用法：
  # 默认跑 B01
  venv\\Scripts\\python.exe -X utf8 test\\test_three_view_e2e.py

  # 指定样例（可多个）
  venv\\Scripts\\python.exe -X utf8 test\\test_three_view_e2e.py B01 B02 B03
"""
from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# 保证从 LLMPlanner 目录运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from planner.llm_client import SwLLMClient  # noqa: E402
from planner.executor import SwExecutor  # noqa: E402


SW_URL = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")
DATASET_DIR = ROOT / "datasets" / "manual_three_view"
SAMPLES_JSON = DATASET_DIR / "samples.json"


# ============================================================
# 样例注册 —— 从 samples.json 加载（与 build_all.py / web app.js 共用同一数据源）
# ============================================================


@dataclass
class Sample:
    part_id: str
    subdir: str
    text_hint: str  # 阶段 A（Vision）用的文字提示


def _load_samples() -> dict[str, Sample]:
    data = json.loads(SAMPLES_JSON.read_text(encoding="utf-8"))
    out: dict[str, Sample] = {}
    for cfg in data["samples"]:
        text_hint = cfg.get("test", {}).get("text_hint") or cfg.get("ui", {}).get("prompt", "")
        out[cfg["part_id"]] = Sample(
            part_id=cfg["part_id"],
            subdir=cfg["subdir"],
            text_hint=text_hint,
        )
    return out


SAMPLES: dict[str, Sample] = _load_samples()


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


def run_one(sample: Sample, client: SwLLMClient) -> bool:
    print("=" * 72)
    print(f"多模态三视图建模 · {sample.part_id} —— {sample.subdir}")
    print("=" * 72)

    drawing = DATASET_DIR / sample.subdir / "drawing.png"
    gt_path = DATASET_DIR / sample.subdir / "groundtruth.json"
    if not drawing.exists():
        print(f"[SKIP] 缺少 drawing.png: {drawing}")
        return False
    if not gt_path.exists():
        print(f"[SKIP] 缺少 groundtruth.json: {gt_path}")
        return False

    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    exp = gt["expected_bbox_mm"]
    exp_vol = gt["expected_volume_mm3"]
    print(f"[GT] bbox={exp} mm  volume={exp_vol} mm³\n")

    # 1) 编码图片
    img_b64 = base64.b64encode(drawing.read_bytes()).decode("ascii")
    print(f"图片 base64 长度: {len(img_b64)} 字节")

    # 2) LLM 规划（两阶段）
    plan = client.plan(sample.text_hint, image_base64=img_b64)
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

    if bb is None or vol is None:
        print("[FAIL] 无法读取包围盒或体积")
        return False

    sizes = sorted([bb["sizeX"], bb["sizeY"], bb["sizeZ"]])
    expected_sizes = sorted([exp["length"], exp["height"], exp["depth"]])
    dims_ok = all(abs(a - b) < 3 for a, b in zip(sizes, expected_sizes))
    vol_ok = abs(vol - exp_vol) / exp_vol < 0.05
    all_ok = ok == len(results) and dims_ok and vol_ok
    print(
        f"\n判定: 步骤成功={ok == len(results)}, "
        f"包围盒±3mm={dims_ok}, 体积±5%={vol_ok}"
    )
    print("🎉 通过！\n" if all_ok else "⚠️ 未完全通过\n")
    return all_ok


def main(argv: list[str]) -> bool:
    if not check_sw():
        print("[SKIP] SolidWorks C# 服务未运行 (http://localhost:5100)")
        return False

    # 默认跑 B01；也可命令行指定
    ids = argv[1:] if len(argv) > 1 else ["B01"]
    samples = []
    for sid in ids:
        if sid not in SAMPLES:
            print(f"[WARN] 未知样例 {sid}，可选：{list(SAMPLES)}")
            continue
        samples.append(SAMPLES[sid])
    if not samples:
        return False

    client = SwLLMClient(use_cache=True, skill_mode="routed")
    try:
        results = [run_one(s, client) for s in samples]
    finally:
        client.close()

    n_pass = sum(results)
    print("=" * 72)
    print(f"总结: {n_pass}/{len(results)} 样例通过")
    print("=" * 72)
    return n_pass == len(results)


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv) else 1)
