"""
executor.py — SolidWorks HTTP API 执行器

将 LLM 输出的 SwModelingPlan 逐步转为 HTTP 请求，
发送到 SolidWorks C# 服务（默认 http://localhost:5100）。
"""

from __future__ import annotations

from typing import Optional

import requests

from .schemas import SwModelingPlan, SwOperation, OperationType

# 操作类型 → HTTP API 端点映射
OPERATION_ENDPOINT_MAP: dict[OperationType, tuple[str, str]] = {
    OperationType.NEW_PART:           ("POST", "/api/new-part"),
    OperationType.SELECT_PLANE:       ("POST", "/api/select-plane"),
    OperationType.SKETCH_LINE:        ("POST", "/api/sketch/line"),
    OperationType.SKETCH_RECTANGLE:   ("POST", "/api/sketch/rectangle"),
    OperationType.SKETCH_CIRCLE:      ("POST", "/api/sketch/circle"),
    OperationType.FEATURE_EXTRUSION:  ("POST", "/api/feature/extrusion"),
    OperationType.CLOSE_SKETCH:       ("POST", "/api/close-sketch"),
}


class SwExecutor:
    """
    SolidWorks HTTP API 执行器。

    接收 SwModelingPlan，按顺序将每个 SwOperation
    转换为对应的 HTTP 请求，发送到 C# 服务端。
    """

    def __init__(self, base_url: str = "http://localhost:5100"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def ping(self) -> bool:
        """
        检查 SolidWorks C# 服务是否可用。

        Returns:
            True 如果服务可达且正常，否则 False
        """
        try:
            resp = self.session.get(f"{self.base_url}/api/ping", timeout=5)
            data = resp.json()
            return data.get("success", False)
        except Exception as e:
            print(f"[Executor] 无法连接到 SolidWorks 服务: {e}")
            return False

    def execute_plan(self, plan: SwModelingPlan, dry_run: bool = False) -> list[dict]:
        """
        执行整个建模计划。

        Args:
            plan: LLM 生成的建模计划
            dry_run: 如果为 True，仅打印步骤但不实际执行

        Returns:
            每个步骤的执行结果列表
        """
        print(f"\n{'='*60}")
        print(f"建模计划: {plan.user_intent}")
        print(f"共 {len(plan.steps)} 个步骤")
        if plan.notes:
            print(f"备注: {plan.notes}")
        print(f"{'='*60}\n")

        results = []

        for i, step in enumerate(plan.steps, 1):
            print(f"[步骤 {i}/{len(plan.steps)}] {step.operation.value}", end="")
            if step.description:
                print(f" — {step.description}", end="")
            if step.params:
                print(f"  参数: {step.params}", end="")
            print()

            if dry_run:
                results.append({
                    "step": i,
                    "operation": step.operation.value,
                    "status": "dry_run",
                    "message": "模拟执行（未实际调用）",
                })
                continue

            result = self._execute_step(step)
            results.append({
                "step": i,
                "operation": step.operation.value,
                **result,
            })

            if not result.get("success", False):
                print(f"[Executor] ❌ 步骤 {i} 执行失败: {result.get('message', '未知错误')}")
                print("[Executor] 中止后续步骤")
                break
            else:
                print(f"  ✅ {result.get('message', 'OK')}")

        print(f"\n{'='*60}")
        success_count = sum(1 for r in results if r.get("success", False) or r.get("status") == "dry_run")
        print(f"执行完成: {success_count}/{len(plan.steps)} 个步骤成功")
        print(f"{'='*60}\n")

        return results

    def _execute_step(self, step: SwOperation) -> dict:
        """
        执行单个操作步骤。

        Args:
            step: 单个 SolidWorks 操作

        Returns:
            包含 success 和 message 的字典
        """
        endpoint_info = OPERATION_ENDPOINT_MAP.get(step.operation)
        if endpoint_info is None:
            return {
                "success": False,
                "message": f"未知操作类型: {step.operation.value}",
            }

        method, path = endpoint_info
        url = f"{self.base_url}{path}"

        try:
            if method == "POST":
                resp = self.session.post(url, json=step.params if step.params else None, timeout=30)
            else:
                resp = self.session.get(url, timeout=30)

            data = resp.json()
            return {
                "success": data.get("success", False),
                "message": data.get("message", ""),
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "message": f"无法连接到 SolidWorks 服务 ({url})，请确保 C# 服务已启动",
            }
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": f"请求超时 ({url})",
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"执行失败: {str(e)}",
            }

    def close(self):
        """关闭 HTTP session。"""
        self.session.close()
