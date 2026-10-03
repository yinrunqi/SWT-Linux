"""用 L1 工具（与 LLM 拿到的工具完全相同）逐题搭出 rubrics_hard 的参考件，证明每道难题在现有工具集下可解。

    python eval/hard_l1_reference.py [case ...]          # 建模并导出 eval/artifacts/hard_l1_reference/<case>_r1.step
    venv-occ/Scripts/python.exe -m geomcheck.rubric --artifacts eval/artifacts/hard_l1_reference --rubric-dir eval/rubrics_hard

每题的解法只用题面给出的尺寸；方位上的自由度（题面只约束相对方位时）按方便建模的方式选取。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from planner.workflow.tools import WorkflowSession  # noqa: E402

SW = "http://localhost:5100"
OUT = ROOT / "eval" / "artifacts" / "hard_l1_reference"


def polar(r: float, deg: float, cx: float = 0.0, cy: float = 0.0) -> list[float]:
    a = math.radians(deg)
    return [round(cx + r * math.cos(a), 6), round(cy + r * math.sin(a), 6)]


def circle(center, d) -> dict:
    return {"type": "circle", "center": list(center), "diameter": d}


def ring(center, d_out, d_in) -> list[dict]:
    return [circle(center, d_out), circle(center, d_in)]


def rect(center, size, **extra) -> dict:
    return {"type": "rect", "center": list(center), "size": list(size), **extra}


def poly(points, **extra) -> dict:
    return {"type": "polygon", "points": [list(p) for p in points], **extra}


def _arc_mid(center, radius, p0, p1, ccw: bool) -> list[float]:
    a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
    a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    return polar(radius, math.degrees(a0 + sweep / 2), *center)


def hull_of_circles(c1, r1, c2, r2) -> dict:
    """两圆及其两条外公切线围成的长圆形（c1 → c2 的方向任意）。"""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    dist = math.hypot(dx, dy)
    base = math.atan2(dy, dx)
    alpha = math.acos((r1 - r2) / dist)
    up, down = math.degrees(base + alpha), math.degrees(base - alpha)
    a1, a2 = polar(r1, up, *c1), polar(r2, up, *c2)
    b2, b1 = polar(r2, down, *c2), polar(r1, down, *c1)
    return {"type": "path", "start": a1, "segments": [
        {"to": a2},
        {"to": b2, "through": polar(r2, math.degrees(base), *c2)},
        {"to": b1},
        {"to": a1, "through": polar(r1, math.degrees(base) + 180, *c1)},
    ]}


def parallel_vane(width: float, r_in: float, r_out: float, deg: float = 0.0) -> dict:
    """两侧平行、内外端为同心圆弧的径向板条。"""
    h = width / 2
    xi, xo = math.sqrt(r_in ** 2 - h ** 2), math.sqrt(r_out ** 2 - h ** 2)
    rot = lambda p: polar(math.hypot(*p), math.degrees(math.atan2(p[1], p[0])) + deg)  # noqa: E731
    return {"type": "path", "start": rot((xi, -h)), "segments": [
        {"to": rot((xo, -h))},
        {"to": rot((xo, h)), "through": polar(r_out, deg)},
        {"to": rot((xi, h))},
        {"to": rot((xi, -h)), "through": polar(r_in, deg)},
    ]}


def filleted_sector(r_in: float, r_out: float, start: float, end: float, radius: float) -> dict:
    """四角倒圆的扇环（窗口）。"""
    do = math.degrees(math.asin(radius / (r_out - radius)))
    di = math.degrees(math.asin(radius / (r_in + radius)))
    co, ci = r_out - radius, r_in + radius
    ro, ri = co * math.cos(math.radians(do)), ci * math.cos(math.radians(di))
    p = {
        "s_in": polar(ri, start), "s_out": polar(ro, start),
        "o_s": polar(r_out, start + do), "o_e": polar(r_out, end - do),
        "e_out": polar(ro, end), "e_in": polar(ri, end),
        "i_e": polar(r_in, end - di), "i_s": polar(r_in, start + di),
    }
    c = {
        "so": polar(co, start + do), "eo": polar(co, end - do),
        "ei": polar(ci, end - di), "si": polar(ci, start + di),
    }
    return {"type": "path", "start": p["s_in"], "segments": [
        {"to": p["s_out"]},
        {"to": p["o_s"], "through": _arc_mid(c["so"], radius, p["s_out"], p["o_s"], True)},
        {"to": p["o_e"], "through": polar(r_out, (start + end) / 2)},
        {"to": p["e_out"], "through": _arc_mid(c["eo"], radius, p["o_e"], p["e_out"], True)},
        {"to": p["e_in"]},
        {"to": p["i_e"], "through": _arc_mid(c["ei"], radius, p["e_in"], p["i_e"], True)},
        {"to": p["i_s"], "through": polar(r_in, (start + end) / 2)},
        {"to": p["s_in"], "through": _arc_mid(c["si"], radius, p["i_s"], p["s_in"], True)},
    ]}


class Builder:
    """按顺序调用 L1 工具；ref(...) 按需新建并缓存平行基准面。"""

    BASE = {"Z": "front_plane", "Y": "top_plane", "X": "right_plane"}

    def __init__(self, session: WorkflowSession):
        self.session = session
        self.planes: dict[tuple[str, float], str] = {}
        self.failures: list[str] = []

    def ref(self, axis: str, offset: float) -> str:
        if abs(offset) < 1e-9:
            return self.BASE[axis]
        key = (axis, round(offset, 6))
        if key not in self.planes:
            out = self.call("ref_plane", {"reference": self.BASE[axis], "offset": offset})
            self.planes[key] = out["name"]
        return self.planes[key]

    def call(self, tool: str, args: dict) -> dict:
        out = self.session.call(tool, args)
        mark = "ok " if out.get("ok") else "ERR"
        detail = "" if out.get("ok") else (out.get("error") or {}).get("message", "")
        print(f"  {mark} {tool} {json.dumps(args, ensure_ascii=False)[:110]} {detail[:200]}", flush=True)
        if not out.get("ok"):
            self.failures.append(f"{tool}: {detail}")
        return out

    def __getattr__(self, tool: str):
        return lambda **args: self.call(tool, args)


# ----------------------------------------------------------------------------- 各题解法


def brake_disc(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=ring((0, 0), 300, 180), depth=10)
    b.extrude(plane=b.ref("Z", 22), loops=ring((0, 0), 300, 180), depth=10)
    b.extrude(plane=b.ref("Z", 22), loops=ring((0, 0), 190, 170), depth=48)
    b.extrude(plane=b.ref("Z", 62), loops=ring((0, 0), 190, 70), depth=8)
    b.extrude(plane=b.ref("Z", 10), loops=[parallel_vane(5, 90, 150)], depth=12)
    b.circular_pattern(count=40, axis="Z", center=[0, 0, 0], angle_deg=360)
    b.hole(plane=b.ref("Z", 62), centers=[polar(57.15, 72 * k) for k in range(5)], diameter=14,
           direction="+Z", depth=8)
    b.hole(plane="front_plane", centers=[polar(120, 4.5 + 18 * k) for k in range(20)], diameter=8,
           direction="+Z", depth=32)


def bt40_holder(b: Builder) -> None:
    half = 3.5 * math.tan(math.radians(30))
    r_small = (44.45 - 65.4 * 7 / 24) / 2
    profile = [
        (-60, 10), (-60, 19), (-59, 20), (-15.9, 20), (-15.9, 31.5), (-8 - half, 31.5), (-8, 28),
        (-8 + half, 31.5), (0, 31.5), (0, 22.225), (65.4, r_small), (65.4, 7), (35.4, 7), (35.4, 3),
        (-10, 3), (-10, 10),
    ]
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-100, 0], axis_end=[100, 0])
    for sign in (1, -1):
        b.cut(plane="front_plane", loops=[rect((sign * 28.25, 0), (9.5, 16.1))], direction="-Z", depth=15.9)
    b.hole(plane=b.ref("Z", -15.9), centers=[[0, 25]], diameter=8, direction="+Z", depth=6)


def cold_plate(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (170, 100))], depth=12)
    top = b.ref("Z", 12)
    for y in (-36, -18, 0, 18, 36):
        b.cut(plane=top, loops=[{"type": "slot", "start": [-55, y], "end": [55, y], "width": 10}],
              direction="-Z", depth=7)
    for (cx, cy), (s, e) in (((55, -27), (-90, 90)), ((-55, -9), (90, 270)), ((55, 9), (-90, 90)),
                             ((-55, 27), (90, 270))):
        b.cut(plane=top, loops=[{"type": "annular_sector", "center": [cx, cy], "r_inner": 4, "r_outer": 14,
                                 "start_deg": s, "end_deg": e}], direction="-Z", depth=7)
    b.hole(plane=b.ref("X", -85), centers=[[8, -36]], diameter=6, direction="+X", depth=30)
    b.hole(plane=b.ref("X", 85), centers=[[8, 36]], diameter=5, direction="-X", depth=30)
    b.cut(plane=top, loops=[rect((0, 0), (149, 93), corner_radius=9.5), rect((0, 0), (143, 87), corner_radius=6.5)],
          direction="-Z", depth=2)
    b.hole(plane="front_plane", centers=[[x, y] for x in (-79, 79) for y in (-40, 0, 40)], diameter=5.5,
           through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=4, direction="+Z", depth=4)


def crankshaft(b: Builder) -> None:
    def arm(z0: float, pin_dir: int) -> None:
        plane = b.ref("Z", z0)
        b.extrude(plane=plane, loops=[hull_of_circles((0, 0), 32, (0, 38 * pin_dir), 26)], depth=15)
        mid = -90 * pin_dir
        b.extrude(plane=plane, loops=[{"type": "annular_sector", "center": [0, 0], "r_inner": 0, "r_outer": 70,
                                       "start_deg": mid - 60, "end_deg": mid + 60}], depth=15)

    b.extrude(plane="front_plane", loops=[circle((0, 0), 100)], depth=15)
    b.extrude(plane=b.ref("Z", 15), loops=[circle((0, 0), 50)], depth=30)
    arm(45, 1)
    b.extrude(plane=b.ref("Z", 60), loops=[circle((0, 38), 40)], depth=40)
    arm(100, 1)
    b.extrude(plane=b.ref("Z", 115), loops=[circle((0, 0), 50)], depth=30)
    arm(145, -1)
    b.extrude(plane=b.ref("Z", 160), loops=[circle((0, -38), 40)], depth=40)
    arm(200, -1)
    b.extrude(plane=b.ref("Z", 215), loops=[circle((0, 0), 50)], depth=30)
    b.extrude(plane=b.ref("Z", 245), loops=[circle((0, 0), 30)], depth=50)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=20, direction="+Z", depth=12)
    b.hole(plane="front_plane", centers=[[0, 25]], diameter=8, direction="+Z", depth=10)
    b.hole(plane="front_plane", centers=[polar(38, 120 + 60 * k) for k in range(6)], diameter=9,
           direction="+Z", depth=15)
    b.hole(plane="right_plane", centers=[[80, 38], [180, -38]], diameter=6, through_all=True, direction="both")
    b.cut(plane=b.ref("Z", 295), loops=[rect((0, -14), (8, 6))], direction="-Z", depth=40)


def engine_piston(b: Builder) -> None:
    profile = [(0, 0), (0, 42), (-1, 43), (-7, 43), (-7, 39), (-9, 39), (-9, 43), (-13, 43), (-13, 39),
               (-15, 39), (-15, 43), (-19, 43), (-19, 39), (-23, 39), (-23, 43), (-70, 43), (-70, 37),
               (-12, 37), (-12, 0)]
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-100, 0], axis_end=[100, 0])
    b.extrude(plane=b.ref("X", 22), loops=[circle((-40, 0), 30)], direction="+X", depth=16)
    b.extrude(plane=b.ref("X", -22), loops=[circle((-40, 0), 30)], direction="-X", depth=16)
    b.hole(plane="right_plane", centers=[[-40, 0]], diameter=22, through_all=True, direction="both")
    b.hole(plane="front_plane", centers=[[0, 18], [0, -18]], diameter=30, direction="-Z", depth=2)
    b.hole(plane=b.ref("X", 36), centers=[[-21, 0]], diameter=3, direction="+X", depth=4)
    b.circular_pattern(count=8, axis="Z", center=[0, 0, 0], angle_deg=360)


def gear_pump_body(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (120, 90), corner_radius=12)], depth=40)
    top = b.ref("Z", 40)
    b.cut(plane=top, loops=[circle((-21, 0), 50.5)], direction="-Z", depth=30)
    b.cut(plane=top, loops=[circle((21, 0), 50.5)], direction="-Z", depth=30)
    b.hole(plane="front_plane", centers=[[-21, 0]], diameter=20, direction="+Z", depth=10)
    b.hole(plane=b.ref("Z", 10), centers=[[21, 0]], diameter=15, direction="-Z", depth=8)
    b.extrude(plane=b.ref("Y", -45), loops=[circle((0, 25), 36)], direction="-Y", depth=8)
    b.extrude(plane=b.ref("Y", 45), loops=[circle((0, 25), 36)], direction="+Y", depth=8)
    b.hole(plane=b.ref("Y", -53), centers=[[0, 25]], diameter=24, direction="+Y", depth=45)
    b.hole(plane=b.ref("Y", 53), centers=[[0, 25]], diameter=16, direction="-Y", depth=45)
    b.hole(plane=top, centers=[[x, y] for x in (-46, 46) for y in (-32, 32)], diameter=6.8, direction="-Z", depth=20)
    b.hole(plane=top, centers=[[-30, 33], [30, -33]], diameter=6, direction="-Z", depth=12)


def gearbox_housing(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (200, 120))], depth=100)
    b.cut(plane=b.ref("Z", 100), loops=[rect((0, 0), (184, 104))], direction="-Z", depth=90)
    b.extrude(plane=b.ref("Z", 90), loops=[rect((0, 0), (240, 160)), rect((0, 0), (184, 104))], depth=10)
    for sign in (1, -1):
        wall = b.ref("Y", 60 * sign)
        direction = "+Y" if sign > 0 else "-Y"
        b.extrude(plane=wall, loops=[circle((-45, 50), 70), circle((50, 50), 70)], direction=direction, depth=15)
        b.extrude(plane=wall, loops=[rect((-45, 12.5), (8, 25)), rect((50, 12.5), (8, 25))],
                  direction=direction, depth=15)
    b.hole(plane="top_plane", centers=[[-45, 50]], diameter=47, through_all=True, direction="both")
    b.hole(plane="top_plane", centers=[[50, 50]], diameter=52, through_all=True, direction="both")
    b.extrude(plane="front_plane", loops=[rect((115, 0), (30, 120)), rect((-115, 0), (30, 120))], depth=12)
    b.hole(plane="front_plane", centers=[[x, y] for x in (-115, 115) for y in (-40, 40)], diameter=13,
           direction="+Z", depth=12)
    b.hole(plane=b.ref("Z", 100), centers=[[x, y] for x in (-110, 0, 110) for y in (-70, 70)], diameter=11,
           direction="-Z", depth=10)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=10.2, direction="+Z", depth=10)
    b.hole(plane=b.ref("X", 100), centers=[[40, 0]], diameter=14, direction="-X", depth=8)


def geneva_wheel(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[circle((0, 0), 140)], depth=12)
    b.extrude(plane=b.ref("Z", 12), loops=[circle((0, 0), 40)], depth=12)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=16, through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[{"type": "slot", "start": polar(40, 60 * k), "end": polar(80, 60 * k), "width": 10}
                                      for k in range(6)], direction="+Z", depth=12)
    b.cut(plane="front_plane", loops=[circle(polar(80, 30 + 60 * k), 64) for k in range(6)], direction="+Z", depth=12)
    b.hole(plane="front_plane", centers=[polar(28, 30 + 60 * k) for k in range(6)], diameter=8,
           direction="+Z", depth=12)
    b.hole(plane="right_plane", centers=[[18, 0]], diameter=5, direction="+X", depth=21)


def heat_sink(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (100, 100))], depth=8)
    b.extrude(plane="front_plane", loops=[rect((0, 0), (60, 60))], direction="-Z", depth=2)
    grid = [-40 + 10 * i for i in range(9)]
    corners = [[x, y] for x in (-40, 40) for y in (-40, 40)]
    pins = [circle((x, y), 6) for x in grid for y in grid if [x, y] not in corners]
    b.extrude(plane=b.ref("Z", 8), loops=pins, depth=40)
    b.counterbore(plane=b.ref("Z", 8), centers=corners, through_diameter=4.5, head_diameter=8, head_depth=4,
                  direction="-Z", depth=8)


def honeycomb_panel(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (200, 120), corner_radius=10)], depth=10)
    rows = [(0, [0, 19, -19, 38, -38, 57, -57, 76, -76])]
    rows += [(y, [9.5, -9.5, 28.5, -28.5, 47.5, -47.5, 66.5, -66.5]) for y in (16.454, -16.454)]
    rows += [(y, [0, 19, -19, 38, -38, 57, -57, 76, -76]) for y in (32.909, -32.909)]
    hexes = [{"type": "regular_polygon", "center": [x, y], "sides": 6, "across_flats": 16, "angle_deg": 90}
             for y, xs in rows for x in xs]
    b.cut(plane=b.ref("Z", 10), loops=hexes, direction="-Z", depth=7)
    b.hole(plane="front_plane", centers=[[x, y] for x in (-90, 90) for y in (-50, 50)], diameter=8.5,
           direction="+Z", depth=10)


def hydraulic_manifold(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((0, 0), (100, 80))], depth=60)
    top = b.ref("Z", 60)
    b.counterbore(plane=top, centers=[[-25, 0]], through_diameter=12, head_diameter=20, head_depth=3,
                  direction="-Z", depth=40)
    b.counterbore(plane=b.ref("X", -50), centers=[[20, 0]], through_diameter=10, head_diameter=16, head_depth=8,
                  direction="+X", depth=70)
    b.hole(plane=b.ref("Y", 40), centers=[[10, 20]], diameter=10, direction="-Y", depth=40)
    b.hole(plane="front_plane", centers=[[25, -15]], diameter=14, direction="+Z", depth=45)
    b.hole(plane=b.ref("X", 50), centers=[[35, -15]], diameter=10, direction="-X", depth=30)
    b.hole(plane="front_plane", centers=[[x, y] for x in (-40, 40) for y in (-30, 30)], diameter=9,
           direction="+Z", depth=60)
    b.hole(plane=top, centers=[[-25 + dx, dy] for dx in (-16, 16) for dy in (-16, 16)], diameter=4.2,
           direction="-Z", depth=10)


def injector_plate(b: Builder) -> None:
    profile = [(0, 3), (0, 17), (8, 17), (8, 23), (0, 23), (0, 32), (8, 32), (8, 38), (0, 38), (0, 47), (8, 47),
               (8, 53), (0, 53), (0, 62), (8, 62), (8, 68), (0, 68), (0, 80), (19, 80), (20, 79), (20, 3)]
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-50, 0], axis_end=[50, 0])
    rings = [(20, 8, 0.0, 2), (35, 16, 11.25, 2), (50, 24, 0.0, 2), (65, 32, 5.625, 1.5)]
    loops = [circle(polar(r, start + 360 / n * k), d) for r, n, start, d in rings for k in range(n)]
    b.cut(plane=b.ref("Z", 8), loops=loops, direction="+Z", depth=12)
    b.hole(plane=b.ref("Z", 20), centers=[[57.5, 0]], diameter=4, direction="-Z", depth=6)
    b.hole(plane="front_plane", centers=[polar(72.5, 15 + 30 * k) for k in range(12)], diameter=8.5,
           direction="+Z", depth=20)


def menger_sponge(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((40.5, 40.5), (81, 81))], depth=81)
    squares = [rect((40.5, 40.5), (27, 27))]
    squares += [rect((27 * i + 13.5, 27 * j + 13.5), (9, 9)) for i in range(3) for j in range(3) if (i, j) != (1, 1)]
    for plane, direction in (("front_plane", "+Z"), ("top_plane", "+Y"), ("right_plane", "+X")):
        b.cut(plane=plane, loops=squares, direction=direction, through_all=True)


def pipe_manifold(b: Builder) -> None:
    b.extrude(plane="right_plane", loops=[circle((0, 0), 60)], direction="+X", depth=300)
    b.extrude(plane="right_plane", loops=[circle((0, 0), 140)], direction="+X", depth=16)
    b.extrude(plane=b.ref("X", 284), loops=[circle((0, 0), 140)], direction="+X", depth=16)
    b.hole(plane="right_plane", centers=[polar(55, 22.5 + 45 * k) for k in range(8)], diameter=14,
           through_all=True, direction="+X")
    xs = (75, 150, 225)
    b.extrude(plane="front_plane", loops=[circle((x, 0), 40) for x in xs], direction="+Z", depth=120)
    for x in xs:
        b.extrude(plane=b.ref("Z", 108), loops=[circle((x, 0), 90)], depth=12)
    b.hole(plane=b.ref("Z", 120), centers=[polar(35, 45 + 90 * k, x, 0) for x in xs for k in range(4)],
           diameter=11, direction="-Z", depth=12)
    b.extrude(plane="front_plane", loops=[circle((150, 0), 30)], direction="-Z", depth=50)
    b.hole(plane=b.ref("Z", 120), centers=[[x, 0] for x in xs], diameter=30, direction="-Z", depth=120)
    b.hole(plane=b.ref("Z", -50), centers=[[150, 0]], diameter=12, direction="+Z", depth=50)
    b.hole(plane="right_plane", centers=[[0, 0]], diameter=48, through_all=True, direction="+X")


def ratchet_wheel(b: Builder) -> None:
    points = []
    for k in range(24):
        points += [polar(50, 15 * k), polar(60, 15 * k)]
    b.extrude(plane="front_plane", loops=[poly(points)], depth=15)
    b.extrude(plane=b.ref("Z", 15), loops=[circle((0, 0), 50)], depth=10)
    b.cut(plane="front_plane", loops=[{"type": "regular_polygon", "center": [0, 0], "sides": 6, "across_flats": 24,
                                       "angle_deg": 30}], through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[polar(33, 60 * k) for k in range(6)], diameter=14, direction="+Z", depth=15)


def robot_arm_link(b: Builder) -> None:
    b.extrude(plane=b.ref("Z", 5), loops=[hull_of_circles((0, 0), 40, (200, 0), 28)], depth=30)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 80)], depth=40)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=52, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[200, 0]], diameter=35, through_all=True, direction="+Z")
    pocket = hull_of_circles((70, 0), 22, (150, 0), 14)
    b.cut(plane=b.ref("Z", 35), loops=[pocket], direction="-Z", depth=10)
    b.cut(plane=b.ref("Z", 5), loops=[pocket], direction="+Z", depth=10)
    b.hole(plane=b.ref("Z", 15), centers=[[110, 0]], diameter=16, direction="+Z", depth=10)
    b.hole(plane=b.ref("Z", 40), centers=[polar(33, 30 + 60 * k) for k in range(6)], diameter=5,
           direction="-Z", depth=12)
    b.cut(plane="front_plane", loops=[rect((215, 0), (30, 2))], through_all=True, direction="+Z")
    b.hole(plane="top_plane", centers=[[219, 20]], diameter=6.6, through_all=True, direction="both")


def rocket_fin_can(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=ring((0, 0), 80, 72), depth=300)
    b.extrude(plane="front_plane", loops=ring((0, 0), 72, 60), depth=8)
    nozzle = [(0, 30), (0, 36), (-30, 16), (-100, 38), (-100, 34), (-30, 12)]
    b.revolve(plane="right_plane", loops=[poly(nozzle)], axis_start=[-150, 0], axis_end=[150, 0])
    # 尾翼平面在 45° 方位，没有斜基准面：先在 0° 做一片、阵列成 8 片，再切掉 0°/90°/180°/270° 的 4 片
    b.extrude(plane="top_plane", loops=[poly([(37, 114), (100, 30), (100, -20), (37, 11.5)])], direction="both",
              depth=4)
    b.circular_pattern(count=8, axis="Z", center=[0, 0, 0], angle_deg=360)
    h = 3.0
    x0 = math.sqrt(40 ** 2 - h ** 2)
    wedges = []
    for deg in (0, 90, 180, 270):
        rot = lambda p, d=deg: polar(math.hypot(*p), math.degrees(math.atan2(p[1], p[0])) + d)  # noqa: E731
        wedges.append({"type": "path", "start": rot((x0, -h)), "segments": [
            {"to": rot((102, -h))}, {"to": rot((102, h))}, {"to": rot((x0, h))},
            {"to": rot((x0, -h)), "through": polar(40, deg)},
        ]})
    b.cut(plane="front_plane", loops=wedges, through_all=True, direction="both")
    b.hole(plane="right_plane", centers=[[250, 0]], diameter=6, through_all=True, direction="both")
    b.hole(plane="top_plane", centers=[[0, 250]], diameter=6, through_all=True, direction="both")


def spiral_staircase(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[circle((0, 0), 300)], depth=10)
    column = {"type": "path", "start": [0, 0], "segments": [
        {"to": [0, 30]}, {"to": [280, 30]}, {"to": [310, 0], "through": [280 + 30 * math.cos(math.pi / 4), 30 * math.sin(math.pi / 4)]},
    ]}
    b.revolve(plane="right_plane", loops=[column], axis_start=[-50, 0], axis_end=[400, 0])
    for k in range(1, 13):
        plane = b.ref("Z", 22 + 20 * (k - 1))
        b.extrude(plane=plane, loops=[{"type": "annular_sector", "center": [0, 0], "r_inner": 0, "r_outer": 140,
                                       "start_deg": 30 * (k - 1), "end_deg": 30 * k}], depth=8)
        b.extrude(plane=plane, loops=[circle(polar(130, 30 * (k - 1) + 15), 8)], depth=98)
    b.hole(plane="front_plane", centers=[[100, 0]], diameter=20, direction="+Z", depth=10)
    b.hole(plane="front_plane", centers=[polar(130, 45 + 90 * k) for k in range(4)], diameter=12,
           direction="+Z", depth=10)


def truss_beam(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[rect((210, 30), (420, 60))], depth=8)
    b.extrude(plane="front_plane", loops=[circle((0, 30), 60), circle((420, 30), 60)], depth=8)
    b.hole(plane="front_plane", centers=[[0, 30]], diameter=20, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[420, 30]], diameter=16, through_all=True, direction="+Z")
    b.extrude(plane=b.ref("Z", 8), loops=[rect((210, 4), (420, 8)), rect((210, 56), (420, 8))], depth=8)
    tris = [poly([(x, 10), (x + 60, 10), (x + 30, 50)], corner_radius=4) for x in (20, 100, 180, 260, 340)]
    tris += [poly([(x + 40, 50), (x + 100, 50), (x + 70, 10)], corner_radius=4) for x in (20, 100, 180, 260)]
    b.cut(plane="front_plane", loops=tris, direction="+Z", depth=8)


def turbine_disk(b: Builder) -> None:
    pts = [(0, 21), (0, 45), (15, 45), (15, 100), (5, 100), (5, 130), (35, 130), (35, 100), (25, 100), (25, 45),
           (40, 45), (40, 21), (39, 20), (1, 20)]
    radii = [0, 0, 5, 5, 0, 0, 0, 0, 5, 5, 0, 0, 0, 0]
    b.revolve(plane="right_plane", loops=[poly(pts, corner_radii=radii)], axis_start=[-50, 0], axis_end=[100, 0])
    b.hole(plane="front_plane", centers=[polar(70, 6 + 36 * k) for k in range(10)], diameter=10,
           through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[poly([(112, -8), (112, 8), (136, 4), (136, -4)])], through_all=True,
          direction="+Z")
    b.circular_pattern(count=30, axis="Z", center=[0, 0, 0], angle_deg=360)


def u_joint_yoke(b: Builder) -> None:
    b.extrude(plane="front_plane", loops=[circle((0, 0), 50)], depth=45)
    b.extrude(plane=b.ref("Z", 45), loops=[circle((0, 0), 80)], depth=10)
    arm = {"type": "path", "start": [45, -20], "segments": [
        {"to": [90, -20]}, {"to": [90, 20], "through": [110, 0]}, {"to": [45, 20]},
    ]}
    b.extrude(plane=b.ref("X", 24), loops=[arm], direction="+X", depth=12)
    b.extrude(plane=b.ref("X", -24), loops=[arm], direction="-X", depth=12)
    b.hole(plane="right_plane", centers=[[90, 0]], diameter=22, through_all=True, direction="both")
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=25, direction="+Z", depth=55)
    b.cut(plane="front_plane", loops=[rect((0, 12.9), (8, 5.8))], direction="+Z", depth=55)
    b.hole(plane=b.ref("Y", 25), centers=[[0, 20]], diameter=6.8, direction="-Y", depth=12)
    b.hole(plane=b.ref("X", 36), centers=[[90, 14.5], [90, -14.5]], diameter=5, direction="-X", depth=10)
    b.hole(plane=b.ref("X", -36), centers=[[90, 14.5], [90, -14.5]], diameter=5, direction="+X", depth=10)


def wheel_rim(b: Builder) -> None:
    profile = [(0, 170), (0, 190), (12, 190), (12, 176), (188, 176), (188, 190), (200, 190), (200, 170),
               (170, 170), (170, 60), (210, 60), (210, 35), (140, 35), (140, 60), (150, 60), (150, 170)]
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-50, 0], axis_end=[300, 0])
    b.hole(plane=b.ref("Z", 140), centers=[polar(57.15, 36 + 72 * k) for k in range(5)], diameter=15,
           direction="+Z", depth=70)
    b.cut(plane=b.ref("Z", 150), loops=[filleted_sector(75, 155, 72 * k - 22, 72 * k + 22, 8) for k in range(5)],
          direction="+Z", depth=20)
    b.hole(plane=b.ref("X", 165), centers=[[100, 0]], diameter=11, direction="+X", depth=15)


CASES = {fn.__name__: fn for fn in (
    brake_disc, bt40_holder, cold_plate, crankshaft, engine_piston, gear_pump_body, gearbox_housing, geneva_wheel,
    heat_sink, honeycomb_panel, hydraulic_manifold, injector_plate, menger_sponge, pipe_manifold, ratchet_wheel,
    robot_arm_link, rocket_fin_can, spiral_staircase, truss_beam, turbine_disk, u_joint_yoke, wheel_rim,
)}


def _post(route: str, body: dict) -> dict:
    return requests.post(f"{SW}{route}", json=body, timeout=300).json()


def build(case: str) -> dict:
    print(f"== {case}", flush=True)
    _post("/api/close-all-documents", {})
    session = WorkflowSession(base_url=SW)
    session.start_part()
    builder = Builder(session)
    try:
        CASES[case](builder)
        state = session.call("get_model_state", {})
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / f"{case}_r1"
        _post("/api/export-step", {"stepPath": str(stem.with_suffix(".step"))})
        _post("/api/save-part-as", {"savePath": str(stem.with_suffix(".SLDPRT"))})
        print(f"  volume={state.get('volume_mm3')} bbox={state.get('bbox_mm', {}).get('size')} "
              f"failures={len(builder.failures)}", flush=True)
        return {"case": case, "failures": builder.failures, "volume_mm3": state.get("volume_mm3")}
    finally:
        _post("/api/close-all-documents", {})


def main() -> None:
    cases = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or list(CASES)
    summary = [build(case) for case in cases]
    for item in summary:
        print(f"{item['case']}: {len(item['failures'])} failed tool calls")


if __name__ == "__main__":
    main()
