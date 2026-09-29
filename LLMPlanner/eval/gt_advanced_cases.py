"""Analytic GT for advanced-tool demo / heldout3 cases.

Run: python eval/gt_advanced_cases.py
"""

from __future__ import annotations

import math

from shapely.geometry import Point


def cyl(r: float, h: float) -> float:
    return math.pi * r * r * h


def gusset_bracket():
    # L: base 60×40×6 + upright 6×40×30; rib ≈ triangle 18×18 × thick 4 / 2
    plate = 60 * 40 * 6 + 6 * 40 * 30
    rib = 0.5 * 18 * 18 * 4
    return plate + rib, (36.0, 40.0, 60.0)


def corner_rib_stand():
    plate = 80 * 50 * 8 + 8 * 50 * 36
    rib = 0.5 * 24 * 24 * 5
    return plate + rib, (44.0, 50.0, 80.0)


def flanged_elbow():
    # solid sweep Ø16 along 90° R40 + two flanges Ø36×4 (minus tube overlap)
    arc = 40 * math.pi / 2
    tube = cyl(8, arc)
    flange = 2 * (cyl(18, 4) - cyl(8, 4))
    return tube + flange, (58.0, 58.0, 36.0)


def bent_tube():
    arc = 50 * math.pi / 2
    tube = cyl(10, arc)
    flange = 2 * (cyl(22, 5) - cyl(10, 5))
    return tube + flange, (72.0, 72.0, 44.0)


def open_box():
    return 80 * 50 * 40 - 72 * 42 * 36, (40.0, 50.0, 80.0)


def lidless_housing():
    return 100 * 60 * 50 - 90 * 50 * 44, (50.0, 60.0, 100.0)


def counterbore_plate():
    plate = 80 * 60 * 10
    one = cyl(3, 6.5) + cyl(5.5, 3.5)
    return plate - 4 * one, (10.0, 60.0, 80.0)


def csk_cover():
    plate = 90 * 70 * 12
    one = cyl(4, 8) + cyl(7, 4)
    return plate - 4 * one, (12.0, 70.0, 90.0)


def side_pad():
    return 50 * 30 * 40 + cyl(8, 20), (30.0, 40.0, 70.0)


def through_boss():
    return 60 * 36 * 48 + cyl(10, 24), (36.0, 48.0, 84.0)


def sheet_angle():
    return 70 * 40 * 1.5, (1.5, 40.0, 70.0)


def sheet_channel():
    return 90 * 50 * 1.2, (1.2, 50.0, 90.0)


def square_to_round():
    return 0.5 * (30 * 30 + math.pi * 12 * 12) * 40, (30.0, 30.0, 40.0)


def rect_to_circle():
    return 0.5 * (40 * 24 + math.pi * 14 * 14) * 50, (24.0, 40.0, 50.0)


CASES = {
    "gusset_bracket": gusset_bracket,
    "corner_rib_stand": corner_rib_stand,
    "flanged_elbow": flanged_elbow,
    "bent_tube": bent_tube,
    "open_box": open_box,
    "lidless_housing": lidless_housing,
    "counterbore_plate": counterbore_plate,
    "csk_cover": csk_cover,
    "side_pad": side_pad,
    "through_boss": through_boss,
    "sheet_angle": sheet_angle,
    "sheet_channel": sheet_channel,
    "square_to_round": square_to_round,
    "rect_to_circle": rect_to_circle,
}


if __name__ == "__main__":
    for name, fn in CASES.items():
        volume, dims = fn()
        print(f"{name:18s} V={volume:10.1f}  bbox={tuple(round(d, 3) for d in sorted(dims))}")
