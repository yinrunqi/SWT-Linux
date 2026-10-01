"""用宏工具按规格手工搭两个试点的正确装配体，作为装配 rubric 与运动学评测的阳性对照。

    python eval/assembly_scratch/build_reference_assemblies.py gearbox|engine|all

依赖 build_standard_parts.py 生成的 gold_parts/。输出 gold_parts/reference_<case>.SLDASM（不入库）。
评测器对这两个装配体应给出满分；若不是，先查评测器而不是查 agent。
"""

from __future__ import annotations

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


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    call("/api/close-all-documents")
    try:
        if which in ("gearbox", "all"):
            print("== gearbox", gearbox())
        if which in ("engine", "all"):
            print("== engine", engine())
    finally:
        call("/api/close-all-documents")


if __name__ == "__main__":
    main()
