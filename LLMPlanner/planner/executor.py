"""
executor.py — SolidWorks HTTP API 执行器

将 LLM 输出的 SwModelingPlan 逐步转为 HTTP 请求，
发送到 SolidWorks C# 服务（默认 http://localhost:5100）。

特色：
- feature_tree 执行后自动缓存特征信息
- modify_dimension 执行前自动修正 featureName（解决中文版 SolidWorks 特征名问题）
"""

from __future__ import annotations

import json
from typing import Optional

import requests

from .schemas import SwModelingPlan, SwOperation, OperationType

# 操作类型 → HTTP API 端点映射
OPERATION_ENDPOINT_MAP: dict[OperationType, tuple[str, str]] = {
    OperationType.NEW_PART:           ("POST", "/api/new-part"),
    OperationType.SELECT_PLANE:       ("POST", "/api/select-plane"),
    OperationType.SKETCH_LINE:        ("POST", "/api/sketch/line"),
    OperationType.SKETCH_POLYLINE:    ("POST", "/api/sketch/polyline"),
    OperationType.SKETCH_RECTANGLE:   ("POST", "/api/sketch/rectangle"),
    OperationType.SKETCH_CIRCLE:      ("POST", "/api/sketch/circle"),
    OperationType.SKETCH_CIRCLES:     ("POST", "/api/sketch/circles"),
    OperationType.SKETCH_CENTERLINE:  ("POST", "/api/sketch/centerline"),
    OperationType.SKETCH_ARC:         ("POST", "/api/sketch/arc"),
    OperationType.SKETCH_ELLIPSE:     ("POST", "/api/sketch/ellipse"),
    OperationType.SKETCH_POLYGON:     ("POST", "/api/sketch/polygon"),
    OperationType.SKETCH_SLOT:        ("POST", "/api/sketch/slot"),
    OperationType.FEATURE_EXTRUSION:  ("POST", "/api/feature/extrusion"),
    OperationType.FEATURE_CUT_EXTRUSION: ("POST", "/api/feature/cut-extrusion"),
    OperationType.FEATURE_REVOLVE:    ("POST", "/api/feature/revolve"),
    OperationType.FEATURE_FILLET:     ("POST", "/api/feature/fillet"),
    OperationType.FEATURE_CHAMFER:    ("POST", "/api/feature/chamfer"),
    OperationType.FEATURE_CIRCULAR_PATTERN: ("POST", "/api/feature/circular-pattern"),
    OperationType.FEATURE_LINEAR_PATTERN: ("POST", "/api/feature/linear-pattern"),
    OperationType.CLOSE_SKETCH:       ("POST", "/api/close-sketch"),
    # --- 查询 & 编辑 ---
    OperationType.FEATURE_TREE:            ("GET",  "/api/feature-tree"),
    OperationType.BODY_BOUNDING_BOX:       ("GET",  "/api/body-bounding-box"),
    OperationType.SELECT_FACE_AND_SKETCH:  ("POST", "/api/select-face-and-sketch"),
    OperationType.MODIFY_DIMENSION:        ("POST", "/api/modify-dimension"),
}

# 常见的英文→中文特征类型名映射（SolidWorks 中文版）
# 用于智能匹配 LLM 猜测的英文名到实际中文名
FEATURE_NAME_ALIASES: dict[str, list[str]] = {
    # 英文关键词 → 对应的中文特征 type 名
    "Boss-Extrude": ["凸台-拉伸", "Boss-Extrude"],
    "Cut-Extrude": ["切除-拉伸", "Cut-Extrude"],
    "Boss-Revolve": ["凸台-旋转", "Boss-Revolve"],
    "Cut-Revolve": ["切除-旋转", "Cut-Revolve"],
    "Fillet": ["圆角", "Fillet"],
    "Chamfer": ["倒角", "Chamfer"],
    "Shell": ["抽壳", "Shell"],
    "Mirror": ["镜像", "Mirror"],
    "LPattern": ["线性阵列", "LPattern"],
    "CirPattern": ["圆周阵列", "CirPattern"],
}


class SwExecutor:
    """
    SolidWorks HTTP API 执行器。

    接收 SwModelingPlan，按顺序将每个 SwOperation
    转换为对应的 HTTP 请求，发送到 C# 服务端。

    特色功能：
    - 执行 feature_tree 后自动缓存返回的特征列表
    - 执行 modify_dimension 前自动修正 featureName
      （解决 LLM 猜英文名但实际 SolidWorks 用中文名的问题）
    """

    def __init__(self, base_url: str = "http://localhost:5100", verbose: bool = True):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self.verbose = verbose

        # 最近一次 feature_tree 返回的特征列表，用于智能修正后续步骤的特征名
        self._cached_features: list[dict] | None = None

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

    # ================================================================
    # 特征名智能修正
    # ================================================================

    def _resolve_feature_name(self, llm_feature_name: str) -> str:
        """
        将 LLM 猜测的特征名（可能是英文）修正为 SolidWorks 中实际的特征名。

        匹配策略（按优先级）：
        1. 精确匹配：缓存中存在完全一致的名称 → 直接返回
        2. 编号匹配：提取 LLM 名称中的基础名和编号（如 "Boss-Extrude1" → 基础名 "Boss-Extrude", 编号 "1"），
           然后在缓存中查找对应中文名+相同编号的特征
        3. 类型匹配：如果 LLM 名称包含某种特征类型关键词（如 "Extrude"），
           在缓存中查找同类型的特征并按编号对应

        Args:
            llm_feature_name: LLM 生成的特征名称

        Returns:
            修正后的实际特征名称（如果无法修正则返回原名）
        """
        if self._cached_features is None:
            if self.verbose:
                print(f"  [修正] ⚠️  无缓存的特征树数据，无法修正特征名，使用原名: {llm_feature_name}")
            return llm_feature_name

        cached_names = [f["name"] for f in self._cached_features]

        # 策略 1：精确匹配
        if llm_feature_name in cached_names:
            if self.verbose:
                print(f"  [修正] ✅ 特征名精确匹配: {llm_feature_name}")
            return llm_feature_name

        # 策略 2：提取基础名和编号，做别名匹配
        base_name, number = self._split_feature_name(llm_feature_name)
        if self.verbose:
            print(f"  [修正] 特征名 \"{llm_feature_name}\" 不在特征树中")
            print(f"  [修正] 解析: 基础名=\"{base_name}\", 编号=\"{number}\"")
            print(f"  [修正] 特征树中的所有特征名: {cached_names}")

        # 查找别名列表
        alias_candidates = []
        for eng_key, zh_names in FEATURE_NAME_ALIASES.items():
            if base_name.lower() == eng_key.lower() or base_name in zh_names:
                alias_candidates = zh_names
                break

        # 如果没找到预定义别名，尝试模糊匹配（基础名包含在缓存特征名中）
        if not alias_candidates:
            # 尝试：缓存中是否有特征名以编号结尾且类型匹配
            for feat in self._cached_features:
                feat_base, feat_num = self._split_feature_name(feat["name"])
                if feat_num == number and feat_base != base_name:
                    # 同编号但不同基础名，可能是中英文对应
                    alias_candidates.append(feat_base)

        if alias_candidates and number:
            # 在缓存中查找 "别名+编号" 的特征
            for alias in alias_candidates:
                candidate = f"{alias}{number}"
                if candidate in cached_names:
                    print(f"  [修正] 🔄 特征名自动修正: \"{llm_feature_name}\" → \"{candidate}\"")
                    return candidate

        # 策略 3：如果只有一个同类型的特征，直接用它
        if base_name:
            type_keyword = base_name.lower().replace("-", "")
            matching_by_type = []
            for feat in self._cached_features:
                feat_type = feat.get("type", "").lower().replace("-", "")
                feat_name_lower = feat["name"].lower().replace("-", "")
                # 检查特征类型或名称是否包含关键词
                if (type_keyword in feat_type or type_keyword in feat_name_lower
                        or "extrusion" in feat_type and "extrude" in type_keyword
                        or "extrude" in type_keyword and "拉伸" in feat["name"]):
                    matching_by_type.append(feat)

            if len(matching_by_type) == 1:
                resolved = matching_by_type[0]["name"]
                print(f"  [修正] 🔄 特征名按类型修正: \"{llm_feature_name}\" → \"{resolved}\"（同类型仅一个）")
                return resolved
            elif len(matching_by_type) > 1 and number:
                # 多个同类型，尝试按编号取第 N 个
                idx = int(number) - 1
                if 0 <= idx < len(matching_by_type):
                    resolved = matching_by_type[idx]["name"]
                    print(f"  [修正] 🔄 特征名按类型+编号修正: \"{llm_feature_name}\" → \"{resolved}\"")
                    return resolved

        print(f"  [修正] ⚠️  无法自动修正特征名 \"{llm_feature_name}\"，使用原名")
        return llm_feature_name

    @staticmethod
    def _split_feature_name(name: str) -> tuple[str, str]:
        """
        拆分特征名为 (基础名, 编号)。

        示例:
            "Boss-Extrude1" → ("Boss-Extrude", "1")
            "凸台-拉伸1"    → ("凸台-拉伸", "1")
            "Fillet2"       → ("Fillet", "2")
            "前视基准面"     → ("前视基准面", "")
        """
        # 从末尾提取连续数字
        i = len(name)
        while i > 0 and name[i - 1].isdigit():
            i -= 1
        if i < len(name):
            return name[:i], name[i:]
        return name, ""

    def _resolve_delta(self, step: SwOperation) -> None:
        """
        处理 modify_dimension 的 delta 增量模式。

        从缓存的 feature_tree 中查找目标特征的目标尺寸的当前值，
        然后计算 newValue = currentValue + delta，替换到 params 中。

        如果找不到当前值（无缓存或特征/尺寸不匹配），则回退到先调用
        feature_tree API 实时查询。
        """
        delta = step.params.get("delta", 0)
        feature_name = step.params.get("featureName", "")
        dim_name = step.params.get("dimensionName", "")

        print(f"  [增量] delta={delta}, 目标特征=\"{feature_name}\", 目标尺寸=\"{dim_name}\"")

        current_value = self._get_dimension_value_from_cache(feature_name, dim_name)

        if current_value is None:
            # 缓存中没有，尝试实时查询 feature_tree
            print(f"  [增量] 缓存中无匹配数据，实时查询 feature_tree...")
            try:
                resp = self.session.get(f"{self.base_url}/api/feature-tree", timeout=30)
                data = resp.json()
                if data.get("success") and "features" in data:
                    self._cached_features = data["features"]
                    current_value = self._get_dimension_value_from_cache(feature_name, dim_name)
            except Exception as e:
                print(f"  [增量] ⚠️  实时查询失败: {e}")

        if current_value is not None:
            new_value = current_value + delta
            print(f"  [增量] ✅ 当前值={current_value:.6f}, delta={delta}, newValue={new_value:.6f}")
            step.params["newValue"] = new_value
            # 移除 delta，避免发送给 C# 端（C# 端不认识 delta）
            del step.params["delta"]
        else:
            print(f"  [增量] ⚠️  无法获取当前值，回退为绝对值模式: newValue={delta}")
            # 无法查到当前值，把 delta 当 newValue 用（不理想但至少不会崩）
            step.params["newValue"] = delta
            del step.params["delta"]

    def _get_dimension_value_from_cache(self, feature_name: str, dim_name: str) -> float | None:
        """
        从缓存的特征树中查找指定特征、指定尺寸的当前值。

        Args:
            feature_name: 特征名
            dim_name: 尺寸名（空字符串表示取第一个尺寸）

        Returns:
            尺寸的当前值（float），找不到返回 None
        """
        if self._cached_features is None:
            return None

        for feat in self._cached_features:
            if feat["name"] != feature_name:
                continue
            dims = feat.get("dimensions", [])
            if not dims:
                continue
            if not dim_name:
                # 取第一个尺寸
                val = dims[0].get("value")
                if isinstance(val, (int, float)):
                    return float(val)
                elif isinstance(val, list) and len(val) > 0:
                    return float(val[0])
                return None
            for d in dims:
                if d.get("name") == dim_name or d.get("fullName") == dim_name:
                    val = d.get("value")
                    if isinstance(val, (int, float)):
                        return float(val)
                    elif isinstance(val, list) and len(val) > 0:
                        return float(val[0])
                    return None
        return None

    # ================================================================
    # 执行逻辑
    # ================================================================

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

        results = self.execute_operations(
            plan.steps,
            dry_run=dry_run,
            step_offset=0,
            total_steps=len(plan.steps),
        )

        print(f"\n{'='*60}")
        success_count = sum(
            1
            for result in results
            if result.get("success", False) or result.get("status") == "dry_run"
        )
        print(f"执行完成: {success_count}/{len(plan.steps)} 个步骤成功")
        print(f"{'='*60}\n")

        return results

    def execute_operations(
        self,
        operations: list[SwOperation],
        *,
        dry_run: bool = False,
        step_offset: int = 0,
        total_steps: int | None = None,
        include_turn_step: bool = False,
    ) -> list[dict]:
        """执行一小批增量操作，供 single-turn 和 multi-turn 共用。"""
        results: list[dict] = []
        display_total = total_steps or step_offset + len(operations)

        for local_index, step in enumerate(operations, 1):
            step_index = step_offset + local_index
            print(f"[步骤 {step_index}/{display_total}] {step.operation.value}", end="")
            if step.description:
                print(f" — {step.description}", end="")
            if step.params:
                print(f"  参数: {step.params}", end="")
            print()

            if dry_run:
                dry_run_result = {
                    "step": step_index,
                    "operation": step.operation.value,
                    "status": "dry_run",
                    "message": "模拟执行（未实际调用）",
                }
                if include_turn_step:
                    dry_run_result["turn_step"] = local_index
                results.append(dry_run_result)
                continue

            # ---- 执行前：智能修正参数 ----
            if step.operation == OperationType.MODIFY_DIMENSION and step.params:
                original_name = step.params.get("featureName", "")
                if original_name:
                    resolved_name = self._resolve_feature_name(original_name)
                    if resolved_name != original_name:
                        step.params["featureName"] = resolved_name

                # 处理 delta 增量模式：从缓存中读取旧值，加上 delta 计算 newValue
                if "delta" in step.params and "newValue" not in step.params:
                    self._resolve_delta(step)

            result = self._execute_step(step)
            response_metadata = result.pop("_response_metadata", {})

            # ---- 执行后：处理返回数据 ----
            if step.operation == OperationType.FEATURE_TREE and result.get("success"):
                self._on_feature_tree_result(result)

            execution_result = {
                "step": step_index,
                "operation": step.operation.value,
                **result,
                **response_metadata,
            }
            if include_turn_step:
                execution_result["turn_step"] = local_index
            results.append(execution_result)

            if not result.get("success", False):
                print(
                    f"[Executor] ❌ 步骤 {step_index} 执行失败: "
                    f"{result.get('message', '未知错误')}"
                )
                print("[Executor] 中止后续步骤")
                break
            else:
                print(f"  ✅ {result.get('message', 'OK')}")

        return results

    def _on_feature_tree_result(self, result: dict) -> None:
        """
        feature_tree 执行成功后的回调：缓存特征列表并打印详情。
        """
        features = result.get("_raw_features")
        if features is None:
            return

        self._cached_features = features

        # 打印特征树详情，方便调试
        print(f"  [特征树] 共 {len(features)} 个特征:")
        for feat in features:
            suppressed = " (已压制)" if feat.get("suppressed") else ""
            dims = feat.get("dimensions", [])
            dim_str = ""
            if dims:
                dim_details = [f"{d['name']}={d['value']}" for d in dims]
                dim_str = f"  尺寸: [{', '.join(dim_details)}]"
            print(f"    [{feat['index']:>2}] {feat['name']:<25} 类型: {feat['type']}{suppressed}{dim_str}")

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

        if self.verbose:
            print(f"  [HTTP] {method} {url}", end="")
            if method == "POST" and step.params:
                print(f"  body={json.dumps(step.params, ensure_ascii=False)}", end="")
            print()

        try:
            # 超时设为 90s：实体含较多圆角/倒角/切除特征时，射线选面与特征树遍历会变慢（可能 >30s）
            if method == "POST":
                resp = self.session.post(url, json=step.params if step.params else None, timeout=90)
            else:
                resp = self.session.get(url, timeout=90)

            data = resp.json()

            if self.verbose:
                # 打印响应摘要（截断过长的内容）
                resp_summary = json.dumps(data, ensure_ascii=False)
                if len(resp_summary) > 500:
                    resp_summary = resp_summary[:500] + "...(截断)"
                print(f"  [HTTP] 响应 [{resp.status_code}]: {resp_summary}")

            result = {
                "success": data.get("success", False),
                "message": data.get("message", ""),
                "_response_metadata": {
                    key: value
                    for key, value in data.items()
                    if key not in ("success", "message")
                },
            }

            # 对 feature_tree 特殊处理：保存原始 features 数据供后续修正使用
            if step.operation == OperationType.FEATURE_TREE and "features" in data:
                result["_raw_features"] = data["features"]

            return result

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
