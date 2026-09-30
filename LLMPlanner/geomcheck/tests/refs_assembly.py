"""装配试点零件的 OCC 参考实体。"""

from __future__ import annotations

from .fixtures import box, cut, cyl, prism
from ._import_src import load_gears

generate_involute_outline = load_gears().generate_involute_outline


def housing():
    return cut(
        box(-30, -40, 0, 160, 40, 10),
        cyl((0, 0, -1), (0, 0, 1), 5.2, 12),
        cyl((54, 0, -1), (0, 0, 1), 5.2, 12),
        cyl((126, 0, -1), (0, 0, 1), 5.2, 12),
    )


def housing_two_holes():
    return cut(
        box(-30, -40, 0, 160, 40, 10),
        cyl((0, 0, -1), (0, 0, 1), 5.2, 12),
        cyl((54, 0, -1), (0, 0, 1), 5.2, 12),
    )


def shaft(length=50):
    return cyl((0, 0, 0), (0, 0, 1), 5, length)


def gear(teeth: int, module=3.0, thick=10, bore=10):
    outline, _ = generate_involute_outline(teeth, module_m=module / 1000, pressure_angle_deg=30)
    points = [(x * 1000, y * 1000, 0) for x, y in outline[:-1]]
    return cut(prism(points, (0, 0, thick)), cyl((0, 0, -1), (0, 0, 1), bore / 2, thick + 2))


def link(length, width=16, hole=10, thick=6):
    import math
    half = width / 2
    pts = []
    for i in range(13):
        a = -math.pi / 2 + math.pi * i / 12
        pts.append((length + half * math.cos(a), half * math.sin(a), 0))
    for i in range(13):
        a = math.pi / 2 + math.pi * i / 12
        pts.append((half * math.cos(a), half * math.sin(a), 0))
    return cut(
        prism(pts, (0, 0, thick)),
        cyl((0, 0, -1), (0, 0, 1), hole / 2, thick + 2),
        cyl((length, 0, -1), (0, 0, 1), hole / 2, thick + 2),
    )
