"""机械配合实测：在 SolidWorks 里逐一建立槽口、齿条小齿轮、螺旋、线性耦合、锁定、
同心锁定旋转、距离限位与球面同心配合，并用 Motion Study 量运动学比值。

    python eval/assembly_scratch/smoke_mechanical_mates.py [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.build_standard_parts import call, close, cylinder, gear, plate  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402


def slot_plate(path: Path) -> None:
    """sketch/slot 退化成矩形，槽口配合认不出；改为矩形切除 + 两端圆孔切除，得到带半圆柱端面的真槽。"""
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/rectangle", {"x1": -0.04, "y1": -0.02, "x2": 0.04, "y2": 0.02})
    call("/api/feature/extrusion", {"depth": 0.008})
    top = {"rx": 0.035, "ry": 0.015, "rz": 0.05, "dx": 0, "dy": 0, "dz": -1}
    call("/api/select-face-and-sketch", top)
    call("/api/sketch/rectangle", {"x1": -0.016, "y1": -0.004, "x2": 0.016, "y2": 0.004})
    call("/api/feature/cut-extrusion", {"depth": 0.02})
    call("/api/select-face-and-sketch", top)
    for cx in (-0.016, 0.016):
        call("/api/sketch/circle", {"cx": cx, "cy": 0, "radius": 0.004})
    call("/api/feature/cut-extrusion", {"depth": 0.02})
    call("/api/save-part-as", {"savePath": str(path)})
    close()


def ball(path: Path, radius: float) -> None:
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/centerline", {"x1": 0, "y1": -radius * 1.5, "x2": 0, "y2": radius * 1.5})
    call("/api/sketch/arc", {"cx": 0, "cy": 0, "startX": 0, "startY": -radius, "endX": 0, "endY": radius,
                             "direction": 1})
    call("/api/sketch/line", {"x1": 0, "y1": radius, "x2": 0, "y2": -radius})
    call("/api/feature/revolve", {"angle": 360})
    call("/api/save-part-as", {"savePath": str(path)})
    close()


def build(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    p = {name: out / f"{name}.SLDPRT" for name in (
        "base", "pinion", "shaft", "rack", "screw", "nut", "slotplate", "pin", "ball")}
    plate(p["base"], -0.08, -0.04, 0.08, 0.04, [(0, 0, 0.0052)], 0.010)
    gear(p["pinion"], 12, 0.003, 0.010, 0.005)
    cylinder(p["shaft"], 0.005, 0.050)
    plate(p["rack"], -0.06, 0.0, 0.06, 0.010, [], 0.010)
    cylinder(p["screw"], 0.005, 0.080)
    cylinder(p["nut"], 0.012, 0.010, bore=0.005)
    slot_plate(p["slotplate"])
    cylinder(p["pin"], 0.004, 0.020)
    ball(p["ball"], 0.008)
    return p


def longest_line(session: AssemblySession, component: str, axis: int) -> str:
    edges = session.asmwf("component-edges", {"component": component, "kind": "line", "maxEdges": 400})["edges"]
    along = [e for e in edges if e.get("direction") and abs(e["direction"][axis]) > 0.99]
    best = max(along, key=lambda e: e["length"])
    return f"edge:{best['index']}"


def mate(session: AssemblySession, kind: str, a: tuple, b: tuple, **kw) -> dict:
    body = {"type": kind, "a": {"component": a[0], "ref": a[1]}, "b": {"component": b[0], "ref": b[1]}, **kw}
    result = session.asmwf("mate", body)
    print(f"  {kind:14s} OK  {result}")
    return result


def sample(session: AssemblySession, driver: str, turns: float = 1.0, frames: int = 49) -> list[dict]:
    """Animation 的马达并不严格按 rpm 转（实测 10rpm 约转成 47rpm），
    所以比值一律按驱动件实际转角算，帧要足够密才能无歧义地展开角度。"""
    rpm = 10.0
    duration = turns * 60.0 / rpm
    session.asmwf("motion-drive", {"component": driver, "ref": "bore:auto", "rpm": rpm, "durationSeconds": duration})
    times = [duration * i / (frames - 1) for i in range(frames)]
    data = session.asmwf("motion-sample", {"times": times, "interference": False})
    session.asmwf("motion-delete")
    return data["frames"]


def travel_mm(frames: list[dict], name: str, axis: int) -> float:
    return (float(frames[-1]["transforms"][name][9 + axis]) - float(frames[0]["transforms"][name][9 + axis])) * 1000


def turns_about_z(frames: list[dict], name: str) -> float:
    total, last = 0.0, None
    for frame in frames:
        t = frame["transforms"][name]
        angle = math.degrees(math.atan2(float(t[1]), float(t[0])))
        if last is not None:
            total += (angle - last + 180.0) % 360.0 - 180.0
        last = angle
    return total / 360.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "asm_mech_smoke"))
    parser.add_argument("--reuse", action="store_true", help="复用 --out 下已建好的零件")
    args = parser.parse_args()
    out = Path(args.out)
    if args.reuse:
        parts = {p.stem: p for p in out.glob("*.SLDPRT")}
        close()
    else:
        parts = build(out)
    report: dict[str, object] = {}
    s = AssemblySession()
    try:
        run(s, parts, out, report, time.strftime("%H%M%S"))
    finally:
        call("/api/close-all-documents")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    (out / "smoke_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


def run(s: AssemblySession, parts: dict[str, Path], out: Path, report: dict, stamp: str) -> None:

    print("== 装配 1：齿条小齿轮、线性耦合、锁定、距离限位")
    s.start_assembly(str(out / f"rack_{stamp}.SLDASM"))
    s.insert_component({"part": str(parts["base"]), "name": "base", "at": [0, 0, 0]})
    s.ground_component({"component": "base"})
    s.insert_component({"part": str(parts["shaft"]), "name": "shaft", "at": [0, 0, 0]})
    s.insert_component({"part": str(parts["pinion"]), "name": "pinion", "at": [0, 0, 15]})
    s.insert_component({"part": str(parts["rack"]), "name": "rack", "at": [0, 25, 15]})
    s.insert_component({"part": str(parts["rack"]), "name": "rack2", "at": [0, -60, 15]})
    base, shaft, pinion, rack, rack2 = (s.aliases[k] for k in ("base", "shaft", "pinion", "rack", "rack2"))
    base_bore = s.resolve_ref("base:cyl(d=10.4)")[1]
    shaft_cyl = s.resolve_ref("shaft:cyl(d=10)")[1]
    mate(s, "concentric", (shaft, shaft_cyl), (base, base_bore))
    mate(s, "coincident", (shaft, "plane:front"), (base, "plane:front"), align="aligned")
    pinion_bore = s.resolve_ref("pinion:cyl(i=0)")[1]
    mate(s, "lock", (pinion, "component"), (shaft, "component"))
    for name, offset in ((rack, 0.018), (rack2, 0.060)):
        mate(s, "coincident", (name, "plane:front"), (base, "plane:front"), align="aligned")
        mate(s, "distance", (name, "plane:top"), (base, "plane:top"), distance=offset)
    edge = longest_line(s, rack, 0)
    rp = mate(s, "rackpinion", (rack, edge), (pinion, pinion_bore), pitchDiameter=0.036)
    edge2 = longest_line(s, rack2, 0)
    lc = mate(s, "linearcoupler", (rack, edge), (rack2, edge2), ratioNumerator=1, ratioDenominator=2)
    frames = sample(s, shaft)
    t1, t2 = travel_mm(frames, rack, 0), travel_mm(frames, rack2, 0)
    turns = turns_about_z(frames, pinion)
    report["rackpinion"] = {"pinion_turns": round(turns, 4), "rack_travel_mm": round(t1, 3),
                            "travel_per_rev_mm": round(t1 / turns, 3) if turns else None,
                            "expected_per_rev_mm": round(math.pi * 36, 3)}
    report["linearcoupler"] = {"rack2_travel_mm": round(t2, 3), "ratio": round(t2 / t1, 4) if t1 else None}
    print("  运动:", report["rackpinion"], report["linearcoupler"])
    s.asmwf("delete-mate", {"name": lc["name"]})
    s.asmwf("delete-mate", {"name": rp["name"]})
    lim = mate(s, "distance", (rack, "plane:right"), (base, "plane:right"),
               distance=0.0, distanceMin=0.0, distanceMax=0.03)
    report["distance_limit"] = {k: v for k, v in lim.items() if k.startswith("distance")}
    report["mates_1"] = s.asmwf("mates")["mates"]
    call("/api/close-all-documents")

    print("== 装配 2：螺旋、槽口、同心锁定旋转、球面")
    s.start_assembly(str(out / f"screw_{stamp}.SLDASM"))
    s.insert_component({"part": str(parts["screw"]), "name": "screw", "at": [0, 0, 0]})
    s.ground_component({"component": "screw"})
    s.insert_component({"part": str(parts["nut"]), "name": "nut", "at": [0, 0, 20]})
    screw, nut = s.aliases["screw"], s.aliases["nut"]
    screw_cyl = s.resolve_ref("screw:cyl(d=10)")[1]
    nut_bore = s.resolve_ref("nut:cyl(d=10)")[1]
    mate(s, "screw", (screw, screw_cyl), (nut, nut_bore), lead=0.002)
    frames = sample(s, nut)
    nut_turns, nut_travel = turns_about_z(frames, nut), travel_mm(frames, nut, 2)
    report["screw"] = {"nut_turns": round(nut_turns, 4), "nut_travel_z_mm": round(nut_travel, 4),
                       "lead_mm": round(nut_travel / nut_turns, 4) if nut_turns else None, "expected_lead_mm": 2.0}
    print("  运动:", report["screw"])

    s.insert_component({"part": str(parts["slotplate"]), "name": "slotplate", "at": [100, 0, 0]})
    s.insert_component({"part": str(parts["pin"]), "name": "pin", "at": [100, 0, 0]})
    s.ground_component({"component": "slotplate"})
    plate_sw, pin_sw = s.aliases["slotplate"], s.aliases["pin"]
    slot_faces = [f for f in s.faces(plate_sw) if f["surfaceType"] == "cylinder"]
    pin_cyl = s.resolve_ref("pin:cyl(d=8)")[1]
    mate(s, "slot", (plate_sw, f"face:{slot_faces[0]['index']}"), (pin_sw, pin_cyl), slotConstraint="free")
    mate(s, "coincident", (pin_sw, "plane:front"), (plate_sw, "plane:front"), align="aligned")

    s.insert_component({"part": str(parts["shaft"]), "name": "axle", "at": [-100, 0, 0]})
    s.insert_component({"part": str(parts["nut"]), "name": "hub", "at": [-100, 0, 10]})
    axle, hub = s.aliases["axle"], s.aliases["hub"]
    s.ground_component({"component": "axle"})
    concentric = mate(s, "concentric", (hub, s.resolve_ref("hub:cyl(d=10)")[1]),
                      (axle, s.resolve_ref("axle:cyl(d=10)")[1]), lockRotation=True)
    report["concentric_lock_rotation"] = concentric.get("lockRotation")

    s.insert_component({"part": str(parts["ball"]), "name": "ball_a", "at": [0, 100, 0]})
    s.insert_component({"part": str(parts["ball"]), "name": "ball_b", "at": [0, 120, 0]})
    ba, bb = s.aliases["ball_a"], s.aliases["ball_b"]
    s.ground_component({"component": "ball_a"})
    spheres = [f for f in s.asmwf("component-faces", {"component": ba})["faces"] if f["surfaceType"] == "sphere"]
    spheres_b = [f for f in s.asmwf("component-faces", {"component": bb})["faces"] if f["surfaceType"] == "sphere"]
    mate(s, "concentric", (bb, f"face:{spheres_b[0]['index']}"), (ba, f"face:{spheres[0]['index']}"))
    report["sphere_faces"] = len(spheres)
    report["mates_2"] = s.asmwf("mates")["mates"]
    report["components_2"] = [
        {"name": c["name"], "constrainedStatus": c["constrainedStatus"]}
        for c in s.asmwf("components")["components"]
    ]


if __name__ == "__main__":
    main()
