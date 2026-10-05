"""扩展测例二（23 个）：零件、角色、提示词、运动规格、装配 rubric 与参考装配的搭法都从这里生成。

与 bench_cases.py 用同一套基本体（板、两端圆头长条、销、圆盘、直齿轮、内齿圈）和同一套规格格式，
区别是每个测例多给两样东西，使测例可以在不开 SolidWorks 的情况下自检（bench2_check.py）：

- layout(s)：参数 s（马达测例是驱动轴转角，多位姿测例是位姿名）下每个角色的平面位姿 (x, y, deg[, z])；
- steps：参考装配的搭建步骤（插入顺序即首次出现的顺序），build() 照此在 SolidWorks 里执行。

  python -m eval.assembly_scratch.bench_cases2 write     # 写 <case>.yaml、assembly_rubrics/、rubrics/、解析锚点
  python -m eval.assembly_scratch.bench_cases2 report    # 组件数、活动度、解算出的行程 / 摆角
  python -m eval.assembly_scratch.bench2_check [case]    # 自检
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from eval.assembly_scratch.bench_cases import (  # noqa: F401
    COMMON, GEAR_TIP, RC, SP, Case, R, Ref, ang, angle_item, case_yaml, circ, disk, dist, fold, g, gc, gear, link,
    mot, part_rubric, pin, plate, pol, poses_motion, r2, ring, rub, rubric_yaml,
)

HERE = Path(__file__).resolve().parent
ANALYTIC_ANCHORS = HERE / "assembly_rubrics" / "joint_anchors_analytic.json"


# ------------------------------------------------------------------ 零件几何查询

def thickness(p: dict) -> float:
    return float(p[{"plate": "t", "link": "t", "pin": "L", "disk": "t", "gear": "face", "ring": "face"}[p["kind"]]])


def holes_of(p: dict) -> list[tuple[float, float, float]]:
    k = p["kind"]
    if k == "plate":
        return [(float(x), float(y), float(d)) for x, y, d in p["holes"]]
    if k == "link":
        return [(float(x), 0.0, float(d)) for x, d in p["holes"]]
    if k == "disk":
        return [(0.0, 0.0, float(p["bore"]))] + [(float(x), float(y), float(d)) for x, y, d in p["holes"]]
    if k == "gear":
        return [(0.0, 0.0, float(p["bore"]))]
    return []


# ------------------------------------------------------------------ 测例

@dataclass
class Case2(Case):
    z: dict = field(default_factory=dict)          # 角色 → 零件 z=0 面的绝对 z
    layout: Callable = None                        # s → {角色: (x, y, deg[, z])}
    steps: list = field(default_factory=list)
    s0: object = 0.0                               # 参考装配的初始位形
    samples: list = field(default_factory=list)    # 自检取样的参数


CASES2: dict[str, Case2] = {}


def case(fn):
    c = fn()
    CASES2[c.name] = c
    return fn


def turns(deg0: float, n: int = 72) -> list[float]:
    return [deg0 + 360.0 * i / n for i in range(n)]


def ref(c: Case2, role: str, hole=None) -> str:
    """角色上的一个圆柱面引用：销取外圆，齿轮 / 齿圈取第一个圆柱面，其余按孔位（零件局部坐标）找孔。"""
    p = c.parts[c.roles[role]]
    if p["kind"] == "pin":
        return f"{role}:cyl(d={g(p['d'])})"
    if p["kind"] in ("gear", "ring") and hole is None:
        return f"{role}:cyl(i=0)"
    x, y = hole or (0.0, 0.0)
    holes = holes_of(p)
    hit = [h for h in holes if abs(h[0] - x) < 1e-6 and abs(h[1] - y) < 1e-6]
    if not hit:
        raise ValueError(f"{role}({c.roles[role]}) 在 ({x},{y}) 没有孔")
    d = hit[0][2]
    if sum(1 for h in holes if h[2] == d) == 1:
        return f"{role}:cyl(d={g(d)})"
    return f"{role}:cyl(d={g(d)}, near=[{g(x)},{g(y)}])"


def pose_of(c: Case2, s, role: str) -> tuple[float, float, float, float]:
    v = c.layout(s)[role]
    return (float(v[0]), float(v[1]), float(v[2]), float(v[3]) if len(v) > 3 else float(c.z[role]))


def plane_normal(deg: float, plane: str) -> tuple[float, float, float]:
    if plane == "front":
        return (0.0, 0.0, 1.0)
    a = math.radians(deg + (90.0 if plane == "top" else 0.0))
    return (math.cos(a), math.sin(a), 0.0)


def plane_offset(c: Case2, s, a: str, pa: str, b: str, pb: str) -> float:
    """a 的基准面原点在 b 的基准面法向上的带符号距离（mm）。"""
    xa, ya, _, za = pose_of(c, s, a)
    xb, yb, db, zb = pose_of(c, s, b)
    n = plane_normal(db, pb)
    return (xa - xb) * n[0] + (ya - yb) * n[1] + (za - zb) * n[2]


class Ref2(Ref):
    def __init__(self, c: Case2):
        super().__init__(c)
        self.done: set[str] = set()

    def put(self, role: str, ground: bool = False) -> None:
        if role in self.done:
            return
        x, y, deg, z = pose_of(self.case, self.case.s0, role)
        self.ins(role, x, y, z, deg % 360 if abs(deg % 360) > 1e-9 else 0.0, ground=ground)
        self.done.add(role)


def run_steps(q: Ref2):
    c: Case2 = q.case
    mobility = c.rubric["mobility"]
    for step in c.steps:
        kind = step[0]
        if kind == "ground":
            q.put(step[1], ground=True)
            continue
        if kind in ("fix", "rev", "cyl"):
            _, a, ha, b, hb = step
            q.put(b)
            q.put(a)
            ra, rb = ref(c, a, ha), ref(c, b, hb)
            {"fix": q.fixed, "rev": q.rev, "cyl": q.close}[kind](ra, rb)
        elif kind in ("slide", "coinc"):
            _, a, pa, b, pb = step
            q.put(b)
            q.put(a)
            if kind == "slide":
                q.plane(f"{a}:plane({pa})", f"{b}:plane({pb})", r2(plane_offset(c, c.s0, a, pa, b, pb)))
            else:
                q.call("mate", {"type": "coincident", "a": f"{a}:plane({pa})", "b": f"{b}:plane({pb})",
                                "align": "closest"})
        elif kind == "mesh":
            _, a, b = step
            pa, pb = c.parts[c.roles[a]], c.parts[c.roles[b]]
            q.mesh(a, b, pa["z"], pb["z"], pa["m"], internal=pb["kind"] == "ring" or pa["kind"] == "ring")
        elif kind == "screw":
            _, screw, nut, lead, hole = step
            q.put(screw)
            q.put(nut)
            q.call("screw_pair", {"screw": screw, "nut": nut, "lead": lead,
                                  "screw_ref": ref(c, screw), "nut_ref": ref(c, nut, hole)})
        else:
            raise ValueError(kind)
    return q.finish(mobility)


def build_reference2(name: str):
    c = CASES2[name]
    return run_steps(Ref2(c))


def mk(name, case_id, title, note, parts, roles, z, layout, steps, prompt, motion, rubric, s0, samples, info=None):
    return Case2(name, case_id, title, note, parts, roles, prompt, motion, rubric, run_steps, info or {},
                 z=z, layout=layout, steps=steps, s0=s0, samples=samples)


# ------------------------------------------------------------------ 运动学小工具

def unwrap_deg(values: list[float]) -> list[float]:
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + ((v - out[-1] + 180) % 360 - 180))
    return out


def sweep_range(c: Case2, role: str, key: str, n: int = 720) -> float:
    """驱动转一整圈时某角色 x / y / 转角的极差（自检和运动规格共用）。"""
    i = {"x": 0, "y": 1, "deg": 2}[key]
    vals = [pose_of(c, s, role)[i] for s in turns(float(c.s0), n)]
    if key == "deg":
        vals = unwrap_deg(vals)
    return max(vals) - min(vals)


def rng(layout: Callable, role: str, key: str, samples) -> float:
    """layout 在 samples 上某角色 x / y / 转角的极差，四舍五入到 0.01（写进运动规格）。"""
    i = {"x": 0, "y": 1, "deg": 2}[key]
    vals = [layout(s)[role][i] for s in samples]
    if key == "deg":
        vals = unwrap_deg(vals)
    return r2(max(vals) - min(vals))


def fine(deg0: float, n: int = 720) -> list[float]:
    return turns(deg0, n)

def slider_pos(B, L, line_pt, line_dir):
    """B 为心、半径 L 的圆与直线 line_pt + t·line_dir 的交点中 t 较大的那个（滑块在导路正向一侧）。"""
    ux, uy = line_dir
    n = math.hypot(ux, uy)
    ux, uy = ux / n, uy / n
    px, py = line_pt[0] - B[0], line_pt[1] - B[1]
    b = px * ux + py * uy
    cc = px * px + py * py - L * L
    disc = b * b - cc
    if disc < 0:
        raise ValueError("连杆够不到导路")
    t = -b + math.sqrt(disc)
    return (line_pt[0] + t * ux, line_pt[1] + t * uy)


def attach(pose, local, deg_extra=0.0):
    """刚体 pose=(x, y, deg) 上局部点 local 的世界坐标，返回 (x, y, deg)。"""
    x, y, d = pose[:3]
    a = math.radians(d)
    return (x + local[0] * math.cos(a) - local[1] * math.sin(a),
            y + local[0] * math.sin(a) + local[1] * math.cos(a), d + deg_extra)


# ================================================================== 马达测例

def slider_crank_x(B, L, y0=0.0, sign=1):
    """连杆一端在 B、另一端滑块孔心在直线 y=y0 上（sign=+1 取 +X 侧）。"""
    return (B[0] + sign * math.sqrt(L * L - (y0 - B[1]) ** 2), y0)


def slider_crank_y(B, L, x0=0.0, sign=1):
    return (x0, B[1] + sign * math.sqrt(L * L - (x0 - B[0]) ** 2))


# ---- N1 L 型双缸发动机

@case
def l_twin_engine():
    r, L = 15, 60
    parts = {
        "lt_frame": plate("机体板", [-80, 110], [-40, 110], 8, [(0, 0, 10), (-48, 0, 10)]),
        "lt_shaft": pin("曲轴", 10, 28),
        "lt_flywheel": disk("飞轮", 60, 6, 10),
        "lt_crank_gear": gear(16, 2, 8, 10),
        "lt_cam_gear": gear(32, 2, 8, 10),
        "lt_cam_shaft": pin("凸轮轴", 10, 22),
        "lt_cam": link("凸轮", 8, 24, 6, [(0, 10)]),
        "lt_crank": link("曲柄", 15, 20, 6, [(0, 10), (15, 8)]),
        "lt_crank_pin": pin("曲柄销", 8, 18),
        "lt_rod": link("连杆", 60, 14, 6, [(0, 8), (60, 6)]),
        "lt_pin12": pin("活塞销", 6, 12),
        "lt_piston": plate("活塞", [-10, 10], [-12, 12], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "lt_frame", "shaft": "lt_shaft", "flywheel": "lt_flywheel", "crank_gear": "lt_crank_gear",
             "crank": "lt_crank", "crank_pin": "lt_crank_pin", "cam_shaft": "lt_cam_shaft", "cam_gear": "lt_cam_gear",
             "cam": "lt_cam", "rod_h": "lt_rod", "rod_v": "lt_rod", "wrist_h": "lt_pin12", "wrist_v": "lt_pin12",
             "piston_h": "lt_piston", "piston_v": "lt_piston"}
    z = {"frame": 0, "shaft": -14, "flywheel": -14, "crank_gear": -8, "crank": 8, "crank_pin": 8, "cam_shaft": -8,
         "cam_gear": -8, "cam": 8, "rod_h": 14, "rod_v": 20, "wrist_h": 14, "wrist_v": 14, "piston_h": 20,
         "piston_v": 14}

    def layout(t):
        B = pol((0, 0), r, t)
        Ph, Pv = slider_crank_x(B, L), slider_crank_y(B, L)
        ah, av = ang(B, Ph), ang(B, Pv)
        cam = (-48, 0, -t / 2)
        return {"frame": (0, 0, 0), "shaft": (0, 0, t), "flywheel": (0, 0, t), "crank_gear": (0, 0, t),
                "crank": (0, 0, t), "crank_pin": (*B, t), "cam_shaft": cam, "cam_gear": cam, "cam": cam,
                "rod_h": (*B, ah), "rod_v": (*B, av), "wrist_h": (*Ph, ah), "wrist_v": (*Pv, av),
                "piston_h": (*Ph, 0), "piston_v": (*Pv, 0)}

    steps = [
        ("ground", "frame"),
        ("rev", "shaft", None, "frame", (0, 0)),
        ("fix", "flywheel", (0, 0), "shaft", None),
        ("fix", "crank_gear", None, "shaft", None),
        ("fix", "crank", (0, 0), "shaft", None),
        ("fix", "crank_pin", None, "crank", (15, 0)),
        ("rev", "cam_shaft", None, "frame", (-48, 0)),
        ("fix", "cam_gear", None, "cam_shaft", None),
        ("fix", "cam", (0, 0), "cam_shaft", None),
        ("mesh", "crank_gear", "cam_gear"),
        ("rev", "rod_h", (0, 0), "crank_pin", None),
        ("fix", "wrist_h", None, "rod_h", (60, 0)),
        ("rev", "piston_h", (0, 0), "wrist_h", None),
        ("slide", "piston_h", "top", "frame", "top"),
        ("rev", "rod_v", (0, 0), "crank_pin", None),
        ("fix", "wrist_v", None, "rod_v", (60, 0)),
        ("rev", "piston_v", (0, 0), "wrist_v", None),
        ("slide", "piston_v", "right", "frame", "right"),
    ]
    prompt = f"""装配 L 型（90°）双缸发动机：两缸共用一个曲柄销，一缸水平、一缸竖直；曲轴经 16/32 正时齿轮带凸轮轴。{COMMON}
- frame 固定，z∈[0,8]。曲轴孔 (0,0)、凸轮轴孔 (-48,0)，直径都是 10 mm。
- shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-14,14]）。板后：flywheel（z∈[-14,-8]）与 16 齿 crank_gear（z∈[-8,0]）固定在 shaft 上；
  板前：crank 的 10 mm 孔固定在 shaft 上（z∈[8,14]）。crank_pin 固定在 crank 的 8 mm 孔里（z∈[8,26]）。
- rod_h、rod_v 的 8 mm 孔都套在 crank_pin 上转动：rod_h 在 z∈[14,20]，rod_v 在 z∈[20,26]。
- 水平缸：wrist_h 固定在 rod_h 的 6 mm 孔里（z∈[14,26]），piston_h 的孔套在 wrist_h 上转动（z∈[20,26]）；
  piston_h 只能沿 frame 的 X 方向移动，孔心始终在 y=0 的直线上、曲轴 +X 侧。
- 竖直缸：wrist_v 固定在 rod_v 的 6 mm 孔里（z∈[14,26]），piston_v 的孔套在 wrist_v 上转动（z∈[14,20]）；
  piston_v 只能沿 frame 的 Y 方向移动，孔心始终在 x=0 的直线上、曲轴 +Y 侧。
- cam_shaft 穿过 (-48,0) 孔绕 Z 转动（z∈[-8,14]）；32 齿 cam_gear（z∈[-8,0]）与 cam（z∈[8,14]）固定在 cam_shaft 上。
  crank_gear 与 cam_gear 外啮合（模数 2，中心距 48），凸轮轴转速是曲轴的一半、反向。
- 曲柄 {r}、连杆 {L}，每缸行程 {2 * r} mm，两缸相位差 90°。{GEAR_TIP}
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank", "rod_h", "rod_v", "piston_h", "piston_v", "cam"],
                 ratios=[{"role": "cam", "ratio": 2, "same_direction": False}],
                 strokes=[{"role": "piston_h", "axis": "x", "mm": 2 * r}, {"role": "piston_v", "axis": "y", "mm": 2 * r}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["shaft", "flywheel", "crank_gear", "crank", "crank_pin"],
                              "cam": ["cam_shaft", "cam_gear", "cam"], "rod_h": ["rod_h"], "rod_v": ["rod_v"],
                              "piston_h": ["piston_h"], "piston_v": ["piston_v"]},
                 [("crank", "frame", R), ("cam", "frame", R), ("rod_h", "crank", R), ("rod_v", "crank", R),
                  ("piston_h", "rod_h", R), ("piston_v", "rod_v", R), ("piston_h", "frame", SP),
                  ("piston_v", "frame", SP)],
                 flex={"wrist_h": ["rod_h", "piston_h"], "wrist_v": ["rod_v", "piston_v"]},
                 couplings=[gc("crank_gear", "cam_gear", 16, 32, 2)])
    return mk("l_twin_engine", "l_twin_engine", "L 型双缸发动机",
              "两连杆共用曲柄销，水平缸 + 竖直缸相差 90°，16/32 正时齿轮带凸轮轴。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 45.0, turns(45.0), {"stroke": 2 * r})


# ---- N2 星形四缸发动机（主连杆 + 三根副连杆）

@case
def radial_engine():
    r, Lm, La, rho = 15, 70, 44, 26
    knuckle = {"e": (0, -rho), "w": (0, rho), "s": (-rho, 0)}       # 法兰局部坐标；主连杆沿法兰 +X
    guide = {"e": (1, 0), "w": (-1, 0), "s": (0, -1)}
    parts = {
        "re_frame": plate("机匣板", [-110, 110], [-110, 110], 8, [(0, 0, 10)]),
        "re_shaft": pin("曲轴", 10, 28),
        "re_prop": plate("螺旋桨", [-90, 90], [-8, 8], 6, [(0, 0, 10)]),
        "re_crank": link("曲柄", 15, 20, 6, [(0, 10), (15, 8)]),
        "re_crank_pin": pin("曲柄销", 8, 18),
        "re_flange": disk("主连杆法兰", 60, 6, 8, [(x, y, 6) for x, y in knuckle.values()]),
        "re_master": link("主连杆", Lm, 16, 6, [(0, 8), (Lm, 6)]),
        "re_rod": link("副连杆", La, 12, 6, [(0, 6), (La, 6)]),
        "re_pin12": pin("铰销", 6, 12),
        "re_piston": plate("活塞", [-12, 12], [-12, 12], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "re_frame", "shaft": "re_shaft", "prop": "re_prop", "crank": "re_crank",
             "crank_pin": "re_crank_pin", "flange": "re_flange", "master": "re_master", "wrist_n": "re_pin12",
             "piston_n": "re_piston"}
    z = {"frame": 0, "shaft": -14, "prop": -14, "crank": 8, "crank_pin": 8, "flange": 14, "master": 20,
         "wrist_n": 20, "piston_n": 26}
    for k in knuckle:
        roles |= {f"knuckle_{k}": "re_pin12", f"rod_{k}": "re_rod", f"wrist_{k}": "re_pin12", f"piston_{k}": "re_piston"}
        z |= {f"knuckle_{k}": 14, f"rod_{k}": 20, f"wrist_{k}": 20, f"piston_{k}": 26}

    def layout(t):
        B = pol((0, 0), r, t)
        Pn = slider_crank_y(B, Lm)
        phi = ang(B, Pn)
        out = {"frame": (0, 0, 0), "shaft": (0, 0, t), "prop": (0, 0, t), "crank": (0, 0, t), "crank_pin": (*B, t),
               "flange": (*B, phi), "master": (*B, phi), "wrist_n": (*Pn, phi), "piston_n": (*Pn, 0)}
        for k, loc in knuckle.items():
            K = attach((*B, phi), loc)[:2]
            P = slider_pos(K, La, (0, 0), guide[k])
            a = ang(K, P)
            out |= {f"knuckle_{k}": (*K, phi), f"rod_{k}": (*K, a), f"wrist_{k}": (*P, a), f"piston_{k}": (*P, 0)}
        return out

    steps = [
        ("ground", "frame"),
        ("rev", "shaft", None, "frame", (0, 0)),
        ("fix", "prop", (0, 0), "shaft", None),
        ("fix", "crank", (0, 0), "shaft", None),
        ("fix", "crank_pin", None, "crank", (15, 0)),
        ("rev", "flange", (0, 0), "crank_pin", None),
        ("fix", "master", (0, 0), "flange", (0, 0)),
        ("fix", "wrist_n", None, "master", (Lm, 0)),
        ("rev", "piston_n", (0, 0), "wrist_n", None),
        ("slide", "piston_n", "right", "frame", "right"),
    ]
    for k, loc in knuckle.items():
        steps += [("fix", f"knuckle_{k}", None, "flange", loc),
                  ("rev", f"rod_{k}", (0, 0), f"knuckle_{k}", None),
                  ("fix", f"wrist_{k}", None, f"rod_{k}", (La, 0)),
                  ("rev", f"piston_{k}", (0, 0), f"wrist_{k}", None),
                  ("slide", f"piston_{k}", "right" if k == "s" else "top", "frame", "right" if k == "s" else "top")]
    S = fine(45.0)
    st = {k: rng(layout, f"piston_{k}", "y" if k in "ns" else "x", S) for k in "news"}
    prompt = f"""装配星形四缸发动机：一根主连杆 + 三根副连杆，四缸沿 ±X、±Y 呈十字分布，共用一个曲柄销。{COMMON}
- frame 固定，z∈[0,8]，曲轴孔 (0,0) 直径 10。shaft 穿过该孔绕 Z 转动（z∈[-14,14]）；
  板后 prop 的中心孔固定在 shaft 上（z∈[-14,-8]）；板前 crank 的 10 mm 孔固定在 shaft 上（z∈[8,14]）。
- crank_pin 固定在 crank 的 8 mm 孔里（z∈[8,26]）。flange 的 8 mm 中心孔套在 crank_pin 上转动（z∈[14,20]）；
  master 的 8 mm 孔也套在 crank_pin 上（z∈[20,26]），并与 flange 固定在一起：master 杆身沿 flange 局部 +X 方向。
- 上缸：wrist_n 固定在 master 的 6 mm 孔里（z∈[20,32]），piston_n 的孔套在 wrist_n 上转动（z∈[26,32]），
  只能沿 frame 的 Y 方向移动，孔心始终在 x=0 的直线上、曲轴 +Y 侧。
- flange 上三个 6 mm 孔（局部 (0,-26)、(0,26)、(-26,0)）各固定一根铰销 knuckle_e、knuckle_w、knuckle_s（z∈[14,26]）。
  副连杆 rod_e、rod_w、rod_s 的原点孔分别套在这三根销上转动（z∈[20,26]）；wrist_e/w/s 固定在各副连杆的另一个孔里（z∈[20,32]）；
  piston_e/w/s 的孔套在对应 wrist 销上转动（z∈[26,32]）。
- piston_e 孔心始终在 y=0 直线上、+X 侧；piston_w 在 y=0 直线上、−X 侧；piston_s 在 x=0 直线上、−Y 侧（都只能沿各自导路移动）。
- 曲柄 {r}、主连杆 {Lm}、副连杆 {La}、法兰铰孔半径 {rho}。四缸行程分别约 上 {st['n']:.1f}、右 {st['e']:.1f}、左 {st['w']:.1f}、下 {st['s']:.1f} mm。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank", "master", "rod_e", "rod_w", "rod_s"] + [f"piston_{k}" for k in "news"],
                 strokes=[{"role": f"piston_{k}", "axis": "y" if k in "ns" else "x", "mm": st[k]} for k in "news"])
    bodies = {"frame": ["frame"], "crank": ["shaft", "prop", "crank"], "master": ["flange", "master"],
              "piston_n": ["piston_n"]}
    joints = [("crank", "frame", R), ("master", "crank", R), ("piston_n", "master", R), ("piston_n", "frame", SP)]
    flex = {"crank_pin": ["crank", "master"], "wrist_n": ["master", "piston_n"]}
    for k in knuckle:
        bodies |= {f"rod_{k}": [f"rod_{k}"], f"piston_{k}": [f"piston_{k}"]}
        joints += [(f"rod_{k}", "master", R), (f"piston_{k}", f"rod_{k}", R), (f"piston_{k}", "frame", SP)]
        flex |= {f"knuckle_{k}": ["master", f"rod_{k}"], f"wrist_{k}": [f"rod_{k}", f"piston_{k}"]}
    rubric = rub("frame", 1, bodies, joints, flex=flex)
    return mk("radial_engine", "radial_engine_4cyl", "星形四缸发动机",
              "主连杆 + 法兰上三根副连杆，十字四缸；副连杆缸行程与主缸略有差别。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 45.0, turns(45.0), st)


# ---- N3 牛头刨急回机构（摆动导杆 + 连杆滑枕，齿轮减速）

@case
def quick_return_shaper():
    r, P, Ll, Lr, yr = 25, (0, -60), 140, 50, 95
    parts = {
        "qr_frame": plate("床身板", [-100, 150], [-100, yr - 8], 8, [(0, 0, 10), (-48, 0, 10), (0, -60, 8)]),
        "qr_shaft": pin("传动轴", 10, 22),
        "qr_gear": gear(36, 2, 8, 10),
        "qr_pinion": gear(12, 2, 8, 10),
        "qr_handwheel": disk("手轮", 50, 6, 10, [(16, 0, 6)]),
        "qr_crank": link("曲柄", r, 20, 6, [(0, 10), (r, 8)]),
        "qr_crank_pin": pin("曲柄销", 8, 12),
        "qr_block": plate("滑块", [-14, 14], [-8, 8], 6, [(0, 0, 8)]),
        "qr_pivot": pin("摆杆轴", 8, 26),
        "qr_lever": link("摆杆", Ll, 16, 6, [(0, 8), (Ll, 6)]),
        "qr_pin12": pin("铰销", 6, 12),
        "qr_rod": link("连杆", Lr, 12, 6, [(0, 6), (Lr, 6)]),
        "qr_ram": plate("滑枕", [-30, 30], [-8, 8], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "qr_frame", "crank_shaft": "qr_shaft", "crank_gear": "qr_gear", "crank": "qr_crank",
             "crank_pin": "qr_crank_pin", "motor_shaft": "qr_shaft", "pinion": "qr_pinion",
             "handwheel": "qr_handwheel", "pivot": "qr_pivot", "lever": "qr_lever", "block": "qr_block",
             "tip_pin": "qr_pin12", "rod": "qr_rod", "ram_pin": "qr_pin12", "ram": "qr_ram"}
    z = {"frame": 0, "crank_shaft": -8, "crank_gear": -8, "crank": 8, "crank_pin": 8, "motor_shaft": -14,
         "pinion": -8, "handwheel": -14, "pivot": 0, "lever": 20, "block": 14, "tip_pin": 20, "rod": 26,
         "ram_pin": 26, "ram": 32}

    def layout(t):
        B = pol((0, 0), r, t)
        phi = ang(P, B)
        T = pol(P, Ll, phi)
        Q = slider_crank_x(T, Lr, yr)
        a = ang(T, Q)
        m = (-48, 0, -3 * t)
        return {"frame": (0, 0, 0), "crank_shaft": (0, 0, t), "crank_gear": (0, 0, t), "crank": (0, 0, t),
                "crank_pin": (*B, t), "motor_shaft": m, "pinion": m, "handwheel": m, "pivot": (*P, 0),
                "lever": (*P, phi), "block": (*B, phi), "tip_pin": (*T, phi), "rod": (*T, a), "ram_pin": (*Q, a),
                "ram": (*Q, 0)}

    steps = [
        ("ground", "frame"),
        ("rev", "crank_shaft", None, "frame", (0, 0)),
        ("fix", "crank_gear", None, "crank_shaft", None),
        ("fix", "crank", (0, 0), "crank_shaft", None),
        ("fix", "crank_pin", None, "crank", (r, 0)),
        ("rev", "motor_shaft", None, "frame", (-48, 0)),
        ("fix", "pinion", None, "motor_shaft", None),
        ("fix", "handwheel", (0, 0), "motor_shaft", None),
        ("mesh", "crank_gear", "pinion"),
        ("fix", "pivot", None, "frame", P),
        ("rev", "lever", (0, 0), "pivot", None),
        ("rev", "block", (0, 0), "crank_pin", None),
        ("coinc", "block", "top", "lever", "top"),
        ("fix", "tip_pin", None, "lever", (Ll, 0)),
        ("rev", "rod", (0, 0), "tip_pin", None),
        ("fix", "ram_pin", None, "rod", (Lr, 0)),
        ("rev", "ram", (0, 0), "ram_pin", None),
        ("slide", "ram", "top", "frame", "top"),
    ]
    S = fine(45.0)
    swing, stroke = rng(layout, "lever", "deg", S), rng(layout, "ram", "x", S)
    xs = [layout(s)["ram"][0] for s in S]
    fwd = sum(1 for i in range(len(xs)) if xs[(i + 1) % len(xs)] > xs[i]) / len(xs)
    prompt = f"""装配牛头刨床的急回机构：曲柄带滑块在摆杆上滑动，摆杆顶端经连杆推动滑枕往复，工作行程慢、回程快。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 (0,0)、电机轴孔 (-48,0) 直径 10，摆杆轴孔 (0,-60) 直径 8。
- crank_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-8,14]）；36 齿 crank_gear 固定在其上（z∈[-8,0]），crank 的 10 mm 孔固定在其上（z∈[8,14]）。
  crank_pin 固定在 crank 的 8 mm 孔里（z∈[8,20]）。
- motor_shaft 穿过 (-48,0) 孔绕 Z 转动（z∈[-14,8]）；12 齿 pinion（z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上。
  pinion 与 crank_gear 外啮合（模数 2，中心距 48），减速比 3。
- pivot 固定在 (0,-60) 孔里（z∈[0,26]）；lever 的 8 mm 孔套在 pivot 上转动（z∈[20,26]）。
- block 的孔套在 crank_pin 上转动（z∈[14,20]），同时只能沿 lever 的杆身方向滑动：block 孔心始终在 lever 两孔连线上、两者长边平行。
- tip_pin 固定在 lever 的 6 mm 孔里（z∈[20,32]）；rod 的原点孔套在 tip_pin 上转动（z∈[26,32]）；
  ram_pin 固定在 rod 的另一个孔里（z∈[26,38]）；ram 的孔套在 ram_pin 上转动（z∈[32,38]），只能沿 frame 的 X 方向移动，
  孔心始终在 y={yr} 的直线上（ram 的下沿面与 frame 的上沿面 y={yr - 8} 平齐），位于 tip_pin 的 +X 侧。
- 曲柄 {r}、摆杆 {Ll}（轴心距曲柄轴 60）、连杆 {Lr}。曲柄一整圈：摆杆摆角约 {swing:.1f}°，滑枕行程约 {stroke:.1f} mm。{GEAR_TIP}
对 crank_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("crank_shaft", ["frame"], ["crank", "block", "lever", "rod", "ram", "motor_shaft"],
                 ratios=[{"role": "motor_shaft", "ratio": 1 / 3, "same_direction": False}],
                 swings=[{"role": "lever", "deg": swing}], strokes=[{"role": "ram", "axis": "x", "mm": stroke}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["crank_shaft", "crank_gear", "crank"],
                              "motor": ["motor_shaft", "pinion", "handwheel"], "lever": ["lever"], "block": ["block"],
                              "rod": ["rod"], "ram": ["ram"]},
                 [("crank", "frame", R), ("motor", "frame", R), ("lever", "frame", R), ("block", "crank", R),
                  ("block", "lever", SP), ("rod", "lever", R), ("ram", "rod", R), ("ram", "frame", SP)],
                 flex={"crank_pin": ["crank", "block"], "pivot": ["frame", "lever"], "tip_pin": ["lever", "rod"],
                       "ram_pin": ["rod", "ram"]},
                 couplings=[gc("crank_gear", "pinion", 36, 12, 2)])
    return mk("quick_return_shaper", "quick_return_shaper", "牛头刨急回机构",
              "摆动导杆急回 + 连杆滑枕，12/36 齿轮；检查摆角、滑枕行程与电机轴 3 倍转速。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 45.0, turns(45.0),
              {"swing": swing, "stroke": stroke, "forward_frac": fwd})


# ---- N4 肘杆压力机

@case
def knuckle_press():
    C, U, r, Lr, Lu, Ll = (-100, 50), (0, 100), 15, 90, 50, 60
    M = (C[0], C[1] - 56)

    def knee(B, side):
        return circ(B, U, Lr, Lu, side)

    B0 = pol(C, r, 45.0)
    side = 1 if knee(B0, 1)[1] < knee(B0, -1)[1] else -1
    parts = {
        "kp_frame": plate("机身板", [-150, 40], [-70, 130], 8, [(*C, 10), (*M, 10), (*U, 8)]),
        "kp_shaft": pin("传动轴", 10, 22),
        "kp_gear": gear(42, 2, 8, 10),
        "kp_pinion": gear(14, 2, 8, 10),
        "kp_flywheel": disk("飞轮", 70, 6, 10, [(22, 0, 8), (-22, 0, 8)]),
        "kp_crank": link("曲柄", r, 20, 6, [(0, 10), (r, 8)]),
        "kp_crank_pin": pin("曲柄销", 8, 12),
        "kp_rod": link("连杆", Lr, 14, 6, [(0, 8), (Lr, 8)]),
        "kp_upper": link("上肘杆", Lu, 16, 6, [(0, 8), (Lu, 8)]),
        "kp_lower": link("下肘杆", Ll, 16, 6, [(0, 8), (Ll, 6)]),
        "kp_top_pin": pin("上铰销", 8, 26),
        "kp_knee_pin": pin("肘销", 8, 18),
        "kp_ram_pin": pin("滑块销", 6, 12),
        "kp_ram": plate("压头", [-20, 20], [-12, 12], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "kp_frame", "crank_shaft": "kp_shaft", "crank_gear": "kp_gear", "crank": "kp_crank",
             "crank_pin": "kp_crank_pin", "motor_shaft": "kp_shaft", "pinion": "kp_pinion", "flywheel": "kp_flywheel",
             "rod": "kp_rod", "top_pin": "kp_top_pin", "upper": "kp_upper", "knee_pin": "kp_knee_pin",
             "lower": "kp_lower", "ram_pin": "kp_ram_pin", "ram": "kp_ram"}
    z = {"frame": 0, "crank_shaft": -8, "crank_gear": -8, "crank": 8, "crank_pin": 8, "motor_shaft": -14,
         "pinion": -8, "flywheel": -14, "rod": 14, "top_pin": 0, "upper": 20, "knee_pin": 8, "lower": 8,
         "ram_pin": 8, "ram": 14}

    def layout(t):
        B = pol(C, r, t)
        K = knee(B, side)
        Q = slider_crank_y(K, Ll, 0.0, -1)
        au, al = ang(U, K), ang(K, Q)
        m = (*M, -3 * t)
        return {"frame": (0, 0, 0), "crank_shaft": (*C, t), "crank_gear": (*C, t), "crank": (*C, t),
                "crank_pin": (*B, t), "motor_shaft": m, "pinion": m, "flywheel": m, "rod": (*B, ang(B, K)),
                "top_pin": (*U, 0), "upper": (*U, au), "knee_pin": (*K, au), "lower": (*K, al), "ram_pin": (*Q, al),
                "ram": (*Q, 0)}

    steps = [
        ("ground", "frame"),
        ("rev", "crank_shaft", None, "frame", C),
        ("fix", "crank_gear", None, "crank_shaft", None),
        ("fix", "crank", (0, 0), "crank_shaft", None),
        ("fix", "crank_pin", None, "crank", (r, 0)),
        ("rev", "motor_shaft", None, "frame", M),
        ("fix", "pinion", None, "motor_shaft", None),
        ("fix", "flywheel", (0, 0), "motor_shaft", None),
        ("mesh", "crank_gear", "pinion"),
        ("rev", "rod", (0, 0), "crank_pin", None),
        ("fix", "top_pin", None, "frame", U),
        ("rev", "upper", (0, 0), "top_pin", None),
        ("fix", "knee_pin", None, "upper", (Lu, 0)),
        ("cyl", "rod", (Lr, 0), "knee_pin", None),
        ("rev", "lower", (0, 0), "knee_pin", None),
        ("fix", "ram_pin", None, "lower", (Ll, 0)),
        ("rev", "ram", (0, 0), "ram_pin", None),
        ("slide", "ram", "right", "frame", "right"),
    ]
    S = fine(45.0)
    swing, stroke = rng(layout, "upper", "deg", S), rng(layout, "ram", "y", S)
    prompt = f"""装配肘杆（肘节）压力机：曲柄经连杆推动肘节，上下两根肘杆接近伸直时压头产生大压力。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 ({C[0]},{C[1]})、电机轴孔 ({M[0]},{M[1]}) 直径 10；上铰孔 ({U[0]},{U[1]}) 直径 8。
- crank_shaft 穿过曲柄轴孔绕 Z 转动（z∈[-8,14]）；42 齿 crank_gear 固定在其上（z∈[-8,0]），crank 的 10 mm 孔固定在其上（z∈[8,14]）；
  crank_pin 固定在 crank 的 8 mm 孔里（z∈[8,20]）。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,8]）；14 齿 pinion（z∈[-8,0]）与 flywheel 的中心孔（z∈[-14,-8]）固定在其上，
  pinion 与 crank_gear 外啮合（模数 2，中心距 56）。
- rod 的原点孔套在 crank_pin 上转动（z∈[14,20]）。top_pin 固定在上铰孔里（z∈[0,26]），upper 的原点孔套在 top_pin 上转动（z∈[20,26]）。
- knee_pin 固定在 upper 的另一个孔里（z∈[8,26]）；rod 的另一个孔、lower 的原点孔（z∈[8,14]）都套在 knee_pin 上转动。肘节在 upper 轴线的下方。
- ram_pin 固定在 lower 的 6 mm 孔里（z∈[8,20]）；ram 的孔套在 ram_pin 上转动（z∈[14,20]），只能沿 frame 的 Y 方向移动，
  孔心始终在 x=0 的直线上、肘节下方。
- 曲柄 {r}、连杆 {Lr}、上肘杆 {Lu}、下肘杆 {Ll}。曲柄一整圈：upper 摆角约 {swing:.1f}°，压头行程约 {stroke:.1f} mm。{GEAR_TIP}
对 crank_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("crank_shaft", ["frame"], ["crank", "rod", "upper", "lower", "ram", "motor_shaft"],
                 ratios=[{"role": "motor_shaft", "ratio": 1 / 3, "same_direction": False}],
                 swings=[{"role": "upper", "deg": swing}], strokes=[{"role": "ram", "axis": "y", "mm": stroke}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["crank_shaft", "crank_gear", "crank"],
                              "motor": ["motor_shaft", "pinion", "flywheel"], "rod": ["rod"],
                              "upper": ["upper", "knee_pin"], "lower": ["lower"], "ram": ["ram"]},
                 [("crank", "frame", R), ("motor", "frame", R), ("rod", "crank", R), ("upper", "frame", R),
                  ("rod", "upper", RC), ("lower", "upper", R), ("ram", "lower", R), ("ram", "frame", SP)],
                 flex={"crank_pin": ["crank", "rod"], "top_pin": ["frame", "upper"], "ram_pin": ["lower", "ram"]},
                 couplings=[gc("crank_gear", "pinion", 42, 14, 2)])
    return mk("knuckle_press", "knuckle_press", "肘杆压力机",
              "曲柄—连杆—肘节（三件共用肘销）—压头，14/42 齿轮；检查压头行程与上肘杆摆角。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 45.0, turns(45.0),
              {"swing": swing, "stroke": stroke})


# ---- N5 十字滑块联轴器（Oldham）

@case
def oldham_coupling():
    e = 12
    parts = {
        "od_frame": plate("底板", [-80, 60], [-80, 50], 8, [(0, 0, 10), (0, -48, 10), (-50, 0, 10)]),
        "od_shaft": pin("轴", 10, 22),
        "od_gear": gear(36, 2, 8, 10),
        "od_pinion": gear(12, 2, 8, 10),
        "od_handwheel": disk("手轮", 44, 6, 10, [(14, 0, 6)]),
        "od_hub": disk("半联轴器", 50, 6, 10, [(16, 0, 6), (-16, 0, 6)]),
        "od_middle": disk("十字滑块", 40, 6, 12, [(0, 14, 4), (0, -14, 4), (14, 0, 4), (-14, 0, 4)]),
        "od_out_shaft": pin("输出轴", 10, 12),
        "od_bracket": plate("轴承架", [-60, 24], [-12, 12], 6, [(-50, 0, 10), (e, 0, 10)]),
        "od_standoff": pin("支柱", 10, 32),
    }
    roles = {"frame": "od_frame", "input_shaft": "od_shaft", "input_gear": "od_gear", "hub_in": "od_hub",
             "motor_shaft": "od_shaft", "pinion": "od_pinion", "handwheel": "od_handwheel", "standoff": "od_standoff",
             "bracket": "od_bracket", "out_shaft": "od_out_shaft", "hub_out": "od_hub", "middle": "od_middle"}
    z = {"frame": 0, "input_shaft": -8, "input_gear": -8, "hub_in": 8, "motor_shaft": -14, "pinion": -8,
         "handwheel": -14, "standoff": 0, "bracket": 26, "out_shaft": 20, "hub_out": 20, "middle": 14}

    def layout(t):
        u = (math.cos(math.radians(t)), math.sin(math.radians(t)))
        k = e * u[0]
        m = (0, -48, -3 * t)
        return {"frame": (0, 0, 0), "input_shaft": (0, 0, t), "input_gear": (0, 0, t), "hub_in": (0, 0, t),
                "motor_shaft": m, "pinion": m, "handwheel": m, "standoff": (-50, 0, 0), "bracket": (0, 0, 0),
                "out_shaft": (e, 0, t), "hub_out": (e, 0, t), "middle": (k * u[0], k * u[1], t)}

    steps = [
        ("ground", "frame"),
        ("rev", "input_shaft", None, "frame", (0, 0)),
        ("fix", "input_gear", None, "input_shaft", None),
        ("fix", "hub_in", (0, 0), "input_shaft", None),
        ("rev", "motor_shaft", None, "frame", (0, -48)),
        ("fix", "pinion", None, "motor_shaft", None),
        ("fix", "handwheel", (0, 0), "motor_shaft", None),
        ("mesh", "input_gear", "pinion"),
        ("fix", "standoff", None, "frame", (-50, 0)),
        ("fix", "bracket", (-50, 0), "standoff", None),
        ("rev", "out_shaft", None, "bracket", (e, 0)),
        ("fix", "hub_out", (0, 0), "out_shaft", None),
        ("coinc", "middle", "top", "hub_in", "top"),
        ("slide", "middle", "front", "hub_in", "front"),
        ("coinc", "middle", "right", "hub_out", "right"),
    ]
    prompt = f"""装配十字滑块联轴器（Oldham）：输入、输出两轴平行但错开 {e} mm，中间滑块在两个互相垂直的方向上滑动，两轴等速同向。{COMMON}
- frame 固定，z∈[0,8]。输入轴孔 (0,0)、电机轴孔 (0,-48)、支柱孔 (-50,0)，直径都是 10。
- input_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-8,14]）；36 齿 input_gear 固定在其上（z∈[-8,0]），hub_in 的中心孔固定在其上（z∈[8,14]）。
- motor_shaft 穿过 (0,-48) 孔绕 Z 转动（z∈[-14,8]）；12 齿 pinion（z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，
  pinion 与 input_gear 外啮合（模数 2，中心距 48）。
- standoff 固定在 (-50,0) 孔里（z∈[0,32]）；bracket 的 (-50,0) 孔固定在 standoff 上（z∈[26,32]），长边沿 frame 的 X 方向。
- out_shaft 穿过 bracket 的 ({e},0) 孔绕 Z 转动（z∈[20,32]）；hub_out 的中心孔固定在 out_shaft 上（z∈[20,26]）。
- middle（z∈[14,20]）：贴着 hub_in 的上表面，只能相对 hub_in 沿 hub_in 局部 X 方向滑动（middle 中心始终在 hub_in 局部 X 轴上，
  两者转角相同）；同时只能相对 hub_out 沿 hub_out 局部 Y 方向滑动（middle 中心始终在 hub_out 局部 Y 轴上）。
- 两轴转角始终相等；输入轴转一圈，middle 中心在 X、Y 方向各走 {e} mm。{GEAR_TIP}
对 input_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("input_shaft", ["frame", "bracket"], ["hub_in", "middle", "hub_out", "motor_shaft"],
                 ratios=[{"role": "hub_out", "ratio": 1, "same_direction": True},
                         {"role": "motor_shaft", "ratio": 1 / 3, "same_direction": False}],
                 strokes=[{"role": "middle", "axis": "x", "mm": e}, {"role": "middle", "axis": "y", "mm": e}])
    rubric = rub("frame", 1, {"frame": ["frame", "standoff", "bracket"], "input": ["input_shaft", "input_gear", "hub_in"],
                              "motor": ["motor_shaft", "pinion", "handwheel"], "output": ["out_shaft", "hub_out"],
                              "middle": ["middle"]},
                 [("input", "frame", R), ("motor", "frame", R), ("output", "frame", R), ("middle", "input", SP),
                  ("middle", "output", SP)],
                 couplings=[gc("input_gear", "pinion", 36, 12, 2)])
    return mk("oldham_coupling", "oldham_coupling", "十字滑块联轴器",
              f"两轴错开 {e} mm，滑块在两垂直方向滑动；检查两轴等速同向、滑块 X/Y 行程。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0), {"offset": e})


def corr(layout: Callable, ra: str, rb: str, key: str, samples) -> float:
    i = {"x": 0, "y": 1}[key]
    a = [layout(s)[ra][i] for s in samples]
    b = [layout(s)[rb][i] for s in samples]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))


# ---- N6 椭圆机（两侧 180°）

@case
def elliptical_trainer():
    r, Lp, Lf, H, c, Lh = 25, 200, 120, (190, 100), 100, 220
    sides = {"f": (0.0, 1), "b": (180.0, -1)}          # 曲柄相位、层的方向（板前 / 板后）
    parts = {
        "et_frame": plate("机架板", [-60, 330], [-50, 240], 8, [(0, 0, 10), (*H, 10)]),
        "et_shaft": pin("曲柄轴", 10, 28),
        "et_flywheel": disk("飞轮", 120, 6, 10, [(40, 0, 14), (-40, 0, 14), (0, 40, 14), (0, -40, 14)]),
        "et_crank": link("曲柄", r, 20, 6, [(0, 10), (r, 8)]),
        "et_pin8": pin("铰销", 8, 12),
        "et_pedal": link("踏板连杆", Lp, 16, 6, [(0, 8), (Lf, 8), (Lp, 8)]),
        "et_rocker": link("摆杆扶手", Lh, 14, 6, [(0, 8), (c, 10)]),
        "et_pivot": pin("摆杆轴", 10, 52),
        "et_foot": plate("脚踏板", [-35, 35], [-8, 8], 6, [(0, 0, 8)]),
    }
    roles = {"frame": "et_frame", "shaft": "et_shaft", "flywheel": "et_flywheel", "pivot": "et_pivot"}
    z = {"frame": 0, "shaft": -14, "flywheel": -8, "pivot": -26}
    for s, (_, d) in sides.items():
        roles |= {f"crank_{s}": "et_crank", f"cpin_{s}": "et_pin8", f"pedal_{s}": "et_pedal", f"dpin_{s}": "et_pin8",
                  f"rocker_{s}": "et_rocker", f"fpin_{s}": "et_pin8", f"foot_{s}": "et_foot"}
        if d > 0:
            z |= {f"crank_{s}": 8, f"cpin_{s}": 8, f"pedal_{s}": 14, f"dpin_{s}": 14, f"rocker_{s}": 20,
                  f"fpin_{s}": 8, f"foot_{s}": 8}
        else:
            z |= {f"crank_{s}": -14, f"cpin_{s}": -20, f"pedal_{s}": -20, f"dpin_{s}": -26, f"rocker_{s}": -26,
                  f"fpin_{s}": -20, f"foot_{s}": -14}

    def layout(t):
        out = {"frame": (0, 0, 0), "shaft": (0, 0, t), "flywheel": (0, 0, t), "pivot": (*H, 0)}
        for s, (ph, _) in sides.items():
            B = pol((0, 0), r, t + ph)
            D = circ(B, H, Lp, c, -1)
            a = ang(B, D)
            F = pol(B, Lf, a)
            out |= {f"crank_{s}": (0, 0, t + ph), f"cpin_{s}": (*B, t + ph), f"pedal_{s}": (*B, a),
                    f"dpin_{s}": (*D, a), f"rocker_{s}": (*D, ang(D, H)), f"fpin_{s}": (*F, a), f"foot_{s}": (*F, a)}
        return out

    steps = [("ground", "frame"), ("rev", "shaft", None, "frame", (0, 0)), ("fix", "flywheel", (0, 0), "shaft", None),
             ("fix", "pivot", None, "frame", H)]
    for s in sides:
        steps += [("fix", f"crank_{s}", (0, 0), "shaft", None),
                  ("fix", f"cpin_{s}", None, f"crank_{s}", (r, 0)),
                  ("rev", f"pedal_{s}", (0, 0), f"cpin_{s}", None),
                  ("fix", f"dpin_{s}", None, f"pedal_{s}", (Lp, 0)),
                  ("rev", f"rocker_{s}", (c, 0), "pivot", None),
                  ("cyl", f"rocker_{s}", (0, 0), f"dpin_{s}", None),
                  ("fix", f"fpin_{s}", None, f"pedal_{s}", (Lf, 0)),
                  ("fix", f"foot_{s}", (0, 0), f"fpin_{s}", None)]
    S = fine(30.0)
    swing = rng(layout, "rocker_f", "deg", S)
    fx, fy = rng(layout, "foot_f", "x", S), rng(layout, "foot_f", "y", S)
    rho = corr(layout, "foot_f", "foot_b", "x", S)
    prompt = f"""装配椭圆机（左右两套踏板机构共用曲柄轴、曲柄相差 180°，脚踏板走椭圆轨迹、扶手前后摆动）。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 (0,0)、摆杆轴孔 ({H[0]},{H[1]})，直径都是 10。
- shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-14,14]）；flywheel 的中心孔固定在 shaft 上（z∈[-8,-2]）。
  pivot 固定在摆杆轴孔里、两面伸出（z∈[-26,26]）。
- 前侧（板前）：crank_f 的 10 mm 孔固定在 shaft 上（z∈[8,14]）；cpin_f 固定在 crank_f 的 8 mm 孔里（z∈[8,20]）；
  pedal_f 的原点孔套在 cpin_f 上转动（z∈[14,20]）；dpin_f 固定在 pedal_f 的 X={Lp} 孔里（z∈[14,26]）；
  rocker_f 的 10 mm 孔套在 pivot 上转动、8 mm 原点孔套在 dpin_f 上（z∈[20,26]），扶手段朝上伸出；
  fpin_f 固定在 pedal_f 的 X={Lf} 孔里（z∈[8,20]），foot_f 的孔固定在 fpin_f 上（z∈[8,14]），长边沿 pedal_f 方向。
- 后侧（板后）同样一套，z 方向镜像：crank_b z∈[-14,-8]，方向与 crank_f 相差 180°；cpin_b z∈[-20,-8]；pedal_b z∈[-20,-14]；
  dpin_b z∈[-26,-14]；rocker_b z∈[-26,-20]；fpin_b z∈[-20,-8]；foot_b z∈[-14,-8]。
- 两侧 rocker 的 8 mm 孔都在 pivot 下方。曲柄 {r}、踏板连杆 {Lp}、摆杆下段 {c}。
  曲柄一整圈：扶手摆角约 {swing:.1f}°，脚踏板中心 X 向行程约 {fx:.1f} mm、Y 向约 {fy:.1f} mm，两侧交替。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    extra = {"antiphase": [{"roles": ["foot_f", "foot_b"], "axis": "x"}]} if rho < -0.95 else {}
    motion = mot("shaft", ["frame"], ["crank_f", "pedal_f", "pedal_b", "rocker_f", "rocker_b", "foot_f", "foot_b"],
                 swings=[{"role": "rocker_f", "deg": swing}, {"role": "rocker_b", "deg": swing}],
                 strokes=[{"role": "foot_f", "axis": "x", "mm": fx}, {"role": "foot_f", "axis": "y", "mm": fy},
                          {"role": "foot_b", "axis": "x", "mm": fx}], **extra)
    bodies = {"frame": ["frame", "pivot"], "crank": ["shaft", "flywheel", "crank_f", "crank_b"]}
    joints = [("crank", "frame", R)]
    flex = {}
    for s in sides:
        bodies |= {f"pedal_{s}": [f"pedal_{s}", f"fpin_{s}", f"foot_{s}"], f"rocker_{s}": [f"rocker_{s}"]}
        joints += [(f"pedal_{s}", "crank", R), (f"rocker_{s}", "frame", R), (f"pedal_{s}", f"rocker_{s}", RC)]
        flex |= {f"cpin_{s}": ["crank", f"pedal_{s}"], f"dpin_{s}": [f"pedal_{s}", f"rocker_{s}"]}
    rubric = rub("frame", 1, bodies, joints, flex=flex)
    return mk("elliptical_trainer", "elliptical_trainer", "椭圆机",
              "两套曲柄—踏板连杆—摆杆扶手四杆，曲柄相差 180°，板前板后各一套；检查扶手摆角与脚踏板轨迹。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0),
              {"swing": swing, "foot_x": fx, "foot_y": fy, "corr_x": rho})


# ---- N7 Jansen 步行腿（Strandbeest，holy numbers ×2）

@case
def jansen_leg():
    sc = 2
    m, A = 15 * sc, (-38 * sc, -7.8 * sc)
    j, b, k, c, d, e, f, g, h, i = [v * sc for v in (50, 41.5, 61.9, 39.3, 40.1, 55.8, 39.4, 36.7, 65.7, 49)]
    Mo = (48, 0)

    def pts(t):
        M = pol((0, 0), m, t)
        B = circ(M, A, j, b, -1)
        C = circ(M, A, k, c, 1)
        D = circ(A, B, d, e, 1)
        E = circ(D, C, f, g, -1)
        F = circ(E, C, h, i, -1)
        return M, B, C, D, E, F

    def bar(label, L):
        return link(label, r2(L), 12, 6, [(0, 6), (r2(L), 6)])

    parts = {
        "jl_frame": plate("机架板", [-240, 80], [-210, 100], 8, [(0, 0, 10), (*Mo, 10), (*A, 6)]),
        "jl_shaft": pin("轴", 10, 22),
        "jl_gear": gear(36, 2, 8, 10),
        "jl_pinion": gear(12, 2, 8, 10),
        "jl_handwheel": disk("手轮", 40, 6, 10, [(12, 0, 6)]),
        "jl_crank": link("曲柄", m, 20, 6, [(0, 10), (m, 6)]),
        "jl_j": bar("上连杆 j", j), "jl_k": bar("下连杆 k", k), "jl_b": bar("上三角边 b", b),
        "jl_d": bar("上三角边 d", d), "jl_c": bar("下摇杆 c", c), "jl_f": bar("连杆 f", f),
        "jl_g": bar("腿三角边 g", g), "jl_i": link("腿 i", r2(i), 14, 6, [(0, 6), (r2(i), 6)]),
        "jl_pin12": pin("铰销", 6, 12),
        "jl_pin18": pin("曲柄销", 6, 18),
        "jl_pin24": pin("长铰销", 6, 24),
        "jl_pin32": pin("机架销", 6, 32),
    }
    roles = {"frame": "jl_frame", "shaft": "jl_shaft", "crank_gear": "jl_gear", "motor_shaft": "jl_shaft",
             "pinion": "jl_pinion", "handwheel": "jl_handwheel", "crank": "jl_crank", "crank_pin": "jl_pin18",
             "a_pin": "jl_pin32", "bar_b": "jl_b", "bar_d": "jl_d", "b_pin": "jl_pin12", "bar_j": "jl_j",
             "bar_c": "jl_c", "c_pin": "jl_pin24", "bar_k": "jl_k", "bar_i": "jl_i", "bar_g": "jl_g",
             "d_pin": "jl_pin12", "bar_f": "jl_f", "e_pin": "jl_pin12", "foot": "jl_pin12"}
    L1, L2, L3, L4 = 8, 14, 20, 26
    z = {"frame": 0, "shaft": -8, "crank_gear": -8, "motor_shaft": -14, "pinion": -8, "handwheel": -14,
         "crank": L1, "crank_pin": L1, "a_pin": 0, "bar_b": L1, "bar_d": L2, "b_pin": L1, "bar_j": L2,
         "bar_c": L4, "c_pin": L1, "bar_k": L3, "bar_i": L1, "bar_g": L2, "d_pin": L1, "bar_f": L1, "e_pin": L1,
         "foot": L1}

    def layout(t):
        M, B, C, D, E, F = pts(t)
        ab, ad, ac, ai = ang(A, B), ang(A, D), ang(A, C), ang(C, F)
        mo = (*Mo, -3 * t)
        return {"frame": (0, 0, 0), "shaft": (0, 0, t), "crank_gear": (0, 0, t), "motor_shaft": mo, "pinion": mo,
                "handwheel": mo, "crank": (0, 0, t), "crank_pin": (*M, t), "a_pin": (*A, 0), "bar_b": (*A, ab),
                "bar_d": (*A, ad), "b_pin": (*B, ab), "bar_j": (*M, ang(M, B)), "bar_c": (*A, ac), "c_pin": (*C, ac),
                "bar_k": (*M, ang(M, C)), "bar_i": (*C, ai), "bar_g": (*C, ang(C, E)), "d_pin": (*D, ad),
                "bar_f": (*D, ang(D, E)), "e_pin": (*E, ang(C, E)), "foot": (*F, ai)}

    steps = [
        ("ground", "frame"),
        ("rev", "shaft", None, "frame", (0, 0)),
        ("fix", "crank_gear", None, "shaft", None),
        ("fix", "crank", (0, 0), "shaft", None),
        ("fix", "crank_pin", None, "crank", (m, 0)),
        ("rev", "motor_shaft", None, "frame", Mo),
        ("fix", "pinion", None, "motor_shaft", None),
        ("fix", "handwheel", (0, 0), "motor_shaft", None),
        ("mesh", "crank_gear", "pinion"),
        ("fix", "a_pin", None, "frame", A),
        ("rev", "bar_b", (0, 0), "a_pin", None),
        ("fix", "bar_d", (0, 0), "bar_b", (0, 0)),
        ("fix", "b_pin", None, "bar_b", (r2(b), 0)),
        ("rev", "bar_j", (0, 0), "crank_pin", None),
        ("cyl", "bar_j", (r2(j), 0), "b_pin", None),
        ("rev", "bar_c", (0, 0), "a_pin", None),
        ("fix", "c_pin", None, "bar_c", (r2(c), 0)),
        ("rev", "bar_k", (0, 0), "crank_pin", None),
        ("cyl", "bar_k", (r2(k), 0), "c_pin", None),
        ("rev", "bar_i", (0, 0), "c_pin", None),
        ("fix", "bar_g", (0, 0), "bar_i", (0, 0)),
        ("fix", "d_pin", None, "bar_d", (r2(d), 0)),
        ("rev", "bar_f", (0, 0), "d_pin", None),
        ("fix", "e_pin", None, "bar_g", (r2(g), 0)),
        ("cyl", "bar_f", (r2(f), 0), "e_pin", None),
        ("fix", "foot", None, "bar_i", (r2(i), 0)),
    ]
    S = fine(30.0)
    fx, fy = rng(layout, "foot", "x", S), rng(layout, "foot", "y", S)
    sb = rng(layout, "bar_b", "deg", S)
    prompt = f"""装配 Theo Jansen 步行腿（Strandbeest 腿，经典 "holy numbers" 放大 2 倍）：曲柄一圈，脚尖走出扁平的 D 形步态。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 (0,0)、电机轴孔 ({Mo[0]},{Mo[1]}) 直径 10；机架铰孔 A=({r2(A[0])},{r2(A[1])}) 直径 6。
- shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-8,14]）；36 齿 crank_gear 固定在其上（z∈[-8,0]）；crank 的 10 mm 孔固定在其上（z∈[8,14]）。
  crank_pin 固定在 crank 的 6 mm 孔里（z∈[8,26]）。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,8]）；12 齿 pinion（z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，与 crank_gear 外啮合。
- 各杆都是两孔长条（孔径 6，原点孔在一端），长度：j={r2(j)}、k={r2(k)}、b={r2(b)}、d={r2(d)}、c={r2(c)}、f={r2(f)}、g={r2(g)}、i={r2(i)}。
- a_pin 固定在 A 孔里（z∈[0,32]）。bar_b（z∈[8,14]）与 bar_d（z∈[14,20]）的原点孔都套在 a_pin 上，两杆固定成上三角（b、d 之间张角保持，
  b 端点 B 与 d 端点 D 相距 {r2(e)}）；bar_c 的原点孔也套在 a_pin 上转动（z∈[26,32]）。
- b_pin 固定在 bar_b 的另一孔 B（z∈[8,20]）；bar_j 的原点孔套在 crank_pin 上（z∈[14,20]），另一孔套在 b_pin 上。
- c_pin 固定在 bar_c 的另一孔 C（z∈[8,32]）；bar_k 的原点孔套在 crank_pin 上（z∈[20,26]），另一孔套在 c_pin 上。
- bar_i（z∈[8,14]）与 bar_g（z∈[14,20]）的原点孔都套在 c_pin 上，两杆固定成腿三角（g 端点 E 与 i 端点 F 相距 {r2(h)}）。
- d_pin 固定在 bar_d 的另一孔 D（z∈[8,20]），e_pin 固定在 bar_g 的另一孔 E（z∈[8,20]）；bar_f（z∈[8,14]）两孔分别套在 d_pin、e_pin 上。
- foot 固定在 bar_i 的另一孔 F（z∈[8,20]）作为脚尖。
- 装配分支：B 在 A 上方，C 在 A 下方，D 在 A 左侧，E 在 C 左侧，F（脚尖）在最下方。
  曲柄一整圈：脚尖 X 向行程约 {fx:.1f} mm、Y 向约 {fy:.1f} mm，上三角摆角约 {sb:.1f}°。{GEAR_TIP}
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank", "bar_j", "bar_k", "bar_b", "bar_c", "bar_f", "bar_i", "foot"],
                 ratios=[{"role": "motor_shaft", "ratio": 1 / 3, "same_direction": False}],
                 swings=[{"role": "bar_b", "deg": sb}],
                 strokes=[{"role": "foot", "axis": "x", "mm": fx}, {"role": "foot", "axis": "y", "mm": fy}])
    rubric = rub("frame", 1, {"frame": ["frame", "a_pin"], "crank": ["shaft", "crank_gear", "crank", "crank_pin"],
                              "motor": ["motor_shaft", "pinion", "handwheel"], "upper": ["bar_b", "bar_d"],
                              "j": ["bar_j"], "c": ["bar_c", "c_pin"], "k": ["bar_k"],
                              "leg": ["bar_i", "bar_g", "foot"], "f": ["bar_f"]},
                 [("crank", "frame", R), ("motor", "frame", R), ("upper", "frame", R), ("c", "frame", R),
                  ("j", "crank", R), ("j", "upper", RC), ("k", "crank", R), ("k", "c", RC), ("leg", "c", R),
                  ("f", "upper", R), ("f", "leg", RC)],
                 flex={"b_pin": ["upper", "j"], "d_pin": ["upper", "f"], "e_pin": ["leg", "f"]},
                 couplings=[gc("crank_gear", "pinion", 36, 12, 2)])
    return mk("jansen_leg", "jansen_leg", "Jansen 步行腿",
              "Strandbeest 八杆腿（两个三角刚体 + 6 根杆），12/36 齿轮驱动；检查脚尖步态包络与上三角摆角。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0),
              {"foot_x": fx, "foot_y": fy, "swing_b": sb})


# ---- N8 Chebyshev λ 步进输送机（两组 λ 机构相差 180°）

@case
def lambda_conveyor():
    a, Dp, Lc, Lr = 20, (-40, 0), 100, 50
    Mo = (0, -45)
    sides = {"f": (0.0, 1), "b": (180.0, -1)}
    parts = {
        "lc_frame": plate("机架板", [-110, 60], [-70, 120], 8, [(0, 0, 10), (*Mo, 10), (*Dp, 8)]),
        "lc_shaft": pin("曲柄轴", 10, 34),
        "lc_motor_shaft": pin("电机轴", 10, 22),
        "lc_gear": gear(30, 2, 8, 10),
        "lc_pinion": gear(15, 2, 8, 10),
        "lc_handwheel": disk("手轮", 40, 6, 10, [(12, 0, 6)]),
        "lc_d_pin": pin("摇杆轴", 8, 34),
        "lc_crank": link("曲柄", a, 20, 6, [(0, 10), (a, 8)]),
        "lc_pin8": pin("铰销", 8, 12),
        "lc_coupler": link("λ 连杆", Lc, 14, 6, [(0, 8), (Lc / 2, 8), (Lc, 8)]),
        "lc_rocker": link("摇杆", Lr, 14, 6, [(0, 8), (Lr, 8)]),
    }
    roles = {"frame": "lc_frame", "shaft": "lc_shaft", "crank_gear": "lc_gear", "motor_shaft": "lc_motor_shaft",
             "pinion": "lc_pinion", "handwheel": "lc_handwheel", "d_pin": "lc_d_pin"}
    z = {"frame": 0, "shaft": -20, "crank_gear": -8, "motor_shaft": -14, "pinion": -8, "handwheel": -14,
         "d_pin": -20}
    for s, (_, dz) in sides.items():
        roles |= {f"crank_{s}": "lc_crank", f"cpin_{s}": "lc_pin8", f"coupler_{s}": "lc_coupler",
                  f"rocker_{s}": "lc_rocker", f"mpin_{s}": "lc_pin8", f"foot_{s}": "lc_pin8"}
        if dz > 0:
            z |= {f"crank_{s}": 8, f"cpin_{s}": 8, f"coupler_{s}": 14, f"rocker_{s}": 8, f"mpin_{s}": 8, f"foot_{s}": 14}
        else:
            z |= {f"crank_{s}": -20, f"cpin_{s}": -26, f"coupler_{s}": -26, f"rocker_{s}": -20, f"mpin_{s}": -26,
                  f"foot_{s}": -32}

    def layout(t):
        mo = (*Mo, -2 * t)
        out = {"frame": (0, 0, 0), "shaft": (0, 0, t), "crank_gear": (0, 0, t), "motor_shaft": mo, "pinion": mo,
               "handwheel": mo, "d_pin": (*Dp, 0)}
        for s, (ph, _) in sides.items():
            B = pol((0, 0), a, t + ph)
            C = circ(B, Dp, Lc / 2, Lr, -1)
            P = (2 * C[0] - B[0], 2 * C[1] - B[1])
            ac = ang(B, C)
            out |= {f"crank_{s}": (0, 0, t + ph), f"cpin_{s}": (*B, t + ph), f"coupler_{s}": (*B, ac),
                    f"rocker_{s}": (*Dp, ang(Dp, C)), f"mpin_{s}": (*C, ang(Dp, C)), f"foot_{s}": (*P, ac)}
        return out

    steps = [("ground", "frame"), ("rev", "shaft", None, "frame", (0, 0)),
             ("fix", "crank_gear", None, "shaft", None), ("rev", "motor_shaft", None, "frame", Mo),
             ("fix", "pinion", None, "motor_shaft", None), ("fix", "handwheel", (0, 0), "motor_shaft", None),
             ("mesh", "crank_gear", "pinion"), ("fix", "d_pin", None, "frame", Dp)]
    for s in sides:
        steps += [("fix", f"crank_{s}", (0, 0), "shaft", None),
                  ("fix", f"cpin_{s}", None, f"crank_{s}", (a, 0)),
                  ("rev", f"coupler_{s}", (0, 0), f"cpin_{s}", None),
                  ("rev", f"rocker_{s}", (0, 0), "d_pin", None),
                  ("fix", f"mpin_{s}", None, f"rocker_{s}", (Lr, 0)),
                  ("cyl", f"coupler_{s}", (Lc / 2, 0), f"mpin_{s}", None),
                  ("fix", f"foot_{s}", None, f"coupler_{s}", (Lc, 0))]
    S = fine(30.0)
    fx, fy = rng(layout, "foot_f", "x", S), rng(layout, "foot_f", "y", S)
    ys = [layout(s)["foot_f"][1] for s in S]
    flat = sum(1 for v in ys if v - min(ys) < 1.0) / len(ys)
    rho = corr(layout, "foot_f", "foot_b", "x", S)
    prompt = f"""装配 Chebyshev λ 步进输送机：两组 λ 型四杆（曲柄 {a}、机架 40、摇杆 {Lr}、λ 连杆 {Lc}，摇杆接连杆中点）共用曲柄轴、曲柄相差 180°，
推爪（连杆末端）大约 {flat * 100:.0f}% 的时间沿一条水平直线走，两组交替推送。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 (0,0)、电机轴孔 ({Mo[0]},{Mo[1]}) 直径 10；摇杆轴孔 ({Dp[0]},{Dp[1]}) 直径 8。
- shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-20,14]）；30 齿 crank_gear 固定在其上（z∈[-8,0]）。
  motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,8]）；15 齿 pinion（z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，
  pinion 与 crank_gear 外啮合（模数 2，中心距 45）。
- d_pin 固定在摇杆轴孔里、两面伸出（z∈[-20,14]）。
- 前组（板前）：crank_f 的 10 mm 孔固定在 shaft 上（z∈[8,14]）；cpin_f 固定在 crank_f 的 8 mm 孔里（z∈[8,20]）；
  coupler_f（三孔：0、{Lc // 2}、{Lc}）的原点孔套在 cpin_f 上转动（z∈[14,20]）；rocker_f 的原点孔套在 d_pin 上转动（z∈[8,14]），
  mpin_f 固定在 rocker_f 的另一孔里（z∈[8,20]），coupler_f 的中孔套在 mpin_f 上；foot_f 固定在 coupler_f 的末孔（z∈[14,26]）。
- 后组（板后，让出齿轮层）：crank_b z∈[-20,-14]，方向与 crank_f 相差 180°；cpin_b z∈[-26,-14]；coupler_b z∈[-26,-20]；
  rocker_b z∈[-20,-14]；mpin_b z∈[-26,-14]；foot_b z∈[-32,-20]。
- 两组的连杆中点都在摇杆轴上方偏右（推爪在曲柄轴上方，y 约 80～98）。曲柄一整圈：推爪 X 向行程约 {fx:.1f} mm、Y 向约 {fy:.1f} mm。{GEAR_TIP}
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    extra = {"antiphase": [{"roles": ["foot_f", "foot_b"], "axis": "x"}]} if rho < -0.95 else {}
    motion = mot("shaft", ["frame"], ["crank_f", "coupler_f", "coupler_b", "rocker_f", "rocker_b", "foot_f", "foot_b"],
                 ratios=[{"role": "motor_shaft", "ratio": 0.5, "same_direction": False}],
                 strokes=[{"role": "foot_f", "axis": "x", "mm": fx}, {"role": "foot_f", "axis": "y", "mm": fy},
                          {"role": "foot_b", "axis": "x", "mm": fx}], **extra)
    bodies = {"frame": ["frame", "d_pin"], "crank": ["shaft", "crank_gear", "crank_f", "crank_b"],
              "motor": ["motor_shaft", "pinion", "handwheel"]}
    joints = [("crank", "frame", R), ("motor", "frame", R)]
    flex = {}
    for s in sides:
        bodies |= {f"coupler_{s}": [f"coupler_{s}", f"foot_{s}"], f"rocker_{s}": [f"rocker_{s}"]}
        joints += [(f"coupler_{s}", "crank", R), (f"rocker_{s}", "frame", R), (f"coupler_{s}", f"rocker_{s}", RC)]
        flex |= {f"cpin_{s}": ["crank", f"coupler_{s}"], f"mpin_{s}": [f"rocker_{s}", f"coupler_{s}"]}
    rubric = rub("frame", 1, bodies, joints, flex=flex, couplings=[gc("crank_gear", "pinion", 30, 15, 2)])
    return mk("lambda_conveyor", "lambda_conveyor", "Chebyshev λ 步进输送机",
              "两组 λ 近似直线机构相差 180°，15/30 齿轮在板后；检查推爪包络与两组交替。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0),
              {"foot_x": fx, "foot_y": fy, "flat_frac": flat, "corr_x": rho})


# ---- N9 齿轮五杆绘图机

@case
def geared_five_bar():
    O2, r, L = (60, 0), 20, 70
    parts = {
        "fb_frame": plate("机架板", [-50, 110], [-50, 130], 8, [(0, 0, 10), (*O2, 10)]),
        "fb_shaft1": pin("主动轴", 10, 28),
        "fb_shaft2": pin("从动轴", 10, 22),
        "fb_flywheel": disk("飞轮", 50, 6, 10, [(15, 0, 6), (-15, 0, 6)]),
        "fb_gear": gear(30, 2, 8, 10),
        "fb_crank": link("曲柄", r, 16, 6, [(0, 10), (r, 6)]),
        "fb_bar": link("连杆", L, 12, 6, [(0, 6), (L, 6)]),
        "fb_pin12": pin("铰销", 6, 12),
        "fb_pin18": pin("长铰销", 6, 18),
        "fb_pen": pin("笔", 6, 18),
    }
    roles = {"frame": "fb_frame", "shaft1": "fb_shaft1", "flywheel": "fb_flywheel", "gear1": "fb_gear",
             "crank1": "fb_crank", "shaft2": "fb_shaft2", "gear2": "fb_gear", "crank2": "fb_crank",
             "pin_b1": "fb_pin12", "pin_b2": "fb_pin18", "bar1": "fb_bar", "bar2": "fb_bar", "pen": "fb_pen"}
    z = {"frame": 0, "shaft1": -14, "flywheel": -14, "gear1": -8, "crank1": 8, "shaft2": -8, "gear2": -8,
         "crank2": 8, "pin_b1": 8, "pin_b2": 8, "bar1": 14, "bar2": 20, "pen": 14}

    def layout(t):
        t2 = 90 - t
        B1, B2 = pol((0, 0), r, t), pol(O2, r, t2)
        P = circ(B1, B2, L, L, 1)
        a1, a2 = ang(B1, P), ang(B2, P)
        return {"frame": (0, 0, 0), "shaft1": (0, 0, t), "flywheel": (0, 0, t), "gear1": (0, 0, t),
                "crank1": (0, 0, t), "shaft2": (*O2, t2), "gear2": (*O2, t2), "crank2": (*O2, t2),
                "pin_b1": (*B1, t), "pin_b2": (*B2, t2), "bar1": (*B1, a1), "bar2": (*B2, a2), "pen": (*P, a2)}

    steps = [
        ("ground", "frame"),
        ("rev", "shaft1", None, "frame", (0, 0)),
        ("fix", "flywheel", (0, 0), "shaft1", None),
        ("fix", "gear1", None, "shaft1", None),
        ("fix", "crank1", (0, 0), "shaft1", None),
        ("rev", "shaft2", None, "frame", O2),
        ("fix", "gear2", None, "shaft2", None),
        ("fix", "crank2", (0, 0), "shaft2", None),
        ("mesh", "gear1", "gear2"),
        ("fix", "pin_b1", None, "crank1", (r, 0)),
        ("fix", "pin_b2", None, "crank2", (r, 0)),
        ("rev", "bar1", (0, 0), "pin_b1", None),
        ("rev", "bar2", (0, 0), "pin_b2", None),
        ("fix", "pen", None, "bar2", (L, 0)),
        ("cyl", "bar1", (L, 0), "pen", None),
    ]
    S = fine(30.0)
    px, py = rng(layout, "pen", "x", S), rng(layout, "pen", "y", S)
    prompt = f"""装配齿轮五杆绘图机：两根曲柄由一对等齿数齿轮反向同步，两根连杆在笔处铰接，笔尖画出闭合花瓣曲线。{COMMON}
- frame 固定，z∈[0,8]。两个轴孔 (0,0)、({O2[0]},{O2[1]})，直径 10。
- shaft1 穿过 (0,0) 孔绕 Z 转动（z∈[-14,14]）；flywheel 的中心孔（z∈[-14,-8]）、30 齿 gear1（z∈[-8,0]）、crank1 的 10 mm 孔（z∈[8,14]）固定在其上。
- shaft2 穿过 ({O2[0]},{O2[1]}) 孔绕 Z 转动（z∈[-8,14]）；30 齿 gear2（z∈[-8,0]）、crank2 的 10 mm 孔（z∈[8,14]）固定在其上。
  gear1 与 gear2 外啮合（模数 2，中心距 60），两曲柄等速反向。crank1 指向 +Y 时，crank2 指向 +X（相位相差 90°）。
- pin_b1 固定在 crank1 的 6 mm 孔里（z∈[8,20]）；pin_b2 固定在 crank2 的 6 mm 孔里（z∈[8,26]）。
- bar1 的原点孔套在 pin_b1 上转动（z∈[14,20]）；bar2 的原点孔套在 pin_b2 上转动（z∈[20,26]）。
- pen 固定在 bar2 的另一孔里（z∈[14,32]），bar1 的另一孔也套在 pen 上。笔在两曲柄上方。
- 曲柄 {r}、连杆 {L}。shaft1 一整圈：笔尖 X 向行程约 {px:.1f} mm、Y 向约 {py:.1f} mm。{GEAR_TIP}
对 shaft1 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft1", ["frame"], ["crank1", "crank2", "bar1", "bar2", "pen"],
                 ratios=[{"role": "crank2", "ratio": 1, "same_direction": False}],
                 strokes=[{"role": "pen", "axis": "x", "mm": px}, {"role": "pen", "axis": "y", "mm": py}])
    rubric = rub("frame", 1, {"frame": ["frame"], "c1": ["shaft1", "flywheel", "gear1", "crank1"],
                              "c2": ["shaft2", "gear2", "crank2"], "bar1": ["bar1"], "bar2": ["bar2", "pen"]},
                 [("c1", "frame", R), ("c2", "frame", R), ("bar1", "c1", R), ("bar2", "c2", R), ("bar1", "bar2", RC)],
                 flex={"pin_b1": ["c1", "bar1"], "pin_b2": ["c2", "bar2"]},
                 couplings=[gc("gear1", "gear2", 30, 30, 2)])
    return mk("geared_five_bar", "geared_five_bar", "齿轮五杆绘图机",
              "两曲柄经 30/30 齿轮反向同步、相位差 90°，五杆闭链带笔；检查反向等速与笔尖包络。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0), {"pen_x": px, "pen_y": py})


# ---- N10 行星搅拌机（太阳轮固定、双行星带搅拌头）

@case
def planetary_mixer():
    Mo = (-64, 0)
    parts = {
        "pm_frame": plate("底座板", [-110, 90], [-80, 80], 8, [(0, 0, 10), (*Mo, 10)]),
        "pm_post": pin("中心柱", 10, 30),
        "pm_sun": gear(24, 2, 8, 10),
        "pm_carrier_gear": gear(48, 2, 8, 10),
        "pm_arm": link("行星架", 96, 20, 6, [(0, 8), (48, 10), (96, 8)]),
        "pm_planet": gear(24, 2, 8, 8),
        "pm_axle": pin("行星轴", 8, 20),
        "pm_beater": link("搅拌头", 40, 12, 6, [(0, 8), (40, 6)]),
        "pm_motor_shaft": pin("电机轴", 10, 30),
        "pm_pinion": gear(16, 2, 8, 10),
        "pm_handwheel": disk("手轮", 50, 6, 10, [(16, 0, 6)]),
    }
    roles = {"frame": "pm_frame", "post": "pm_post", "sun": "pm_sun", "carrier_gear": "pm_carrier_gear",
             "arm": "pm_arm", "motor_shaft": "pm_motor_shaft", "pinion": "pm_pinion", "handwheel": "pm_handwheel"}
    z = {"frame": 0, "post": 0, "sun": 16, "carrier_gear": 8, "arm": 24, "motor_shaft": -14, "pinion": 8,
         "handwheel": -14}
    hole = {"a": (0, 0), "b": (96, 0)}
    for s in hole:
        roles |= {f"planet_{s}": "pm_planet", f"axle_{s}": "pm_axle", f"beater_{s}": "pm_beater"}
        z |= {f"planet_{s}": 16, f"axle_{s}": 16, f"beater_{s}": 30}

    def layout(t):
        c = -t / 3
        mo = (*Mo, t)
        out = {"frame": (0, 0, 0), "post": (0, 0, 0), "sun": (0, 0, 0), "carrier_gear": (0, 0, c),
               "arm": (*pol((0, 0), 48, c + 180), c), "motor_shaft": mo, "pinion": mo, "handwheel": mo}
        for s, ph in (("a", 180), ("b", 0)):
            P = pol((0, 0), 48, c + ph)
            p = 2 * c + (0 if s == "a" else 7.5)
            out |= {f"planet_{s}": (*P, p), f"axle_{s}": (*P, p), f"beater_{s}": (*P, p)}
        return out

    steps = [("ground", "frame"), ("fix", "post", None, "frame", (0, 0)), ("fix", "sun", None, "post", None),
             ("rev", "carrier_gear", None, "post", None), ("fix", "arm", (48, 0), "carrier_gear", None),
             ("rev", "motor_shaft", None, "frame", Mo), ("fix", "pinion", None, "motor_shaft", None),
             ("fix", "handwheel", (0, 0), "motor_shaft", None), ("mesh", "pinion", "carrier_gear")]
    for s, hxy in hole.items():
        steps += [("rev", f"axle_{s}", None, "arm", hxy), ("fix", f"planet_{s}", None, f"axle_{s}", None),
                  ("fix", f"beater_{s}", (0, 0), f"axle_{s}", None), ("mesh", "sun", f"planet_{s}")]
    prompt = f"""装配行星搅拌机：太阳轮固定，电机经小齿轮带行星架公转，两只行星轮绕太阳轮滚动、带着搅拌头一边公转一边自转。{COMMON}
- frame 固定，z∈[0,8]。中心孔 (0,0)、电机轴孔 ({Mo[0]},{Mo[1]})，直径 10。
- post 固定在中心孔里（z∈[0,30]）；24 齿 sun 固定在 post 上（z∈[16,24]）。
- 48 齿 carrier_gear 套在 post 上转动（z∈[8,16]）；arm（三孔：0、48、96）的 X=48 孔套在 post 上（z∈[24,30]），并与 carrier_gear 固定。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,16]）；16 齿 pinion（z∈[8,16]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，
  pinion 与 carrier_gear 外啮合（模数 2，中心距 64）。
- 两套行星：axle_a、axle_b 分别插在 arm 的 X=0、X=96 孔里绕 Z 转动（z∈[16,36]）；24 齿 planet_a/planet_b 固定在对应轴上（z∈[16,24]），
  都与 sun 外啮合（中心距 48）；beater_a/beater_b 的 8 mm 孔固定在对应轴上（z∈[30,36]）。
- 电机转 3 圈行星架反向转 1 圈；行星轮绝对转速是行星架的 2 倍、同向。{GEAR_TIP}
对 motor_shaft 做 check_motion（电机转 3 圈，行星架正好转一周），确认无穿模后再保存。"""
    S = [1080.0 * i / 2160 for i in range(2160)]
    motion = mot("motor_shaft", ["frame", "sun"], ["carrier_gear", "arm", "planet_a", "planet_b", "beater_a"],
                 ratios=[{"role": "arm", "ratio": 3, "same_direction": False},
                         {"role": "planet_a", "ratio": 1.5, "same_direction": False},
                         {"role": "beater_b", "ratio": 1.5, "same_direction": False}],
                 strokes=[{"role": "planet_a", "axis": "y", "mm": rng(layout, "planet_a", "y", S)}],
                 sweep_deg=1080, dense_frames=361)
    bodies = {"frame": ["frame", "post", "sun"], "carrier": ["carrier_gear", "arm"],
              "motor": ["motor_shaft", "pinion", "handwheel"]}
    joints = [("motor", "frame", R), ("carrier", "frame", R)]
    couplings = [gc("pinion", "carrier_gear", 16, 48, 2)]
    for s in hole:
        bodies[f"planet_{s}"] = [f"planet_{s}", f"axle_{s}", f"beater_{s}"]
        joints.append((f"planet_{s}", "carrier", R))
        couplings.append(gc("sun", f"planet_{s}", 24, 24, 2))
    rubric = rub("frame", 1, bodies, joints, couplings=couplings)
    return mk("planetary_mixer", "planetary_mixer", "行星搅拌机",
              "固定太阳轮 + 双行星 + 齿轮驱动行星架（3 对啮合）；检查行星架 1/3 反向、行星轮 2/3 反向。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 0.0, turns(0.0))


# ---- N11 万花尺（内齿圈固定、双行星带笔）

@case
def spirograph():
    Mo = (0, -60)
    pens = {"a": (24, (0, 0)), "b": (12, (60, 0))}
    parts = {
        "sg_frame": plate("底板", [-90, 90], [-110, 80], 8, [(0, 0, 10), (*Mo, 10)]),
        "sg_carrier_shaft": pin("行星架轴", 10, 28),
        "sg_carrier_gear": gear(45, 2, 8, 10),
        "sg_motor_shaft": pin("电机轴", 10, 22),
        "sg_pinion": gear(15, 2, 8, 10),
        "sg_handwheel": disk("手轮", 40, 6, 10, [(12, 0, 6)]),
        "sg_ring": ring(60, 1.5, 6, 110),
        "sg_arm": link("行星架", 60, 14, 6, [(0, 6), (30, 10), (60, 6)]),
        "sg_planet": gear(20, 1.5, 6, 6),
        "sg_axle": pin("行星轴", 6, 18),
        "sg_pen_arm_a": link("笔杆 A", 24, 10, 6, [(0, 6), (24, 4)]),
        "sg_pen_arm_b": link("笔杆 B", 12, 10, 6, [(0, 6), (12, 4)]),
        "sg_pen": pin("笔", 4, 16),
    }
    roles = {"frame": "sg_frame", "carrier_shaft": "sg_carrier_shaft", "carrier_gear": "sg_carrier_gear",
             "motor_shaft": "sg_motor_shaft", "pinion": "sg_pinion", "handwheel": "sg_handwheel", "ring": "sg_ring",
             "arm": "sg_arm"}
    z = {"frame": 0, "carrier_shaft": -8, "carrier_gear": -8, "motor_shaft": -14, "pinion": -8, "handwheel": -14,
         "ring": 8, "arm": 14}
    for s in pens:
        roles |= {f"planet_{s}": "sg_planet", f"axle_{s}": "sg_axle", f"pen_arm_{s}": f"sg_pen_arm_{s}",
                  f"pen_{s}": "sg_pen"}
        z |= {f"planet_{s}": 8, f"axle_{s}": 8, f"pen_arm_{s}": 20, f"pen_{s}": 20}

    def layout(t):
        c = -t / 3
        mo = (*Mo, t)
        out = {"frame": (0, 0, 0), "carrier_shaft": (0, 0, c), "carrier_gear": (0, 0, c), "motor_shaft": mo,
               "pinion": mo, "handwheel": mo, "ring": (0, 0, 0), "arm": (*pol((0, 0), 30, c + 180), c)}
        for s, ph in (("a", 180), ("b", 0)):
            L = pens[s][0]
            P = pol((0, 0), 30, c + ph)
            p = -2 * c + (0 if s == "a" else 9)
            out |= {f"planet_{s}": (*P, p), f"axle_{s}": (*P, p), f"pen_arm_{s}": (*P, p),
                    f"pen_{s}": attach((*P, p), (L, 0))}
        return out

    steps = [("ground", "frame"), ("fix", "ring", None, "frame", (0, 0)),
             ("rev", "carrier_shaft", None, "frame", (0, 0)), ("fix", "carrier_gear", None, "carrier_shaft", None),
             ("fix", "arm", (30, 0), "carrier_shaft", None), ("rev", "motor_shaft", None, "frame", Mo),
             ("fix", "pinion", None, "motor_shaft", None), ("fix", "handwheel", (0, 0), "motor_shaft", None),
             ("mesh", "carrier_gear", "pinion")]
    for s, (L, hxy) in pens.items():
        steps += [("rev", f"axle_{s}", None, "arm", hxy), ("fix", f"planet_{s}", None, f"axle_{s}", None),
                  ("fix", f"pen_arm_{s}", (0, 0), f"axle_{s}", None), ("fix", f"pen_{s}", None, f"pen_arm_{s}", (L, 0)),
                  ("mesh", f"planet_{s}", "ring")]
    S = turns(0.0, 1080)
    st = {s: (rng(layout, f"pen_{s}", "x", S), rng(layout, f"pen_{s}", "y", S)) for s in pens}
    prompt = f"""装配万花尺（内齿圈固定，两只行星轮在圈内滚动，各带一支笔画内摆线花样）。{COMMON}
- frame 固定，z∈[0,8]。中心孔 (0,0)、电机轴孔 ({Mo[0]},{Mo[1]})，直径 10。60 齿内齿圈 ring（模数 1.5）固定在 frame 上、与中心孔同轴（z∈[8,14]）。
- carrier_shaft 穿过中心孔绕 Z 转动（z∈[-8,20]）；45 齿 carrier_gear（模数 2）固定在其上（z∈[-8,0]）；
  arm（三孔：0、30、60）的 X=30 孔固定在 carrier_shaft 上（z∈[14,20]）。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,8]）；15 齿 pinion（模数 2，z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，
  pinion 与 carrier_gear 外啮合（中心距 60）。
- axle_a、axle_b 分别插在 arm 的 X=0、X=60 孔里绕 Z 转动（z∈[8,26]）；20 齿 planet_a/planet_b（模数 1.5）固定在对应轴上（z∈[8,14]），
  都与 ring 内啮合（中心距 30）。
- pen_arm_a（长 24）、pen_arm_b（长 12）的 6 mm 孔分别固定在 axle_a、axle_b 上（z∈[20,26]）；pen_a、pen_b 固定在笔杆另一端 4 mm 孔里（z∈[20,36]）。
- 电机转 3 圈行星架反向转 1 圈；行星轮绝对转速是行星架的 2 倍、反向（与电机同向）。{GEAR_TIP}
对 motor_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("motor_shaft", ["frame", "ring"], ["carrier_shaft", "arm", "planet_a", "planet_b", "pen_a", "pen_b"],
                 ratios=[{"role": "arm", "ratio": 3, "same_direction": False},
                         {"role": "planet_a", "ratio": 1.5, "same_direction": True},
                         {"role": "pen_arm_b", "ratio": 1.5, "same_direction": True}])
    bodies = {"frame": ["frame", "ring"], "carrier": ["carrier_shaft", "carrier_gear", "arm"],
              "motor": ["motor_shaft", "pinion", "handwheel"]}
    joints = [("motor", "frame", R), ("carrier", "frame", R)]
    couplings = [gc("carrier_gear", "pinion", 45, 15, 2)]
    for s in pens:
        bodies[f"planet_{s}"] = [f"planet_{s}", f"axle_{s}", f"pen_arm_{s}", f"pen_{s}"]
        joints.append((f"planet_{s}", "carrier", R))
        couplings.append(gc(f"planet_{s}", "ring", 20, 60, 1.5, internal=True))
    rubric = rub("frame", 1, bodies, joints, couplings=couplings)
    return mk("spirograph", "spirograph", "万花尺",
              "固定内齿圈 60 + 两只 20 齿行星（笔偏心 24 / 12）+ 齿轮驱动行星架；检查行星架与行星轮的转速、转向。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 0.0, turns(0.0),
              {"pen_a_x": st["a"][0], "pen_b_x": st["b"][0]})


# ---- N12 Wankel 转子发动机（运动学模型）

@case
def wankel_rotary():
    e = 12
    apex = [(0, 40), (r2(-40 * math.cos(math.radians(30))), -20), (r2(40 * math.cos(math.radians(30))), -20)]
    parts = {
        "wk_frame": plate("缸体板", [-90, 90], [-90, 90], 8, [(0, 0, 10)]),
        "wk_shaft": pin("偏心轴", 10, 36),
        "wk_flywheel": disk("飞轮", 80, 6, 10, [(28, 0, 8), (-28, 0, 8), (0, 28, 8), (0, -28, 8)]),
        "wk_stat": gear(24, 2, 8, 10),
        "wk_crank": link("偏心臂", e, 24, 6, [(0, 10), (e, 8)]),
        "wk_ecc_pin": pin("偏心销", 8, 12),
        "wk_ring": ring(36, 2, 8, 100),
        "wk_rotor": disk("转子盖板", 100, 6, 8, [(x, y, 6) for x, y in apex]),
        "wk_apex": pin("顶封", 6, 12),
    }
    roles = {"frame": "wk_frame", "shaft": "wk_shaft", "flywheel": "wk_flywheel", "stat": "wk_stat",
             "crank": "wk_crank", "ecc_pin": "wk_ecc_pin", "rotor": "wk_rotor", "ring": "wk_ring"}
    z = {"frame": 0, "shaft": -14, "flywheel": -14, "stat": 8, "crank": 16, "ecc_pin": 16, "rotor": 22, "ring": 8}
    for k in range(3):
        roles[f"apex_{k + 1}"] = "wk_apex"
        z[f"apex_{k + 1}"] = 22

    def layout(t):
        Cr = pol((0, 0), e, t)
        rho = t / 3
        out = {"frame": (0, 0, 0), "shaft": (0, 0, t), "flywheel": (0, 0, t), "stat": (0, 0, 0), "crank": (0, 0, t),
               "ecc_pin": (*Cr, t), "rotor": (*Cr, rho), "ring": (*Cr, rho)}
        for k, hxy in enumerate(apex):
            out[f"apex_{k + 1}"] = attach((*Cr, rho), hxy)
        return out

    steps = [("ground", "frame"), ("fix", "stat", None, "frame", (0, 0)), ("rev", "shaft", None, "frame", (0, 0)),
             ("fix", "flywheel", (0, 0), "shaft", None), ("fix", "crank", (0, 0), "shaft", None),
             ("fix", "ecc_pin", None, "crank", (e, 0)), ("rev", "rotor", (0, 0), "ecc_pin", None),
             ("fix", "ring", None, "rotor", (0, 0)), ("mesh", "stat", "ring")]
    steps += [("fix", f"apex_{k + 1}", None, "rotor", hxy) for k, hxy in enumerate(apex)]
    prompt = f"""装配 Wankel 转子发动机的运动学模型：偏心轴带转子公转，转子上的 36 齿内齿圈绕固定的 24 齿相位齿轮滚动，
转子自转速度是偏心轴的 1/3、同向，三个顶点画出外旋轮线缸型。{COMMON}
- frame 固定，z∈[0,8]，中心孔 (0,0) 直径 10。24 齿 stat 固定在 frame 上、与中心孔同轴（z∈[8,16]）。
- shaft 穿过中心孔（也穿过 stat 的孔）绕 Z 转动（z∈[-14,22]）；flywheel 的中心孔固定在其上（z∈[-14,-8]），
  crank 的 10 mm 孔固定在其上（z∈[16,22]）。ecc_pin 固定在 crank 的 8 mm 孔里（偏心 {e}，z∈[16,28]）。
- rotor（转子盖板）的 8 mm 中心孔套在 ecc_pin 上转动（z∈[22,28]）；36 齿内齿圈 ring 与 rotor 同轴固定（z∈[8,16]）。
  ring 与 stat 内啮合（模数 2，中心距 {e}）。
- rotor 上三个 6 mm 孔各固定一根顶封销 apex_1、apex_2、apex_3（z∈[22,34]），apex_1 对应局部 (0,40) 孔。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame", "stat"], ["crank", "rotor", "ring", "apex_1"],
                 ratios=[{"role": "rotor", "ratio": 3, "same_direction": True}],
                 strokes=[{"role": "rotor", "axis": "x", "mm": 2 * e}, {"role": "rotor", "axis": "y", "mm": 2 * e}])
    rubric = rub("frame", 1, {"frame": ["frame", "stat"], "crank": ["shaft", "flywheel", "crank"],
                              "rotor": ["rotor", "ring", "apex_1", "apex_2", "apex_3"]},
                 [("crank", "frame", R), ("rotor", "crank", R)],
                 flex={"ecc_pin": ["crank", "rotor"]},
                 couplings=[gc("stat", "ring", 24, 36, 2, internal=True)])
    return mk("wankel_rotary", "wankel_rotary", "Wankel 转子发动机",
              "偏心 12，36/24 内啮合相位齿轮，转子 1/3 同向自转；检查转子转速、公转圆直径 24。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0), {"e": e})


# ---- N13 太阳系仪（地球—月球，惰轮传动）

@case
def orrery():
    Mo = (-48, 0)
    parts = {
        "or_frame": plate("底座", [-100, 100], [-100, 100], 8, [(0, 0, 10), (*Mo, 10)]),
        "or_arm_shaft": pin("主轴", 10, 30),
        "or_arm_gear": gear(36, 2, 8, 10),
        "or_motor_shaft": pin("电机轴", 10, 22),
        "or_pinion": gear(12, 2, 8, 10),
        "or_handwheel": disk("手轮", 40, 6, 10, [(12, 0, 6)]),
        "or_sun": gear(24, 2, 8, 10),
        "or_arm": link("行星臂", 64, 18, 6, [(0, 10), (36, 8), (64, 8)]),
        "or_idler_axle": pin("惰轮轴", 8, 14),
        "or_idler": gear(12, 2, 8, 8),
        "or_earth_axle": pin("地球轴", 8, 26),
        "or_earth_gear": gear(16, 2, 8, 8),
        "or_earth": disk("地球", 30, 6, 8, [(9, 0, 4), (-9, 0, 4)]),
        "or_moon_arm": link("月球臂", 30, 10, 6, [(0, 8), (30, 6)]),
        "or_moon_pin": pin("月球销", 6, 12),
        "or_moon": disk("月球", 14, 6, 6),
    }
    roles = {k[3:]: k for k in parts}
    z = {"frame": 0, "arm_shaft": -8, "arm_gear": -8, "motor_shaft": -14, "pinion": -8, "handwheel": -14, "sun": 8,
         "arm": 16, "idler_axle": 8, "idler": 8, "earth_axle": 8, "earth_gear": 8, "earth": 22, "moon_arm": 28,
         "moon_pin": 28, "moon": 34}

    def layout(t):
        c = -t / 3
        I, E = pol((0, 0), 36, c), pol((0, 0), 64, c)
        i, ed = 3 * c + 15, -0.5 * c + 5
        mo = (*Mo, t)
        Mn = attach((*E, ed), (30, 0))
        return {"frame": (0, 0, 0), "arm_shaft": (0, 0, c), "arm_gear": (0, 0, c), "motor_shaft": mo, "pinion": mo,
                "handwheel": mo, "sun": (0, 0, 0), "arm": (0, 0, c), "idler_axle": (*I, c), "idler": (*I, i),
                "earth_axle": (*E, ed), "earth_gear": (*E, ed), "earth": (*E, ed), "moon_arm": (*E, ed),
                "moon_pin": Mn, "moon": Mn}

    steps = [("ground", "frame"), ("fix", "sun", None, "frame", (0, 0)),
             ("rev", "arm_shaft", None, "frame", (0, 0)), ("fix", "arm_gear", None, "arm_shaft", None),
             ("fix", "arm", (0, 0), "arm_shaft", None), ("rev", "motor_shaft", None, "frame", Mo),
             ("fix", "pinion", None, "motor_shaft", None), ("fix", "handwheel", (0, 0), "motor_shaft", None),
             ("mesh", "arm_gear", "pinion"),
             ("fix", "idler_axle", None, "arm", (36, 0)), ("rev", "idler", None, "idler_axle", None),
             ("mesh", "sun", "idler"),
             ("rev", "earth_axle", None, "arm", (64, 0)), ("fix", "earth_gear", None, "earth_axle", None),
             ("fix", "earth", (0, 0), "earth_axle", None), ("fix", "moon_arm", (0, 0), "earth_axle", None),
             ("fix", "moon_pin", None, "moon_arm", (30, 0)), ("fix", "moon", (0, 0), "moon_pin", None),
             ("mesh", "idler", "earth_gear")]
    prompt = f"""装配太阳系仪（地球—月球）：太阳齿轮固定，电机经齿轮带行星臂公转；臂上惰轮把太阳齿轮的相对转动传给地球齿轮，
地球带着月球臂一起转。{COMMON}
- frame 固定，z∈[0,8]。中心孔 (0,0)、电机轴孔 ({Mo[0]},{Mo[1]})，直径 10。24 齿 sun 固定在 frame 上、与中心孔同轴（z∈[8,16]）。
- arm_shaft 穿过中心孔（也穿过 sun 的孔）绕 Z 转动（z∈[-8,22]）；36 齿 arm_gear 固定在其上（z∈[-8,0]），arm（三孔：0、36、64）的原点孔固定在其上（z∈[16,22]）。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-14,8]）；12 齿 pinion（z∈[-8,0]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上，与 arm_gear 外啮合。
- idler_axle 固定在 arm 的 X=36 孔里（z∈[8,22]）；12 齿 idler 套在 idler_axle 上转动（z∈[8,16]），与 sun 外啮合。
- earth_axle 插在 arm 的 X=64 孔里绕 Z 转动（z∈[8,34]）；16 齿 earth_gear（z∈[8,16]）固定在其上，与 idler 外啮合；
  earth 的 8 mm 孔（z∈[22,28]）与 moon_arm 的 8 mm 孔（z∈[28,34]）也固定在 earth_axle 上。
- moon_pin 固定在 moon_arm 的 6 mm 孔里（z∈[28,40]）；moon 的孔固定在 moon_pin 上（z∈[34,40]）。全部齿轮模数 2。
- 电机转 3 圈行星臂反向转 1 圈；地球绝对转速是行星臂的 1/2、反向（与电机同向）。{GEAR_TIP}
对 motor_shaft 做 check_motion（电机转 3 圈，行星臂正好转一周），确认无穿模后再保存。"""
    S = [1080.0 * i / 2160 for i in range(2160)]
    motion = mot("motor_shaft", ["frame", "sun"], ["arm", "idler", "earth", "moon_arm", "moon"],
                 ratios=[{"role": "arm", "ratio": 3, "same_direction": False},
                         {"role": "idler", "ratio": 1, "same_direction": False},
                         {"role": "earth", "ratio": 6, "same_direction": True}],
                 strokes=[{"role": "earth", "axis": "y", "mm": rng(layout, "earth", "y", S)}],
                 sweep_deg=1080, dense_frames=361)
    rubric = rub("frame", 1, {"frame": ["frame", "sun"], "motor": ["motor_shaft", "pinion", "handwheel"],
                              "arm": ["arm_shaft", "arm_gear", "arm"], "idler": ["idler"],
                              "earth": ["earth_axle", "earth_gear", "earth", "moon_arm", "moon_pin", "moon"]},
                 [("motor", "frame", R), ("arm", "frame", R), ("idler", "arm", R), ("earth", "arm", R)],
                 flex={"idler_axle": ["arm", "idler"]},
                 couplings=[gc("arm_gear", "pinion", 36, 12, 2), gc("sun", "idler", 24, 12, 2),
                            gc("idler", "earth_gear", 12, 16, 2)])
    return mk("orrery", "orrery_earth_moon", "太阳系仪（地球—月球）",
              "固定太阳轮—惰轮—地球轮（行星臂上）+ 电机齿轮，3 对啮合；检查臂、惰轮、地球三项转速与转向。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 0.0, turns(0.0))


# ---- N14 摇头风扇（两级齿轮减速 + 曲柄摇杆）

@case
def oscillating_fan():
    H, a, b, c = (80, 0), 20, 70, 40
    Mi, Mo = (-48, 0), (-48, -48)

    def coupler_end(B, side):
        return circ(B, H, b, c, side)

    side = 1
    parts = {
        "of_frame": plate("底座板", [-90, 150], [-90, 70], 8, [(0, 0, 10), (*Mi, 10), (*Mo, 10), (*H, 10)]),
        "of_crank_shaft": pin("曲柄轴", 10, 30),
        "of_mid_shaft": pin("中间轴", 10, 24),
        "of_motor_shaft": pin("电机轴", 10, 16),
        "of_gear": gear(36, 2, 8, 10),
        "of_pinion": gear(12, 2, 8, 10),
        "of_crank": link("曲柄", a, 20, 6, [(0, 10), (a, 8)]),
        "of_pin8": pin("铰销", 8, 12),
        "of_coupler": link("连杆", b, 12, 6, [(0, 8), (b, 8)]),
        "of_rocker": link("摇杆", c, 16, 6, [(0, 10), (c, 8)]),
        "of_pivot": pin("摇头轴", 10, 32),
        "of_head": disk("扇罩", 120, 6, 10, [(*map(r2, pol((0, 0), 40, k * 60)), 8) for k in range(6)]),
    }
    roles = {"frame": "of_frame", "crank_shaft": "of_crank_shaft", "crank_gear": "of_gear", "crank": "of_crank",
             "crank_pin": "of_pin8", "mid_shaft": "of_mid_shaft", "mid_gear": "of_gear", "mid_pinion": "of_pinion",
             "motor_shaft": "of_motor_shaft", "motor_pinion": "of_pinion", "coupler": "of_coupler",
             "pivot": "of_pivot", "rocker": "of_rocker", "head": "of_head", "rocker_pin": "of_pin8"}
    z = {"frame": 0, "crank_shaft": -16, "crank_gear": -16, "crank": 8, "crank_pin": 8, "mid_shaft": -16,
         "mid_gear": -8, "mid_pinion": -16, "motor_shaft": -8, "motor_pinion": -8, "coupler": 14, "pivot": 0,
         "rocker": 20, "head": 26, "rocker_pin": 14}

    def layout(t):
        B = pol((0, 0), a, t)
        C = coupler_end(B, side)
        ar = ang(H, C)
        mi, mo = (*Mi, -3 * t), (*Mo, 9 * t)
        return {"frame": (0, 0, 0), "crank_shaft": (0, 0, t), "crank_gear": (0, 0, t), "crank": (0, 0, t),
                "crank_pin": (*B, t), "mid_shaft": mi, "mid_gear": mi, "mid_pinion": mi, "motor_shaft": mo,
                "motor_pinion": mo, "coupler": (*B, ang(B, C)), "pivot": (*H, 0), "rocker": (*H, ar),
                "head": (*H, ar), "rocker_pin": (*C, ar)}

    steps = [("ground", "frame"),
             ("rev", "crank_shaft", None, "frame", (0, 0)), ("fix", "crank_gear", None, "crank_shaft", None),
             ("fix", "crank", (0, 0), "crank_shaft", None), ("fix", "crank_pin", None, "crank", (a, 0)),
             ("rev", "mid_shaft", None, "frame", Mi), ("fix", "mid_gear", None, "mid_shaft", None),
             ("fix", "mid_pinion", None, "mid_shaft", None), ("mesh", "crank_gear", "mid_pinion"),
             ("rev", "motor_shaft", None, "frame", Mo), ("fix", "motor_pinion", None, "motor_shaft", None),
             ("mesh", "mid_gear", "motor_pinion"),
             ("rev", "coupler", (0, 0), "crank_pin", None), ("fix", "pivot", None, "frame", H),
             ("rev", "rocker", (0, 0), "pivot", None), ("fix", "head", (0, 0), "rocker", (0, 0)),
             ("fix", "rocker_pin", None, "rocker", (c, 0)), ("cyl", "coupler", (b, 0), "rocker_pin", None)]
    S = fine(30.0)
    swing = rng(layout, "rocker", "deg", S)
    prompt = f"""装配摇头风扇的摇头机构：电机经两级 12/36 齿轮减速（总减速比 9）带曲柄，曲柄—连杆—摇杆让扇头往复摆动。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴孔 (0,0)、中间轴孔 ({Mi[0]},{Mi[1]})、电机轴孔 ({Mo[0]},{Mo[1]})、摇头轴孔 ({H[0]},{H[1]})，直径都是 10。
- motor_shaft 穿过电机轴孔绕 Z 转动（z∈[-8,8]）；12 齿 motor_pinion 固定在其上（z∈[-8,0]）。
- mid_shaft 穿过中间轴孔绕 Z 转动（z∈[-16,8]）；36 齿 mid_gear（z∈[-8,0]）与 12 齿 mid_pinion（z∈[-16,-8]）固定在其上；
  motor_pinion 与 mid_gear 外啮合（模数 2，中心距 48）。
- crank_shaft 穿过曲柄轴孔绕 Z 转动（z∈[-16,14]）；36 齿 crank_gear（z∈[-16,-8]）固定在其上，与 mid_pinion 外啮合（中心距 48）；
  crank 的 10 mm 孔固定在其上（z∈[8,14]）。crank_pin 固定在 crank 的 8 mm 孔里（z∈[8,20]）。
- coupler 的原点孔套在 crank_pin 上转动（z∈[14,20]）。pivot 固定在摇头轴孔里（z∈[0,32]）；
  rocker 的 10 mm 孔套在 pivot 上转动（z∈[20,26]）；head 的中心孔也套在 pivot 上（z∈[26,32]），并与 rocker 固定。
- rocker_pin 固定在 rocker 的 8 mm 孔里（z∈[14,26]），coupler 的另一孔套在 rocker_pin 上。rocker_pin 在摇头轴上方一侧（左转支）。
- 曲柄 {a}、连杆 {b}、摇杆 {c}、机架 {H[0]}。曲柄一整圈扇头摆角约 {swing:.1f}°。{GEAR_TIP}
对 crank_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("crank_shaft", ["frame"], ["crank", "coupler", "rocker", "head", "mid_shaft", "motor_shaft"],
                 ratios=[{"role": "mid_shaft", "ratio": 1 / 3, "same_direction": False}],
                 swings=[{"role": "head", "deg": swing}])
    rubric = rub("frame", 1, {"frame": ["frame"], "motor": ["motor_shaft", "motor_pinion"],
                              "mid": ["mid_shaft", "mid_gear", "mid_pinion"],
                              "crank": ["crank_shaft", "crank_gear", "crank"], "coupler": ["coupler"],
                              "rocker": ["rocker", "head"]},
                 [("motor", "frame", R), ("mid", "frame", R), ("crank", "frame", R), ("coupler", "crank", R),
                  ("rocker", "frame", R), ("coupler", "rocker", RC)],
                 flex={"crank_pin": ["crank", "coupler"], "pivot": ["frame", "rocker"], "rocker_pin": ["rocker", "coupler"]},
                 couplings=[gc("crank_gear", "mid_pinion", 36, 12, 2), gc("mid_gear", "motor_pinion", 36, 12, 2)])
    return mk("oscillating_fan", "oscillating_fan", "摇头风扇",
              "两级 12/36 减速（i=9）+ 曲柄摇杆摇头；检查扇头摆角与中间轴 3 倍转速。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 30.0, turns(30.0), {"swing": swing})


# ---- N15 齿轮丝杠升降台

@case
def gear_screw_lift():
    lead, z0 = 6, 50
    parts = {
        "gl_frame": plate("底板", [-60, 60], [-40, 40], 8, [(0, 0, 10), (-36, 0, 10), (36, 0, 10), (0, 24, 10),
                                                         (0, -24, 10)]),
        "gl_motor_shaft": pin("电机轴", 10, 30),
        "gl_handwheel": disk("手轮", 50, 6, 10, [(16, 0, 6)]),
        "gl_pinion": gear(12, 2, 8, 10),
        "gl_screw": pin("丝杠", 10, 110),
        "gl_gear": gear(24, 2, 8, 10),
        "gl_platform": plate("升降平台", [-50, 50], [-14, 14], 8, [(-36, 0, 10), (36, 0, 10)]),
        "gl_post": pin("立柱", 10, 120),
        "gl_top": plate("顶板", [-50, 50], [-30, 30], 8, [(-36, 0, 12), (36, 0, 12), (0, 24, 10), (0, -24, 10)]),
    }
    roles = {"frame": "gl_frame", "motor_shaft": "gl_motor_shaft", "handwheel": "gl_handwheel", "pinion": "gl_pinion",
             "screw_l": "gl_screw", "gear_l": "gl_gear", "screw_r": "gl_screw", "gear_r": "gl_gear",
             "platform": "gl_platform", "post_a": "gl_post", "post_b": "gl_post", "top": "gl_top"}
    z = {"frame": 0, "motor_shaft": -14, "handwheel": -14, "pinion": 8, "screw_l": 0, "gear_l": 8, "screw_r": 0,
         "gear_r": 8, "platform": z0, "post_a": 0, "post_b": 0, "top": 112}

    def layout(t):
        rho = -t / 2
        return {"frame": (0, 0, 0), "motor_shaft": (0, 0, t), "handwheel": (0, 0, t), "pinion": (0, 0, t),
                "screw_l": (-36, 0, rho), "gear_l": (-36, 0, rho), "screw_r": (36, 0, rho + 7.5),
                "gear_r": (36, 0, rho + 7.5), "platform": (0, 0, 0, z0 + lead * t / 720), "post_a": (0, 24, 0),
                "post_b": (0, -24, 0), "top": (0, 0, 0)}

    steps = [("ground", "frame"), ("rev", "motor_shaft", None, "frame", (0, 0)),
             ("fix", "pinion", None, "motor_shaft", None), ("fix", "handwheel", (0, 0), "motor_shaft", None),
             ("rev", "screw_l", None, "frame", (-36, 0)), ("fix", "gear_l", None, "screw_l", None),
             ("rev", "screw_r", None, "frame", (36, 0)), ("fix", "gear_r", None, "screw_r", None),
             ("mesh", "pinion", "gear_l"), ("mesh", "pinion", "gear_r"),
             ("fix", "post_a", None, "frame", (0, 24)), ("fix", "post_b", None, "frame", (0, -24)),
             ("fix", "top", (0, 24), "post_a", None),
             ("screw", "screw_l", "platform", lead, (-36, 0)), ("cyl", "platform", (36, 0), "screw_r", None)]
    prompt = f"""装配齿轮丝杠升降台：电机小齿轮同时带动左右两根丝杠，左丝杠经螺旋副推动平台升降，右丝杠兼作导向。{COMMON}
- frame 固定，z∈[0,8]。电机轴孔 (0,0)、丝杠孔 (-36,0)、(36,0)、立柱孔 (0,24)、(0,-24)，直径都是 10。
- motor_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-14,16]）；12 齿 pinion（z∈[8,16]）与 handwheel 的中心孔（z∈[-14,-8]）固定在其上。
- screw_l、screw_r 分别穿过 (-36,0)、(36,0) 孔绕 Z 转动（z∈[0,110]）；24 齿 gear_l、gear_r 固定在对应丝杠上（z∈[8,16]），
  都与 pinion 外啮合（模数 2，中心距 36）。
- post_a、post_b 固定在两个立柱孔里（z∈[0,120]）；top 的 (0,24) 孔固定在 post_a 上（z∈[112,120]），post_b 从 top 的 (0,-24) 孔穿过。
- platform 的 (-36,0) 孔与 screw_l 组成螺旋副（导程 {lead} mm）；(36,0) 孔套在 screw_r 上，只能沿 Z 滑动（同时可绕 screw_r 相对转动）。
  初始 platform 在 z∈[{z0},{z0 + 8}]。
- 电机转 1 圈，丝杠转半圈，平台移动 {lead / 2:g} mm。{GEAR_TIP}
对 motor_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("motor_shaft", ["frame", "top"], ["pinion", "screw_l", "screw_r", "platform"],
                 ratios=[{"role": "screw_l", "ratio": 2, "same_direction": False},
                         {"role": "screw_r", "ratio": 2, "same_direction": False}],
                 leads=[{"role": "platform", "axis": "z", "mm_per_rev": lead / 2}])
    rubric = rub("frame", 1, {"frame": ["frame", "post_a", "post_b", "top"], "motor": ["motor_shaft", "pinion", "handwheel"],
                              "screw_l": ["screw_l", "gear_l"], "screw_r": ["screw_r", "gear_r"], "platform": ["platform"]},
                 [("motor", "frame", R), ("screw_l", "frame", R), ("screw_r", "frame", R),
                  ("platform", "screw_l", "screw"), ("platform", "screw_r", "cylindrical")],
                 couplings=[gc("pinion", "gear_l", 12, 24, 2), gc("pinion", "gear_r", 12, 24, 2)])
    return mk("gear_screw_lift", "gear_screw_lift", "齿轮丝杠升降台",
              f"一拖二齿轮 + 导程 {lead} 丝杠，右丝杠兼导向；检查两丝杠转速与平台每转 {lead / 2:g} mm。",
              parts, roles, z, layout, steps, prompt, motion, rubric, 0.0, turns(0.0), {"lead": lead})


# ================================================================== 多位姿测例
# layout 的参数是位姿名；参考装配按第一个位姿搭。点位取在杆件 X 轴或板的孔心上。

def pt(role, local, xy):
    return {"role": role, "local": [r2(local[0]), r2(local[1]), 0], "expect": {"x": r2(xy[0]), "y": r2(xy[1])}}


def mkp(name, case_id, title, note, parts, roles, z, layout, steps, prompt, poses, rubric, names, info=None):
    return mk(name, case_id, title, note, parts, roles, z, layout, steps, prompt, poses_motion(steps[0][1], poses),
              rubric, names[0], list(names), info)


def solve_rot(J, G, h, l, side):
    """刚体绕 J 转、其上局部点 h 到定点 G 的距离为 l：返回刚体转角。"""
    H = circ(J, G, math.hypot(*h), l, side)
    return ang(J, H) - math.degrees(math.atan2(h[1], h[0]))


# ---- P1 三级剪叉升降台

@case
def scissor_lift():
    L = 120
    P = {"low": 25, "mid": 40, "high": 55}

    def geo(s):
        a = P[s]
        return a, L * math.cos(math.radians(a)), L * math.sin(math.radians(a))

    parts = {
        "sl_frame": plate("立板", [-40, 170], [-30, 330], 8, [(0, 0, 6)]),
        "sl_arm": link("剪叉臂", L, 12, 6, [(0, 6), (L / 2, 6), (L, 6)]),
        "sl_pin12": pin("铰销", 6, 12),
        "sl_pin18": pin("滑块销", 6, 18),
        "sl_pin20": pin("底铰销", 6, 20),
        "sl_block": plate("滑块", [-10, 10], [-8, 8], 6, [(0, 0, 6)]),
        "sl_platform": plate("平台", [-20, 140], [-8, 8], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "sl_frame", "base_pin": "sl_pin20", "slider": "sl_block", "slider_pin": "sl_pin18",
             "top_pin": "sl_pin12", "platform": "sl_platform", "tb_pin": "sl_pin12", "tblock": "sl_block"}
    z = {"frame": 0, "base_pin": 0, "slider": 8, "slider_pin": 8, "top_pin": 20, "platform": 26, "tb_pin": 14,
         "tblock": 20}
    for k in (1, 2, 3):
        roles |= {f"a{k}": "sl_arm", f"b{k}": "sl_arm", f"c{k}": "sl_pin12"}
        z |= {f"a{k}": 14, f"b{k}": 20, f"c{k}": 14}
    for k in (1, 2):
        roles |= {f"jl{k}": "sl_pin12", f"jr{k}": "sl_pin12"}
        z |= {f"jl{k}": 14, f"jr{k}": 14}

    def layout(s):
        a, c, h = geo(s)
        out = {"frame": (0, 0, 0), "base_pin": (0, 0, 0), "slider": (c, 0, 0), "slider_pin": (c, 0, 180 - a),
               "top_pin": (0, 3 * h, 180 - a), "platform": (0, 3 * h, 0), "tb_pin": (c, 3 * h, a),
               "tblock": (c, 3 * h, 0)}
        for k in (1, 2, 3):
            out |= {f"a{k}": (0, (k - 1) * h, a), f"b{k}": (c, (k - 1) * h, 180 - a),
                    f"c{k}": (c / 2, (k - 0.5) * h, a)}
        for k in (1, 2):
            out |= {f"jl{k}": (0, k * h, 180 - a), f"jr{k}": (c, k * h, a)}
        return out

    steps = [("ground", "frame"), ("fix", "base_pin", None, "frame", (0, 0)), ("rev", "a1", (0, 0), "base_pin", None),
             ("fix", "c1", None, "a1", (L / 2, 0)), ("rev", "b1", (L / 2, 0), "c1", None),
             ("fix", "slider_pin", None, "b1", (0, 0)), ("rev", "slider", (0, 0), "slider_pin", None),
             ("slide", "slider", "top", "frame", "top")]
    for k in (2, 3):
        steps += [("fix", f"jl{k - 1}", None, f"b{k - 1}", (L, 0)), ("rev", f"a{k}", (0, 0), f"jl{k - 1}", None),
                  ("fix", f"c{k}", None, f"a{k}", (L / 2, 0)), ("rev", f"b{k}", (L / 2, 0), f"c{k}", None),
                  ("fix", f"jr{k - 1}", None, f"a{k - 1}", (L, 0)), ("cyl", f"b{k}", (0, 0), f"jr{k - 1}", None)]
    steps += [("fix", "top_pin", None, "b3", (L, 0)), ("rev", "platform", (0, 0), "top_pin", None),
              ("fix", "tb_pin", None, "a3", (L, 0)), ("rev", "tblock", (0, 0), "tb_pin", None),
              ("slide", "tblock", "top", "platform", "top")]
    poses = []
    for s in P:
        a, c, h = geo(s)
        poses.append({"name": s, "set": [{"type": "offset", "a": "slider:plane(right)", "b": "frame:plane(right)",
                                          "offset_mm": r2(c),
                                          "points": [pt("platform", (0, 0), (0, 3 * h)), pt("a3", (L, 0), (c, 3 * h)),
                                                     pt("a2", (L / 2, 0), (c / 2, 1.5 * h))]}]})
    prompt = f"""装配三级剪叉升降台：底部滑块水平移动，三级剪叉臂同步张合，平台始终水平升降。{COMMON}
- frame（竖直立板，XY 平面）固定，z∈[0,8]，底铰孔 (0,0) 直径 6。所有剪叉臂都是三孔长条（孔距 60、长 {L}），孔径 6。
- base_pin 固定在底铰孔里（z∈[0,20]）。a1 的原点孔套在 base_pin 上转动（z∈[14,20]）。
- 每一级 k=1,2,3：a_k 在 z∈[14,20]，b_k 在 z∈[20,26]；中销 c_k 固定在 a_k 的中孔（z∈[14,26]），b_k 的中孔套在 c_k 上，两臂交叉成 X。
  a_k 从左下到右上，b_k 从右下到左上；左侧铰点都在 x=0，右侧铰点在同一条竖线上。
- slider_pin 固定在 b1 的原点孔里（z∈[8,26]）；slider 的孔套在 slider_pin 上转动（z∈[8,14]），slider 只能沿 frame 的 X 方向移动，孔心始终在 y=0。
- 级间：jl1、jl2 分别固定在 b1、b2 的末孔（左侧，z∈[14,26]），a2、a3 的原点孔依次套在上面；
  jr1、jr2 分别固定在 a1、a2 的末孔（右侧，z∈[14,26]），b2、b3 的原点孔依次套在上面。
- 顶部：top_pin 固定在 b3 的末孔（z∈[20,32]），platform 的孔套在上面转动（z∈[26,32]，长边沿 X 向右伸出）；
  tb_pin 固定在 a3 的末孔（z∈[14,26]），tblock 的孔套在上面转动（z∈[20,26]），tblock 只能沿 platform 的长边方向滑动、孔心在 platform 长边中线上。
- 升降这 1 个自由度保持自由（整机活动度 1），不要固定 slider 位置。各件互不穿模，装好后保存。"""
    bodies = {"frame": ["frame"], "slider": ["slider"], "platform": ["platform"], "tblock": ["tblock"]}
    joints = [("a1", "frame", R), ("b1", "a1", R), ("slider", "b1", R), ("slider", "frame", SP)]
    flex = {"base_pin": ["frame", "a1"], "slider_pin": ["b1", "slider"], "top_pin": ["b3", "platform"],
            "tb_pin": ["a3", "tblock"]}
    for k in (1, 2, 3):
        bodies |= {f"a{k}": [f"a{k}"], f"b{k}": [f"b{k}"]}
        flex[f"c{k}"] = [f"a{k}", f"b{k}"]
    for k in (2, 3):
        joints += [(f"a{k}", f"b{k - 1}", R), (f"b{k}", f"a{k}", R), (f"b{k}", f"a{k - 1}", RC)]
        flex |= {f"jl{k - 1}": [f"b{k - 1}", f"a{k}"], f"jr{k - 1}": [f"a{k - 1}", f"b{k}"]}
    joints += [("platform", "b3", R), ("tblock", "a3", R), ("tblock", "platform", SP)]
    rubric = rub("frame", 1, bodies, joints, flex=flex)
    return mkp("scissor_lift", "scissor_lift_3stage", "三级剪叉升降台（多位姿）",
               "三级剪叉 + 底部滑块 + 顶部滑块平台，21 个组件、活动度 1；三个滑块偏距检查平台与各级铰点。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P))


# ---- P2 五叶机械光圈

@case
def mechanical_iris():
    Rb, Ra, lc, Lb = 60, 45, 25, 50
    P = {"open": 10, "half": 20, "closed": 30}
    layer = (2, 3, 2, 3, 4)
    piv = [tuple(map(r2, pol((0, 0), Rb, 72 * i))) for i in range(5)]
    act = [tuple(map(r2, pol((0, 0), Ra, 72 * i))) for i in range(5)]
    parts = {
        "ir_base": plate("底座", [-80, 80], [-80, 80], 8, [(0, 0, 10)] + [(*p, 6) for p in piv]),
        "ir_center": pin("中心轴", 10, 14),
        "ir_actuator": disk("驱动环", 110, 6, 10, [(*p, 6) for p in act]),
        "ir_blade": link("叶片", Lb, 14, 6, [(0, 6), (lc, 6)]),
        "ir_conn": link("连杆", lc, 10, 6, [(0, 6), (lc, 6)]),
        "ir_apin": pin("驱动销", 6, 12),
    }
    for li in sorted(set(layer)):
        parts[f"ir_bpin{14 + 6 * li}"] = pin("叶片轴", 6, 14 + 6 * li)
        parts[f"ir_qpin{6 * li}"] = pin("叶片销", 6, 6 * li)
    roles = {"base": "ir_base", "center_pin": "ir_center", "actuator": "ir_actuator"}
    z = {"base": 0, "center_pin": 0, "actuator": 8}
    for i in range(5):
        k, li = i + 1, layer[i]
        roles |= {f"blade_{k}": "ir_blade", f"conn_{k}": "ir_conn", f"bpin_{k}": f"ir_bpin{14 + 6 * li}",
                  f"apin_{k}": "ir_apin", f"qpin_{k}": f"ir_qpin{6 * li}"}
        z |= {f"blade_{k}": 8 + 6 * li, f"conn_{k}": 14, f"bpin_{k}": 0, f"apin_{k}": 8, f"qpin_{k}": 14}

    def layout(s):
        psi = P[s]
        out = {"base": (0, 0, 0), "center_pin": (0, 0, 0), "actuator": (0, 0, psi)}
        for i in range(5):
            k = i + 1
            A = attach((0, 0, psi), act[i])[:2]
            Q = circ(piv[i], A, lc, lc, 1)
            th = ang(piv[i], Q)
            out |= {f"blade_{k}": (*piv[i], th), f"bpin_{k}": (*piv[i], 0), f"apin_{k}": (*A, psi),
                    f"conn_{k}": (*A, ang(A, Q)), f"qpin_{k}": (*Q, th)}
        return out

    steps = [("ground", "base"), ("fix", "center_pin", None, "base", (0, 0)),
             ("rev", "actuator", (0, 0), "center_pin", None)]
    for i in range(5):
        k = i + 1
        steps += [("fix", f"bpin_{k}", None, "base", piv[i]), ("rev", f"blade_{k}", (0, 0), f"bpin_{k}", None),
                  ("fix", f"apin_{k}", None, "actuator", act[i]), ("rev", f"conn_{k}", (0, 0), f"apin_{k}", None),
                  ("fix", f"qpin_{k}", None, f"blade_{k}", (lc, 0)), ("cyl", f"conn_{k}", (lc, 0), f"qpin_{k}", None)]
    poses, aperture = [], {}
    for s, psi in P.items():
        lay = layout(s)
        tips = {k: attach(lay[f"blade_{k}"], (Lb, 0))[:2] for k in range(1, 6)}
        aperture[s] = r2(min(math.hypot(*t) for t in tips.values()))
        poses.append({"name": s, "set": [angle_item("actuator", "base", psi,
                                                    [pt(f"blade_{k}", (Lb, 0), tips[k]) for k in range(1, 6)])]})
    zl = {li: (8 + 6 * li, 14 + 6 * li) for li in layer}
    blade_z = "；".join(f"blade_{i + 1} z∈[{zl[layer[i]][0]},{zl[layer[i]][1]}]" for i in range(5))
    prompt = f"""装配五叶机械光圈：转动驱动环，五组"驱动销—连杆—叶片"四杆同时动作，叶片尖端向中心收拢或张开。{COMMON}
- base 固定，z∈[0,8]：中心孔 (0,0) 直径 10；五个叶片轴孔在半径 {Rb} 的圆上，第 k 个在 {72}·(k−1)° 方向（k=1 在 +X），直径 6。
- center_pin 固定在中心孔里（z∈[0,14]）；actuator（驱动环）的中心孔套在 center_pin 上转动（z∈[8,14]）；
  actuator 上五个 6 mm 孔在半径 {Ra} 的圆上，第 k 个在局部 72·(k−1)° 方向。
- 第 k 组（k=1..5）：bpin_k 固定在第 k 个叶片轴孔里，blade_k 的原点孔套在 bpin_k 上转动；apin_k 固定在 actuator 第 k 个孔里（z∈[8,20]），
  conn_k 的原点孔套在 apin_k 上转动（z∈[14,20]）；qpin_k 固定在 blade_k 的 X={lc} 孔里，conn_k 的另一孔套在 qpin_k 上。
- 叶片分层：{blade_z}；bpin_k 从 z=0 伸到所在叶片层顶面，qpin_k 从 z=14 伸到所在叶片层顶面。
- 叶片从轴孔出发朝圆心一侧（顺时针方向）伸出，各叶片尖端在三个位姿下离中心约 {aperture['open']:.0f}、{aperture['half']:.0f}、{aperture['closed']:.0f} mm。
- 驱动环转角这 1 个自由度保持自由（整机活动度 1），不要固定。各件互不穿模，装好后保存。"""
    bodies = {"base": ["base"], "actuator": ["actuator"]}
    joints = [("actuator", "base", R)]
    flex = {"center_pin": ["base", "actuator"]}
    for k in range(1, 6):
        bodies |= {f"blade_{k}": [f"blade_{k}"], f"conn_{k}": [f"conn_{k}"]}
        joints += [(f"blade_{k}", "base", R), (f"conn_{k}", "actuator", R), (f"conn_{k}", f"blade_{k}", RC)]
        flex |= {f"bpin_{k}": ["base", f"blade_{k}"], f"apin_{k}": ["actuator", f"conn_{k}"],
                 f"qpin_{k}": [f"blade_{k}", f"conn_{k}"]}
    rubric = rub("base", 1, bodies, joints, flex=flex)
    return mkp("mechanical_iris", "mechanical_iris_5blade", "五叶机械光圈（多位姿）",
               "驱动环 + 5 组四杆叶片（分 3 层交叠），28 个组件、活动度 1；三个驱动环转角检查五个叶尖。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P), aperture)


# ---- P3 Peaucellier 精确直线机构

@case
def peaucellier_linkage():
    Lo, s_, r = 100, 50, 40
    C = (r, 0)
    P = {"low": 30, "mid": 60, "high": 90}

    def geo(name):
        Q = pol(C, r, P[name])
        k = (Lo ** 2 - s_ ** 2) / (Q[0] ** 2 + Q[1] ** 2)
        Pp = (Q[0] * k, Q[1] * k)
        return Q, Pp, circ((0, 0), Q, Lo, s_, 1), circ((0, 0), Q, Lo, s_, -1)

    parts = {
        "pl_frame": plate("底板", [-30, 130], [-80, 130], 8, [(0, 0, 8), (*C, 8)]),
        "pl_crank": link("曲柄", r, 14, 6, [(0, 8), (r, 6)]),
        "pl_long": link("长杆", Lo, 12, 6, [(0, 8), (Lo, 6)]),
        "pl_side": link("菱形边", s_, 10, 6, [(0, 6), (s_, 6)]),
        "pl_o_pin": pin("机架销", 8, 20),
        "pl_c_pin": pin("曲柄轴", 8, 14),
        "pl_pin18": pin("铰销", 6, 18),
        "pl_pen": pin("笔", 6, 24),
    }
    roles = {"frame": "pl_frame", "o_pin": "pl_o_pin", "c_pin": "pl_c_pin", "crank": "pl_crank", "q_pin": "pl_pin18",
             "ob": "pl_long", "od": "pl_long", "b_pin": "pl_pin18", "d_pin": "pl_pin18", "qb": "pl_side",
             "qd": "pl_side", "bp": "pl_side", "dp": "pl_side", "p_pin": "pl_pen"}
    z = {"frame": 0, "o_pin": 0, "c_pin": 0, "crank": 8, "q_pin": 8, "ob": 8, "od": 14, "b_pin": 8, "d_pin": 8,
         "qb": 14, "qd": 20, "bp": 20, "dp": 8, "p_pin": 8}

    def layout(name):
        Q, Pp, B, D = geo(name)
        th = P[name]
        return {"frame": (0, 0, 0), "o_pin": (0, 0, 0), "c_pin": (*C, 0), "crank": (*C, th), "q_pin": (*Q, th),
                "ob": (0, 0, ang((0, 0), B)), "od": (0, 0, ang((0, 0), D)), "b_pin": (*B, ang((0, 0), B)),
                "d_pin": (*D, ang((0, 0), D)), "qb": (*Q, ang(Q, B)), "qd": (*Q, ang(Q, D)), "bp": (*B, ang(B, Pp)),
                "dp": (*D, ang(D, Pp)), "p_pin": (*Pp, ang(D, Pp))}

    steps = [("ground", "frame"), ("fix", "o_pin", None, "frame", (0, 0)), ("fix", "c_pin", None, "frame", C),
             ("rev", "crank", (0, 0), "c_pin", None), ("fix", "q_pin", None, "crank", (r, 0)),
             ("rev", "ob", (0, 0), "o_pin", None), ("fix", "b_pin", None, "ob", (Lo, 0)),
             ("rev", "od", (0, 0), "o_pin", None), ("fix", "d_pin", None, "od", (Lo, 0)),
             ("rev", "qb", (0, 0), "q_pin", None), ("cyl", "qb", (s_, 0), "b_pin", None),
             ("rev", "qd", (0, 0), "q_pin", None), ("cyl", "qd", (s_, 0), "d_pin", None),
             ("rev", "bp", (0, 0), "b_pin", None), ("rev", "dp", (0, 0), "d_pin", None),
             ("fix", "p_pin", None, "dp", (s_, 0)), ("cyl", "bp", (s_, 0), "p_pin", None)]
    poses = []
    for name, th in P.items():
        Q, Pp, B, D = geo(name)
        poses.append({"name": name, "set": [angle_item("crank", "frame", th,
                                                       [pt("dp", (s_, 0), Pp), pt("ob", (Lo, 0), B),
                                                        pt("od", (Lo, 0), D)])]})
    xline = (Lo ** 2 - s_ ** 2) / (2 * r)
    prompt = f"""装配 Peaucellier–Lipkin 精确直线机构：反演器把曲柄端点的圆弧变成笔尖的直线，笔尖始终在 x={xline:g} 的竖线上。{COMMON}
- frame 固定，z∈[0,8]。机架铰孔 O=(0,0)、曲柄轴孔 C=({C[0]},0)，直径 8。
- o_pin 固定在 O 孔里（z∈[0,20]）；c_pin 固定在 C 孔里（z∈[0,14]）。
- crank（长 {r}）的 8 mm 孔套在 c_pin 上转动（z∈[8,14]）；q_pin 固定在 crank 的 6 mm 孔里（z∈[8,26]），记该点为 Q。
- ob、od（长 {Lo}）的 8 mm 原点孔都套在 o_pin 上转动：ob 在 z∈[8,14]，od 在 z∈[14,20]；b_pin、d_pin 分别固定在 ob、od 的 6 mm 孔里（z∈[8,26]），
  记为 B、D。B 在 OQ 连线左侧（逆时针一侧），D 在右侧。
- 菱形四边（长 {s_}）：qb（z∈[14,20]）连 Q—B，qd（z∈[20,26]）连 Q—D，原点孔都在 Q；bp（z∈[20,26]）原点孔在 B，dp（z∈[8,14]）原点孔在 D。
- p_pin（笔）固定在 dp 的另一孔里（z∈[8,32]），bp 的另一孔也套在 p_pin 上，记为 P。
- 曲柄转角这 1 个自由度保持自由（整机活动度 1），不要固定。各件互不穿模，装好后保存。"""
    rubric = rub("frame", 1, {"frame": ["frame", "o_pin"], "crank": ["crank", "q_pin"], "ob": ["ob", "b_pin"],
                              "od": ["od", "d_pin"], "qb": ["qb"], "qd": ["qd"], "bp": ["bp"], "dp": ["dp"]},
                 [("crank", "frame", R), ("ob", "frame", R), ("od", "frame", R), ("qb", "crank", R),
                  ("qb", "ob", RC), ("qd", "crank", R), ("qd", "od", RC), ("bp", "ob", R), ("dp", "od", R),
                  ("bp", "dp", RC)],
                 flex={"c_pin": ["frame", "crank"], "p_pin": ["dp", "bp"]})
    return mkp("peaucellier_linkage", "peaucellier_linkage", "Peaucellier 精确直线机构（多位姿）",
               f"反演器 OB=OD={Lo}、菱形边 {s_}、曲柄 {r}，笔尖在 x={xline:g} 直线上；三个曲柄角检查笔尖与 B、D。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P), {"x_line": xline})


# ---- P4 五杆并联机器人（活动度 2）

@case
def five_bar_robot():
    A2, L1, L2 = (60, 0), 70, 100
    P = {"left": (125, 80), "reach": (100, 45), "right": (140, 100)}

    def geo(name):
        t1, t2 = P[name]
        E1, E2 = pol((0, 0), L1, t1), pol(A2, L1, t2)
        return E1, E2, circ(E1, E2, L2, L2, 1)

    parts = {
        "fr_base": plate("底座", [-60, 120], [-40, 40], 8, [(0, 0, 10), (*A2, 10)]),
        "fr_shaft": pin("电机轴", 10, 14),
        "fr_prox": link("主动臂", L1, 18, 6, [(0, 10), (L1, 6)]),
        "fr_dist": link("从动臂", L2, 14, 6, [(0, 6), (L2, 6)]),
        "fr_pin12": pin("肘销", 6, 12),
        "fr_pin18": pin("长肘销", 6, 18),
        "fr_tool_pin": pin("末端销", 6, 18),
        "fr_tool": disk("末端执行器", 30, 6, 6, [(9, 0, 4), (-9, 0, 4)]),
    }
    roles = {"base": "fr_base", "a1_pin": "fr_shaft", "a2_pin": "fr_shaft", "p1": "fr_prox", "p2": "fr_prox",
             "e1_pin": "fr_pin12", "e2_pin": "fr_pin18", "d1": "fr_dist", "d2": "fr_dist", "p_pin": "fr_tool_pin",
             "tool": "fr_tool"}
    z = {"base": 0, "a1_pin": 0, "a2_pin": 0, "p1": 8, "p2": 8, "e1_pin": 8, "e2_pin": 8, "d1": 14, "d2": 20,
         "p_pin": 14, "tool": 26}

    def layout(name):
        E1, E2, Pp = geo(name)
        t1, t2 = P[name]
        a2 = ang(E2, Pp)
        return {"base": (0, 0, 0), "a1_pin": (0, 0, 0), "a2_pin": (*A2, 0), "p1": (0, 0, t1), "p2": (*A2, t2),
                "e1_pin": (*E1, t1), "e2_pin": (*E2, t2), "d1": (*E1, ang(E1, Pp)), "d2": (*E2, a2),
                "p_pin": (*Pp, a2), "tool": (*Pp, a2)}

    steps = [("ground", "base"), ("fix", "a1_pin", None, "base", (0, 0)), ("fix", "a2_pin", None, "base", A2),
             ("rev", "p1", (0, 0), "a1_pin", None), ("fix", "e1_pin", None, "p1", (L1, 0)),
             ("rev", "d1", (0, 0), "e1_pin", None), ("rev", "p2", (0, 0), "a2_pin", None),
             ("fix", "e2_pin", None, "p2", (L1, 0)), ("rev", "d2", (0, 0), "e2_pin", None),
             ("fix", "p_pin", None, "d2", (L2, 0)), ("cyl", "d1", (L2, 0), "p_pin", None),
             ("fix", "tool", (0, 0), "p_pin", None)]
    poses = []
    for name, (t1, t2) in P.items():
        E1, E2, Pp = geo(name)
        poses.append({"name": name, "set": [angle_item("p1", "base", t1, [pt("p1", (L1, 0), E1)]),
                                            angle_item("p2", "base", t2, [pt("p2", (L1, 0), E2), pt("d2", (L2, 0), Pp)])]})
    prompt = f"""装配五杆并联机器人：两只主动臂分别绕底座上两根轴转动，两只从动臂在末端铰接，末端执行器在平面内两自由度定位。{COMMON}
- base 固定，z∈[0,8]，两个轴孔 (0,0)、({A2[0]},{A2[1]})，直径 10。a1_pin、a2_pin 分别固定在这两个孔里（z∈[0,14]）。
- p1、p2（主动臂，长 {L1}）的 10 mm 孔分别套在 a1_pin、a2_pin 上转动（z∈[8,14]）。
- e1_pin 固定在 p1 的 6 mm 孔里（z∈[8,20]）；e2_pin 固定在 p2 的 6 mm 孔里（z∈[8,26]）。
- d1（从动臂，长 {L2}）的原点孔套在 e1_pin 上转动（z∈[14,20]）；d2 的原点孔套在 e2_pin 上转动（z∈[20,26]）。
- p_pin 固定在 d2 的另一孔里（z∈[14,32]），d1 的另一孔也套在 p_pin 上；tool 的中心孔固定在 p_pin 上（z∈[26,32]）。末端在两肘点连线的上方。
- 两只主动臂的转角保持自由（整机活动度 2），不要固定。各件互不穿模，装好后保存。"""
    rubric = rub("base", 2, {"base": ["base"], "p1": ["p1"], "p2": ["p2"], "d1": ["d1"], "d2": ["d2", "p_pin", "tool"]},
                 [("p1", "base", R), ("p2", "base", R), ("d1", "p1", R), ("d2", "p2", R), ("d1", "d2", RC)],
                 flex={"a1_pin": ["base", "p1"], "a2_pin": ["base", "p2"], "e1_pin": ["p1", "d1"],
                       "e2_pin": ["p2", "d2"]})
    return mkp("five_bar_robot", "five_bar_robot", "五杆并联机器人（多位姿）",
               "两主动臂 + 两从动臂闭链，活动度 2；三组双角度位姿检查两肘点与末端。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P))


# ---- P5 码垛机器人（双平行四边形，活动度 2）

@case
def palletizing_robot():
    L1, L2, Lr, F1 = 120, 140, 30, (-30, 0)
    P = {"pick": (100, 10), "lift": (80, -10), "place": (120, 25)}

    def geo(name):
        al, be = P[name]
        E = pol((0, 0), L1, al)
        W = pol(E, L2, be)
        R_ = pol(E, Lr, be + 180)
        K = pol((0, 0), Lr, be + 180)
        T1, T2, W2 = (E[0] - 30, E[1]), (E[0], E[1] - 30), (W[0], W[1] - 30)
        return al, be, E, W, R_, K, T1, T2, W2

    parts = {
        "pr_frame": plate("底座", [-60, 80], [-40, 60], 8, [(0, 0, 10), (*F1, 6)]),
        "pr_lower": link("大臂", L1, 18, 6, [(0, 10), (L1, 8)]),
        "pr_crank": link("驱动曲柄", Lr, 16, 6, [(0, 10), (Lr, 6)]),
        "pr_upper": link("小臂", Lr + L2, 16, 6, [(0, 6), (Lr, 8), (Lr + L2, 6)]),
        "pr_bar120": link("拉杆 120", L1, 10, 6, [(0, 6), (L1, 6)]),
        "pr_bar140": link("调平拉杆 140", L2, 10, 6, [(0, 6), (L2, 6)]),
        "pr_tri": plate("三角板", [-38, 8], [-38, 8], 6, [(0, 0, 8), (-30, 0, 6), (0, -30, 6)]),
        "pr_wrist": plate("腕部法兰", [-8, 30], [-38, 8], 6, [(0, 0, 6), (0, -30, 6)]),
        "pr_s_pin": pin("肩轴", 10, 20),
        "pr_pin14": pin("底铰销", 6, 14),
        "pr_e_pin": pin("肘轴", 8, 18),
        "pr_pin12": pin("铰销", 6, 12),
        "pr_pin18": pin("长铰销", 6, 18),
    }
    roles = {"frame": "pr_frame", "s_pin": "pr_s_pin", "f1_pin": "pr_pin14", "lower": "pr_lower", "e_pin": "pr_e_pin",
             "crank": "pr_crank", "k_pin": "pr_pin18", "upper": "pr_upper", "r_pin": "pr_pin12", "drive": "pr_bar120",
             "tri": "pr_tri", "t1_pin": "pr_pin12", "t2_pin": "pr_pin12", "lev1": "pr_bar120", "w_pin": "pr_pin18",
             "wrist": "pr_wrist", "lev2": "pr_bar140", "w2_pin": "pr_pin18"}
    z = {"frame": 0, "s_pin": 0, "f1_pin": 0, "lower": 8, "e_pin": 8, "crank": 14, "k_pin": 14, "upper": 20,
         "r_pin": 20, "drive": 26, "tri": 14, "t1_pin": 8, "t2_pin": 14, "lev1": 8, "w_pin": 8, "wrist": 8,
         "lev2": 20, "w2_pin": 8}

    def layout(name):
        al, be, E, W, R_, K, T1, T2, W2 = geo(name)
        return {"frame": (0, 0, 0), "s_pin": (0, 0, 0), "f1_pin": (*F1, 0), "lower": (0, 0, al), "e_pin": (*E, al),
                "crank": (0, 0, be + 180), "k_pin": (*K, be + 180), "upper": (*R_, be), "r_pin": (*R_, be),
                "drive": (*K, ang(K, R_)), "tri": (*E, 0), "t1_pin": (*T1, 0), "t2_pin": (*T2, 0),
                "lev1": (*F1, ang(F1, T1)), "w_pin": (*W, be), "wrist": (*W, 0), "lev2": (*T2, be),
                "w2_pin": (*W2, be)}

    steps = [("ground", "frame"), ("fix", "s_pin", None, "frame", (0, 0)), ("fix", "f1_pin", None, "frame", F1),
             ("rev", "lower", (0, 0), "s_pin", None), ("fix", "e_pin", None, "lower", (L1, 0)),
             ("rev", "crank", (0, 0), "s_pin", None), ("fix", "k_pin", None, "crank", (Lr, 0)),
             ("rev", "upper", (Lr, 0), "e_pin", None), ("fix", "r_pin", None, "upper", (0, 0)),
             ("rev", "drive", (0, 0), "k_pin", None), ("cyl", "drive", (L1, 0), "r_pin", None),
             ("rev", "tri", (0, 0), "e_pin", None), ("fix", "t1_pin", None, "tri", (-30, 0)),
             ("fix", "t2_pin", None, "tri", (0, -30)), ("rev", "lev1", (0, 0), "f1_pin", None),
             ("cyl", "lev1", (L1, 0), "t1_pin", None), ("fix", "w_pin", None, "upper", (Lr + L2, 0)),
             ("rev", "wrist", (0, 0), "w_pin", None), ("rev", "lev2", (0, 0), "t2_pin", None),
             ("fix", "w2_pin", None, "lev2", (L2, 0)), ("cyl", "wrist", (0, -30), "w2_pin", None)]
    poses = []
    for name in P:
        al, be, E, W, R_, K, T1, T2, W2 = geo(name)
        poses.append({"name": name, "set": [
            angle_item("lower", "frame", al, [pt("lower", (L1, 0), E)]),
            angle_item("crank", "frame", be + 180, [pt("upper", (Lr + L2, 0), W), pt("lev2", (L2, 0), W2)])]})
    prompt = f"""装配码垛机器人的臂部：大臂、小臂各由一路驱动（肩部两个同轴转动件），小臂经平行四边形拉杆驱动；
另有两组平行四边形调平拉杆，使腕部法兰始终保持水平。{COMMON}
- frame 固定，z∈[0,8]。肩轴孔 S=(0,0) 直径 10，调平铰孔 F=({F1[0]},{F1[1]}) 直径 6。
  s_pin 固定在 S 孔（z∈[0,20]），f1_pin 固定在 F 孔（z∈[0,14]）。
- lower（大臂，长 {L1}）的 10 mm 孔套在 s_pin 上转动（z∈[8,14]）；e_pin 固定在 lower 的 8 mm 孔里（z∈[8,26]），记为肘点 E。
- crank（长 {Lr}）的 10 mm 孔也套在 s_pin 上转动（z∈[14,20]）；k_pin 固定在 crank 的 6 mm 孔里（z∈[14,32]）。
- upper（小臂，三孔：0、{Lr}、{Lr + L2}）的 X={Lr} 孔套在 e_pin 上转动（z∈[20,26]）；r_pin 固定在 upper 的原点孔（z∈[20,32]）。
  drive（长 {L1}）两孔分别套在 k_pin、r_pin 上（z∈[26,32]），crank 与 upper 的后伸段始终平行。
- tri（三角板，孔 (0,0) 8 mm、(-30,0) 与 (0,-30) 6 mm）的 8 mm 孔套在 e_pin 上转动（z∈[14,20]）；t1_pin、t2_pin 分别固定在其 (-30,0)、(0,-30) 孔
  （t1_pin z∈[8,20]，t2_pin z∈[14,26]）。lev1（长 {L1}）两孔分别套在 f1_pin、t1_pin 上（z∈[8,14]），使 tri 始终不转。
- w_pin 固定在 upper 的 X={Lr + L2} 孔（腕点 W，z∈[8,26]）；wrist（孔 (0,0)、(0,-30)）的原点孔套在 w_pin 上（z∈[8,14]）；
  lev2（长 {L2}）原点孔套在 t2_pin 上（z∈[20,26]），w2_pin 固定在 lev2 的另一孔（z∈[8,26]），wrist 的 (0,-30) 孔套在 w2_pin 上。wrist 始终不转。
- 大臂、小臂两个转角保持自由（整机活动度 2），不要固定。各件互不穿模，装好后保存。"""
    rubric = rub("frame", 2, {"frame": ["frame", "s_pin"], "lower": ["lower", "e_pin"], "crank": ["crank"],
                              "upper": ["upper"], "drive": ["drive"], "tri": ["tri"], "lev1": ["lev1"],
                              "lev2": ["lev2"], "wrist": ["wrist"]},
                 [("lower", "frame", R), ("crank", "frame", R), ("upper", "lower", R), ("tri", "lower", R),
                  ("drive", "crank", R), ("drive", "upper", RC), ("lev1", "frame", R), ("lev1", "tri", RC),
                  ("lev2", "tri", R), ("wrist", "upper", R), ("wrist", "lev2", RC)],
                 flex={"f1_pin": ["frame", "lev1"], "k_pin": ["crank", "drive"], "r_pin": ["upper", "drive"],
                       "t1_pin": ["tri", "lev1"], "t2_pin": ["tri", "lev2"], "w_pin": ["upper", "wrist"],
                       "w2_pin": ["lev2", "wrist"]})
    return mkp("palletizing_robot", "palletizing_robot", "码垛机器人（多位姿）",
               "驱动平行四边形 + 两级调平平行四边形，18 个组件、活动度 2；三个位姿检查肘点、腕点与腕部法兰姿态。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P))


# ---- P6 齿轮同步平行夹爪

@case
def gear_sync_gripper():
    GL, GR, Ld = (-15, 0), (15, 0), 50
    vL, vR = (-25.3, 5.9), (25.3, 5.9)
    IL, IR = (GL[0] + vL[0], GL[1] + vL[1]), (GR[0] + vR[0], GR[1] + vR[1])
    P = {"open": -125, "mid": -105, "close": -82}
    parts = {
        "gg_base": plate("夹爪底座", [-60, 60], [-30, 25], 8, [(*GL, 10), (*GR, 10), (*map(r2, IL), 6), (*map(r2, IR), 6)]),
        "gg_shaft": pin("齿轮轴", 10, 22),
        "gg_gear": gear(15, 2, 8, 10),
        "gg_drive": link("主动摆杆", Ld, 14, 6, [(0, 10), (Ld, 6)]),
        "gg_idler": link("从动摆杆", Ld, 10, 6, [(0, 6), (Ld, 6)]),
        "gg_pin22": pin("底座销", 6, 22),
        "gg_pin12": pin("铰销", 6, 12),
        "gg_finger_l": plate("左指", [-32, 4], [-70, 12], 6, [(0, 0, 6), (*vL, 6)]),
        "gg_finger_r": plate("右指", [-4, 32], [-70, 12], 6, [(0, 0, 6), (*vR, 6)]),
    }
    roles = {"base": "gg_base"}
    z = {"base": 0}
    for s in "lr":
        roles |= {f"shaft_{s}": "gg_shaft", f"gear_{s}": "gg_gear", f"drive_{s}": "gg_drive", f"ipin_{s}": "gg_pin22",
                  f"idler_{s}": "gg_idler", f"fpin_d{s}": "gg_pin12", f"fpin_i{s}": "gg_pin12",
                  f"finger_{s}": f"gg_finger_{s}"}
        z |= {f"shaft_{s}": 0, f"gear_{s}": 8, f"drive_{s}": 16, f"ipin_{s}": 0, f"idler_{s}": 16, f"fpin_d{s}": 16,
              f"fpin_i{s}": 16, f"finger_{s}": 22}

    def layout(name):
        out = {"base": (0, 0, 0)}
        for s, G, I, v, th in (("l", GL, IL, vL, P[name]), ("r", GR, IR, vR, 180 - P[name])):
            F = pol(G, Ld, th)
            Fi = (F[0] + v[0], F[1] + v[1])
            out |= {f"shaft_{s}": (*G, th), f"gear_{s}": (*G, th + (12 if s == "r" else 0)), f"drive_{s}": (*G, th),
                    f"ipin_{s}": (*I, 0), f"idler_{s}": (*I, th), f"fpin_d{s}": (*F, th), f"fpin_i{s}": (*Fi, th),
                    f"finger_{s}": (*F, 0)}
        return out

    steps = [("ground", "base")]
    for s, G in (("l", GL), ("r", GR)):
        steps += [("rev", f"shaft_{s}", None, "base", G), ("fix", f"gear_{s}", None, f"shaft_{s}", None),
                  ("fix", f"drive_{s}", (0, 0), f"shaft_{s}", None)]
    steps.append(("mesh", "gear_l", "gear_r"))
    for s, I, v in (("l", IL, vL), ("r", IR, vR)):
        steps += [("fix", f"ipin_{s}", None, "base", tuple(map(r2, I))), ("rev", f"idler_{s}", (0, 0), f"ipin_{s}", None),
                  ("fix", f"fpin_d{s}", None, f"drive_{s}", (Ld, 0)), ("rev", f"finger_{s}", (0, 0), f"fpin_d{s}", None),
                  ("fix", f"fpin_i{s}", None, f"idler_{s}", (Ld, 0)), ("cyl", f"finger_{s}", v, f"fpin_i{s}", None)]
    poses, gap = [], {}
    for name, th in P.items():
        FL, FR = pol(GL, Ld, th), pol(GR, Ld, 180 - th)
        gap[name] = r2(FR[0] - FL[0] - 8)
        # 静态求解（加临时角度配合后重建）不传递齿轮配合，右侧不会跟着左侧动，所以两侧各设一个角度；
        # 齿轮同步由装配 rubric 的齿轮耦合与活动度 1 考核
        poses.append({"name": name, "set": [
            angle_item("drive_l", "base", th, [pt("finger_l", (0, 0), FL)]),
            angle_item("drive_r", "base", 180 - th, [pt("finger_r", (0, 0), FR),
                                                     pt("idler_r", (Ld, 0), (FR[0] + vR[0], FR[1] + vR[1]))])]})
    prompt = f"""装配齿轮同步平行夹爪：左右两只主动摆杆各固定在一只 15 齿齿轮上，两齿轮直接啮合、反向同步；
每侧"主动摆杆—从动摆杆—指板"组成平行四边形，两指始终竖直、对称开合。{COMMON}
- base 固定，z∈[0,8]：齿轮轴孔 ({GL[0]},0)、({GR[0]},0) 直径 10；从动摆杆轴孔 ({r2(IL[0])},{r2(IL[1])})、({r2(IR[0])},{r2(IR[1])}) 直径 6。
- shaft_l、shaft_r 分别穿过两个齿轮轴孔绕 Z 转动（z∈[0,22]）；gear_l、gear_r 固定在对应轴上（z∈[8,16]），两者外啮合（模数 2，中心距 30）；
  drive_l、drive_r（长 {Ld}）的 10 mm 孔固定在对应轴上（z∈[16,22]）。
- ipin_l、ipin_r 固定在两个从动摆杆轴孔里（z∈[0,22]）；idler_l、idler_r（长 {Ld}）的原点孔分别套在上面转动（z∈[16,22]）。
- fpin_dl、fpin_dr 固定在两只主动摆杆的 6 mm 孔里，fpin_il、fpin_ir 固定在两只从动摆杆的另一孔里（都在 z∈[16,28]）。
- finger_l 的 (0,0) 孔套在 fpin_dl 上、({vL[0]:g},{vL[1]:g}) 孔套在 fpin_il 上（z∈[22,28]）；finger_r 的 (0,0) 孔套在 fpin_dr 上、
  ({vR[0]:g},{vR[1]:g}) 孔套在 fpin_ir 上。两只摆杆都朝下（−Y 侧）偏外伸出，左右镜像对称，指尖朝下。
- 开合这 1 个自由度保持自由（整机活动度 1），不要固定。各件互不穿模，装好后保存。"""
    bodies = {"base": ["base"]}
    joints, flex = [], {}
    for s in "lr":
        bodies |= {f"jaw_{s}": [f"shaft_{s}", f"gear_{s}", f"drive_{s}"], f"idler_{s}": [f"idler_{s}"],
                   f"finger_{s}": [f"finger_{s}"]}
        joints += [(f"jaw_{s}", "base", R), (f"idler_{s}", "base", R), (f"finger_{s}", f"jaw_{s}", R),
                   (f"finger_{s}", f"idler_{s}", RC)]
        flex |= {f"ipin_{s}": ["base", f"idler_{s}"], f"fpin_d{s}": [f"jaw_{s}", f"finger_{s}"],
                 f"fpin_i{s}": [f"idler_{s}", f"finger_{s}"]}
    rubric = rub("base", 1, bodies, joints, flex=flex, couplings=[gc("gear_l", "gear_r", 15, 15, 2)])
    return mkp("gear_sync_gripper", "gear_sync_gripper", "齿轮同步平行夹爪（多位姿）",
               "15/15 齿轮反向同步 + 两组平行四边形指板，17 个组件、活动度 1；三个开合角检查两指位置。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P), gap)


# ---- P7 连杆驱动三节手指（反平行四边形耦合）

@case
def robot_finger():
    e, L1, L2, L3 = 14, 50, 40, 30
    G0 = (e, 0)

    def loc(world, pose):
        x, y, d = pose
        a = math.radians(d)
        dx, dy = world[0] - x, world[1] - y
        return (r2(dx * math.cos(a) + dy * math.sin(a)), r2(-dx * math.sin(a) + dy * math.cos(a)))

    h1 = loc(circ((0, L1), G0, e, L1, -1), (0, L1, 90))
    g1 = (L1, -e)
    h2 = loc(circ((0, L1 + L2), (e, L1), e, L2, -1), (0, L1 + L2, 90))
    P = {"open": 85, "half": 65, "grip": 45}

    def geo(name):
        al = P[name]
        J1 = pol((0, 0), L1, al)
        b = solve_rot(J1, G0, h1, L1, -1)
        J2 = pol(J1, L2, b)
        G1 = attach((0, 0, al), g1)[:2]
        c = solve_rot(J2, G1, h2, L2, -1)
        return al, b, c, J1, J2, G1, attach((*J1, b), h1)[:2], attach((*J2, c), h2)[:2], pol(J2, L3, c)

    parts = {
        "rf_palm": plate("掌板", [-40, 40], [-60, 8], 8, [(0, 0, 8), (*G0, 4)]),
        "rf_p1": plate("近节指骨", [-8, 58], [-20, 7], 6, [(0, 0, 8), (L1, 0, 8), (*g1, 4)]),
        "rf_p2": plate("中节指骨", [-12, 48], [-7, 18], 6, [(0, 0, 8), (L2, 0, 8), (*h1, 4)]),
        "rf_p3": plate("远节指骨", [-13, 34], [-7, 17], 6, [(0, 0, 8), (*h2, 4)]),
        "rf_link1": link("耦合杆 1", L1, 8, 6, [(0, 4), (L1, 4)]),
        "rf_link2": link("耦合杆 2", L2, 8, 6, [(0, 4), (L2, 4)]),
        "rf_m_pin": pin("掌指关节轴", 8, 20),
        "rf_pin8": pin("指关节轴", 8, 12),
        "rf_pin4_14": pin("短耦合销", 4, 14),
        "rf_pin4_12": pin("耦合销", 4, 12),
        "rf_pin4_18": pin("长耦合销", 4, 18),
    }
    roles = {"palm": "rf_palm", "m_pin": "rf_m_pin", "g0_pin": "rf_pin4_14", "p1": "rf_p1", "j1_pin": "rf_pin8",
             "g1_pin": "rf_pin4_12", "p2": "rf_p2", "h1_pin": "rf_pin4_18", "link1": "rf_link1", "j2_pin": "rf_pin8",
             "p3": "rf_p3", "h2_pin": "rf_pin4_12", "link2": "rf_link2"}
    z = {"palm": 0, "m_pin": 0, "g0_pin": 0, "p1": 14, "j1_pin": 14, "g1_pin": 8, "p2": 20, "h1_pin": 8,
         "link1": 8, "j2_pin": 14, "p3": 14, "h2_pin": 8, "link2": 8}

    def layout(name):
        al, b, c, J1, J2, G1, H1, H2, T = geo(name)
        return {"palm": (0, 0, 0), "m_pin": (0, 0, 0), "g0_pin": (*G0, 0), "p1": (0, 0, al), "j1_pin": (*J1, al),
                "g1_pin": (*G1, al), "p2": (*J1, b), "h1_pin": (*H1, b), "link1": (*G0, ang(G0, H1)),
                "j2_pin": (*J2, b), "p3": (*J2, c), "h2_pin": (*H2, c), "link2": (*G1, ang(G1, H2))}

    steps = [("ground", "palm"), ("fix", "m_pin", None, "palm", (0, 0)), ("fix", "g0_pin", None, "palm", G0),
             ("rev", "p1", (0, 0), "m_pin", None), ("fix", "j1_pin", None, "p1", (L1, 0)),
             ("fix", "g1_pin", None, "p1", g1), ("rev", "p2", (0, 0), "j1_pin", None),
             ("fix", "h1_pin", None, "p2", h1), ("rev", "link1", (0, 0), "g0_pin", None),
             ("cyl", "link1", (L1, 0), "h1_pin", None), ("fix", "j2_pin", None, "p2", (L2, 0)),
             ("rev", "p3", (0, 0), "j2_pin", None), ("fix", "h2_pin", None, "p3", h2),
             ("rev", "link2", (0, 0), "g1_pin", None), ("cyl", "link2", (L2, 0), "h2_pin", None)]
    poses, curl = [], {}
    for name, al in P.items():
        _, b, c, J1, J2, G1, H1, H2, T = geo(name)
        curl[name] = r2(fold(al - c))
        poses.append({"name": name, "set": [angle_item("p1", "palm", al,
                                                       [pt("p2", (L2, 0), J2), pt("p3", (L3, 0), T)])]})
    prompt = f"""装配连杆驱动的三节机器人手指：只驱动近节指骨，中节、远节经两组交叉（反平行四边形）耦合杆联动，手指整体屈曲。{COMMON}
- palm 固定，z∈[0,8]：掌指关节孔 M=(0,0) 直径 8，耦合铰孔 G0=({G0[0]},0) 直径 4。m_pin 固定在 M 孔（z∈[0,20]），g0_pin 固定在 G0 孔（z∈[0,14]）。
- p1（近节，孔 (0,0)、({L1},0) 直径 8，({g1[0]:g},{g1[1]:g}) 直径 4）的原点孔套在 m_pin 上转动（z∈[14,20]）；
  j1_pin 固定在 p1 的 ({L1},0) 孔（z∈[14,26]），g1_pin 固定在 p1 的 4 mm 孔（z∈[8,20]）。
- p2（中节，孔 (0,0)、({L2},0) 直径 8，({h1[0]:g},{h1[1]:g}) 直径 4）的原点孔套在 j1_pin 上转动（z∈[20,26]）；
  h1_pin 固定在 p2 的 4 mm 孔（z∈[8,26]），j2_pin 固定在 p2 的 ({L2},0) 孔（z∈[14,26]）。
- p3（远节，孔 (0,0) 直径 8，({h2[0]:g},{h2[1]:g}) 直径 4，指尖在局部 ({L3},0)）的原点孔套在 j2_pin 上转动（z∈[14,20]）；h2_pin 固定在 p3 的 4 mm 孔（z∈[8,20]）。
- link1（长 {L1}）两孔分别套在 g0_pin、h1_pin 上（z∈[8,14]）；link2（长 {L2}）两孔分别套在 g1_pin、h2_pin 上（z∈[8,14]）。
  两根耦合杆都与相邻指骨交叉（反平行四边形），因此近节向 +X 侧屈曲时中节、远节也随之向同侧屈曲；伸直时手指沿 +Y。
- 近节转角这 1 个自由度保持自由（整机活动度 1），不要固定。各件互不穿模，装好后保存。"""
    rubric = rub("palm", 1, {"palm": ["palm"], "p1": ["p1"], "p2": ["p2"], "p3": ["p3"], "link1": ["link1"],
                             "link2": ["link2"]},
                 [("p1", "palm", R), ("p2", "p1", R), ("link1", "palm", R), ("link1", "p2", RC), ("p3", "p2", R),
                  ("link2", "p1", R), ("link2", "p3", RC)],
                 flex={"m_pin": ["palm", "p1"], "g0_pin": ["palm", "link1"], "j1_pin": ["p1", "p2"],
                       "g1_pin": ["p1", "link2"], "h1_pin": ["p2", "link1"], "j2_pin": ["p2", "p3"],
                       "h2_pin": ["p3", "link2"]})
    return mkp("robot_finger", "robot_finger_coupled", "连杆驱动三节手指（多位姿）",
               "近节 + 两组反平行四边形耦合，活动度 1；三个近节角检查中节末端与指尖。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P), curl)


# ---- P8 水平变幅起重机（四杆臂架 + 变幅油缸）

@case
def luffing_crane():
    A, D, F = (0, 0), (-25, 75), (90, -30)
    Lb, Lrk, Lcb, Lh, Lg, Lcy = 170, 130, 40, 120, 60, 70
    P = {"near": 65, "mid": 50, "far": 35}

    def geo(name):
        al = P[name]
        B = pol(A, Lb, al)
        C = circ(B, D, Lcb, Lrk, -1)
        hd = ang(C, B)
        T = pol(C, Lh, hd)
        G = pol(A, Lg, al)
        return al, B, C, hd, T, G, ang(F, G)

    parts = {
        "lu_base": plate("塔身板", [-60, 240], [-50, 200], 8, [(*A, 10), (*D, 8), (*F, 8)]),
        "lu_boom": link("主臂", Lb, 16, 6, [(0, 10), (Lg, 6), (Lb, 8)]),
        "lu_rocker": link("拉杆", Lrk, 12, 6, [(0, 8), (Lrk, 8)]),
        "lu_head": link("象鼻梁", Lh, 14, 6, [(0, 8), (Lcb, 8), (Lh, 6)]),
        "lu_barrel": link("油缸筒", Lcy, 16, 6, [(0, 8)]),
        "lu_ram": link("活塞杆", Lcy, 10, 6, [(0, 6)]),
        "lu_a_pin": pin("主臂根销", 10, 20),
        "lu_pin20": pin("拉杆根销", 8, 20),
        "lu_pin14": pin("缸底销", 8, 14),
        "lu_pin8_12": pin("臂头销", 8, 12),
        "lu_pin6_12": pin("铰销", 6, 12),
        "lu_hook": disk("吊钩", 24, 6, 6),
    }
    roles = {"base": "lu_base", "a_pin": "lu_a_pin", "boom": "lu_boom", "b_pin": "lu_pin8_12",
             "g_pin": "lu_pin6_12", "d_pin": "lu_pin20", "rocker": "lu_rocker", "c_pin": "lu_pin8_12",
             "head": "lu_head", "t_pin": "lu_pin6_12", "hook": "lu_hook", "f_pin": "lu_pin14",
             "barrel": "lu_barrel", "ram": "lu_ram"}
    z = {"base": 0, "a_pin": 0, "boom": 14, "b_pin": 8, "g_pin": 14, "d_pin": 0, "rocker": 14, "c_pin": 8,
         "head": 8, "t_pin": 8, "hook": 14, "f_pin": 0, "barrel": 8, "ram": 20}

    def layout(name):
        al, B, C, hd, T, G, u = geo(name)
        ar = ang(D, C)
        return {"base": (0, 0, 0), "a_pin": (*A, 0), "boom": (*A, al), "b_pin": (*B, al), "g_pin": (*G, al),
                "d_pin": (*D, 0), "rocker": (*D, ar), "c_pin": (*C, ar), "head": (*C, hd), "t_pin": (*T, hd),
                "hook": (*T, hd), "f_pin": (*F, 0), "barrel": (*F, u), "ram": (*G, u + 180)}

    steps = [("ground", "base"), ("fix", "a_pin", None, "base", A), ("rev", "boom", (0, 0), "a_pin", None),
             ("fix", "b_pin", None, "boom", (Lb, 0)), ("fix", "g_pin", None, "boom", (Lg, 0)),
             ("fix", "d_pin", None, "base", D), ("rev", "rocker", (0, 0), "d_pin", None),
             ("fix", "c_pin", None, "rocker", (Lrk, 0)), ("rev", "head", (0, 0), "c_pin", None),
             ("cyl", "head", (Lcb, 0), "b_pin", None), ("fix", "t_pin", None, "head", (Lh, 0)),
             ("fix", "hook", (0, 0), "t_pin", None), ("fix", "f_pin", None, "base", F),
             ("rev", "barrel", (0, 0), "f_pin", None), ("rev", "ram", (0, 0), "g_pin", None),
             ("coinc", "ram", "top", "barrel", "top")]
    poses, tip = [], {}
    for name, al in P.items():
        _, B, C, hd, T, G, u = geo(name)
        tip[name] = r2(T[1])
        poses.append({"name": name, "set": [angle_item("boom", "base", al,
                                                       [pt("head", (Lh, 0), T), pt("boom", (Lb, 0), B),
                                                        pt("ram", (0, 0), G)])]})
    prompt = f"""装配水平变幅起重机的臂架：主臂、拉杆与象鼻梁组成四杆机构，变幅油缸推动主臂俯仰时，吊点的升降远小于主臂头的升降（近似水平变幅）。{COMMON}
- base 固定，z∈[0,8]：主臂根孔 A=(0,0) 直径 10，拉杆根孔 D=({D[0]},{D[1]}) 与缸底孔 F=({F[0]},{F[1]}) 直径 8。
- a_pin 固定在 A 孔（z∈[0,20]）；boom（主臂，孔 0、{Lg}、{Lb}）的原点孔套在 a_pin 上转动（z∈[14,20]）；
  b_pin 固定在 boom 的 X={Lb} 孔（z∈[8,20]），g_pin 固定在 boom 的 X={Lg} 孔（z∈[14,26]）。
- d_pin 固定在 D 孔（z∈[0,20]）；rocker（拉杆，长 {Lrk}）的原点孔套在 d_pin 上转动（z∈[14,20]）；c_pin 固定在 rocker 的另一孔（z∈[8,20]）。
- head（象鼻梁，孔 0、{Lcb}、{Lh}）的原点孔套在 c_pin 上（z∈[8,14]），X={Lcb} 孔套在 b_pin 上，吊点端（X={Lh}）朝外；
  拉杆铰点 C 在主臂头 B 的后上方。t_pin 固定在 head 的 X={Lh} 孔（z∈[8,20]），hook 固定在 t_pin 上（z∈[14,20]）。
- f_pin 固定在 F 孔（z∈[0,14]）；barrel（油缸筒）的孔套在 f_pin 上转动（z∈[8,14]），筒身朝 g_pin 伸出；
  ram（活塞杆）的孔套在 g_pin 上转动（z∈[20,26]），杆身朝 f_pin 伸进缸筒，ram 与 barrel 轴线始终共线、只能相对伸缩。
- 主臂俯仰这 1 个自由度保持自由（整机活动度 1），不要固定角度。各件互不穿模，装好后保存。"""
    rubric = rub("base", 1, {"base": ["base"], "boom": ["boom"], "rocker": ["rocker"], "head": ["head", "t_pin", "hook"],
                             "barrel": ["barrel"], "ram": ["ram"]},
                 [("boom", "base", R), ("rocker", "base", R), ("head", "rocker", R), ("head", "boom", RC),
                  ("barrel", "base", R), ("ram", "boom", R), ("ram", "barrel", SP)],
                 flex={"a_pin": ["base", "boom"], "d_pin": ["base", "rocker"], "f_pin": ["base", "barrel"],
                       "b_pin": ["boom", "head"], "c_pin": ["rocker", "head"], "g_pin": ["boom", "ram"]})
    return mkp("luffing_crane", "luffing_crane", "水平变幅起重机（多位姿）",
               "四杆臂架（主臂—拉杆—象鼻梁）+ 变幅油缸闭环，活动度 1；三个俯仰角检查吊点、臂头与油缸铰点。",
               parts, roles, z, layout, steps, prompt, poses, rubric, list(P), tip)


# ================================================================== 输出

def write_all() -> None:
    from eval.assembly_scratch import bench2_check
    from eval.assembly_scratch.bench_cases import CASES
    clash = {pid for c in CASES.values() for pid in c.parts} & {pid for c in CASES2.values() for pid in c.parts}
    assert not clash, f"零件 id 与 bench_cases 重复，会覆盖它的零件 rubric：{sorted(clash)}"
    for c in CASES2.values():
        for path, text in ((HERE / f"{c.name}.yaml", case_yaml(c)),
                           (HERE / "assembly_rubrics" / f"{c.case_id}.yaml", rubric_yaml(c))):
            path.write_text(text.replace("由 bench_cases.py 生成", "由 bench_cases2.py 生成"), encoding="utf-8")
        for pid, p in c.parts.items():
            (HERE / "rubrics" / f"{pid}.yaml").write_text(part_rubric(pid, p), encoding="utf-8")
    table = {c.case_id: bench2_check.analytic_anchors(c) for c in CASES2.values()}
    ANALYTIC_ANCHORS.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {len(CASES2)} 个测例")


def report() -> None:
    for c in CASES2.values():
        extra = ", ".join(f"{k}={v:.2f}" for k, v in c.info.items())
        print(f"{c.name:22s} parts={len(c.parts):2d} comps={len(c.roles):2d} dof={c.rubric['mobility']} "
              f"{c.motion.get('mode', 'motor'):6s} {extra}")


def build_parts(name: str) -> None:
    from eval.assembly_scratch.bench_cases import build_part
    from eval.assembly_scratch.build_reference_assemblies import GOLD
    c = CASES2[name]
    out = GOLD / name
    out.mkdir(parents=True, exist_ok=True)
    for pid, p in c.parts.items():
        build_part(str(out / f"{pid}.SLDPRT"), p)


if __name__ == "__main__":
    {"write": write_all, "report": report}[sys.argv[1]]()
