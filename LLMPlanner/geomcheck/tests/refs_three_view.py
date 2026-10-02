"""三视图 track 33 道题的参考实体：按图纸尺寸用 OCP 直接构造。

B/C 组取自 Web Demo 的三视图样例；D 组是自行设计的零件，图纸由这里的参考实体投影生成。

坐标约定与图纸一致：主视图沿 −Z 观察（X 向右、Y 向上），俯视图沿 −Y 观察，右视图沿 −X 观察。
板类零件（键帽、面板、圆盘、碗、键盘、齿轮）的厚度方向是 Z，主视图看到的是板面；
其余零件 Y 向上，主视图是立面。

每个构造函数的默认参数给出图纸上的正例；个别参数用来生成针对性改错的反例。

生成图纸与参考 STEP：python -m geomcheck.tests.refs_three_view [case ...]
"""

from __future__ import annotations

import math
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_Sewing,
    BRepBuilderAPI_Transform,
)
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec

from .fixtures import box, clean, cut, cyl, fillet, fuse, prism
from .refs import common, hull2, line_edges_at, revolve, rotate_z

Z = (0, 0, 1)
Y = (0, 1, 0)
X = (1, 0, 0)

CASES = [
    "b01_l_bracket", "b02_plate_with_holes", "b03_t_bracket", "b04_keycap", "b05_keycap_full",
    "b06_lock_striker", "b07_switch_panel", "b11_gear_disc", "b22_round_bowl",
    "c1_mech_keyboard", "c2_gearbox", "c3_gear_pair", "c4_desktop_pen_holder",
]
# D 组：自拟零件，三视图由参考实体投影生成（不取自任何外部数据集）
D_BASIC = [
    "d01_v_block", "d04_flange_cover", "d05_stepped_shaft", "d06_u_clevis", "d07_eccentric_cam",
    "d09_angle_bracket", "d10_clamp_plate", "d11_dovetail_slide", "d12_cut_block", "d18_gear_rack",
]
D_COMPOSITE = [
    "d02_bearing_block", "d03_rib_bracket", "d08_pipe_tee", "d13_v_pulley", "d14_link_rod",
    "d15_motor_mount", "d16_heat_sink", "d17_pipe_clamp", "d19_hinge_leaf", "d20_ribbed_base",
]
CASES = CASES + sorted(D_BASIC + D_COMPOSITE)


# ---------------------------------------------------------------- helpers


def translate(shape, dx, dy, dz):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(dx, dy, dz))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def compound(*shapes):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def _face(points):
    poly = BRepBuilderAPI_MakePolygon()
    for p in points:
        poly.Add(gp_Pnt(*p))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire()).Face()


def frustum(bottom, top, h, z0=0.0):
    """底面 bottom×bottom、顶面 top×top 的正四棱台（全部为平面），中心在原点、底面在 z0。"""
    b, t = bottom / 2, top / 2
    lo = [(-b, -b, z0), (b, -b, z0), (b, b, z0), (-b, b, z0)]
    hi = [(-t, -t, z0 + h), (t, -t, z0 + h), (t, t, z0 + h), (-t, t, z0 + h)]
    sew = BRepBuilderAPI_Sewing(1e-6)
    sew.Add(_face(lo[::-1]))
    sew.Add(_face(hi))
    for k in range(4):
        sew.Add(_face([lo[k], lo[(k + 1) % 4], hi[(k + 1) % 4], hi[k]]))
    sew.Perform()
    return BRepBuilderAPI_MakeSolid(TopoDS.Shell(sew.SewedShape())).Solid()


def stadium_z(cx, cy, length, width, z0, h):
    """沿 X 的长圆（总长 length、宽 width），轴向 Z。"""
    r = width / 2
    half = length / 2 - r
    return fuse(box(cx - half, cy - r, z0, cx + half, cy + r, z0 + h),
                cyl((cx - half, cy, z0), Z, r, h), cyl((cx + half, cy, z0), Z, r, h))


def _vertical_edges(points, axis="z"):
    """平行于 axis 的直线棱边，其另两个坐标落在 points 中某一点上。"""
    def pick(c):
        if c.GetType() != GeomAbs_Line:
            return False
        p0, p1 = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        a, b = (p0.X(), p0.Y()), (p1.X(), p1.Y())
        if axis == "y":
            a, b = (p0.X(), p0.Z()), (p1.X(), p1.Z())
        if abs(a[0] - b[0]) > 1e-9 or abs(a[1] - b[1]) > 1e-9:
            return False
        return any(math.hypot(a[0] - x, a[1] - y) < 1e-4 for x, y in points)
    return pick


def _polar(r, deg):
    a = math.radians(deg)
    return r * math.cos(a), r * math.sin(a)


# ---------------------------------------------------------------- B 组


def b01_l_bracket(notch=(30.0, 20.0), depth=20.0):
    nx, ny = notch
    pts = [(0, 0), (60, 0), (60, 40 - ny), (60 - nx, 40 - ny), (60 - nx, 40), (0, 40)]
    return prism([(x, y, 0) for x, y in pts], (0, 0, depth))


def b02_plate_with_holes(spacing=40.0, d=10.0):
    plate = box(0, 0, 0, 80, 40, 10)
    x0 = 40 - spacing / 2
    return cut(plate, cyl((x0, 20, -1), Z, d / 2, 12), cyl((x0 + spacing, 20, -1), Z, d / 2, 12))


def b03_t_bracket(stem_w=20.0, stem_x0=None):
    x0 = 30 - stem_w / 2 if stem_x0 is None else stem_x0
    x1 = x0 + stem_w
    pts = [(x0, 0), (x1, 0), (x1, 30), (60, 30), (60, 45), (0, 45), (0, 30), (x0, 30)]
    return prism([(x, y, 0) for x, y in pts], (0, 0, 15))


def b04_keycap(top=14.0):
    return frustum(18.0, top, 8.0)


def b05_keycap_full(pocket_depth=3.0, fillet_r=1.5):
    body = frustum(18.0, 14.0, 8.0)
    if fillet_r:
        body = clean(fillet(body, fillet_r, line_edges_at(8.0)))
    return clean(cut(body, box(-6, -6, -1, 6, 6, pocket_depth)))


def b06_lock_striker(hole_y=32.0, slot_len=20.0, corner_r=3.0):
    plate = box(-15, -40, 0, 15, 40, 3)
    if corner_r:
        plate = clean(fillet(plate, corner_r, _vertical_edges([(sx * 15, sy * 40) for sx in (1, -1) for sy in (1, -1)])))
    tools = [stadium_z(0, 0, slot_len, 10, -1, 5), cyl((0, hole_y, -1), Z, 2.5, 5), cyl((0, -hole_y, -1), Z, 2.5, 5)]
    return clean(cut(plate, *tools))


def b07_switch_panel(pocket_depth=3.0, hole_c=35.0):
    plate = box(-43, -43, 0, 43, 43, 10)
    plate = clean(fillet(plate, 3.0, _vertical_edges([(sx * 43, sy * 43) for sx in (1, -1) for sy in (1, -1)])))
    tools = [box(-25, -25, 10 - pocket_depth, 25, 25, 11)]
    tools += [cyl((sx * hole_c, sy * hole_c, -1), Z, 1.5, 12) for sx in (1, -1) for sy in (1, -1)]
    return clean(cut(plate, *tools))


def b11_gear_disc(n_holes=12, pcd=48.0):
    disc = cyl((0, 0, 0), Z, 30, 10)
    tools = [cyl((0, 0, -1), Z, 7.5, 12)]
    tools += [cyl((*_polar(pcd / 2, 360 / n_holes * k), -1), Z, 2, 12) for k in range(n_holes)]
    return clean(cut(disc, *tools))


def b22_round_bowl(depth=34.0, inner_d=68.0):
    body = cyl((0, 0, 0), Z, 40, 40)
    return clean(cut(body, cyl((0, 0, 40 - depth), Z, inner_d / 2, depth + 1)))


# ---------------------------------------------------------------- C 组


def c1_mech_keyboard(cols=13, rows=5, pitch=18.0, key_top=13.0):
    base = box(-142.5, -47.5, 0, 142.5, 47.5, 15)
    key = frustum(15.0, key_top, 5.0, z0=15.0)
    keys = [translate(key, (k - (cols - 1) / 2) * pitch, (j - (rows - 1) / 2) * pitch, 0)
            for k in range(cols) for j in range(rows)]
    return clean(fuse(base, compound(*keys)))


def c2_gearbox(wall=8.0, bearing_y=60.0, mount_holes=True):
    flange = box(-100, 0, -60, 100, 20, 60)
    body = fuse(flange, box(-80, 20, -50, 80, 100, 50))
    tools = [box(-80 + wall, 26, -50 + wall, 80 - wall, 101, 50 - wall)]
    tools.append(cyl((0, bearing_y, -51), Z, 12.5, 102))
    if mount_holes:
        tools += [cyl((sx * 85, -1, sz * 45), Y, 4, 22) for sx in (1, -1) for sz in (1, -1)]
    return clean(cut(body, *tools))


def _gear(cx, root_r, n_teeth, phase_deg, thick=15.0, tooth_w=5.0, tooth_h=4.0):
    disc = cyl((0, 0, 0), Z, root_r, thick)
    teeth = [rotate_z(box(root_r - 1, -tooth_w / 2, 0, root_r + tooth_h, tooth_w / 2, thick),
                      phase_deg + 360 / n_teeth * k) for k in range(n_teeth)]
    return translate(clean(fuse(disc, *teeth)), cx, 0, 0)


def c3_gear_pair(center_dist=70.0, small_teeth=10, big_phase=180.0):
    small = _gear(-center_dist / 2, 20.0, small_teeth, 0.0)
    big = _gear(center_dist / 2, 40.0, 20, big_phase)
    return compound(small, big)


def c4_desktop_pen_holder(rear_depth=102.0, charge_hole=True, fillet_r=3.0, steps=(44.0, 78.0, 110.0)):
    h0, h1, h2 = steps
    pts = [(-70, 0), (70, 0), (70, h2), (28, h2), (28, h1), (-18, h1), (-18, h0), (-70, h0)]
    body = prism([(x, y, 0) for x, y in pts], (0, 0, 90))
    if fillet_r:
        body = clean(fillet(body, fillet_r, _vertical_edges([(sx * 70, z) for sx in (1, -1) for z in (0, 90)], axis="y")))
    tools = [
        box(-63, h0 - 36, 9, -25, h0 + 1, 81),          # 前部浅仓 38×72 深 36
        box(-12, h1 - 70, 9, 22, h1 + 1, 81),           # 中部文具仓 34×72 深 70
        box(34, h2 - rear_depth, 9, 64, h2 + 1, 81),    # 后部高笔仓 30×72 深 102
        box(-57, -1, 38, -31, 1.5, 52),                 # 底面防滑浅槽 26×14 深 1.5
        box(36, -1, 38, 62, 1.5, 52),
    ]
    if charge_hole:
        tools.append(box(-51, 27, 80, -37, 37, 91))     # 前壁充电线孔 14×10
    slot = fuse(box(42, 83, 80, 56, 93, 91), cyl((42, 88, 80), Z, 5, 11), cyl((56, 88, 80), Z, 5, 11))
    tools.append(slot)                                  # 后仓前壁提手槽 24×10
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- D 组（基础）


def _xy_prism(pts, z0, z1):
    return prism([(x, y, z0) for x, y in pts], (0, 0, z1 - z0))


def _stadium(p0, p1, r, axis, h, start):
    """两圆心 p0、p1（垂直于 axis 的平面内坐标）间的长圆，沿 axis 从 start 拉伸 h。"""
    def at(p):
        return {"x": (start, *p), "y": (p[0], start, p[1]), "z": (*p, start)}[axis]

    d = {"x": X, "y": Y, "z": Z}[axis]
    (a0, b0), (a1, b1) = p0, p1
    if a0 == a1:
        lo, hi = sorted((b0, b1))
        corners = ((a0 - r, lo), (a0 + r, hi))
    else:
        lo, hi = sorted((a0, a1))
        corners = ((lo, b0 - r), (hi, b0 + r))
    (u0, v0), (u1, v1) = corners
    rng = {"x": ((start, u0, v0), (start + h, u1, v1)), "y": ((u0, start, v0), (u1, start + h, v1)),
           "z": ((u0, v0, start), (u1, v1, start + h))}[axis]
    return fuse(box(*rng[0], *rng[1]), cyl(at(p0), d, r, h), cyl(at(p1), d, r, h))


def d01_v_block(v_width=40.0, relief=True, groove_y=30.0):
    """V 形块：主视图为截面（沿 Z 拉伸 40）。顶部 90° V 槽，槽底让刀槽，底部通槽，两侧夹持槽。"""
    apex = 50 - v_width / 2
    s = 2.0 if relief else 0.0
    pts = [(-40, 0), (-10, 0), (-10, 6), (10, 6), (10, 0), (40, 0),
           (40, groove_y), (35, groove_y), (35, groove_y + 8), (40, groove_y + 8), (40, 50),
           (v_width / 2, 50), (s, apex + s)]
    if relief:
        pts += [(2, 26), (-2, 26)]
    pts += [(-s, apex + s), (-v_width / 2, 50), (-40, 50), (-40, groove_y + 8), (-35, groove_y + 8),
            (-35, groove_y), (-40, groove_y)]
    out = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    return _xy_prism(out, 0, 40)


def d04_flange_cover(n_holes=6, recess_r=35.0):
    body = fuse(cyl((0, 0, 0), Z, 50, 12), cyl((0, 0, 12), Z, 25, 20))
    tools = [cyl((0, 0, -1), Z, 15, 34), cyl((0, 0, -1), Z, recess_r, 4)]
    tools += [cyl((*_polar(40, 360 / n_holes * k), -1), Z, 4.5, 14) for k in range(n_holes)]
    return clean(cut(body, *tools))


def d05_stepped_shaft(key=(40.0, 70.0), hole_x=15.0):
    body = fuse(cyl((0, 0, 0), X, 10, 30), cyl((30, 0, 0), X, 15, 50), cyl((80, 0, 0), X, 12, 40))
    k0, k1 = key
    keyway = _stadium((k0 + 4, 0), (k1 - 4, 0), 4, "y", 5, 11)
    return clean(cut(body, keyway, cyl((hole_x, -11, 0), Y, 3, 22)))


def d06_u_clevis(slot_w=16.0, pin_y=38.0, tap_depth=16.0):
    block = box(-20, 0, -15, 20, 38, 15)
    caps = [cyl((x0, 38, 0), X, 15, 12) for x0 in (-20, 8)]
    boss = cyl((0, -20, 0), Y, 12, 20)
    body = fuse(block, boss, *caps)
    tools = [box(-slot_w / 2, 20, -16, slot_w / 2, 54, 16), cyl((-21, pin_y, 0), X, 5, 42),
             cyl((0, -21, 0), Y, 6, tap_depth + 1)]
    return clean(cut(body, *tools))


def d07_eccentric_cam(offset=10.0, keyway=True, hub_x=None):
    hx = offset if hub_x is None else hub_x
    body = fuse(cyl((0, 0, 0), Z, 40, 15), cyl((hx, 0, 15), Z, 18, 10))
    tools = [cyl((offset, 0, -1), Z, 10, 27)]
    if keyway:
        tools.append(box(offset - 3, 0, -1, offset + 3, 13, 26))
    tools += [cyl((-18, sy * 14, -1), Z, 6, 17) for sy in (1, -1)]
    return clean(cut(body, *tools))


def d09_angle_bracket(slot=(25.0, 47.0), hole_y=26.0, fillet_r=5.0):
    pts = [(0, 0), (60, 0), (60, 8), (8, 8), (8, 40), (0, 40)]
    body = prism([(x, y, -50) for x, y in pts], (0, 0, 100))
    if fillet_r:
        body = clean(fillet(body, fillet_r, _vertical_edges([(8, 8)])))
    s0, s1 = slot
    tools = [_stadium((s0 + 4, z), (s1 - 4, z), 4, "y", 10, -1) for z in (-30, 30)]
    tools += [cyl((-1, hole_y, z), X, 4.5, 10) for z in (-25, 25)]
    return clean(cut(body, *tools))


def d10_clamp_plate(cbore_depth=8.0, rebate_depth=5.0, window=(40.0, 20.0)):
    plate = box(-50, -35, 0, 50, 35, 15)
    wx, wy = window[0] / 2, window[1] / 2
    tools = [box(-wx, -wy, -1, wx, wy, 16), box(-51, 29, 15 - rebate_depth, 51, 36, 16)]
    for sx in (1, -1):
        for sy in (1, -1):
            tools += [cyl((sx * 38, sy * 20, -1), Z, 4.5, 17), cyl((sx * 38, sy * 20, 15 - cbore_depth), Z, 7.5, 9)]
    return clean(cut(plate, *tools))


def d11_dovetail_slide(angle=60.0, hole_depth=12.0, top_slot=True):
    w = 12 + 15 / math.tan(math.radians(angle))
    groove = _xy_prism([(-12, -1), (12, -1), (12, 0), (w, 15), (-w, 15), (-12, 0)], -1, 61)
    tools = [groove] + [cyl((sx * 20, 40 - hole_depth, 30), Y, 4, hole_depth + 1) for sx in (1, -1)]
    if top_slot:
        tools.append(box(-5, 35, -1, 5, 41, 61))
    return clean(cut(box(-40, 0, 0, 40, 40, 60), *tools))


def d12_cut_block(chamfer_z=20.0, notch_w=16.0, hole_z=12.0):
    body = _xy_prism([(0, 0), (70, 0), (70, 20), (25, 20), (25, 50), (0, 50)], 0, 40)
    c = 50 + chamfer_z
    wedge = prism([(-1, c - 41, 41), (-1, 51, c - 51), (-1, 51, 41)], (27, 0, 0))
    tools = [wedge, box(45, -1, 20 - notch_w / 2, 71, 21, 20 + notch_w / 2), cyl((-1, 35, hole_z), X, 5, 27)]
    return clean(cut(body, *tools))


def d18_gear_rack(n_teeth=12, flank_deg=20.0, cbore_depth=6.0):
    body = box(0, 0, 0, 120, 20, 15)
    half_b = 1.5 + 5 * math.tan(math.radians(flank_deg))
    x0 = 60 - 10 * (n_teeth - 1) / 2
    teeth = [prism([(x0 + 10 * k + sx * h, y, 0) for sx, h, y in
                    ((-1, half_b, 20), (1, half_b, 20), (1, 1.5, 25), (-1, 1.5, 25))], (0, 0, 15))
             for k in range(n_teeth)]
    body = fuse(body, compound(*teeth))
    tools = []
    for x in (30, 90):
        tools += [cyl((x, -1, 7.5), Y, 3.3, 22), cyl((x, -1, 7.5), Y, 5.5, cbore_depth + 1)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- D 组（组合）


def d02_bearing_block(bore_d=35.0, oil_hole=True, hole_dx=45.0, recess=True):
    body = fuse(box(-60, 0, -25, 60, 14, 25), box(-30, 14, -20, 30, 50, 20), cyl((0, 50, -20), Z, 30, 40))
    tools = [cyl((0, 50, -21), Z, bore_d / 2, 42)]
    tools += [cyl((sx * hole_dx, -1, 0), Y, 6, 16) for sx in (1, -1)]
    if oil_hole:
        tools.append(cyl((0, 60, 0), Y, 3, 21))
    if recess:
        tools.append(box(-30, -1, -26, 30, 3, 26))
    return clean(cut(body, *tools))


def d03_rib_bracket(rib_top=50.0, hole_y=45.0, fillet_r=10.0):
    upright = box(-40, 0, -30, 40, 70, -20)
    if fillet_r:
        upright = clean(fillet(upright, fillet_r, _vertical_edges([(-40, 70), (40, 70)])))
    ribs = [prism([(x0, 10, -20), (x0, 10, 20), (x0, rib_top, -20)], (6, 0, 0)) for x0 in (-40, 34)]
    body = fuse(box(-40, 0, -30, 40, 10, 30), upright, *ribs)
    tools = [cyl((0, hole_y, -31), Z, 10, 12)] + [cyl((sx * 28, -1, 12), Y, 5, 12) for sx in (1, -1)]
    return clean(cut(body, *tools))


def d08_pipe_tee(branch_bore=11.0, flange_t=8.0, hole_deg=45.0):
    body = fuse(cyl((-50, 0, 0), X, 20, 100), cyl((-50, 0, 0), X, 30, flange_t),
                cyl((50 - flange_t, 0, 0), X, 30, flange_t), cyl((0, 0, 0), Y, 15, 60))
    tools = [cyl((-51, 0, 0), X, 15, 102), cyl((0, 0, 0), Y, branch_bore, 61)]
    for k in range(4):
        y, z = _polar(24, hole_deg + 90 * k)
        tools += [cyl((-51, y, z), X, 3, flange_t + 2), cyl((49 - flange_t, y, z), X, 3, flange_t + 2)]
    return clean(cut(body, *tools))


def d13_v_pulley(groove_depth=8.0, n_holes=4, keyway=True):
    dz = groove_depth * math.tan(math.radians(19))
    rb = 50 - groove_depth
    rz = [(10, 0), (20, 0), (20, 15), (40, 15), (40, 5), (50, 5)]
    for zc in (13, 27):
        rz += [(50, zc - 6), (rb, zc - 6 + dz), (rb, zc + 6 - dz), (50, zc + 6)]
    rz += [(50, 35), (40, 35), (40, 25), (20, 25), (20, 40), (10, 40)]
    body = revolve(rz)
    tools = [cyl((*_polar(30, 360 / n_holes * k), 14), Z, 6, 12) for k in range(n_holes)]
    if keyway:
        tools.append(box(0, -3, -1, 12.8, 3, 41))
    return clean(cut(body, *tools))


def d14_link_rod(center=120.0, pocket_depth=4.0, small_t=16.0):
    z0 = 10 - small_t / 2
    body = fuse(hull2((0, 0), 24, (center, 0), 12, 2, 16), cyl((0, 0, 0), Z, 30, 20), cyl((center, 0, z0), Z, 16, small_t))
    cx = center * 68 / 120
    tools = [cyl((0, 0, -1), Z, 18, 22), cyl((center, 0, -1), Z, 8, 22),
             _stadium((cx - 19, 0), (cx + 19, 0), 7, "z", pocket_depth + 1, 18 - pocket_depth),
             _stadium((cx - 19, 0), (cx + 19, 0), 7, "z", pocket_depth + 1, 1)]
    return clean(cut(body, *tools))


def d15_motor_mount(bore_d=22.0, square=31.0, gussets=True):
    body = fuse(box(-40, 0, 0, 40, 8, 50), box(-40, 0, 0, 40, 86, 8))
    if gussets:
        body = fuse(body, *[prism([(x0, 8, 8), (x0, 8, 40), (x0, 40, 8)], (6, 0, 0)) for x0 in (-40, 34)])
    h = square / 2
    tools = [cyl((0, 52, -1), Z, bore_d / 2, 10)]
    tools += [cyl((sx * h, 52 + sy * h, -1), Z, 1.75, 10) for sx in (1, -1) for sy in (1, -1)]
    tools += [_stadium((sx * 25, 25), (sx * 25, 39), 3, "y", 10, -1) for sx in (1, -1)]
    return clean(cut(body, *tools))


def d16_heat_sink(n_fins=7, fin_t=2.0, hole_dz=15.0):
    base = box(-35, 0, -25, 35, 6, 25)
    x0 = -8 * (n_fins - 1) / 2
    fins = [box(x0 + 8 * k - fin_t / 2, 6, -25, x0 + 8 * k + fin_t / 2, 30, 25) for k in range(n_fins)]
    body = fuse(base, compound(*fins))
    tools = [cyl((sx * 31.5, -1, sz * hole_dz), Y, 2.25, 8) for sx in (1, -1) for sz in (1, -1)]
    return clean(cut(body, *tools))


def d17_pipe_clamp(outer_r=30.0, gap=2.0, hole_x=40.0):
    g = gap / 2
    ring = common(cut(cyl((0, 0, -10), Z, outer_r, 20), cyl((0, 0, -11), Z, 20, 22)), box(-31, g, -11, 31, 31, 11))
    strap = fuse(ring, box(-50, g, -10, -20, g + 6, 10), box(20, g, -10, 50, g + 6, 10))
    base = cut(box(-50, -25, -10, 50, -g, 10), cyl((0, 0, -11), Z, 20, 22))
    tools = [cyl((sx * hole_x, -26, 0), Y, 3.5, 34) for sx in (1, -1)]
    return compound(clean(cut(strap, *tools)), clean(cut(base, *tools)))


def d19_hinge_leaf(n_knuckles=3, notch=5.0, hole_x=40.0):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir

    pitch = 40 / (n_knuckles - 1)
    knuckles = [cyl((0, 4, k * pitch), Z, 4, 10) for k in range(n_knuckles)]
    body = fuse(box(0, 0, 0, 60, 3, 50), *knuckles)
    tools = [box(-5, -1, k * pitch + 10, notch, 9, (k + 1) * pitch) for k in range(n_knuckles - 1)]
    tools.append(cyl((0, 4, -1), Z, 1.5, 52))
    for z in (10, 25, 40):
        cone = BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(hole_x, 1, z), gp_Dir(0, 1, 0)), 2.5, 5.0, 2.5).Shape()
        tools += [cyl((hole_x, -1, z), Y, 2.5, 5), cone]
    return clean(cut(body, *tools))


def d20_ribbed_base(rib_h=15.0, center_depth=20.0, boss_x=55.0):
    body = box(-70, 0, -45, 70, 10, 45)
    bosses = [cyl((sx * boss_x, 10, sz * 32), Y, 10, 20) for sx in (1, -1) for sz in (1, -1)]
    ribs = [box(-45, 10, -3, 45, 10 + rib_h, 3), box(-3, 10, -32, 3, 10 + rib_h, 32)]
    body = fuse(body, *bosses, *ribs, cyl((0, 10, 0), Y, 15, 25))
    tools = [box(-40, -1, -25, 40, 4, 25), cyl((0, 35 - center_depth, 0), Y, 8, center_depth + 1)]
    tools += [cyl((sx * boss_x, -1, sz * 32), Y, 4, 32) for sx in (1, -1) for sz in (1, -1)]
    return clean(cut(body, *tools))


# ---------------------------------------------------------------- 生成图纸与参考 STEP

ROOT = Path(__file__).resolve().parents[2]
RUBRIC_DIR = ROOT / "eval" / "rubrics_three_view"


def write_step(shape, path) -> None:
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    if writer.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP 写出失败: {path}")


def build_assets(cases=None) -> None:
    """按 rubric 中的 drawing 清单生成三视图 PNG，并导出参考 STEP（路径取自 rubric）。"""
    import yaml

    from ..drawing import render

    for case in cases or CASES:
        rubric = yaml.safe_load((RUBRIC_DIR / f"{case}.yaml").read_text(encoding="utf-8"))
        shape = globals()[case]()
        write_step(shape, ROOT / rubric["reference"])
        render(shape, rubric.get("drawing"), rubric["title"], ROOT / rubric["image"])
        print(case, "->", rubric["image"], rubric["reference"])


if __name__ == "__main__":
    import sys

    build_assets(sys.argv[1:] or None)
