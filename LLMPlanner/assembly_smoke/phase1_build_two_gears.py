"""用分段渐开线轮廓生成一对可共轭啮合的真实齿轮零件。"""

from __future__ import annotations

import math
import os

import requests


BASE = "http://localhost:5100"
SMOKE_DIR = os.path.abspath(os.path.dirname(__file__))
MODULE_M = 0.004
PRESSURE_ANGLE_DEG = 30.0
THICKNESS_M = 0.015
BORE_RADIUS_M = 0.005
SMALL_TEETH = 10
BIG_TEETH = 20


def call(path: str, body: dict | None = None) -> dict:
    response = requests.post(f"{BASE}{path}", json=body or {}, timeout=90)
    data = response.json()
    if not response.ok or not data.get("success"):
        raise RuntimeError(f"{path} failed: {data.get('message')}")
    return data


def polar(radius: float, angle: float) -> tuple[float, float]:
    return radius * math.cos(angle), radius * math.sin(angle)


def involute_angle(radius: float, base_radius: float) -> float:
    """返回渐开线在指定半径处相对基圆切点的极角增量。"""
    parameter = math.sqrt(max(0.0, (radius / base_radius) ** 2 - 1.0))
    return parameter - math.atan(parameter)


def arc_points(
    radius: float,
    start_angle: float,
    end_angle: float,
    segments: int,
) -> list[tuple[float, float]]:
    return [
        polar(radius, start_angle + (end_angle - start_angle) * i / segments)
        for i in range(1, segments + 1)
    ]


def generate_involute_outline(
    tooth_count: int,
    phase_angle_deg: float = 0,
    module_m: float = MODULE_M,
    pressure_angle_deg: float = PRESSURE_ANGLE_DEG,
    flank_segments: int = 8,
    tip_arc_segments: int = 3,
    root_arc_segments: int = 3,
) -> tuple[list[tuple[float, float]], dict[str, float]]:
    """生成闭合齿轮外轮廓；30° 压力角避免 10 齿小齿轮发生根切干涉。"""
    pressure_angle = math.radians(pressure_angle_deg)
    phase = math.radians(phase_angle_deg)
    pitch_radius = module_m * tooth_count / 2
    base_radius = pitch_radius * math.cos(pressure_angle)
    outer_radius = pitch_radius + module_m
    root_radius = pitch_radius - 1.25 * module_m
    tooth_pitch_angle = 2 * math.pi / tooth_count
    half_tooth_angle_at_pitch = math.pi / (2 * tooth_count)
    pitch_involute = math.tan(pressure_angle) - pressure_angle
    base_half_angle = half_tooth_angle_at_pitch + pitch_involute
    outer_half_angle = base_half_angle - involute_angle(
        outer_radius,
        base_radius,
    )
    flank_start_radius = max(root_radius, base_radius)
    root_half_angle = base_half_angle - involute_angle(
        flank_start_radius,
        base_radius,
    )

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

        # 下齿面：max(基圆, 齿根圆) → 齿顶圆。
        for sample in range(1, flank_segments + 1):
            radius = flank_start_radius + (
                outer_radius - flank_start_radius
            ) * sample / flank_segments
            half_angle = base_half_angle - involute_angle(radius, base_radius)
            points.append(polar(radius, center_angle - half_angle))

        # 齿顶圆弧：下齿面 → 上齿面。
        points.extend(
            arc_points(
                outer_radius,
                center_angle - outer_half_angle,
                center_angle + outer_half_angle,
                tip_arc_segments,
            )
        )

        # 上齿面：齿顶圆 → 基圆。
        for sample in range(flank_segments - 1, -1, -1):
            radius = flank_start_radius + (
                outer_radius - flank_start_radius
            ) * sample / flank_segments
            half_angle = base_half_angle - involute_angle(radius, base_radius)
            points.append(polar(radius, center_angle + half_angle))

        upper_root_angle = center_angle + root_half_angle
        if root_radius < base_radius:
            points.append(polar(root_radius, upper_root_angle))

        # 齿根圆弧：当前齿上侧 → 下一齿下侧；1.25m 齿根给齿顶留 0.25m 间隙。
        next_lower_base_angle = (
            center_angle + tooth_pitch_angle - root_half_angle
        )
        points.extend(
            arc_points(
                root_radius,
                upper_root_angle,
                next_lower_base_angle,
                root_arc_segments,
            )
        )

    # 相邻齿共享齿根圆弧端点；移除重复点，避免 SolidWorks 草图中出现零长度线。
    deduplicated: list[tuple[float, float]] = []
    for point in points:
        if not deduplicated or math.dist(deduplicated[-1], point) > 1e-10:
            deduplicated.append(point)

    # 消除 2π 浮点误差，确保 SolidWorks 识别为严格闭合轮廓。
    deduplicated[-1] = deduplicated[0]
    parameters = {
        "module": module_m,
        "pressureAngleDeg": pressure_angle_deg,
        "pitchRadius": pitch_radius,
        "baseRadius": base_radius,
        "outerRadius": outer_radius,
        "rootRadius": root_radius,
    }
    return deduplicated, parameters


def build_and_save_gear(
    tooth_count: int,
    phase_angle_deg: float,
    save_path: str,
) -> dict[str, float]:
    outline, parameters = generate_involute_outline(
        tooth_count=tooth_count,
        phase_angle_deg=phase_angle_deg,
    )
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    for start, end in zip(outline, outline[1:]):
        call(
            "/api/sketch/line",
            {
                "x1": start[0],
                "y1": start[1],
                "x2": end[0],
                "y2": end[1],
            },
        )
    # 中心孔既符合真实齿轮结构，也为 Gear Mate 提供稳定的圆柱选择面。
    call(
        "/api/sketch/circle",
        {"cx": 0, "cy": 0, "radius": BORE_RADIUS_M},
    )
    call("/api/feature/extrusion", {"depth": THICKNESS_M})
    call("/api/save-part-as", {"savePath": save_path})
    return parameters


def main() -> None:
    ping = requests.get(f"{BASE}/api/ping", timeout=5).json()
    print(f"C# 服务: {ping['message']}")
    print(
        f"齿轮参数: module={MODULE_M * 1000:.1f}mm, "
        f"pressure_angle={PRESSURE_ANGLE_DEG:.1f}°, "
        f"thickness={THICKNESS_M * 1000:.1f}mm"
    )

    small_path = os.path.join(SMOKE_DIR, "gear_small_involute_bored.SLDPRT")
    big_path = os.path.join(SMOKE_DIR, "gear_big_involute_bored.SLDPRT")
    small = build_and_save_gear(SMALL_TEETH, 0, small_path)
    big = build_and_save_gear(BIG_TEETH, 9, big_path)

    center_distance = small["pitchRadius"] + big["pitchRadius"]
    print(f"✅ 小齿轮: {small_path}")
    print(f"✅ 大齿轮: {big_path}")
    print(f"✅ 理论中心距: {center_distance * 1000:.3f}mm")


if __name__ == "__main__":
    main()
