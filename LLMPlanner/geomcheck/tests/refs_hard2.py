"""困难子集第二批 22 道复杂综合件的参考实体：按题面尺寸用 OCP 直接构造。

每个构造函数的默认参数给出一个符合题面的正例；个别参数用来生成针对性改错的反例。
各零件的摆放方向与题面坐标一致（题面未规定坐标时以 Z 轴为主轴）；rubric 只用零件自身的特征建坐标系。
"""

from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from .fixtures import box, clean, cut, cyl, fillet, fuse, prism
from .refs import common, hexagon, hull2, revolve, rotate_z, vertical_lines

Z = (0, 0, 1)
X = (1, 0, 0)
Y = (0, 1, 0)


def _polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


def _translate(shape, v):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*v))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _rotate(shape, axis, deg, origin=(0, 0, 0)):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(*origin), gp_Dir(*axis)), math.radians(deg))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _fuse_all(shapes):
    """两两分组做布尔并（大量同类小实体时比逐个累加快）。"""
    shapes = list(shapes)
    while len(shapes) > 1:
        nxt = [BRepAlgoAPI_Fuse(a, b).Shape() for a, b in zip(shapes[0::2], shapes[1::2])]
        if len(shapes) % 2:
            nxt.append(shapes[-1])
        shapes = nxt
    return shapes[0]


def _cut_all(base, tools):
    return cut(base, _fuse_all(tools)) if tools else base


def _annulus(r0, r1, z0, h):
    return cut(cyl((0, 0, z0), Z, r1, h), cyl((0, 0, z0 - 1), Z, r0, h + 2))


def _fan(r, a0, a1, z0, h):
    """圆心角 [a0, a1]（度，逆时针）的扇形柱体，半径 r。"""
    far = 4 * r
    n = max(2, int(math.ceil((a1 - a0) / 30)))
    pts = [(0, 0, z0)] + [(*_polar(far, a0 + (a1 - a0) * i / n), z0) for i in range(n + 1)]
    return common(cyl((0, 0, z0), Z, r, h), prism(pts, (0, 0, h)))


def _sector(r0, r1, a0, a1, z0, h):
    s = _fan(r1, a0, a1, z0, h)
    return cut(s, cyl((0, 0, z0 - 1), Z, r0, h + 2)) if r0 > 0 else s


def _edges_at(points, tol=1e-4):
    """平行于 Z 轴、经过给定 (x, y) 点之一的直线棱边。"""
    def pick(c):
        if c.GetType() != GeomAbs_Line:
            return False
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        if abs(p0.X() - p1.X()) > 1e-7 or abs(p0.Y() - p1.Y()) > 1e-7:
            return False
        return any(math.hypot(p0.X() - x, p0.Y() - y) < tol for x, y in points)
    return pick


def _circles_at(specs):
    """圆心在 Z 轴上、(半径, z) 属于 specs 的圆棱边。"""
    def pick(c):
        if c.GetType() != GeomAbs_Circle:
            return False
        circ = c.Circle()
        loc = circ.Location()
        return math.hypot(loc.X(), loc.Y()) < 1e-6 and any(
            abs(circ.Radius() - r) < 1e-6 and abs(loc.Z() - z) < 1e-6 for r, z in specs)
    return pick


def _rrect(a, b, r, z0, h):
    """中心在原点、a×b、竖直棱边倒 R r 的矩形柱体。"""
    return fillet(box(-a / 2, -b / 2, z0, a / 2, b / 2, z0 + h), r, vertical_lines)


# ---------------------------------------------------------------- 1 双拐曲轴（沿 Z，t = z）


def crankshaft(phase_b=180.0, counterweight=True, keyway_side=180.0, pin_offset=38.0):
    parts = [cyl((0, 0, 0), Z, 50, 15)]
    parts += [cyl((0, 0, z0), Z, 25, 30) for z0 in (15, 115, 215)]
    parts.append(cyl((0, 0, 245), Z, 15, 50))
    for phi, (w0, p0, w1) in ((0.0, (45, 60, 100)), (phase_b, (145, 160, 200))):
        px, py = _polar(pin_offset, phi)
        parts.append(cyl((px, py, p0), Z, 20, 40))
        for z0 in (w0, w1):
            parts.append(hull2((0, 0), 32, (px, py), 26, z0, 15))
            if counterweight:
                parts.append(_fan(70, phi + 120, phi + 240, z0, 15))
    body = clean(fuse(*parts))
    tools = [cyl((0, 0, -1), Z, 10, 13), cyl((25, 0, -1), Z, 4, 11)]
    tools += [cyl((*_polar(38, 30 + 60 * k), -1), Z, 4.5, 17) for k in range(6)]
    key = box(11, -4, 255, 20, 4, 296)
    tools.append(rotate_z(key, keyway_side))
    for phi, zc in ((0.0, 80), (phase_b, 180)):
        px, py = _polar(pin_offset, phi)
        d = (-math.sin(math.radians(phi)), math.cos(math.radians(phi)), 0)
        tools.append(cyl((px - 25 * d[0], py - 25 * d[1], zc), d, 3, 50))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 2 通风刹车盘


def brake_disc(n_vanes=40, hole_phase=4.5, gap=12.0, hat_wall=(85.0, 95.0)):
    top = 10 + gap
    rings = [_annulus(90, 150, 0, 10), _annulus(90, 150, top, 10)]
    band = _annulus(90, 150, 10, gap)
    vanes = [common(band, rotate_z(box(80, -2.5, 10, 160, 2.5, top), 360 * k / n_vanes)) for k in range(n_vanes)]
    r0, r1 = hat_wall
    hat = [_annulus(r0, r1, top, 70 - top), cyl((0, 0, 62), Z, r1, 8)]
    body = clean(_fuse_all(rings + vanes + hat))
    tools = [cyl((0, 0, 61), Z, 35, 10)]
    tools += [cyl((*_polar(57.15, 72 * k), 61), Z, 7, 10) for k in range(5)]
    tools += [cyl((*_polar(120, hole_phase + 18 * k), -1), Z, 4, top + 12) for k in range(20)]
    return clean(_cut_all(body, tools))


# ---------------------------------------------------------------- 3 五辐轮毂


def wheel_rim(window_deg=44.0, fillet_r=8.0, valve_deg=36.0, n_lugs=5):
    parts = [_annulus(170, 176, 0, 200), _annulus(170, 190, 0, 12), _annulus(170, 190, 188, 12),
             cyl((0, 0, 150), Z, 172, 20), cyl((0, 0, 140), Z, 60, 70)]
    body = clean(fuse(*parts))
    h = window_deg / 2
    windows = [_sector(75, 155, 36 + 72 * k - h, 36 + 72 * k + h, 100, 120) for k in range(5)]
    body = clean(cut(body, *windows))
    if fillet_r:
        corners = [_polar(r, 36 + 72 * k + s * h) for k in range(5) for s in (-1, 1) for r in (75, 155)]
        body = fillet(body, fillet_r, _edges_at(corners))
    tools = [cyl((0, 0, 139), Z, 35, 72)]
    tools += [cyl((*_polar(57.15, 360 * k / n_lugs), 139), Z, 7.5, 72) for k in range(n_lugs)]
    d = (*_polar(1, valve_deg), 0)
    tools.append(cyl((160 * d[0], 160 * d[1], 100), d, 5.5, 30))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 4 六槽槽轮


def geneva_wheel(n=6, lock_r=32.0, slot_end=40.0, hole_r=28.0):
    body = clean(fuse(cyl((0, 0, 0), Z, 70, 12), cyl((0, 0, 12), Z, 20, 12)))
    step = 360 / n
    tools = []
    for k in range(n):
        a = step * k
        tools.append(rotate_z(fuse(box(slot_end, -5, -1, 80, 5, 13), cyl((slot_end, 0, -1), Z, 5, 14)), a))
        tools.append(cyl((*_polar(80, a + step / 2), -1), Z, lock_r, 14))
        tools.append(cyl((*_polar(hole_r, a + step / 2), -1), Z, 4, 14))
    tools += [cyl((0, 0, -1), Z, 8, 26), cyl((0, 0, 18), X, 2.5, 25)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 5 旋转楼梯


def spiral_staircase(n_steps=12, step_deg=30.0, rise=20.0, post_h=90.0, ccw=True):
    parts = [cyl((0, 0, 0), Z, 150, 10), cyl((0, 0, 0), Z, 30, 280),
             common(BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 280), 30).Shape(), cyl((0, 0, 280), Z, 31, 31))]
    sgn = 1 if ccw else -1
    for k in range(n_steps):
        a0 = sgn * step_deg * k
        a1 = a0 + sgn * step_deg
        top = 30 + rise * k
        parts.append(_fan(140, min(a0, a1), max(a0, a1), top - 8, 8))
        parts.append(cyl((*_polar(130, (a0 + a1) / 2), top), Z, 4, post_h))
    body = clean(_fuse_all(parts))
    tools = [cyl((100, 0, -1), Z, 10, 12)] + [cyl((*_polar(130, 45 + 90 * k), -1), Z, 6, 12) for k in range(4)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 6 发动机活塞（顶面在 z = 70）


def engine_piston(grooves=((61, 63), (55, 57), (47, 51)), relief_depth=2.0, pin_z=30.0, n_drain=8):
    body = revolve([(0, 0), (43, 0), (43, 69), (42, 70), (0, 70)])
    body = cut(body, *[_annulus(39, 45, z0, z1 - z0) for z0, z1 in grooves])
    body = cut(body, cyl((0, 0, -1), Z, 37, 59))
    body = clean(fuse(body, cyl((22, 0, pin_z), X, 15, 18), cyl((-40, 0, pin_z), X, 15, 18)))
    tools = [cyl((-45, 0, pin_z), X, 11, 90)]
    tools += [cyl((0, s * 18, 70 - relief_depth), Z, 15, 5) for s in (1, -1)]
    oil = grooves[-1]
    zc = (oil[0] + oil[1]) / 2
    for k in range(n_drain):
        d = (*_polar(1, 360 * k / n_drain), 0)
        tools.append(cyl((35 * d[0], 35 * d[1], zc), d, 1.5, 6))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 7 三通分配歧管（主管沿 X）


def pipe_manifold(branch_x=(75.0, 150.0, 225.0), branch_open=True, drain=True, flange_phase=22.5):
    parts = [cyl((0, 0, 0), X, 30, 300), cyl((0, 0, 0), X, 70, 16), cyl((284, 0, 0), X, 70, 16)]
    for x in branch_x:
        parts += [cyl((x, 0, 0), Z, 20, 120), cyl((x, 0, 108), Z, 45, 12)]
    if drain:
        parts.append(cyl((150, 0, 0), (0, 0, -1), 15, 50))
    body = clean(fuse(*parts))
    tools = [cyl((-1, 0, 0), X, 24, 302)]
    for x in branch_x:
        tools.append(cyl((x, 0, 0 if branch_open else 26), Z, 15, 121))
        tools += [cyl((x + c, s, 107), Z, 5.5, 14) for c, s in
                  [_polar(35, 45 + 90 * k) for k in range(4)]]
    if drain:
        tools.append(cyl((150, 0, 0), (0, 0, -1), 6, 51))
    for k in range(8):
        a = math.radians(flange_phase + 45 * k)
        y, z = 55 * math.sin(a), -55 * math.cos(a)
        tools += [cyl((-1, y, z), X, 7, 18), cyl((283, y, z), X, 7, 18)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 8 减速器箱体


def gearbox_housing(bores=((-45.0, 23.5), (50.0, 26.0)), wall=8.0, floor=10.0, rib_h=25.0):
    parts = [box(-100, -60, 0, 100, 60, 100), box(-120, -80, 90, 120, 80, 100),
             box(100, -60, 0, 130, 60, 12), box(-130, -60, 0, -100, 60, 12)]
    for x0, _ in bores:
        parts += [cyl((x0, 59, 50), Y, 35, 16), cyl((x0, -59, 50), (0, -1, 0), 35, 16),
                  box(x0 - 4, 59, 0, x0 + 4, 75, rib_h), box(x0 - 4, -75, 0, x0 + 4, -59, rib_h)]
    body = clean(fuse(*parts))
    tools = [box(-100 + wall, -60 + wall, floor, 100 - wall, 60 - wall, 101)]
    tools += [cyl((x0, -76, 50), Y, r, 152) for x0, r in bores]
    tools += [cyl((0, 0, -1), Z, 5.1, floor + 2), cyl((91, 0, 40), X, 7, 10)]
    tools += [cyl((x, y, 89), Z, 5.5, 12) for x in (-110, 0, 110) for y in (-70, 70)]
    tools += [cyl((x, y, -1), Z, 6.5, 14) for x in (-115, 115) for y in (-40, 40)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 9 针状散热器


def heat_sink(pin_h=40.0, pitch=10.0, skip_corners=True, cbore_depth=4.0, pad=60.0):
    parts = [box(-50, -50, 0, 50, 50, 8), box(-pad / 2, -pad / 2, -2, pad / 2, pad / 2, 0)]
    for i in range(-4, 5):
        for j in range(-4, 5):
            if skip_corners and abs(i) == 4 and abs(j) == 4:
                continue
            parts.append(cyl((pitch * i, pitch * j, 8), Z, 3, pin_h))
    body = clean(_fuse_all(parts))
    tools = []
    for x in (-40, 40):
        for y in (-40, 40):
            tools += [cyl((x, y, -3), Z, 2.25, 12), cyl((x, y, 8 - cbore_depth), Z, 4, cbore_depth + 1)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 10 蜂窝减重板


def _hex_prism(cx, cy, af, z0, h, rot=30.0):
    rc = af / math.sqrt(3)
    pts = [(cx + rc * math.cos(math.radians(60 * k + rot)), cy + rc * math.sin(math.radians(60 * k + rot)), z0)
           for k in range(6)]
    return prism(pts, (0, 0, h))


def honeycomb_centers(pitch=19.0, rows=2):
    dy = pitch * math.sqrt(3) / 2
    out = []
    for j in range(-rows, rows + 1):
        xs = [pitch * i for i in range(-4, 5)] if j % 2 == 0 else [pitch * (i + 0.5) for i in range(-4, 4)]
        out += [(x, j * dy) for x in xs]
    return out


def honeycomb_panel(floor=3.0, pitch=19.0, rot=30.0, corner_r=10.0):
    body = _rrect(200, 120, corner_r, 0, 10) if corner_r else box(-100, -60, 0, 100, 60, 10)
    tools = [_hex_prism(x, y, 16, floor, 11, rot) for x, y in honeycomb_centers(pitch)]
    tools += [cyl((x, y, -1), Z, 4.25, 12) for x in (-90, 90) for y in (-50, 50)]
    return clean(_cut_all(body, tools))


# ---------------------------------------------------------------- 11 齿轮泵泵体


def gear_pump_body(center=21.0, pocket_depth=30.0, inlet_side=-1, dowels=((-30, 33), (30, -33))):
    body = _rrect(120, 90, 12, 0, 40)
    s = inlet_side
    body = clean(fuse(body, cyl((0, s * 44, 25), (0, s, 0), 18, 9), cyl((0, -s * 44, 25), (0, -s, 0), 18, 9)))
    floor = 40 - pocket_depth
    tools = [cyl((-center, 0, floor), Z, 25.25, pocket_depth + 1), cyl((center, 0, floor), Z, 25.25, pocket_depth + 1),
             cyl((-center, 0, -1), Z, 10, floor + 2), cyl((center, 0, floor - 8), Z, 7.5, 9),
             cyl((0, s * 54, 25), (0, -s, 0), 12, 49), cyl((0, -s * 54, 25), (0, s, 0), 8, 49)]
    tools += [cyl((x, y, 20), Z, 3.4, 21) for x in (-46, 46) for y in (-32, 32)]
    tools += [cyl((x, y, 28), Z, 3, 13) for x, y in dowels]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 12 蛇形流道水冷板


def cold_plate(depth=7.0, passes=(-36, -18, 0, 18, 36), groove=True, turn_r=9.0):
    body = box(-85, -50, 0, 85, 50, 12)
    z0, h = 12 - depth, depth + 1
    ch = [box(-55, y - 5, z0, 55, y + 5, z0 + h) for y in passes]
    for k in range(len(passes) - 1):
        xc = 55 if k % 2 == 0 else -55
        yc = (passes[k] + passes[k + 1]) / 2
        ring = cut(cyl((xc, yc, z0), Z, turn_r + 5, h), cyl((xc, yc, z0 - 1), Z, turn_r - 5, h + 2))
        half = box(xc, yc - 20, z0 - 1, xc + 20, yc + 20, z0 + h + 1) if xc > 0 else \
            box(xc - 20, yc - 20, z0 - 1, xc, yc + 20, z0 + h + 1)
        ch.append(common(ring, half))
    end_x = 55 if (len(passes) - 1) % 2 == 0 else -55
    ch += [cyl((-55, passes[0], z0), Z, 5, h), cyl((end_x, passes[-1], z0), Z, 5, h)]
    tools = [_fuse_all(ch)]
    if groove:
        tools.append(cut(_rrect(149, 93, 9.5, 10, 3), _rrect(143, 87, 6.5, 9, 5)))
    tools += [cyl((-86, passes[0], 8), X, 3, 31), cyl((86, passes[-1], 8), (-1, 0, 0), 2.5, 31),
              cyl((0, 0, -1), Z, 2, 5)]
    tools += [cyl((x, y, -1), Z, 2.75, 14) for x in (-79, 79) for y in (-40, 0, 40)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 13 华伦桁架梁


def truss_beam(fillet_r=4.0, n_up=5, rib=True, pitch=80.0):
    body = fuse(box(0, 0, 0, 420, 60, 8), cyl((0, 30, 0), Z, 30, 8), cyl((420, 30, 0), Z, 30, 8))
    if rib:
        body = fuse(body, box(0, 0, 8, 420, 8, 16), box(0, 52, 8, 420, 60, 16))
    body = clean(body)
    tris, corners = [], []
    for k in range(n_up):
        x0 = 20 + pitch * k
        tri = [(x0, 10), (x0 + 60, 10), (x0 + 30, 50)]
        tris.append(tri)
        if k < n_up - 1:
            tris.append([(x0 + 40, 50), (x0 + 100, 50), (x0 + 70, 10)])
    for tri in tris:
        corners += tri
    body = clean(cut(body, *[prism([(x, y, -1) for x, y in t], (0, 0, 10)) for t in tris]))
    if fillet_r:
        body = clean(fillet(body, fillet_r, _edges_at(corners)))
    return clean(cut(body, cyl((0, 30, -1), Z, 10, 20), cyl((420, 30, -1), Z, 8, 20)))


# ---------------------------------------------------------------- 14 燕尾榫槽涡轮盘


def turbine_disk(n_slots=30, slot_bottom_w=16.0, fillet_r=5.0, n_holes=10, slot_phase=6.0):
    prof = [(21, 0), (45, 0), (45, 15), (100, 15), (100, 5), (130, 5), (130, 35), (100, 35), (100, 25),
            (45, 25), (45, 40), (21, 40), (20, 39), (20, 1)]
    body = revolve(prof)
    if fillet_r:
        body = fillet(body, fillet_r, _circles_at([(45, 15), (45, 25), (100, 15), (100, 25)]))
    hb = slot_bottom_w / 2
    k = (hb - 5) / 18  # 两侧直线过 (112, ±hb) 与 (130, ±5)
    w_far = hb - k * 24
    slot = prism([(112, -hb, -1), (136, -w_far, -1), (136, w_far, -1), (112, hb, -1)], (0, 0, 42))
    tools = [rotate_z(slot, slot_phase + 360 * i / n_slots) for i in range(n_slots)]
    tools += [cyl((*_polar(70, 360 * i / n_holes), -1), Z, 5, 42) for i in range(n_holes)]
    return clean(_cut_all(body, tools))


# ---------------------------------------------------------------- 15 BT40 刀柄（z = 0 在锥柄大端）


def bt40_holder(groove_depth=3.5, slot_bottom=23.5, bore_depth=50.0, taper_len=65.4):
    small_r = 22.225 - taper_len * 7 / 48
    body = revolve([(0, -60), (19, -60), (20, -59), (20, -15.9), (31.5, -15.9), (31.5, 0), (22.225, 0),
                    (small_r, taper_len), (0, taper_len)])
    rb = 31.5 - groove_depth
    w = (32 - rb) * math.tan(math.radians(30))
    groove = revolve([(rb, -8), (32, -8 - w), (32, -8 + w)])
    tools = [groove, box(-8.05, slot_bottom, -17, 8.05, 40, 1), box(-8.05, -40, -17, 8.05, -slot_bottom, 1),
             cyl((25, 0, -16.9), Z, 4, 7), cyl((0, 0, -61), Z, 10, bore_depth + 1),
             cyl((0, 0, -60 + bore_depth - 1), Z, 3, taper_len - 30 - (-60 + bore_depth) + 2),
             cyl((0, 0, taper_len - 30), Z, 7, 31)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 16 液压阀块


def hydraulic_manifold(gallery_end=20.0, a_port_x=10.0, t_cross=True, cbore=3.0):
    body = box(-50, -40, 0, 50, 40, 60)
    tools = [cyl((-25, 0, 20), Z, 6, 41), cyl((-25, 0, 60 - cbore), Z, 10, cbore + 1),
             cyl((-51, 0, 20), X, 5, gallery_end + 51), cyl((-51, 0, 20), X, 8, 9),
             cyl((a_port_x, 41, 20), (0, -1, 0), 5, 41),
             cyl((25, -15, -1), Z, 7, 46)]
    tools.append(cyl((51, -15, 35), (-1, 0, 0), 5, 31 if t_cross else 25))
    tools += [cyl((x, y, -1), Z, 4.5, 62) for x in (-40, 40) for y in (-30, 30)]
    tools += [cyl((-25 + dx, dy, 50), Z, 2.1, 11) for dx in (-16, 16) for dy in (-16, 16)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 17 火箭尾段


def rocket_fin_can(n_fins=4, fin_phase=45.0, tip_r=100.0, fin_t=4.0, vent_z=250.0):
    parts = [_annulus(36, 40, 0, 300), _annulus(30, 36, 0, 8),
             revolve([(30, 0), (36, 0), (16, -30), (38, -100), (34, -100), (12, -30)])]
    span = tip_r - 40
    lead = lambda r: 110 - (r - 40) * 80 / 60  # noqa: E731  前缘：根部 (40, 110) → 翼尖 (100, 30)
    trail = lambda r: 10 - (r - 40) * 30 / 60  # noqa: E731  后缘：根部 (40, 10) → 翼尖 (100, −20)
    fin = prism([(37, -fin_t / 2, trail(37)), (37, -fin_t / 2, lead(37)),
                 (40 + span, -fin_t / 2, lead(40 + span)), (40 + span, -fin_t / 2, trail(40 + span))],
                (0, fin_t, 0))
    parts += [rotate_z(fin, fin_phase + 360 * k / n_fins) for k in range(n_fins)]
    body = clean(fuse(*parts))
    tools = []
    for k in range(4):
        d = (*_polar(1, 90 * k), 0)
        tools.append(cyl((34 * d[0], 34 * d[1], vent_z), d, 3, 8))
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 18 棘轮


def ratchet_wheel(n_teeth=24, ccw=True, hex_rot=30.0, hole_phase=0.0):
    step = 360 / n_teeth
    pts = []
    for k in range(n_teeth):
        a = step * k if ccw else -step * k
        pts += [(*_polar(50, a), 0), (*_polar(60, a), 0)]
    if not ccw:
        pts = pts[::-1]
    disc = prism(pts, (0, 0, 15))
    body = clean(fuse(disc, cyl((0, 0, 15), Z, 25, 10)))
    tools = [hexagon(24, -1, 27, rot=hex_rot)]
    tools += [cyl((*_polar(33, hole_phase + 60 * k), -1), Z, 7, 17) for k in range(6)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 19 喷注器面板（前面板在 z = 20）


INJECTOR_RINGS = ((20, 8, 0.0, 1.0), (35, 16, 11.25, 1.0), (50, 24, 0.0, 1.0), (65, 32, 5.625, 0.75))


def injector_plate(rings=INJECTOR_RINGS, groove_depth=8.0, dowel_r=57.5):
    body = revolve([(0, 0), (80, 0), (80, 19), (79, 20), (0, 20)])
    tools = [_annulus(r - 3, r + 3, -1, groove_depth + 1) for r, _, _, _ in rings]
    for r, n, ph, hr in rings:
        tools += [cyl((*_polar(r, ph + 360 * k / n), groove_depth - 1), Z, hr, 22 - groove_depth) for k in range(n)]
    tools += [cyl((0, 0, -1), Z, 3, 22), cyl((dowel_r, 0, 14), Z, 2, 7)]
    tools += [cyl((*_polar(72.5, 15 + 30 * k), -1), Z, 4.25, 22) for k in range(12)]
    return clean(_cut_all(body, tools))


# ---------------------------------------------------------------- 20 机械臂连杆


def robot_arm_link(pocket_depth=10.0, slit=True, cable=True, tap_phase=30.0):
    body = fuse(hull2((0, 0), 40, (200, 0), 28, 5, 30), cyl((0, 0, 0), Z, 40, 40))
    body = clean(body)
    tools = [hull2((70, 0), 22, (150, 0), 14, 35 - pocket_depth, pocket_depth + 1),
             hull2((70, 0), 22, (150, 0), 14, 4, pocket_depth + 1),
             cyl((0, 0, -1), Z, 26, 42), cyl((200, 0, -1), Z, 17.5, 42)]
    tools += [cyl((*_polar(33, tap_phase + 60 * k), 28), Z, 2.5, 13) for k in range(6)]
    if cable:
        tools.append(cyl((110, 0, -1), Z, 8, 42))
    if slit:
        tools += [box(200, -1, 4, 229, 1, 36), cyl((219, -30, 20), Y, 3.3, 60)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 21 二级门格海绵


def carpet_squares(size=81.0):
    """二级谢尔宾斯基地毯挖去的 9 个正方形 ((u0, u1), (v0, v1))：中心 1 个大方孔 + 8 个小方孔。"""
    a = size / 3
    b = a / 3
    out = [((a, 2 * a), (a, 2 * a))]
    for i in range(3):
        for j in range(3):
            if (i, j) != (1, 1):
                out.append(((a * i + b, a * i + 2 * b), (a * j + b, a * j + 2 * b)))
    return out


def menger_sponge(size=81.0, level2=True, axes=(0, 1, 2)):
    body = box(0, 0, 0, size, size, size)
    squares = carpet_squares(size) if level2 else carpet_squares(size)[:1]
    tools = []
    for ax in axes:
        for (u0, u1), (v0, v1) in squares:
            if ax == 2:
                tools.append(box(u0, v0, -1, u1, v1, size + 1))
            elif ax == 0:
                tools.append(box(-1, u0, v0, size + 1, u1, v1))
            else:
                tools.append(box(u0, -1, v0, u1, size + 1, v1))
    return clean(_cut_all(body, tools))


# ---------------------------------------------------------------- 22 万向节叉


def u_joint_yoke(gap=48.0, bore_z=90.0, keyway=True, tap_depth=10.0):
    x_in = gap / 2
    parts = [cyl((0, 0, 0), Z, 25, 45), cyl((0, 0, 45), Z, 40, 10)]
    for s in (1, -1):
        x0 = x_in if s > 0 else -x_in - 12
        parts += [box(x0, -20, 45, x0 + 12, 20, bore_z), cyl((x0, 0, bore_z), X, 20, 12)]
    body = clean(fuse(*parts))
    tools = [cyl((0, 0, -1), Z, 12.5, 57), cyl((0, 0, 20), Y, 3.4, 26), cyl((-x_in - 13, 0, bore_z), X, 11, gap + 26)]
    if keyway:
        tools.append(box(-4, 0, -1, 4, 15.8, 56))
    for s in (1, -1):
        for y in (-14.5, 14.5):
            tools.append(cyl((s * (x_in + 13), y, bore_z), (-s, 0, 0), 2.5, tap_depth + 1))
    return clean(cut(body, *tools))
