"""Normalize LLM plans into bounded, atomic high-level operations."""

from __future__ import annotations

from math import atan, degrees, isclose

from .schemas import OperationType, SwModelingPlan, SwOperation, ThreeViewIR


def _point(params: dict, suffix: str) -> dict:
    point = {
        "x": float(params[f"x{suffix}"]),
        "y": float(params[f"y{suffix}"]),
    }
    z_key = f"z{suffix}"
    if z_key in params:
        point["z"] = float(params[z_key])
    return point


def _same_point(left: dict, right: dict) -> bool:
    return all(
        isclose(
            float(left.get(axis, 0)),
            float(right.get(axis, 0)),
            abs_tol=1e-9,
        )
        for axis in ("x", "y", "z")
    )


def _collapse_lines(lines: list[SwOperation]) -> SwOperation | None:
    if len(lines) < 2:
        return None
    points = [_point(lines[0].params, "1")]
    current = _point(lines[0].params, "2")
    points.append(current)
    for line in lines[1:]:
        start = _point(line.params, "1")
        end = _point(line.params, "2")
        if not _same_point(current, start):
            return None
        points.append(end)
        current = end
    closed = _same_point(points[0], points[-1])
    if closed:
        points.pop()
    return SwOperation(
        operation=OperationType.SKETCH_POLYLINE,
        params={"points": points, "closed": closed},
        description=f"批量绘制 {len(points)} 点连续轮廓",
    )


def optimize_operations(operations: list[SwOperation]) -> list[SwOperation]:
    """Collapse contiguous sketch primitives without changing plan semantics."""
    optimized: list[SwOperation] = []
    index = 0
    while index < len(operations):
        operation = operations[index]
        if operation.operation == OperationType.SKETCH_LINE:
            end = index + 1
            while (
                end < len(operations)
                and operations[end].operation == OperationType.SKETCH_LINE
            ):
                end += 1
            collapsed = _collapse_lines(operations[index:end])
            if collapsed is not None:
                optimized.append(collapsed)
            else:
                optimized.extend(operations[index:end])
            index = end
            continue
        if operation.operation == OperationType.SKETCH_CIRCLE:
            end = index + 1
            while (
                end < len(operations)
                and operations[end].operation == OperationType.SKETCH_CIRCLE
            ):
                end += 1
            circles = operations[index:end]
            if len(circles) > 1:
                optimized.append(SwOperation(
                    operation=OperationType.SKETCH_CIRCLES,
                    params={
                        "circles": [
                            dict(circle.params)
                            for circle in circles
                        ],
                    },
                    description=f"批量绘制 {len(circles)} 个圆",
                ))
            else:
                optimized.append(operation)
            index = end
            continue
        optimized.append(operation)
        index += 1
    return optimized


_FEATURE_OPERATIONS = {
    OperationType.FEATURE_EXTRUSION,
    OperationType.FEATURE_CUT_EXTRUSION,
    OperationType.FEATURE_REVOLVE,
    OperationType.FEATURE_FILLET,
    OperationType.FEATURE_CHAMFER,
    OperationType.FEATURE_CIRCULAR_PATTERN,
    OperationType.FEATURE_LINEAR_PATTERN,
    OperationType.MODIFY_DIMENSION,
    OperationType.ASSEMBLY_NEW,
    OperationType.ASSEMBLY_MATE_COINCIDENT,
    OperationType.ASSEMBLY_MATE_CONCENTRIC,
    OperationType.ASSEMBLY_MATE_GEAR,
    OperationType.ASSEMBLY_SET_COMPONENT_POSE,
    OperationType.ASSEMBLY_SET_CRANK_SLIDER_POSE,
    OperationType.ASSEMBLY_SET_INDEXED_ROTARY_POSE,
    OperationType.ASSEMBLY_REBUILD,
    OperationType.ASSEMBLY_CHECK_INTERFERENCE,
    OperationType.ASSEMBLY_SAVE,
}


def group_feature_batches(
    operations: list[SwOperation],
    *,
    maximum_actions: int = 5,
) -> list[list[SwOperation]]:
    """Group selection/sketch/feature transactions without splitting them."""
    batches: list[list[SwOperation]] = []
    current: list[SwOperation] = []
    for operation in optimize_operations(operations):
        current.append(operation)
        if operation.operation in _FEATURE_OPERATIONS:
            batches.append(current)
            current = []
        elif len(current) >= maximum_actions:
            # Queries or document setup may not terminate in a feature.
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return batches


def _dimension_mm(ir: ThreeViewIR, *terms: str) -> float | None:
    lowered_terms = tuple(term.lower() for term in terms)
    for dimension in ir.dimensions:
        haystack = f"{dimension.label} {dimension.applies_to}".lower()
        if all(term in haystack for term in lowered_terms):
            return float(dimension.value_mm)
    return None


def stabilize_three_view_blueprint(
    plan: SwModelingPlan,
    ir: ThreeViewIR,
) -> SwModelingPlan:
    """Repair structurally incomplete plans using only the current IR.

    This is geometry-class logic, not a benchmark recipe.  A concave L profile
    is fully determined by its outer width/height plus notch width/height, so a
    deterministic polyline is safer than retrying an LLM-generated regular
    polygon or intersecting rectangles.
    """
    # The C# endpoint requires a positive depth even for through_all.  Providers
    # often omit it because it is geometrically redundant, so derive a harmless
    # traversal depth from the existing base feature or current IR.
    base_depths = [
        float(step.params["depth"])
        for step in plan.steps
        if step.operation == OperationType.FEATURE_EXTRUSION
        and step.params.get("depth") is not None
    ]
    size_values = [
        float(value) / 1000.0
        for value in (ir.overall_size_mm or {}).values()
        if isinstance(value, (int, float)) and value > 0
    ]
    traversal_depth = max(base_depths or size_values or [0.001])
    normalized_steps = []
    for step in plan.steps:
        if (
            step.operation == OperationType.FEATURE_CUT_EXTRUSION
            and str(step.params.get("endCondition", "")).lower()
            == "through_all"
            and not step.params.get("depth")
        ):
            normalized_steps.append(step.model_copy(update={
                "params": {**step.params, "depth": traversal_depth},
            }))
        else:
            normalized_steps.append(step)
    plan = plan.model_copy(update={"steps": normalized_steps})

    profile_text = " ".join(
        profile.outline_desc for profile in ir.profiles
    ).lower()
    feature_kinds_text = " ".join(feature.kind.lower() for feature in ir.features)

    top_face_sizes = [
        float(dimension.value_mm)
        for dimension in ir.dimensions
        if (
            "顶面" in f"{dimension.label} {dimension.applies_to}"
            or "top" in f"{dimension.label} {dimension.applies_to}".lower()
        )
        and "圆角" not in f"{dimension.label} {dimension.applies_to}"
        and "fillet" not in f"{dimension.label} {dimension.applies_to}".lower()
    ]
    overall_values = sorted(
        (
            float(value)
            for value in (ir.overall_size_mm or {}).values()
            if isinstance(value, (int, float)) and value > 0
        ),
        reverse=True,
    )
    if top_face_sizes and len(overall_values) >= 3:
        base_width_mm, base_height_mm = overall_values[:2]
        extrusion_mm = overall_values[-1]
        top_width_mm = max(top_face_sizes)
        top_height_mm = min(top_face_sizes)
        if (
            0 < top_width_mm <= base_width_mm
            and 0 < top_height_mm <= base_height_mm
            and extrusion_mm > 0
        ):
            inset_mm = max(
                (base_width_mm - top_width_mm) / 2.0,
                (base_height_mm - top_height_mm) / 2.0,
            )
            draft_angle = degrees(atan(inset_mm / extrusion_mm))
            drafted_steps = [
                SwOperation(
                    operation=OperationType.NEW_PART,
                    params={},
                    description="新建拔模棱柱零件",
                ),
                SwOperation(
                    operation=OperationType.SELECT_PLANE,
                    params={"index": 0},
                    description="选择主轮廓基准面",
                ),
                SwOperation(
                    operation=OperationType.SKETCH_RECTANGLE,
                    params={
                        "x1": -base_width_mm / 2000.0,
                        "y1": -base_height_mm / 2000.0,
                        "x2": base_width_mm / 2000.0,
                        "y2": base_height_mm / 2000.0,
                    },
                    description="按 IR 底面尺寸绘制居中矩形",
                ),
                SwOperation(
                    operation=OperationType.FEATURE_EXTRUSION,
                    params={
                        "depth": extrusion_mm / 1000.0,
                        "draftAngleDeg": draft_angle,
                        "draftOutward": False,
                    },
                    description="按底面、顶面和高度关系生成均匀内收侧壁",
                ),
            ]

            blind_dimensions = [
                dimension
                for dimension in ir.dimensions
                if any(
                    marker in f"{dimension.label} {dimension.applies_to}".lower()
                    for marker in ("盲槽", "blind", "recess", "pocket")
                )
            ]
            blind_depth = next(
                (
                    float(dimension.value_mm)
                    for dimension in blind_dimensions
                    if any(
                        marker in f"{dimension.label} {dimension.applies_to}".lower()
                        for marker in ("切深", "深度", "cut depth", "blind depth")
                    )
                    and float(dimension.value_mm) < extrusion_mm
                ),
                None,
            )
            blind_planar = sorted(
                (
                    float(dimension.value_mm)
                    for dimension in blind_dimensions
                    if float(dimension.value_mm) != blind_depth
                ),
                reverse=True,
            )
            if blind_depth and blind_planar:
                pocket_width = blind_planar[0]
                pocket_height = blind_planar[1] if len(blind_planar) > 1 else pocket_width
                drafted_steps.extend([
                    SwOperation(
                        operation=OperationType.SELECT_FACE_AND_SKETCH,
                        params={
                            "rx": 0.001,
                            "ry": 0.001,
                            "rz": -0.001,
                            "dx": 0.0,
                            "dy": 0.0,
                            "dz": 1.0,
                        },
                        description="选择底面并创建盲槽草图",
                    ),
                    SwOperation(
                        operation=OperationType.SKETCH_RECTANGLE,
                        params={
                            "x1": -pocket_width / 2000.0,
                            "y1": -pocket_height / 2000.0,
                            "z1": 0.0,
                            "x2": pocket_width / 2000.0,
                            "y2": pocket_height / 2000.0,
                            "z2": 0.0,
                        },
                        description="按 IR 尺寸绘制居中盲槽",
                    ),
                    SwOperation(
                        operation=OperationType.FEATURE_CUT_EXTRUSION,
                        params={
                            "depth": blind_depth / 1000.0,
                            "endCondition": "blind",
                        },
                        description="按 IR 深度切除盲槽",
                    ),
                ])

            fillet_radius = next(
                (
                    float(dimension.value_mm)
                    for dimension in ir.dimensions
                    if "圆角" in f"{dimension.label} {dimension.applies_to}"
                    or "fillet" in f"{dimension.label} {dimension.applies_to}".lower()
                ),
                None,
            )
            fillet_count = sum(
                feature.count
                for feature in ir.features
                if feature.kind.lower() in ("fillet", "圆角")
            )
            if fillet_radius and fillet_count:
                for edge_name in (
                    "front_left",
                    "front_right",
                    "front_top",
                    "front_bottom",
                )[:min(fillet_count, 4)]:
                    drafted_steps.append(SwOperation(
                        operation=OperationType.FEATURE_FILLET,
                        params={
                            "radius": fillet_radius / 1000.0,
                            "edgeSelection": edge_name,
                        },
                        description="按 IR 圆角半径处理顶面周边边线",
                    ))

            return plan.model_copy(update={
                "steps": drafted_steps,
                "notes": (
                    plan.notes
                    + " 拔模棱柱已按 IR 的底面、顶面和高度关系参数化归一化。"
                ).strip(),
            })

    has_tapered_side_view = "trapezoid" in profile_text or "梯形" in profile_text
    only_projected_holes = (
        bool(ir.features)
        and all(
            feature.kind.lower() in ("hole", "孔")
            for feature in ir.features
        )
    )
    if has_tapered_side_view and only_projected_holes:
        # A smaller nested rectangle in one view plus trapezoidal adjacent views
        # is commonly the projected top face, not a through-cut.  Remove only
        # cut transactions; retain the drafted base and cosmetic features.
        retained_batches = [
            batch
            for batch in group_feature_batches(plan.steps)
            if not batch
            or batch[-1].operation != OperationType.FEATURE_CUT_EXTRUSION
        ]
        plan = plan.model_copy(update={
            "steps": [
                operation
                for batch in retained_batches
                for operation in batch
            ],
        })

    is_t_profile = any(
        marker in profile_text
        for marker in ("t-shaped", "t shaped", "t形", "t型")
    )
    if ir.part_type != "l_bracket" and not is_t_profile:
        return plan

    size = ir.overall_size_mm or {}
    width_mm = (
        _dimension_mm(ir, "overall", "length")
        or _dimension_mm(ir, "总", "长")
        or size.get("length")
        or size.get("x")
    )
    height_mm = (
        _dimension_mm(ir, "overall", "height")
        or _dimension_mm(ir, "总", "高")
        or size.get("height")
        or size.get("y")
    )
    depth_mm = (
        _dimension_mm(ir, "overall", "depth")
        or _dimension_mm(ir, "总", "厚")
        or _dimension_mm(ir, "主体", "深")
        or size.get("depth")
        or size.get("width")
        or size.get("z")
    )
    notch_width_mm = (
        _dimension_mm(ir, "notch", "width")
        or _dimension_mm(ir, "缺口", "宽")
        or _dimension_mm(ir, "stem", "width")
        or _dimension_mm(ir, "竖杆", "宽")
    )
    notch_height_mm = (
        _dimension_mm(ir, "notch", "height")
        or _dimension_mm(ir, "缺口", "高")
        or _dimension_mm(ir, "stem", "height")
        or _dimension_mm(ir, "竖杆", "高")
    )
    values = (
        width_mm,
        height_mm,
        depth_mm,
        notch_width_mm,
        notch_height_mm,
    )
    if any(value is None or float(value) <= 0 for value in values):
        return plan

    width, height, depth, notch_width, notch_height = (
        float(value) / 1000.0 for value in values
    )
    if notch_width >= width or notch_height >= height:
        return plan

    if is_t_profile:
        stem_left = round((width - notch_width) / 2.0, 12)
        stem_right = round(stem_left + notch_width, 12)
        points = [
            {"x": stem_left, "y": 0.0},
            {"x": stem_right, "y": 0.0},
            {"x": stem_right, "y": notch_height},
            {"x": width, "y": notch_height},
            {"x": width, "y": height},
            {"x": 0.0, "y": height},
            {"x": 0.0, "y": notch_height},
            {"x": stem_left, "y": notch_height},
        ]
        profile_description = "按当前 IR 生成居中竖杆与顶部横梁的连续 T 形轮廓"
    else:
        points = [
            {"x": 0.0, "y": 0.0},
            {"x": width, "y": 0.0},
            {"x": width, "y": height - notch_height},
            {"x": width - notch_width, "y": height - notch_height},
            {"x": width - notch_width, "y": height},
            {"x": 0.0, "y": height},
        ]
        profile_description = "按当前 IR 的外轮廓与缺口尺寸生成连续凹多边形"

    polyline = SwOperation(
        operation=OperationType.SKETCH_POLYLINE,
        params={
            "points": points,
            "closed": True,
        },
        description=profile_description,
    )

    first_feature = next(
        (
            index
            for index, operation in enumerate(plan.steps)
            if operation.operation in _FEATURE_OPERATIONS
        ),
        None,
    )
    base_end = first_feature if first_feature is not None else len(plan.steps)
    prefix = [
        operation
        for operation in plan.steps[:base_end]
        if not operation.operation.value.startswith("sketch_")
    ]
    if not any(
        operation.operation == OperationType.NEW_PART for operation in prefix
    ):
        prefix.insert(0, SwOperation(
            operation=OperationType.NEW_PART,
            params={},
            description="新建测试零件",
        ))
    if not any(
        operation.operation == OperationType.SELECT_PLANE for operation in prefix
    ):
        plane_index = {"front": 0, "top": 1, "right": 2}.get(ir.base_view, 0)
        prefix.append(SwOperation(
            operation=OperationType.SELECT_PLANE,
            params={"index": plane_index},
            description="选择 IR 主轮廓对应基准面",
        ))
    prefix.append(polyline)

    base_extrusion = SwOperation(
        operation=OperationType.FEATURE_EXTRUSION,
        params={"depth": depth},
        description="按当前 IR 深度拉伸主体轮廓",
    )
    if (
        first_feature is None
        or plan.steps[first_feature].operation
        != OperationType.FEATURE_EXTRUSION
    ):
        suffix = [base_extrusion]
    else:
        suffix = [plan.steps[first_feature]]

    has_independent_features = any(
        marker in feature_kinds_text
        for marker in (
            "hole",
            "孔",
            "fillet",
            "圆角",
            "chamfer",
            "倒角",
            "slot",
            "槽",
            "boss",
            "凸台",
        )
    )
    if first_feature is not None and has_independent_features:
        suffix.extend(plan.steps[first_feature + 1:])

    if not suffix:
        suffix = [SwOperation(
            operation=OperationType.FEATURE_EXTRUSION,
            params={"depth": depth},
            description="按当前 IR 深度拉伸主体轮廓",
        )]

    return plan.model_copy(update={
        "steps": prefix + suffix,
        "notes": (
            plan.notes
            + " 主体凹轮廓已由 IR 尺寸确定性归一化为连续 polyline。"
        ).strip(),
    })
