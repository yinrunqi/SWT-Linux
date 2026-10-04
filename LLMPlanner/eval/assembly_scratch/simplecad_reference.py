"""SimpleCADAPI 装配基线的阳性对照：手写三个测例的零件与约束（曲柄摇杆、偏置曲柄滑块、手摇绞盘），
产物目录与 eval_assembly_simplecad.py 相同，用 score_simplecad.py 打分，检查 STEP → SolidWorks 桥接与评分链路。
手摇绞盘的齿轮用节圆略小的圆盘代替（只检查齿轮配合的桥接，零件 rubric 不会通过）。

    venv-scad/bin/python -X utf8 eval/assembly_scratch/simplecad_reference.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.eval_assembly_simplecad import _write_json, extract, grade_part, load_case, prepare_assembly  # noqa: E402

OUT = ROOT / "eval" / "artifacts" / "assembly_simplecad_reference"

PART_HEADER = '''
import simplecadapi as scad

def stadium(width, length, thick, holes):
    r = width / 2
    body = scad.union_rsolid(
        scad.make_box_rsolid(length, width, thick, bottom_face_center=(length / 2, 0, 0)),
        scad.make_cylinder_rsolid(r, thick),
        scad.make_cylinder_rsolid(r, thick, bottom_face_center=(length, 0, 0)))
    for x, d in holes:
        body = scad.cut_rsolid(body, scad.make_cylinder_rsolid(d / 2, thick + 2, bottom_face_center=(x, 0, -1)))
    return body

def plate(x0, x1, y0, y1, thick, holes):
    body = scad.make_box_rsolid(x1 - x0, y1 - y0, thick, bottom_face_center=((x0 + x1) / 2, (y0 + y1) / 2, 0))
    for x, y, d in holes:
        body = scad.cut_rsolid(body, scad.make_cylinder_rsolid(d / 2, thick + 2, bottom_face_center=(x, y, -1)))
    return body

def disc(r, thick, bore):
    return scad.cut_rsolid(scad.make_cylinder_rsolid(r, thick),
                           scad.make_cylinder_rsolid(bore / 2, thick + 2, bottom_face_center=(0, 0, -1)))

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    return BODY

res = build()
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
'''

PARTS = {
    "crank_rocker": {
        "cr_frame": "plate(-30, 110, -30, 90, 8, [(0, 0, 10), (80, 0, 6)])",
        "cr_shaft": "scad.make_cylinder_rsolid(5, 24)",
        "cr_crank": "stadium(16, 20, 6, [(0, 10), (20, 6)])",
        "cr_pin12": "scad.make_cylinder_rsolid(3, 12)",
        "cr_coupler": "stadium(12, 70, 6, [(0, 6), (70, 6)])",
        "cr_rocker": "stadium(12, 50, 6, [(0, 6), (50, 6)])",
        "cr_pin26": "scad.make_cylinder_rsolid(3, 26)",
    },
    "offset_slider_crank": {
        "os_frame": "plate(-30, 110, -30, 40, 8, [(0, 0, 10)])",
        "os_shaft": "scad.make_cylinder_rsolid(5, 24)",
        "os_crank": "stadium(16, 15, 6, [(0, 10), (15, 6)])",
        "os_pin12": "scad.make_cylinder_rsolid(3, 12)",
        "os_rod": "stadium(12, 60, 6, [(0, 6), (60, 6)])",
        "os_slider": "plate(-12, 12, -8, 8, 6, [(0, 0, 6)])",
    },
    "hand_winch": {
        "hw_frame": "plate(-30, 80, -40, 40, 8, [(0, 0, 10), (48, 0, 10)])",
        "hw_handle_shaft": "scad.make_cylinder_rsolid(5, 30)",
        "hw_drum_shaft": "scad.make_cylinder_rsolid(5, 56)",
        "hw_pinion": "disc(11.5, 8, 10)",
        "hw_gear": "disc(35.5, 8, 10)",
        "hw_handle": "stadium(14, 40, 6, [(0, 10), (40, 6)])",
        "hw_knob": "scad.make_cylinder_rsolid(3, 30)",
        "hw_drum": "disc(20, 40, 10)",
    },
}

ASM_HEADER = '''
import math
import simplecadapi as scad
from scad_parts import ROLES, load_part_solid, part_inputs

P = scad.make_placement_rplacement
R = scad.ConnectorRef

def rz(origin, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return P(tuple(origin), x_axis=(c, s, 0.0), y_axis=(-s, c, 0.0))

def make(pid, connectors):
    @scad.part(id=pid, cache="off", inputs=part_inputs(pid))
    def build():
        part = scad.make_part_rpart(pid, load_part_solid(pid))
        for cid, at in connectors.items():
            frame = P(at) if isinstance(at, tuple) else at
            part = scad.add_connector_rpart(part, scad.make_placement_connector_rconnector(cid, frame))
        return part
    return build()
'''

ASM = {
    "crank_rocker": '''
th = 60.0
B = (20 * math.cos(math.radians(th)), 20 * math.sin(math.radians(th)))
dx, dy = 80 - B[0], -B[1]
d = math.hypot(dx, dy)
a = (70 ** 2 - 50 ** 2 + d * d) / (2 * d)
h = math.sqrt(70 ** 2 - a * a)
ex, ey = dx / d, dy / d
C = (B[0] + a * ex - h * ey, B[1] + a * ey + h * ex)
if C[1] < 0:
    C = (B[0] + a * ex + h * ey, B[1] + a * ey - h * ex)
coupler_deg = math.degrees(math.atan2(C[1] - B[1], C[0] - B[0]))
rocker_deg = math.degrees(math.atan2(C[1], C[0] - 80))
defs = {
    "cr_frame": make("cr_frame", {"bore": (0, 0, 0), "rhole": (80, 0, 0)}),
    "cr_shaft": make("cr_shaft", {"axis": (0, 0, 10), "c8": (0, 0, 18)}),
    "cr_crank": make("cr_crank", {"bore": (0, 0, 0), "pin": (20, 0, 0)}),
    "cr_pin12": make("cr_pin12", {"base": (0, 0, 0), "at6": (0, 0, 6)}),
    "cr_coupler": make("cr_coupler", {"e0": (0, 0, 0), "e1": (70, 0, 0)}),
    "cr_rocker": make("cr_rocker", {"e0": (0, 0, 0), "e1": (50, 0, 0)}),
    "cr_pin26": make("cr_pin26", {"base": (0, 0, 0), "at20": (0, 0, 20)}),
}
poses = {
    "frame": rz((0, 0, 0), 0), "shaft": rz((0, 0, -10), th), "crank": rz((0, 0, 8), th),
    "crank_pin": rz((B[0], B[1], 8), th), "coupler": rz((B[0], B[1], 14), coupler_deg),
    "coupler_pin": rz((C[0], C[1], 14), coupler_deg), "rocker": rz((80, 0, 20), rocker_deg),
    "rocker_pin": rz((80, 0, 0), 0),
}
joints = [
    ("j_shaft", "revolute", ("frame", "bore"), ("shaft", "axis")),
    ("f_crank", "fixed", ("shaft", "c8"), ("crank", "bore")),
    ("f_cpin", "fixed", ("crank", "pin"), ("crank_pin", "base")),
    ("j_coupler", "revolute", ("crank_pin", "at6"), ("coupler", "e0")),
    ("f_kpin", "fixed", ("coupler", "e1"), ("coupler_pin", "base")),
    ("f_rpin", "fixed", ("frame", "rhole"), ("rocker_pin", "base")),
    ("j_rocker", "revolute", ("rocker_pin", "at20"), ("rocker", "e0")),
    ("j_loop", "revolute", ("coupler_pin", "at6"), ("rocker", "e1")),
]
''',
    "offset_slider_crank": '''
th = 60.0
B = (15 * math.cos(math.radians(th)), 15 * math.sin(math.radians(th)))
C = (B[0] + math.sqrt(60 ** 2 - (10 - B[1]) ** 2), 10.0)
rod_deg = math.degrees(math.atan2(C[1] - B[1], C[0] - B[0]))
guide = dict(x_axis=(0, 1, 0), y_axis=(0, 0, 1))   # Z 轴沿 +X
defs = {
    "os_frame": make("os_frame", {"bore": (0, 0, 0), "guide": P((0, 10, 20), **guide)}),
    "os_shaft": make("os_shaft", {"axis": (0, 0, 10), "c8": (0, 0, 18)}),
    "os_crank": make("os_crank", {"bore": (0, 0, 0), "pin": (15, 0, 0)}),
    "os_pin12": make("os_pin12", {"base": (0, 0, 0), "at6": (0, 0, 6)}),
    "os_rod": make("os_rod", {"e0": (0, 0, 0), "e1": (60, 0, 0)}),
    "os_slider": make("os_slider", {"bore": (0, 0, 0), "guide": P((0, 0, 0), **guide)}),
}
poses = {
    "frame": rz((0, 0, 0), 0), "shaft": rz((0, 0, -10), th), "crank": rz((0, 0, 8), th),
    "crank_pin": rz((B[0], B[1], 8), th), "rod": rz((B[0], B[1], 14), rod_deg),
    "wrist_pin": rz((C[0], C[1], 14), rod_deg), "slider": rz((C[0], C[1], 20), 0),
}
joints = [
    ("j_shaft", "revolute", ("frame", "bore"), ("shaft", "axis")),
    ("f_crank", "fixed", ("shaft", "c8"), ("crank", "bore")),
    ("f_cpin", "fixed", ("crank", "pin"), ("crank_pin", "base")),
    ("j_rod", "revolute", ("crank_pin", "at6"), ("rod", "e0")),
    ("f_wpin", "fixed", ("rod", "e1"), ("wrist_pin", "base")),
    ("j_slider", "revolute", ("wrist_pin", "at6"), ("slider", "bore")),
    ("p_slider", "prismatic", ("frame", "guide"), ("slider", "guide")),
]
''',
    "hand_winch": '''
defs = {
    "hw_frame": make("hw_frame", {"b0": (0, 0, 0), "b1": (48, 0, 0)}),
    "hw_handle_shaft": make("hw_handle_shaft", {"axis": (0, 0, 14), "pin": (0, 0, 22), "hdl": (0, 0, 0)}),
    "hw_drum_shaft": make("hw_drum_shaft", {"axis": (0, 0, 0), "gear": (0, 0, 8), "drum": (0, 0, 16)}),
    "hw_pinion": make("hw_pinion", {"bore": (0, 0, 0)}),
    "hw_gear": make("hw_gear", {"bore": (0, 0, 0)}),
    "hw_handle": make("hw_handle", {"bore": (0, 0, 0), "knob": (40, 0, 0)}),
    "hw_knob": make("hw_knob", {"top": (0, 0, 30)}),
    "hw_drum": make("hw_drum", {"bore": (0, 0, 0)}),
}
poses = {
    "frame": rz((0, 0, 0), 0), "handle_shaft": rz((0, 0, -14), 0), "pinion": rz((0, 0, 8), 0),
    "handle": rz((0, 0, -14), 0), "knob": rz((40, 0, -44), 0),
    "drum_shaft": rz((48, 0, 0), 0), "gear": rz((48, 0, 8), 0), "drum": rz((48, 0, 16), 0),
}
joints = [
    ("j_hs", "revolute", ("frame", "b0"), ("handle_shaft", "axis")),
    ("f_pin", "fixed", ("handle_shaft", "pin"), ("pinion", "bore")),
    ("f_hdl", "fixed", ("handle_shaft", "hdl"), ("handle", "bore")),
    ("f_knob", "fixed", ("handle", "knob"), ("knob", "top")),
    ("j_ds", "revolute", ("frame", "b1"), ("drum_shaft", "axis")),
    ("f_gear", "fixed", ("drum_shaft", "gear"), ("gear", "bore")),
    ("f_drum", "fixed", ("drum_shaft", "drum"), ("drum", "bore")),
    ("g", "gear", ("handle_shaft", "axis"), ("drum_shaft", "axis")),
]
''',
}

ASM_FOOTER = '''
@scad.assemble(id="asm", definitions=tuple(defs.values()), cache="off")
def build_assembly():
    asm = scad.make_assembly_rassembly("asm")
    for role, pid in ROLES.items():
        asm = scad.add_component_rassembly(asm, defs[pid].value, role, poses[role])
    asm = scad.ground_component_rassembly(asm, "frame")
    for cid, kind, a, b in joints:
        ra, rb = R(*a), R(*b)
        if kind == "revolute":
            asm = scad.add_revolute_constraint_rassembly(asm, cid, ra, rb)
        elif kind == "prismatic":
            asm = scad.add_prismatic_constraint_rassembly(asm, cid, ra, rb)
        elif kind == "fixed":
            asm = scad.add_fixed_constraint_rassembly(asm, cid, ra, rb)
        elif kind == "gear":
            asm = scad.add_gear_constraint_rassembly(asm, cid, ra, rb, 12.0, 36.0)
    return scad.solve_assembly_constraints_rassembly(asm)

res = build_assembly()
print(res.solve_report)
scad.capture(res, "assembly.scadpkg", include_scene=False)
'''


def run_py(code: str, work: Path) -> None:
    (work / "build.py").write_text(code, encoding="utf-8")
    p = subprocess.run([sys.executable, "-X", "utf8", "build.py"], cwd=work, capture_output=True, text=True,
                       env=dict(os.environ, SIMPLECAD_CACHE_MODE="off",
                                SIMPLECAD_CACHE_DIR=str(work / ".simplecad_cache")))
    if p.returncode != 0:
        raise RuntimeError(f"{work}: {p.stderr[-2000:]}")


def main() -> None:
    runs = []
    for name, bodies in PARTS.items():
        case = load_case(name)
        out = OUT / case["case_id"] / "r1"
        if out.exists():
            shutil.rmtree(out)
        (out / "parts").mkdir(parents=True)
        parts = []
        for pid, body in bodies.items():
            work = out / "parts" / pid
            work.mkdir()
            run_py(PART_HEADER.replace("BODY", body), work)
            shutil.copyfile(work / "model.step", out / "parts" / f"{pid}.step")
            grade = grade_part(out / "parts" / f"{pid}.step", pid)
            parts.append({"part_id": pid, "status": "reference", "step": f"parts/{pid}.step",
                          "accepted": bool(grade.get("accepted")), "score": grade.get("score"), "grade": grade})
            print(name, pid, grade.get("accepted"), grade.get("score"), flush=True)
        work, missing = prepare_assembly(case, parts, out)
        run_py(ASM_HEADER + ASM[name] + ASM_FOOTER, work)
        ok, detail = extract(work, out / "assembly_export.json")
        print(name, "export", ok, detail, flush=True)
        run = {"case_id": case["case_id"], "case_name": name, "repeat": 1, "condition": "gen", "model": "reference",
               "parts": parts, "assembly": {"status": "reference", "export_ok": ok, "export_detail": detail},
               "part_pass_rate": sum(1 for p in parts if p["accepted"]) / len(parts)}
        _write_json(out / "run.json", run)
        runs.append(run)
    _write_json(ROOT / "eval" / "assembly_simplecad_reference.json",
                {"model": "reference", "artifacts": str(OUT.relative_to(ROOT)), "runs": runs})


if __name__ == "__main__":
    main()
