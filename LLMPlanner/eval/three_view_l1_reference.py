"""用 L1 工具（与 LLM 拿到的 24 个工具完全相同）逐题搭出三视图 track 50 道题的参考件，
证明每道题都存在一条能拿到 rubric 满分的 workflow 调用轨迹，并统计轨迹长度。

    python eval/three_view_l1_reference.py [case ...]
        建模并导出 eval/artifacts/three_view_l1_reference/<case>_r1.step，
        调用轨迹写到 eval/three_view/oracle_l1/<case>.json
    venv-occ/Scripts/python.exe -m geomcheck.three_view --artifacts eval/artifacts/three_view_l1_reference

几何与 geomcheck/tests/refs_three_view.py 的参考实体一致（同一摆放，所以图像分也应为 100）；
每题只用图纸上能读到的尺寸。
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from hard_l1_reference import (  # noqa: E402
    Builder, _post, circle, hull_of_circles, poly, polar, rect, ring,
)
from planner.workflow.tools import WorkflowSession  # noqa: E402

OUT = ROOT / "eval" / "artifacts" / "three_view_l1_reference"
TRAJ = ROOT / "eval" / "three_view" / "oracle_l1"
QUERY_TOOLS = {"get_model_state", "set_parameters", "finish"}


class RecordingBuilder(Builder):
    """记录每次工具调用（参数与结果摘要），作为 oracle 轨迹。"""

    def __init__(self, session: WorkflowSession):
        super().__init__(session)
        self.trajectory: list[dict] = []

    def call(self, tool: str, args: dict) -> dict:
        out = super().call(tool, args)
        self.trajectory.append({
            "tool": tool,
            "args": json.loads(json.dumps(args)),
            "ok": bool(out.get("ok")),
            "error": None if out.get("ok") else (out.get("error") or {}).get("message"),
            "volume_mm3": out.get("volume_mm3"),
        })
        return out


def rot(p, deg, c=(0.0, 0.0)):
    """点 p 绕 c 逆时针转 deg 度。"""
    a = math.radians(deg)
    x, y = p[0] - c[0], p[1] - c[1]
    return [round(c[0] + x * math.cos(a) - y * math.sin(a), 6), round(c[1] + x * math.sin(a) + y * math.cos(a), 6)]


def slot(p0, p1, width) -> dict:
    return {"type": "slot", "start": list(p0), "end": list(p1), "width": width}


def tooth(cx, r_from, r_to, width, deg) -> dict:
    """齿轮上沿径向的矩形齿（绕齿轮中心 (cx, 0) 转 deg 度）。"""
    h = width / 2
    pts = [(cx + r_from, -h), (cx + r_to, -h), (cx + r_to, h), (cx + r_from, h)]
    return poly([rot(p, deg, (cx, 0)) for p in pts])


# ----------------------------------------------------------------------------- B 组


def b01_l_bracket(b):
    b.extrude(plane="front_plane", loops=[poly([(0, 0), (60, 0), (60, 20), (30, 20), (30, 40), (0, 40)])], depth=20)


def b02_plate_with_holes(b):
    b.extrude(plane="front_plane", loops=[rect((40, 20), (80, 40))], depth=10)
    b.hole(plane="front_plane", centers=[[20, 20], [60, 20]], diameter=10, through_all=True, direction="+Z")


def b03_t_bracket(b):
    pts = [(20, 0), (40, 0), (40, 30), (60, 30), (60, 45), (0, 45), (0, 30), (20, 30)]
    b.extrude(plane="front_plane", loops=[poly(pts)], depth=15)


KEYCAP_DRAFT = math.degrees(math.atan(2 / 8))   # 18 → 14、高 8 的正四棱台


def b04_keycap(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (18, 18))], depth=8, draft_deg=KEYCAP_DRAFT)


def b05_keycap_full(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (18, 18))], depth=8, draft_deg=KEYCAP_DRAFT)
    for origin, direction in (((0, 20, 8), (0, -1, 0)), ((0, -20, 8), (0, 1, 0)),
                              ((20, 0, 8), (-1, 0, 0)), ((-20, 0, 8), (1, 0, 0))):
        b.fillet(radius=1.5, edges={"origin": list(origin), "direction": list(direction)})
    b.cut(plane="front_plane", loops=[rect((0, 0), (12, 12))], direction="+Z", depth=3)


def b06_lock_striker(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (30, 80), corner_radius=3)], depth=3)
    b.cut(plane="front_plane", loops=[slot((-5, 0), (5, 0), 10), circle((0, 32), 5), circle((0, -32), 5)],
          through_all=True, direction="+Z")


def b07_switch_panel(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (86, 86), corner_radius=3)], depth=10)
    b.cut(plane=b.ref("Z", 10), loops=[rect((0, 0), (50, 50))], direction="-Z", depth=3)
    b.hole(plane="front_plane", centers=[[sx * 35, sy * 35] for sx in (1, -1) for sy in (1, -1)], diameter=3,
           through_all=True, direction="+Z")


def b11_gear_disc(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 60)], depth=10)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=15, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[polar(24, 30 * k) for k in range(12)], diameter=4, through_all=True,
           direction="+Z")


def b22_round_bowl(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 80)], depth=40)
    b.cut(plane=b.ref("Z", 40), loops=[circle((0, 0), 68)], direction="-Z", depth=34)


# ----------------------------------------------------------------------------- C 组


def c1_mech_keyboard(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (285, 95))], depth=15)
    keys = [rect(((k - 6) * 18, (j - 2) * 18), (15, 15)) for k in range(13) for j in range(5)]
    b.extrude(plane=b.ref("Z", 15), loops=keys, depth=5, draft_deg=math.degrees(math.atan(1 / 5)))


def c2_gearbox(b):
    b.extrude(plane=b.ref("Z", -60), loops=[rect((0, 10), (200, 20))], depth=120)
    b.extrude(plane=b.ref("Z", -50), loops=[rect((0, 60), (160, 80))], depth=100)
    b.cut(plane=b.ref("Y", 100), loops=[rect((0, 0), (144, 84))], direction="-Y", depth=74)
    b.hole(plane="front_plane", centers=[[0, 60]], diameter=25, through_all=True, direction="both")
    b.hole(plane="top_plane", centers=[[sx * 85, sz * 45] for sx in (1, -1) for sz in (1, -1)], diameter=8,
           direction="+Y", depth=20)


def c3_gear_pair(b):
    b.extrude(plane="front_plane", loops=[circle((-35, 0), 40)], depth=15)
    b.extrude(plane="front_plane", loops=[tooth(-35, 19, 24, 5, 36 * k) for k in range(10)], depth=15)
    b.extrude(plane="front_plane", loops=[circle((35, 0), 80)], depth=15)
    b.extrude(plane="front_plane", loops=[tooth(35, 39, 44, 5, 180 + 18 * k) for k in range(20)], depth=15)


def c4_desktop_pen_holder(b):
    # 外侧 4 条竖直棱（x = ±70、z = 0 / 90）的 R3 直接画进各层轮廓
    b.extrude(plane="top_plane", loops=[rect((0, 45), (140, 90), corner_radius=3)], direction="+Y", depth=44)
    step = [0, 3, 3, 0]
    b.extrude(plane=b.ref("Y", 44), loops=[poly([(-18, 0), (70, 0), (70, 90), (-18, 90)], corner_radii=step)],
              direction="+Y", depth=34)
    b.extrude(plane=b.ref("Y", 78), loops=[poly([(28, 0), (70, 0), (70, 90), (28, 90)], corner_radii=step)],
              direction="+Y", depth=32)
    b.cut(plane=b.ref("Y", 44), loops=[rect((-44, 45), (38, 72))], direction="-Y", depth=36)
    b.cut(plane=b.ref("Y", 78), loops=[rect((5, 45), (34, 72))], direction="-Y", depth=70)
    b.cut(plane=b.ref("Y", 110), loops=[rect((49, 45), (30, 72))], direction="-Y", depth=102)
    b.cut(plane="top_plane", loops=[rect((-44, 45), (26, 14)), rect((49, 45), (26, 14))], direction="+Y", depth=1.5)
    b.cut(plane=b.ref("Z", 90), loops=[rect((-44, 32), (14, 10))], direction="-Z", depth=10)
    b.cut(plane=b.ref("Z", 90), loops=[slot((42, 88), (56, 88), 10)], direction="-Z", depth=10)


# ----------------------------------------------------------------------------- D 组（基础）


def d01_v_block(b):
    apex, s = 30, 2
    pts = [(-40, 0), (-10, 0), (-10, 6), (10, 6), (10, 0), (40, 0), (40, 30), (35, 30), (35, 38), (40, 38),
           (40, 50), (20, 50), (s, apex + s), (2, 26), (-2, 26), (-s, apex + s), (-20, 50), (-40, 50), (-40, 38),
           (-35, 38), (-35, 30), (-40, 30)]
    b.extrude(plane="front_plane", loops=[poly(pts)], depth=40)


def d04_flange_cover(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 100)], depth=12)
    b.extrude(plane=b.ref("Z", 12), loops=[circle((0, 0), 50)], depth=20)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=30, through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[circle((0, 0), 70)], direction="+Z", depth=3)
    b.hole(plane="front_plane", centers=[polar(40, 60 * k) for k in range(6)], diameter=9, direction="+Z", depth=12)


def d05_stepped_shaft(b):
    profile = [(0, 0), (0, 10), (30, 10), (30, 15), (80, 15), (80, 12), (120, 12), (120, 0)]
    b.revolve(plane="front_plane", loops=[poly(profile)], axis_start=[-10, 0], axis_end=[130, 0])
    b.cut(plane=b.ref("Y", 11), loops=[slot((44, 0), (66, 0), 8)], direction="+Y", depth=5)
    b.hole(plane=b.ref("Y", -11), centers=[[15, 0]], diameter=6, direction="+Y", depth=22)


def d06_u_clevis(b):
    b.extrude(plane=b.ref("Z", -15), loops=[rect((0, 19), (40, 38))], depth=30)
    b.extrude(plane=b.ref("X", -20), loops=[circle((0, 38), 30)], direction="+X", depth=12)
    b.extrude(plane=b.ref("X", 8), loops=[circle((0, 38), 30)], direction="+X", depth=12)
    b.extrude(plane=b.ref("Y", -20), loops=[circle((0, 0), 24)], direction="+Y", depth=20)
    b.cut(plane=b.ref("Y", 54), loops=[rect((0, 0), (16, 32))], direction="-Y", depth=34)
    b.hole(plane=b.ref("X", -21), centers=[[0, 38]], diameter=10, direction="+X", depth=42)
    b.hole(plane=b.ref("Y", -21), centers=[[0, 0]], diameter=12, direction="+Y", depth=17)


def d07_eccentric_cam(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 80)], depth=15)
    b.extrude(plane=b.ref("Z", 15), loops=[circle((10, 0), 36)], depth=10)
    b.hole(plane="front_plane", centers=[[10, 0]], diameter=20, through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[rect((10, 6.5), (6, 13))], through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[-18, 14], [-18, -14]], diameter=12, direction="+Z", depth=15)


def d09_angle_bracket(b):
    pts = [(0, 0), (60, 0), (60, 8), (8, 8), (8, 40), (0, 40)]
    b.extrude(plane=b.ref("Z", -50), loops=[poly(pts, corner_radii=[0, 0, 0, 5, 0, 0])], depth=100)
    b.cut(plane="top_plane", loops=[slot((29, z), (43, z), 8) for z in (-30, 30)], direction="+Y", depth=8)
    b.hole(plane="right_plane", centers=[[-25, 26], [25, 26]], diameter=9, direction="+X", depth=8)


def d10_clamp_plate(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (100, 70))], depth=15)
    b.cut(plane="front_plane", loops=[rect((0, 0), (40, 20))], through_all=True, direction="+Z")
    b.cut(plane=b.ref("Z", 15), loops=[rect((0, 32.5), (102, 7))], direction="-Z", depth=5)
    b.counterbore(plane=b.ref("Z", 15), centers=[[sx * 38, sy * 20] for sx in (1, -1) for sy in (1, -1)],
                  through_diameter=9, head_diameter=15, head_depth=8, direction="-Z", depth=15)


def d11_dovetail_slide(b):
    w = 12 + 15 / math.tan(math.radians(60))
    b.extrude(plane="front_plane", loops=[rect((0, 20), (80, 40))], depth=60)
    b.cut(plane="front_plane", loops=[poly([(-12, 0), (12, 0), (w, 15), (-w, 15)])], through_all=True,
          direction="+Z")
    b.hole(plane=b.ref("Y", 40), centers=[[20, 30], [-20, 30]], diameter=8, direction="-Y", depth=12)
    b.cut(plane=b.ref("Y", 40), loops=[rect((0, 30), (10, 62))], direction="-Y", depth=5)


def d12_cut_block(b):
    b.extrude(plane="front_plane", loops=[poly([(0, 0), (70, 0), (70, 20), (25, 20), (25, 50), (0, 50)])], depth=40)
    b.cut(plane=b.ref("X", -1), loops=[poly([(41, 29), (19, 51), (41, 51)])], direction="+X", depth=27)
    b.cut(plane=b.ref("Y", 21), loops=[rect((58, 20), (26, 16))], direction="-Y", depth=22)
    b.hole(plane=b.ref("X", -1), centers=[[12, 35]], diameter=10, direction="+X", depth=27)


def d18_gear_rack(b):
    half_b = 1.5 + 5 * math.tan(math.radians(20))
    b.extrude(plane="front_plane", loops=[rect((60, 10), (120, 20))], depth=15)
    teeth = [poly([(x - half_b, 20), (x + half_b, 20), (x + 1.5, 25), (x - 1.5, 25)])
             for x in (5 + 10 * k for k in range(12))]
    b.extrude(plane="front_plane", loops=teeth, depth=15)
    b.counterbore(plane="top_plane", centers=[[30, 7.5], [90, 7.5]], through_diameter=6.6, head_diameter=11,
                  head_depth=6, direction="+Y", depth=20)


def d21_t_slot_block(b):
    b.extrude(plane=b.ref("Z", -25), loops=[rect((0, 20), (80, 40))], depth=50)
    t_slot = [(-6, 41), (6, 41), (6, 30), (12, 30), (12, 22), (-12, 22), (-12, 30), (-6, 30)]
    b.cut(plane="front_plane", loops=[poly(t_slot)], through_all=True, direction="both")
    b.hole(plane="top_plane", centers=[[30, 0], [-30, 0]], diameter=9, through_all=True, direction="+Y")


def d22_flanged_bushing(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 60)], depth=8)
    b.extrude(plane=b.ref("Z", 8), loops=[circle((0, 0), 40)], depth=30)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=25, through_all=True, direction="+Z")
    b.cut(plane=b.ref("Z", 38), loops=[circle((0, 0), 30)], direction="-Z", depth=5)
    b.hole(plane=b.ref("X", -21), centers=[[25, 0]], diameter=4, direction="+X", depth=42)
    b.hole(plane="front_plane", centers=[polar(25, 45 + 90 * k) for k in range(4)], diameter=6, direction="+Z",
           depth=8)


def d23_cross_plate(b):
    a, r = 15, 5
    pts = [(60, -a), (60, a), (a, a), (a, 60), (-a, 60), (-a, a), (-60, a), (-60, -a), (-a, -a), (-a, -60),
           (a, -60), (a, -a)]
    b.extrude(plane="front_plane", loops=[poly(pts, corner_radii=[0, 0, r, 0, 0, r, 0, 0, r, 0, 0, r])], depth=10)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=30, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[polar(45, 90 * k) for k in range(4)], diameter=8, through_all=True,
           direction="+Z")


def d24_u_channel(b):
    u = [(-30, 0), (30, 0), (30, 48), (22, 48), (22, 8), (-22, 8), (-22, 48), (-30, 48)]
    b.extrude(plane=b.ref("X", -50), loops=[poly(u)], direction="+X", depth=100)
    b.cut(plane="top_plane", loops=[slot((-15, 0), (15, 0), 10)], direction="+Y", depth=8)
    b.hole(plane="front_plane", centers=[[30, 28], [-30, 28]], diameter=12, through_all=True, direction="both")


def d25_step_pin(b):
    profile = [(0, 0), (0, 12), (6, 12), (6, 8), (53, 8), (53, 6.5), (56, 6.5), (56, 8), (65, 8), (66, 7), (66, 0)]
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-10, 0], axis_end=[80, 0])
    b.hole(plane="right_plane", centers=[[61, 0]], diameter=4, through_all=True, direction="both")


def d26_square_flange(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (80, 80), corner_radius=10)], depth=10)
    b.extrude(plane=b.ref("Z", 10), loops=[circle((0, 0), 40)], depth=20)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=20, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[sx * 30, sy * 30] for sx in (1, -1) for sy in (1, -1)], diameter=9,
           direction="+Z", depth=10)


def d27_guide_rail(b):
    section = [(-20, 0), (20, 0), (20, 10), (10, 10), (10, 25), (-10, 25), (-10, 10), (-20, 10)]
    b.extrude(plane="right_plane", loops=[poly(section)], direction="+X", depth=150)
    b.counterbore(plane=b.ref("Y", 25), centers=[[15, 0], [75, 0], [135, 0]], through_diameter=6.6,
                  head_diameter=11, head_depth=6, direction="-Y", depth=25)


def d35_shaft_collar(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 40)], depth=14)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=20, through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[rect((10.5, 0), (21, 2))], through_all=True, direction="+Z")
    b.hole(plane=b.ref("Y", -21), centers=[[15, 7]], diameter=4.5, direction="+Y", depth=42)


# ----------------------------------------------------------------------------- D 组（组合）


def d02_bearing_block(b):
    b.extrude(plane=b.ref("Z", -25), loops=[rect((0, 7), (120, 14))], depth=50)
    housing = {"type": "path", "start": [-30, 14], "segments": [
        {"to": [30, 14]}, {"to": [30, 50]}, {"to": [-30, 50], "through": [0, 80]},
    ]}
    b.extrude(plane=b.ref("Z", -20), loops=[housing], depth=40)
    b.hole(plane="front_plane", centers=[[0, 50]], diameter=35, through_all=True, direction="both")
    b.hole(plane="top_plane", centers=[[45, 0], [-45, 0]], diameter=12, direction="+Y", depth=14)
    b.hole(plane=b.ref("Y", 81), centers=[[0, 0]], diameter=6, direction="-Y", depth=21)
    b.cut(plane="top_plane", loops=[rect((0, 0), (60, 52))], direction="+Y", depth=3)


def d03_rib_bracket(b):
    b.extrude(plane=b.ref("Z", -30), loops=[rect((0, 5), (80, 10))], depth=60)
    upright = poly([(-40, 0), (40, 0), (40, 70), (-40, 70)], corner_radii=[0, 0, 10, 10])
    b.extrude(plane=b.ref("Z", -30), loops=[upright], depth=10)
    rib = poly([(-20, 10), (20, 10), (-20, 50)])
    b.extrude(plane=b.ref("X", -40), loops=[rib], direction="+X", depth=6)
    b.extrude(plane=b.ref("X", 34), loops=[rib], direction="+X", depth=6)
    b.hole(plane=b.ref("Z", -31), centers=[[0, 45]], diameter=20, direction="+Z", depth=12)
    b.hole(plane="top_plane", centers=[[28, 12], [-28, 12]], diameter=10, direction="+Y", depth=10)


def d08_pipe_tee(b):
    b.extrude(plane=b.ref("X", -50), loops=[circle((0, 0), 40)], direction="+X", depth=100)
    b.extrude(plane=b.ref("X", -50), loops=[circle((0, 0), 60)], direction="+X", depth=8)
    b.extrude(plane=b.ref("X", 42), loops=[circle((0, 0), 60)], direction="+X", depth=8)
    b.extrude(plane="top_plane", loops=[circle((0, 0), 30)], direction="+Y", depth=60)
    b.hole(plane=b.ref("X", -50), centers=[[0, 0]], diameter=30, through_all=True, direction="+X")
    b.hole(plane=b.ref("Y", 60), centers=[[0, 0]], diameter=22, direction="-Y", depth=60)
    # 法兰孔：(y, z) = 24·(cos, sin)(45° + 90°k)；右视面 (u, v) = (z, y)
    flange_holes = [[24 * math.sin(math.radians(45 + 90 * k)), 24 * math.cos(math.radians(45 + 90 * k))]
                    for k in range(4)]
    b.hole(plane=b.ref("X", -50), centers=flange_holes, diameter=6, direction="+X", depth=8)
    b.hole(plane=b.ref("X", 50), centers=flange_holes, diameter=6, direction="-X", depth=8)


def d13_v_pulley(b):
    dz = 8 * math.tan(math.radians(19))
    rz = [(10, 0), (20, 0), (20, 15), (40, 15), (40, 5), (50, 5)]
    for zc in (13, 27):
        rz += [(50, zc - 6), (42, zc - 6 + dz), (42, zc + 6 - dz), (50, zc + 6)]
    rz += [(50, 35), (40, 35), (40, 25), (20, 25), (20, 40), (10, 40)]
    profile = []
    for r, z in rz:                       # 去掉相邻重复点；right_plane 上 (u, v) = (z, r)
        if not profile or profile[-1] != (z, r):
            profile.append((z, r))
    b.revolve(plane="right_plane", loops=[poly(profile)], axis_start=[-10, 0], axis_end=[50, 0])
    b.hole(plane="front_plane", centers=[polar(30, 90 * k) for k in range(4)], diameter=12, through_all=True,
           direction="+Z")
    b.cut(plane="front_plane", loops=[rect((6.4, 0), (12.8, 6))], through_all=True, direction="+Z")


def d14_link_rod(b):
    b.extrude(plane=b.ref("Z", 2), loops=[hull_of_circles((0, 0), 24, (120, 0), 12)], depth=16)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 60)], depth=20)
    b.extrude(plane=b.ref("Z", 2), loops=[circle((120, 0), 32)], depth=16)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=36, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[120, 0]], diameter=16, through_all=True, direction="+Z")
    pocket = slot((49, 0), (87, 0), 14)
    b.cut(plane=b.ref("Z", 18), loops=[pocket], direction="-Z", depth=4)
    b.cut(plane=b.ref("Z", 2), loops=[pocket], direction="+Z", depth=4)


def d15_motor_mount(b):
    b.extrude(plane="top_plane", loops=[rect((0, 25), (80, 50))], direction="+Y", depth=8)
    b.extrude(plane="front_plane", loops=[rect((0, 43), (80, 86))], direction="+Z", depth=8)
    gusset = poly([(8, 8), (40, 8), (8, 40)])
    b.extrude(plane=b.ref("X", -40), loops=[gusset], direction="+X", depth=6)
    b.extrude(plane=b.ref("X", 34), loops=[gusset], direction="+X", depth=6)
    b.hole(plane="front_plane", centers=[[0, 52]], diameter=22, direction="+Z", depth=8)
    b.hole(plane="front_plane", centers=[[sx * 15.5, 52 + sy * 15.5] for sx in (1, -1) for sy in (1, -1)],
           diameter=3.5, direction="+Z", depth=8)
    b.cut(plane="top_plane", loops=[slot((25, 25), (25, 39), 6), slot((-25, 25), (-25, 39), 6)], direction="+Y",
          depth=8)


def d16_heat_sink(b):
    b.extrude(plane=b.ref("Z", -25), loops=[rect((0, 3), (70, 6))], depth=50)
    b.extrude(plane=b.ref("Z", -25), loops=[rect((-24 + 8 * k, 18), (2, 24)) for k in range(7)], depth=50)
    b.hole(plane="top_plane", centers=[[sx * 31.5, sz * 15] for sx in (1, -1) for sz in (1, -1)], diameter=4.5,
           direction="+Y", depth=6)


def d17_pipe_clamp(b):
    xo, xi = math.sqrt(30 ** 2 - 7 ** 2), math.sqrt(20 ** 2 - 1)
    strap = {"type": "path", "start": [-50, 1], "segments": [
        {"to": [-50, 7]}, {"to": [-xo, 7]}, {"to": [xo, 7], "through": [0, 30]}, {"to": [50, 7]}, {"to": [50, 1]},
        {"to": [xi, 1]}, {"to": [-xi, 1], "through": [0, 20]},
    ]}
    base = {"type": "path", "start": [-50, -25], "segments": [
        {"to": [50, -25]}, {"to": [50, -1]}, {"to": [xi, -1]}, {"to": [-xi, -1], "through": [0, -20]},
        {"to": [-50, -1]},
    ]}
    b.extrude(plane=b.ref("Z", -10), loops=[strap], depth=20)
    b.extrude(plane=b.ref("Z", -10), loops=[base], depth=20)
    b.hole(plane=b.ref("Y", -26), centers=[[40, 0], [-40, 0]], diameter=7, direction="+Y", depth=34)


def d19_hinge_leaf(b):
    b.extrude(plane="front_plane", loops=[rect((30, 1.5), (60, 3))], depth=50)
    for z in (0, 20, 40):
        b.extrude(plane=b.ref("Z", z), loops=[circle((0, 4), 8)], depth=10)
    for z in (10, 30):
        b.cut(plane=b.ref("Z", z), loops=[rect((0, 4), (10, 10))], direction="+Z", depth=10)
    b.hole(plane="front_plane", centers=[[0, 4]], diameter=3, through_all=True, direction="+Z")
    b.hole(plane=b.ref("Y", 3), centers=[[40, 10], [40, 25], [40, 40]], diameter=5, direction="-Y", depth=3)
    # 90° 锥形沉头：L1 的 countersink 与 counterbore 实现相同（圆柱沉头），所以用旋转切除做锥面再阵列。
    # 平面 X = 40 上 (u, v) = (z, y)；锥面从 y = 1（半径 2.5）开到 y = 3.5（半径 5），板面 y = 3 处直径 9。
    b.revolve(plane=b.ref("X", 40), loops=[poly([(10, 1), (12.5, 1), (15, 3.5), (10, 3.5)])],
              axis_start=[10, -1], axis_end=[10, 5], cut=True)
    b.linear_pattern(count1=3, spacing1=15, direction1="+Z")


def d20_ribbed_base(b):
    b.extrude(plane="top_plane", loops=[rect((0, 0), (140, 90))], direction="+Y", depth=10)
    top = b.ref("Y", 10)
    b.extrude(plane=top, loops=[circle((sx * 55, sz * 32), 20) for sx in (1, -1) for sz in (1, -1)], direction="+Y",
              depth=20)
    b.extrude(plane=top, loops=[rect((0, 0), (90, 6))], direction="+Y", depth=15)
    b.extrude(plane=top, loops=[rect((0, 0), (6, 64))], direction="+Y", depth=15)
    b.extrude(plane=top, loops=[circle((0, 0), 30)], direction="+Y", depth=25)
    b.cut(plane="top_plane", loops=[rect((0, 0), (80, 50))], direction="+Y", depth=4)
    b.hole(plane=b.ref("Y", 35), centers=[[0, 0]], diameter=16, direction="-Y", depth=20)
    b.hole(plane="top_plane", centers=[[sx * 55, sz * 32] for sx in (1, -1) for sz in (1, -1)], diameter=8,
           direction="+Y", depth=30)


def d28_round_cup(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 80)], depth=30)
    b.extrude(plane=b.ref("Z", 26), loops=[circle((0, 0), 96)], depth=4)
    b.cut(plane=b.ref("Z", 30), loops=[circle((0, 0), 72)], direction="-Z", depth=24)
    b.hole(plane=b.ref("X", 30), centers=[[18, 0]], diameter=12, direction="+X", depth=20)
    b.hole(plane="front_plane", centers=[polar(25, 120 * k) for k in range(3)], diameter=6, direction="+Z", depth=6)


def d29_hanger_bracket(b):
    b.extrude(plane=b.ref("Y", 60), loops=[rect((0, 0), (80, 40))], direction="+Y", depth=10)
    leg = {"type": "path", "start": [-20, 60], "segments": [
        {"to": [-20, 20]}, {"to": [20, 20], "through": [0, 0]}, {"to": [20, 60]},
    ]}
    for x0 in (-30, 22):
        b.extrude(plane=b.ref("X", x0), loops=[leg], direction="+X", depth=8)
    b.hole(plane=b.ref("X", -41), centers=[[0, 20]], diameter=16, through_all=True, direction="+X")
    b.hole(plane=b.ref("Y", 70), centers=[[0, 12], [0, -12]], diameter=9, direction="-Y", depth=10)


def d30_valve_body(b):
    b.extrude(plane=b.ref("Z", -25), loops=[rect((0, 25), (70, 50))], depth=50)
    b.extrude(plane=b.ref("Y", 50), loops=[circle((0, 0), 30)], direction="+Y", depth=10)
    b.hole(plane=b.ref("X", -36), centers=[[0, 25]], diameter=20, through_all=True, direction="+X")
    b.hole(plane=b.ref("Y", 60), centers=[[0, 0]], diameter=16, direction="-Y", depth=36)
    b.hole(plane=b.ref("Z", 25), centers=[[sx * 18, 25 + sy * 18] for sx in (1, -1) for sy in (1, -1)], diameter=5,
           direction="-Z", depth=10)


def d31_lever_arm(b):
    b.extrude(plane="front_plane", loops=[circle((0, 0), 30)], depth=20)
    arms = poly([(0, -6), (80, -6), (80, 6), (6, 6), (6, 50), (-6, 50), (-6, 0), (0, 0)])
    b.extrude(plane="front_plane", loops=[arms], depth=10)
    b.extrude(plane="front_plane", loops=[circle((80, 0), 20), circle((0, 50), 20)], depth=14)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=14, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[80, 0]], diameter=8, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[0, 50]], diameter=10, through_all=True, direction="+Z")
    b.cut(plane="front_plane", loops=[rect((-4.5, 0), (9, 4))], through_all=True, direction="+Z")


def d32_tool_holder(b):
    outline = [(-65, 0), (65, 0), (65, 10), (50, 10), (50, 30), (-50, 30), (-50, 10), (-65, 10)]
    b.extrude(plane=b.ref("Z", -20), loops=[poly(outline)], depth=40)
    b.hole(plane=b.ref("Y", 30), centers=[[x, 0] for x in (-33, -11, 11, 33)], diameter=12, direction="-Y", depth=20)
    b.hole(plane="top_plane", centers=[[57.5, 0], [-57.5, 0]], diameter=8, direction="+Y", depth=10)


def d33_shell_housing(b):
    outline = [(-55, 0), (55, 0), (55, 6), (40, 6), (40, 40), (-40, 40), (-40, 6), (-55, 6)]
    b.extrude(plane=b.ref("Z", -25), loops=[poly(outline)], depth=50)
    b.cut(plane="top_plane", loops=[rect((0, 0), (70, 40))], direction="+Y", depth=34)
    b.hole(plane=b.ref("Y", 40), centers=[[0, 0]], diameter=20, direction="-Y", depth=6)
    b.hole(plane="top_plane", centers=[[47.5, 0], [-47.5, 0]], diameter=8, direction="+Y", depth=6)


def d34_fixture_plate(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (120, 80))], depth=15)
    b.cut(plane=b.ref("Z", 15), loops=[rect((0, 0), (40, 30), corner_radius=5)], direction="-Z", depth=6)
    b.hole(plane="front_plane", centers=[[k * 40, sy * 25] for k in (-1, 0, 1) for sy in (1, -1)], diameter=8,
           through_all=True, direction="+Z")
    b.hole(plane=b.ref("Z", 15), centers=[[52, 0], [-52, 0]], diameter=6, direction="-Z", depth=10)


def d36_flanged_tube(b):
    b.extrude(plane="front_plane", loops=[rect((0, 0), (70, 70))], depth=8)
    b.extrude(plane="front_plane", loops=[circle((0, 0), 40)], depth=60)
    b.extrude(plane=b.ref("Z", 52), loops=[circle((0, 0), 70)], depth=8)
    b.hole(plane="front_plane", centers=[[0, 0]], diameter=30, through_all=True, direction="+Z")
    b.hole(plane="front_plane", centers=[[sx * 27, sy * 27] for sx in (1, -1) for sy in (1, -1)], diameter=7,
           direction="+Z", depth=8)
    b.hole(plane=b.ref("Z", 60), centers=[polar(28, 90 * k) for k in range(4)], diameter=9, direction="-Z", depth=8)


def d37_column_base(b):
    c = 5 * math.cos(math.radians(45))
    profile = {"type": "path", "start": [0, 0], "segments": [
        {"to": [0, 40]}, {"to": [12, 40]}, {"to": [12, 20]}, {"to": [17, 15], "through": [17 - c, 20 - c]},
        {"to": [72, 15]}, {"to": [72, 0]},
    ]}
    b.revolve(plane="right_plane", loops=[profile], axis_start=[-10, 0], axis_end=[90, 0])
    b.hole(plane=b.ref("Z", 72), centers=[[0, 0]], diameter=10, direction="-Z", depth=20)
    b.hole(plane=b.ref("X", -16), centers=[[40, 0]], diameter=8, direction="+X", depth=32)
    b.hole(plane="front_plane", centers=[polar(30, 45 + 90 * k) for k in range(4)], diameter=9, direction="+Z",
           depth=12)


CASES = {fn.__name__: fn for fn in (
    b01_l_bracket, b02_plate_with_holes, b03_t_bracket, b04_keycap, b05_keycap_full, b06_lock_striker,
    b07_switch_panel, b11_gear_disc, b22_round_bowl, c1_mech_keyboard, c2_gearbox, c3_gear_pair,
    c4_desktop_pen_holder, d01_v_block, d02_bearing_block, d03_rib_bracket, d04_flange_cover, d05_stepped_shaft,
    d06_u_clevis, d07_eccentric_cam, d08_pipe_tee, d09_angle_bracket, d10_clamp_plate, d11_dovetail_slide,
    d12_cut_block, d13_v_pulley, d14_link_rod, d15_motor_mount, d16_heat_sink, d17_pipe_clamp, d18_gear_rack,
    d19_hinge_leaf, d20_ribbed_base, d21_t_slot_block, d22_flanged_bushing, d23_cross_plate, d24_u_channel,
    d25_step_pin, d26_square_flange, d27_guide_rail, d28_round_cup, d29_hanger_bracket, d30_valve_body,
    d31_lever_arm, d32_tool_holder, d33_shell_housing, d34_fixture_plate, d35_shaft_collar, d36_flanged_tube,
    d37_column_base,
)}


def build(case: str) -> dict:
    print(f"== {case}", flush=True)
    started = time.perf_counter()
    _post("/api/close-all-documents", {})
    session = WorkflowSession()
    session.start_part()
    builder = RecordingBuilder(session)
    try:
        CASES[case](builder)
        state = session.call("get_model_state", {}).get("state") or {}
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / f"{case}_r1"
        _post("/api/export-step", {"stepPath": str(stem.with_suffix(".step"))})
        _post("/api/save-part-as", {"savePath": str(stem.with_suffix(".SLDPRT"))})
        modeling = [c for c in builder.trajectory if c["tool"] not in QUERY_TOOLS]
        record = {
            "case": case,
            "tool_calls": len(builder.trajectory),
            "modeling_calls": len(modeling),
            "ref_planes": sum(c["tool"] == "ref_plane" for c in builder.trajectory),
            "failed_calls": len(builder.failures),
            # 每轮一个工具调用时的 LLM 调用数：建模调用 + 收尾的 finish
            "llm_calls_one_tool_per_turn": len(builder.trajectory) + 1,
            "volume_mm3": state.get("volume_mm3"),
            "bbox_mm": (state.get("bbox_mm") or {}).get("size"),
            "seconds": round(time.perf_counter() - started, 1),
            "trajectory": builder.trajectory + [{"tool": "finish", "args": {"summary": f"{case} oracle"}}],
        }
        TRAJ.mkdir(parents=True, exist_ok=True)
        (TRAJ / f"{case}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  volume={record['volume_mm3']} bbox={record['bbox_mm']} calls={record['tool_calls']} "
              f"failures={record['failed_calls']} {record['seconds']}s", flush=True)
        return {k: v for k, v in record.items() if k != "trajectory"}
    finally:
        _post("/api/close-all-documents", {})


def main() -> None:
    cases = [arg for arg in sys.argv[1:] if not arg.startswith("-")] or list(CASES)
    summary = [build(case) for case in cases]
    for item in summary:
        print(f"{item['case']}: calls={item['tool_calls']} failed={item['failed_calls']} volume={item['volume_mm3']}")


if __name__ == "__main__":
    main()
