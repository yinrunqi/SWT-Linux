"""Analytic GT for the complex demo / heldout2 Text2CAD cases.

Volumes are computed from exact planar regions (shapely, 256 segments per
quarter circle) times prism length, with inclusion-exclusion for bores.
Run: python eval/gt_complex_cases.py
"""

from __future__ import annotations

import math

from shapely.geometry import Point, box
from shapely.ops import unary_union

Q = 256


def disk(x: float, y: float, r: float):
    return Point(x, y).buffer(r, quad_segs=Q)


def hull(*shapes):
    return unary_union(shapes).convex_hull


def lug(r_center: float, width: float, angle_deg: float):
    """Straight lug of given width from the origin out to a semicircle end."""
    a = math.radians(angle_deg)
    end = (r_center * math.cos(a), r_center * math.sin(a))
    return hull(disk(0, 0, width / 2), disk(*end, width / 2))


def cyl(r: float, h: float) -> float:
    return math.pi * r * r * h


def fork_end():
    # Shank Ø20 x 35 (Y 0..35); root 40(X) x 24(Z) x 10 (Y 35..45);
    # two lugs 8 thick in X, profile in YZ = rect 24 x 18 + R12 half-disk; pin Ø10 along X.
    lug_profile = unary_union([box(-12, 45, 12, 63), disk(0, 63, 12)])
    v = cyl(10, 35) + 40 * 24 * 10 + 2 * 8 * lug_profile.area
    v -= 2 * 8 * cyl(5, 1)  # pin hole through both lugs
    v -= cyl(3.4, 20)  # bottom blind hole Ø6.8 x 20
    return v, (40.0, 75.0, 24.0)


def clevis_bracket():
    # Plate 80(X) x 50(Z) x 10(Y), 4-Ø9 at (±30, ±17); ears X∈[15,25] mirrored,
    # ear profile in YZ = rect 30 x 25 (Y 10..35) + R15 half-disk; pin Ø12 along X.
    ear = unary_union([box(-15, 10, 15, 35), disk(0, 35, 15)])
    v = 80 * 50 * 10 - 4 * cyl(4.5, 10) + 2 * 10 * ear.area - 2 * 10 * cyl(6, 1)
    return v, (80.0, 50.0, 50.0)


def three_lug_housing():
    # Flange t=10: disk R32 ∪ 3 lugs (width 22, end centre radius 48, at 0/120/240°),
    # 3-Ø9 at lug centres; hub Ø46 (Y 10..38); bore Ø30 through; top seat Ø38 x 9.
    flange = unary_union([disk(0, 0, 32)] + [lug(48, 22, a) for a in (0, 120, 240)])
    v = flange.area * 10 - 3 * cyl(4.5, 10)
    v += cyl(23, 28)
    v -= cyl(15, 38 - 9) + cyl(19, 9)
    minx, miny, maxx, maxy = flange.bounds
    return v, (maxx - minx, 38.0, maxy - miny)


def diamond_flange():
    # Plate t=8: hull(R25 at 0, R12 at (±42,0)); 2-Ø10 at (±42,0);
    # boss Ø34 (Y 8..26); bore Ø20 through.
    plate = hull(disk(0, 0, 25), disk(-42, 0, 12), disk(42, 0, 12))
    v = plate.area * 8 - 2 * cyl(5, 8) + cyl(17, 18) - cyl(10, 26)
    return v, (108.0, 26.0, 50.0)


def pillow_block():
    # Base 120(X) x 40(Z) x 12(Y); body front profile rect 60 wide Y 12..45 + R30 half-disk,
    # full 40 deep; bore Ø35 at (0,45) along Z; two X-slots width 11, centre distance 10, at x=±45.
    body = unary_union([box(-30, 12, 30, 45), disk(0, 45, 30)])
    slot = hull(disk(-5, 0, 5.5), disk(5, 0, 5.5))
    v = 120 * 40 * 12 + body.area * 40 - cyl(17.5, 40) - 2 * slot.area * 12
    return v, (120.0, 75.0, 40.0)


def support_seat():
    # Base 100(X) x 50(Z) x 10(Y); body 20 deep (Z) centred, profile rect 50 wide Y 10..40
    # + R25 half-disk; bore Ø24 at (0,40) along Z; 2-Ø10 at (±38, 0).
    body = unary_union([box(-25, 10, 25, 40), disk(0, 40, 25)])
    v = 100 * 50 * 10 + body.area * 20 - cyl(12, 20) - 2 * cyl(5, 10)
    return v, (100.0, 65.0, 50.0)


def twin_arm_rocker():
    # Hub Ø30 along Z (Z −20..20); arms t=8 at Z∈[12,20] and [−20,−12],
    # profile hull(R15 at 0, R10 at (70,0)) in XY; bore Ø12 through hub; pin Ø8 at (70,0).
    arm = hull(disk(0, 0, 15), disk(70, 0, 10))
    outside_hub = arm.difference(disk(0, 0, 15)).area
    v = cyl(15, 40) + 2 * 8 * outside_hub - cyl(6, 40) - 2 * cyl(4, 8)
    return v, (95.0, 30.0, 40.0)


def crank():
    # Hub Ø36 along Z (Z −12..12); arm t=10 at Z∈[2,12], profile hull(R18 at 0, R11 at (60,0));
    # pin boss Ø22 on the arm top (Z 12..27) at (60,0); pin hole Ø10 through boss+arm; bore Ø16.
    arm = hull(disk(0, 0, 18), disk(60, 0, 11))
    outside_hub = arm.difference(disk(0, 0, 18)).area
    v = cyl(18, 24) + 10 * outside_hub + cyl(11, 15) - cyl(8, 24) - cyl(5, 25)
    return v, (89.0, 36.0, 39.0)


CASES = {
    "fork_end": fork_end,
    "clevis_bracket": clevis_bracket,
    "three_lug_housing": three_lug_housing,
    "diamond_flange": diamond_flange,
    "pillow_block": pillow_block,
    "support_seat": support_seat,
    "twin_arm_rocker": twin_arm_rocker,
    "crank": crank,
}


if __name__ == "__main__":
    for name, fn in CASES.items():
        volume, dims = fn()
        print(f"{name:18s} V={volume:10.1f}  bbox(sorted)={tuple(round(d, 3) for d in sorted(dims))}")
