"""
旋转特征 LLM Prompt 测试。
通过 LLM 自然语言指令生成旋转建模计划，然后执行。

测试前请确保：
  1. SolidWorks 已启动
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）
  3. 环境变量 OPENAI_API_KEY 已设置
  4. （可选）OPENAI_BASE_URL 如果使用第三方 API

测试内容：
  1. LLM Prompt → 用旋转法创建圆柱体
  2. LLM Prompt → 用旋转法创建球体

用法：
  python test_revolve_llm.py            # 运行全部 LLM 测试
  python test_revolve_llm.py cylinder   # 仅测试圆柱体 prompt
  python test_revolve_llm.py sphere     # 仅测试球体 prompt
"""

import os
import sys
import time

# 将 LLMPlanner 加入模块搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner.schemas import SwModelingPlan, OperationType
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor


def check_prerequisites():
    """检查 API 服务和 LLM 配置。"""
    print("=" * 60)
    print("前置条件检查")
    print("=" * 60)

    # 检查 LLM API Key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("  ❌ 未设置 OPENAI_API_KEY 环境变量")
        return False
    print(f"  ✅ OPENAI_API_KEY 已设置 ({api_key[:8]}...)")

    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        print(f"  ✅ OPENAI_BASE_URL: {base_url}")

    # 检查 SolidWorks API 服务
    executor = SwExecutor(verbose=False)
    if not executor.ping():
        print("  ❌ SolidWorks API 服务不可用")
        executor.close()
        return False
    print("  ✅ SolidWorks API 服务正常")
    executor.close()

    print()
    return True


def validate_plan_has_operations(plan: SwModelingPlan, required_ops: list[str]) -> list[str]:
    """
    验证计划中是否包含必须的操作类型。
    返回缺失的操作列表。
    """
    plan_ops = {step.operation.value for step in plan.steps}
    missing = [op for op in required_ops if op not in plan_ops]
    return missing


def test_cylinder_prompt():
    """
    LLM Prompt 测试：用旋转法创建圆柱体。
    
    用户 Prompt: "用旋转法创建一个直径 40mm、高度 80mm 的圆柱体"
    预期 LLM 计划: 包含 new_part → select_plane → sketch_centerline → 画轮廓 → feature_revolve
    预期结果: ⌀40mm × 80mm 的圆柱体
    验证: 包围盒 sizeX≈0.04, sizeY≈0.08, sizeZ≈0.04
    """
    print("=" * 60)
    print("LLM 测试 1：用旋转法创建圆柱体")
    print("=" * 60)

    prompt = "用旋转法创建一个直径 40mm、高度 80mm 的圆柱体"
    print(f"  用户 Prompt: \"{prompt}\"\n")

    # 1. LLM 生成计划
    client = SwLLMClient(use_cache=True)
    try:
        plan = client.plan(prompt)
    except Exception as e:
        print(f"  ❌ LLM 调用失败: {e}")
        client.close()
        return False
    finally:
        client.close()

    # 2. 打印计划内容
    print(f"\n  LLM 生成计划:")
    print(f"  用户意图: {plan.user_intent}")
    print(f"  步骤数: {len(plan.steps)}")
    for i, step in enumerate(plan.steps, 1):
        print(f"    步骤 {i}: {step.operation.value} {step.params} — {step.description}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    # 3. 验证计划结构
    required_ops = ["new_part", "select_plane", "sketch_centerline", "feature_revolve"]
    missing = validate_plan_has_operations(plan, required_ops)
    if missing:
        print(f"  ⚠️ 计划缺少预期操作: {missing}")
        print("  （继续执行，看看实际效果）")
    else:
        print("  ✅ 计划包含所有预期操作")

    # 4. 执行计划
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    # 5. 检查执行结果
    all_success = all(r.get("success", False) for r in results)
    if not all_success:
        print("  ❌ 部分步骤执行失败")
        return False

    # 6. 验证包围盒
    import requests
    try:
        bbox = requests.get("http://localhost:5100/api/body-bounding-box", timeout=10).json()
        if "boundingBox" in bbox:
            b = bbox["boundingBox"]
            sx = b.get("sizeX", 0) * 1000
            sy = b.get("sizeY", 0) * 1000
            sz = b.get("sizeZ", 0) * 1000
            print(f"\n  验证包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
            # 宽松验证：直径方向约 40mm，高度约 80mm
            if abs(sx - 40) < 10 and abs(sy - 80) < 10 and abs(sz - 40) < 10:
                print("  ✅ 包围盒尺寸大致符合预期（⌀40mm × 80mm 圆柱体）")
            else:
                print("  ⚠️ 包围盒尺寸与预期偏差较大，请在 SolidWorks 中确认")
    except Exception as e:
        print(f"  ⚠️ 无法验证包围盒: {e}")

    print()
    return all_success


def test_sphere_prompt():
    """
    LLM Prompt 测试：用旋转法创建球体。
    
    用户 Prompt: "用旋转法创建一个直径 50mm 的球体"
    预期 LLM 计划: 包含 new_part → select_plane → sketch_centerline → sketch_arc → feature_revolve
    预期结果: ⌀50mm 球体
    验证: 包围盒 sizeX≈0.05, sizeY≈0.05, sizeZ≈0.05
    """
    print("=" * 60)
    print("LLM 测试 2：用旋转法创建球体")
    print("=" * 60)

    prompt = "用旋转法创建一个直径 50mm 的球体"
    print(f"  用户 Prompt: \"{prompt}\"\n")

    # 1. LLM 生成计划
    client = SwLLMClient(use_cache=True)
    try:
        plan = client.plan(prompt)
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
        print(f"    步骤 {i}: {step.operation.value} {step.params} — {step.description}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    # 3. 验证计划结构
    required_ops = ["new_part", "select_plane", "sketch_centerline", "feature_revolve"]
    missing = validate_plan_has_operations(plan, required_ops)
    if missing:
        print(f"  ⚠️ 计划缺少预期操作: {missing}")
    else:
        print("  ✅ 计划包含所有预期操作")

    # 检查是否使用了圆弧（球体通常需要）
    has_arc = any(s.operation.value == "sketch_arc" for s in plan.steps)
    if has_arc:
        print("  ✅ 计划使用了 sketch_arc（适合球体轮廓）")
    else:
        print("  ℹ️ 计划未使用 sketch_arc（可能用其他方式画轮廓）")

    # 4. 执行计划
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    # 5. 检查执行结果
    all_success = all(r.get("success", False) for r in results)
    if not all_success:
        print("  ❌ 部分步骤执行失败")
        return False

    # 6. 验证包围盒
    import requests
    try:
        bbox = requests.get("http://localhost:5100/api/body-bounding-box", timeout=10).json()
        if "boundingBox" in bbox:
            b = bbox["boundingBox"]
            sx = b.get("sizeX", 0) * 1000
            sy = b.get("sizeY", 0) * 1000
            sz = b.get("sizeZ", 0) * 1000
            print(f"\n  验证包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
            # 球体：三个方向约 50mm
            if abs(sx - 50) < 10 and abs(sy - 50) < 10 and abs(sz - 50) < 10:
                print("  ✅ 包围盒尺寸大致符合预期（⌀50mm 球体）")
            else:
                print("  ⚠️ 包围盒尺寸与预期偏差较大，请在 SolidWorks 中确认")
    except Exception as e:
        print(f"  ⚠️ 无法验证包围盒: {e}")

    print()
    return all_success


if __name__ == "__main__":
    if not check_prerequisites():
        sys.exit(1)

    # 解析命令行参数
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    results = {}

    if arg in ("all", "cylinder"):
        results["LLM: 旋转圆柱体"] = test_cylinder_prompt()
        if arg == "all":
            time.sleep(1)

    if arg in ("all", "sphere"):
        results["LLM: 旋转球体"] = test_sphere_prompt()

    # 汇总
    print("=" * 60)
    print("LLM 测试结果汇总")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
    print()

    all_passed = all(results.values())
    if all_passed:
        print("🎉 所有 LLM 测试通过！")
    else:
        print("⚠️ 部分 LLM 测试失败")
    sys.exit(0 if all_passed else 1)
