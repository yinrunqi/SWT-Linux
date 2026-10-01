"""渐开线直齿轮轮廓（米制），供 L1 spur_gear / internal_gear 与标准零件脚本共用。"""

from __future__ import annotations

import math


def polar(radius: float, angle: float) -> tuple[float, float]:
    return radius * math.cos(angle), radius * math.sin(angle)


def involute_angle(radius: float, base_radius: float) -> float:
    parameter = math.sqrt(max(0.0, (radius / base_radius) ** 2 - 1.0))
    return parameter - math.atan(parameter)


def arc_points(radius: float, start_angle: float, end_angle: float, segments: int) -> list[tuple[float, float]]:
    return [
        polar(radius, start_angle + (end_angle - start_angle) * i / segments)
        for i in range(1, segments + 1)
    ]


def generate_involute_outline(
    tooth_count: int,
    phase_angle_deg: float = 0,
    module_m: float = 0.003,
    pressure_angle_deg: float = 30.0,
    flank_segments: int = 8,
    tip_arc_segments: int = 3,
    root_arc_segments: int = 3,
    addendum: float = 1.0,
    dedendum: float = 1.25,
    thickness_extra_m: float = 0.0,
) -> tuple[list[tuple[float, float]], dict[str, float]]:
    """闭合齿轮外轮廓，坐标单位为米。30° 压力角避免少齿根切。
    addendum / dedendum 以模数为单位；thickness_extra_m 是分度圆上额外增加的齿厚（弧长）。"""
    pressure_angle = math.radians(pressure_angle_deg)
    phase = math.radians(phase_angle_deg)
    pitch_radius = module_m * tooth_count / 2
    base_radius = pitch_radius * math.cos(pressure_angle)
    outer_radius = pitch_radius + addendum * module_m
    root_radius = pitch_radius - dedendum * module_m
    tooth_pitch_angle = 2 * math.pi / tooth_count
    half_tooth_angle_at_pitch = math.pi / (2 * tooth_count) + thickness_extra_m / (2 * pitch_radius)
    pitch_involute = math.tan(pressure_angle) - pressure_angle
    base_half_angle = half_tooth_angle_at_pitch + pitch_involute
    outer_half_angle = base_half_angle - involute_angle(outer_radius, base_radius)
    flank_start_radius = max(root_radius, base_radius)
    root_half_angle = base_half_angle - involute_angle(flank_start_radius, base_radius)

    if root_radius <= 0 or flank_start_radius >= outer_radius:
        raise ValueError("齿根圆或渐开线起点半径无效")
    if outer_half_angle <= 0:
        raise ValueError("齿顶厚度无效，请调整模数、齿数或压力角")
    if tooth_pitch_angle <= 2 * root_half_angle:
        raise ValueError("相邻齿根发生重叠")

    points: list[tuple[float, float]] = []
    for tooth_index in range(tooth_count):
        center_angle = phase + tooth_index * tooth_pitch_angle
        lower_root_angle = center_angle - root_half_angle
        points.append(polar(root_radius, lower_root_angle))
        if root_radius < base_radius:
            points.append(polar(base_radius, center_angle - base_half_angle))
        for sample in range(1, flank_segments + 1):
            radius = flank_start_radius + (outer_radius - flank_start_radius) * sample / flank_segments
            half_angle = base_half_angle - involute_angle(radius, base_radius)
            points.append(polar(radius, center_angle - half_angle))
        points.extend(arc_points(
            outer_radius, center_angle - outer_half_angle, center_angle + outer_half_angle, tip_arc_segments,
        ))
        for sample in range(flank_segments - 1, -1, -1):
            radius = flank_start_radius + (outer_radius - flank_start_radius) * sample / flank_segments
            half_angle = base_half_angle - involute_angle(radius, base_radius)
            points.append(polar(radius, center_angle + half_angle))
        upper_root_angle = center_angle + root_half_angle
        if root_radius < base_radius:
            points.append(polar(root_radius, upper_root_angle))
        next_lower = center_angle + tooth_pitch_angle - root_half_angle
        points.extend(arc_points(root_radius, upper_root_angle, next_lower, root_arc_segments))

    deduplicated: list[tuple[float, float]] = []
    for point in points:
        if not deduplicated or math.dist(deduplicated[-1], point) > 1e-10:
            deduplicated.append(point)
    deduplicated[-1] = deduplicated[0]
    return deduplicated, {
        "module": module_m,
        "pressureAngleDeg": pressure_angle_deg,
        "pitchRadius": pitch_radius,
        "baseRadius": base_radius,
        "outerRadius": outer_radius,
        "rootRadius": root_radius,
        "toothCount": tooth_count,
    }


INTERNAL_ROOT_COEF = 1.15


def generate_internal_cavity(
    tooth_count: int,
    phase_angle_deg: float = 0,
    module_m: float = 0.003,
    pressure_angle_deg: float = 30.0,
    backlash_m: float | None = None,
) -> tuple[list[tuple[float, float]], dict[str, float]]:
    """内齿圈的齿槽轮廓（米）。齿槽形状即同模数同齿数外齿轮的齿形、齿顶高与齿根高对调：
    齿圈齿顶在 r−m，齿根在 r+1.15m（留 0.15m 顶隙），齿槽在分度圆上加宽 backlash（默认 0.05m）。
    phase_angle_deg 是某个齿圈轮齿中心的方位角，与 spur_gear 的 phase 含义一致。"""
    backlash = 0.05 * module_m if backlash_m is None else backlash_m
    outline, params = generate_involute_outline(
        tooth_count=tooth_count,
        phase_angle_deg=phase_angle_deg + 180.0 / tooth_count,
        module_m=module_m,
        pressure_angle_deg=pressure_angle_deg,
        addendum=INTERNAL_ROOT_COEF,
        dedendum=1.0,
        thickness_extra_m=backlash,
    )
    return outline, {
        "module": module_m,
        "pressureAngleDeg": pressure_angle_deg,
        "pitchRadius": params["pitchRadius"],
        "baseRadius": params["baseRadius"],
        "tipRadius": params["rootRadius"],
        "rootRadius": params["outerRadius"],
        "toothCount": tooth_count,
    }
