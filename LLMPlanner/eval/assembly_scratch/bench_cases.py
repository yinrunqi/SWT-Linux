"""扩展装配测例（20 个）：零件、角色、提示词、运动规格、装配 rubric 与参考装配都从这里生成。

几何量（摆角、行程、位姿点位、初始插入位姿）由下面的平面运动学解算，不手填。
所有机构都是"零件 z=0 面落在指定绝对 z 层、转轴沿 Z"的平面叠层结构，提示词里写明各层 z 范围。

  python -m eval.assembly_scratch.bench_cases write      # 写 <case>.yaml、assembly_rubrics/、rubrics/
  python -m eval.assembly_scratch.bench_cases report     # 打印各例解算出的行程 / 摆角 / 传动角
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent


# ------------------------------------------------------------------ 平面几何

def pol(p, r, deg):
    return (p[0] + r * math.cos(math.radians(deg)), p[1] + r * math.sin(math.radians(deg)))


def ang(p, q):
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def circ(p, q, r1, r2, side=1):
    """以 p 为心半径 r1、q 为心半径 r2 的两圆交点；side=+1 取 p→q 左侧那一个。"""
    d = dist(p, q)
    a = (r1 ** 2 - r2 ** 2 + d ** 2) / (2 * d)
    h2 = r1 ** 2 - a ** 2
    if h2 < 0:
        raise ValueError("两圆无交点")
    h = math.sqrt(h2)
    ux, uy = (q[0] - p[0]) / d, (q[1] - p[1]) / d
    return (p[0] + a * ux - side * h * uy, p[1] + a * uy + side * h * ux)


def r2(x):
    return round(float(x), 2)


def seg_dist(p, a, b):
    ax, ay = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / (ax * ax + ay * ay)))
    return dist(p, (a[0] + t * ax, a[1] + t * ay))


# ------------------------------------------------------------------ 零件

def plate(label, x, y, t, holes):
    return {"kind": "plate", "label": label, "x": x, "y": y, "t": t, "holes": holes}


def link(label, L, w, t, holes):
    """两端圆头长条，半圆圆心在 (0,0) 与 (L,0)；holes: [(x, d)]，孔心都在 X 轴上，第一个孔在原点。"""
    return {"kind": "link", "label": label, "L": L, "w": w, "t": t, "holes": holes}


def pin(label, d, L):
    return {"kind": "pin", "label": label, "d": d, "L": L}


def disk(label, od, t, bore, holes=()):
    return {"kind": "disk", "label": label, "od": od, "t": t, "bore": bore, "holes": list(holes)}


def gear(z, m, face, bore):
    return {"kind": "gear", "z": z, "m": m, "face": face, "bore": bore}


def ring(z, m, face, od):
    return {"kind": "ring", "z": z, "m": m, "face": face, "od": od}


def g(x):
    return f"{x:g}"


def part_prompt(p: dict) -> str:
    k = p["kind"]
    if k == "plate":
        holes = "，".join(f"({g(x)},{g(y)}) 直径 {g(d)} mm" for x, y, d in p["holes"])
        return (f"创建{p['label']}：在 front_plane 上画矩形，X 从 {g(p['x'][0])} 到 {g(p['x'][1])} mm，"
                f"Y 从 {g(p['y'][0])} 到 {g(p['y'][1])} mm，向 +Z 拉伸 {g(p['t'])} mm。\n"
                f"开贯穿孔：{holes}。不要抽壳。")
    if k == "link":
        holes = "，".join(f"X={g(x)} 处直径 {g(d)} mm" for x, d in p["holes"])
        return (f"创建{p['label']}：在 front_plane 上画两端圆头的长条，宽 {g(p['w'])} mm，"
                f"两端半圆的圆心分别在原点和 ({g(p['L'])},0)，向 +Z 拉伸 {g(p['t'])} mm。\n"
                f"在 X 轴上开贯穿孔：{holes}。")
    if k == "pin":
        return f"创建{p['label']}：直径 {g(p['d'])} mm、长 {g(p['L'])} mm 的圆柱，轴线沿 Z，从 Z=0 向 +Z 拉伸。"
    if k == "disk":
        text = (f"创建{p['label']}：外径 {g(p['od'])} mm、厚 {g(p['t'])} mm 的圆盘，轴线沿 Z，从 Z=0 向 +Z 拉伸，"
                f"中心贯穿孔直径 {g(p['bore'])} mm。")
        if p["holes"]:
            text += "另开贯穿孔：" + "，".join(f"({g(x)},{g(y)}) 直径 {g(d)} mm" for x, y, d in p["holes"]) + "。"
        return text
    if k == "gear":
        return (f"创建渐开线直齿轮，模数 {g(p['m'])} mm，齿数 {p['z']}，压力角 30°，齿宽 {g(p['face'])} mm，"
                f"轴孔直径 {g(p['bore'])} mm，轴线沿 Z，从 Z=0 向 +Z 拉伸。优先 spur_gear。")
    if k == "ring":
        return (f"创建渐开线内齿圈，模数 {g(p['m'])} mm，齿数 {p['z']}，压力角 30°，齿宽 {g(p['face'])} mm，"
                f"外径 {g(p['od'])} mm，轴线沿 Z，从 Z=0 向 +Z 拉伸。优先 internal_gear。")
    raise ValueError(k)


def part_tools(p: dict) -> list[str]:
    return {"gear": ["spur_gear"], "ring": ["internal_gear"]}.get(p["kind"], [])


def _groups(holes, key):
    out: dict[float, list] = {}
    for h in holes:
        out.setdefault(float(key(h)), []).append(h)
    return out


def part_rubric(name: str, p: dict) -> str:
    """零件接口 rubric：外形、孔数与孔位（孔位在包围盒最小角为原点的世界轴坐标里比）。"""
    k = p["kind"]
    ents, rubrics, tol = {}, [], (0.25, 0.06)
    if k == "plate":
        x0, y0 = p["x"][0], p["y"][0]
        ents["wf"] = "{world_frame: {}}"
        checks = [f"bbox_sorted: [{', '.join(g(v) for v in sorted([p['x'][1] - x0, p['y'][1] - y0, p['t']]))}]"]
        for i, (d, hs) in enumerate(sorted(_groups(p["holes"], lambda h: h[2]).items())):
            ents[f"h{i}"] = f"{{cylinder: {{d: {g(d)}, side: inner}}, count: {len(hs)}}}"
            expect = ", ".join(f"[{g(round(x - x0, 3))}, {g(round(y - y0, 3))}]" for x, y, _ in hs)
            checks += [f"exists: h{i}", f"through: {{holes: h{i}}}",
                       f"axis_positions: {{frame: wf, items: h{i}, axes: [0, 1], expect: [{expect}]}}"]
        rubrics.append(("shape", checks))
        tol = (0.25, 0.04)
    elif k == "link":
        ents["wf"] = "{world_frame: {}}"
        w = p["w"]
        checks = [f"bbox_sorted: [{', '.join(g(v) for v in sorted([p['L'] + w, w, p['t']]))}]"]
        for i, (d, hs) in enumerate(sorted(_groups(p["holes"], lambda h: h[1]).items())):
            ents[f"h{i}"] = f"{{cylinder: {{d: {g(d)}, side: inner}}, count: {len(hs)}}}"
            expect = ", ".join(f"[{g(round(x + w / 2, 3))}, {g(round(w / 2, 3))}]" for x, _ in hs)
            checks += [f"exists: h{i}", f"through: {{holes: h{i}}}",
                       f"axis_positions: {{frame: wf, items: h{i}, axes: [0, 1], expect: [{expect}]}}"]
        rubrics.append(("shape", checks))
    elif k == "pin":
        ents["shank"] = f"{{cylinder: {{d: {g(p['d'])}, side: outer}}, count: 1}}"
        rubrics.append(("size", ["exists: shank", f"extent_along: {{axis: shank, of: all, value: {g(p['L'])}}}",
                                 f"bbox_sorted: [{g(p['d'])}, {g(p['d'])}, {g(p['L'])}]"]))
        tol = (0.2, 0.04)
    elif k == "disk":
        ents["rim"] = f"{{cylinder: {{d: {g(p['od'])}, side: outer}}, count: 1}}"
        ents["bore"] = f"{{cylinder: {{d: {g(p['bore'])}, side: inner}}, count: 1}}"
        checks = ["exists: rim", "exists: bore", "coaxial: [rim, bore]", "through: {holes: bore}",
                  f"extent_along: {{axis: rim, of: all, value: {g(p['t'])}}}"]
        for i, (d, hs) in enumerate(sorted(_groups(p["holes"], lambda h: h[2]).items())):
            assert d != p["bore"], "圆盘偏心孔与中心孔同径时 rubric 无法区分"
            radii = {round(math.hypot(x, y), 2) for x, y, _ in hs}
            assert len(radii) == 1
            ents[f"h{i}"] = f"{{cylinder: {{d: {g(d)}, side: inner}}, count: {len(hs)}}}"
            checks += [f"exists: h{i}", f"through: {{holes: h{i}}}",
                       f"axis_distance: {{from: rim, to: h{i}, value: {g(radii.pop())}}}"]
        rubrics.append(("shape", checks))
        tol = (0.25, 0.05)
    elif k == "gear":
        pitch = p["m"] * p["z"] / 2
        lo, hi, step = round(pitch - p["m"], 3), round(pitch + p["m"] - 0.1, 3), round(p["m"] / 8, 3)
        ents["bore"] = f"{{cylinder: {{d: {g(p['bore'])}, side: inner}}, count: 1}}"
        ents["tframe"] = "{axis_frame: {axis: bore}}"
        rubrics += [("bore", ["exists: bore", "through: {holes: bore}"]),
                    ("teeth", [f"angular_openings: {{frame: tframe, t_list: [{g(p['face'] / 2)}], "
                               f"scan_r: [{g(lo)}, {g(hi)}, {g(step)}], count: {p['z']}, identical: true}}",
                               f"extent_along: {{axis: bore, of: all, value: {g(p['face'])}}}"])]
        tol = (0.25, 0.08)
    elif k == "ring":
        pitch = p["m"] * p["z"] / 2
        lo, hi, step = round(pitch - 0.5 * p["m"], 3), round(pitch + p["m"], 3), round(p["m"] / 8, 3)
        ents["rim"] = f"{{cylinder: {{d: {g(p['od'])}, side: outer}}, count: 1}}"
        ents["tframe"] = "{axis_frame: {axis: rim}}"
        rubrics += [("rim", ["exists: rim", f"extent_along: {{axis: rim, of: all, value: {g(p['face'])}}}"]),
                    ("teeth", [f"angular_openings: {{frame: tframe, t_list: [{g(p['face'] / 2)}], "
                               f"scan_r: [{g(lo)}, {g(hi)}, {g(step)}], count: {p['z']}, identical: true}}"])]
        tol = (0.25, 0.08)
    lines = [f"case: {name}", f"prompt: {json.dumps(part_prompt(p).replace(chr(10), ''), ensure_ascii=False)}",
             f"tolerance: {{length_mm: {tol[0]}, angle_deg: 1.0, volume_rel: {tol[1]}}}", "entities:"]
    lines += [f"  {key}: {val}" for key, val in ents.items()]
    lines += ["rubrics:", "  - id: solid", "    hard: true", "    check:", "      - single_valid_solid: {}"]
    for rid, checks in rubrics:
        lines += [f"  - id: {rid}", "    hard: true", "    check:"] + [f"      - {c}" for c in checks]
    return "\n".join(lines) + "\n"


def build_part(path: Path, p: dict) -> None:
    """用 build_standard_parts 的基本体生成标准零件（单位换成 m）。"""
    from eval.assembly_scratch import build_standard_parts as bsp
    k, mm, path = p["kind"], 1e-3, Path(path)
    if k == "plate":
        bsp.plate(path, p["x"][0] * mm, p["y"][0] * mm, p["x"][1] * mm, p["y"][1] * mm,
                  [(x * mm, y * mm, d / 2 * mm) for x, y, d in p["holes"]], p["t"] * mm)
    elif k == "link":
        assert p["holes"][0][0] == 0
        bsp.link(path, p["L"] * mm, p["w"] * mm, p["holes"][0][1] / 2 * mm, p["t"] * mm,
                 holes=[(x * mm, d / 2 * mm) for x, d in p["holes"]])
    elif k == "pin":
        bsp.cylinder(path, p["d"] / 2 * mm, p["L"] * mm)
    elif k == "disk":
        bsp.disk(path, p["od"] / 2 * mm, p["t"] * mm,
                 [(0.0, 0.0, p["bore"] / 2 * mm)] + [(x * mm, y * mm, d / 2 * mm) for x, y, d in p["holes"]])
    elif k == "gear":
        bsp.gear(path, p["z"], p["m"] * mm, p["face"] * mm, p["bore"] / 2 * mm)
    elif k == "ring":
        bsp.ring_gear(path, p["z"], p["m"] * mm, p["face"] * mm, p["od"] / 2 * mm)
    else:
        raise ValueError(k)


# ------------------------------------------------------------------ 参考装配

class Ref:
    """参考装配记录器：记下每个角色的绝对 z，关节偏距按"a 的 z − b 所在零件的 z"自动算。"""

    def __init__(self, case: "Case"):
        from eval.assembly_scratch.build_reference_assemblies import GOLD, _run, _session
        self.case, self._run = case, _run
        self.s = _session()
        self.dest = GOLD / f"reference_{case.name}.SLDASM"
        self.parts = GOLD / case.name
        self.z: dict[str, float] = {}
        self.s.start_assembly(str(self.dest))

    def ins(self, role, x, y, z, rot=0.0, ground=False):
        args = {"part": str(self.parts / f"{self.case.roles[role]}.SLDPRT"), "name": role, "at": [x, y, z]}
        if rot:
            args["rotate_deg"] = rot
        self._run(self.s, "insert_component", args)
        self.z[role] = z
        if ground:
            self._run(self.s, "ground_component", {"component": role})

    def joint(self, kind, a, b, offset=True):
        ra, rb = a.split(":")[0], b.split(":")[0]
        args = {"type": kind, "a": a, "b": b, "face_a": f"{ra}:plane(front)", "face_b": f"{rb}:plane(front)"}
        if offset is True:
            args["offset"] = self.z[ra] - self.z[rb]
        self._run(self.s, "joint", args)

    def fixed(self, a, b):
        self.joint("fixed", a, b)

    def rev(self, a, b):
        self.joint("revolute", a, b)

    def close(self, a, b):
        """闭环最后一铰：只做同轴。"""
        self._run(self.s, "joint", {"type": "cylindrical", "a": a, "b": b})

    def plane(self, a, b, offset_mm):
        """a 面落在 b 面法向正侧 offset 处（滑块导向）。"""
        from geomcheck.assembly_motion import _pose_offset
        _pose_offset(self.s, a, b, offset_mm)

    def call(self, tool, args):
        self._run(self.s, tool, args)

    def mesh(self, a, b, za, zb, m, internal=False):
        args = {"a": a, "b": b, "teeth_a": za, "teeth_b": zb, "module": m}
        if internal:
            args["internal"] = True
        self._run(self.s, "mesh_gears", args)

    def finish(self, mobility):
        from eval.assembly_scratch.build_reference_assemblies import _finish
        return _finish(self.s, self.dest, mobility)


# ------------------------------------------------------------------ 测例

@dataclass
class Case:
    name: str                       # 文件名 / gold 目录名
    case_id: str
    title: str
    note: str
    parts: dict[str, dict]
    roles: dict[str, str]
    prompt: str
    motion: dict
    rubric: dict
    build: Callable[["Ref"], object] = None
    info: dict = field(default_factory=dict)


CASES: dict[str, Case] = {}


def case(fn):
    c = fn()
    CASES[c.name] = c
    return fn


def fourbar(A, D, a, b, c, side, steps=1440):
    """曲柄 AB=a、连杆 BC=b、摇杆 DC=c；返回摇杆角极差与最小传动角（连杆与摇杆夹角离 0/180 的最小距离）。"""
    angles, trans = [], []
    for i in range(steps):
        B = pol(A, a, 360 * i / steps)
        C = circ(B, D, b, c, side)
        angles.append(ang(D, C))
        mu = abs(((ang(C, B) - ang(C, D)) + 180) % 360 - 180)
        trans.append(min(mu, 180 - mu))
    unwrapped = [angles[0]]
    for v in angles[1:]:
        unwrapped.append(unwrapped[-1] + ((v - unwrapped[-1] + 180) % 360 - 180))
    return max(unwrapped) - min(unwrapped), min(trans)


def slider_x(r, L, e, theta):
    """曲柄半径 r、连杆 L、偏距 e，滑块沿 X（导路 y=e）。"""
    s = math.radians(theta)
    return r * math.cos(s) + math.sqrt(L * L - (e - r * math.sin(s)) ** 2)


def slider_stroke(r, L, e):
    xs = [slider_x(r, L, e, i / 4) for i in range(1440)]
    return max(xs) - min(xs)


COMMON = "不要把插入坐标当作最终位姿。所有转轴沿 Z；下面写的 z∈[a,b] 是零件装好后占的 Z 范围。"
GEAR_TIP = "各齿轮互不穿模，啮合齿相位要对上。"


def rub(ground, mobility, bodies, joints, flex=None, couplings=()):
    out = {"ground": ground, "mobility": mobility, "bodies": bodies}
    if flex:
        out["flex"] = flex
    out["joints"] = [{"bodies": [a, b], "type": t} for a, b, t in joints]
    if couplings:
        out["couplings"] = list(couplings)
        out["center_distance_tol_mm"] = 0.2
    return out


def gc(a, b, za, zb, m, internal=False):
    return {"type": "gear", "a": a, "b": b, "teeth": [za, zb], "module_mm": m, "external": not internal}


def mot(driver, fixed, moving, **kw):
    return {"driver": driver, "sweep_deg": 360, "frames": 13, "rpm": 10, "expect_fixed": fixed,
            "expect_moving": moving, **kw, "max_interference": 0}


R, RC, SP = "revolute", ["revolute", "cylindrical"], ["slider", "planar"]


def pick(cands, key):
    return max(cands, key=key)


# ---- M1 曲柄摇杆四杆

@case
def crank_rocker():
    A, D, a, b, c = (0, 0), (80, 0), 20, 70, 50
    swing, mu = fourbar(A, D, a, b, c, side=1)
    B0 = pol(A, a, 90)
    C0 = circ(B0, D, b, c, side=1)
    parts = {
        "cr_frame": plate("机架板", [-30, 110], [-30, 90], 8, [(0, 0, 10), (80, 0, 6)]),
        "cr_shaft": pin("曲柄轴", 10, 24),
        "cr_crank": link("曲柄", 20, 16, 6, [(0, 10), (20, 6)]),
        "cr_pin12": pin("铰销", 6, 12),
        "cr_coupler": link("连杆", 70, 12, 6, [(0, 6), (70, 6)]),
        "cr_rocker": link("摇杆", 50, 12, 6, [(0, 6), (50, 6)]),
        "cr_pin26": pin("摇杆支销", 6, 26),
    }
    roles = {"frame": "cr_frame", "shaft": "cr_shaft", "crank": "cr_crank", "crank_pin": "cr_pin12",
             "coupler": "cr_coupler", "coupler_pin": "cr_pin12", "rocker": "cr_rocker", "rocker_pin": "cr_pin26"}
    prompt = f"""装配曲柄摇杆四杆机构。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴承孔在原点，摇杆支孔在 (80,0)。
- shaft 穿过原点孔绕 Z 转动（z∈[-10,14]）；crank 的 10 mm 孔固定在 shaft 上（z∈[8,14]），与 shaft 同转。
- crank_pin 固定在 crank 的 6 mm 孔里（z∈[8,20]）；coupler 一端套在 crank_pin 上转动（z∈[14,20]）。
- coupler_pin 固定在 coupler 另一端孔里（z∈[14,26]）。
- rocker_pin 固定在 frame 的 (80,0) 孔里（z∈[0,26]）；rocker 一端套在 rocker_pin 上转动（z∈[20,26]），
  另一端套在 coupler_pin 上，组成曲柄 20、连杆 70、摇杆 50、机架 80 的曲柄摇杆机构，摇杆在机架连线上方（+Y 侧）。
- 曲柄整周转动时摇杆往复摆动约 {swing:.1f}°。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = {"driver": "shaft", "sweep_deg": 360, "frames": 13, "rpm": 10, "expect_fixed": ["frame"],
              "expect_moving": ["crank", "coupler", "rocker"], "swings": [{"role": "rocker", "deg": r2(swing)}],
              "max_interference": 0}
    rubric = {"ground": "frame", "mobility": 1,
              "bodies": {"frame": ["frame"], "crank": ["shaft", "crank"], "coupler": ["coupler"], "rocker": ["rocker"]},
              "flex": {"crank_pin": ["crank", "coupler"], "coupler_pin": ["coupler", "rocker"],
                       "rocker_pin": ["frame", "rocker"]},
              "joints": [{"bodies": ["crank", "frame"], "type": "revolute"},
                         {"bodies": ["coupler", "crank"], "type": "revolute"},
                         {"bodies": ["rocker", "frame"], "type": "revolute"},
                         {"bodies": ["coupler", "rocker"], "type": ["revolute", "cylindrical"]}]}

    def build(r: Ref):
        r.ins("frame", 0, 0, 0, ground=True)
        r.ins("shaft", 0, 0, -10)
        r.rev("shaft:cyl(d=10)", "frame:cyl(d=10)")
        r.ins("crank", 0, 0, 8, 90)
        r.fixed("crank:cyl(d=10)", "shaft:cyl(d=10)")
        r.ins("crank_pin", *B0, 8)
        r.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        r.ins("coupler", *B0, 14, ang(B0, C0))
        r.rev("coupler:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        r.ins("coupler_pin", *C0, 14)
        r.fixed("coupler_pin:cyl(d=6)", "coupler:cyl(d=6, near=[70,0])")
        r.ins("rocker_pin", *D, 0)
        r.fixed("rocker_pin:cyl(d=6)", "frame:cyl(d=6)")
        r.ins("rocker", *D, 20, ang(D, C0))
        r.rev("rocker:cyl(d=6, near=[0,0])", "rocker_pin:cyl(d=6)")
        r.close("rocker:cyl(d=6, near=[50,0])", "coupler_pin:cyl(d=6)")
        return r.finish(1)

    return Case("crank_rocker", "crank_rocker_linkage", "曲柄摇杆四杆机构",
                "曲柄 20、连杆 70、摇杆 50、机架 80（Grashof 曲柄摇杆）；分三层叠放。",
                parts, roles, prompt, motion, rubric, build, {"swing": swing, "min_transmission": mu})


# ---- M2 偏置曲柄滑块

@case
def offset_slider_crank():
    r, L, e = 15, 60, 10
    stroke = slider_stroke(r, L, e)
    B0 = pol((0, 0), r, 90)
    P0 = (slider_x(r, L, e, 90), e)
    parts = {
        "os_frame": plate("机架板", [-30, 110], [-30, 40], 8, [(0, 0, 10)]),
        "os_shaft": pin("曲柄轴", 10, 24),
        "os_crank": link("曲柄", 15, 16, 6, [(0, 10), (15, 6)]),
        "os_pin12": pin("铰销", 6, 12),
        "os_rod": link("连杆", 60, 12, 6, [(0, 6), (60, 6)]),
        "os_slider": plate("滑块", [-12, 12], [-8, 8], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "os_frame", "shaft": "os_shaft", "crank": "os_crank", "crank_pin": "os_pin12",
             "rod": "os_rod", "wrist_pin": "os_pin12", "slider": "os_slider"}
    prompt = f"""装配偏置曲柄滑块机构。{COMMON}
- frame 固定，z∈[0,8]，曲柄轴承孔在原点。
- shaft 穿过轴承孔绕 Z 转动（z∈[-10,14]）；crank 的 10 mm 孔固定在 shaft 上（z∈[8,14]）。
- crank_pin 固定在 crank 的 6 mm 孔里（z∈[8,20]）；rod 一端套在 crank_pin 上转动（z∈[14,20]）。
- wrist_pin 固定在 rod 另一端孔里（z∈[14,26]）；slider 的孔套在 wrist_pin 上转动（z∈[20,26]）。
- slider 只能沿 frame 的 X 方向移动，导路偏置：slider 孔心始终在 y=+{e} 的直线上，位于曲柄轴 +X 侧。
- 曲柄 {r}、连杆 {L}、偏距 {e}，曲柄转一圈滑块行程约 {stroke:.2f} mm。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank", "rod", "slider"],
                 strokes=[{"role": "slider", "axis": "x", "mm": r2(stroke)}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["shaft", "crank"], "rod": ["rod"], "slider": ["slider"]},
                 [("crank", "frame", R), ("rod", "crank", R), ("slider", "rod", R), ("slider", "frame", SP)],
                 flex={"crank_pin": ["crank", "rod"], "wrist_pin": ["rod", "slider"]})

    def build(r_: Ref):
        r_.ins("frame", 0, 0, 0, ground=True)
        r_.ins("shaft", 0, 0, -10)
        r_.rev("shaft:cyl(d=10)", "frame:cyl(d=10)")
        r_.ins("crank", 0, 0, 8, 90)
        r_.fixed("crank:cyl(d=10)", "shaft:cyl(d=10)")
        r_.ins("crank_pin", *B0, 8)
        r_.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        r_.ins("rod", *B0, 14, ang(B0, P0))
        r_.rev("rod:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        r_.ins("wrist_pin", *P0, 14)
        r_.fixed("wrist_pin:cyl(d=6)", "rod:cyl(d=6, near=[60,0])")
        r_.ins("slider", *P0, 20)
        r_.rev("slider:cyl(d=6)", "wrist_pin:cyl(d=6)")
        r_.plane("slider:plane(top)", "frame:plane(top)", e)
        return r_.finish(1)

    return Case("offset_slider_crank", "offset_slider_crank", "偏置曲柄滑块机构",
                "曲柄 15、连杆 60、偏距 10，滑块沿 X；行程由解算得出。",
                parts, roles, prompt, motion, rubric, build, {"stroke": stroke})


# ---- M3 双缸 180° 曲柄泵

@case
def twin_pump():
    r, L = 15, 60
    parts = {
        "tp_frame": plate("泵体板", [-40, 40], [-30, 110], 8, [(0, 0, 10)]),
        "tp_shaft": pin("曲轴", 10, 20),
        "tp_crank": link("曲柄", 15, 16, 6, [(0, 10), (15, 6)]),
        "tp_pin12": pin("铰销", 6, 12),
        "tp_rod": link("连杆", 60, 12, 6, [(0, 6), (60, 6)]),
        "tp_piston": plate("活塞", [-10, 10], [-12, 12], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "tp_frame", "shaft": "tp_shaft", "crank_a": "tp_crank", "crank_b": "tp_crank",
             "crank_pin_a": "tp_pin12", "crank_pin_b": "tp_pin12", "rod_a": "tp_rod", "rod_b": "tp_rod",
             "wrist_pin_a": "tp_pin12", "wrist_pin_b": "tp_pin12", "piston_a": "tp_piston", "piston_b": "tp_piston"}
    prompt = f"""装配双缸往复泵（两缸共用一根曲轴，两个曲柄相差 180°）。{COMMON}
- frame 固定，z∈[0,8]，曲轴承孔在原点。shaft 穿过轴承孔绕 Z 转动（z∈[-6,14]）。
- 前缸（板前 +Z 侧）：crank_a 的 10 mm 孔固定在 shaft 上（z∈[8,14]）；crank_pin_a 固定在 crank_a 的 6 mm 孔（z∈[8,20]）；
  rod_a 一端绕 crank_pin_a 转（z∈[14,20]）；wrist_pin_a 固定在 rod_a 另一端孔（z∈[14,26]）；
  piston_a 的孔套在 wrist_pin_a 上转动（z∈[20,26]）。
- 后缸（板后 −Z 侧）：crank_b 的 10 mm 孔固定在 shaft 上（z∈[-6,0]），曲柄方向与 crank_a 相反（相差 180°）；
  crank_pin_b 固定在 crank_b 的 6 mm 孔（z∈[-12,0]）；rod_b 一端绕 crank_pin_b 转（z∈[-12,-6]）；
  wrist_pin_b 固定在 rod_b 另一端孔（z∈[-18,-6]）；piston_b 的孔套在 wrist_pin_b 上转动（z∈[-18,-12]）。
- 两个活塞都只能沿 frame 的 Y 方向移动，孔心始终在 x=0 的直线上、曲轴 +Y 侧。
- 曲柄 {r}、连杆 {L}，每缸行程 {2 * r} mm，两活塞反相往复。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank_a", "piston_a", "piston_b"],
                 strokes=[{"role": "piston_a", "axis": "y", "mm": 2 * r}, {"role": "piston_b", "axis": "y", "mm": 2 * r}],
                 antiphase=[{"roles": ["piston_a", "piston_b"], "axis": "y"}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["shaft", "crank_a", "crank_b"], "rod_a": ["rod_a"],
                              "rod_b": ["rod_b"], "piston_a": ["piston_a"], "piston_b": ["piston_b"]},
                 [("crank", "frame", R), ("rod_a", "crank", R), ("rod_b", "crank", R),
                  ("piston_a", "rod_a", R), ("piston_b", "rod_b", R), ("piston_a", "frame", SP), ("piston_b", "frame", SP)],
                 flex={"crank_pin_a": ["crank", "rod_a"], "crank_pin_b": ["crank", "rod_b"],
                       "wrist_pin_a": ["rod_a", "piston_a"], "wrist_pin_b": ["rod_b", "piston_b"]})

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("shaft", 0, 0, -6)
        q.rev("shaft:cyl(d=10)", "frame:cyl(d=10)")
        for s, zc, zp, zr, zw, sign in (("a", 8, 8, 14, 14, 1), ("b", -6, -12, -12, -18, -1)):
            Bp = (0, r * sign)
            yp = Bp[1] + L
            q.ins(f"crank_{s}", 0, 0, zc, 90 * sign)
            q.fixed(f"crank_{s}:cyl(d=10)", "shaft:cyl(d=10)")
            q.ins(f"crank_pin_{s}", *Bp, zp)
            q.fixed(f"crank_pin_{s}:cyl(d=6)", f"crank_{s}:cyl(d=6)")
            q.ins(f"rod_{s}", *Bp, zr, 90)
            q.rev(f"rod_{s}:cyl(d=6, near=[0,0])", f"crank_pin_{s}:cyl(d=6)")
            q.ins(f"wrist_pin_{s}", 0, yp, zw)
            q.fixed(f"wrist_pin_{s}:cyl(d=6)", f"rod_{s}:cyl(d=6, near=[60,0])")
            q.ins(f"piston_{s}", 0, yp, zc + 12 if s == "a" else zw)
            q.rev(f"piston_{s}:cyl(d=6)", f"wrist_pin_{s}:cyl(d=6)")
            q.plane(f"piston_{s}:plane(right)", "frame:plane(right)", 0)
        return q.finish(1)

    return Case("twin_pump", "twin_cylinder_pump", "双缸 180° 曲柄往复泵",
                "两组曲柄滑块共用曲轴、曲柄相差 180°，板前板后各一缸；检查两缸行程与反相。",
                parts, roles, prompt, motion, rubric, build, {"stroke": 2 * r})


# ---- M4 抽油机（齿轮减速 + 曲柄摇杆游梁）

@case
def pumpjack():
    A, D, a, b, c = (0, 0), (-30, 90), 20, 90, 50
    side = 1 if circ(pol(A, a, 90), D, b, c, 1)[0] > D[0] else -1
    swing, mu = fourbar(A, D, a, b, c, side)
    B0 = pol(A, a, 90)
    C0 = circ(B0, D, b, c, side)
    parts = {
        "pj_frame": plate("机架板", [-80, 40], [-30, 110], 8, [(-50, 0, 10), (0, 0, 10), (-30, 90, 6)]),
        "pj_motor_shaft": pin("电机轴", 10, 26),
        "pj_crank_shaft": pin("曲柄轴", 10, 32),
        "pj_pinion": gear(10, 2.5, 8, 10),
        "pj_bull_gear": gear(30, 2.5, 8, 10),
        "pj_crank": link("曲柄", 20, 16, 6, [(0, 10), (20, 6)]),
        "pj_pin12": pin("铰销", 6, 12),
        "pj_pitman": link("连杆", 90, 12, 6, [(0, 6), (90, 6)]),
        "pj_beam": link("游梁", 130, 14, 6, [(0, 6), (50, 6), (130, 6)]),
        "pj_pivot_pin": pin("游梁支销", 6, 34),
    }
    roles = {"frame": "pj_frame", "motor_shaft": "pj_motor_shaft", "pinion": "pj_pinion",
             "crank_shaft": "pj_crank_shaft", "bull_gear": "pj_bull_gear", "crank": "pj_crank",
             "crank_pin": "pj_pin12", "pitman": "pj_pitman", "pitman_pin": "pj_pin12", "beam": "pj_beam",
             "pivot_pin": "pj_pivot_pin"}
    prompt = f"""装配游梁式抽油机（齿轮减速带曲柄，曲柄摇杆机构带游梁摆动）。{COMMON}
- frame 固定，z∈[0,8]。轴承孔：电机轴 (-50,0)、曲柄轴 (0,0)；游梁支孔 (-30,90)。
- motor_shaft 穿过 (-50,0) 孔绕 Z 转动（z∈[-10,16]），10 齿 pinion 固定在其上（z∈[8,16]）。
- crank_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-10,22]），30 齿 bull_gear（z∈[8,16]）与 crank 的 10 mm 孔（z∈[16,22]）
  都固定在 crank_shaft 上。pinion 与 bull_gear 外啮合（模数 2.5，中心距 50），减速比 3。
- crank_pin 固定在 crank 的 6 mm 孔（z∈[16,28]）；pitman 一端绕 crank_pin 转（z∈[22,28]）；
  pitman_pin 固定在 pitman 另一端孔（z∈[22,34]）。
- pivot_pin 固定在 frame 的 (-30,90) 孔里（z∈[0,34]）；beam 的 X=50 孔套在 pivot_pin 上转动（z∈[28,34]），
  beam 的 X=0 孔套在 pitman_pin 上，X=130 一端（驴头）伸向 pivot_pin 的另一侧。
  曲柄 20、连杆 90、游梁后臂 50、机架 {dist(A, D):.1f}；beam 的 X=0 端在 pivot_pin 的 +X 侧。
- 曲柄整周转动时游梁往复摆动约 {swing:.1f}°。{GEAR_TIP}
对 motor_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("motor_shaft", ["frame"], ["pinion", "crank", "pitman", "beam"],
                 ratios=[{"role": "crank", "ratio": 3, "same_direction": False}],
                 swings=[{"role": "beam", "deg": r2(swing)}])
    rubric = rub("frame", 1, {"frame": ["frame"], "motor": ["motor_shaft", "pinion"],
                              "crank": ["crank_shaft", "bull_gear", "crank"], "pitman": ["pitman"], "beam": ["beam"]},
                 [("motor", "frame", R), ("crank", "frame", R), ("pitman", "crank", R), ("beam", "frame", R),
                  ("pitman", "beam", RC)],
                 flex={"crank_pin": ["crank", "pitman"], "pitman_pin": ["pitman", "beam"], "pivot_pin": ["frame", "beam"]},
                 couplings=[gc("pinion", "bull_gear", 10, 30, 2.5)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("motor_shaft", -50, 0, -10)
        q.rev("motor_shaft:cyl(d=10)", "frame:cyl(d=10, near=[-50,0])")
        q.ins("pinion", -50, 0, 8)
        q.fixed("pinion:cyl(i=0)", "motor_shaft:cyl(d=10)")
        q.ins("crank_shaft", 0, 0, -10)
        q.rev("crank_shaft:cyl(d=10)", "frame:cyl(d=10, near=[0,0])")
        q.ins("bull_gear", 0, 0, 8)
        q.fixed("bull_gear:cyl(i=0)", "crank_shaft:cyl(d=10)")
        q.ins("crank", 0, 0, 16, 90)
        q.fixed("crank:cyl(d=10)", "crank_shaft:cyl(d=10)")
        q.mesh("pinion", "bull_gear", 10, 30, 2.5)
        q.ins("crank_pin", *B0, 16)
        q.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        q.ins("pitman", *B0, 22, ang(B0, C0))
        q.rev("pitman:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        q.ins("pitman_pin", *C0, 22)
        q.fixed("pitman_pin:cyl(d=6)", "pitman:cyl(d=6, near=[90,0])")
        q.ins("pivot_pin", *D, 0)
        q.fixed("pivot_pin:cyl(d=6)", "frame:cyl(d=6)")
        q.ins("beam", *C0, 28, ang(C0, D))
        q.rev("beam:cyl(d=6, near=[50,0])", "pivot_pin:cyl(d=6)")
        q.close("beam:cyl(d=6, near=[0,0])", "pitman_pin:cyl(d=6)")
        return q.finish(1)

    return Case("pumpjack", "pumpjack_drive", "游梁式抽油机传动",
                "10/30 齿减速带曲柄，曲柄 20、连杆 90、游梁后臂 50 的曲柄摇杆带游梁摆动。",
                parts, roles, prompt, motion, rubric, build, {"swing": swing, "min_transmission": mu})


# ---- M5 雨刮联动（曲柄摇杆 + 平行四边形第二刮臂）

@case
def wiper_linkage():
    A, D1, D2, a, b, c = (0, 0), (60, 0), (160, 0), 15, 50, 40
    swing, mu = fourbar(A, D1, a, b, c, 1)
    B0 = pol(A, a, 90)
    C0 = circ(B0, D1, b, c, 1)
    phi = ang(D1, C0)
    E1, E2 = pol(D1, 25, phi), pol(D2, 25, phi)
    parts = {
        "wp_frame": plate("机架板", [-30, 190], [-30, 40], 8, [(0, 0, 10), (60, 0, 6), (160, 0, 6)]),
        "wp_motor_shaft": pin("电机轴", 10, 24),
        "wp_crank": link("曲柄", 15, 16, 6, [(0, 10), (15, 6)]),
        "wp_pin12": pin("铰销", 6, 12),
        "wp_pivot_pin": pin("刮臂支销", 6, 26),
        "wp_link1": link("驱动连杆", 50, 12, 6, [(0, 6), (50, 6)]),
        "wp_arm1": link("主刮臂", 80, 12, 6, [(0, 6), (25, 6), (40, 6)]),
        "wp_arm2": link("副刮臂", 80, 12, 6, [(0, 6), (25, 6)]),
        "wp_link2": link("联动杆", 100, 12, 6, [(0, 6), (100, 6)]),
    }
    roles = {"frame": "wp_frame", "motor_shaft": "wp_motor_shaft", "crank": "wp_crank", "crank_pin": "wp_pin12",
             "link1": "wp_link1", "link1_pin": "wp_pin12", "arm1": "wp_arm1", "arm1_pin": "wp_pivot_pin",
             "arm2": "wp_arm2", "arm2_pin": "wp_pivot_pin", "link2": "wp_link2",
             "link2_pin_a": "wp_pin12", "link2_pin_b": "wp_pin12"}
    prompt = f"""装配双刮臂雨刮联动机构。{COMMON}
- frame 固定，z∈[0,8]。电机轴承孔 (0,0)，主刮臂支孔 (60,0)，副刮臂支孔 (160,0)。
- motor_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-10,14]）；crank 的 10 mm 孔固定在 motor_shaft 上（z∈[8,14]）。
- crank_pin 固定在 crank 的 6 mm 孔（z∈[8,20]）；link1 一端绕 crank_pin 转（z∈[14,20]）；
  link1_pin 固定在 link1 另一端孔（z∈[14,26]）。
- arm1_pin、arm2_pin 分别固定在 frame 的 (60,0)、(160,0) 孔里（z∈[0,26]）；arm1、arm2 的原点孔分别套在上面转动（z∈[20,26]）。
  arm1 的 X=40 孔套在 link1_pin 上：曲柄 15、连杆 50、摇杆 40、机架 60 的曲柄摇杆，刮臂朝 +Y 侧。
- link2_pin_a、link2_pin_b 分别固定在 link2 两端孔里（z∈[20,32]），link2 在 z∈[26,32]；
  link2_pin_a 插在 arm1 的 X=25 孔里转动，link2_pin_b 插在 arm2 的 X=25 孔里转动，两刮臂组成平行四边形、始终平行同摆。
- 曲柄整周转动时两刮臂各摆动约 {swing:.1f}°。
对 motor_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("motor_shaft", ["frame"], ["crank", "link1", "arm1", "arm2", "link2"],
                 swings=[{"role": "arm1", "deg": r2(swing)}, {"role": "arm2", "deg": r2(swing)}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["motor_shaft", "crank"], "link1": ["link1"],
                              "arm1": ["arm1"], "arm2": ["arm2"], "link2": ["link2"]},
                 [("crank", "frame", R), ("link1", "crank", R), ("arm1", "frame", R), ("arm1", "link1", RC),
                  ("arm2", "frame", R), ("link2", "arm1", R), ("link2", "arm2", RC)],
                 flex={"crank_pin": ["crank", "link1"], "link1_pin": ["link1", "arm1"], "arm1_pin": ["frame", "arm1"],
                       "arm2_pin": ["frame", "arm2"], "link2_pin_a": ["link2", "arm1"], "link2_pin_b": ["link2", "arm2"]})

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("motor_shaft", 0, 0, -10)
        q.rev("motor_shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("crank", 0, 0, 8, 90)
        q.fixed("crank:cyl(d=10)", "motor_shaft:cyl(d=10)")
        q.ins("crank_pin", *B0, 8)
        q.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        q.ins("link1", *B0, 14, ang(B0, C0))
        q.rev("link1:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        q.ins("link1_pin", *C0, 14)
        q.fixed("link1_pin:cyl(d=6)", "link1:cyl(d=6, near=[50,0])")
        q.ins("arm1_pin", *D1, 0)
        q.fixed("arm1_pin:cyl(d=6)", "frame:cyl(d=6, near=[60,0])")
        q.ins("arm1", *D1, 20, phi)
        q.rev("arm1:cyl(d=6, near=[0,0])", "arm1_pin:cyl(d=6)")
        q.close("arm1:cyl(d=6, near=[40,0])", "link1_pin:cyl(d=6)")
        q.ins("link2_pin_a", *E1, 20)
        q.rev("link2_pin_a:cyl(d=6)", "arm1:cyl(d=6, near=[25,0])")
        q.ins("link2", *E1, 26)
        q.fixed("link2:cyl(d=6, near=[0,0])", "link2_pin_a:cyl(d=6)")
        q.ins("arm2_pin", *D2, 0)
        q.fixed("arm2_pin:cyl(d=6)", "frame:cyl(d=6, near=[160,0])")
        q.ins("arm2", *D2, 20, phi)
        q.rev("arm2:cyl(d=6, near=[0,0])", "arm2_pin:cyl(d=6)")
        q.ins("link2_pin_b", *E2, 20)
        q.fixed("link2_pin_b:cyl(d=6)", "link2:cyl(d=6, near=[100,0])")
        q.close("arm2:cyl(d=6, near=[25,0])", "link2_pin_b:cyl(d=6)")
        return q.finish(1)

    return Case("wiper_linkage", "windshield_wiper_linkage", "双刮臂雨刮联动",
                "曲柄摇杆驱动主刮臂，联动杆与两刮臂组成平行四边形带副刮臂同摆。",
                parts, roles, prompt, motion, rubric, build, {"swing": swing, "min_transmission": mu})


# ---- M6 蒸汽梁机（曲柄摇杆游梁 + 另一端带活塞）

@case
def beam_engine():
    A, D, a, b, c = (60, 0), (0, 120), 20, 110, 60
    B0 = pol(A, a, 90)
    side = 1 if circ(B0, D, b, c, 1)[0] > 0 else -1
    swing, mu = fourbar(A, D, a, b, c, side)

    def state(theta):
        B = pol(A, a, theta)
        C = circ(B, D, b, c, side)
        E = (2 * D[0] - C[0], 2 * D[1] - C[1])
        P = (-60, E[1] - math.sqrt(60 ** 2 - (E[0] + 60) ** 2))
        return B, C, E, P

    ys = [state(i / 4)[3][1] for i in range(1440)]
    stroke = max(ys) - min(ys)
    B0, C0, E0, P0 = state(90)
    parts = {
        "be_frame": plate("机架板", [-90, 90], [-30, 140], 8, [(60, 0, 10), (0, 120, 6)]),
        "be_shaft": pin("曲柄轴", 10, 24),
        "be_crank": link("曲柄", 20, 16, 6, [(0, 10), (20, 6)]),
        "be_pin12": pin("铰销", 6, 12),
        "be_conrod": link("连杆", 110, 12, 6, [(0, 6), (110, 6)]),
        "be_beam": link("游梁", 120, 14, 6, [(0, 6), (60, 6), (120, 6)]),
        "be_pivot_pin": pin("游梁支销", 6, 26),
        "be_piston_rod": link("活塞杆", 60, 10, 6, [(0, 6), (60, 6)]),
        "be_piston": plate("活塞", [-10, 10], [-12, 12], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "be_frame", "shaft": "be_shaft", "crank": "be_crank", "crank_pin": "be_pin12",
             "conrod": "be_conrod", "conrod_pin": "be_pin12", "beam": "be_beam", "pivot_pin": "be_pivot_pin",
             "e_pin": "be_pin12", "piston_rod": "be_piston_rod", "p_pin": "be_pin12", "piston": "be_piston"}
    prompt = f"""装配蒸汽梁机（曲柄—连杆—游梁—活塞杆—活塞）。{COMMON}
- frame 固定，z∈[0,8]。曲柄轴承孔 (60,0)，游梁支孔 (0,120)。
- shaft 穿过 (60,0) 孔绕 Z 转动（z∈[-10,14]）；crank 的 10 mm 孔固定在 shaft 上（z∈[8,14]）。
- crank_pin 固定在 crank 的 6 mm 孔（z∈[8,20]）；conrod 一端绕 crank_pin 转（z∈[14,20]）；
  conrod_pin 固定在 conrod 另一端孔（z∈[14,26]）。
- pivot_pin 固定在 frame 的 (0,120) 孔（z∈[0,26]）；beam 的 X=60 孔套在 pivot_pin 上转动（z∈[20,26]），
  beam 的 X=120 孔套在 conrod_pin 上（这一端在 +X 侧，靠曲柄），X=0 端在 -X 侧。
- e_pin 固定在 piston_rod 的原点孔（z∈[14,26]），插在 beam 的 X=0 孔里转动；piston_rod 在 z∈[14,20]，
  另一端孔里固定 p_pin（z∈[8,20]）；piston 的孔套在 p_pin 上转动（z∈[8,14]）。
- piston 只能沿 frame 的 Y 方向移动，孔心始终在 x=-60 的直线上，位于游梁下方。
- 曲柄整周转动时游梁摆动约 {swing:.1f}°，活塞行程约 {stroke:.1f} mm。
对 shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("shaft", ["frame"], ["crank", "conrod", "beam", "piston"],
                 swings=[{"role": "beam", "deg": r2(swing)}], strokes=[{"role": "piston", "axis": "y", "mm": r2(stroke)}])
    rubric = rub("frame", 1, {"frame": ["frame"], "crank": ["shaft", "crank"], "conrod": ["conrod"], "beam": ["beam"],
                              "piston_rod": ["piston_rod"], "piston": ["piston"]},
                 [("crank", "frame", R), ("conrod", "crank", R), ("beam", "frame", R), ("conrod", "beam", RC),
                  ("piston_rod", "beam", R), ("piston", "piston_rod", R), ("piston", "frame", SP)],
                 flex={"crank_pin": ["crank", "conrod"], "conrod_pin": ["conrod", "beam"], "pivot_pin": ["frame", "beam"],
                       "e_pin": ["beam", "piston_rod"], "p_pin": ["piston_rod", "piston"]})

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("shaft", *A, -10)
        q.rev("shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("crank", *A, 8, 90)
        q.fixed("crank:cyl(d=10)", "shaft:cyl(d=10)")
        q.ins("crank_pin", *B0, 8)
        q.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        q.ins("conrod", *B0, 14, ang(B0, C0))
        q.rev("conrod:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        q.ins("conrod_pin", *C0, 14)
        q.fixed("conrod_pin:cyl(d=6)", "conrod:cyl(d=6, near=[110,0])")
        q.ins("pivot_pin", *D, 0)
        q.fixed("pivot_pin:cyl(d=6)", "frame:cyl(d=6)")
        q.ins("beam", *E0, 20, ang(E0, C0))
        q.rev("beam:cyl(d=6, near=[60,0])", "pivot_pin:cyl(d=6)")
        q.close("beam:cyl(d=6, near=[120,0])", "conrod_pin:cyl(d=6)")
        q.ins("e_pin", *E0, 14)
        q.rev("e_pin:cyl(d=6)", "beam:cyl(d=6, near=[0,0])")
        q.ins("piston_rod", *E0, 14, ang(E0, P0))
        q.fixed("piston_rod:cyl(d=6, near=[0,0])", "e_pin:cyl(d=6)")
        q.ins("p_pin", *P0, 8)
        q.fixed("p_pin:cyl(d=6)", "piston_rod:cyl(d=6, near=[60,0])")
        q.ins("piston", *P0, 8)
        q.rev("piston:cyl(d=6)", "p_pin:cyl(d=6)")
        q.plane("piston:plane(right)", "frame:plane(right)", -60)
        return q.finish(1)

    return Case("beam_engine", "beam_engine", "蒸汽梁机",
                "曲柄 20、连杆 110、半游梁 60 的曲柄摇杆，游梁另一端经活塞杆带活塞沿 Y 往复。",
                parts, roles, prompt, motion, rubric, build, {"swing": swing, "stroke": stroke, "min_transmission": mu})


# ---- M7 钟表跨轮（时分针 12:1）

@case
def clock_motion():
    parts = {
        "ck_frame": plate("机芯夹板", [-40, 60], [-40, 40], 8, [(0, 0, 4), (30, 0, 4)]),
        "ck_minute_arbor": pin("分轮轴", 4, 50),
        "ck_wheel_arbor": pin("跨轮轴", 4, 26),
        "ck_cannon_pinion": gear(10, 1.5, 6, 4),
        "ck_minute_wheel": gear(30, 1.5, 6, 4),
        "ck_minute_pinion": gear(8, 1.5, 6, 4),
        "ck_hour_wheel": gear(32, 1.5, 6, 8),
        "ck_hour_tube": disk("时轮管", 8, 16, 4),
        "ck_minute_hand": link("分针", 50, 8, 2, [(0, 4)]),
        "ck_hour_hand": link("时针", 35, 12, 2, [(0, 8)]),
    }
    roles = {"frame": "ck_frame", "minute_arbor": "ck_minute_arbor", "cannon_pinion": "ck_cannon_pinion",
             "minute_hand": "ck_minute_hand", "wheel_arbor": "ck_wheel_arbor", "minute_wheel": "ck_minute_wheel",
             "minute_pinion": "ck_minute_pinion", "hour_tube": "ck_hour_tube", "hour_wheel": "ck_hour_wheel",
             "hour_hand": "ck_hour_hand"}
    prompt = f"""装配钟表跨轮机构（分针转 12 圈时针转 1 圈）。{COMMON}
- frame 固定，z∈[0,8]，分轮轴孔 (0,0)，跨轮轴孔 (30,0)，两孔直径都是 4 mm。
- minute_arbor 穿过 (0,0) 孔绕 Z 转动（z∈[-10,40]）；10 齿 cannon_pinion（z∈[8,14]）与 minute_hand（z∈[34,36]）
  都固定在 minute_arbor 上。
- wheel_arbor 穿过 (30,0) 孔绕 Z 转动（z∈[-6,20]）；30 齿 minute_wheel（z∈[8,14]）与 8 齿 minute_pinion（z∈[14,20]）
  都固定在 wheel_arbor 上。cannon_pinion 与 minute_wheel 外啮合（模数 1.5，中心距 30）。
- hour_tube 套在 minute_arbor 上绕 Z 转动（z∈[14,30]）；32 齿 hour_wheel（z∈[14,20]，轴孔 8 mm）与 hour_hand
  （z∈[28,30]，孔 8 mm）都固定在 hour_tube 上。minute_pinion 与 hour_wheel 外啮合（中心距 30）。
- 传动比：minute_wheel 是分轮转速的 1/3、反向；hour_wheel 是分轮的 1/12、同向。{GEAR_TIP}
对 minute_arbor 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("minute_arbor", ["frame"], ["cannon_pinion", "minute_wheel", "hour_hand"],
                 ratios=[{"role": "minute_wheel", "ratio": 3, "same_direction": False},
                         {"role": "hour_hand", "ratio": 12, "same_direction": True}])
    rubric = rub("frame", 1, {"frame": ["frame"], "minute": ["minute_arbor", "cannon_pinion", "minute_hand"],
                              "wheel": ["wheel_arbor", "minute_wheel", "minute_pinion"],
                              "hour": ["hour_tube", "hour_wheel", "hour_hand"]},
                 [("minute", "frame", R), ("wheel", "frame", R), ("hour", "minute", R)],
                 couplings=[gc("cannon_pinion", "minute_wheel", 10, 30, 1.5), gc("minute_pinion", "hour_wheel", 8, 32, 1.5)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("minute_arbor", 0, 0, -10)
        q.rev("minute_arbor:cyl(d=4)", "frame:cyl(d=4, near=[0,0])")
        q.ins("cannon_pinion", 0, 0, 8)
        q.fixed("cannon_pinion:cyl(i=0)", "minute_arbor:cyl(d=4)")
        q.ins("minute_hand", 0, 0, 34, 90)
        q.fixed("minute_hand:cyl(d=4)", "minute_arbor:cyl(d=4)")
        q.ins("wheel_arbor", 30, 0, -6)
        q.rev("wheel_arbor:cyl(d=4)", "frame:cyl(d=4, near=[30,0])")
        q.ins("minute_wheel", 30, 0, 8)
        q.fixed("minute_wheel:cyl(i=0)", "wheel_arbor:cyl(d=4)")
        q.ins("minute_pinion", 30, 0, 14)
        q.fixed("minute_pinion:cyl(i=0)", "wheel_arbor:cyl(d=4)")
        q.ins("hour_tube", 0, 0, 14)
        q.rev("hour_tube:cyl(d=4)", "minute_arbor:cyl(d=4)")
        q.ins("hour_wheel", 0, 0, 14)
        q.fixed("hour_wheel:cyl(i=0)", "hour_tube:cyl(d=8)")
        q.ins("hour_hand", 0, 0, 28, 180)
        q.fixed("hour_hand:cyl(d=8)", "hour_tube:cyl(d=8)")
        q.mesh("cannon_pinion", "minute_wheel", 10, 30, 1.5)
        q.mesh("minute_pinion", "hour_wheel", 8, 32, 1.5)
        return q.finish(1)

    return Case("clock_motion", "clock_motion_works", "钟表跨轮（12:1 时分针）",
                "10/30 与 8/32 两级外啮合，时轮管套在分轮轴上；两针分层。",
                parts, roles, prompt, motion, rubric, build)


# ---- M8 手摇绞盘

@case
def hand_winch():
    parts = {
        "hw_frame": plate("支架板", [-30, 80], [-40, 40], 8, [(0, 0, 10), (48, 0, 10)]),
        "hw_handle_shaft": pin("摇把轴", 10, 30),
        "hw_drum_shaft": pin("卷筒轴", 10, 56),
        "hw_pinion": gear(12, 2, 8, 10),
        "hw_gear": gear(36, 2, 8, 10),
        "hw_handle": link("摇把", 40, 14, 6, [(0, 10), (40, 6)]),
        "hw_knob": pin("手柄", 6, 30),
        "hw_drum": disk("卷筒", 40, 40, 10),
    }
    roles = {"frame": "hw_frame", "handle_shaft": "hw_handle_shaft", "pinion": "hw_pinion", "handle": "hw_handle",
             "knob": "hw_knob", "drum_shaft": "hw_drum_shaft", "gear": "hw_gear", "drum": "hw_drum"}
    prompt = f"""装配手摇绞盘（摇把经 12/36 齿轮减速带卷筒）。{COMMON}
- frame 固定，z∈[0,8]，轴承孔 (0,0) 与 (48,0)。
- handle_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-14,16]）；12 齿 pinion（z∈[8,16]）固定在其上；
  板后 handle 的 10 mm 孔固定在 handle_shaft 上（z∈[-14,-8]），knob 固定在 handle 的 6 mm 孔里、向 −Z 伸出（z∈[-44,-14]）。
- drum_shaft 穿过 (48,0) 孔绕 Z 转动（z∈[0,56]）；36 齿 gear（z∈[8,16]）与 drum（z∈[16,56]）都固定在 drum_shaft 上。
- pinion 与 gear 外啮合（模数 2，中心距 48），卷筒转速为摇把的 1/3、反向。{GEAR_TIP}
对 handle_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("handle_shaft", ["frame"], ["pinion", "handle", "drum"],
                 ratios=[{"role": "drum", "ratio": 3, "same_direction": False}])
    rubric = rub("frame", 1, {"frame": ["frame"], "handle": ["handle_shaft", "pinion", "handle", "knob"],
                              "drum": ["drum_shaft", "gear", "drum"]},
                 [("handle", "frame", R), ("drum", "frame", R)], couplings=[gc("pinion", "gear", 12, 36, 2)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("handle_shaft", 0, 0, -14)
        q.rev("handle_shaft:cyl(d=10)", "frame:cyl(d=10, near=[0,0])")
        q.ins("pinion", 0, 0, 8)
        q.fixed("pinion:cyl(i=0)", "handle_shaft:cyl(d=10)")
        q.ins("handle", 0, 0, -14)
        q.fixed("handle:cyl(d=10)", "handle_shaft:cyl(d=10)")
        q.ins("knob", 40, 0, -44)
        q.fixed("knob:cyl(d=6)", "handle:cyl(d=6)")
        q.ins("drum_shaft", 48, 0, 0)
        q.rev("drum_shaft:cyl(d=10)", "frame:cyl(d=10, near=[48,0])")
        q.ins("gear", 48, 0, 8)
        q.fixed("gear:cyl(i=0)", "drum_shaft:cyl(d=10)")
        q.ins("drum", 48, 0, 16)
        q.fixed("drum:cyl(d=10)", "drum_shaft:cyl(d=10)")
        q.mesh("pinion", "gear", 12, 36, 2)
        return q.finish(1)

    return Case("hand_winch", "hand_winch", "手摇绞盘", "摇把轴 12 齿带卷筒轴 36 齿，单级外啮合减速 3。",
                parts, roles, prompt, motion, rubric, build)


# ---- M9 内齿圈转台

@case
def turntable():
    parts = {
        "tt_frame": plate("底座板", [-70, 70], [-70, 70], 8, [(0, 0, 10), (36, 0, 10)]),
        "tt_spindle": pin("中心立轴", 10, 22),
        "tt_pinion_shaft": pin("小齿轮轴", 10, 26),
        "tt_pinion": gear(12, 2, 8, 10),
        "tt_ring": ring(48, 2, 8, 110),
        "tt_table": disk("转台盘", 110, 6, 10),
    }
    roles = {"frame": "tt_frame", "spindle": "tt_spindle", "pinion_shaft": "tt_pinion_shaft", "pinion": "tt_pinion",
             "ring": "tt_ring", "table": "tt_table"}
    prompt = f"""装配内齿圈驱动的转台。{COMMON}
- frame 固定，z∈[0,8]，孔 (0,0) 与 (36,0)，直径都是 10 mm。spindle 固定在 (0,0) 孔里（z∈[0,22]）。
- pinion_shaft 穿过 (36,0) 孔绕 Z 转动（z∈[-10,16]），12 齿 pinion 固定在其上（z∈[8,16]）。
- table 的中心孔套在 spindle 上绕 Z 转动（z∈[16,22]）；48 齿内齿圈 ring 固定在 table 下面（z∈[8,16]），与 table 同轴。
- pinion 与 ring 内啮合（模数 2，中心距 36），转台转速为小齿轮的 1/4、同向。{GEAR_TIP}
对 pinion_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("pinion_shaft", ["frame"], ["pinion", "table"],
                 ratios=[{"role": "table", "ratio": 4, "same_direction": True}])
    rubric = rub("frame", 1, {"frame": ["frame", "spindle"], "drive": ["pinion_shaft", "pinion"], "table": ["table", "ring"]},
                 [("drive", "frame", R), ("table", "frame", R)], couplings=[gc("pinion", "ring", 12, 48, 2, internal=True)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("spindle", 0, 0, 0)
        q.fixed("spindle:cyl(d=10)", "frame:cyl(d=10, near=[0,0])")
        q.ins("pinion_shaft", 36, 0, -10)
        q.rev("pinion_shaft:cyl(d=10)", "frame:cyl(d=10, near=[36,0])")
        q.ins("pinion", 36, 0, 8)
        q.fixed("pinion:cyl(i=0)", "pinion_shaft:cyl(d=10)")
        q.ins("table", 0, 0, 16)
        q.rev("table:cyl(d=10)", "spindle:cyl(d=10)")
        q.ins("ring", 0, 0, 8)
        q.fixed("ring:cyl(i=0)", "table:cyl(d=10)")
        q.mesh("pinion", "ring", 12, 48, 2, internal=True)
        return q.finish(1)

    return Case("turntable", "turntable_ring_drive", "内齿圈转台", "12 齿小齿轮内啮合 48 齿齿圈，齿圈与转台盘一体绕中心立轴转。",
                parts, roles, prompt, motion, rubric, build)


# ---- M10 星型轮系（行星轮轴固定、齿圈输出）

@case
def star_reducer():
    parts = {
        "sr_frame": plate("箱体板", [-60, 60], [-60, 60], 8, [(0, 0, 10), (30, 0, 6)]),
        "sr_shaft26": pin("输入轴", 10, 26),
        "sr_output_shaft": pin("输出轴", 10, 20),
        "sr_sun": gear(12, 2, 8, 10),
        "sr_planet_pin": pin("惰轮销", 6, 16),
        "sr_planet": gear(18, 2, 8, 6),
        "sr_ring": ring(48, 2, 8, 106),
        "sr_cover": disk("输出盘", 106, 6, 10),
    }
    roles = {"frame": "sr_frame", "input_shaft": "sr_shaft26", "sun": "sr_sun", "planet_pin": "sr_planet_pin",
             "planet": "sr_planet", "ring": "sr_ring", "cover": "sr_cover", "output_shaft": "sr_output_shaft"}
    prompt = f"""装配星型齿轮减速器（中间轮轴固定、内齿圈输出）。{COMMON}
- frame 固定，z∈[0,8]，主轴承孔 (0,0) 直径 10，中间轮销孔 (30,0) 直径 6。
- input_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-10,16]），12 齿 sun 固定在其上（z∈[8,16]）。
- planet_pin 固定在 (30,0) 孔里（z∈[0,16]）；18 齿 planet 套在 planet_pin 上转动（z∈[8,16]）。
- output_shaft 与主轴承孔同轴、绕 Z 转动（z∈[16,36]）；cover 的中心孔（z∈[16,22]）与 48 齿内齿圈 ring（z∈[8,16]）
  都固定在 output_shaft 上。
- sun 与 planet 外啮合、planet 与 ring 内啮合（模数 2，中心距都是 30）。输出转速为输入的 1/4、反向。{GEAR_TIP}
对 input_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("input_shaft", ["frame"], ["sun", "planet", "cover"],
                 ratios=[{"role": "cover", "ratio": 4, "same_direction": False}])
    rubric = rub("frame", 1, {"frame": ["frame"], "input": ["input_shaft", "sun"], "planet": ["planet"],
                              "output": ["output_shaft", "cover", "ring"]},
                 [("input", "frame", R), ("planet", "frame", R), ("output", "frame", R)],
                 flex={"planet_pin": ["frame", "planet"]},
                 couplings=[gc("sun", "planet", 12, 18, 2), gc("planet", "ring", 18, 48, 2, internal=True)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("input_shaft", 0, 0, -10)
        q.rev("input_shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("sun", 0, 0, 8)
        q.fixed("sun:cyl(i=0)", "input_shaft:cyl(d=10)")
        q.ins("planet_pin", 30, 0, 0)
        q.fixed("planet_pin:cyl(d=6)", "frame:cyl(d=6)")
        q.ins("planet", 30, 0, 8)
        q.rev("planet:cyl(i=0)", "planet_pin:cyl(d=6)")
        q.ins("output_shaft", 0, 0, 16)
        q.rev("output_shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("cover", 0, 0, 16)
        q.fixed("cover:cyl(d=10)", "output_shaft:cyl(d=10)")
        q.ins("ring", 0, 0, 8)
        q.fixed("ring:cyl(i=0)", "output_shaft:cyl(d=10)")
        q.mesh("sun", "planet", 12, 18, 2)
        q.mesh("planet", "ring", 18, 48, 2, internal=True)
        return q.finish(1)

    return Case("star_reducer", "star_gear_reducer", "星型齿轮减速器", "太阳轮 12、固定轴中间轮 18、齿圈 48 输出，i=-4。",
                parts, roles, prompt, motion, rubric, build)


# ---- M11 丝杠滑台

@case
def lead_screw_stage():
    lead = 2
    parts = {
        "ls_frame": plate("底板", [-30, 70], [-30, 30], 8, [(0, 0, 10), (40, 0, 8)]),
        "ls_screw": pin("丝杠", 10, 128),
        "ls_handwheel": disk("手轮", 50, 8, 10),
        "ls_guide_rod": pin("导杆", 8, 120),
        "ls_carriage": plate("滑台", [-15, 55], [-15, 15], 10, [(0, 0, 20), (40, 0, 8)]),
        "ls_nut": disk("螺母", 20, 10, 10),
    }
    roles = {"frame": "ls_frame", "screw": "ls_screw", "handwheel": "ls_handwheel", "guide_rod": "ls_guide_rod",
             "carriage": "ls_carriage", "nut": "ls_nut"}
    prompt = f"""装配竖直丝杠滑台。{COMMON}
- frame 固定，z∈[0,8]，丝杠孔 (0,0) 直径 10，导杆孔 (40,0) 直径 8。
- screw 穿过 (0,0) 孔绕 Z 转动（z∈[-16,112]）；handwheel 固定在 screw 下端（z∈[-16,-8]）。
- guide_rod 固定在 (40,0) 孔里（z∈[0,120]）。
- carriage 的 8 mm 孔套在 guide_rod 上，只能沿 Z 滑动（初始 z∈[50,60]）；nut 固定在 carriage 的 20 mm 孔里（z∈[50,60]），
  nut 与 screw 组成螺旋副，导程 {lead} mm：丝杠转一圈滑台沿 Z 移动 {lead} mm。
对 screw 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("screw", ["frame"], ["handwheel", "carriage"],
                 leads=[{"role": "carriage", "axis": "z", "mm_per_rev": lead}])
    rubric = rub("frame", 1, {"frame": ["frame", "guide_rod"], "screw": ["screw", "handwheel"], "carriage": ["carriage", "nut"]},
                 [("screw", "frame", R), ("carriage", "frame", ["cylindrical", "slider"]), ("carriage", "screw", "screw")])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("screw", 0, 0, -16)
        q.rev("screw:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("handwheel", 0, 0, -16)
        q.fixed("handwheel:cyl(d=10)", "screw:cyl(d=10)")
        q.ins("guide_rod", 40, 0, 0)
        q.fixed("guide_rod:cyl(d=8)", "frame:cyl(d=8)")
        q.ins("carriage", 0, 0, 50)
        q.call("joint", {"type": "cylindrical", "a": "carriage:cyl(d=8)", "b": "guide_rod:cyl(d=8)"})
        q.ins("nut", 0, 0, 50)
        q.fixed("nut:cyl(d=20)", "carriage:cyl(d=20)")
        q.call("screw_pair", {"screw": "screw", "nut": "nut", "lead": lead,
                              "screw_ref": "screw:cyl(d=10)", "nut_ref": "nut:cyl(d=10)"})
        return q.finish(1)

    return Case("lead_screw_stage", "lead_screw_stage", "竖直丝杠滑台", "丝杠导程 2，滑台由导杆导向、螺母与丝杠成螺旋副。",
                parts, roles, prompt, motion, rubric, build)


# ---- M12 齿轮驱动曲柄滑块

@case
def geared_slider_crank():
    r, L = 20, 70
    B0 = (0, r)
    P0 = (math.sqrt(L * L - r * r), 0)
    parts = {
        "gs_frame": plate("机架板", [-70, 110], [-45, 45], 8, [(-48, 0, 10), (0, 0, 10)]),
        "gs_motor_shaft": pin("电机轴", 10, 26),
        "gs_crank_shaft": pin("曲柄轴", 10, 32),
        "gs_pinion": gear(12, 2, 8, 10),
        "gs_gear": gear(36, 2, 8, 10),
        "gs_crank": link("曲柄", 20, 16, 6, [(0, 10), (20, 6)]),
        "gs_pin12": pin("铰销", 6, 12),
        "gs_rod": link("连杆", 70, 12, 6, [(0, 6), (70, 6)]),
        "gs_slider": plate("滑块", [-12, 12], [-10, 10], 6, [(0, 0, 6)]),
    }
    roles = {"frame": "gs_frame", "motor_shaft": "gs_motor_shaft", "pinion": "gs_pinion", "crank_shaft": "gs_crank_shaft",
             "gear": "gs_gear", "crank": "gs_crank", "crank_pin": "gs_pin12", "rod": "gs_rod", "wrist_pin": "gs_pin12",
             "slider": "gs_slider"}
    prompt = f"""装配齿轮减速驱动的曲柄滑块机构。{COMMON}
- frame 固定，z∈[0,8]，轴承孔 (-48,0) 与 (0,0)。
- motor_shaft 穿过 (-48,0) 孔绕 Z 转动（z∈[-10,16]），12 齿 pinion 固定在其上（z∈[8,16]）。
- crank_shaft 穿过 (0,0) 孔绕 Z 转动（z∈[-10,22]）；36 齿 gear（z∈[8,16]）与 crank 的 10 mm 孔（z∈[16,22]）都固定在其上。
  pinion 与 gear 外啮合（模数 2，中心距 48）。
- crank_pin 固定在 crank 的 6 mm 孔（z∈[16,28]）；rod 一端绕 crank_pin 转（z∈[22,28]）；
  wrist_pin 固定在 rod 另一端孔（z∈[22,34]）；slider 的孔套在 wrist_pin 上转动（z∈[28,34]）。
- slider 只能沿 frame 的 X 方向移动，孔心始终在 y=0 的直线上、曲柄轴 +X 侧。曲柄 {r}、连杆 {L}，行程 {2 * r} mm。{GEAR_TIP}
对 motor_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("motor_shaft", ["frame"], ["pinion", "crank", "rod", "slider"],
                 ratios=[{"role": "crank", "ratio": 3, "same_direction": False}],
                 strokes=[{"role": "slider", "axis": "x", "mm": 2 * r}])
    rubric = rub("frame", 1, {"frame": ["frame"], "motor": ["motor_shaft", "pinion"], "crank": ["crank_shaft", "gear", "crank"],
                              "rod": ["rod"], "slider": ["slider"]},
                 [("motor", "frame", R), ("crank", "frame", R), ("rod", "crank", R), ("slider", "rod", R),
                  ("slider", "frame", SP)],
                 flex={"crank_pin": ["crank", "rod"], "wrist_pin": ["rod", "slider"]},
                 couplings=[gc("pinion", "gear", 12, 36, 2)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("motor_shaft", -48, 0, -10)
        q.rev("motor_shaft:cyl(d=10)", "frame:cyl(d=10, near=[-48,0])")
        q.ins("pinion", -48, 0, 8)
        q.fixed("pinion:cyl(i=0)", "motor_shaft:cyl(d=10)")
        q.ins("crank_shaft", 0, 0, -10)
        q.rev("crank_shaft:cyl(d=10)", "frame:cyl(d=10, near=[0,0])")
        q.ins("gear", 0, 0, 8)
        q.fixed("gear:cyl(i=0)", "crank_shaft:cyl(d=10)")
        q.ins("crank", 0, 0, 16, 90)
        q.fixed("crank:cyl(d=10)", "crank_shaft:cyl(d=10)")
        q.mesh("pinion", "gear", 12, 36, 2)
        q.ins("crank_pin", *B0, 16)
        q.fixed("crank_pin:cyl(d=6)", "crank:cyl(d=6)")
        q.ins("rod", *B0, 22, ang(B0, P0))
        q.rev("rod:cyl(d=6, near=[0,0])", "crank_pin:cyl(d=6)")
        q.ins("wrist_pin", *P0, 22)
        q.fixed("wrist_pin:cyl(d=6)", "rod:cyl(d=6, near=[70,0])")
        q.ins("slider", *P0, 28)
        q.rev("slider:cyl(d=6)", "wrist_pin:cyl(d=6)")
        q.plane("slider:plane(top)", "frame:plane(top)", 0)
        return q.finish(1)

    return Case("geared_slider_crank", "gear_driven_slider_crank", "齿轮驱动曲柄滑块",
                "12/36 齿减速带曲柄 20、连杆 70 的对心曲柄滑块。",
                parts, roles, prompt, motion, rubric, build, {"stroke": 2 * r})


# ---- M13 行星架输出的单级行星减速器

@case
def planetary_carrier():
    parts = {
        "pc_frame": plate("箱体板", [-60, 60], [-60, 60], 8, [(0, 0, 10)]),
        "pc_input_shaft": pin("输入轴", 10, 26),
        "pc_output_shaft": pin("输出轴", 10, 20),
        "pc_sun": gear(12, 2, 8, 10),
        "pc_planet": gear(18, 2, 8, 6),
        "pc_ring": ring(48, 2, 8, 106),
        "pc_carrier": disk("行星架", 76, 6, 10, [(30, 0, 6)]),
        "pc_planet_pin": pin("行星轮销", 6, 14),
    }
    roles = {"frame": "pc_frame", "ring": "pc_ring", "input_shaft": "pc_input_shaft", "sun": "pc_sun",
             "output_shaft": "pc_output_shaft", "carrier": "pc_carrier", "planet_pin": "pc_planet_pin", "planet": "pc_planet"}
    prompt = f"""装配单级行星减速器（齿圈固定、行星架输出）。{COMMON}
- frame 固定，z∈[0,8]，主轴承孔 (0,0) 直径 10。48 齿内齿圈 ring 固定在 frame 上、与主轴承孔同轴（z∈[8,16]）。
- input_shaft 穿过主轴承孔绕 Z 转动（z∈[-10,16]），12 齿 sun 固定在其上（z∈[8,16]）。
- output_shaft 与主轴承孔同轴、绕 Z 转动（z∈[16,36]）；carrier 的中心孔固定在 output_shaft 上（z∈[16,22]）。
- planet_pin 固定在 carrier 的 6 mm 偏心孔里、向 −Z 伸出（z∈[8,22]）；18 齿 planet 套在 planet_pin 上转动（z∈[8,16]），
  同时与 sun 外啮合、与 ring 内啮合（模数 2，中心距 30）。
- 传动比 5：input_shaft 转 5 圈，carrier 同向转 1 圈。{GEAR_TIP}
对 input_shaft 做 check_motion（一整圈），确认无穿模后再保存。"""
    motion = mot("input_shaft", ["frame"], ["sun", "planet", "carrier"],
                 ratios=[{"role": "carrier", "ratio": 5, "same_direction": True}])
    rubric = rub("frame", 1, {"frame": ["frame", "ring"], "input": ["input_shaft", "sun"],
                              "carrier": ["output_shaft", "carrier"], "planet": ["planet"]},
                 [("input", "frame", R), ("carrier", "frame", R), ("planet", "carrier", R)],
                 flex={"planet_pin": ["carrier", "planet"]},
                 couplings=[gc("sun", "planet", 12, 18, 2), gc("planet", "ring", 18, 48, 2, internal=True)])

    def build(q: Ref):
        q.ins("frame", 0, 0, 0, ground=True)
        q.ins("ring", 0, 0, 8)
        q.fixed("ring:cyl(i=0)", "frame:cyl(d=10)")
        q.ins("input_shaft", 0, 0, -10)
        q.rev("input_shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("sun", 0, 0, 8)
        q.fixed("sun:cyl(i=0)", "input_shaft:cyl(d=10)")
        q.ins("output_shaft", 0, 0, 16)
        q.rev("output_shaft:cyl(d=10)", "frame:cyl(d=10)")
        q.ins("carrier", 0, 0, 16)
        q.fixed("carrier:cyl(d=10)", "output_shaft:cyl(d=10)")
        q.ins("planet_pin", 30, 0, 8)
        q.fixed("planet_pin:cyl(d=6)", "carrier:cyl(d=6)")
        q.ins("planet", 30, 0, 8)
        q.rev("planet:cyl(i=0)", "planet_pin:cyl(d=6)")
        q.mesh("sun", "planet", 12, 18, 2)
        q.mesh("planet", "ring", 18, 48, 2, internal=True)
        return q.finish(1)

    return Case("planetary_carrier", "epicyclic_carrier_output", "单级行星减速器（行星架输出）",
                "太阳轮 12 输入、齿圈 48 固定、行星架输出，i=5。", parts, roles, prompt, motion, rubric, build)


# ------------------------------------------------------------------ 多位姿测例
# 角度配合没有符号，评测在 ANGLE_VARIANTS 里按点位挑；这里 angle_deg 取转角绝对值折到 [0,180]。
# 点位都取在杆件 X 轴（孔心连线）上，与零件正装 / 翻面无关。

def fold(deg):
    v = abs(((deg + 180) % 360) - 180)
    assert 3 < v < 177, f"角度 {deg} 太接近 0/180"
    return r2(v)


def pt(role, local, xy):
    return {"role": role, "local": [r2(local[0]), r2(local[1]), 0], "expect": {"x": r2(xy[0]), "y": r2(xy[1])}}


def angle_item(a, b, deg, points):
    return {"type": "angle", "a": f"{a}:plane(top)", "b": f"{b}:plane(top)", "angle_deg": fold(deg), "points": points}


def poses_motion(frame, poses):
    return {"mode": "poses", "frame": frame, "expect_fixed": [frame], "tolerance_mm": 1.5, "max_interference": 0,
            "poses": poses}


# ---- P1 挖掘机臂（动臂油缸 + 斗杆 + 铲斗）

@case
def excavator_arm():
    O, Pb, Lb, Le, Ls, Lk = (0, 40), (110, 0), 160, 60, 110, 50

    def state(tb, ts, tk):
        E, S = pol(O, Le, tb), pol(O, Lb, tb)
        K = pol(S, Ls, tb + ts)
        T = pol(K, Lk, tb + ts + tk)
        return E, S, K, T

    poses = []
    for name, (tb, ts, tk) in (("dig", (40, -60, -40)), ("lift", (55, -80, -60)), ("reach", (70, -100, -30))):
        E, S, K, T = state(tb, ts, tk)
        assert 90 + 10 < dist(E, Pb) < 170, dist(E, Pb)
        poses.append({"name": name, "set": [
            angle_item("boom", "base", tb, [pt("boom", (Lb, 0), S)]),
            angle_item("stick", "boom", ts, [pt("stick", (Ls, 0), K)]),
            angle_item("bucket", "stick", tk, [pt("bucket", (Lk, 0), T)])]})
    parts = {
        "ex_base": plate("底座板", [-60, 140], [-30, 70], 8, [(0, 40, 6), (110, 0, 6)]),
        "ex_boom": link("动臂", 160, 14, 6, [(0, 6), (60, 6), (160, 6)]),
        "ex_stick": link("斗杆", 110, 12, 6, [(0, 6), (110, 6)]),
        "ex_bucket": link("铲斗板", 50, 20, 6, [(0, 6)]),
        "ex_barrel": link("油缸筒", 90, 14, 6, [(0, 6)]),
        "ex_ram": link("活塞杆", 90, 10, 6, [(0, 6)]),
        "ex_pin26": pin("动臂销", 6, 26),
        "ex_pin14": pin("缸底销", 6, 14),
        "ex_pin12": pin("铰销", 6, 12),
    }
    roles = {"base": "ex_base", "boom": "ex_boom", "stick": "ex_stick", "bucket": "ex_bucket", "barrel": "ex_barrel",
             "ram": "ex_ram", "boom_pin": "ex_pin26", "barrel_pin": "ex_pin14", "ram_pin": "ex_pin12",
             "stick_pin": "ex_pin12", "bucket_pin": "ex_pin12"}
    prompt = f"""装配挖掘机工作臂（动臂由油缸驱动，斗杆、铲斗可转）。{COMMON}
- base 固定，z∈[0,8]。动臂铰孔 (0,40)，油缸底铰孔 (110,0)。
- boom_pin 固定在 (0,40) 孔（z∈[0,26]）；boom 的原点孔套在 boom_pin 上转动（z∈[20,26]）。
- barrel_pin 固定在 (110,0) 孔（z∈[0,14]）；barrel 的孔套在 barrel_pin 上转动（z∈[8,14]），筒身朝动臂方向伸出。
- ram_pin 固定在 boom 的 X=60 孔（z∈[14,26]）；ram 的孔套在 ram_pin 上转动（z∈[14,20]），杆身朝 barrel_pin 方向伸进油缸筒，
  ram 与 barrel 组成滑动副（只能沿缸筒轴线伸缩，两者轴线始终共线）。
- stick_pin 固定在 boom 的 X=160 孔（z∈[20,32]）；stick 的原点孔套在上面转动（z∈[26,32]）。
- bucket_pin 固定在 stick 的 X=110 孔（z∈[26,38]）；bucket 的孔套在上面转动（z∈[32,38]）。
- 动臂俯仰（油缸伸缩）、斗杆、铲斗三个自由度都保持自由（整机活动度 3），不要固定角度。各件互不穿模，装好后保存。"""
    motion = poses_motion("base", poses)
    rubric = rub("base", 3, {"base": ["base"], "boom": ["boom"], "stick": ["stick"], "bucket": ["bucket"],
                             "barrel": ["barrel"], "ram": ["ram"]},
                 [("boom", "base", R), ("barrel", "base", R), ("ram", "boom", R), ("ram", "barrel", SP),
                  ("stick", "boom", R), ("bucket", "stick", R)],
                 flex={"boom_pin": ["base", "boom"], "barrel_pin": ["base", "barrel"], "ram_pin": ["boom", "ram"],
                       "stick_pin": ["boom", "stick"], "bucket_pin": ["stick", "bucket"]})
    E0, S0, K0, T0 = state(40, -60, -40)

    def build(q: Ref):
        q.ins("base", 0, 0, 0, ground=True)
        q.ins("boom_pin", *O, 0)
        q.fixed("boom_pin:cyl(d=6)", "base:cyl(d=6, near=[0,40])")
        q.ins("boom", *O, 20, 40)
        q.rev("boom:cyl(d=6, near=[0,0])", "boom_pin:cyl(d=6)")
        q.ins("barrel_pin", *Pb, 0)
        q.fixed("barrel_pin:cyl(d=6)", "base:cyl(d=6, near=[110,0])")
        q.ins("barrel", *Pb, 8, ang(Pb, E0))
        q.rev("barrel:cyl(d=6)", "barrel_pin:cyl(d=6)")
        q.ins("ram_pin", *E0, 14)
        q.fixed("ram_pin:cyl(d=6)", "boom:cyl(d=6, near=[60,0])")
        q.ins("ram", *E0, 14, ang(E0, Pb))
        q.rev("ram:cyl(d=6)", "ram_pin:cyl(d=6)")
        q.call("mate", {"type": "coincident", "a": "ram:plane(top)", "b": "barrel:plane(top)", "align": "closest"})
        q.ins("stick_pin", *S0, 20)
        q.fixed("stick_pin:cyl(d=6)", "boom:cyl(d=6, near=[160,0])")
        q.ins("stick", *S0, 26, -20)
        q.rev("stick:cyl(d=6, near=[0,0])", "stick_pin:cyl(d=6)")
        q.ins("bucket_pin", *K0, 26)
        q.fixed("bucket_pin:cyl(d=6)", "stick:cyl(d=6, near=[110,0])")
        q.ins("bucket", *K0, 32, -60)
        q.rev("bucket:cyl(d=6)", "bucket_pin:cyl(d=6)")
        return q.finish(3)

    return Case("excavator_arm", "excavator_arm", "挖掘机工作臂（多位姿）",
                "动臂—油缸筒—活塞杆闭环 + 斗杆 + 铲斗，活动度 3；三个位姿检查斗杆根、铲斗根、斗尖位置。",
                parts, roles, prompt, motion, rubric, build)


# ---- P2 SCARA 机械臂

@case
def scara_arm():
    L1, L2 = 150, 120
    poses = []
    for name, (t1, t2, zq) in (("pick", (20, 60, 0)), ("place", (60, -70, -30)), ("tuck", (-30, 100, 20))):
        Eb = pol((0, 0), L1, t1)
        Q = pol(Eb, L2, t1 + t2)
        poses.append({"name": name, "set": [
            angle_item("upper_arm", "base", t1, [pt("upper_arm", (L1, 0), Eb)]),
            angle_item("forearm", "upper_arm", t2, [pt("forearm", (L2, 0), Q)]),
            {"type": "offset", "a": "quill:plane(front)", "b": "base:plane(front)", "offset_mm": zq,
             "points": [{"role": "quill", "local": [0, 0, 0], "expect": {"x": r2(Q[0]), "y": r2(Q[1]), "z": zq}}]}]})
    parts = {
        "sc_base": plate("底座", [-60, 60], [-60, 60], 10, [(0, 0, 10)]),
        "sc_column": pin("立柱", 10, 40),
        "sc_upper_arm": link("大臂", 150, 30, 10, [(0, 10), (150, 10)]),
        "sc_elbow_pin": pin("肘销", 10, 20),
        "sc_forearm": link("小臂", 120, 24, 10, [(0, 10), (120, 10)]),
        "sc_quill": pin("升降杆", 10, 100),
    }
    roles = {"base": "sc_base", "column": "sc_column", "upper_arm": "sc_upper_arm", "elbow_pin": "sc_elbow_pin",
             "forearm": "sc_forearm", "quill": "sc_quill"}
    prompt = f"""装配 SCARA 机械臂。{COMMON}
- base 固定，z∈[0,10]，中心孔 (0,0)。column 固定在中心孔里（z∈[0,40]）。
- upper_arm 的原点孔套在 column 上绕 Z 转动（z∈[30,40]）；elbow_pin 固定在 upper_arm 的 X=150 孔（z∈[30,50]）。
- forearm 的原点孔套在 elbow_pin 上绕 Z 转动（z∈[40,50]）。
- quill 插在 forearm 的 X=120 孔里，可沿 Z 升降、也可绕自身轴线转动（圆柱副）；初始 z∈[0,100]。
- 两个转角、升降与 quill 自转都保持自由（整机活动度 4），不要固定。各件互不穿模，装好后保存。"""
    motion = poses_motion("base", poses)
    rubric = rub("base", 4, {"base": ["base", "column"], "upper_arm": ["upper_arm"], "forearm": ["forearm"],
                             "quill": ["quill"]},
                 [("upper_arm", "base", R), ("forearm", "upper_arm", R), ("quill", "forearm", "cylindrical")],
                 flex={"elbow_pin": ["upper_arm", "forearm"]})

    def build(q: Ref):
        q.ins("base", 0, 0, 0, ground=True)
        q.ins("column", 0, 0, 0)
        q.fixed("column:cyl(d=10)", "base:cyl(d=10)")
        q.ins("upper_arm", 0, 0, 30)
        q.rev("upper_arm:cyl(d=10, near=[0,0])", "column:cyl(d=10)")
        q.ins("elbow_pin", L1, 0, 30)
        q.fixed("elbow_pin:cyl(d=10)", "upper_arm:cyl(d=10, near=[150,0])")
        q.ins("forearm", L1, 0, 40, 60)
        q.rev("forearm:cyl(d=10, near=[0,0])", "elbow_pin:cyl(d=10)")
        Q = pol((L1, 0), L2, 60)
        q.ins("quill", *Q, 0)
        q.close("quill:cyl(d=10)", "forearm:cyl(d=10, near=[120,0])")
        return q.finish(4)

    return Case("scara_arm", "scara_arm", "SCARA 机械臂（多位姿）",
                "大臂 150、小臂 120 两转动 + 升降杆圆柱副，活动度 4；三个位姿检查肘点、末端 xyz。",
                parts, roles, prompt, motion, rubric, build)


# ---- P3 缩放仪（2 倍）

@case
def pantograph():
    L = 60
    poses = []
    for name, (t1, t2) in (("a", (30, 80)), ("b", (10, 100)), ("c", (50, 70))):
        u, v = t1, t1 + t2
        A, T = pol((0, 0), 2 * L, u), pol(pol((0, 0), L, u), L, v)
        P = pol(A, 2 * L, v)
        poses.append({"name": name, "set": [
            angle_item("bar1", "base", t1, [pt("bar1", (2 * L, 0), A)]),
            angle_item("bar2", "bar1", t2, [pt("bar2", (2 * L, 0), P), pt("bar3", (L, 0), T)])]})
    parts = {
        "pg_base": plate("底座", [-30, 30], [-30, 30], 8, [(0, 0, 6)]),
        "pg_long_bar": link("长杆", 120, 10, 6, [(0, 6), (60, 6), (120, 6)]),
        "pg_short_bar": link("短杆", 60, 10, 6, [(0, 6), (60, 6)]),
        "pg_pin14": pin("支点销", 6, 14),
        "pg_pin12": pin("铰销", 6, 12),
    }
    roles = {"base": "pg_base", "pin_o": "pg_pin14", "bar1": "pg_long_bar", "bar2": "pg_long_bar", "bar3": "pg_short_bar",
             "bar4": "pg_short_bar", "pin_c": "pg_pin12", "pin_a": "pg_pin12", "pin_d": "pg_pin12", "pin_t": "pg_pin12"}
    prompt = f"""装配 2 倍缩放仪（平行四边形连杆）。{COMMON}
- base 固定，z∈[0,8]，支点孔 (0,0)。pin_o 固定在支点孔（z∈[0,14]）；bar1 的原点孔套在 pin_o 上转动（z∈[8,14]）。
- pin_c、pin_a 分别固定在 bar1 的 X=60、X=120 孔里（z∈[8,20]）。
- bar2 的原点孔套在 pin_a 上转动（z∈[14,20]）；bar3 的原点孔套在 pin_c 上转动（z∈[14,20]）。
- pin_d 固定在 bar2 的 X=60 孔（z∈[14,26]），pin_t 固定在 bar3 的 X=60 孔（z∈[14,26]）；
  bar4 两端孔分别套在 pin_t 与 pin_d 上（z∈[20,26]）。bar3 与 bar2 平行、bar4 与 bar1 平行，
  bar3 远端（描点 T）与 bar2 远端（笔尖 P）始终满足 P = 2·T（以支点为原点）。
- 两个转角保持自由（活动度 2），不要固定。各杆互不穿模，装好后保存。"""
    motion = poses_motion("base", poses)
    rubric = rub("base", 2, {"base": ["base"], "bar1": ["bar1"], "bar2": ["bar2"], "bar3": ["bar3"], "bar4": ["bar4"]},
                 [("bar1", "base", R), ("bar2", "bar1", R), ("bar3", "bar1", R), ("bar4", "bar3", R), ("bar4", "bar2", RC)],
                 flex={"pin_o": ["base", "bar1"], "pin_c": ["bar1", "bar3"], "pin_a": ["bar1", "bar2"],
                       "pin_d": ["bar2", "bar4"], "pin_t": ["bar3", "bar4"]})
    t1, t2 = 30, 80
    C0, A0 = pol((0, 0), L, t1), pol((0, 0), 2 * L, t1)
    D0, T0 = pol(A0, L, t1 + t2), pol(C0, L, t1 + t2)

    def build(q: Ref):
        q.ins("base", 0, 0, 0, ground=True)
        q.ins("pin_o", 0, 0, 0)
        q.fixed("pin_o:cyl(d=6)", "base:cyl(d=6)")
        q.ins("bar1", 0, 0, 8, t1)
        q.rev("bar1:cyl(d=6, near=[0,0])", "pin_o:cyl(d=6)")
        q.ins("pin_c", *C0, 8)
        q.fixed("pin_c:cyl(d=6)", "bar1:cyl(d=6, near=[60,0])")
        q.ins("pin_a", *A0, 8)
        q.fixed("pin_a:cyl(d=6)", "bar1:cyl(d=6, near=[120,0])")
        q.ins("bar2", *A0, 14, t1 + t2)
        q.rev("bar2:cyl(d=6, near=[0,0])", "pin_a:cyl(d=6)")
        q.ins("bar3", *C0, 14, t1 + t2)
        q.rev("bar3:cyl(d=6, near=[0,0])", "pin_c:cyl(d=6)")
        q.ins("pin_d", *D0, 14)
        q.fixed("pin_d:cyl(d=6)", "bar2:cyl(d=6, near=[60,0])")
        q.ins("pin_t", *T0, 14)
        q.fixed("pin_t:cyl(d=6)", "bar3:cyl(d=6, near=[60,0])")
        q.ins("bar4", *T0, 20, t1)
        q.rev("bar4:cyl(d=6, near=[0,0])", "pin_t:cyl(d=6)")
        q.close("bar4:cyl(d=6, near=[60,0])", "pin_d:cyl(d=6)")
        return q.finish(2)

    return Case("pantograph", "pantograph", "2 倍缩放仪（多位姿）",
                "平行四边形缩放连杆，活动度 2；三个位姿检查 A、T、P 点，P = 2T。",
                parts, roles, prompt, motion, rubric, build)


# ---- P4 阿克曼转向梯形

@case
def ackermann_steering():
    KL, KR, arm, beta = (-60, 0), (60, 0), 25, 16
    tie = 120 - 2 * arm * math.sin(math.radians(beta))
    base_l, base_r = -90 + beta, -90 - beta

    def right(delta):
        JL = pol(KL, arm, base_l + delta)
        cands = [circ(JL, KR, tie, arm, s) for s in (1, -1)]
        JR = pick(cands, key=lambda p: -p[1])
        return JL, JR

    poses = []
    for name, delta in (("left_small", 15), ("left_full", 28), ("right", -20)):
        JL, JR = right(delta)
        poses.append({"name": name, "set": [angle_item("knuckle_l", "axle", base_l + delta,
                                                      [pt("knuckle_l", (arm, 0), JL), pt("knuckle_r", (arm, 0), JR)])],
                      "right_steer_deg": r2(ang(KR, JR) - base_r)})
    parts = {
        "ak_axle": plate("前桥板", [-80, 80], [-15, 15], 8, [(-60, 0, 6), (60, 0, 6)]),
        "ak_knuckle": link("转向节臂", 25, 12, 6, [(0, 6), (25, 6)]),
        "ak_tie_rod": link("横拉杆", r2(tie), 10, 6, [(0, 6), (r2(tie), 6)]),
        "ak_pin14": pin("主销", 6, 14),
        "ak_pin12": pin("拉杆销", 6, 12),
    }
    roles = {"axle": "ak_axle", "kingpin_l": "ak_pin14", "kingpin_r": "ak_pin14", "knuckle_l": "ak_knuckle",
             "knuckle_r": "ak_knuckle", "tie_pin_l": "ak_pin12", "tie_pin_r": "ak_pin12", "tie_rod": "ak_tie_rod"}
    prompt = f"""装配阿克曼转向梯形。{COMMON}
- axle 固定，z∈[0,8]，主销孔 (-60,0) 与 (60,0)。kingpin_l、kingpin_r 分别固定在两孔里（z∈[0,14]）。
- knuckle_l、knuckle_r 的原点孔分别套在 kingpin_l、kingpin_r 上转动（z∈[8,14]）。
  直行时两转向节臂都指向 −Y（车尾方向）并向内倾 {beta}°：knuckle_l 指向 {base_l}°，knuckle_r 指向 {base_r}°（从 +X 量）。
- tie_pin_l、tie_pin_r 分别固定在两转向节臂的 X=25 孔里（z∈[8,20]）；tie_rod 两端孔分别套在上面（z∈[14,20]）。
- 转向自由度保持自由（活动度 1），不要固定。各件互不穿模，装好后保存。"""
    motion = poses_motion("axle", poses)
    rubric = rub("axle", 1, {"axle": ["axle"], "knuckle_l": ["knuckle_l"], "knuckle_r": ["knuckle_r"], "tie_rod": ["tie_rod"]},
                 [("knuckle_l", "axle", R), ("knuckle_r", "axle", R), ("tie_rod", "knuckle_l", R), ("tie_rod", "knuckle_r", RC)],
                 flex={"kingpin_l": ["axle", "knuckle_l"], "kingpin_r": ["axle", "knuckle_r"],
                       "tie_pin_l": ["knuckle_l", "tie_rod"], "tie_pin_r": ["knuckle_r", "tie_rod"]})
    JL0, JR0 = pol(KL, arm, base_l), pol(KR, arm, base_r)

    def build(q: Ref):
        q.ins("axle", 0, 0, 0, ground=True)
        for s, K, J, rot in (("l", KL, JL0, base_l), ("r", KR, JR0, base_r)):
            q.ins(f"kingpin_{s}", *K, 0)
            q.fixed(f"kingpin_{s}:cyl(d=6)", f"axle:cyl(d=6, near=[{K[0]},0])")
            q.ins(f"knuckle_{s}", *K, 8, rot)
            q.rev(f"knuckle_{s}:cyl(d=6, near=[0,0])", f"kingpin_{s}:cyl(d=6)")
            q.ins(f"tie_pin_{s}", *J, 8)
            q.fixed(f"tie_pin_{s}:cyl(d=6)", f"knuckle_{s}:cyl(d=6, near=[25,0])")
        q.ins("tie_rod", *JL0, 14)
        q.rev("tie_rod:cyl(d=6, near=[0,0])", "tie_pin_l:cyl(d=6)")
        q.close(f"tie_rod:cyl(d=6, near=[{r2(tie)},0])", "tie_pin_r:cyl(d=6)")
        return q.finish(1)

    return Case("ackermann_steering", "ackermann_steering", "阿克曼转向梯形（多位姿）",
                f"主销距 120、臂长 25、内倾 {beta}°，横拉杆 {tie:.2f}；三个转角检查内外轮转向节位置。",
                parts, roles, prompt, motion, rubric, build, {"tie": tie})


# ---- P5 肘节夹钳

@case
def toggle_clamp():
    A, H, aj, hk, b = (0, 0), (60, -20), 30, 25, 40

    def state(phi):
        K = pol(H, hk, phi)
        J = pick([circ(A, K, aj, b, s) for s in (1, -1)], key=lambda p: p[1])
        return J, K

    poses = []
    for name, phi in (("open", 150), ("mid", 120), ("clamp", 90)):
        J, K = state(phi)
        alpha = ang(A, J)
        poses.append({"name": name, "set": [angle_item("handle", "base", phi, [
            pt("handle", (80, 0), pol(H, 80, phi)), pt("arm", (100, 0), pol(A, 100, alpha))])]})
    parts = {
        "tc_base": plate("底座板", [-30, 90], [-40, 30], 8, [(0, 0, 6), (60, -20, 6)]),
        "tc_arm": link("压臂", 100, 12, 6, [(0, 6), (30, 6)]),
        "tc_handle": link("手柄", 80, 12, 6, [(0, 6), (25, 6)]),
        "tc_link": link("连杆", 40, 10, 6, [(0, 6), (40, 6)]),
        "tc_pin14": pin("压臂销", 6, 14),
        "tc_pin26": pin("手柄销", 6, 26),
        "tc_pin12": pin("铰销", 6, 12),
    }
    roles = {"base": "tc_base", "arm": "tc_arm", "handle": "tc_handle", "link": "tc_link", "pin_a": "tc_pin14",
             "pin_h": "tc_pin26", "pin_j": "tc_pin12", "pin_k": "tc_pin12"}
    prompt = f"""装配肘节夹钳（四杆机构：压臂、连杆、手柄）。{COMMON}
- base 固定，z∈[0,8]，压臂铰孔 (0,0)，手柄铰孔 (60,-20)。
- pin_a 固定在 (0,0) 孔（z∈[0,14]），arm 的原点孔套在上面转动（z∈[8,14]）；pin_j 固定在 arm 的 X=30 孔（z∈[8,20]）。
- pin_h 固定在 (60,-20) 孔（z∈[0,26]），handle 的原点孔套在上面转动（z∈[20,26]）；pin_k 固定在 handle 的 X=25 孔（z∈[14,26]）。
- link 两端孔分别套在 pin_j、pin_k 上（z∈[14,20]）。压臂 30、连杆 40、手柄 25、机架 {dist(A, H):.2f}，
  连杆与压臂的铰点 J 在 A、K 连线的上方（+Y 侧）。
- 机构活动度 1，不要固定角度。各件互不穿模，装好后保存。"""
    motion = poses_motion("base", poses)
    rubric = rub("base", 1, {"base": ["base"], "arm": ["arm"], "handle": ["handle"], "link": ["link"]},
                 [("arm", "base", R), ("handle", "base", R), ("link", "arm", R), ("link", "handle", RC)],
                 flex={"pin_a": ["base", "arm"], "pin_h": ["base", "handle"], "pin_j": ["arm", "link"],
                       "pin_k": ["handle", "link"]})
    J0, K0 = state(120)

    def build(q: Ref):
        q.ins("base", 0, 0, 0, ground=True)
        q.ins("pin_a", *A, 0)
        q.fixed("pin_a:cyl(d=6)", "base:cyl(d=6, near=[0,0])")
        q.ins("arm", *A, 8, ang(A, J0))
        q.rev("arm:cyl(d=6, near=[0,0])", "pin_a:cyl(d=6)")
        q.ins("pin_j", *J0, 8)
        q.fixed("pin_j:cyl(d=6)", "arm:cyl(d=6, near=[30,0])")
        q.ins("pin_h", *H, 0)
        q.fixed("pin_h:cyl(d=6)", "base:cyl(d=6, near=[60,-20])")
        q.ins("handle", *H, 20, 120)
        q.rev("handle:cyl(d=6, near=[0,0])", "pin_h:cyl(d=6)")
        q.ins("pin_k", *K0, 14)
        q.fixed("pin_k:cyl(d=6)", "handle:cyl(d=6, near=[25,0])")
        q.ins("link", *J0, 14, ang(J0, K0))
        q.rev("link:cyl(d=6, near=[0,0])", "pin_j:cyl(d=6)")
        q.close("link:cyl(d=6, near=[40,0])", "pin_k:cyl(d=6)")
        return q.finish(1)

    return Case("toggle_clamp", "toggle_clamp", "肘节夹钳（多位姿）", "压臂—连杆—手柄四杆，三个手柄角检查压臂端与手柄端位置。",
                parts, roles, prompt, motion, rubric, build)


# ---- P6 平行夹爪（两组平行四边形）

@case
def parallel_gripper():
    Lk = 40
    pivots = {"l": [(-60, 0), (-60, 30)], "r": [(60, 0), (60, 30)]}

    def finger(side, theta):
        a = theta if side == "l" else 180 - theta
        return pol(pivots[side][0], Lk, a), a

    poses = []
    for name, (tl, tr) in (("open", (10, 10)), ("close", (-15, -15)), ("skew", (20, -10))):
        items = []
        for side, th in (("l", tl), ("r", tr)):
            F, a = finger(side, th)
            items.append(angle_item(f"lower_{side}", "base", a, [pt(f"finger_{side}", (60, 0), (F[0], F[1] + 60))]))
        poses.append({"name": name, "set": items})
    parts = {
        "gp_base": plate("夹爪底座", [-80, 80], [-20, 50], 8, [(-60, 0, 6), (-60, 30, 6), (60, 0, 6), (60, 30, 6)]),
        "gp_link": link("摆杆", 40, 10, 6, [(0, 6), (40, 6)]),
        "gp_finger": link("指板", 60, 12, 6, [(0, 6), (30, 6)]),
        "gp_pin14": pin("底座销", 6, 14),
        "gp_pin12": pin("铰销", 6, 12),
    }
    roles = {"base": "gp_base"}
    for s in "lr":
        roles.update({f"lower_{s}": "gp_link", f"upper_{s}": "gp_link", f"finger_{s}": "gp_finger",
                      f"base_pin_lo_{s}": "gp_pin14", f"base_pin_up_{s}": "gp_pin14",
                      f"link_pin_lo_{s}": "gp_pin12", f"link_pin_up_{s}": "gp_pin12"})
    prompt = f"""装配平行四边形夹爪（左右两指各由一组平行四边形连杆导向，两指独立）。{COMMON}
- base 固定，z∈[0,8]，左侧铰孔 (-60,0)、(-60,30)，右侧铰孔 (60,0)、(60,30)。
- base_pin_lo_l / base_pin_up_l / base_pin_lo_r / base_pin_up_r 依次固定在这四个孔里（z∈[0,14]）。
- lower_l、upper_l 的原点孔分别套在左侧两根底座销上转动，lower_r、upper_r 套在右侧两根上（z∈[8,14]）；
  四根摆杆初始都朝向机构中线（左侧指向 +X，右侧指向 −X）。
- link_pin_lo_* / link_pin_up_* 分别固定在对应摆杆的 X=40 孔里（z∈[8,20]）。
- finger_l 的原点孔套在 link_pin_lo_l 上、X=30 孔套在 link_pin_up_l 上（z∈[14,20]），指尖朝 +Y；finger_r 同理接右侧。
  每侧摆杆、指板与底座组成平行四边形，指板始终竖直平移。
- 两指各 1 个自由度（整机活动度 2），不要固定。各件互不穿模，装好后保存。"""
    motion = poses_motion("base", poses)
    bodies = {"base": ["base"]}
    joints, flex = [], {}
    for s in "lr":
        bodies.update({f"lower_{s}": [f"lower_{s}"], f"upper_{s}": [f"upper_{s}"], f"finger_{s}": [f"finger_{s}"]})
        joints += [(f"lower_{s}", "base", R), (f"upper_{s}", "base", R), (f"finger_{s}", f"lower_{s}", R),
                   (f"finger_{s}", f"upper_{s}", RC)]
        flex.update({f"base_pin_lo_{s}": ["base", f"lower_{s}"], f"base_pin_up_{s}": ["base", f"upper_{s}"],
                     f"link_pin_lo_{s}": [f"lower_{s}", f"finger_{s}"], f"link_pin_up_{s}": [f"upper_{s}", f"finger_{s}"]})
    rubric = rub("base", 2, bodies, joints, flex=flex)

    def build(q: Ref):
        q.ins("base", 0, 0, 0, ground=True)
        for s in "lr":
            a = 0 if s == "l" else 180
            for lvl, P in zip(("lo", "up"), pivots[s]):
                q.ins(f"base_pin_{lvl}_{s}", *P, 0)
                q.fixed(f"base_pin_{lvl}_{s}:cyl(d=6)", f"base:cyl(d=6, near=[{P[0]},{P[1]}])")
                bar = f"{'lower' if lvl == 'lo' else 'upper'}_{s}"
                q.ins(bar, *P, 8, a)
                q.rev(f"{bar}:cyl(d=6, near=[0,0])", f"base_pin_{lvl}_{s}:cyl(d=6)")
                Q = pol(P, Lk, a)
                q.ins(f"link_pin_{lvl}_{s}", *Q, 8)
                q.fixed(f"link_pin_{lvl}_{s}:cyl(d=6)", f"{bar}:cyl(d=6, near=[40,0])")
            F = pol(pivots[s][0], Lk, a)
            q.ins(f"finger_{s}", *F, 14, 90)
            q.rev(f"finger_{s}:cyl(d=6, near=[0,0])", f"link_pin_lo_{s}:cyl(d=6)")
            q.close(f"finger_{s}:cyl(d=6, near=[30,0])", f"link_pin_up_{s}:cyl(d=6)")
        return q.finish(2)

    return Case("parallel_gripper", "parallel_gripper", "平行四边形夹爪（多位姿）",
                "左右两组平行四边形，活动度 2；含对称与不对称三个位姿，检查两指尖位置。",
                parts, roles, prompt, motion, rubric, build)


# ---- P7 双横臂悬架

@case
def double_wishbone():
    L0, U0, la, ua, kl = (0, 0), (10, 60), 100, 70, 70

    def state(alpha):
        BL = pol(L0, la, alpha)
        BU = pick([circ(BL, U0, kl, ua, s) for s in (1, -1)], key=lambda p: p[1])
        return BL, BU

    poses = []
    for name, alpha in (("rebound", -12), ("ride", 5), ("bump", 15)):
        BL, BU = state(alpha)
        mid = ((BL[0] + BU[0]) / 2, (BL[1] + BU[1]) / 2)
        poses.append({"name": name, "set": [angle_item("lower_arm", "chassis", alpha, [
            pt("lower_arm", (la, 0), BL), pt("knuckle", (kl, 0), BU)])],
            "points": [{"role": "hub", "local": [0, 0, 0], "expect": {"x": r2(mid[0]), "y": r2(mid[1])}}]})
    parts = {
        "dw_chassis": plate("车架板", [-30, 30], [-20, 80], 8, [(0, 0, 6), (10, 60, 6)]),
        "dw_lower_arm": link("下摆臂", 100, 12, 6, [(0, 6), (100, 6)]),
        "dw_upper_arm": link("上摆臂", 70, 12, 6, [(0, 6), (70, 6)]),
        "dw_knuckle": link("转向节", 70, 14, 6, [(0, 6), (35, 6), (70, 6)]),
        "dw_hub": disk("轮毂盘", 50, 6, 6),
        "dw_pin14": pin("车架销", 6, 14),
        "dw_pin12": pin("铰销", 6, 12),
    }
    roles = {"chassis": "dw_chassis", "lower_pin": "dw_pin14", "upper_pin": "dw_pin14", "lower_arm": "dw_lower_arm",
             "upper_arm": "dw_upper_arm", "lower_ball": "dw_pin12", "upper_ball": "dw_pin12", "knuckle": "dw_knuckle",
             "spindle": "dw_pin12", "hub": "dw_hub"}
    prompt = f"""装配双横臂悬架（平面简化）。{COMMON}
- chassis 固定，z∈[0,8]，下摆臂铰孔 (0,0)，上摆臂铰孔 (10,60)。lower_pin、upper_pin 分别固定在两孔里（z∈[0,14]）。
- lower_arm、upper_arm 的原点孔分别套在 lower_pin、upper_pin 上转动（z∈[8,14]），都朝 +X 伸出。
- lower_ball、upper_ball 分别固定在 lower_arm 的 X=100 孔、upper_arm 的 X=70 孔里（z∈[8,20]）。
- knuckle 的原点孔套在 lower_ball 上、X=70 孔套在 upper_ball 上（z∈[14,20]），X=70 端在上。
- spindle 固定在 knuckle 的 X=35 孔里（z∈[14,26]）；hub 固定在 spindle 上（z∈[20,26]）。
- 悬架上下跳动保持自由（活动度 1），不要固定角度。各件互不穿模，装好后保存。"""
    motion = poses_motion("chassis", poses)
    rubric = rub("chassis", 1, {"chassis": ["chassis"], "lower_arm": ["lower_arm"], "upper_arm": ["upper_arm"],
                                "knuckle": ["knuckle", "spindle", "hub"]},
                 [("lower_arm", "chassis", R), ("upper_arm", "chassis", R), ("knuckle", "lower_arm", R),
                  ("knuckle", "upper_arm", RC)],
                 flex={"lower_pin": ["chassis", "lower_arm"], "upper_pin": ["chassis", "upper_arm"],
                       "lower_ball": ["lower_arm", "knuckle"], "upper_ball": ["upper_arm", "knuckle"]})
    BL0, BU0 = state(0)
    M0 = ((BL0[0] + BU0[0]) / 2, (BL0[1] + BU0[1]) / 2)

    def build(q: Ref):
        q.ins("chassis", 0, 0, 0, ground=True)
        for s, P, Bp, n in (("lower", L0, BL0, la), ("upper", U0, BU0, ua)):
            q.ins(f"{s}_pin", *P, 0)
            q.fixed(f"{s}_pin:cyl(d=6)", f"chassis:cyl(d=6, near=[{P[0]},{P[1]}])")
            q.ins(f"{s}_arm", *P, 8, ang(P, Bp))
            q.rev(f"{s}_arm:cyl(d=6, near=[0,0])", f"{s}_pin:cyl(d=6)")
            q.ins(f"{s}_ball", *Bp, 8)
            q.fixed(f"{s}_ball:cyl(d=6)", f"{s}_arm:cyl(d=6, near=[{n},0])")
        q.ins("knuckle", *BL0, 14, ang(BL0, BU0))
        q.rev("knuckle:cyl(d=6, near=[0,0])", "lower_ball:cyl(d=6)")
        q.close("knuckle:cyl(d=6, near=[70,0])", "upper_ball:cyl(d=6)")
        q.ins("spindle", *M0, 14)
        q.fixed("spindle:cyl(d=6)", "knuckle:cyl(d=6, near=[35,0])")
        q.ins("hub", *M0, 20)
        q.fixed("hub:cyl(d=6)", "spindle:cyl(d=6)")
        return q.finish(1)

    return Case("double_wishbone", "double_wishbone", "双横臂悬架（多位姿）",
                "下摆臂 100、上摆臂 70、转向节 70，三个跳动位置检查上下球铰与轮心。",
                parts, roles, prompt, motion, rubric, build)


# ------------------------------------------------------------------ 输出

def case_yaml(c: Case) -> str:
    import yaml
    head = [f"case_id: {c.case_id}", f"title: {c.title}", f"# {c.note}",
            "# 由 bench_cases.py 生成，改测例请改那里再运行 write。", ""]
    body = {
        "parts": [{"id": pid, "extra_tools": part_tools(p), "prompt": part_prompt(p) + "\n"} for pid, p in c.parts.items()],
        "gold_roles": c.roles,
        "assembly": {"prompt": c.prompt + "\n"},
        "motion": c.motion,
    }

    class Dumper(yaml.SafeDumper):
        pass

    def str_rep(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|" if "\n" in data else None)

    Dumper.add_representer(str, str_rep)
    return "\n".join(head) + yaml.dump(body, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=200)


def rubric_yaml(c: Case) -> str:
    import yaml
    head = [f"# 装配级 rubric：角色名与 {c.name}.yaml 的 gold_roles 一致（由 bench_cases.py 生成）。"]
    body = {"case_id": c.case_id, **c.rubric}
    return "\n".join(head) + "\n" + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=200,
                                                   default_flow_style=None)


def write_all() -> None:
    for c in CASES.values():
        (HERE / f"{c.name}.yaml").write_text(case_yaml(c), encoding="utf-8")
        (HERE / "assembly_rubrics" / f"{c.case_id}.yaml").write_text(rubric_yaml(c), encoding="utf-8")
        for pid, p in c.parts.items():
            (HERE / "rubrics" / f"{pid}.yaml").write_text(part_rubric(pid, p), encoding="utf-8")
    print(f"写入 {len(CASES)} 个测例")


def report() -> None:
    for c in CASES.values():
        extra = ", ".join(f"{k}={v:.2f}" for k, v in c.info.items())
        print(f"{c.name:22s} parts={len(c.parts):2d} comps={len(c.roles):2d} {extra}")


def build_parts(name: str) -> None:
    from eval.assembly_scratch.build_reference_assemblies import GOLD
    c = CASES[name]
    out = GOLD / name
    out.mkdir(parents=True, exist_ok=True)
    for pid, p in c.parts.items():
        build_part(str(out / f"{pid}.SLDPRT"), p)


def build_reference(name: str):
    c = CASES[name]
    return c.build(Ref(c))


if __name__ == "__main__":
    {"write": write_all, "report": report}[sys.argv[1]]()
