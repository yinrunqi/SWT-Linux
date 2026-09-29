"""复杂综合件（showcase）的参考实体：按题面尺寸用 OCP 直接构造。

每个构造函数的默认参数给出一个符合题面的正例；个别参数用来生成针对性改错的反例。
除凸轮轴沿 X 轴外，其余零件都以 Z 轴为主轴或板厚方向。
"""

from __future__ import annotations

import math

from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.GeomAbs import GeomAbs_Line
from OCP.gp import gp_Pnt

from .fixtures import box, clean, cut, cyl, fillet, fuse, prism, torus
from .refs import common, hexagon, hull2, rect_z, revolve, rotate_z, vertical_lines

Z = (0, 0, 1)
X = (1, 0, 0)


def _polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


def _stadium_polar(r0, r1, width, deg, z0, h):
    """沿方位角 deg 的径向长圆孔工具：两端半圆圆心在半径 r0、r1 处。"""
    body = fuse(rect_z(r0, -width / 2, r1, width / 2, z0, h),
                cyl((r0, 0, z0), Z, width / 2, h), cyl((r1, 0, z0), Z, width / 2, h))
    return rotate_z(body, deg)


def _vertical_edges_at(points):
    def pick(c):
        if c.GetType() != GeomAbs_Line:
            return False
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        if abs(p0.X() - p1.X()) > 1e-9 or abs(p0.Y() - p1.Y()) > 1e-9:
            return False
        return any(math.hypot(p0.X() - x, p0.Y() - y) < 1e-4 for x, y in points)
    return pick


# ---------------------------------------------------------------- 四旋翼机架


def quad_frame(arm_w=16.0, fillet_r=5.0, slot=(45.0, 75.0), motor_offset=9.5, mount_r=110.0):
    t = 4.0
    body = box(-30, -30, 0, 30, 30, t)
    parts = []
    for k in range(4):
        a = 45 + 90 * k
        parts.append(rect_z(0, -arm_w / 2, mount_r, arm_w / 2, 0, t, rot=a))
        parts.append(cyl((*_polar(mount_r, a), 0), Z, 16, t))
    body = clean(fuse(body, *parts))
    if fillet_r:
        e = 30 - arm_w / 2 * math.sqrt(2)
        corners = [(sx * 30, sy * e) for sx in (1, -1) for sy in (1, -1)] + \
                  [(sx * e, sy * 30) for sx in (1, -1) for sy in (1, -1)]
        body = clean(fillet(body, fillet_r, _vertical_edges_at(corners)))
    tools = [cyl((0, 0, -1), Z, 6, t + 2)]
    tools += [cyl((sx * 15.25, sy * 15.25, -1), Z, 1.75, t + 2) for sx in (1, -1) for sy in (1, -1)]
    for k in range(4):
        a = 45 + 90 * k
        cx, cy = _polar(mount_r, a)
        tools.append(cyl((cx, cy, -1), Z, 4, t + 2))
        tools += [cyl((cx + dx, cy + dy, -1), Z, 1.6, t + 2)
                  for dx, dy in ((motor_offset, 0), (-motor_offset, 0), (0, motor_offset), (0, -motor_offset))]
        tools.append(_stadium_polar(slot[0], slot[1], 6, a, -1, t + 2))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 连杆


def connecting_rod(pocket_depth=6.0, oil_hole=True, chamfer=True, center=140.0):
    body = hull2((0, 0), 28, (center, 0), 15, 0, 20)
    trap = [(40, -12), (110, -7), (110, 7), (40, 12)]
    tools = [cyl((0, 0, -1), Z, 18, 22), cyl((center, 0, -1), Z, 10, 22),
             prism([(x, y, 20 - pocket_depth) for x, y in trap], (0, 0, pocket_depth + 1)),
             prism([(x, y, -1) for x, y in trap], (0, 0, pocket_depth + 1))]
    if oil_hole:
        tools.append(cyl((center, 0, 10), X, 2, 25))
    if chamfer:
        tools.append(revolve([(17, 19), (18, 19), (20, 21), (17, 21)]))
        tools.append(revolve([(17, -1), (20, -1), (18, 1), (17, 1)]))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 散热电机壳


def finned_housing(n_fins=12, fin_phase=0.0, fin_top=92.0, bottom=8.0):
    flange = fillet(box(-50, -50, 92, 50, 50, 100), 10, vertical_lines)
    fins = [rect_z(38, -1.5, 50, 1.5, 0, fin_top, rot=fin_phase + k * 360 / n_fins) for k in range(n_fins)]
    body = clean(fuse(cyl((0, 0, 0), Z, 40, 100), flange, *fins))
    tools = [cyl((0, 0, bottom), Z, 35, 101 - bottom), cyl((0, 0, -1), Z, 11, bottom + 2)]
    tools += [cyl((sx * 42, sy * 42, 91), Z, 3.5, 10) for sx in (1, -1) for sy in (1, -1)]
    tools += [cyl((*_polar(20, 45 + 90 * k), -1), Z, 2.25, bottom + 2) for k in range(4)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 链轮


def sprocket(n_teeth=18, pitch_d=73.14, keyway_bottom=12.8, screw_z=13.5, hole_phase=0.0):
    body = fuse(cyl((0, 0, 0), Z, 40, 7), cyl((0, 0, 7), Z, 20, 13))
    tools = [cyl((*_polar(pitch_d / 2, k * 360 / n_teeth), -1), Z, 4.3, 9) for k in range(n_teeth)]
    tools += [cyl((*_polar(26, hole_phase + 60 * k), -1), Z, 4.5, 9) for k in range(6)]
    tools += [cyl((0, 0, -1), Z, 10, 22), box(0, -3, -1, keyway_bottom, 3, 21)]
    tools.append(cyl((0, 0, screw_z), X, 2.1, 25))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 手轮


def handwheel(n_spokes=5, handle_top=70.0, spoke_d=12.0, handle_r=92.0):
    mid = 18.0
    parts = [torus((0, 0, mid), Z, X, 92, 8), cyl((0, 0, 0), Z, 20, 36)]
    for k in range(n_spokes):
        a = math.radians(72 * k * 5 / n_spokes)
        d = (math.cos(a), math.sin(a), 0)
        parts.append(cyl((15 * d[0], 15 * d[1], mid), d, spoke_d / 2, 77))
    h = handle_top - 10
    parts.append(cyl((handle_r, 0, mid), Z, 10, h))
    parts.append(BRepPrimAPI_MakeSphere(gp_Pnt(handle_r, 0, mid + h), 10).Shape())
    # 轮辐与环面相贯处 UnifySameDomain 会产生无效拓扑，这里不做面合并
    return cut(fuse(*parts), hexagon(17, -1, 38))


# ---------------------------------------------------------------- 凸轮轴


def _hull2_x(c1, r1, c2, r2, x0, h):
    """YZ 平面内两圆（圆心 (y, z)）的公切线凸轮廓，沿 +X 拉伸 h。"""
    (y1, z1), (y2, z2) = c1, c2
    dy, dz = y2 - y1, z2 - z1
    D = math.hypot(dy, dz)
    uy, uz = dy / D, dz / D
    s = (r1 - r2) / D
    c = math.sqrt(1 - s * s)
    pts = []
    for sign in (1, -1):
        ny, nz = s * uy - sign * c * uz, s * uz + sign * c * uy
        pts.append(((y1 + r1 * ny, z1 + r1 * nz), (y2 + r2 * ny, z2 + r2 * nz)))
    (a1, a2), (b1, b2) = pts
    quad = prism([(x0, *a1), (x0, *a2), (x0, *b2), (x0, *b1)], (h, 0, 0))
    return fuse(quad, cyl((x0, y1, z1), X, r1, h), cyl((x0, y2, z2), X, r2, h))


def camshaft(phases=(0, 90, 180, 270), centers=(40, 78, 142, 180), nose_off=12.0, dowel_depth=8.0):
    """从左往右看顺时针 = 绕 +X 的右手正向：鼻尖方向 (0, cos φ, sin φ)，φ = 0 指向定位销孔（+Y）。"""
    parts = [cyl((0, 0, 0), X, 10, 220), cyl((220, 0, 0), X, 9, 25)]
    parts += [cyl((x0, 0, 0), X, 15, 16) for x0 in (0, 102, 204)]
    for xc, ph in zip(centers, phases):
        a = math.radians(ph)
        parts.append(_hull2_x((0, 0), 16, (nose_off * math.cos(a), nose_off * math.sin(a)), 8, xc - 7, 14))
    body = clean(fuse(*parts))
    return clean(cut(body, cyl((-1, 9, 0), X, 2.5, dowel_depth + 1), box(220, 6, -2.5, 246, 10, 2.5)))


# ---------------------------------------------------------------- 行星架


def _disc(z0):
    return revolve([(0, z0), (59, z0), (60, z0 + 1), (60, z0 + 7), (59, z0 + 8), (0, z0 + 8)])


def planet_carrier(gap=20.0, pin_phase=0.0, tap_depth=20.0, sun_d=40.0):
    top = 8 + gap
    parts = [_disc(0), _disc(top), cyl((0, 0, -40), Z, 15, 40)]
    parts += [cyl((*_polar(45, 60 + 120 * k), 8), Z, 9, gap) for k in range(3)]
    body = clean(fuse(*parts))
    tools = [cyl((*_polar(45, 60 + 120 * k), -1), Z, 3.3, top + 10) for k in range(3)]
    tools += [cyl((*_polar(45, pin_phase + 120 * k), -1), Z, 6, top + 10) for k in range(3)]
    tools += [cyl((0, 0, top - 1), Z, sun_d / 2, 10), cyl((0, 0, -41), Z, 4.25, tap_depth + 1)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 离心叶轮


def _blade(center, radius, height=20.0, tip_r=58.0, tip="arc"):
    """tip：arc（外端为 R58 圆柱面）/ radial（外端为过中线圆弧圆心的径向平面，中线恰止于 R58）。"""
    band = cut(cyl((*center, 6), Z, radius + 1.5, height), cyl((*center, 5), Z, radius - 1.5, height + 2))
    if tip == "arc":
        band = common(band, cyl((0, 0, 5), Z, tip_r, height + 2))
    else:
        cx, cy = center
        lo, hi = 0.0, 180.0  # 中线圆心角 θ（自 +Y 顺时针）到达半径 tip_r 处
        for _ in range(60):
            m = (lo + hi) / 2
            p = (cx + radius * math.sin(math.radians(m)), cy + radius * math.cos(math.radians(m)))
            lo, hi = (m, hi) if math.hypot(*p) < tip_r else (lo, m)
        L = 200.0
        u = lambda deg: (cx + L * math.sin(math.radians(deg)), cy + L * math.cos(math.radians(deg)), 5)
        band = common(band, prism([(cx, cy, 5), u(-20), u(20), u(lo)], (0, 0, height + 2)))
    return common(band, box(10, -100, 5, 100, 100, 7 + height))


def impeller(n_blades=8, center=(18.0, -40.0), radius=40.0, height=20.0, chamfer=True, tip="arc"):
    hub = [(0, 0), (18, 0), (18, 29), (17, 30), (0, 30)] if chamfer else [(0, 0), (18, 0), (18, 30), (0, 30)]
    blade = _blade(center, radius, height, tip=tip)
    parts = [cyl((0, 0, 0), Z, 60, 6), revolve(hub)] + [rotate_z(blade, k * 360 / n_blades) for k in range(n_blades)]
    body = clean(fuse(*parts))
    return clean(cut(body, cyl((0, 0, -1), Z, 6, 32)))
