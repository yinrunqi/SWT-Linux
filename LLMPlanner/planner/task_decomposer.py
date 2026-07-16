"""Task decomposition for ledger-driven multi-turn modeling."""

from __future__ import annotations

from typing import Any

from .schemas import Milestone, PostconditionSpec, TaskPlan


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

