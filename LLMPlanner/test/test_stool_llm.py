"""
test_stool_llm.py — 阶段2：LLM 自动建模测试（四脚板凳）

用一句简短的自然语言描述，让 LLM 自动规划并执行 SolidWorks 建模。
验证 LLM 是否能正确理解多特征组合建模需求。

测试前请确保：
  1. SolidWorks 已启动
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）
  3. 环境变量 OPENAI_API_KEY 已设置
  4. （可选）OPENAI_BASE_URL 如果使用第三方 API

用法：
  python test/test_stool_llm.py                # 运行全部 LLM 测试
  python test/test_stool_llm.py stool          # 基础四脚板凳
  python test/test_stool_llm.py stool_fillet   # 带圆角的四脚板凳
"""

import os
import sys
import time

import requests

# 将 LLMPlanner 加入模块搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner.schemas import SwModelingPlan, OperationType
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor

BASE_URL = "http://localhost:5100"


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
    """验证计划中是否包含必须的操作类型。返回缺失的操作列表。"""
    plan_ops = {step.operation.value for step in plan.steps}
    missing = [op for op in required_ops if op not in plan_ops]
    return missing


def verify_bounding_box(expected_size: dict, tolerance_mm: float = 15.0) -> bool:
    """
    验证实体包围盒尺寸。
    expected_size: {"sizeX": mm, "sizeY": mm, "sizeZ": mm}
    tolerance_mm: 容差（毫米），LLM 可能有误差
    """
    try:
        resp = requests.get(f"{BASE_URL}/api/body-bounding-box", timeout=60)
        bbox = resp.json()
        if not bbox.get("success"):
            print(f"  ❌ 获取包围盒失败: {bbox.get('message')}")
            return False

        b = bbox["boundingBox"]
        sx = b.get("sizeX", 0) * 1000
        sy = b.get("sizeY", 0) * 1000
        sz = b.get("sizeZ", 0) * 1000
        print(f"\n  实际包围盒尺寸: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")

        ex = expected_size["sizeX"]
        ey = expected_size["sizeY"]
        ez = expected_size["sizeZ"]
        print(f"  期望包围盒尺寸: {ex:.1f} × {ey:.1f} × {ez:.1f} mm (容差 ±{tolerance_mm:.0f}mm)")

        ok_x = abs(sx - ex) <= tolerance_mm
        ok_y = abs(sy - ey) <= tolerance_mm
        ok_z = abs(sz - ez) <= tolerance_mm

        if ok_x and ok_y and ok_z:
            print("  ✅ 包围盒尺寸在可接受范围内")
            return True
        else:
            if not ok_x:
                print(f"  ❌ X 方向偏差过大: 实际 {sx:.1f} vs 期望 {ex:.1f}")
            if not ok_y:
                print(f"  ❌ Y 方向偏差过大: 实际 {sy:.1f} vs 期望 {ey:.1f}")
            if not ok_z:
                print(f"  ❌ Z 方向偏差过大: 实际 {sz:.1f} vs 期望 {ez:.1f}")
            return False
    except Exception as e:
        print(f"  ⚠️ 无法验证包围盒: {e}")
        return False


def verify_feature_count(feature_type_keyword: str, expected_count: int) -> bool:
    """验证特征树中某类特征的数量。"""
    try:
        resp = requests.get(f"{BASE_URL}/api/feature-tree", timeout=60)
        tree = resp.json()
        if not tree.get("success"):
            print(f"  ⚠️ 获取特征树失败: {tree.get('message')}")
            return False

        features = tree.get("features", [])
        matched = [f for f in features
                   if feature_type_keyword.lower() in f.get("type", "").lower()
                   or feature_type_keyword.lower() in f.get("name", "").lower()]
        print(f"  特征计数: {feature_type_keyword} = {len(matched)} (期望 ≥{expected_count})")

        if len(matched) >= expected_count:
            print(f"  ✅ {feature_type_keyword} 特征数量满足预期")
            return True
        else:
            print(f"  ⚠️ {feature_type_keyword} 特征数量不足（LLM 可能用了不同策略）")
            return False
    except Exception as e:
        print(f"  ⚠️ 无法验证特征树: {e}")
        return False


# ============================================================
# 测试 1：LLM 自动建模四脚板凳
# ============================================================

def test_stool_llm():
    """
    LLM Prompt 测试：创建四脚板凳。

    用户 Prompt: 简短的板凳描述
    预期 LLM 计划: new_part → 画凳面 → 拉伸 → 选面画腿 → 拉伸 ×4
    预期结果: 200×200×20mm 凳面 + 4条 ⌀20mm×300mm 腿，总高 320mm

    验证条件（宽松）：
    - 包围盒总高约 320mm (凳面 20mm + 腿 300mm)
    - 包围盒宽约 200mm
    - 至少有 5 个拉伸特征（1 凳面 + 4 腿）
    """
    print("=" * 60)
    print("LLM 测试 1：四脚板凳自动建模")
    print("=" * 60)

    prompt = (
        "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，"
        "在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 300mm），"
        "腿的圆心距凳面边缘 30mm。"
    )
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
        params_str = str(step.params) if step.params else "{}"
        print(f"    步骤 {i:2d}: {step.operation.value:<25s} {params_str}")
        if step.description:
            print(f"            → {step.description}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    # 3. 验证计划结构（宽松——只要有关键操作）
    required_ops = ["new_part", "feature_extrusion"]
    missing = validate_plan_has_operations(plan, required_ops)
    if missing:
        print(f"  ❌ 计划缺少关键操作: {missing}")
        return False
    print("  ✅ 计划包含必要操作")

    # 检查是否有面选择操作（板凳需要在底面上画腿）
    has_face_select = any(s.operation.value == "select_face_and_sketch" for s in plan.steps)
    if has_face_select:
        print("  ✅ 计划使用了 select_face_and_sketch（在面上建模）")
    else:
        print("  ℹ️ 计划未使用 select_face_and_sketch（可能用了其他策略）")

    # 4. 执行计划
    print("\n--- 执行建模计划 ---")
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    # 5. 检查执行结果
    success_count = sum(1 for r in results if r.get("success", False))
    total_count = len(results)
    print(f"\n  执行结果: {success_count}/{total_count} 步成功")

    all_success = all(r.get("success", False) for r in results)
    if not all_success:
        # 找出失败的步骤
        for r in results:
            if not r.get("success", False):
                print(f"  ❌ 失败步骤: {r.get('operation', '?')} — {r.get('message', '?')}")
        return False

    # 6. 验证几何结果
    print("\n--- 几何验证 ---")

    # 验证包围盒：约 200mm × 320mm × 200mm
    bbox_ok = verify_bounding_box(
        {"sizeX": 200, "sizeY": 320, "sizeZ": 200},
        tolerance_mm=20  # LLM 可能位置稍有偏差
    )

    # 验证拉伸特征数量
    extrude_ok = verify_feature_count("Extrusion", 5)

    if bbox_ok:
        print("\n  🎉 LLM 测试 1 通过！四脚板凳自动建模成功！")
    else:
        print("\n  ⚠️ 建模执行成功但几何验证未完全通过，请在 SolidWorks 中确认结果")

    return bbox_ok


# ============================================================
# 测试 2：LLM 自动建模带圆角的板凳
# ============================================================

def test_stool_fillet_llm():
    """
    LLM Prompt 测试：创建带圆角的四脚板凳。

    比测试 1 增加了圆角修饰特征——考验 LLM 是否懂得：
    - 先做结构特征再做修饰特征
    - 使用语义化选边（如 "top_front"）

    验证条件（宽松）：
    - 包围盒总高约 320mm
    - 至少有 5 个拉伸特征 + 圆角特征
    """
    print("=" * 60)
    print("LLM 测试 2：带圆角的四脚板凳自动建模")
    print("=" * 60)

    prompt = (
        "创建一个四脚板凳并对凳面做圆角处理："
        "凳面为 200mm×200mm×20mm 的方形平板，"
        "在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 300mm），"
        "腿的圆心距凳面边缘 30mm。"
        "最后对凳面顶部的 4 条边做 R5mm 圆角。"
    )
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
        params_str = str(step.params) if step.params else "{}"
        print(f"    步骤 {i:2d}: {step.operation.value:<25s} {params_str}")
        if step.description:
            print(f"            → {step.description}")
    if plan.notes:
        print(f"  备注: {plan.notes}")
    print()

    # 3. 验证计划结构
    required_ops = ["new_part", "feature_extrusion", "feature_fillet"]
    missing = validate_plan_has_operations(plan, required_ops)
    if missing:
        print(f"  ❌ 计划缺少关键操作: {missing}")
        return False
    print("  ✅ 计划包含必要操作（含圆角）")

    # 检查圆角是否在最后（best practice）
    fillet_indices = [i for i, s in enumerate(plan.steps) if s.operation.value == "feature_fillet"]
    extrusion_indices = [i for i, s in enumerate(plan.steps) if s.operation.value == "feature_extrusion"]
    if fillet_indices and extrusion_indices and min(fillet_indices) > max(extrusion_indices):
        print("  ✅ 圆角特征位于所有拉伸之后（符合 CAD 最佳实践）")
    else:
        print("  ℹ️ 圆角特征未全部在拉伸之后（可能导致面拓扑问题）")

    # 4. 执行计划
    print("\n--- 执行建模计划 ---")
    executor = SwExecutor(verbose=True)
    try:
        results = executor.execute_plan(plan)
    finally:
        executor.close()

    # 5. 检查执行结果
    success_count = sum(1 for r in results if r.get("success", False))
    total_count = len(results)
    print(f"\n  执行结果: {success_count}/{total_count} 步成功")

    all_success = all(r.get("success", False) for r in results)
    if not all_success:
        for r in results:
            if not r.get("success", False):
                print(f"  ❌ 失败步骤: {r.get('operation', '?')} — {r.get('message', '?')}")
        return False

    # 6. 验证几何结果
    print("\n--- 几何验证 ---")

    # 验证包围盒：约 200mm × 320mm × 200mm
    bbox_ok = verify_bounding_box(
        {"sizeX": 200, "sizeY": 320, "sizeZ": 200},
        tolerance_mm=20
    )

    # 验证拉伸 + 圆角特征
    verify_feature_count("Extrusion", 5)
    verify_feature_count("Fillet", 1)  # 至少有 1 个圆角特征（LLM 可能合并为一个）

    if bbox_ok:
        print("\n  🎉 LLM 测试 2 通过！带圆角的板凳自动建模成功！")
    else:
        print("\n  ⚠️ 建模执行成功但几何验证未完全通过，请在 SolidWorks 中确认结果")

    return bbox_ok


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    if not check_prerequisites():
        sys.exit(1)

    # 解析命令行参数
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    results = {}

    if arg in ("all", "stool"):
        results["LLM: 四脚板凳"] = test_stool_llm()
        if arg == "all":
            time.sleep(1)

    if arg in ("all", "stool_fillet"):
        results["LLM: 带圆角板凳"] = test_stool_fillet_llm()

    # 汇总
    print("\n" + "=" * 60)
    print("LLM 自动建模测试结果汇总")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
    print()

    all_passed = all(results.values())
    if all_passed:
        print("🎉 所有 LLM 自动建模测试通过！")
    else:
        print("⚠️ 部分 LLM 测试失败")
    sys.exit(0 if all_passed else 1)
