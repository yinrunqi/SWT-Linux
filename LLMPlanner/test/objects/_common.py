"""
_common.py — 日常物体 LLM 建模测试的共享工具

把前置检查、LLM 规划+执行、几何验证等重复逻辑集中在此，
每个具体物体的测试脚本只需描述 prompt 与预期，调用 run_object_test 即可。

测试前请确保：
  1. SolidWorks 已启动（普通权限）
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）
  3. 已配置 LLMPlanner/.env（可复制 .env.example），或先运行 setup_test_env.bat
"""

import os
import sys

import requests

# 将 LLMPlanner 根目录加入模块搜索路径（test/objects/ 往上两级）
_LLM_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _LLM_ROOT)

from load_env import ensure_test_env_loaded
from planner.schemas import SwModelingPlan
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor

ensure_test_env_loaded()
BASE_URL = os.getenv("SW_API_URL", "http://localhost:5100").rstrip("/")


def format_volume_mm3(vol: float | None) -> str:
    return f"{vol:.1f}" if vol is not None else "N/A"


def check_prerequisites() -> bool:
    """检查 LLM API Key 与 SolidWorks 服务可用性。"""
    print("=" * 60)
    print("前置条件检查")
    print("=" * 60)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  ❌ 未设置 OPENAI_API_KEY 环境变量")
        return False
    print(f"  ✅ OPENAI_API_KEY 已设置 ({api_key[:8]}...)")

    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        print(f"  ✅ OPENAI_BASE_URL: {base_url}")

    executor = SwExecutor(verbose=False)
    if not executor.ping():
        print("  ❌ SolidWorks API 服务不可用")
        executor.close()
        return False
    print("  ✅ SolidWorks API 服务正常")
    executor.close()
    print()
    return True


def get_bounding_box() -> dict | None:
    """获取当前实体包围盒（尺寸单位转换为 mm）。失败返回 None。"""
    try:
        resp = requests.get(f"{BASE_URL}/api/body-bounding-box", timeout=60)
        bbox = resp.json()
        if not bbox.get("success"):
            print(f"  ⚠️ 获取包围盒失败: {bbox.get('message')}")
            return None
        b = bbox["boundingBox"]
        return {
            "sizeX": b.get("sizeX", 0) * 1000,
            "sizeY": b.get("sizeY", 0) * 1000,
            "sizeZ": b.get("sizeZ", 0) * 1000,
        }
    except Exception as e:
        print(f"  ⚠️ 无法获取包围盒: {e}")
        return None


def verify_bounding_box(expected: dict, tolerance_mm: float = 15.0) -> bool:
    """
    验证包围盒尺寸是否在容差内。
    expected: {"sizeX": mm, "sizeY": mm, "sizeZ": mm}
    """
    actual = get_bounding_box()
    if actual is None:
        return False

    print(f"\n  实际包围盒尺寸: "
          f"{actual['sizeX']:.1f} × {actual['sizeY']:.1f} × {actual['sizeZ']:.1f} mm")
    print(f"  期望包围盒尺寸: "
          f"{expected['sizeX']:.1f} × {expected['sizeY']:.1f} × {expected['sizeZ']:.1f} mm "
          f"(容差 ±{tolerance_mm:.0f}mm)")

    ok = True
    for axis in ("sizeX", "sizeY", "sizeZ"):
        if abs(actual[axis] - expected[axis]) > tolerance_mm:
            print(f"  ❌ {axis} 偏差过大: 实际 {actual[axis]:.1f} vs 期望 {expected[axis]:.1f}")
            ok = False
    if ok:
        print("  ✅ 包围盒尺寸在可接受范围内")
    return ok


def verify_feature_count(keyword: str, expected_min: int) -> bool:
    """验证特征树中含某关键字的特征数量 >= expected_min。"""
    try:
        resp = requests.get(f"{BASE_URL}/api/feature-tree", timeout=60)
        tree = resp.json()
        if not tree.get("success"):
            print(f"  ⚠️ 获取特征树失败: {tree.get('message')}")
            return False
        features = tree.get("features", [])
        matched = [f for f in features
                   if keyword.lower() in f.get("type", "").lower()
                   or keyword.lower() in f.get("name", "").lower()]
        print(f"  特征计数: {keyword} = {len(matched)} (期望 ≥{expected_min})")
        ok = len(matched) >= expected_min
        print(f"  {'✅' if ok else '⚠️'} {keyword} 特征数量"
              f"{'满足' if ok else '不足（LLM 可能用了不同策略）'}")
        return ok
    except Exception as e:
        print(f"  ⚠️ 无法验证特征树: {e}")
        return False


def run_object_test(
    title: str,
    prompt: str,
    required_ops: list[str],
    expected_bbox: dict,
    bbox_tolerance_mm: float = 20.0,
    feature_checks: list[tuple[str, int]] | None = None,
) -> bool:
    """
    通用物体建模测试流程：LLM 规划 → 验证计划结构 → 执行 → 几何验证。

    title:           测试标题
    prompt:          自然语言建模指令
    required_ops:    计划中必须包含的操作类型（如 ["new_part", "feature_extrusion"]）
    expected_bbox:   预期包围盒 {"sizeX","sizeY","sizeZ"}（mm）
    bbox_tolerance_mm: 包围盒容差（mm）
    feature_checks:  可选的特征计数检查列表，如 [("Extrusion", 1), ("Cut", 1)]

    返回：是否通过（以包围盒验证为准）。
    """
    print("=" * 60)
    print(f"物体建模测试：{title}")
    print("=" * 60)
    print(f"  用户 Prompt: \"{prompt}\"\n")

    # 1. LLM 生成计划
    client = SwLLMClient(use_cache=True)
    try:
        plan: SwModelingPlan = client.plan(prompt)
    except Exception as e:
        print(f"  ❌ LLM 调用失败: {e}")
        client.close()
        return False
    finally:
        client.close()

    # 2. 打印计划
    print(f"\n  LLM 生成计划:")
    print(f"  用户意图: {plan.user_intent}")
    print(f"  步骤数: {len(plan.steps)}")
    for i, step in enumerate(plan.steps, 1):
        params_str = str(step.params) if step.params else "{}"
        print(f"    步骤 {i:2d}: {step.operation.value:<25s} {params_str}")
        if step.description:
            print(f"            → {step.description}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    # 3. 验证计划结构
    plan_ops = {s.operation.value for s in plan.steps}
    missing = [op for op in required_ops if op not in plan_ops]
    if missing:
        print(f"  ❌ 计划缺少关键操作: {missing}")
        return False
    print("  ✅ 计划包含必要操作")

    # 4. 执行计划
    print("\n--- 执行建模计划 ---")
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    success_count = sum(1 for r in results if r.get("success", False))
    print(f"\n  执行结果: {success_count}/{len(results)} 步成功")
    if not all(r.get("success", False) for r in results):
        for r in results:
            if not r.get("success", False):
                print(f"  ❌ 失败步骤: {r.get('operation', '?')} — {r.get('message', '?')}")
        return False

    # 5. 几何验证
    print("\n--- 几何验证 ---")
    bbox_ok = verify_bounding_box(expected_bbox, tolerance_mm=bbox_tolerance_mm)
    if feature_checks:
        for keyword, expected_min in feature_checks:
            verify_feature_count(keyword, expected_min)

    if bbox_ok:
        print(f"\n  🎉 测试通过！{title} 建模成功！")
    else:
        print(f"\n  ⚠️ 建模执行成功但几何验证未完全通过，请在 SolidWorks 中确认结果")
    return bbox_ok
