"""三视图 track 13 道题的参考实体：按图纸尺寸用 OCP 直接构造。

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
from .refs import line_edges_at, rotate_z

Z = (0, 0, 1)
Y = (0, 1, 0)
X = (1, 0, 0)

CASES = [
    "b01_l_bracket", "b02_plate_with_holes", "b03_t_bracket", "b04_keycap", "b05_keycap_full",
    "b06_lock_striker", "b07_switch_panel", "b11_gear_disc", "b22_round_bowl",
    "c1_mech_keyboard", "c2_gearbox", "c3_gear_pair", "c4_desktop_pen_holder",
]


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
