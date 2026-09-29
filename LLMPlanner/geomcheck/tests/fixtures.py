"""用 OCP 直接构造的测试实体：每题一个符合题面的正例，以及若干针对性改错的反例。

反例中带 ``run`` 字样的，复刻的是 dev 集旧 12 工具运行记录里的实际几何。
Z 轴向上。
"""

from __future__ import annotations

import math

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeTorus,
)
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec

_SIN74 = math.sin(math.radians(74))
_TAN74 = math.tan(math.radians(74))


def box(x0, y0, z0, x1, y1, z1):
    return BRepPrimAPI_MakeBox(gp_Pnt(x0, y0, z0), gp_Pnt(x1, y1, z1)).Shape()


def cyl(p, d, r, h):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p), gp_Dir(*d)), r, h).Shape()


def torus(c, axis, xdir, R, r, angle_deg=360.0):
    ax = gp_Ax2(gp_Pnt(*c), gp_Dir(*axis), gp_Dir(*xdir))
    return BRepPrimAPI_MakeTorus(ax, R, r, math.radians(angle_deg)).Shape()


def prism(points, vec):
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(*vec)).Shape()


def fuse(*shapes):
    out = shapes[0]
    for s in shapes[1:]:
        out = BRepAlgoAPI_Fuse(out, s).Shape()
    return out


def cut(base, *tools):
    for t in tools:
        base = BRepAlgoAPI_Cut(base, t).Shape()
    return base


def clean(shape):
    unify = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    unify.Build()
    return unify.Shape()


def fillet(shape, radius, pick):
    """对满足 pick(curve_adaptor) 的边倒圆。"""
    mk = BRepFilletAPI_MakeFillet(shape)
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    seen = set()
    while exp.More():
        edge = TopoDS.Edge(exp.Current())
        key = edge.__hash__()
        if key not in seen and pick(BRepAdaptor_Curve(edge)):
            seen.add(key)
            mk.Add(radius, edge)
        exp.Next()
    mk.Build()
    return mk.Shape()


def circle_edge(radius, z):
    def pick(c):
        if c.GetType() != GeomAbs_Circle:
            return False
        circ = c.Circle()
        return abs(circ.Radius() - radius) < 1e-6 and abs(circ.Location().Z() - z) < 1e-6

    return pick


# ---------------------------------------------------------------- flange


def flange(n_holes=6, pcd_r=40.0, blind_first=False):
    body = cut(cyl((0, 0, 0), (0, 0, 1), 50, 12), cyl((0, 0, -1), (0, 0, 1), 15, 14))
    for k in range(n_holes):
        a = math.radians(15 + k * 360 / n_holes)
        p = (pcd_r * math.cos(a), pcd_r * math.sin(a))
        if blind_first and k == 0:
            body = cut(body, cyl((p[0], p[1], 4), (0, 0, 1), 4, 9))
        else:
            body = cut(body, cyl((p[0], p[1], -1), (0, 0, 1), 4, 14))
    return clean(body)


# ---------------------------------------------------------------- housing


def housing(wall=5.0, floor=5.0):
    return clean(cut(box(-50, -30, 0, 50, 30, 50), box(-50 + wall, -30 + wall, floor, 50 - wall, 30 - wall, 51)))


# ---------------------------------------------------------------- elbow


def elbow(inward=False, hollow=False):
    tube = torus((0, 0, 0), (0, 0, 1), (1, 0, 0), 40, 8, 90)
    if hollow:
        tube = cut(tube, torus((0, 0, 0), (0, 0, 1), (1, 0, 0), 40, 5, 90))
    y0 = 0 if inward else -4
    f1 = cyl((40, y0, 0), (0, 1, 0), 18, 4)
    f2 = cyl((y0, 40, 0), (1, 0, 0), 18, 4)
    return clean(fuse(tube, f1, f2))


# ---------------------------------------------------------------- socket bolt


def _hex(z0, depth, af=8.0):
    rc = af / math.sqrt(3)
    pts = [(rc * math.cos(math.radians(60 * k)), rc * math.sin(math.radians(60 * k)), z0) for k in range(6)]
    return prism(pts, (0, 0, depth))


def bolt(fillet_at="neck"):
    body = clean(fuse(cyl((0, 0, 0), (0, 0, 1), 5, 50), cyl((0, 0, 50), (0, 0, 1), 9, 6)))
    pick = {"neck": circle_edge(5, 50), "head_bottom": circle_edge(9, 50), "head_top": circle_edge(9, 56)}[fillet_at]
    body = fillet(body, 0.5, pick)
    return clean(cut(body, _hex(52, 5)))


# ---------------------------------------------------------------- mug


def _cup():
    return cut(cyl((0, 0, 0), (0, 0, 1), 35, 90), cyl((0, 0, 10), (0, 0, 1), 30, 81))


def mug(handle="ok"):
    cup = _cup()
    if handle == "ok":  # 竖立的矩形截面环：外径 40、内径 24、厚 8，嵌入杯壁 3mm
        ring = cut(cyl((52, -4, 45), (0, 1, 0), 20, 8), cyl((52, -5, 45), (0, 1, 0), 12, 10))
        return clean(fuse(cup, ring))
    if handle == "run1":  # 绕杯壁竖线旋转 180° 的水平半环，一端扎进杯腔
        return clean(fuse(cup, torus((35, 0, 45), (0, 0, 1), (1, 0, 0), 16, 4, 180)))
    if handle == "run2":  # 完整水平圆环，只在一点与杯壁相切
        return clean(fuse(cup, torus((55, 0, 45), (0, 0, 1), (1, 0, 0), 16, 4)))
    raise ValueError(handle)


# ---------------------------------------------------------------- phone stand


def _base():
    def vertical_corner(c):
        if c.GetType() != GeomAbs_Line:
            return False
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        return abs(p0.X() - p1.X()) < 1e-9 and abs(p0.Y() - p1.Y()) < 1e-9

    base = fillet(box(-55, -39, 0, 55, 39, 8), 4, vertical_corner)
    pads = [box(cx - 12, -5, -1, cx + 12, 5, 1.5) for cx in (-30, 30)]
    return cut(base, *pads)


def phone_stand(plate="ok"):
    """plate: ok（法向厚 6）/ run2（水平方向厚 6）/ run1（竖直、在自身平面内歪斜的纵向薄板）。"""
    base = _base()
    if plate == "run1":
        fin = prism([(-3, -39, 8), (-3, 39, 8), (-3, 7.45, 118), (-3, -70.55, 118)], (6, 0, 0))
        lip = box(-55, 29, 8, 55, 39, 20)
        ribs = [prism([(x, -39, 8), (x, 11, 8), (x, -39, 50)], (3, 0, 0)) for x in (3, -6)]
        slot = box(-10, -8, 8, 10, 8, 36)
        vents = [box(-10, -33, c - 4, 10, 3, c + 4) for c in (50, 68, 86)]
        return clean(cut(fuse(base, fin, lip, *ribs), slot, *vents))
    t_h = 6 / _SIN74 if plate == "ok" else 6.0
    back = 110 / _TAN74
    foot = -55 + t_h
    slab = prism([(-55, -39, 8), (foot, -39, 8), (foot - back, -39, 118), (-55 - back, -39, 118)], (0, 78, 0))
    lip = box(45, -39, 8, 55, 39, 20)
    x_top = foot - 32 / _TAN74 - 1
    ribs = [prism([(foot, y, 8), (-25, y, 8), (x_top, y, 40)], (0, 5, 0)) for y in (34, -39)]
    slot = box(-120, -8, 8, -40, 8, 36)
    vents = [box(-130, -18, c - 4, -40, 18, c + 4) for c in (50, 68, 86)]
    return clean(cut(fuse(base, slab, lip, *ribs), slot, *vents))
