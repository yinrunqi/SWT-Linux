"""用 L1_v2 工具（与 LLM 拿到的工具完全相同）逐题搭出 rubrics_extreme 20 道极难题的参考件，
证明每道题都存在一条能拿到 rubric 满分的 workflow 调用轨迹，并统计轨迹长度（= 每轮一个工具调用时的 LLM 调用数）。

    python eval/extreme_l1v2_reference.py [case ...] [--no-score]
        建模并导出 eval/artifacts/extreme_l1v2_reference/<case>_r1.step，
        调用轨迹写到 eval/extreme/oracle_l1v2/<case>.json，再用 venv-occ 跑 geomcheck.rubric 打分，
        汇总写到 eval/extreme/oracle_l1v2/summary.json

不调用任何 LLM。每题只用题面给出的尺寸（题面要求推算的量——平衡孔径、孔 B 入口、展开图孔位等——按题面推算）。
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from hard_l1_reference import circle, poly, polar, rect  # noqa: E402
from three_view_l1_reference import QUERY_TOOLS, RecordingBuilder  # noqa: E402
from planner.workflow.tools_v2 import WorkflowSessionV2  # noqa: E402

SW = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")
OUT = ROOT / "eval" / "artifacts" / "extreme_l1v2_reference"
TRAJ = ROOT / "eval" / "extreme" / "oracle_l1v2"
RUBRICS = ROOT / "eval" / "rubrics_extreme"
OCC_PYTHON = ROOT / "venv-occ" / "Scripts" / "python.exe"


def _post(route: str, body: dict) -> dict:
    return requests.post(f"{SW}{route}", json=body, timeout=600).json()


# ----------------------------------------------------------------------------- 几何小工具


def _unit(v):
    n = math.hypot(*v)
    return [c / n for c in v]


def _r6(p):
    return [round(float(c), 6) for c in p]


def arc_mid(center, r, p, q):
    """圆心 center、半径 r 上 p→q 短弧（< 180°）的中点。"""
    d = _unit([(p[0] - center[0]) + (q[0] - center[0]), (p[1] - center[1]) + (q[1] - center[1])])
    return _r6([center[0] + r * d[0], center[1] + r * d[1]])


def arc_mid_ccw(center, r, p, q):
    """圆心 center 上从 p 逆时针走到 q 的圆弧中点（可超过 180°）。"""
    a0 = math.atan2(p[1] - center[1], p[0] - center[0])
    a1 = math.atan2(q[1] - center[1], q[0] - center[0])
    while a1 <= a0:
        a1 += 2 * math.pi
    return polar(r, math.degrees((a0 + a1) / 2), *center)


def rot2(p, deg, c=(0.0, 0.0)):
    a = math.radians(deg)
    x, y = p[0] - c[0], p[1] - c[1]
    return _r6([c[0] + x * math.cos(a) - y * math.sin(a), c[1] + x * math.sin(a) + y * math.cos(a)])


def path(start, *segments) -> dict:
    """segments: ("line", to) 或 ("arc", to, through)。"""
    out = []
    for seg in segments:
        if seg[0] == "line":
            out.append({"to": _r6(seg[1])})
        else:
            out.append({"to": _r6(seg[1]), "through": _r6(seg[2])})
    return {"type": "path", "start": _r6(start), "segments": out}


def rotate_loop(loop: dict, deg: float) -> dict:
    return {"type": "path", "start": rot2(loop["start"], deg), "segments": [
        {k: rot2(v, deg) for k, v in seg.items()} for seg in loop["segments"]]}


# ----------------------------------------------------------------------------- 各题解法


def spoke_window(h, ri, ro, rf, span=60.0) -> dict:
    """辐条中心线 0° 与 span° 之间、半宽 h 的直辐条围出的 ri~ro 窗口，四个竖直角倒 rf（逆时针）。"""
    yc = h + rf
    c1 = (math.sqrt((ri + rf) ** 2 - yc ** 2), yc)
    c2 = (math.sqrt((ro - rf) ** 2 - yc ** 2), yc)
    mirror = lambda p: rot2([p[0], -p[1]], span)  # noqa: E731  关于 span/2 方向的镜像
    t1_line, t1_in = (c1[0], h), [c1[0] * ri / (ri + rf), c1[1] * ri / (ri + rf)]
    t2_line, t2_out = (c2[0], h), [c2[0] * ro / (ro - rf), c2[1] * ro / (ro - rf)]
    c3, c4 = mirror(c2), mirror(c1)
    t3_out, t3_line = mirror(t2_out), mirror(t2_line)
    t4_line, t4_in = mirror(t1_line), mirror(t1_in)
    return path(
        t1_line,
        ("line", t2_line),
        ("arc", t2_out, arc_mid(c2, rf, t2_line, t2_out)),
        ("arc", t3_out, polar(ro, span / 2)),
        ("arc", t3_line, arc_mid(c3, rf, t3_out, t3_line)),
        ("line", t4_line),
        ("arc", t4_in, arc_mid(c4, rf, t4_line, t4_in)),
        ("arc", t1_in, polar(ri, span / 2)),
        ("arc", t1_line, arc_mid(c1, rf, t1_in, t1_line)),
    )


def balanced_flywheel(b):
    section = [(15, -20), (30, -20), (30, -6), (70, -6), (70, -15), (100, -15), (100, 15), (70, 15), (70, 6),
               (30, 6), (30, 20), (15, 20)]
    radii = [0, 0, 4, 4, 0, 0, 0, 0, 4, 4, 0, 0]
    b.revolve(plane="top_plane", loops=[poly(section, corner_radii=radii)], axis_start=[0, -30], axis_end=[0, 30])
    # 平衡孔：π·6²·30·92 + 40·∫∫x dA（键槽）= π·(d/2)²·30·80
    key_moment = 20 * ((18.3 ** 2 - 15 ** 2) * 8 + 2 * 4 ** 3 / 3)
    balance_d = 2 * math.sqrt((math.pi * 36 * 30 * 92 + key_moment) / (math.pi * 30 * 80))
    xk = math.sqrt(15 ** 2 - 4 ** 2)
    bore = path((xk, 4), ("arc", (xk, -4), (-15, 0)), ("line", (18.3, -4)), ("line", (18.3, 4)), ("line", (xk, 4)))
    window = spoke_window(6, 38, 62, 5)
    loops = [bore, circle((92, 0), 12), circle((-80, 0), round(balance_d, 4))]
    loops += [rotate_loop(window, 60 * k) for k in range(6)]
    b.cut(plane="front_plane", loops=loops, through_all=True, direction="both")


INCH = 25.4


def drill_profile(axis_v, r, top_u, bottom_u, point=True):
    """在轴线 v = axis_v（沿 u 方向）一侧画出钻孔的半截面：从 top_u 到 bottom_u 的圆柱段 + 118° 钻尖。"""
    sign = 1 if bottom_u < top_u else -1
    tip = r / math.tan(math.radians(59))
    pts = [(top_u, axis_v), (top_u, axis_v + r), (bottom_u, axis_v + r)]
    pts.append((bottom_u - sign * tip, axis_v) if point else (bottom_u, axis_v))
    return poly(pts)


def inch_callout_plate(b):
    t = 0.5 * INCH
    b.extrude(plane="front_plane", loops=[rect((3 * INCH, 2 * INCH), (6 * INCH, 4 * INCH), corner_radius=0.25 * INCH)],
              depth=t)
    b.chamfer(distance=0.030 * INCH, edges={"origin": [3 * INCH, 1.0, t + 10], "direction": [0, 0, -1]})
    top = f"Z={t:g}"
    corners = [[x * INCH, y * INCH] for x, y in ((0.375, 0.375), (5.625, 0.375), (0.375, 3.625), (5.625, 3.625))]
    b.cut(plane=top, loops=[circle(c, round(0.266 * INCH, 4)) for c in corners] + [
        {"type": "slot", "start": [2.5 * INCH, 0.75 * INCH], "end": [3.5 * INCH, 0.75 * INCH], "width": 0.25 * INCH}],
        through_all=True, direction="-Z")
    b.counterbore(plane=top, centers=[[1.5 * INCH, 2 * INCH]], through_diameter=round(0.531 * INCH, 4),
                  head_diameter=round(0.812 * INCH, 4), head_depth=round(0.313 * INCH, 4), through_all=True,
                  direction="-Z")
    ck = (4.5 * INCH, 2 * INCH)
    b.countersink(plane=top, centers=[list(ck)], through_diameter=round(0.391 * INCH, 4),
                  head_diameter=0.75 * INCH, angle_deg=82, through_all=True, direction="-Z")
    # 3X #21 ↧.375 带 118° 钻尖：在过第一个孔轴线的 X 平面上旋转切除，再绕孔 ③ 轴线阵列
    y1 = ck[1] + 0.625 * INCH
    b.revolve(plane=f"X={ck[0]:g}", loops=[drill_profile(y1, 0.159 * INCH / 2, t + 1, t - 0.375 * INCH)],
              axis_start=[t + 2, y1], axis_end=[0, y1], cut=True)
    b.circular_pattern(count=3, axis="Z", center=[ck[0], ck[1], 0], angle_deg=360)


def clipped_hex_perforation(b):
    x0 = math.sqrt(90 ** 2 - 70 ** 2)
    b.extrude(plane="front_plane", loops=[path((-x0, 70), ("line", (x0, 70)), ("arc", (-x0, 70), (0, -90)))], depth=6)
    rc = 10 / math.sqrt(3)
    dy = 12 * math.sqrt(3) / 2
    hexes = []
    for j in range(-10, 11):
        for i in range(-12, 13):
            c = (12 * i + 6 * j, dy * j)
            verts = [(c[0] + rc * math.cos(math.radians(90 + 60 * m)), c[1] + rc * math.sin(math.radians(90 + 60 * m)))
                     for m in range(6)]
            if all(16 <= math.hypot(*p) <= 78 and p[1] <= 64 for p in verts):
                hexes.append({"type": "regular_polygon", "center": _r6(c), "sides": 6, "across_flats": 10,
                              "angle_deg": 90})
    loops = [circle((0, 0), 20)] + [circle(polar(82, 45 + 90 * k), 6.6) for k in range(4)] + hexes
    b.cut(plane="front_plane", loops=loops, through_all=True, direction="+Z")


def _link_geometry():
    (a, ra), (bb, rb), (c, rc) = ((0.0, 0.0), 25.0), ((140.0, 0.0), 15.0), ((60.0, 70.0), 12.0)
    tri = ((a[0] + bb[0] + c[0]) / 3, (a[1] + bb[1] + c[1]) / 3)

    def far_center(p1, r1, p2, r2, R):
        d1, d2 = R + r1, R + r2
        dx, dy = p2[0] - p1[0], p2[1] - p1[1]
        L = math.hypot(dx, dy)
        s = (d1 ** 2 - d2 ** 2 + L ** 2) / (2 * L)
        h = math.sqrt(d1 ** 2 - s ** 2)
        mx, my = p1[0] + s * dx / L, p1[1] + s * dy / L
        cands = [(mx - h * dy / L, my + h * dx / L), (mx + h * dy / L, my - h * dx / L)]
        return max(cands, key=lambda q: math.hypot(q[0] - tri[0], q[1] - tri[1]))

    def touch(center, r, o):
        d = _unit([o[0] - center[0], o[1] - center[1]])
        return (center[0] + r * d[0], center[1] + r * d[1])

    obc, oca = far_center(bb, rb, c, rc, 80), far_center(c, rc, a, ra, 50)
    L = math.hypot(bb[0] - a[0], bb[1] - a[1])
    nang = math.atan2(bb[1] - a[1], bb[0] - a[0]) - math.acos((ra - rb) / L)
    t_a = (a[0] + ra * math.cos(nang), a[1] + ra * math.sin(nang))
    t_b = (bb[0] + rb * math.cos(nang), bb[1] + rb * math.sin(nang))
    bc_b, bc_c = touch(bb, rb, obc), touch(c, rc, obc)
    ca_c, ca_a = touch(c, rc, oca), touch(a, ra, oca)
    return path(
        t_a,
        ("line", t_b),
        ("arc", bc_b, arc_mid_ccw(bb, rb, t_b, bc_b)),
        ("arc", bc_c, arc_mid(obc, 80, bc_b, bc_c)),
        ("arc", ca_c, arc_mid_ccw(c, rc, bc_c, ca_c)),
        ("arc", ca_a, arc_mid(oca, 50, ca_c, ca_a)),
        ("arc", t_a, arc_mid_ccw(a, ra, ca_a, t_a)),
    )


def arc_slot(center, R, w, deg0, deg1):
    h = w / 2
    p = lambda r, d: polar(r, d, *center)  # noqa: E731
    e1, e0 = p(R, deg1), p(R, deg0)
    return path(
        p(R + h, deg0),
        ("arc", p(R + h, deg1), p(R + h, (deg0 + deg1) / 2)),
        ("arc", p(R - h, deg1), polar(h, deg1 + 90, *e1)),
        ("arc", p(R - h, deg0), p(R - h, (deg0 + deg1) / 2)),
        ("arc", p(R + h, deg0), polar(h, deg0 - 90, *e0)),
    )


def tangent_link_lever(b):
    b.extrude(plane="front_plane", loops=[_link_geometry()], depth=12)
    b.extrude(plane="Z=12", loops=[circle((0, 0), 50)], depth=8)
    b.extrude(plane="Z=12", loops=[circle((140, 0), 30)], depth=4)
    xk = math.sqrt(12.5 ** 2 - 4 ** 2)
    bore = path((4, xk), ("line", (4, 15.8)), ("line", (-4, 15.8)), ("line", (-4, xk)), ("arc", (4, xk), (0, -12.5)))
    b.cut(plane="front_plane", loops=[bore, circle((140, 0), 15), circle((60, 70), 10), arc_slot((0, 0), 72, 8, 12, 32)],
          through_all=True, direction="+Z")


def filleted_shell_enclosure(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (120, 80), corner_radius=10)], depth=40)
    b.fillet(radius=6, edges={"origin": [0, 39, 50], "direction": [0, 0, -1]})
    b.shell(thickness=2.5, open_faces=["face(-Z, outermost)"])
    b.cut(plane="front_plane", loops=[rect((0, 0), (130, 90)), rect((0, 0), (117.6, 77.6), corner_radius=8.8)],
          direction="+Z", depth=2)
    b.extrude(plane="Z=5", loops=[circle((x, y), 8) for x in (-45, 45) for y in (-25, 25)], depth=33)
    b.hole(plane="Z=5", centers=[[x, y] for x in (-45, 45) for y in (-25, 25)], diameter=2.5, direction="+Z", depth=12)
    b.cut(plane="Z=28", loops=[rect((52.5, y), (25, 3)) for y in (-12, -6, 0, 6, 12)], direction="+Z",
          through_all=True)


def tricylinder_node(b):
    b.extrude(plane="right_plane", loops=[circle((0, 0), 40)], direction="both", depth=60)
    for plane in ("top_plane", "front_plane"):
        b.cut(plane=plane, loops=[rect((0, 0), (120, 120)), circle((0, 0), 40)], direction="both", through_all=True)
    b.extrude(plane="right_plane", loops=[circle((0, 0), 24)], direction="+X", depth=60)
    b.extrude(plane="top_plane", loops=[circle((0, 0), 20)], direction="+Y", depth=55)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 16)], direction="+Z", depth=50)
    c = -50 / math.sqrt(3)
    b.sweep(path_3d={"points": [[0, 0, 0], [c, c, c]]}, profile_diameter=12)
    # 球头：过球心的 X 平面上半圆绕平行于 Z 的直线旋转（该平面 u = Z、v = Y）
    b.revolve(plane=f"X={c:.6f}", loops=[path((c - 6, c), ("line", (c + 6, c)), ("arc", (c - 6, c), (c, c + 6)))],
              axis_start=[c - 10, c], axis_end=[c + 10, c])
    b.hole(plane="X=60", centers=[[0, 0]], diameter=14, direction="-X", depth=65)
    b.hole(plane="Y=55", centers=[[0, 0]], diameter=12, direction="-Y", depth=60)
    b.hole(plane="Z=50", centers=[[0, 0]], diameter=8, direction="-Z", depth=55)


def twisted_flute_knob(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 40)], depth=34)
    b.fillet(radius=4, edges={"origin": [19, 0, 40], "direction": [0, 0, -1]})
    # 槽心方位角 30° − 40°·z/30：z = 22.5 处正好在 +X（螺旋线起点），从这里向上、向下各扫一段，再各阵列 6 份
    lead = 30 * 360 / 40
    for axis, height in (("Z", 7.5), ("-Z", 23.5)):
        b.sweep(helix={"axis": axis, "center": [0, 0, 22.5], "radius": 21, "pitch": lead, "height": height,
                       "right_handed": False},
                profile_plane="Z=22.5", loops=[circle((21, 0), 10)], cut=True)
        b.circular_pattern(count=6, axis="Z", center=[0, 0, 0], angle_deg=360)
    yd = math.sqrt(9 - 2.5 ** 2)
    b.cut(plane="front_plane", loops=[path((2.5, yd), ("arc", (2.5, -yd), (-3, 0)), ("line", (2.5, yd)))],
          direction="+Z", depth=15)
    b.hole(plane="X=21", centers=[[8, 0]], diameter=2.5, direction="-X", depth=18.5)


def _sph(lat, lon, r=1.0):
    la, lo = math.radians(lat), math.radians(lon)
    return [r * math.cos(la) * math.cos(lo), r * math.cos(la) * math.sin(lo), r * math.sin(la)]


def spherical_dome_ports(b):
    outer = path((0, -40), ("line", (100, -40)), ("line", (100, -30)), ("line", (80, -30)), ("line", (80, 0)),
                 ("arc", (0, 80), polar(80, 45)), ("line", (0, -40)))
    b.revolve(plane="top_plane", loops=[outer], axis_start=[0, -50], axis_end=[0, 100])
    n1, n2 = (40, 30, 110, 30, 20), (60, 210, 105, 24, 16)
    for lat, lon, length, d_out, _ in (n1, n2):
        b.sweep(path_3d={"points": [[0, 0, 0], _r6(_sph(lat, lon, length))]}, profile_diameter=d_out)
    b.extrude(plane="right_plane", loops=[circle((40, -30), 28)], direction="+X", depth=100)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 50)], depth=95)
    cavity = path((0, -41), ("line", (72, -41)), ("line", (72, 0)), ("arc", (0, 72), polar(72, 45)), ("line", (0, -41)))
    b.revolve(plane="top_plane", loops=[cavity], axis_start=[0, -50], axis_end=[0, 100], cut=True)
    for lat, lon, length, _, d_in in (n1, n2):
        b.sweep(path_3d={"points": [[0, 0, 0], _r6(_sph(lat, lon, length + 1))]}, profile_diameter=d_in, cut=True)
    b.hole(plane="X=100", centers=[[40, -30]], diameter=18, direction="-X", depth=100)
    b.hole(plane="Z=95", centers=[[0, 0]], diameter=30, direction="-Z", depth=95)
    b.hole(plane="Z=-40", polar={"center": [0, 0], "pcd": 180, "count": 8, "start_deg": 22.5}, diameter=14,
           direction="+Z", depth=10)


def _v(a, b):
    return [b[k] - a[k] for k in range(3)]


def _vunit(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


def _vcross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _vadd(p, *terms):
    out = list(p)
    for s, v in terms:
        out = [out[k] + s * v[k] for k in range(3)]
    return _r6(out)


def pipe_route_3d(b):
    p0, p1, p2, p3 = [0, 0, 0], [150, 0, 0], [150, 120, 0], [230, 200, 140]
    m = [150, 60, 0]  # P1P2 直段上两个弯头切点之间的点，也是支管位置
    v3 = _vunit(_v(p2, p3))
    # 两个弯头半径不同：在 m 处分成两段扫描，先做实心管，最后沿同一路线扫掉内孔
    b.sweep(path_3d={"points": [p0, p1, m], "corner_radius": 45}, profile_diameter=30)
    b.sweep(path_3d={"points": [m, p2, p3], "corner_radius": 60}, profile_diameter=30)
    b.extrude(plane="right_plane", loops=[circle((0, 0), 80)] + [circle(polar(30, 45 + 90 * k), 9) for k in range(4)],
              direction="+X", depth=10)
    b.sweep(path_3d={"points": [_vadd(p3, (-12, v3)), p3]}, profile_diameter=90)
    b.extrude(plane="front_plane", loops=[circle((150, 60), 20)], direction="-Z", depth=70)
    b.sweep(path_3d={"points": [[-1, 0, 0], p1, m], "corner_radius": 45}, profile_diameter=24, cut=True)
    b.sweep(path_3d={"points": [m, p2, _vadd(p3, (1, v3))], "corner_radius": 60}, profile_diameter=24, cut=True)
    b.hole(plane="Z=-70", centers=[[150, 60]], diameter=14, direction="+Z", depth=70)
    u12 = _vunit(_v(p1, p2))
    e_in = _vunit(_vadd(u12, (-sum(u12[k] * v3[k] for k in range(3)), v3)))
    side = _vcross(v3, e_in)
    for k in range(4):
        a = math.radians(45 + 90 * k)
        c = _vadd(p3, (34 * math.cos(a), e_in), (34 * math.sin(a), side))
        b.sweep(path_3d={"points": [_vadd(c, (-13, v3)), _vadd(c, (1, v3))]}, profile_diameter=11, cut=True)


def sheet_metal_kfactor(b):
    a = math.radians(45)
    cs, sn = math.cos(a), math.sin(a)
    lo, li = (55 + 5 * cs, 50 + 5 * sn), (55 + 3 * cs, 50 + 3 * sn)
    d = (-sn, cs)
    lo2, li2 = (lo[0] + 20 * d[0], lo[1] + 20 * d[1]), (li[0] + 20 * d[0], li[1] + 20 * d[1])
    yz = [  # (y, z) 侧视轮廓，right_plane 上 (u, v) = (z, y)
        ("line", (55, 0)), ("arc", (60, 5), polar(5, -45, 55, 5)), ("line", (60, 50)),
        ("arc", lo, polar(5, 22.5, 55, 50)), ("line", lo2), ("line", li2), ("line", li),
        ("arc", (58, 50), polar(3, 22.5, 55, 50)), ("line", (58, 5)), ("arc", (55, 2), polar(3, -45, 55, 5)),
        ("line", (0, 2)), ("line", (0, 0)),
    ]
    swap = lambda p: (p[1], p[0])  # noqa: E731
    segs = [(s[0], swap(s[1])) if s[0] == "line" else (s[0], swap(s[1]), swap(s[2])) for s in yz]
    b.extrude(plane="right_plane", loops=[path((0, 0), *segs)], direction="both", depth=80)
    t, k = 2.0, 0.4
    ba1, ba2 = math.pi / 2 * (3 + k * t), a * (3 + k * t)
    b.hole(plane="front_plane", centers=[[0, 25]], diameter=8, direction="+Z", depth=2)
    z6 = 5 + 80 - (55 + ba1)
    b.hole(plane="Y=60", centers=[[-25, z6], [25, z6]], diameter=6, direction="-Y", depth=2)
    along = 122 - (55 + ba1 + 45 + ba2)
    m0 = (55 + (3 + t / 2) * cs, 50 + (3 + t / 2) * sn)
    yc, zc = m0[0] + along * d[0], m0[1] + along * d[1]
    n = (cs, sn)
    b.sweep(path_3d={"points": [_r6([0, yc - 3 * n[0], zc - 3 * n[1]]), _r6([0, yc + 3 * n[0], zc + 3 * n[1]])]},
            profile_diameter=5, cut=True)


def cast_manifold_fillets(b):
    b.extrude(plane="right_plane", loops=[circle((0, 0), 50)], direction="both", depth=160)
    holes = [circle((24.749, 24.749), 11), circle((24.749, -24.749), 11), circle((-24.749, 24.749), 11),
             circle((-24.749, -24.749), 11)]
    b.extrude(plane="X=-80", loops=[circle((0, 0), 90)] + holes, direction="+X", depth=12)
    b.extrude(plane="X=80", loops=[circle((0, 0), 90)] + holes, direction="-X", depth=12)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 36)], direction="+Z", depth=60)
    b.extrude(plane="top_plane", loops=[circle((40, 0), 30)], direction="+Y", depth=55)
    # 射线（选边半径 2mm，整条直线都不能靠近别的棱）在支管面上、离相贯线约 1mm 处命中；命中点离开圆柱面的起始缝线
    c30, s30 = math.cos(math.radians(30)), 0.5
    z_v = math.sqrt(25 ** 2 - 18 ** 2) + 1  # 竖直支管 +Y 方位处相贯线高度 + 1
    y_s = math.sqrt(25 ** 2 - (15 * s30) ** 2) + 1  # 侧支管 150° 方位处相贯线 y + 1
    b.fillet(radius=5, edges=[
        {"origin": _r6([0, 40, z_v]), "direction": [0, -1, 0]},
        {"origin": _r6([40 - 35 * c30, y_s, 35 * s30]), "direction": _r6([c30, 0, -s30])},
    ])
    # 法兰内侧端面上离主管外圆 1mm 处，沿主管轴向贴着外圆柱面射过去
    b.fillet(radius=3, edges=[
        {"origin": _r6([sx * 50, 26 * s30, 26 * c30]), "direction": [sx, 0, 0]} for sx in (1, -1)
    ])
    b.hole(plane="X=-80", centers=[[0, 0]], diameter=34, direction="+X", through_all=True)
    b.hole(plane="Z=60", centers=[[0, 0]], diameter=22, direction="-Z", depth=60)
    b.hole(plane="Y=55", centers=[[40, 0]], diameter=18, direction="-Y", depth=55)


def die_cast_cover(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (160, 100), corner_radius=12)], depth=45, draft_deg=3)
    # 内外壁拔模角相同、水平壁厚处处 5：抽壳厚度取法向距离 5·cos3°（顶板因此薄 0.007mm）
    b.shell(thickness=round(5 * math.cos(math.radians(3)), 6), open_faces=["face(-Z, outermost)"])
    b.extrude(plane="Z=4", loops=[circle((x, y), 12) for x in (-60, 60) for y in (-30, 30)], direction="+Z",
              depth=36.5, draft_deg=1.5, draft_outward=True)
    t1 = 1.5 + 20.5 * math.tan(math.radians(1))
    # 筋两端插进侧壁（z 20~40.5 处内壁 |x| ≤ 73.95、外壁 |x| ≥ 77.88；|y| 为 43.95 / 47.88）
    b.extrude(plane="right_plane", loops=[poly([(20, -1.5), (20, 1.5), (40.5, t1), (40.5, -t1)])],
              direction="both", depth=150)
    b.extrude(plane="top_plane", loops=[poly([(-1.5, 20), (1.5, 20), (t1, 40.5), (-t1, 40.5)])],
              direction="both", depth=90)
    b.extrude(plane="Z=45", loops=[circle((-40, 0), 40)], direction="+Z", depth=10, draft_deg=2)
    b.hole(plane="Z=55", centers=[[-40, 0]], diameter=20, direction="-Z", through_all=True)
    b.hole(plane="Z=4", centers=[[x, y] for x in (-60, 60) for y in (-30, 30)], diameter=4.2, direction="+Z",
           depth=25)


def drill_section(p, d, r, length):
    """(u, v) 平面内从 p 沿方向 d 钻孔的半截面（圆柱段 length + 118° 钻尖），返回 (轮廓, 轴线终点)。"""
    n = math.hypot(*d)
    d = (d[0] / n, d[1] / n)
    side = (-d[1], d[0])
    end = (p[0] + length * d[0], p[1] + length * d[1])
    tip = r / math.tan(math.radians(59))
    pts = [p, (p[0] + r * side[0], p[1] + r * side[1]), (end[0] + r * side[0], end[1] + r * side[1]),
           (end[0] + tip * d[0], end[1] + tip * d[1])]
    return poly([_r6(q) for q in pts]), _r6(end)


def compound_drill_block(b):
    k35 = math.tan(math.radians(35))
    b.extrude(plane="right_plane", loops=[poly([(0, 0), (40, 0), (60, _r6([20 / k35])[0]), (60, 80), (0, 80)])],
              direction="+X", depth=120)
    da = [math.tan(math.radians(20)), -math.tan(math.radians(15)), -1.0]
    entry_a = [40.0, 55.0, 60.0]
    # 孔 B 轴线在 x = xb 平面内、垂直斜面，并与孔 A 轴线交于 q
    y_mid = 10 / k35
    v = (entry_a[1] - y_mid + k35 * 10) / (k35 - da[1])
    q = _vadd(entry_a, (v, da))
    sin35, cos35 = math.sin(math.radians(35)), math.cos(math.radians(35))
    entry_b = (50.0, y_mid)  # X 平面上 (u, v) = (z, y)
    ub = (-cos35, sin35)
    lb = math.hypot(q[2] - entry_b[0], q[1] - entry_b[1])
    start_b = (entry_b[0] - 5 * ub[0], entry_b[1] - 5 * ub[1])
    sec_b, end_b = drill_section(start_b, ub, 5, lb + 5)
    b.revolve(plane=f"X={q[0]:.6f}", loops=[sec_b], axis_start=_r6(start_b), axis_end=end_b, cut=True)
    # 孔 A 轴线不在任何坐标平面内：先在前面临时加一块料，把孔 A 绕平行 X 的轴 (y, z) = (40, -300) 转 +15°
    # 得到落在 Y 平面内、位于临时料里的种子孔；旋转切除种子后再阵列回 -15°，最后切掉临时料
    c, s, yc, zc = math.cos(math.radians(15)), math.sin(math.radians(15)), 40.0, -300.0

    def rot(p):
        y, z = p[1] - yc, p[2] - zc
        return [p[0], yc + c * y - s * z, zc + s * y + c * z]

    ua = _vunit(da)
    la = 52 * math.sqrt(sum(x * x for x in da))
    seed0 = rot(_vadd(entry_a, (-5, ua)))
    seed_d = [ua[0], c * ua[1] - s * ua[2], s * ua[1] + c * ua[2]]
    assert abs(seed_d[1]) < 1e-12
    b.extrude(plane="Y=0", loops=[rect((60, 20), (120, 40))], direction="-Y", depth=60)
    sec_a, end_a = drill_section((seed0[0], seed0[2]), (seed_d[0], seed_d[2]), 6, la + 5)
    b.revolve(plane=f"Y={seed0[1]:.6f}", loops=[sec_a], axis_start=_r6([seed0[0], seed0[2]]), axis_end=end_a,
              cut=True)
    b.circular_pattern(count=2, axis="X", center=[0, yc, zc], angle_deg=15)
    b.cut(plane="Y=0", loops=[rect((60, 30), (140, 80))], direction="-Y", through_all=True)
    b.counterbore(plane="X=120", centers=[[30, 40]], through_diameter=8, head_diameter=14, head_depth=6, depth=50,
                  direction="-X")


def variable_fillet_bracket(b):
    h = 3 / math.sqrt(2)
    yz = [(60, 0), (60, 77), ("arc", (57, 80), (57 + h, 77 + h)), (53, 80), (50, 74), (50, 10), (2, 10), (0, 8)]
    segs = [("arc", s[1][::-1], s[2][::-1]) if s[0] == "arc" else ("line", s[::-1]) for s in yz]
    b.extrude(plane="right_plane", loops=[path((0, 0), *segs)], direction="+X", depth=120)  # (u, v) = (z, y)

    # 半径沿 X 线性变化的内角圆角：两端截面是以内角为中心相似放大的圆角区（多出 1mm 伸进实体），直纹放样后
    # 中间每个 X 截面都正好是半径 3 + 0.05x 的圆弧
    def web(r):
        m = r - r / math.sqrt(2)
        return path((10, 50 - r), ("arc", (10 + r, 50), (10 + m, 50 - m)), ("line", (10 + r, 51)), ("line", (9, 51)),
                    ("line", (9, 50 - r)))

    b.loft(sections=[{"plane": "X=0", "loops": [web(3)]}, {"plane": "X=120", "loops": [web(9)]}])
    b.extrude(plane="X=56", loops=[poly([(10, 50), (10, 10), (60, 50)])], direction="+X", depth=8)
    b.countersink(plane="Z=10", centers=[[30, 25], [90, 25]], through_diameter=6.6, head_diameter=13, angle_deg=90,
                  through_all=True, direction="-Z")
    b.cut(plane="Y=60", loops=[{"type": "slot", "start": [45, 50], "end": [75, 50], "width": 12}], direction="-Y",
          depth=10)


def _cam_lift(phi):
    if phi <= 150:
        u = phi / 150
        return 20 * (u - math.sin(2 * math.pi * u) / (2 * math.pi))
    if phi <= 180:
        return 20.0
    if phi <= 300:
        u = (phi - 180) / 120
        return 20 * (1 - (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5))
    return 0.0


def roller_cam(b, n=360):
    pitch = [polar(45 + _cam_lift(360 * k / n), 360 * k / n) for k in range(n)]
    pts = []
    for k, p in enumerate(pitch):
        a, c = pitch[k - 1], pitch[(k + 1) % n]
        t = _unit([c[0] - a[0], c[1] - a[1]])
        pts.append(_r6([p[0] - 12 * t[1], p[1] + 12 * t[0]]))  # 逆时针点列的外法向为 (t_y, -t_x)，向内偏置 12
    b.extrude(plane="front_plane", loops=[poly(pts)], depth=14)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 50)], depth=30)
    xk = math.sqrt(12.5 ** 2 - 4 ** 2)
    bore = path((xk, 4), ("line", (15.8, 4)), ("line", (15.8, -4)), ("line", (xk, -4)), ("arc", (xk, 4), (-12.5, 0)))
    b.cut(plane="front_plane", loops=[bore], through_all=True, direction="+Z")
    b.hole(plane="X=25", centers=[[22, 0]], diameter=5, direction="-X", depth=13)


def _naca(xb):
    """NACA 4412（c = 240）在弦长分数 xb 处的 (yt, yc, θ)。"""
    yt = 144 * (0.2969 * math.sqrt(xb) - 0.1260 * xb - 0.3516 * xb ** 2 + 0.2843 * xb ** 3 - 0.1036 * xb ** 4)
    if xb < 0.4:
        yc, dy = 0.25 * (0.8 * xb - xb ** 2), 0.5 * (0.4 - xb)
    else:
        yc, dy = 0.04 / 0.36 * (0.2 + 0.8 * xb - xb ** 2), 0.08 / 0.36 * (0.4 - xb)
    return yt, 240 * yc, math.atan(dy)


def naca_wing_rib(b, n=150):
    xs = [(1 - math.cos(math.pi * k / (n - 1))) / 2 for k in range(n)]
    pts = []
    for xb, s in [(x, 1) for x in xs[::-1]] + [(x, -1) for x in xs[1:-1]]:  # 后缘 → 上表面 → 前缘 → 下表面
        yt, yc, th = _naca(xb)
        pts.append(_r6([240 * xb - s * yt * math.sin(th), yc + s * yt * math.cos(th)]))
    b.extrude(plane="front_plane", loops=[poly(pts)], depth=4)
    holes = []
    for x in (60, 110, 160):
        yt, yc, _ = _naca(x / 240)
        holes.append(circle((x, round(yc, 6)), round(2 * yt - 12, 6)))
    b.cut(plane="front_plane", loops=holes + [rect((96, 25), (12, 30.8))], through_all=True, direction="+Z")


def _involute_band(phi0, phi1, k0, k1, per_rad=16):
    """基圆 3 的渐开线带 u = φ + k（k ∈ [k0, k1]），两端沿发生线截平：外壁 φ0→φ1，再内壁 φ1→φ0。"""
    n = max(8, int((phi1 - phi0) * per_rad))
    ph = [phi0 + (phi1 - phi0) * i / n for i in range(n + 1)]

    def pt(p, k):
        u = p + k
        return _r6([3 * (math.cos(p) + u * math.sin(p)), 3 * (math.sin(p) - u * math.cos(p))])

    return poly([pt(p, k1) for p in ph] + [pt(p, k0) for p in ph[::-1]])


def scroll_wrap_plate(b):
    bolts = [circle(polar(70, 45 + 90 * k), 6.6) for k in range(4)]
    b.extrude(plane="front_plane", loops=[circle((0, 0), 150), circle((0, 0), 10), circle((0, -70), 5)] + bolts,
              depth=8)
    b.extrude(plane="Z=8", loops=[_involute_band(math.pi, 6 * math.pi, -0.75, 0.75)], direction="+Z", depth=30)
    b.cut(plane="Z=38", loops=[_involute_band(2 * math.pi, 6 * math.pi - 0.5, -0.25, 0.25)], direction="-Z", depth=2)


SPRING_PITCH = [(0.0, 5.0), (0.75, 5.0), (1.25, 12.0), (5.75, 12.0), (6.25, 5.0), (7.0, 5.0)]


def _spring_z(n):
    z = 0.0
    for (n0, p0), (n1, p1) in zip(SPRING_PITCH, SPRING_PITCH[1:]):
        if n <= n0:
            break
        m = min(n, n1)
        z += (p0 + p0 + (p1 - p0) * (m - n0) / (n1 - n0)) / 2 * (m - n0)
    return z


def variable_pitch_spring(b, per_turn=96):
    # 等螺距段用螺旋线（起点必须在 +X 方位，即整圈处）；变螺距过渡段用每圈 96 点的空间折线 + 转角圆角 R19
    # （接近螺旋线的曲率半径，弦高误差约 0.01mm）
    def coil(n0, n1):
        k0, k1 = round(n0 * per_turn), round(n1 * per_turn)
        pts = [_r6([20 * math.cos(2 * math.pi * n), -20 * math.sin(2 * math.pi * n), _spring_z(n)])
               for n in (k / per_turn for k in range(k0, k1 + 1))]
        b.sweep(path_3d={"points": pts, "corner_radius": 19}, profile_diameter=4)

    helix = {"radius": 20, "right_handed": False}
    b.sweep(helix={**helix, "axis": "Z", "center": [0, 0, 0], "pitch": 5, "height": 3.75}, profile_diameter=4)
    coil(0.75, 2)
    b.sweep(helix={**helix, "axis": "Z", "center": [0, 0, _spring_z(2)], "pitch": 12, "height": 45},
            profile_diameter=4)
    coil(5.75, 6.25)
    b.sweep(helix={**helix, "axis": "-Z", "center": [0, 0, 70], "pitch": 5, "height": 3.75}, profile_diameter=4)
    b.cut(plane="front_plane", loops=[rect((0, 0), (60, 60))], direction="-Z", through_all=True)
    b.cut(plane="Z=70", loops=[rect((0, 0), (60, 60))], direction="+Z", through_all=True)


def worm_shaft(b):
    tip = 2.5 / math.tan(math.radians(59))
    rz = [(10, 0), (10, 4), (9.3, 4), (9.3, 5.3), (10, 5.3), (10, 25), (18, 25), (18, 85), (10, 85), (10, 115),
          (2.5, 115), (2.5, 103), (0, 103 - tip)]
    b.revolve(plane="top_plane", loops=[poly([(0, 0)] + rz)], axis_start=[0, 0], axis_end=[0, 120])
    # ZA 蜗杆：轴向截面里的齿槽是直边梯形（槽半宽 p/4 + (r − 15)·tan20°），沿 r = 15 的左旋螺旋线扫掉；
    # 螺旋起点（方位 0°）放在一个齿槽中心 z = 55 − p/2 − 2·导程，第二头绕轴阵列 180°
    p = math.pi * 3
    t20 = math.tan(math.radians(20))
    half = lambda r: p / 4 + (r - 15) * t20  # noqa: E731
    z_s = round(55 - p / 2 - 4 * p, 6)
    # SolidWorks 沿样条螺旋线扫描时，截面在起点相对路径切向固定多转约 2.67°（实测：轴向截面里齿槽中心
    # 偏 0.034 + 0.0475·(r − 15) mm，与转角无关）。截面画在 Y = 0 平面上，先绕起点切向反转这一角度；
    # 转出平面的分量按螺旋运动折算成轴向位移
    alpha = 0.0466
    lam = math.atan(2 * p / (2 * math.pi * 15))
    c, s = math.cos(lam), math.sin(lam)

    def vertex(r, w):
        rho = r - 15
        return _r6([r + alpha * c * w, z_s + w - alpha * c * rho - 3 * alpha * s * rho / r])

    space = poly([vertex(r, sgn * half(r)) for r, sgn in ((11.4, -1), (19, -1), (19, 1), (11.4, 1))])
    b.sweep(helix={"axis": "Z", "center": [0, 0, z_s], "radius": 15, "pitch": round(2 * p, 6),
                   "height": 80, "right_handed": False}, profile_plane="Y=0", loops=[space], cut=True)
    b.circular_pattern(count=2, axis="Z", center=[0, 0, 0], angle_deg=360)
    b.cut(plane="X=6.5", loops=[{"type": "slot", "start": [93, 0], "end": [107, 0], "width": 6}], direction="+X",
          through_all=True)


def helical_ring_gear(b):
    beta = math.radians(15)
    mt = 2.5 / math.cos(beta)
    at = math.atan(math.tan(math.radians(20)) / math.cos(beta))
    rp = 54 * mt / 2
    rb, ra, rf = rp * math.cos(at), rp - 2.5, rp + 1.25 * 2.5
    inv = lambda a: math.tan(a) - a  # noqa: E731
    half = lambda r: math.pi / 108 + inv(at) - inv(math.acos(rb / r))  # noqa: E731  齿槽半角（弧度）
    bolts = [circle(polar(80, 60 * k), 9) for k in range(6)]
    b.extrude(plane="front_plane", loops=[circle((0, 0), 180), circle((0, 0), round(2 * ra, 6))] + bolts, depth=20)

    # 一个齿槽的端面轮廓：两侧渐开线（16 点）+ 齿根圆弧，向内多伸 1mm 进内孔；右旋：截面方位随 z 线性增加
    def space(z):
        c = math.pi / 54 + (z - 10) * math.tan(beta) / rp
        rs = [ra + (rf - ra) * i / 15 for i in range(16)]
        pts = [(ra - 1, c - half(ra))] + [(r, c - half(r)) for r in rs]
        pts += [(rf, c - half(rf) + 2 * half(rf) * i / 6) for i in range(1, 6)]
        pts += [(r, c + half(r)) for r in rs[::-1]] + [(ra - 1, c + half(ra))]
        return poly([_r6([r * math.cos(a), r * math.sin(a)]) for r, a in pts])

    b.loft(sections=[{"plane": f"Z={z:g}", "loops": [space(z)]} for z in (-1, 10, 21)], cut=True)
    b.circular_pattern(count=54, axis="Z", center=[0, 0, 0], angle_deg=360)
    b.revolve(plane="top_plane", loops=[poly(_r6(q) for q in ((ra - 3, 19), (ra, 19), (ra + 2.5, 21.5), (ra - 3, 21.5)))],
              axis_start=[0, 0], axis_end=[0, 30], cut=True)
    b.hole(plane="Y=90", centers=[[0, 10]], diameter=4, direction="-Y", depth=30)


CASES = {fn.__name__: fn for fn in (
    helical_ring_gear, worm_shaft, compound_drill_block, variable_fillet_bracket, roller_cam, naca_wing_rib, scroll_wrap_plate,
    variable_pitch_spring,
    pipe_route_3d, sheet_metal_kfactor, cast_manifold_fillets, die_cast_cover,
    balanced_flywheel, inch_callout_plate, clipped_hex_perforation, tangent_link_lever, filleted_shell_enclosure,
    tricylinder_node, twisted_flute_knob, spherical_dome_ports,
)}


# ----------------------------------------------------------------------------- 运行与打分


def score(case: str, step: Path) -> dict:
    out = step.with_suffix(".rubric.json")
    proc = subprocess.run(
        [str(OCC_PYTHON), "-X", "utf8", "-m", "geomcheck.rubric", "--step", str(step),
         "--rubric", str(RUBRICS / f"{case}.yaml"), "--output", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(proc.stdout.strip() or proc.stderr.strip()[-2000:], flush=True)
    if not out.exists():
        return {"rubric_score": None, "accepted": False}
    res = json.loads(out.read_text(encoding="utf-8"))[0]
    return {"rubric_score": res["score"], "accepted": res["accepted"],
            "failed_items": [r["id"] for r in res["rubrics"] if not r["ok"]]}


def build(case: str, do_score: bool = True) -> dict:
    print(f"== {case}", flush=True)
    started = time.perf_counter()
    _post("/api/close-all-documents", {})
    session = WorkflowSessionV2(base_url=SW)
    session.start_part()
    builder = RecordingBuilder(session)
    try:
        CASES[case](builder)
        state = session.call("get_model_state", {}).get("state") or {}
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / f"{case}_r1"
        _post("/api/export-step", {"stepPath": str(stem.with_suffix(".step"))})
        _post("/api/save-part-as", {"savePath": str(stem.with_suffix(".SLDPRT"))})
    finally:
        _post("/api/close-all-documents", {})
    modeling = [c for c in builder.trajectory if c["tool"] not in QUERY_TOOLS]
    record = {
        "case": case,
        "tool_calls": len(builder.trajectory),
        "modeling_calls": len(modeling),
        "failed_calls": len(builder.failures),
        # 每轮一个工具调用时的 LLM 调用数：建模调用 + 收尾的 finish
        "llm_calls_one_tool_per_turn": len(builder.trajectory) + 1,
        "volume_mm3": state.get("volume_mm3"),
        "bbox_mm": (state.get("bbox_mm") or {}).get("size"),
        "seconds": round(time.perf_counter() - started, 1),
    }
    if do_score:
        record.update(score(case, stem.with_suffix(".step")))
    record["trajectory"] = builder.trajectory + [{"tool": "finish", "args": {"summary": f"{case} oracle"}}]
    TRAJ.mkdir(parents=True, exist_ok=True)
    (TRAJ / f"{case}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  volume={record['volume_mm3']} bbox={record['bbox_mm']} calls={record['tool_calls']} "
          f"failures={record['failed_calls']} score={record.get('rubric_score')} {record['seconds']}s", flush=True)
    return {k: v for k, v in record.items() if k != "trajectory"}


def write_summary() -> None:
    rows = []
    for case in CASES:
        path_ = TRAJ / f"{case}.json"
        if path_.exists():
            rows.append({k: v for k, v in json.loads(path_.read_text(encoding="utf-8")).items() if k != "trajectory"})
    calls = sorted(r["llm_calls_one_tool_per_turn"] for r in rows)
    stats = {}
    if calls:
        stats = {"min": calls[0], "median": statistics.median(calls), "mean": round(statistics.mean(calls), 2),
                 "p90": calls[min(len(calls) - 1, math.ceil(0.9 * len(calls)) - 1)], "max": calls[-1]}
    summary = {
        "description": "extreme 20 题的 L1_v2 oracle 轨迹（手写工具调用，不调用 LLM）；"
                       "llm_calls_one_tool_per_turn = 工具调用数 + finish",
        "tools": "L1_v2 (planner/workflow/tools_v2.py)",
        "accepted": sum(bool(r.get("accepted")) for r in rows),
        "cases": rows,
        "llm_calls": stats,
    }
    (TRAJ / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"summary: {summary['accepted']}/{len(rows)} accepted, llm_calls={stats}")


def main() -> None:
    cases = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or list(CASES)
    do_score = "--no-score" not in sys.argv
    for case in cases:
        build(case, do_score)
    write_summary()


if __name__ == "__main__":
    main()
