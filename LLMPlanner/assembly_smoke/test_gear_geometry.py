"""渐开线齿轮对的纯几何回归测试（不需要 SolidWorks）。"""

from __future__ import annotations

import math

try:
    from LLMPlanner.assembly_smoke.phase1_build_two_gears import (
        BIG_TEETH,
        MODULE_M,
        PRESSURE_ANGLE_DEG,
        SMALL_TEETH,
        generate_involute_outline,
    )
except ModuleNotFoundError:
    try:
        from .phase1_build_two_gears import (  # type: ignore[no-redef]
            BIG_TEETH,
            MODULE_M,
            PRESSURE_ANGLE_DEG,
            SMALL_TEETH,
            generate_involute_outline,
        )
    except ImportError:
        from phase1_build_two_gears import (  # type: ignore[no-redef]
            BIG_TEETH,
            MODULE_M,
            PRESSURE_ANGLE_DEG,
            SMALL_TEETH,
            generate_involute_outline,
        )


def transform_outline(
    points: list[tuple[float, float]],
    angle: float,
    center_x: float,
) -> list[tuple[float, float]]:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return [
        (
            x * cosine - y * sine + center_x,
            x * sine + y * cosine,
        )
        for x, y in points
    ]


def cross(
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
) -> float:
    return (
        (end[0] - start[0]) * (point[1] - start[1])
        - (end[1] - start[1]) * (point[0] - start[0])
    )


def has_proper_edge_intersection(
    first: list[tuple[float, float]],
    second: list[tuple[float, float]],
) -> bool:
    """检测轮廓是否真正交叉；相切不算穿模。"""
    for first_start, first_end in zip(first, first[1:]):
        first_min_x, first_max_x = sorted((first_start[0], first_end[0]))
        first_min_y, first_max_y = sorted((first_start[1], first_end[1]))
        for second_start, second_end in zip(second, second[1:]):
            second_min_x, second_max_x = sorted(
                (second_start[0], second_end[0])
            )
            second_min_y, second_max_y = sorted(
                (second_start[1], second_end[1])
            )
            if (
                first_max_x < second_min_x
                or second_max_x < first_min_x
                or first_max_y < second_min_y
                or second_max_y < first_min_y
            ):
                continue
            first_crosses = (
                cross(first_start, first_end, second_start)
                * cross(first_start, first_end, second_end)
                < -1e-18
            )
            second_crosses = (
                cross(second_start, second_end, first_start)
                * cross(second_start, second_end, first_end)
                < -1e-18
            )
            if first_crosses and second_crosses:
                return True
    return False


def point_is_inside_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
) -> bool:
    """射线法检测点是否严格位于实体轮廓内部。"""
    x, y = point
    inside = False
    for start, end in zip(polygon, polygon[1:]):
        if (start[1] > y) == (end[1] > y):
            continue
        crossing_x = (
            (end[0] - start[0])
            * (y - start[1])
            / (end[1] - start[1])
            + start[0]
        )
        if x < crossing_x:
            inside = not inside
    return inside


def has_material_overlap(
    first: list[tuple[float, float]],
    second: list[tuple[float, float]],
) -> bool:
    """同时覆盖边线交叉与齿顶被完整包含在另一实体内的穿模。"""
    return (
        has_proper_edge_intersection(first, second)
        or any(point_is_inside_polygon(point, second) for point in first[:-1])
        or any(point_is_inside_polygon(point, first) for point in second[:-1])
    )


def test_involute_pair_has_no_interference_over_full_tooth_cycle() -> None:
    small, small_parameters = generate_involute_outline(SMALL_TEETH)
    big, big_parameters = generate_involute_outline(BIG_TEETH, 9)
    center_distance = (
        small_parameters["pitchRadius"] + big_parameters["pitchRadius"]
    )

    assert MODULE_M == 0.004
    assert PRESSURE_ANGLE_DEG == 30.0
    assert abs(center_distance - 0.060) < 1e-12

    small_tooth_cycle = 2 * math.pi / SMALL_TEETH
    for sample in range(73):
        small_angle = small_tooth_cycle * sample / 72
        big_angle = -small_angle * SMALL_TEETH / BIG_TEETH
        positioned_small = transform_outline(
            small,
            small_angle,
            -center_distance / 2,
        )
        positioned_big = transform_outline(
            big,
            big_angle,
            center_distance / 2,
        )
        assert not has_material_overlap(
            positioned_small,
            positioned_big,
        ), f"齿轮在小齿轮转角 {math.degrees(small_angle):.3f}° 时发生穿模"


if __name__ == "__main__":
    test_involute_pair_has_no_interference_over_full_tooth_cycle()
    print("✅ 渐开线齿轮完整齿周期 73 帧无实体重叠")
