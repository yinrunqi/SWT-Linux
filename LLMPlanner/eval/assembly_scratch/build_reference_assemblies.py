"""用宏工具按规格手工搭各测例的正确装配体，作为装配 rubric 与运动学评测的阳性对照。

    python eval/assembly_scratch/build_reference_assemblies.py gearbox|engine|desk_lamp|umbrella|drill_gearbox|sewing_machine|projector|all

依赖 build_standard_parts.py 生成的 gold_parts/。输出 gold_parts/reference_<case>.SLDASM（不入库）。
评测器对这两个装配体应给出满分；若不是，先查评测器而不是查 agent。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.build_standard_parts import call  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402
from planner.assembly_workflow.tools import tool_names  # noqa: E402

GOLD = Path(__file__).resolve().parent / "gold_parts"


def _run(session: AssemblySession, tool: str, args: dict) -> dict:
    out = session.call(tool, args)
    print(f"  {tool:16s} {'OK ' if out.get('ok') else 'ERR'} {str(out.get('result') or out.get('error'))[:160]}")
    if not out.get("ok"):
        raise RuntimeError(f"{tool} 失败: {out.get('error')}")
    return out


def _session() -> AssemblySession:
    return AssemblySession(timeout=900, workflow="v2", allowed_tools=tool_names("v2"))


def gearbox() -> Path:
    parts = GOLD / "gearbox"
    dest = GOLD / "reference_gearbox.SLDASM"
    s = _session()
    s.start_assembly(str(dest))
    _run(s, "insert_component", {"part": str(parts / "housing.SLDPRT"), "name": "housing", "at": [0, 0, 0]})
    _run(s, "ground_component", {"component": "housing"})
    for name, x in (("input_shaft", 0), ("lay_shaft", 54), ("output_shaft", 126)):
        _run(s, "insert_component", {"part": str(parts / f"{name}.SLDPRT"), "name": name, "at": [x, 0, 0]})
        _run(s, "joint", {"type": "revolute", "a": f"{name}:cyl(d=10)", "b": f"housing:cyl(d=10.4, near=[{x},0])",
                          "face_a": f"{name}:plane(front)", "face_b": "housing:plane(front)"})
    layout = (
        ("input_pinion", "input_shaft", 15, 0),
        ("lay_wheel", "lay_shaft", 15, 54),
        ("lay_pinion", "lay_shaft", 27, 54),
        ("output_wheel", "output_shaft", 27, 126),
    )
    for name, shaft, z, x in layout:
        _run(s, "insert_component", {"part": str(parts / f"{name}.SLDPRT"), "name": name, "at": [x, 0, z]})
        _run(s, "mount_on_shaft", {"part": name, "shaft": shaft, "diameter": 10, "offset": z, "lock_rotation": True})
    _run(s, "mesh_gears", {"a": "input_pinion", "b": "lay_wheel", "teeth_a": 12, "teeth_b": 24, "module": 3})
    _run(s, "mesh_gears", {"a": "lay_pinion", "b": "output_wheel", "teeth_a": 12, "teeth_b": 36, "module": 3})
    _run(s, "check_dof", {"expected_mobility": 1})
    _run(s, "save_assembly", {})
    call("/api/close-all-documents")
    return dest


def engine() -> Path:
    """机体固定；曲轴 z∈[-26,14] 穿主轴承；曲柄 z∈[8,14]；曲柄销固定在曲柄小端；连杆 z∈[14,20]；
    活塞销固定在活塞上（活塞 z∈[20,50]），活塞右视基准面贴机体右视基准面，配合连杆链 → 沿 Y 滑动；
    飞轮、曲柄齿轮在板下；凸轮轴在板外 (−69.17, 20)，离主轴 72 mm，与 16/32 正时齿轮啮合。"""
    parts = GOLD / "engine"
    dest = GOLD / "reference_engine.SLDASM"
    y_pin = (90 ** 2 - 25 ** 2) ** 0.5
    xc, yc = -((72 ** 2 - 20 ** 2) ** 0.5), 20.0
    s = _session()
    s.start_assembly(str(dest))

    def insert(name, at):
        _run(s, "insert_component", {"part": str(parts / f"{name}.SLDPRT"), "name": name, "at": at})

    def joint(kind, a, b, offset=None, **extra):
        args = {"type": kind, "a": a, "b": b,
                "face_a": f"{a.split(':')[0]}:plane(front)", "face_b": f"{b.split(':')[0]}:plane(front)", **extra}
        if offset:
            args["offset"] = offset
        _run(s, "joint", args)

    insert("frame", [0, 0, 0])
    _run(s, "ground_component", {"component": "frame"})
    insert("crank_shaft", [0, 0, -26])
    joint("revolute", "crank_shaft:cyl(d=10)", "frame:cyl(d=10.4)", -26)
    insert("crank", [0, 0, 8])
    joint("fixed", "crank:cyl(d=10, near=[0,0])", "crank_shaft:cyl(d=10)", 34)
    insert("crank_pin", [25, 0, 8])
    joint("fixed", "crank_pin:cyl(d=10)", "crank:cyl(d=10, near=[25,0])")
    insert("rod", [25, 0, 14])
    joint("revolute", "rod:cyl(d=10, near=[0,0])", "crank_pin:cyl(d=10)", 6)
    insert("piston_pin", [0, y_pin, 14])
    insert("piston", [0, y_pin, 20])
    joint("fixed", "piston_pin:cyl(d=10)", "piston:cyl(d=10)", -6)
    joint("revolute", "rod:cyl(d=10, near=[90,0])", "piston_pin:cyl(d=10)")
    # 活塞的 z 与绕 X 转已由连杆链定下，闭环只需一个面重合；再加前视基准面距离会被判过定义
    _run(s, "mate", {"type": "coincident", "a": "piston:plane(right)", "b": "frame:plane(right)", "align": "aligned"})
    insert("flywheel", [0, 0, -14])
    joint("fixed", "flywheel:cyl(d=10)", "crank_shaft:cyl(d=10)", 12)
    insert("crank_gear", [0, 0, -26])
    joint("fixed", "crank_gear:cyl(i=0)", "crank_shaft:cyl(d=10)")
    insert("camshaft", [xc, yc, -26])
    _run(s, "mate", {"type": "distance", "a": "camshaft:cyl(d=10)", "b": "frame:plane(right)", "distance": -xc})
    _run(s, "mate", {"type": "distance", "a": "camshaft:cyl(d=10)", "b": "frame:plane(top)", "distance": yc})
    _run(s, "mate", {"type": "distance", "a": "camshaft:plane(front)", "b": "frame:plane(front)",
                     "distance": 26, "align": "aligned"})
    insert("cam_gear", [xc, yc, -26])
    joint("fixed", "cam_gear:cyl(i=0)", "camshaft:cyl(d=10)")
    insert("cam", [xc, yc, 10])
    joint("fixed", "cam:cyl(d=10)", "camshaft:cyl(d=10)", 36)
    _run(s, "mesh_gears", {"a": "crank_gear", "b": "cam_gear", "teeth_a": 16, "teeth_b": 32, "module": 3})
    _run(s, "check_dof", {"expected_mobility": 1})
    _run(s, "save_assembly", {})
    call("/api/close-all-documents")
    return dest


# ------------------------------------------------------------------ 由旧示例转换的五个测例
# 多个角色共用同一零件（如两根伞骨），组件名不再是 <角色>-1；角色→组件名另存 reference_<case>.aliases.json。

def _start(case: str):
    s = _session()
    dest = GOLD / f"reference_{case}.SLDASM"
    s.start_assembly(str(dest))
    parts = GOLD / case

    def insert(role, part, at, rotate=0.0):
        args = {"part": str(parts / f"{part}.SLDPRT"), "name": role, "at": at}
        if rotate:
            args["rotate_deg"] = rotate
        _run(s, "insert_component", args)

    def joint(kind, a, b, offset=None, face_a=None, face_b=None):
        args = {"type": kind, "a": a, "b": b,
                "face_a": face_a or f"{a.split(':')[0]}:plane(front)",
                "face_b": face_b or f"{b.split(':')[0]}:plane(front)"}
        if offset is not None:
            args["offset"] = offset
        _run(s, "joint", args)

    return s, dest, insert, joint


def _finish(s: AssemblySession, dest: Path, mobility: int) -> Path:
    _run(s, "check_dof", {"expected_mobility": mobility})
    _run(s, "save_assembly", {})
    dest.with_suffix(".aliases.json").write_text(json.dumps(s.aliases, ensure_ascii=False, indent=1), encoding="utf-8")
    call("/api/close-all-documents")
    return dest


def desk_lamp() -> Path:
    """底座 z∈[0,8]；肩销固定在底座 (0,60)、z∈[3,13]；下臂 [8,13]、上臂 [13,18]、灯头 [18,23]；
    肘销、腕销分别固定在下臂、上臂远端孔。初始全部水平伸向 +X。"""
    s, dest, insert, joint = _start("desk_lamp")
    insert("base", "lamp_base", [0, 0, 0])
    _run(s, "ground_component", {"component": "base"})
    insert("shoulder_pin", "lamp_pin", [0, 60, 3])
    joint("fixed", "shoulder_pin:cyl(d=5)", "base:cyl(d=5.2)", 3)
    insert("lower_arm", "lamp_lower_arm", [0, 60, 8])
    joint("revolute", "lower_arm:cyl(d=5, near=[0,0])", "shoulder_pin:cyl(d=5)", 8, face_b="base:plane(front)")
    insert("elbow_pin", "lamp_pin", [120, 60, 8])
    joint("fixed", "elbow_pin:cyl(d=5)", "lower_arm:cyl(d=5, near=[120,0])")
    insert("upper_arm", "lamp_upper_arm", [120, 60, 13])
    joint("revolute", "upper_arm:cyl(d=5, near=[0,0])", "elbow_pin:cyl(d=5)", 5, face_b="lower_arm:plane(front)")
    insert("wrist_pin", "lamp_pin", [225, 60, 13])
    joint("fixed", "wrist_pin:cyl(d=5)", "upper_arm:cyl(d=5, near=[105,0])")
    insert("shade", "lamp_shade", [225, 60, 18])
    joint("revolute", "shade:cyl(d=5)", "wrist_pin:cyl(d=5)", 5, face_b="upper_arm:plane(front)")
    return _finish(s, dest, 3)


def _side_b_revolute(s: AssemblySession, link: str, base: str, x: float, y: float) -> None:
    """-Y 侧铰接。同轴配合按 closest 把杆件 +Z 转到 +Y，杆件前视面须落在底座 -Y 面外 4 mm（板厚）处；
    joint 的 coincident/distance 固定用 aligned，这里会过定义，故直接建 anti 距离配合并核对落位。"""
    s._mate("concentric", f"{link}:cyl(d=4, near=[0,0])", f"{base}:cyl(d=4, near=[{-x},0])", align="closest")
    name = s.aliases[link]
    for flip in (False, True):
        mate = s._mate("distance", f"{link}:plane(front)", f"{base}:face(-Y, largest)",
                       distance=0.004, align="anti", flip=flip)
        comp = next(c for c in s.asmwf("components")["components"] if c["name"] == name)
        if abs(comp["transform"][10] * 1000 + y + 4) < 0.5:
            print(f"  {link} 铰接 OK")
            return
        s._rollback_mates([mate["name"]])
    raise RuntimeError(f"{link} -Y 侧铰接落位不对")


def umbrella() -> Path:
    """中棒 z∈[0,400] 固定；上巢固定在 z=340；伞骨贴上巢 ±Y 面、撑杆贴下巢 ±Y 面；
    撑杆远端与伞骨 120 mm 孔只做同轴（闭环最后一铰）。初始伞骨水平、下巢在闭环对应高度。"""
    s, dest, insert, joint = _start("umbrella")
    insert("shaft", "umb_shaft", [0, 0, 0])
    _run(s, "ground_component", {"component": "shaft"})
    insert("hub", "umb_hub", [0, 0, 340])
    joint("fixed", "hub:cyl(d=10)", "shaft:cyl(d=10)", 340)
    z_r = 340 - (160 ** 2 - 123 ** 2) ** 0.5
    insert("runner", "umb_runner", [0, 0, z_r])
    _run(s, "joint", {"type": "cylindrical", "a": "runner:cyl(d=10.4)", "b": "shaft:cyl(d=10)"})
    for side, sign in (("a", 1), ("b", -1)):
        axis = "+Y" if sign > 0 else "-Y"
        insert(f"rib_{side}", "umb_rib", [18 * sign, 8 * sign, 340], 180 if sign < 0 else 0)
        for link, base, x, y in ((f"rib_{side}", "hub", 18, 8), (f"stretcher_{side}", "runner", 15, 12)):
            if link.startswith("stretcher"):
                insert(link, "umb_stretcher", [15 * sign, 12 * sign, z_r], 180 if sign < 0 else 0)
            if sign > 0:
                joint("revolute", f"{link}:cyl(d=4, near=[0,0])", f"{base}:cyl(d=4, near=[{x},0])",
                      face_b=f"{base}:face({axis}, largest)")
            else:
                _side_b_revolute(s, link, base, x, y)
        _run(s, "joint", {"type": "cylindrical", "a": f"stretcher_{side}:cyl(d=4, near=[160,0])",
                          "b": f"rib_{side}:cyl(d=4, near=[120,0])"})
    return _finish(s, dest, 1)


def drill_gearbox() -> Path:
    """两级行星（每级一个行星轮）：齿圈固定；行星架一 z∈[16,20] 带太阳轮二；行星架二 z∈[30,34] 带输出轴。
    第二级 15/15/45 齿：行星轮须错开 12° 才与齿圈啮合，此时朝太阳轮一侧是齿，太阳轮二须先错开 12°。
    太阳轮二固定在行星架一上，事后转动行星轮不会带动它，所以插入时就转好。"""
    s, dest, insert, joint = _start("drill_gearbox")
    insert("housing", "drill_housing", [0, 0, 0])
    _run(s, "ground_component", {"component": "housing"})
    insert("ring1", "drill_ring48", [0, 0, 6])
    joint("fixed", "ring1:cyl(i=0)", "housing:cyl(d=4.4)", 6, face_b="housing:plane(front)")
    insert("ring2", "drill_ring45", [0, 0, 20])
    joint("fixed", "ring2:cyl(i=0)", "housing:cyl(d=4.4)", 20, face_b="housing:plane(front)")
    insert("input_shaft", "drill_shaft", [0, 0, -6])
    joint("revolute", "input_shaft:cyl(d=4)", "housing:cyl(d=4.4)", -6)
    insert("sun1", "drill_sun12", [0, 0, 6])
    _run(s, "mount_on_shaft", {"part": "sun1", "shaft": "input_shaft", "diameter": 4, "offset": 12, "lock_rotation": True})
    insert("carrier1", "drill_carrier", [0, 0, 16])
    joint("revolute", "carrier1:cyl(d=4, near=[0,0])", "housing:cyl(d=4.4)", 16, face_b="housing:plane(front)")
    insert("sun2", "drill_gear15", [0, 0, 20], 12)
    joint("fixed", "sun2:cyl(i=0)", "carrier1:cyl(d=4, near=[0,0])", 4)
    insert("carrier2", "drill_carrier", [0, 0, 30])
    joint("revolute", "carrier2:cyl(d=4, near=[0,0])", "housing:cyl(d=4.4)", 30, face_b="housing:plane(front)")
    insert("output_shaft", "drill_shaft", [0, 0, 30])
    joint("fixed", "output_shaft:cyl(d=4)", "carrier2:cyl(d=4, near=[0,0])")
    for stage, carrier, sun, ring, part, z, zs, zp, zr in (
            (1, "carrier1", "sun1", "ring1", "drill_planet18", 6, 12, 18, 48),
            (2, "carrier2", "sun2", "ring2", "drill_gear15", 20, 15, 15, 45)):
        name = f"planet{stage}"
        insert(name, part, [18, 0, z])
        joint("revolute", f"{name}:cyl(i=0)", f"{carrier}:cyl(d=4, near=[18,0])", -10)
        _run(s, "mesh_gears", {"a": sun, "b": name, "teeth_a": zs, "teeth_b": zp, "module": 1.2})
        _run(s, "mesh_gears", {"a": name, "b": ring, "teeth_a": zp, "teeth_b": zr, "module": 1.2, "internal": True})
    return _finish(s, dest, 1)


def sewing_machine() -> Path:
    """板前：主轴 z∈[-18,14]，针杆曲柄 [8,14]，曲柄销 [8,28]，连杆 [14,20]，针杆销 [14,34] 固定在针杆滑块 [20,50]；
    板后：主轴齿轮 [-12,-4]，送布曲柄 [-18,-12]，送布连杆 [-24,-18]，摇杆 [-30,-24]（绕固定在机体上的摇杆销），
    旋梭轴在 (-45,0)、z∈[-20,12]，旋梭齿轮 [-12,-4]、旋梭盘 [-20,-12]。"""
    import math
    s, dest, insert, joint = _start("sewing_machine")
    y_pin = (65 ** 2 - 18 ** 2) ** 0.5
    insert("frame", "sew_frame", [0, 0, 0])
    _run(s, "ground_component", {"component": "frame"})
    insert("main_shaft", "sew_shaft", [0, 0, -18])
    joint("revolute", "main_shaft:cyl(d=10)", "frame:cyl(d=10.4, near=[0,0])", -18)
    insert("crank", "sew_crank", [0, 0, 8])
    joint("fixed", "crank:cyl(d=10, near=[0,0])", "main_shaft:cyl(d=10)", 26)
    insert("crank_pin", "sew_pin", [18, 0, 8])
    joint("fixed", "crank_pin:cyl(d=10)", "crank:cyl(d=10, near=[18,0])")
    insert("needle_rod", "sew_rod", [18, 0, 14], math.degrees(math.atan2(y_pin, -18)))
    joint("revolute", "needle_rod:cyl(d=10, near=[0,0])", "crank_pin:cyl(d=10)", 6)
    insert("needle_pin", "sew_pin", [0, y_pin, 14])
    insert("needle_bar", "sew_needle_bar", [0, y_pin, 20])
    joint("fixed", "needle_pin:cyl(d=10)", "needle_bar:cyl(d=10)", -6)
    joint("revolute", "needle_rod:cyl(d=10, near=[65,0])", "needle_pin:cyl(d=10)")
    _run(s, "mate", {"type": "coincident", "a": "needle_bar:plane(right)", "b": "frame:plane(right)", "align": "aligned"})
    insert("main_gear", "sew_main_gear", [0, 0, -12])
    joint("fixed", "main_gear:cyl(i=0)", "main_shaft:cyl(d=10)", 6)
    insert("feed_crank", "sew_feed_crank", [0, 0, -18])
    joint("fixed", "feed_crank:cyl(d=10)", "main_shaft:cyl(d=10)")
    insert("hook_shaft", "sew_shaft", [-45, 0, -20])
    joint("revolute", "hook_shaft:cyl(d=10)", "frame:cyl(d=10.4, near=[-45,0])", -20)
    insert("hook_gear", "sew_hook_gear", [-45, 0, -12])
    joint("fixed", "hook_gear:cyl(i=0)", "hook_shaft:cyl(d=10)", 8)
    insert("hook", "sew_hook", [-45, 0, -20])
    joint("fixed", "hook:cyl(d=10)", "hook_shaft:cyl(d=10)")
    _run(s, "mesh_gears", {"a": "main_gear", "b": "hook_gear", "teeth_a": 40, "teeth_b": 20, "module": 1.5})
    # 曲柄摇杆：曲柄销 C=(12,0)，摇杆支点 G=(36,-27)，连杆 50、摇杆 36，取 R 在 CG 连线下方的一支
    cx, gx, gy = 12.0, 36.0, -27.0
    dx, dy = gx - cx, gy
    dist = math.hypot(dx, dy)
    along = (50 ** 2 - 36 ** 2 + dist ** 2) / (2 * dist)
    h = (50 ** 2 - along ** 2) ** 0.5
    rx, ry = cx + along * dx / dist + h * dy / dist, along * dy / dist - h * dx / dist
    insert("rocker_pin", "sew_rocker_pin", [gx, gy, -30])
    joint("fixed", "rocker_pin:cyl(d=6)", "frame:cyl(d=6.4)", -30)
    insert("feed_rocker", "sew_rocker", [gx, gy, -30], math.degrees(math.atan2(ry - gy, rx - gx)))
    joint("revolute", "feed_rocker:cyl(d=6, near=[0,0])", "rocker_pin:cyl(d=6)", -30, face_b="frame:plane(front)")
    insert("feed_coupler", "sew_coupler", [cx, 0, -24], math.degrees(math.atan2(ry, rx - cx)))
    joint("revolute", "feed_coupler:cyl(d=6, near=[0,0])", "feed_crank:cyl(d=6)", -6)
    _run(s, "joint", {"type": "cylindrical", "a": "feed_coupler:cyl(d=6, near=[50,0])",
                      "b": "feed_rocker:cyl(d=6, near=[36,0])"})
    return _finish(s, dest, 1)


def projector() -> Path:
    """四根轴 z∈[-14,36]；第一层齿轮 z∈[10,18]，第二层 [20,28]，快门盘 [30,33]，输片鼓在板后 [-16,0]。"""
    s, dest, insert, joint = _start("projector")
    insert("frame", "proj_frame", [0, 0, 0])
    _run(s, "ground_component", {"component": "frame"})
    for role, x, y in (("drive_shaft", 0, 0), ("idler_shaft", 31.5, 0), ("shutter_shaft", 63, 0), ("sprocket_shaft", 0, -60)):
        insert(role, "proj_shaft", [x, y, -14])
        joint("revolute", f"{role}:cyl(d=10)", f"frame:cyl(d=10.4, near=[{x},{y}])", -14)
    for role, part, shaft, x, y, offset in (
            ("drive_gear", "proj_gear24", "drive_shaft", 0, 0, 24),
            ("idler_gear", "proj_idler18", "idler_shaft", 31.5, 0, 24),
            ("shutter_gear", "proj_gear24", "shutter_shaft", 63, 0, 24),
            ("drive_pinion", "proj_pinion16", "drive_shaft", 0, 0, 34),
            ("sprocket_gear", "proj_gear64", "sprocket_shaft", 0, -60, 34),
            ("shutter", "proj_shutter", "shutter_shaft", 63, 0, 44),
            ("sprocket", "proj_sprocket", "sprocket_shaft", 0, -60, -2)):
        insert(role, part, [x, y, -14 + offset])
        joint("fixed", f"{role}:cyl(d=10)", f"{shaft}:cyl(d=10)", offset)
    _run(s, "mesh_gears", {"a": "drive_gear", "b": "idler_gear", "teeth_a": 24, "teeth_b": 18, "module": 1.5})
    _run(s, "mesh_gears", {"a": "idler_gear", "b": "shutter_gear", "teeth_a": 18, "teeth_b": 24, "module": 1.5})
    _run(s, "mesh_gears", {"a": "drive_pinion", "b": "sprocket_gear", "teeth_a": 16, "teeth_b": 64, "module": 1.5})
    return _finish(s, dest, 1)


BUILDS = {"gearbox": gearbox, "engine": engine, "desk_lamp": desk_lamp, "umbrella": umbrella,
          "drill_gearbox": drill_gearbox, "sewing_machine": sewing_machine, "projector": projector}


def _register_bench() -> None:
    from eval.assembly_scratch.bench_cases import CASES, build_reference
    from eval.assembly_scratch.bench_cases2 import CASES2, build_reference2
    for name in CASES:
        BUILDS[name] = lambda name=name: build_reference(name)
    for name in CASES2:
        BUILDS[name] = lambda name=name: build_reference2(name)


_register_bench()


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    call("/api/close-all-documents")
    try:
        for name, build in BUILDS.items():
            if which in ("all", name):
                print(f"== {name}", build())
    finally:
        call("/api/close-all-documents")


if __name__ == "__main__":
    main()
