"""bench_cases / bench_cases2 零件规格的 OCC 参考实体，几何与 build_standard_parts 的 SolidWorks 草图一致。"""

from __future__ import annotations

import math

from .fixtures import box, cut, cyl, prism
from ._import_src import load_gears

_gears = load_gears()


def _holes(shape, t, holes):
    return cut(shape, *[cyl((x, y, -1), (0, 0, 1), d / 2, t + 2) for x, y, d in holes]) if holes else shape


def _gear(z, m, face, bore):
    outline, _ = _gears.generate_involute_outline(tooth_count=z, module_m=m / 1000, pressure_angle_deg=30)
    body = prism([(x * 1000, y * 1000, 0) for x, y in outline[:-1]], (0, 0, face))
    return _holes(body, face, [(0, 0, bore)])


def _ring(z, m, face, od):
    cavity, _ = _gears.generate_internal_cavity(tooth_count=z, module_m=m / 1000, pressure_angle_deg=30)
    hole = prism([(x * 1000, y * 1000, -1) for x, y in cavity[:-1]], (0, 0, face + 2))
    return cut(cyl((0, 0, 0), (0, 0, 1), od / 2, face), hole)


def _link(L, w, t, holes):
    half = w / 2
    pts = [(L + half * math.cos(-math.pi / 2 + math.pi * i / 12), half * math.sin(-math.pi / 2 + math.pi * i / 12), 0)
           for i in range(13)]
    pts += [(half * math.cos(math.pi / 2 + math.pi * i / 12), half * math.sin(math.pi / 2 + math.pi * i / 12), 0)
            for i in range(13)]
    return _holes(prism(pts, (0, 0, t)), t, [(x, 0, d) for x, d in holes])


def shape(p: dict):
    k = p["kind"]
    if k == "plate":
        return _holes(box(p["x"][0], p["y"][0], 0, p["x"][1], p["y"][1], p["t"]), p["t"], p["holes"])
    if k == "link":
        return _link(p["L"], p["w"], p["t"], p["holes"])
    if k == "pin":
        return cyl((0, 0, 0), (0, 0, 1), p["d"] / 2, p["L"])
    if k == "disk":
        return _holes(cyl((0, 0, 0), (0, 0, 1), p["od"] / 2, p["t"]), p["t"], [(0, 0, p["bore"])] + p["holes"])
    if k == "gear":
        return _gear(p["z"], p["m"], p["face"], p["bore"])
    if k == "ring":
        return _ring(p["z"], p["m"], p["face"], p["od"])
    raise ValueError(k)
