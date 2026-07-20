"""Task decomposition for ledger-driven multi-turn modeling."""

from __future__ import annotations

from typing import Any

from .schemas import Milestone, PostconditionSpec, TaskPlan, ThreeViewIR


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


def _dice_plan(user_goal: str) -> TaskPlan:
    base = Milestone(
        id="dice_body",
        title="创建边长 20mm 的骰子主体",
        postconditions=[
            PostconditionSpec(
                kind="bbox_axes",
                params={"expected": [20, 20, 20], "tolerance": 1.0},
            ),
            PostconditionSpec(
                kind="feature_count",
                params={"keyword": "extr", "minimum": 1},
            ),
        ],
    )
    fillet = Milestone(
        id="dice_fillet",
        title="对主体全部 12 条棱做 R1.5mm 圆角",
        depends_on=[base.id],
        postconditions=[
            PostconditionSpec(
                kind="fillet_radius",
                params={"radius_mm": 1.5, "minimum_features": 1},
            ),
        ],
    )
    face_specs = [
        ("pos_y", "+Y", 1),
        ("neg_y", "-Y", 6),
        ("pos_z", "+Z", 2),
        ("neg_z", "-Z", 5),
        ("pos_x", "+X", 3),
        ("neg_x", "-X", 4),
    ]
    milestones = [base, fillet]
    for index, (suffix, face_key, pips) in enumerate(face_specs, 1):
        milestones.append(Milestone(
            id=f"dice_pips_{suffix}",
            title=f"在 {face_key} 面创建 {pips} 点凹坑",
            depends_on=[fillet.id],
            postconditions=[
                PostconditionSpec(
                    kind="face_pips",
                    params={
                        "face_key": face_key,
                        "circle_count": pips,
                        "diameter_mm": 3.0,
                        "cut_depth_mm": 1.5,
                        "minimum_ice_count": index,
                    },
                ),
            ],
        ))
    return TaskPlan(
        user_goal=user_goal,
        milestones=milestones,
        notes="标准骰子相对面点数和为 7；每个面只允许加工一次。",
        recovery_buffer=4,
    )


def _rounded_dice_base_plan(user_goal: str) -> TaskPlan:
    return TaskPlan(
        user_goal=user_goal,
        milestones=[
            Milestone(
                id="dice_body",
                title="创建边长 20mm 的立方体",
                postconditions=[
                    PostconditionSpec(
                        kind="bbox_axes",
                        params={"expected": [20, 20, 20], "tolerance": 1.0},
                    ),
                    PostconditionSpec(
                        kind="feature_count",
                        params={"keyword": "extr", "minimum": 1},
                    ),
                ],
            ),
            Milestone(
                id="dice_fillet",
                title="对全部 12 条棱做 R2mm 圆角",
                depends_on=["dice_body"],
                postconditions=[
                    PostconditionSpec(
                        kind="fillet_radius",
                        params={"radius_mm": 2.0, "minimum_features": 1},
                    ),
                ],
            ),
        ],
        notes="只创建骰子基础圆角外形，不创建点数。",
    )


def _storage_box_plan(user_goal: str) -> TaskPlan:
    return TaskPlan(
        user_goal=user_goal,
        milestones=[
            Milestone(
                id="box_outer",
                title="创建 120×80×50mm 外轮廓壳体主体",
                postconditions=[
                    PostconditionSpec(
                        kind="bbox_axes",
                        params={"expected": [50, 80, 120], "tolerance": 2.0},
                    ),
                    PostconditionSpec(
                        kind="feature_count",
                        params={"keyword": "extr", "minimum": 1},
                    ),
                ],
            ),
            Milestone(
                id="box_cavity",
                title="切除顶部开口空腔，壁厚与底厚均为 8mm",
                depends_on=["box_outer"],
                postconditions=[
                    PostconditionSpec(
                        kind="feature_count",
                        params={"keyword": "ice", "minimum": 1},
                    ),
                    PostconditionSpec(
                        kind="bbox_axes",
                        params={"expected": [50, 80, 120], "tolerance": 2.0},
                    ),
                ],
            ),
            Milestone(
                id="box_fillet",
                title="顶部开口四条边做 R3mm 圆角",
                depends_on=["box_cavity"],
                postconditions=[
                    PostconditionSpec(
                        kind="fillet_radius",
                        params={
                            "radius_mm": 3.0,
                            "minimum_features": 1,
                            "require_all_edges": False,
                            "minimum_edges": 4,
                        },
                    ),
                ],
            ),
        ],
        notes="收纳盒：外轮廓 → 空腔切除 → 顶部圆角。",
        recovery_buffer=3,
    )


class TaskDecomposer:
    """Prefer deterministic recipes; use the LLM only for unknown objects."""

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

    def decompose(self, user_goal: str) -> TaskPlan:
        normalized = user_goal.lower()
        if any(keyword in normalized for keyword in ("骰子", "色子", "dice")):
            is_standard = any(
                keyword in normalized
                for keyword in ("标准", "点数", "凹坑", "1、2、3、4、5、6")
            )
            return (
                _dice_plan(user_goal)
                if is_standard
                else _rounded_dice_base_plan(user_goal)
            )
        if any(keyword in normalized for keyword in ("收纳盒", "storage box")):
            return _storage_box_plan(user_goal)
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

