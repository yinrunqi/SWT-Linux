"""极难子集（extreme）20 道题的参考实体：按题面尺寸用 OCP 直接构造。

每个构造函数的默认参数给出一个符合题面的正例；个别参数用来生成针对性改错的反例。
摆放方向与题面坐标一致；rubric 只用零件自身的特征建坐标系。
"""

from __future__ import annotations

import math

import numpy as np
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepLib import BRepLib
from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle, BRepOffsetAPI_MakePipeShell
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeSphere
from OCP.collections import Array1_gp_Pnt2d, HArray1_gp_Pnt
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pln, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec

from .fixtures import box, clean, cut, cyl, fillet, fuse, prism, torus
from .refs import common
from .refs_hard2 import _cut_all, _fuse_all, _polar, _rotate

Z = (0, 0, 1)
X = (1, 0, 0)
Y = (0, 1, 0)


# ---------------------------------------------------------------- helpers


def _poly_wire(pts):
    poly = BRepBuilderAPI_MakePolygon()
    for p in pts:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    return poly.Wire()


def _spline_face(pts, z=0.0):
    """XY 平面内闭合点列（逆时针）插值成的周期 B 样条围成的面。"""
    arr = HArray1_gp_Pnt(1, len(pts))
    for i, (x, y) in enumerate(pts):
        arr.SetValue(i + 1, gp_Pnt(x, y, z))
    interp = GeomAPI_Interpolate(arr, True, 1e-7)
    interp.Perform()
    edge = BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()
    return BRepBuilderAPI_MakeFace(BRepBuilderAPI_MakeWire(edge).Wire()).Face()


def _extrude(face, h):
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, h)).Shape()


def _helix_wire(r, lead, z0, z1, phase_deg, lefthand):
    """绕 Z 轴的等螺距螺旋线：z0 处方位角 phase_deg，左旋时 z 增大方位角减小。"""
    xd = gp_Dir(math.cos(math.radians(phase_deg)), math.sin(math.radians(phase_deg)), 0)
    surf = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1), xd), r)
    h = z1 - z0
    du = 2 * math.pi * h / lead * (-1 if lefthand else 1)
    line = Geom2d_Line(gp_Pnt2d(0, 0), gp_Dir2d(du, h))
    edge = BRepBuilderAPI_MakeEdge(line, surf, 0.0, math.hypot(du, h)).Edge()
    BRepLib.BuildCurves3d_s(edge)
    return BRepBuilderAPI_MakeWire(edge).Wire()


def _thread_tooth(rz, lead, z0, z1, phase_deg, lefthand):
    """轴向截面多边形 rz=[(r, dz)]（dz 相对齿中心）沿螺旋扫掠；z0 处齿中心在方位角 phase_deg。"""
    c, s = math.cos(math.radians(phase_deg)), math.sin(math.radians(phase_deg))
    prof = _poly_wire([(r * c, r * s, z0 + dz) for r, dz in rz])
    spine = _helix_wire(min(r for r, _ in rz), lead, z0, z1, phase_deg, lefthand)
    mk = BRepOffsetAPI_MakePipeShell(spine)
    mk.SetMode(gp_Dir(0, 0, 1))
    mk.Add(prof)
    mk.Build()
    mk.MakeSolid()
    return mk.Shape()


def _cone(p, d, r0, r1, h):
    if abs(r0 - r1) < 1e-9:
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*p), gp_Dir(*d)), r0, h).Shape()
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(*p), gp_Dir(*d)), r0, r1, h).Shape()


def _drill(p, d, r, depth, point=True):
    """从 p 沿单位方向 d 钻孔：圆柱段长 depth，孔底带 118° 钻尖（point=False 为平底）。"""
    d = np.asarray(d, float) / np.linalg.norm(d)
    body = cyl(tuple(p), tuple(d), r, depth)
    if not point:
        return body
    tip = r / math.tan(math.radians(59))
    return fuse(body, _cone(tuple(np.asarray(p) + depth * d), tuple(d), r, 0.0, tip))


def _slot(c0, c1, r, z0, h):
    """XY 平面内两圆心 c0、c1、半径 r 的长圆孔柱体。"""
    (x0, y0), (x1, y1) = c0, c1
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = -uy * r, ux * r
    quad = prism([(x0 + nx, y0 + ny, z0), (x1 + nx, y1 + ny, z0), (x1 - nx, y1 - ny, z0), (x0 - nx, y0 - ny, z0)],
                 (0, 0, h))
    return fuse(quad, cyl((x0, y0, z0), Z, r, h), cyl((x1, y1, z0), Z, r, h))


def _rrect_face(a, b, r, z):
    """中心在原点、a×b、圆角 r 的圆角矩形面（z 高度）。"""
    return _face_of(fillet(box(-a / 2, -b / 2, z, a / 2, b / 2, z + 1), r, _vertical), z)


def _vertical(c):
    if c.GetType() != GeomAbs_Line:
        return False
    p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
    return abs(p0.X() - p1.X()) < 1e-9 and abs(p0.Y() - p1.Y()) < 1e-9


def _face_of(solid, z):
    """实体上 z 高度、法向 −Z 的平面面。"""
    exp = TopExp_Explorer(solid, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face(exp.Current())
        ad = BRepAdaptor_Surface(f)
        if ad.GetType() == GeomAbs_Plane and abs(ad.Plane().Location().Z() - z) < 1e-9 \
                and abs(abs(ad.Plane().Axis().Direction().Z()) - 1) < 1e-9:
            return f
        exp.Next()
    raise ValueError("没有找到该高度的平面")


def _drafted_prism(face_solid, angle_deg, z_neutral, inward_up=True):
    """对柱体的全部竖直侧面拔模：中性面 z_neutral，inward_up=True 时越往上越收。"""
    pln = gp_Pln(gp_Pnt(0, 0, z_neutral), gp_Dir(0, 0, 1))
    draft = BRepOffsetAPI_DraftAngle(face_solid)
    exp = TopExp_Explorer(face_solid, TopAbs_FACE)
    ang = math.radians(angle_deg)
    while exp.More():
        f = TopoDS.Face(exp.Current())
        ad = BRepAdaptor_Surface(f)
        is_cap = ad.GetType() == GeomAbs_Plane and abs(abs(ad.Plane().Axis().Direction().Z()) - 1) < 1e-9
        if not is_cap:
            draft.Add(f, gp_Dir(0, 0, 1), ang if inward_up else -ang, pln)
        exp.Next()
    draft.Build()
    return draft.Shape()


def _edges(shape, pick):
    out, seen = [], set()
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        e = TopoDS.Edge(exp.Current())
        if e.__hash__() not in seen and pick(BRepAdaptor_Curve(e)):
            seen.add(e.__hash__())
            out.append(e)
        exp.Next()
    return out


def _line_between(p, q, tol=1e-6):
    """端点为 p、q（不分先后）的直线棱边。"""
    def pick(c):
        if c.GetType() != GeomAbs_Line:
            return False
        a, b = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        a, b = np.array([a.X(), a.Y(), a.Z()]), np.array([b.X(), b.Y(), b.Z()])
        return (np.linalg.norm(a - p) < tol and np.linalg.norm(b - q) < tol) or \
            (np.linalg.norm(a - q) < tol and np.linalg.norm(b - p) < tol)
    return pick


# ---------------------------------------------------------------- 1 左旋双头蜗杆轴（沿 Z）

WORM_M = 3.0
WORM_P = math.pi * WORM_M  # 轴向齿距
WORM_RA, WORM_RF, WORM_R1 = 18.0, 11.4, 15.0


def worm_shaft(lefthand=True, starts=2, flank_deg=20.0, phase=0.0, groove=True):
    lead = starts * WORM_P
    tan_f = math.tan(math.radians(flank_deg))

    def half(r):
        return WORM_P / 4 - (r - WORM_R1) * tan_f

    r_in = WORM_RF - 1.0
    rz = [(r_in, -half(r_in)), (WORM_RA, -half(WORM_RA)), (WORM_RA, half(WORM_RA)), (r_in, half(r_in))]
    z0, z1 = 25 - lead, 85 + lead
    sign = -1 if lefthand else 1
    # z = 55 处方位角 phase 是齿中心：z0 处该齿中心的方位角
    phi0 = phase - sign * (55 - z0) / lead * 360
    teeth = _fuse_all([_thread_tooth(rz, lead, z0, z1, phi0 + 360 * k / starts, lefthand) for k in range(starts)])
    teeth = common(teeth, cyl((0, 0, 25), Z, WORM_RA + 1, 60))
    body = fuse(cyl((0, 0, 0), Z, 10, 25), cyl((0, 0, 25), Z, WORM_RF, 60), teeth, cyl((0, 0, 85), Z, 10, 30))
    tools = [_drill((0, 0, 115), (0, 0, -1), 2.5, 12)]
    if groove:
        tools.append(cut(cyl((0, 0, 4), Z, 11, 1.3), cyl((0, 0, 3), Z, 9.3, 3.3)))
    tools.append(fuse(box(6.5, -3, 93, 12, 3, 107), cyl((6.5, 0, 93), X, 3, 6), cyl((6.5, 0, 107), X, 3, 6)))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 2 拔模压铸盖（分型面 z = 0，开口朝下）

TAN3 = math.tan(math.radians(3))


def die_cast_cover(outer_draft=3.0, cavity_draft=3.0, boss_draft=1.5, rib_taper=True):
    outer = _drafted_prism(fillet(box(-80, -50, 0, 80, 50, 45), 12, _vertical), outer_draft, 0.0)
    cavity = _drafted_prism(fillet(box(-75, -45, -1, 75, 45, 40), 7, _vertical), cavity_draft, 0.0)
    body = cut(outer, cavity)
    tb = math.tan(math.radians(boss_draft))
    bosses = [_cone((x, y, 4), Z, 6.0, 6.0 + 36.5 * tb, 36.5) for x in (-60, 60) for y in (-30, 30)]
    t0, t1 = (1.5, 1.5 + 20.5 * math.tan(math.radians(1))) if rib_taper else (1.5, 1.5)
    sec = [(-t0, 20), (t0, 20), (t1, 40.5), (-t1, 40.5)]
    ribs = common(fuse(_ruled_rib(sec, "x"), _ruled_rib(sec, "y")), cavity)
    body = fuse(body, *bosses, ribs)
    top_boss = _cone((-40, 0, 45), Z, 20.0, 20.0 - 10 * math.tan(math.radians(2)), 10)
    body = fuse(body, top_boss)
    tools = [cyl((-40, 0, -1), Z, 10, 60)]
    tools += [cyl((x, y, 4), Z, 2.1, 25) for x in (-60, 60) for y in (-30, 30)]
    return _cut_all(body, tools)


def _ruled_rib(sec, along):
    """截面 sec=[(横向, z)] 的直筋，沿 X（along='x'）或 Y 方向贯通 ±90。"""
    if along == "x":
        pts = [(-90, a, b) for a, b in sec]
        return prism(pts, (180, 0, 0))
    pts = [(a, -90, b) for a, b in sec]
    return prism(pts, (0, 180, 0))


# ---------------------------------------------------------------- 3 复合角钻孔块

BLOCK_SLOPE = math.radians(35)
HOLE_A_DIR = np.array([math.tan(math.radians(20)), -math.tan(math.radians(15)), -1.0])
HOLE_A_ENTRY = np.array([40.0, 55.0, 60.0])
HOLE_B_DIR = np.array([0.0, math.sin(BLOCK_SLOPE), -math.cos(BLOCK_SLOPE)])
SLOPE_Y_TOP = 20 / math.tan(BLOCK_SLOPE)


def _hole_b_entry():
    """斜面中线（z = 50）上、使孔 B 轴线与孔 A 轴线相交的入口点，以及交点。"""
    da = HOLE_A_DIR / -HOLE_A_DIR[2]  # 每下降 1mm 的位移
    # 孔 B 轴线在 x = xb 平面内：y − y_mid = tan35·(50 − z)
    y_mid = (50 - 40) / math.tan(BLOCK_SLOPE)
    k = math.tan(BLOCK_SLOPE)
    # A 上 z 下降 v：y = 55 + da_y v，z = 60 − v；代入 B 的直线方程
    v = (HOLE_A_ENTRY[1] - y_mid - k * (50 - 60)) / (k - da[1])
    q = HOLE_A_ENTRY + v * da
    return np.array([q[0], y_mid, 50.0]), q


def compound_block(a_dir=None, b_x=None, a_depth=52.0):
    a_dir = HOLE_A_DIR if a_dir is None else np.asarray(a_dir, float)
    body = cut(box(0, 0, 0, 120, 80, 60), prism([(-1, 0, 40), (-1, SLOPE_Y_TOP, 60.001), (-1, -1, 60.001), (-1, -1, 40)],
                                                (122, 0, 0)))
    b_entry, q = _hole_b_entry()
    if b_x is not None:
        b_entry = np.array([b_x, b_entry[1], b_entry[2]])
    ua = a_dir / np.linalg.norm(a_dir)
    la = a_depth * np.linalg.norm(a_dir) / abs(a_dir[2])
    hole_a = _drill(HOLE_A_ENTRY - 5 * ua, ua, 6.0, la + 5)
    ub = HOLE_B_DIR
    lb = float(np.dot(q - b_entry, ub))
    hole_b = _drill(b_entry - 5 * ub, ub, 5.0, lb + 5)
    hole_c = fuse(cyl((125, 40, 30), (-1, 0, 0), 7.0, 11.0), cyl((125, 40, 30), (-1, 0, 0), 4.0, 55.0))
    return _cut_all(body, [hole_a, hole_b, hole_c])


# ---------------------------------------------------------------- 4 变半径圆角支架


def variable_fillet_bracket(r0=3.0, r1=9.0, chamfer=(6.0, 3.0), csk_deg=90.0):
    body = fuse(box(0, 0, 0, 120, 60, 10), box(0, 50, 0, 120, 60, 80))
    body = clean(body)
    inner = _edges(body, _line_between(np.array([0, 50, 10.0]), np.array([120, 50, 10.0])))
    mk = BRepFilletAPI_MakeFillet(body)
    c = BRepAdaptor_Curve(inner[0])
    u0, u1 = c.FirstParameter(), c.LastParameter()
    if c.Value(u0).X() > 60:
        r0, r1 = r1, r0
    if r0 == r1:
        mk.Add(r0, inner[0])
    else:
        # Add(r0, r1, e) 实际是 S 形过渡；题面要求沿棱线性变化，用密集插值点逼近
        n = 25
        ur = Array1_gp_Pnt2d(1, n)
        for i in range(n):
            f = i / (n - 1)
            ur.SetValue(i + 1, gp_Pnt2d(u0 + f * (u1 - u0), r0 + f * (r1 - r0)))
        mk.Add(ur, inner[0])
    mk.Build()
    body = mk.Shape()
    back_top = _edges(body, _line_between(np.array([0, 60, 80.0]), np.array([120, 60, 80.0])))
    mk = BRepFilletAPI_MakeFillet(body)
    mk.Add(3.0, back_top[0])
    mk.Build()
    body = mk.Shape()
    down, along = chamfer
    body = cut(body, prism([(-1, 50 - 1e-3, 80 - down), (-1, 50 + along, 80 + 1e-3), (-1, 49, 81), (-1, 49, 80 - down)],
                           (122, 0, 0)))
    body = cut(body, prism([(-1, -1, 7), (-1, 3, 11), (-1, -1, 11)], (122, 0, 0)))
    gusset = prism([(56, 50, 10), (56, 10, 10), (56, 50, 60)], (8, 0, 0))
    body = fuse(body, gusset)
    t = math.tan(math.radians(csk_deg / 2))
    tools = []
    for x in (30, 90):
        tools += [cyl((x, 25, -1), Z, 3.3, 12), _cone((x, 25, 10 - 3.25 / t), Z, 3.25, 6.5 + 1 * t, 3.25 / t + 1)]
    tools.append(fuse(box(45, 49, 44, 75, 61, 56), cyl((45, 49, 50), Y, 6, 12), cyl((75, 49, 50), Y, 6, 12)))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 5 滚子从动件盘形凸轮

CAM_RB, CAM_H, CAM_ROLLER = 45.0, 20.0, 12.0


def cam_lift(phi):
    """理论廓线（滚子中心轨迹）的升程，phi 为度。"""
    phi %= 360
    if phi <= 150:
        u = phi / 150
        return CAM_H * (u - math.sin(2 * math.pi * u) / (2 * math.pi))
    if phi <= 180:
        return CAM_H
    if phi <= 300:
        u = (phi - 180) / 120
        return CAM_H * (1 - (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5))
    return 0.0


def cam_profile(n=1440, roller=CAM_ROLLER, normal=True, ccw=True):
    """实际廓线点列（逆时针）：理论廓线沿法向向内偏置滚子半径（normal=False 为沿径向偏置）。"""
    sgn = 1 if ccw else -1
    ph = np.linspace(0, 360, n, endpoint=False)
    R = np.array([CAM_RB + cam_lift(p) for p in ph])
    a = np.radians(sgn * ph)
    pts = np.c_[R * np.cos(a), R * np.sin(a)]
    if not normal:
        r2 = R - roller
        return list(zip(r2 * np.cos(a), r2 * np.sin(a)))
    d = np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)
    t = d / np.linalg.norm(d, axis=1)[:, None]
    nrm = np.c_[t[:, 1], -t[:, 0]] * sgn  # 逆时针点列的外法向
    out = pts - roller * nrm
    return [tuple(p) for p in (out if ccw else out[::-1])]


def roller_cam(normal=True, ccw=True, screw=True):
    plate = _extrude(_spline_face(cam_profile(normal=normal, ccw=ccw)), 14)
    body = fuse(plate, cyl((0, 0, 0), Z, 25, 30))
    tools = [cyl((0, 0, -1), Z, 12.5, 32), box(0, -4, -1, 15.8, 4, 31)]
    if screw:
        tools.append(cyl((0, 0, 22), X, 2.5, 30))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 6 斜齿内齿圈


def _inv(a):
    return math.tan(a) - a


def _interp_edge(pts, periodic=False):
    arr = HArray1_gp_Pnt(1, len(pts))
    for i, p in enumerate(pts):
        arr.SetValue(i + 1, gp_Pnt(*p))
    it = GeomAPI_Interpolate(arr, periodic, 1e-7)
    it.Perform()
    return BRepBuilderAPI_MakeEdge(it.Curve()).Edge()


def _arc_edge(c, r, a0, a1, z):
    am = (a0 + a1) / 2
    pts = [gp_Pnt(c[0] + r * math.cos(a), c[1] + r * math.sin(a), z) for a in (a0, am, a1)]
    return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(*pts).Value()).Edge()


def _wire(edges):
    mk = BRepBuilderAPI_MakeWire()
    for e in edges:
        mk.Add(e)
    return mk.Wire()


def ring_gear_params(z=54, mn=2.5, alpha_n=20.0, beta=15.0, transverse=False):
    """法面模数/压力角给定的斜齿内齿轮；transverse=True 时把它们误当端面参数。"""
    b = math.radians(beta)
    if transverse:
        mt, at = mn, math.radians(alpha_n)
    else:
        mt = mn / math.cos(b)
        at = math.atan(math.tan(math.radians(alpha_n)) / math.cos(b))
    rp = z * mt / 2
    return {"z": z, "rp": rp, "rb": rp * math.cos(at), "at": at, "ra": rp - mn, "rf": rp + 1.25 * mn,
            "tanb": math.tan(b)}


def ring_gear_space_half(P, r):
    """端面上半径 r 处齿槽的半角（内齿轮的齿槽 = 外齿轮的齿）。"""
    return math.pi / (2 * P["z"]) + _inv(P["at"]) - _inv(math.acos(P["rb"] / r))


def _ring_gear_core(P, z0, phase):
    """z0 截面上 内孔 + 全部齿槽 的边界；phase 为一个齿中心的方位角（弧度）。"""
    rs = np.linspace(P["ra"], P["rf"], 16)
    half_a, half_f = ring_gear_space_half(P, P["ra"]), ring_gear_space_half(P, P["rf"])
    pitch = 2 * math.pi / P["z"]

    def pt(r, a):
        return (r * math.cos(a), r * math.sin(a), z0)

    edges = []
    for k in range(P["z"]):
        c = phase + (k + 0.5) * pitch
        edges.append(_arc_edge((0, 0), P["ra"], c - pitch + half_a, c - half_a, z0))
        edges.append(_interp_edge([pt(r, c - ring_gear_space_half(P, r)) for r in rs]))
        edges.append(_arc_edge((0, 0), P["rf"], c - half_f, c + half_f, z0))
        edges.append(_interp_edge([pt(r, c + ring_gear_space_half(P, r)) for r in rs[::-1]]))
    return _wire(edges)


def ring_gear(lefthand=False, transverse=False, chamfer=True, holes_on="teeth"):
    """z=54、mn=2.5、αn=20°、β=15° 右旋内齿圈，齿宽 20，外径 180；z=10 截面上 φ=0 是齿中心。"""
    P = ring_gear_params(transverse=transverse)
    sgn = -1 if lefthand else 1
    z0, z1 = -1.0, 21.0
    prof = _ring_gear_core(P, z0, sgn * (z0 - 10) * P["tanb"] / P["rp"])
    mk = BRepOffsetAPI_MakePipeShell(_helix_wire(P["ra"], 2 * math.pi * P["rp"] / P["tanb"], z0, z1, 0.0, lefthand))
    mk.SetMode(gp_Dir(0, 0, 1))
    mk.Add(prof)
    mk.Build()
    mk.MakeSolid()
    body = cut(cyl((0, 0, 0), Z, 90, 20), mk.Shape())
    tools = [_cone((0, 0, 19), Z, P["ra"], P["ra"] + 2.5, 2.5)] if chamfer else []
    off = 0.0 if holes_on == "teeth" else 180.0 / P["z"]
    for k in range(6):
        a = math.radians(60 * k + off)
        tools.append(cyl((80 * math.cos(a), 80 * math.sin(a), -1), Z, 4.5, 22))
    tools.append(cyl((0, 60, 10), Y, 2, 40))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 7 NACA 4412 翼肋

RIB_C, RIB_M, RIB_P, RIB_T = 240.0, 0.04, 0.4, 0.12


def naca_yt(xb, closed=True):
    a4 = -0.1036 if closed else -0.1015
    return 5 * RIB_T * RIB_C * (0.2969 * math.sqrt(xb) - 0.1260 * xb - 0.3516 * xb ** 2 + 0.2843 * xb ** 3 + a4 * xb ** 4)


def naca_camber(xb):
    """中弧线高度与斜率角（xb 为弦长分数）。"""
    m, p = RIB_M, RIB_P
    if xb < p:
        yc, dy = m / p ** 2 * (2 * p * xb - xb ** 2), 2 * m / p ** 2 * (p - xb)
    else:
        yc, dy = m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * xb - xb ** 2), 2 * m / (1 - p) ** 2 * (p - xb)
    return yc * RIB_C, math.atan(dy)


def naca_point(xb, upper, perpendicular=True, closed=True):
    yt = naca_yt(xb, closed)
    yc, th = naca_camber(xb)
    s = 1 if upper else -1
    if not perpendicular:
        return xb * RIB_C, yc + s * yt
    return xb * RIB_C - s * yt * math.sin(th), yc + s * yt * math.cos(th)


def naca_outline(n=200, perpendicular=True, closed=True):
    """后缘 → 上表面 → 前缘 → 下表面 → 后缘（逆时针）。"""
    xb = (1 - np.cos(np.linspace(0, math.pi, n))) / 2
    up = [naca_point(x, True, perpendicular, closed) for x in xb[::-1]]
    lo = [naca_point(x, False, perpendicular, closed) for x in xb[1:]]
    return up + lo


def naca_rib(perpendicular=True, closed=True, hole_rule=True, notch=True):
    """NACA 4412 翼肋，弦长 240、厚 4；三个减重孔圆心在中弧线上，孔径 = 2·yt − 12；翼梁缺口 x 90~102。"""
    pts = naca_outline(perpendicular=perpendicular, closed=closed)
    if closed:
        pts[-1] = pts[0]
        edges = [_interp_edge([(x, y, 0.0) for x, y in pts])]
    else:
        edges = [_interp_edge([(x, y, 0.0) for x, y in pts]),
                 BRepBuilderAPI_MakeEdge(gp_Pnt(*pts[-1], 0.0), gp_Pnt(*pts[0], 0.0)).Edge()]
    body = _extrude(BRepBuilderAPI_MakeFace(_wire(edges)).Face(), 4)
    tools = []
    for x in (60.0, 110.0, 160.0):
        yc, _ = naca_camber(x / RIB_C)
        d = 2 * naca_yt(x / RIB_C) - 12 if hole_rule else 10.0
        tools.append(cyl((x, yc, -1), Z, d / 2, 6))
    if notch:
        tools.append(box(90, naca_camber(0.4)[0], -1, 102, 40, 5))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 8 涡旋压缩机静盘

SCROLL_A, SCROLL_ALPHA = 3.0, 0.75


def scroll_point(phi, k, a=SCROLL_A):
    """基圆半径 a 的渐开线族：发生线在基圆上的切点方位角 phi，沿发生线的无量纲长度 phi + k。"""
    u = phi + k
    return a * (math.cos(phi) + u * math.sin(phi)), a * (math.sin(phi) - u * math.cos(phi))


def _scroll_band(phi0, phi1, k0, k1, z, h, n=None):
    """phi ∈ [phi0, phi1]、偏移 ∈ [k0, k1] 的渐开线带（两端沿发生线截平）拉伸 h。"""
    n = n or max(40, int((phi1 - phi0) * 24))
    ph = np.linspace(phi0, phi1, n)
    outer = [(*scroll_point(p, k1), z) for p in ph]
    inner = [(*scroll_point(p, k0), z) for p in ph[::-1]]
    edges = [_interp_edge(outer), BRepBuilderAPI_MakeEdge(gp_Pnt(*outer[-1]), gp_Pnt(*inner[0])).Edge(),
             _interp_edge(inner), BRepBuilderAPI_MakeEdge(gp_Pnt(*inner[-1]), gp_Pnt(*outer[0])).Edge()]
    return _extrude(BRepBuilderAPI_MakeFace(_wire(edges)).Face(), h)


def scroll_plate(alpha=SCROLL_ALPHA, phi_end=6 * math.pi, ccw=True, groove=True):
    """底板 Ø150×8，涡旋壁高 30（z 8~38），渐开线基圆 3、壁厚 2·a·alpha，φ = π ~ phi_end；
    壁顶中线上宽 1.5、深 2 的密封槽（φ = 2π ~ phi_end − 0.5）；中心 Ø10 排气孔，4×Ø6.6 安装孔，(0, −70) Ø5 销孔。"""
    plate = cyl((0, 0, 0), Z, 75, 8)
    wrap = _scroll_band(math.pi, phi_end, -alpha, alpha, 8 - 1e-3, 30 + 1e-3)
    body = fuse(plate, wrap)
    tools = [cyl((0, 0, -1), Z, 5, 10), cyl((0, -70, -1), Z, 2.5, 10)]
    for k in range(4):
        a = math.radians(45 + 90 * k)
        tools.append(cyl((70 * math.cos(a), 70 * math.sin(a), -1), Z, 3.3, 10))
    if groove:
        tools.append(_scroll_band(2 * math.pi, phi_end - 0.5, -0.25, 0.25, 36, 3))
    body = _cut_all(body, tools)
    if not ccw:
        body = _mirror(body, X)
    return body


def _mirror(shape, normal=X):
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*normal)))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


# ---------------------------------------------------------------- 9 左旋变螺距压缩弹簧

SPRING_PITCH = [(0.0, 5.0), (0.75, 5.0), (1.25, 12.0), (5.75, 12.0), (6.25, 5.0), (7.0, 5.0)]


def spring_z(n, table=SPRING_PITCH):
    """螺距随圈数分段线性变化时，第 n 圈处中心线高度 = ∫ pitch dn。"""
    z = 0.0
    for (n0, p0), (n1, p1) in zip(table, table[1:]):
        if n <= n0:
            break
        m = min(n, n1)
        pm = p0 + (p1 - p0) * (m - n0) / (n1 - n0)
        z += (p0 + pm) / 2 * (m - n0)
    return z


def spring(lefthand=True, table=SPRING_PITCH, ground=True, wire=4.0, mean_d=40.0):
    # 右旋直接扫掠时修正 Frenet 标架会让管体体积偏差约 1%，改为镜像左旋结果
    if not lefthand:
        return _mirror(spring(True, table, ground, wire, mean_d), Y)
    turns = table[-1][0]
    ns = np.linspace(0, turns, int(turns * 96) + 1)
    R = mean_d / 2
    pts = [(R * math.cos(2 * math.pi * n), -R * math.sin(2 * math.pi * n), spring_z(n, table)) for n in ns]
    spine = BRepBuilderAPI_MakeWire(_interp_edge(pts)).Wire()
    t0 = np.array(pts[1]) - np.array(pts[0])
    circ = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(*pts[0]), gp_Dir(*t0)), wire / 2)).Edge()
    mk = BRepOffsetAPI_MakePipeShell(spine)
    mk.SetMode(False)
    mk.Add(BRepBuilderAPI_MakeWire(circ).Wire())
    mk.Build()
    mk.MakeSolid()
    body = mk.Shape()
    if ground:
        top = spring_z(turns, table)
        body = common(body, box(-R - 10, -R - 10, 0, R + 10, R + 10, top))
    return body


# ---------------------------------------------------------------- 10 相切轮廓连杆

LINK_A, LINK_B, LINK_C = ((0.0, 0.0), 25.0), ((140.0, 0.0), 15.0), ((60.0, 70.0), 12.0)


def link_geometry(r_bc=80.0, r_ca=50.0):
    """外轮廓关键点：A、B 下侧外公切线，B–C 凹弧 R80，C–A 凹弧 R50（凹弧圆心在三角形 ABC 之外）。"""
    (a, ra), (b, rb), (c, rc) = LINK_A, LINK_B, LINK_C
    tri = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)

    def far_center(p1, r1, p2, r2, R):
        (x1, y1), (x2, y2) = p1, p2
        d1, d2 = R + r1, R + r2
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        s = (d1 ** 2 - d2 ** 2 + L ** 2) / (2 * L)
        h = math.sqrt(d1 ** 2 - s ** 2)
        mx, my = x1 + s * dx / L, y1 + s * dy / L
        cands = [(mx - h * dy / L, my + h * dx / L), (mx + h * dy / L, my - h * dx / L)]
        return max(cands, key=lambda q: math.hypot(q[0] - tri[0], q[1] - tri[1]))

    obc, oca = far_center(b, rb, c, rc, r_bc), far_center(c, rc, a, ra, r_ca)

    def touch(center, r, o):
        ang = math.atan2(o[1] - center[1], o[0] - center[0])
        return ang, (center[0] + r * math.cos(ang), center[1] + r * math.sin(ang))

    # A、B 下侧外公切线：法向 n 满足 n·(B−A) = ra − rb，且指向 −Y 一侧
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    th = math.atan2(dy, dx)
    beta = math.acos((ra - rb) / L)
    nang = th - beta
    t_a = (a[0] + ra * math.cos(nang), a[1] + ra * math.sin(nang))
    t_b = (b[0] + rb * math.cos(nang), b[1] + rb * math.sin(nang))
    return {"obc": obc, "oca": oca, "nang": nang, "t_a": t_a, "t_b": t_b,
            "bc_b": touch(b, rb, obc), "bc_c": touch(c, rc, obc), "ca_c": touch(c, rc, oca), "ca_a": touch(a, ra, oca)}


def link_lever(r_bc=80.0, r_ca=50.0, slot=True, keyway=True):
    """板厚 12；A 轮毂 Ø50 高 20、孔 Ø25 带键槽（宽 8，槽底距轴 15.8，朝 +Y）；B 轮毂 Ø30 高 16、孔 Ø15；
    C 孔 Ø10；以 A 为圆心、R72、宽 8、方位 12°~32° 的圆弧槽。"""
    g = link_geometry(r_bc, r_ca)
    (a, ra), (b, rb), (c, rc) = LINK_A, LINK_B, LINK_C
    z = 0.0
    edges = [
        BRepBuilderAPI_MakeEdge(gp_Pnt(*g["t_a"], z), gp_Pnt(*g["t_b"], z)).Edge(),
        _arc_edge(b, rb, *_ccw_span(b, g["t_b"], g["bc_b"][1]), z),
        _arc_edge(g["obc"], r_bc, *_ccw_span(g["obc"], g["bc_b"][1], g["bc_c"][1], concave=True), z),
        _arc_edge(c, rc, *_ccw_span(c, g["bc_c"][1], g["ca_c"][1]), z),
        _arc_edge(g["oca"], r_ca, *_ccw_span(g["oca"], g["ca_c"][1], g["ca_a"][1], concave=True), z),
        _arc_edge(a, ra, *_ccw_span(a, g["ca_a"][1], g["t_a"]), z),
    ]
    body = _extrude(BRepBuilderAPI_MakeFace(_wire(edges)).Face(), 12)
    body = fuse(body, cyl((*a, 0), Z, ra, 20), cyl((*b, 0), Z, rb, 16))
    tools = [cyl((*a, -1), Z, 12.5, 22), cyl((*b, -1), Z, 7.5, 18), cyl((*c, -1), Z, 5, 14)]
    if keyway:
        tools.append(box(-4, 0, -1, 4, 15.8, 21))
    if slot:
        tools.append(_arc_slot(a, 72, 4, 12, 32, -1, 14))
    return _cut_all(body, tools)


def _ccw_span(center, p, q, concave=False):
    """圆心 center 上从 p 到 q 的弧的起止角：凸弧逆时针走，凹弧（轮廓逆时针时）顺时针走，返回 (a0, a1)。"""
    a0 = math.atan2(p[1] - center[1], p[0] - center[0])
    a1 = math.atan2(q[1] - center[1], q[0] - center[0])
    if concave:
        while a1 > a0:
            a1 -= 2 * math.pi
    else:
        while a1 < a0:
            a1 += 2 * math.pi
    return a0, a1


def _arc_slot(center, R, half_w, deg0, deg1, z0, h):
    a0, a1 = math.radians(deg0), math.radians(deg1)
    cx, cy = center
    edges = [
        _arc_edge(center, R + half_w, a0, a1, z0),
        _arc_edge((cx + R * math.cos(a1), cy + R * math.sin(a1)), half_w, a1, a1 + math.pi, z0),
        _arc_edge(center, R - half_w, a1, a0, z0),
        _arc_edge((cx + R * math.cos(a0), cy + R * math.sin(a0)), half_w, a0 + math.pi, a0 + 2 * math.pi, z0),
    ]
    return _extrude(BRepBuilderAPI_MakeFace(_wire(edges)).Face(), h)


# ---------------------------------------------------------------- 11 三维折弯管路

PIPE_PI = [(0.0, 0.0, 0.0), (150.0, 0.0, 0.0), (150.0, 120.0, 0.0), (230.0, 200.0, 140.0)]
PIPE_BEND_R = (45.0, 60.0)


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def pipe_bends(pts=PIPE_PI, radii=PIPE_BEND_R):
    """每个中间交点处的弯头：入/出方向 u、v，转角 θ，切线长 T = R·tan(θ/2)，切点 A、B，弯心 C。"""
    pts = [np.asarray(p, float) for p in pts]
    out = []
    for i, R in zip(range(1, len(pts) - 1), radii):
        u, v = _unit(pts[i] - pts[i - 1]), _unit(pts[i + 1] - pts[i])
        th = math.acos(float(np.clip(np.dot(u, v), -1, 1)))
        T = R * math.tan(th / 2)
        C = pts[i] + R / math.cos(th / 2) * _unit(v - u)
        out.append({"P": pts[i], "u": u, "v": v, "theta": th, "R": R, "T": T,
                    "A": pts[i] - T * u, "B": pts[i] + T * v, "C": C})
    return out


def pipe_flange_holes(n_pcd_r, axis, ref, angles_deg, origin):
    """绕 axis 的螺栓孔中心：从 ref（与 axis 垂直）起按右手方向转 angles。"""
    axis, ref = _unit(axis), _unit(ref)
    side = np.cross(axis, ref)
    return [origin + n_pcd_r * (math.cos(math.radians(a)) * ref + math.sin(math.radians(a)) * side) for a in angles_deg]


def pipe_route(pts=PIPE_PI, radii=PIPE_BEND_R, branch_dir=-1, end_holes_straddle=True):
    """外径 30、内径 24 的管子按交点 P0…P3 走线，P1 弯曲半径 45、P2 弯曲半径 60（管中心线）；
    P0 端法兰 Ø80×10、4×Ø9 于 PCD60（与 +Z 成 45°）；P3 端法兰 Ø90×12、4×Ø11 于 PCD68，跨 P1P2P3 平面布置；
    P1P2 段 y = 60 处向 −Z 的支管 Ø20/Ø14，管口在 z = −70。"""
    pts = [np.asarray(p, float) for p in pts]
    bends = pipe_bends(pts, radii)
    starts = [pts[0]] + [b["B"] for b in bends]
    ends = [b["A"] for b in bends] + [pts[-1]]

    def tube(r, ext):
        parts = []
        for k, (s, e) in enumerate(zip(starts, ends)):
            d = _unit(e - s)
            s2 = s - (ext * d if k == 0 else 0)
            L = float(np.linalg.norm(e - s2)) + (ext if k == len(starts) - 1 else 0)
            parts.append(cyl(tuple(s2), tuple(d), r, L))
        for b in bends:
            n = _unit(np.cross(b["u"], b["v"]))
            parts.append(torus(tuple(b["C"]), tuple(n), tuple(_unit(b["A"] - b["C"])), b["R"], r,
                               math.degrees(b["theta"])))
        return _fuse_all(parts)

    v0, v3 = _unit(pts[1] - pts[0]), _unit(pts[-1] - pts[-2])
    outer = tube(15.0, 0.0)
    f0 = cyl(tuple(pts[0]), tuple(v0), 40, 10)
    f3 = cyl(tuple(pts[-1] - 12 * v3), tuple(v3), 45, 12)
    br_o = cyl((150, 60, 0), (0, 0, branch_dir), 10, 70)
    body = _fuse_all([outer, f0, f3, br_o])
    tools = [tube(12.0, 2.0), cyl((150, 60, 0), (0, 0, branch_dir), 7, 71)]
    for c in pipe_flange_holes(30, v0, Y, (45, 135, 225, 315), pts[0]):
        tools.append(cyl(tuple(c - v0), tuple(v0), 4.5, 12))
    e_in = _unit((pts[-2] - pts[-3]) - np.dot(pts[-2] - pts[-3], v3) * v3)
    angs = (45, 135, 225, 315) if end_holes_straddle else (0, 90, 180, 270)
    for c in pipe_flange_holes(34, v3, e_in, angs, pts[-1]):
        tools.append(cyl(tuple(c - 13 * v3), tuple(v3), 5.5, 14))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 12 球形封头接管


def _sph_dir(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def dome_head(n1=(40.0, 30.0), n3_radial=False, flange_holes_deg=22.5, top_boss=True):
    """SR80/SR72 半球封头 + 筒节 Ø160/Ø144（z −30~0）+ 底法兰 Ø200（z −40~−30）、8×Ø14 于 PCD180；
    N1 径向接管 Ø30/Ø20 端面距球心 110；N2 径向 Ø24/Ø16（纬度 60°、经度 210°）端面距球心 105；
    N3 水平接管 Ø28/Ø18，轴线 y = −30、z = 40，沿 +X 端面在 x = 100；顶部 Ø50/Ø30 竖直接管顶面 z = 95。"""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    sph = lambda r: BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), r).Shape()
    dome = common(sph(80), box(-81, -81, 0, 81, 81, 81))
    a1, a2 = _sph_dir(*n1), _sph_dir(60, 210)
    parts = [cyl((0, 0, -30), Z, 80, 30), cyl((0, 0, -40), Z, 100, 10),
             cyl((0, 0, 0), tuple(a1), 15, 110), cyl((0, 0, 0), tuple(a2), 12, 105)]
    if n3_radial:
        a3 = _unit((62.45, -30, 40))
        parts.append(cyl((0, 0, 0), tuple(a3), 14, 100))
    else:
        parts.append(cyl((0, -30, 40), X, 14, 100))
    if top_boss:
        parts.append(cyl((0, 0, 0), Z, 25, 95))
    body = _fuse_all([dome, *parts])
    tools = [sph(72), cyl((0, 0, -41), Z, 72, 41), cyl((0, 0, 0), tuple(a1), 10, 111), cyl((0, 0, 0), tuple(a2), 8, 106)]
    tools.append(cyl((0, 0, 0), tuple(a3), 9, 101) if n3_radial else cyl((0, -30, 40), X, 9, 101))
    if top_boss:
        tools.append(cyl((0, 0, 0), Z, 15, 96))
    for k in range(8):
        a = math.radians(flange_holes_deg + 45 * k)
        tools.append(cyl((90 * math.cos(a), 90 * math.sin(a), -41), Z, 7, 12))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 13 钣金折弯支架（K 因子）

SHEET_T, SHEET_R, SHEET_K = 2.0, 3.0, 0.4


def sheet_flat_to_3d(s, k=SHEET_K, lip_angle=45.0):
    """展开长度坐标 s（从底板前缘起）→ 中性层/中面上的 (y, z) 与该处板面法向。"""
    t, R = SHEET_T, SHEET_R
    ba1 = math.pi / 2 * (R + k * t)
    ba2 = math.radians(lip_angle) * (R + k * t)
    s1, s2, s3 = 55.0, 55.0 + ba1 + 45.0, 55.0 + ba1 + 45.0 + ba2
    if s <= s1:
        return np.array([s, t / 2]), np.array([0.0, 1.0])
    if s1 + ba1 <= s <= s2:
        return np.array([60 - t / 2, 5 + s - (s1 + ba1)]), np.array([1.0, 0.0])
    if s >= s3:
        a = math.radians(lip_angle)
        m0 = np.array([55 + (R + t / 2) * math.cos(a), 50 + (R + t / 2) * math.sin(a)])
        return m0 + (s - s3) * np.array([-math.sin(a), math.cos(a)]), np.array([math.cos(a), math.sin(a)])
    raise ValueError("孔不能落在折弯区")


def sheet_bracket(k=SHEET_K, lip_angle=45.0):
    """板厚 2、内弯半径 3、宽 80（x ±40）：底板外侧 y 0~60 的前段、竖板外侧面 y = 60、竖板平直段 z 5~50、
    向 −Y 折 45° 的唇边平直段 20；孔位按展开图（K = 0.4）给出：Ø8 @ s25、2×Ø6 @ s80 x ±25、Ø5 @ s122。"""
    t, R = SHEET_T, SHEET_R
    a = math.radians(lip_angle)
    c, s = math.cos(a), math.sin(a)
    lo, li = 5.0 * np.array([c, s]) + (55, 50), 3.0 * np.array([c, s]) + (55, 50)
    d = np.array([-s, c])
    edges = [
        BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(55, 0, 0)).Edge(),
        _arc_edge((55, 5), 5, -math.pi / 2, 0, 0),
        BRepBuilderAPI_MakeEdge(gp_Pnt(60, 5, 0), gp_Pnt(60, 50, 0)).Edge(),
        _arc_edge((55, 50), 5, 0, a, 0),
        BRepBuilderAPI_MakeEdge(gp_Pnt(*lo, 0), gp_Pnt(*(lo + 20 * d), 0)).Edge(),
        BRepBuilderAPI_MakeEdge(gp_Pnt(*(lo + 20 * d), 0), gp_Pnt(*(li + 20 * d), 0)).Edge(),
        BRepBuilderAPI_MakeEdge(gp_Pnt(*(li + 20 * d), 0), gp_Pnt(*li, 0)).Edge(),
        _arc_edge((55, 50), 3, a, 0, 0),
        BRepBuilderAPI_MakeEdge(gp_Pnt(58, 50, 0), gp_Pnt(58, 5, 0)).Edge(),
        _arc_edge((55, 5), 3, 0, -math.pi / 2, 0),
        BRepBuilderAPI_MakeEdge(gp_Pnt(55, 2, 0), gp_Pnt(0, 2, 0)).Edge(),
        BRepBuilderAPI_MakeEdge(gp_Pnt(0, 2, 0), gp_Pnt(0, 0, 0)).Edge(),
    ]
    prof = _extrude(BRepBuilderAPI_MakeFace(_wire(edges)).Face(), 80)
    trsf = gp_Trsf()
    trsf.SetValues(0, 0, 1, -40, 1, 0, 0, 0, 0, 1, 0, 0)
    body = BRepBuilderAPI_Transform(prof, trsf, True).Shape()
    tools = []
    for sv, xs, r in ((25.0, (0.0,), 4.0), (80.0, (-25.0, 25.0), 3.0), (122.0, (0.0,), 2.5)):
        (yy, zz), n = sheet_flat_to_3d(sv, k, lip_angle)
        n3 = np.array([0.0, n[0], n[1]])
        for x in xs:
            p = np.array([x, yy, zz]) - 3 * n3
            tools.append(cyl(tuple(p), tuple(n3), r, 6))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 14 铸造三通歧管（马鞍圆角）


def manifold(saddle_r=5.0, flange_r=3.0, side_x=40.0):
    """主管 Ø50（x ±80）、两端法兰 Ø90×12、4×Ø11 于 PCD70（与 +Z 成 45°）；竖直支管 Ø36 到 z = 60；
    侧支管 Ø30 在 x = 40 沿 +Y 到 y = 55；支管与主管相贯处 R5 圆角，法兰内侧与主管 R3 圆角；
    孔：主管 Ø34 贯通，竖直 Ø22、侧向 Ø18 通入主孔。"""
    body = _fuse_all([cyl((-80, 0, 0), X, 25, 160), cyl((-80, 0, 0), X, 45, 12), cyl((68, 0, 0), X, 45, 12),
                                                     cyl((0, 0, 0), Z, 18, 60), cyl((side_x, 0, 0), Y, 15, 55)])
    mk = BRepFilletAPI_MakeFillet(body)
    exp = TopExp_Explorer(body, TopAbs_EDGE)
    seen = set()
    while exp.More():
        e = TopoDS.Edge(exp.Current())
        key = e.__hash__()
        if key not in seen:
            seen.add(key)
            ad = BRepAdaptor_Curve(e)
            if ad.GetType() not in (GeomAbs_Line, GeomAbs_Circle):
                if saddle_r:
                    mk.Add(saddle_r, e)
            elif ad.GetType() == GeomAbs_Circle and abs(ad.Circle().Radius() - 25) < 1e-6 \
                    and abs(abs(ad.Circle().Location().X()) - 68) < 1e-6 and flange_r:
                mk.Add(flange_r, e)
        exp.Next()
    mk.Build()
    body = mk.Shape()
    tools = [cyl((-81, 0, 0), X, 17, 162), cyl((0, 0, 0), Z, 11, 61), cyl((side_x, 0, 0), Y, 9, 56)]
    for x0 in (-81, 67):
        for k in range(4):
            a = math.radians(45 + 90 * k)
            tools.append(cyl((x0, 35 * math.sin(a), 35 * math.cos(a)), X, 5.5, 14))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 15 圆角抽壳外壳


def shell_enclosure(wall=2.5, top_r=6.0, lip=True, vents=True, bosses=True):
    """120×80×40 盒：竖直棱 R10、顶面棱 R6，向内抽壳 2.5、底面开口；底边外侧止口（内缩 1.2、高 2）；
    +X 端 5 条宽 3 的通风槽（y = 0、±6、±12，x ≥ 40、z ≥ 28 的部分切穿）；4 个 Ø8 螺钉柱 (±45, ±25) 从内顶面
    伸到 z = 5，柱底 Ø2.5 孔深 12。"""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.collections import List_TopoDS_Shape
    body = fillet(box(-60, -40, 0, 60, 40, 40), 10.0, _vertical)

    def top_edge(c):
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        return abs(p0.Z() - 40) < 1e-9 and abs(p1.Z() - 40) < 1e-9

    if top_r:
        body = fillet(body, top_r, top_edge)
    bottom = _face_of(body, 0.0)
    faces = List_TopoDS_Shape()
    faces.Append(bottom)
    mk = BRepOffsetAPI_MakeThickSolid()
    mk.MakeThickSolidByJoin(body, faces, -wall, 1e-4)
    mk.Build()
    body = mk.Shape()
    if bosses:
        posts = [cyl((x, y, 5), Z, 4, 34) for x in (-45, 45) for y in (-25, 25)]
        body = _fuse_all([body, *posts])
    tools = []
    if lip:
        ring = cut(_extrude(_rrect_face(124, 84, 12, -1), 3), _extrude(_rrect_face(117.6, 77.6, 8.8, -1), 3))
        tools.append(ring)
    if vents:
        tools += [box(40, y - 1.5, 28, 70, y + 1.5, 50) for y in (-12, -6, 0, 6, 12)]
    if bosses:
        tools += [cyl((x, y, 4), Z, 1.25, 13) for x in (-45, 45) for y in (-25, 25)]
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 16 静平衡飞轮


def _circle_edges(r, z_abs):
    def pick(c):
        if c.GetType() != GeomAbs_Circle:
            return False
        circ = c.Circle()
        return abs(circ.Radius() - r) < 1e-6 and abs(abs(circ.Location().Z()) - z_abs) < 1e-6
    return pick


def flywheel_balance_d(keyway=True):
    """r = 80、厚 30 的平衡孔直径：抵消 r = 92 的 Ø12 定位孔（厚 30）和键槽（宽 8、到 r = 18.3、长 40）的一次矩。"""
    m = math.pi * 36 * 30 * 92
    if keyway:
        m += 20 * ((18.3 ** 2 - 225) * 8 + 2 * 4 ** 3 / 3)
    return 2 * math.sqrt(m / (math.pi * 30 * 80))


def _window_tool(spoke_w, r_in, r_out, corner_r):
    """辐条 0° 与 60° 之间的窗口：r_in~r_out 的环带，离两条辐条中心线都不小于 spoke_w / 2，竖直棱倒 corner_r。"""
    h = spoke_w / 2
    n2 = np.array([math.sin(math.pi / 3), -math.cos(math.pi / 3)])
    u2 = np.array([math.cos(math.pi / 3), math.sin(math.pi / 3)])
    corner = np.linalg.solve(np.array([[0.0, 1.0], n2]), [h, h])
    far1, far2 = np.array([200.0, h]), 200 * u2 + h * n2
    wedge = prism([(*corner, -8), (*far1, -8), (*far2, -8)], (0, 0, 16))
    ring = cut(cyl((0, 0, -8), Z, r_out, 16), cyl((0, 0, -9), Z, r_in, 18))
    tool = common(ring, wedge)
    return fillet(tool, corner_r, _vertical) if corner_r else tool


def flywheel(balance_d=None, keyway=True, corner_r=5.0, web_fillet=4.0, spoke_w=12.0):
    """轮毂 Ø60×40、腹板 r 30~70 厚 12、轮缘 r 70~100 厚 30，腹板与轮毂/轮缘交角 R4；6 个窗口在 r 38~62、
    辐条（0° 起每 60°）宽 12、窗口竖直棱 R5；Ø30 孔 + 宽 8 键槽（0° 方向，槽底到 r = 18.3）；
    r = 92、0° 的 Ø12 定位孔，r = 80、180° 的静平衡孔。"""
    if balance_d is None:
        balance_d = flywheel_balance_d(keyway=True)
    rim = cut(cyl((0, 0, -15), Z, 100, 30), cyl((0, 0, -16), Z, 70, 32))
    body = clean(_fuse_all([cyl((0, 0, -20), Z, 30, 40), cyl((0, 0, -6), Z, 70, 12), rim]))
    if web_fillet:
        body = fillet(body, web_fillet, lambda c: _circle_edges(30, 6)(c) or _circle_edges(70, 6)(c))
    win = _window_tool(spoke_w, 38.0, 62.0, corner_r)
    tools = [_rotate(win, Z, 60 * k) for k in range(6)]
    tools += [cyl((0, 0, -21), Z, 15, 42), cyl((92, 0, -16), Z, 6, 32)]
    if balance_d:
        tools.append(cyl((-80, 0, -16), Z, balance_d / 2, 32))
    if keyway:
        tools.append(box(0, -4, -21, 18.3, 4, 21))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 17 英制图纸标注板

INCH = 25.4
DRILL_H = 0.266 * INCH  # 字母钻 H
DRILL_21 = 0.159 * INCH  # 10-32 UNF 底孔钻 #21


def _inch_chamfered_plate(t, r, ch):
    """6.000 × 4.000 × t 的板，竖直棱 R r，顶面外缘 ch × 45° 倒角（全部按 mm）。"""
    W, H = 6 * INCH, 4 * INCH
    plate = fillet(box(0, 0, 0, W, H, t), r, _vertical)
    if not ch:
        return plate
    mk = BRepFilletAPI_MakeChamfer(plate)
    for e in _edges(plate, lambda c: abs(c.Value(c.FirstParameter()).Z() - t) < 1e-9
                    and abs(c.Value(c.LastParameter()).Z() - t) < 1e-9):
        mk.Add(ch, e)
    mk.Build()
    return mk.Shape()


def inch_plate(corner_d=DRILL_H, tap_d=DRILL_21, csk_deg=82.0, cb_depth=0.313, chamfer=0.030, bc_start=90.0):
    """尺寸均为英寸：板 6 × 4 × .5，R.25；4×H 钻通孔于 (.375, .375) 等；Ø.531 通孔 + Ø.812↧.313 沉孔于 (1.5, 2)；
    Ø.391 通孔 + Ø.750×82° 锥形沉孔于 (4.5, 2)；3×10-32 底孔 #21↧.375（118° 钻尖）于 Ø1.250 圆、起始 90°；
    .250 宽、中心距 1.000 的长圆通孔于 (3, .75)；顶面外缘 .030×45° 倒角。"""
    t = 0.5 * INCH
    body = _inch_chamfered_plate(t, 0.25 * INCH, chamfer * INCH)
    tools = []
    for x, y in ((0.375, 0.375), (5.625, 0.375), (0.375, 3.625), (5.625, 3.625)):
        tools.append(cyl((x * INCH, y * INCH, -1), Z, corner_d / 2, t + 2))
    cb = (1.5 * INCH, 2.0 * INCH)
    tools += [cyl((*cb, -1), Z, 0.531 * INCH / 2, t + 2), cyl((*cb, t - cb_depth * INCH), Z, 0.812 * INCH / 2, 10)]
    ck = (4.5 * INCH, 2.0 * INCH)
    r_top = 0.75 * INCH / 2
    tan_h = math.tan(math.radians(csk_deg / 2))
    tools += [cyl((*ck, -1), Z, 0.391 * INCH / 2, t + 2),
              _cone((ck[0], ck[1], t + 1), (0, 0, -1), r_top + tan_h, 0.0, (r_top + tan_h) / tan_h)]
    for k in range(3):
        dx, dy = _polar(0.625 * INCH, bc_start + 120 * k)
        tools.append(_drill((ck[0] + dx, ck[1] + dy, t + 1), (0, 0, -1), tap_d / 2, 0.375 * INCH + 1))
    tools.append(_slot((2.5 * INCH, 0.75 * INCH), (3.5 * INCH, 0.75 * INCH), 0.125 * INCH, -1, t + 2))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 18 六角孔阵裁切板

HEX_AF, HEX_PITCH = 10.0, 12.0


def hex_centers(pointy=True, rule="vertices", flat_clip=True):
    """三角形点阵 (12i + 6j, 10.392j) 上对边 10 的六角孔；按顶点（或孔心）全部落在 16 ≤ r ≤ 78、y ≤ 64 内取舍。"""
    rc = HEX_AF / math.sqrt(3)
    dy = HEX_PITCH * math.sqrt(3) / 2
    phase = 90.0 if pointy else 0.0
    keep = []
    for j in range(-10, 11):
        for i in range(-12, 13):
            c = np.array([HEX_PITCH * i + HEX_PITCH / 2 * j, dy * j])
            pts = [c] if rule == "center" else [c + rc * np.array(_polar(1, phase + 60 * m)) for m in range(6)]
            if all(16 <= np.hypot(*p) <= 78 and (not flat_clip or p[1] <= 64) for p in pts):
                keep.append(c)
    return keep


def hex_plate(pointy=True, rule="vertices", flat_clip=True):
    """Ø180、厚 6 的圆板在 y = 70 处切平；Ø20 中心孔；4×Ø6.6 于 r = 82、45° 起每 90°；六角孔阵按 hex_centers。"""
    rc = HEX_AF / math.sqrt(3)
    phase = 90.0 if pointy else 0.0
    plate = common(cyl((0, 0, 0), Z, 90, 6), box(-91, -91, -1, 91, 70, 7))
    tools = [cyl((0, 0, -1), Z, 10, 8)]
    tools += [cyl((*_polar(82, 45 + 90 * k), -1), Z, 3.3, 8) for k in range(4)]
    for c in hex_centers(pointy, rule, flat_clip):
        tools.append(prism([(*(c + rc * np.array(_polar(1, phase + 60 * m))), -1) for m in range(6)], (0, 0, 8)))
    return _cut_all(plate, tools)


# ---------------------------------------------------------------- 19 左旋扭转槽旋钮


def _twisted_flute(r_c, r_f, phase0, twist_deg, z0, z1, height):
    """圆心在半径 r_c、半径 r_f 的水平圆截面，z 每升高 height 绕 Z 轴转 twist_deg（负为顺时针），从 z0 扫到 z1。"""
    a0 = phase0 + twist_deg * z0 / height
    c = np.array([r_c * math.cos(math.radians(a0)), r_c * math.sin(math.radians(a0)), z0])
    circ = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(*c), gp_Dir(0, 0, 1)), r_f)).Edge()
    prof = BRepBuilderAPI_MakeWire(circ).Wire()
    if not twist_deg:
        return _extrude(BRepBuilderAPI_MakeFace(prof).Face(), z1 - z0)
    lead = height * 360 / abs(twist_deg)
    spine = _helix_wire(r_c, lead, z0, z1, a0, twist_deg < 0)
    mk = BRepOffsetAPI_MakePipeShell(spine)
    mk.SetMode(gp_Dir(0, 0, 1))
    mk.Add(prof)
    mk.Build()
    mk.MakeSolid()
    return mk.Shape()


def flute_knob(twist_deg=-40.0, phase=30.0, d_flat=2.5, screw_z=8.0, top_r=4.0):
    """Ø40 旋钮 z 0~34：z 0~30 有 6 条 R5 槽（槽心 r = 21，z = 0 处在 30° + 60k），每升高 30 顺时针转 40°（左旋）；
    顶面外缘 R4；底部 D 形孔 Ø6、平面在 x = 2.5、深 15；z = 8 沿 +X 的 Ø2.5 紧定螺钉孔通到 D 孔平面。"""
    body = cyl((0, 0, 0), Z, 20, 34)
    if top_r:
        body = fillet(body, top_r, _circle_edges(20, 34))
    tools = [_twisted_flute(21.0, 5.0, phase + 60 * k, twist_deg, -1.0, 30.0, 30.0) for k in range(6)]
    tools.append(common(cyl((0, 0, -1), Z, 3, 16), box(-4, -4, -2, d_flat, 4, 17)))
    tools.append(cyl((0, 0, screw_z), X, 1.25, 25))
    return _cut_all(body, tools)


# ---------------------------------------------------------------- 20 三圆柱交体空间节点

NODE_DIAG = -np.ones(3) / math.sqrt(3)


def tricyl_node(core="common", strut_dir=NODE_DIAG, bores=True, tube_r=(12.0, 10.0, 8.0)):
    """核心为三根 R20、沿 X/Y/Z 轴的圆柱的公共部分；+X 管 Ø24 到 x = 60、+Y 管 Ø20 到 y = 55、+Z 管 Ø16 到 z = 50；
    沿 (−1, −1, −1) 的 Ø12 斜撑，端部 SR6 球头、球心距节点中心 50；孔 Ø14 / Ø12 / Ø8 从管端钻到中心另一侧 5。"""
    L = 30.0
    cx, cy, cz = cyl((-L, 0, 0), X, 20, 2 * L), cyl((0, -L, 0), Y, 20, 2 * L), cyl((0, 0, -L), Z, 20, 2 * L)
    if core == "common":
        body = common(common(cx, cy), cz)
    else:
        body = _fuse_all([cyl((-20, 0, 0), X, 20, 40), cyl((0, -20, 0), Y, 20, 40), cyl((0, 0, -20), Z, 20, 40)])
    u = np.asarray(strut_dir, float) / np.linalg.norm(strut_dir)
    rx, ry, rz = tube_r
    parts = [body, cyl((0, 0, 0), X, rx, 60), cyl((0, 0, 0), Y, ry, 55), cyl((0, 0, 0), Z, rz, 50),
             cyl((0, 0, 0), tuple(u), 6, 50), BRepPrimAPI_MakeSphere(gp_Pnt(*(50 * u)), 6).Shape()]
    body = _fuse_all(parts)
    if bores:
        body = _cut_all(body, [cyl((-5, 0, 0), X, 7, 66), cyl((0, -5, 0), Y, 6, 61), cyl((0, 0, -5), Z, 4, 56)])
    return body
