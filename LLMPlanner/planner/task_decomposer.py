"""Task decomposition for ledger-driven multi-turn modeling."""

from __future__ import annotations

from typing import Any

from .schemas import (
    Milestone,
    OperationType,
    PostconditionSpec,
    SwOperation,
    TaskPlan,
    ThreeViewIR,
)


def _ir_overall_dims_mm(ir: ThreeViewIR) -> list[float]:
    size = ir.overall_size_mm or {}
    length = float(size.get("length") or size.get("x") or 0)
    width = float(
        size.get("width")
        or size.get("depth")
        or size.get("y")
        or 0
    )
    height = float(size.get("height") or size.get("z") or 0)
    dims = [value for value in (length, width, height) if value > 0]
    if len(dims) >= 3:
        return dims[:3]
    # Fall back to numeric dimension annotations when IR size is incomplete.
    from_dims = sorted(
        {
            float(item.value_mm)
            for item in ir.dimensions
            if float(item.value_mm) > 0
        },
        reverse=True,
    )
    for value in from_dims:
        if len(dims) >= 3:
            break
        if value not in dims:
            dims.append(value)
    while len(dims) < 3:
        dims.append(dims[-1] if dims else 10.0)
    return dims[:3]


class TaskDecomposer:
    """Build generic, evidence-driven milestones without object recipes."""

    def __init__(self, llm_client: Any | None = None):
        self.llm_client = llm_client

    def decompose_from_three_view_ir(
        self,
        user_goal: str,
        ir: ThreeViewIR,
        *,
        expected_volume_mm3: float | None = None,
        expected_holes: bool | None = None,
        expected_fillets: bool | None = None,
    ) -> TaskPlan:
        """Build verifiable milestones directly from Vision IR (no LLM)."""
        dims = _ir_overall_dims_mm(ir)
        profile_summary = "；".join(
            profile.outline_desc for profile in ir.profiles[:3]
        )
        holes = [
            feature
            for feature in ir.features
            if str(feature.kind).lower() in {"hole", "slot", "pocket"}
            or "孔" in str(feature.kind)
        ]
        fillets = [
            feature
            for feature in ir.features
            if str(feature.kind).lower() in {"fillet", "chamfer"}
            or "圆角" in str(feature.kind)
            or "倒角" in str(feature.kind)
        ]
        has_holes = bool(holes) if expected_holes is None else expected_holes
        has_fillets = (
            bool(fillets)
            if expected_fillets is None
            else expected_fillets
        )
        body = Milestone(
            id="tv_body",
            title=(
                "按 ThreeViewIR 精确创建主体轮廓并达到目标包围盒"
                + (f"：{profile_summary}" if profile_summary else "")
            ),
            postconditions=[
                PostconditionSpec(
                    kind="feature_count",
                    params={"keyword": "extr", "minimum": 1},
                ),
                PostconditionSpec(
                    kind="bbox_axes",
                    params={"expected": dims, "tolerance": 3.0},
                ),
                PostconditionSpec(kind="valid_entity"),
            ],
        )
        milestones = [body]
        detail_posts: list[PostconditionSpec] = [
            PostconditionSpec(
                kind="bbox_axes",
                params={"expected": dims, "tolerance": 3.0},
            ),
        ]
        # Multiple holes are often one cut-extrude (single ICE), so only require
        # presence of the feature family rather than a 1:1 hole count.
        if has_holes:
            detail_posts.append(
                PostconditionSpec(
                    kind="feature_count",
                    params={"keyword": "ice", "minimum": 1},
                )
            )
        if has_fillets:
            detail_posts.append(
                PostconditionSpec(
                    kind="feature_count",
                    params={"keyword": "fillet", "minimum": 1},
                )
            )
        volume_post = None
        if expected_volume_mm3 and expected_volume_mm3 > 0:
            volume_post = PostconditionSpec(
                kind="volume_range",
                params={
                    "minimum": expected_volume_mm3 * 0.95,
                    "maximum": expected_volume_mm3 * 1.05,
                },
            )
        if has_holes or has_fillets:
            if volume_post is not None:
                detail_posts.append(volume_post)
            milestones.append(
                Milestone(
                    id="tv_details",
                    title=(
                        f"按 IR 完成附加特征"
                        f"（孔/槽 {'有' if has_holes else '无'}，"
                        f"圆角/倒角 {'有' if has_fillets else '无'}）"
                    ),
                    depends_on=[body.id],
                    postconditions=detail_posts,
                )
            )
        elif volume_post is not None:
            body.postconditions.append(volume_post)
        return TaskPlan(
            user_goal=user_goal,
            milestones=milestones,
            notes=(
                f"三视图确定性分解：part_type={ir.part_type}, "
                f"base_view={ir.base_view}, dims_mm={dims}"
            ),
            recovery_buffer=max(4, len(milestones) + 2),
        )

    def decompose_blueprint(
        self,
        user_goal: str,
        batches: list[list[SwOperation]],
        *,
        expected_dims_mm: list[float] | None = None,
        expected_volume_mm3: float | None = None,
    ) -> TaskPlan:
        """Create one verifiable milestone per atomic feature batch."""
        feature_keywords = {
            OperationType.FEATURE_EXTRUSION: "extr",
            OperationType.FEATURE_CUT_EXTRUSION: "ice",
            OperationType.FEATURE_REVOLVE: "revol",
            OperationType.FEATURE_FILLET: "fillet",
            OperationType.FEATURE_CHAMFER: "chamfer",
            OperationType.FEATURE_CIRCULAR_PATTERN: "pattern",
            OperationType.FEATURE_LINEAR_PATTERN: "pattern",
        }
        cumulative: dict[str, int] = {}
        milestones: list[Milestone] = []
        previous: str | None = None
        is_assembly = any(
            action.operation.value.startswith("assembly_")
            for batch in batches
            for action in batch
        )
        component_total = 0
        mate_total = 0
        assembly_path = ""
        for index, batch in enumerate(batches, 1):
            postconditions: list[PostconditionSpec] = []
            labels: list[str] = []
            for action in batch:
                labels.append(action.description or action.operation.value)
                keyword = feature_keywords.get(action.operation)
                if action.operation == OperationType.ASSEMBLY_ADD_COMPONENT:
                    component_total += 1
                    postconditions.append(PostconditionSpec(
                        kind="component_count",
                        params={"minimum": component_total},
                    ))
                elif action.operation in {
                    OperationType.ASSEMBLY_MATE_COINCIDENT,
                    OperationType.ASSEMBLY_MATE_CONCENTRIC,
                    OperationType.ASSEMBLY_MATE_GEAR,
                }:
                    mate_total += 1
                    postconditions.append(PostconditionSpec(
                        kind="mate_count",
                        params={"minimum": mate_total},
                    ))
                elif action.operation == OperationType.ASSEMBLY_CHECK_INTERFERENCE:
                    postconditions.append(PostconditionSpec(
                        kind="no_interference",
                        params={"maximum": 0},
                    ))
                elif action.operation == OperationType.ASSEMBLY_NEW:
                    assembly_path = str(action.params.get("savePath", ""))
                elif action.operation == OperationType.ASSEMBLY_SAVE:
                    postconditions.append(PostconditionSpec(
                        kind="assembly_saved",
                        params={"path": assembly_path},
                    ))
                if keyword is None:
                    continue
                cumulative[keyword] = cumulative.get(keyword, 0) + 1
                postconditions.append(PostconditionSpec(
                    kind="feature_count",
                    params={
                        "keyword": keyword,
                        "minimum": cumulative[keyword],
                    },
                ))
            if not postconditions:
                postconditions.append(PostconditionSpec(
                    kind="last_execution_success",
                ))
            milestone_id = f"blueprint_{index:02d}"
            milestones.append(Milestone(
                id=milestone_id,
                title=(
                    f"执行全局蓝图阶段 {index}/{len(batches)}："
                    + "；".join(labels)
                ),
                depends_on=[] if previous is None else [previous],
                postconditions=postconditions,
                max_actions=min(5, max(1, len(batch))),
            ))
            previous = milestone_id

        if not milestones:
            milestones.append(Milestone(
                id="blueprint_01",
                title="完成全局建模蓝图",
                postconditions=[PostconditionSpec(kind="valid_entity")],
            ))

        final = milestones[-1]
        final.postconditions.append(PostconditionSpec(
            kind="valid_assembly" if is_assembly else "valid_entity"
        ))
        if expected_dims_mm:
            final.postconditions.append(PostconditionSpec(
                kind="bbox_axes",
                params={
                    "expected": expected_dims_mm,
                    "tolerance": 3.0,
                },
            ))
        if expected_volume_mm3 and expected_volume_mm3 > 0:
            final.postconditions.append(PostconditionSpec(
                kind="volume_range",
                params={
                    "minimum": expected_volume_mm3 * 0.95,
                    "maximum": expected_volume_mm3 * 1.05,
                },
            ))
        return TaskPlan(
            user_goal=user_goal,
            milestones=milestones,
            notes="全局计划先行；每个特征事务执行后观察并验收，失败时原位修复。",
            recovery_buffer=max(6, len(milestones) * 2),
        )

    def decompose(self, user_goal: str) -> TaskPlan:
        if self.llm_client is not None and hasattr(
            self.llm_client,
            "decompose_task",
        ):
            return self.llm_client.decompose_task(user_goal)
        return TaskPlan(
            user_goal=user_goal,
            milestones=[Milestone(
                id="model_goal",
                title=user_goal,
                postconditions=[
                    PostconditionSpec(kind="valid_entity"),
                ],
            )],
            notes="通用任务：由 Agent 增量建模，最终验证有效实体。",
        )

