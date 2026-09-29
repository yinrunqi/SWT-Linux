"""其余 43 道题的参考实体：按题面尺寸用 OCP 直接构造，Z 轴向上。

每个构造函数的默认参数给出一个符合题面的正例；个别参数用来生成针对性改错的反例。
"""

from __future__ import annotations

import math

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeSphere
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf

from .fixtures import box, clean, cut, cyl, fillet, fuse, prism, torus

Z = (0, 0, 1)
X = (1, 0, 0)
Y = (0, 1, 0)


# ---------------------------------------------------------------- helpers


def rotate_z(shape, deg):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(deg))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def common(a, b):
    return BRepAlgoAPI_Common(a, b).Shape()


def revolve(rz):
    """绕 Z 轴旋转 (r, z) 折线围成的截面。"""
    poly = BRepBuilderAPI_MakePolygon()
    for r, z in rz:
        poly.Add(gp_Pnt(r, 0, z))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakeRevol(face, gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2 * math.pi).Shape()


def hull2(c1, r1, c2, r2, z0, h):
    """两圆公切线围成的凸轮廓（在 XY 平面内），沿 Z 拉伸 h。"""
    dx, dy = c2[0] - c1[0], c2[1] - c1[1]
    D = math.hypot(dx, dy)
    ux, uy = dx / D, dy / D
    s = (r1 - r2) / D
    c = math.sqrt(1 - s * s)
    pts = []
    for sign in (1, -1):
        nx, ny = s * ux - sign * c * uy, s * uy + sign * c * ux
        pts.append(((c1[0] + r1 * nx, c1[1] + r1 * ny), (c2[0] + r2 * nx, c2[1] + r2 * ny)))
    (a1, a2), (b1, b2) = pts
    quad = prism([(*a1, z0), (*a2, z0), (*b2, z0), (*b1, z0)], (0, 0, h))
    return fuse(quad, cyl((*c1, z0), Z, r1, h), cyl((*c2, z0), Z, r2, h))


def hexagon(af, z0, h, rot=0.0):
    rc = af / math.sqrt(3)
    pts = [(rc * math.cos(math.radians(60 * k + rot)), rc * math.sin(math.radians(60 * k + rot)), z0) for k in range(6)]
    return prism(pts, (0, 0, h))


def rect_z(x0, y0, x1, y1, z0, h, rot=0.0):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    return prism([(x * ca - y * sa, x * sa + y * ca, z0) for x, y in pts], (0, 0, h))


def line_edges_at(z):
    def pick(c):
        if c.GetType() != GeomAbs_Line:
            return False
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        return abs(p0.Z() - z) < 1e-6 and abs(p1.Z() - z) < 1e-6
    return pick


def vertical_lines(c):
    if c.GetType() != GeomAbs_Line:
        return False
    p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
    return abs(p0.X() - p1.X()) < 1e-9 and abs(p0.Y() - p1.Y()) < 1e-9


def all_edges(shape, r):
    mk = BRepFilletAPI_MakeFillet(shape)
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        mk.Add(r, TopoDS.Edge(exp.Current()))
        exp.Next()
    mk.Build()
    return mk.Shape()


def loft(wire0, wire1):
    th = BRepOffsetAPI_ThruSections(True, False)
    th.AddWire(wire0)
    th.AddWire(wire1)
    th.Build()
    return th.Shape()


def rect_wire(a, b, z):
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in ((-a / 2, -b / 2), (a / 2, -b / 2), (a / 2, b / 2), (-a / 2, b / 2)):
        poly.Add(gp_Pnt(x, y, z))
    poly.Close()
    return poly.Wire()


def circle_wire(r, z):
    edge = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1)), r)).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


# ---------------------------------------------------------------- dev


def stool(edge=30.0, rounded=False):
    seat = box(0, 0, 150, 200, 200, 170)
    legs = [cyl((x, y, 0), Z, 10, 150) for x in (edge, 200 - edge) for y in (edge, 200 - edge)]
    body = clean(fuse(seat, *legs))
    if rounded:
        body = clean(fillet(body, 5, line_edges_at(170)))
    return body


def rounded_stool(edge=30.0):
    return stool(edge, rounded=True)


def storage_box(wall=8.0, fillet_r=3.0, loops="both"):
    body = clean(cut(box(0, 0, 0, 120, 80, 50), box(wall, wall, wall, 120 - wall, 80 - wall, 51)))
    at_top = line_edges_at(50)

    def outer_only(c):
        if not at_top(c):
            return False
        p = c.Value((c.FirstParameter() + c.LastParameter()) / 2)
        return min(abs(p.X()), abs(p.X() - 120), abs(p.Y()), abs(p.Y() - 80)) < 1e-6

    return clean(fillet(body, fillet_r, at_top if loops == "both" else outer_only))


_PIPS = {
    1: [(0, 0)],
    2: [(-5, -5), (5, 5)],
    3: [(-5, -5), (0, 0), (5, 5)],
    4: [(-5, -5), (-5, 5), (5, -5), (5, 5)],
    5: [(-5, -5), (-5, 5), (0, 0), (5, -5), (5, 5)],
    6: [(-5, -5), (-5, 0), (-5, 5), (5, -5), (5, 0), (5, 5)],
}


def standard_die(pit="sphere", faces=(1, 6, 2, 5, 3, 4)):
    """faces：+Z、−Z、+X、−X、+Y、−Y 各面点数。"""
    body = all_edges(box(-10, -10, -10, 10, 10, 10), 1.5)
    frames = [((0, 0, 10), (1, 0, 0), (0, 1, 0), (0, 0, 1)), ((0, 0, -10), (1, 0, 0), (0, 1, 0), (0, 0, -1)),
              ((10, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)), ((-10, 0, 0), (0, 1, 0), (0, 0, 1), (-1, 0, 0)),
              ((0, 10, 0), (1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, -10, 0), (1, 0, 0), (0, 0, 1), (0, -1, 0))]
    tools = []
    for n, (o, u, v, w) in zip(faces, frames):
        for a, b in _PIPS[n]:
            c = tuple(o[k] + a * u[k] + b * v[k] for k in range(3))
            if pit == "sphere":
                tools.append(BRepPrimAPI_MakeSphere(gp_Ax2(gp_Pnt(*c), gp_Dir(*w)), 1.5).Shape())
            else:
                tools.append(cyl(tuple(c[k] - 1.5 * w[k] for k in range(3)), w, 1.5, 2))
    return clean(cut(body, *tools))


def hex_nut(z0=-4.0):
    return clean(cut(hexagon(30, z0, 8), cyl((0, 0, z0 - 1), Z, 5, 10)))


def wrench(handle_w=15.0):
    body = fuse(cyl((0, 0, 0), Z, 20, 8), box(0, -handle_w / 2, 0, 120, handle_w / 2, 8))
    return clean(cut(body, hexagon(13, -1, 10)))


def vase(neck_r=20.0, lower="flare"):
    """lower：flare（底径 60 外扩到腹部）/ straight（下半部 d90 直筒）/ bottom_max（最大径只在底部）。"""
    top = [(neck_r, 120), (neck_r, 125), (27.5, 150), (0, 150)]
    top_in = [(neck_r - 3, 120), (neck_r - 3, 125), (24.5, 150), (24.5, 151), (0, 151)]
    outer, inner = {
        "flare": ([(0, 0), (30, 0), (45, 60), (45, 75)], [(0, 10), (29.5, 10), (42, 60), (42, 75)]),
        "straight": ([(0, 0), (45, 0), (45, 75)], [(0, 10), (42, 10), (42, 75)]),
        "bottom_max": ([(0, 0), (45, 0), (38, 60), (38, 75)], [(0, 10), (42, 10), (35, 60), (35, 75)]),
    }[lower]
    return clean(cut(revolve(outer + top), revolve(inner + top_in)))


def key(n_teeth=5):
    body = fuse(cyl((0, 0, 0), Z, 10, 2), box(8, -4, 0, 50, 4, 2))
    body = cut(body, box(-5, -2.5, -1, 5, 2.5, 3))
    notches = [box(c - 1, 2.5, -1, c + 1, 5, 3) for c in (20 + 6 * k for k in range(n_teeth))]
    return clean(cut(body, *notches))


def gear_disc(n_slots=12):
    body = cut(cyl((0, 0, 0), Z, 30, 8), cyl((0, 0, -1), Z, 6, 10))
    slots = [rect_z(24, -1, 27, 1, -1, 10, rot=k * 360 / n_slots) for k in range(n_slots)]
    return clean(cut(body, *slots))


def pulley(chamfer=True):
    top = [(50, 19), (49, 20)] if chamfer else [(50, 20)]
    return clean(revolve([(12.5, 0), (50, 0), *top, (45, 20), (45, 10), (35, 10), (35, 20), (12.5, 20)]))


def stepped_shaft(shift=0.0):
    z = [v + shift for v in (-45, -5, 25, 45)]
    return clean(revolve([(0, z[0]), (17.5, z[0]), (17.5, z[1]), (25, z[1]), (25, z[2]), (12.5, z[2]),
                          (12.5, z[3]), (0, z[3])]))


# ---------------------------------------------------------------- heldout


def l_bracket(hole2=38.0):
    body = fuse(box(0, 0, 0, 50, 30, 5), box(0, 0, 0, 5, 30, 40))
    holes = [cyl((x, 15, -1), Z, 3, 7) for x in (20, hole2)] + [cyl((-1, 15, 25), X, 3, 7)]
    return clean(cut(body, *holes))


def spacer_ring(chamfers=2):
    top = [(20, 9), (19, 10)] if chamfers == 2 else [(20, 10)]
    return clean(revolve([(10, 0), (19, 0), (20, 1), *top, (10, 10)]))


def mounting_plate(window=(40, 20)):
    body = fillet(box(0, 0, 0, 120, 80, 6), 8, vertical_lines)
    holes = [cyl((x, y, -1), Z, 3.5, 8) for x in (12, 108) for y in (12, 68)]
    w, h = window
    return clean(cut(body, *holes, box(60 - w / 2, 40 - h / 2, -1, 60 + w / 2, 40 + h / 2, 7)))


def stepped_block(hole_from_bottom=False):
    body = fuse(box(0, 0, 0, 80, 50, 15), box(0, 0, 15, 40, 50, 30))
    hole = cyl((60, 25, -1), Z, 5, 9) if hole_from_bottom else cyl((60, 25, 7), Z, 5, 9)
    return clean(cut(body, hole))


def knob(n_grooves=12):
    body = fuse(cyl((0, 0, 0), Z, 15, 20), cyl((0, 0, 20), Z, 5, 5))
    grooves = [rect_z(14, -1, 16, 1, -1, 22, rot=k * 360 / n_grooves) for k in range(n_grooves)]
    return clean(cut(body, *grooves, cyl((0, 0, -1), Z, 3, 13)))


def hex_standoff(depth=6.0):
    return clean(cut(hexagon(8, 0, 20), cyl((0, 0, -1), Z, 1.65, depth + 1), cyl((0, 0, 20 - depth), Z, 1.65, depth + 1)))


def u_channel(pitch=30.0):
    body = cut(box(0, 0, 0, 100, 40, 30), box(-1, 4, 4, 101, 36, 31))
    return clean(cut(body, *[cyl((50 + k * pitch, 20, -1), Z, 4, 6) for k in (-1, 0, 1)]))


def flanged_bushing(flange_t=5.0):
    return clean(revolve([(6, 0), (18, 0), (18, flange_t), (10, flange_t), (10, 30), (6, 30)]))


def wedge_block(low=5.0):
    return clean(prism([(0, 0, 0), (60, 0, 0), (60, 0, low), (0, 0, 20)], (0, 30, 0)))


def spoked_wheel(span=60.0):
    body = cut(cyl((0, 0, 0), Z, 60, 10), cyl((0, 0, -1), Z, 10, 12))
    ring = cut(cyl((0, 0, -1), Z, 50, 12), cyl((0, 0, -2), Z, 20, 14))
    far = 200
    h = math.radians(span / 2)
    wedge = prism([(0, 0, -1), (far * math.cos(h), -far * math.sin(h), -1), (far * math.cos(h), far * math.sin(h), -1)],
                  (0, 0, 12))
    sector = common(ring, wedge)
    return clean(cut(body, *[rotate_z(sector, 45 + 90 * k) for k in range(4)]))


def end_cap(top=3.0):
    return clean(revolve([(4, 15 - top), (25, 15 - top), (25, 0), (30, 0), (30, 15), (4, 15)]))


def link_bar(width=14.0):
    body = fuse(cyl((0, 0, 0), Z, 12, 6), cyl((80, 0, 0), Z, 12, 6), box(0, -width / 2, 0, 80, width / 2, 6))
    return clean(cut(body, cyl((0, 0, -1), Z, 5, 8), cyl((80, 0, -1), Z, 5, 8)))


# ---------------------------------------------------------------- demo / heldout2


def fork_end(ear_center=63.0):
    parts = [cyl((0, 0, 0), Z, 10, 35), box(-20, -12, 35, 20, 12, 45)]
    for x0 in (-20, 12):
        parts += [box(x0, -12, 45, x0 + 8, 12, ear_center), cyl((x0, 0, ear_center), X, 12, 8)]
    body = fuse(*parts)
    return clean(cut(body, cyl((-21, 0, ear_center), X, 5, 42), cyl((0, 0, -1), Z, 3.4, 21)))


def three_lug_housing(n_lugs=3, cbore_depth=9.0):
    lug = fuse(rect_z(0, -11, 48, 11, 0, 10), cyl((48, 0, 0), Z, 11, 10))
    lug = cut(lug, cyl((48, 0, -1), Z, 4.5, 12))
    flange = fuse(cyl((0, 0, 0), Z, 32, 10), *[rotate_z(lug, 360 * k / n_lugs) for k in range(n_lugs)])
    for k in range(n_lugs):
        flange = cut(flange, rotate_z(cyl((48, 0, -1), Z, 4.5, 12), 360 * k / n_lugs))
    body = fuse(flange, cyl((0, 0, 0), Z, 23, 38))
    return clean(cut(body, cyl((0, 0, -1), Z, 15, 40), cyl((0, 0, 38 - cbore_depth), Z, 19, cbore_depth + 1)))


def _stadium(cx, half, r, z0, h):
    return fuse(box(cx - half, -r, z0, cx + half, r, z0 + h), cyl((cx - half, 0, z0), Z, r, h), cyl((cx + half, 0, z0), Z, r, h))


def pillow_block(slot_offset=45.0):
    body = fuse(box(-60, -20, 0, 60, 20, 12), box(-30, -20, 12, 30, 20, 45), cyl((0, -20, 45), Y, 30, 40))
    slots = [_stadium(s * slot_offset, 5, 5.5, -1, 14) for s in (-1, 1)]
    return clean(cut(body, cyl((0, -21, 45), Y, 17.5, 42), *slots))


def twin_arm_rocker(plate_t=8.0):
    plates = [hull2((0, 0), 15, (70, 0), 10, 0, plate_t), hull2((0, 0), 15, (70, 0), 10, 40 - plate_t, plate_t)]
    body = fuse(cyl((0, 0, 0), Z, 15, 40), *plates)
    return clean(cut(body, cyl((0, 0, -1), Z, 6, 42), cyl((70, 0, -1), Z, 4, 42)))


def clevis_bracket(inner_gap=30.0):
    x_in = inner_gap / 2
    parts = [box(-40, -25, 0, 40, 25, 10)]
    for x0 in (-x_in - 10, x_in):
        parts += [box(x0, -15, 10, x0 + 10, 15, 35), cyl((x0, 0, 35), X, 15, 10)]
    body = fuse(*parts)
    holes = [cyl((x, y, -1), Z, 4.5, 12) for x in (-30, 30) for y in (-17, 17)]
    return clean(cut(body, *holes, cyl((-41, 0, 35), X, 6, 82)))


def diamond_flange(lobe=42.0):
    plate = fuse(hull2((0, 0), 25, (lobe, 0), 12, 0, 8), hull2((0, 0), 25, (-lobe, 0), 12, 0, 8))
    body = fuse(plate, cyl((0, 0, 0), Z, 17, 26))
    return clean(cut(body, cyl((lobe, 0, -1), Z, 5, 10), cyl((-lobe, 0, -1), Z, 5, 10), cyl((0, 0, -1), Z, 10, 28)))


def support_seat(center_z=40.0):
    body = fuse(box(-50, -25, 0, 50, 25, 10), box(-25, -10, 10, 25, 10, center_z), cyl((0, -10, center_z), Y, 25, 20))
    holes = [cyl((x, 0, -1), Z, 5, 12) for x in (-38, 38)]
    return clean(cut(body, cyl((0, -11, center_z), Y, 12, 22), *holes))


def crank(boss_side="flush"):
    arm = hull2((0, 0), 18, (60, 0), 11, 0, 10)
    boss = cyl((60, 0, -15), Z, 11, 15) if boss_side == "flush" else cyl((60, 0, 10), Z, 11, 15)
    body = fuse(cyl((0, 0, 0), Z, 18, 24), arm, boss)
    return clean(cut(body, cyl((0, 0, -1), Z, 8, 26), cyl((60, 0, -16), Z, 5, 42)))


# ---------------------------------------------------------------- demo3 / heldout3


def _rib_bracket(L, W, t, up_h, up_t, rib_t, leg):
    x_in = L / 2 - up_t
    body = fuse(box(-L / 2, -W / 2, 0, L / 2, W / 2, t), box(x_in, -W / 2, 0, L / 2, W / 2, up_h),
                prism([(x_in - leg, -rib_t / 2, t), (x_in, -rib_t / 2, t), (x_in, -rib_t / 2, t + leg)], (0, rib_t, 0)))
    return clean(body)


def gusset_bracket(total_h=36.0, rib_t=4.0):
    return _rib_bracket(60, 40, 6, total_h, 6, rib_t, 18)


def corner_rib_stand(total_h=44.0, rib_t=5.0):
    return _rib_bracket(80, 50, 8, total_h, 8, rib_t, 24)


def open_box(wall=4.0):
    return clean(cut(box(0, 0, 0, 80, 50, 40), box(wall, wall, 4, 80 - wall, 50 - wall, 41)))


def _recess_plate(L, W, T, pos, d, top_d, depth, kind="cbore", flip_first=False):
    body = box(0, 0, 0, L, W, T)
    tools = []
    for k, (x, y) in enumerate(pos):
        top = not (flip_first and k == 0)
        tools.append(cyl((x, y, -1), Z, d / 2, T + 2))
        z_face = T if top else 0.0
        sgn = 1 if top else -1
        if kind == "cbore":
            z0 = z_face - depth if top else -1
            tools.append(cyl((x, y, z0), Z, top_d / 2, depth + 1))
        else:
            ax = gp_Ax2(gp_Pnt(x, y, z_face - sgn * depth), gp_Dir(0, 0, sgn))
            tools.append(BRepPrimAPI_MakeCone(ax, d / 2, top_d / 2, depth).Shape())
            tools.append(cyl((x, y, z_face), (0, 0, sgn), top_d / 2, 1))
    return clean(cut(body, *tools))


def counterbore_plate(kind="cbore", flip_first=False):
    pos = [(x, y) for x in (14, 66) for y in (14, 46)]
    return _recess_plate(80, 60, 10, pos, 6, 11, 3.5, kind, flip_first)


def csk_cover(kind="csk", flip_first=False):
    pos = [(x, y) for x in (15, 75) for y in (15, 55)]
    return _recess_plate(90, 70, 12, pos, 8, 14, 4, kind, flip_first)


def side_pad(side=+1, length=20.0):
    x0 = 50 if side > 0 else -length
    return clean(fuse(box(0, 0, 0, 50, 30, 40), cyl((x0, 15, 20), X, 8, length)))


def through_boss(side=+1, length=24.0):
    x0 = 60 if side > 0 else -length
    return clean(fuse(box(0, 0, 0, 60, 36, 48), cyl((x0, 18, 24), X, 10, length)))


def sheet_angle(t=1.5):
    return box(0, 0, 0, 70, 40, t)


def sheet_channel(t=1.2):
    return box(0, 0, 0, 90, 50, t)


def square_to_round(top_r=12.0):
    return loft(rect_wire(30, 30, 0), circle_wire(top_r, 40))


def rect_to_circle(top_r=14.0, offset=0.0):
    w1 = circle_wire(top_r, 50)
    if offset:
        t = gp_Trsf()
        t.SetTranslation(gp_Pnt(0, 0, 0), gp_Pnt(offset, 0, 0))
        w1 = TopoDS.Wire(BRepBuilderAPI_Transform(w1, t, True).Shape())
    return loft(rect_wire(40, 24, 0), w1)


def bent_tube(inward=False):
    tube = torus((0, 0, 0), Z, X, 50, 10, 90)
    y0 = 0 if inward else -5
    return clean(fuse(tube, cyl((50, y0, 0), Y, 22, 5), cyl((y0, 50, 0), X, 22, 5)))

