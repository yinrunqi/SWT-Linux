"""按试点规格用 C# API 确定性建造标准零件（与 L1 规格一致）。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from planner.gears import generate_internal_cavity, generate_involute_outline

BASE = "http://localhost:5100"
HTTP = requests.Session()


def call(path: str, body: dict | None = None, timeout: int = 300) -> dict:
    data = HTTP.post(f"{BASE}{path}", json=body or {}, timeout=timeout).json()
    if not data.get("success"):
        raise RuntimeError(f"{path}: {data.get('message')}")
    return data.get("result", data)


def close() -> None:
    call("/api/close-all-documents")


def export_pair(sldprt: Path) -> Path:
    step = sldprt.with_suffix(".step")
    call("/api/export-step", {"stepPath": str(step)})
    return step


def plate(path: Path, x0, y0, x1, y1, holes, thick) -> None:
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/rectangle", {"x1": x0, "y1": y0, "x2": x1, "y2": y1})
    for cx, cy, r in holes:
        call("/api/sketch/circle", {"noInference": True, "cx": cx, "cy": cy, "radius": r})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def cylinder(path: Path, radius, length, bore=0.0, cx=0.0) -> None:
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    if bore:
        call("/api/sketch/circle", {"noInference": True, "cx": 0, "cy": 0, "radius": bore})
    call("/api/sketch/circle", {"noInference": True, "cx": cx, "cy": 0, "radius": radius})
    call("/api/feature/extrusion", {"depth": length})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def gear(path: Path, teeth: int, module_m: float, thick: float, bore: float) -> None:
    outline, _ = generate_involute_outline(tooth_count=teeth, module_m=module_m, pressure_angle_deg=30)
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"noInference": True, "cx": 0, "cy": 0, "radius": bore})
    call("/api/sketch/polyline", {
        "points": [{"x": x, "y": y} for x, y in outline[:-1]],
        "closed": True,
    }, timeout=300)
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def link(path: Path, length, width, hole_r, thick, holes=None) -> None:
    """两端圆头长条（半圆圆心在 x=0 与 x=length）。holes 缺省为两端各一个半径 hole_r 的孔；
    否则为 [(x, r), ...]。"""
    half = width / 2
    points = []
    for i in range(0, 13):
        a = -math.pi / 2 + math.pi * i / 12
        points.append((length + half * math.cos(a), half * math.sin(a)))
    for i in range(0, 13):
        a = math.pi / 2 + math.pi * i / 12
        points.append((half * math.cos(a), half * math.sin(a)))
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/polyline", {"points": [{"x": x, "y": y} for x, y in points], "closed": True})
    for x, r in holes or [(0, hole_r), (length, hole_r)]:
        call("/api/sketch/circle", {"noInference": True, "cx": x, "cy": 0, "radius": r})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def block(path: Path, sx, sy, sz, bore_r, cross_holes) -> None:
    """长方体 X∈±sx/2、Y∈±sy/2、Z∈±sz/2，沿 Z 的中心贯穿孔，再沿 Y 贯穿若干铰孔 [(x, z, r)]。"""
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/rectangle", {"x1": -sx / 2, "y1": -sy / 2, "x2": sx / 2, "y2": sy / 2})
    call("/api/sketch/circle", {"noInference": True, "cx": 0, "cy": 0, "radius": bore_r})
    call("/api/feature/extrusion", {"depth": sz, "midPlane": True})
    call("/api/select-plane", {"index": 1})
    for x, z, r in cross_holes:
        call("/api/sketch/circle", {"noInference": True, "cx": x, "cy": z, "radius": r})
    call("/api/feature/cut-extrusion", {"depth": 2 * sy + 0.01, "endCondition": "mid_plane"})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def disk(path: Path, radius, thick, holes) -> None:
    """圆盘（轴线 Z，从 Z=0 拉伸），holes: [(cx, cy, r)]。"""
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"noInference": True, "cx": 0, "cy": 0, "radius": radius})
    for cx, cy, r in holes:
        call("/api/sketch/circle", {"noInference": True, "cx": cx, "cy": cy, "radius": r})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def ring_gear(path: Path, teeth: int, module_m: float, thick: float, outer_r: float) -> None:
    cavity, _ = generate_internal_cavity(tooth_count=teeth, module_m=module_m, pressure_angle_deg=30)
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"noInference": True, "cx": 0, "cy": 0, "radius": outer_r})
    call("/api/sketch/polyline", {"points": [{"x": x, "y": y} for x, y in cavity[:-1]], "closed": True},
         timeout=300)
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def build_gearbox(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    m, t, bore = 0.003, 0.010, 0.005
    paths = {
        "housing": out / "housing.SLDPRT",
        "input_shaft": out / "input_shaft.SLDPRT",
        "lay_shaft": out / "lay_shaft.SLDPRT",
        "output_shaft": out / "output_shaft.SLDPRT",
        "input_pinion": out / "input_pinion.SLDPRT",
        "lay_wheel": out / "lay_wheel.SLDPRT",
        "lay_pinion": out / "lay_pinion.SLDPRT",
        "output_wheel": out / "output_wheel.SLDPRT",
    }
    plate(paths["housing"], -0.030, -0.040, 0.160, 0.040,
          [(0, 0, 0.0052), (0.054, 0, 0.0052), (0.126, 0, 0.0052)], 0.010)
    for key in ("input_shaft", "lay_shaft", "output_shaft"):
        cylinder(paths[key], 0.005, 0.050)
    gear(paths["input_pinion"], 12, m, t, bore)
    gear(paths["lay_wheel"], 24, m, t, bore)
    gear(paths["lay_pinion"], 12, m, t, bore)
    gear(paths["output_wheel"], 36, m, t, bore)
    return paths


def build_engine(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    m, t, bore = 0.003, 0.010, 0.005
    paths = {
        "frame": out / "frame.SLDPRT",
        "crank": out / "crank.SLDPRT",
        "rod": out / "rod.SLDPRT",
        "piston": out / "piston.SLDPRT",
        "crank_shaft": out / "crank_shaft.SLDPRT",
        "crank_pin": out / "crank_pin.SLDPRT",
        "piston_pin": out / "piston_pin.SLDPRT",
        "flywheel": out / "flywheel.SLDPRT",
        "crank_gear": out / "crank_gear.SLDPRT",
        "cam_gear": out / "cam_gear.SLDPRT",
        "camshaft": out / "camshaft.SLDPRT",
        "cam": out / "cam.SLDPRT",
    }
    plate(paths["frame"], -0.050, -0.050, 0.050, 0.160, [(0, 0, 0.0052)], 0.008)
    link(paths["crank"], 0.025, 0.016, 0.005, 0.006)
    link(paths["rod"], 0.090, 0.014, 0.005, 0.006)
    cylinder(paths["piston"], 0.012, 0.030, bore=0.005)
    for key in ("crank_shaft", "crank_pin", "piston_pin"):
        cylinder(paths[key], 0.005, 0.040)
    cylinder(paths["flywheel"], 0.030, 0.008, bore=0.005)
    gear(paths["crank_gear"], 16, m, t, bore)
    gear(paths["cam_gear"], 32, m, t, bore)
    cylinder(paths["camshaft"], 0.005, 0.050)
    cylinder(paths["cam"], 0.014, 0.008, bore=0.005, cx=0.004)
    return paths


# 以下各测例的零件按零件 id 命名（同一 id 可被多个角色复用），尺寸与对应 yaml 的零件提示词一致。

def build_desk_lamp(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    p = {k: out / f"{k}.SLDPRT" for k in ("lamp_base", "lamp_lower_arm", "lamp_upper_arm", "lamp_shade", "lamp_pin")}
    plate(p["lamp_base"], -0.060, -0.010, 0.060, 0.070, [(0, 0.060, 0.0026)], 0.008)
    link(p["lamp_lower_arm"], 0.120, 0.012, 0.0025, 0.005)
    link(p["lamp_upper_arm"], 0.105, 0.012, 0.0025, 0.005)
    plate(p["lamp_shade"], -0.007, -0.008, 0.045, 0.008, [(0, 0, 0.0025)], 0.005)
    cylinder(p["lamp_pin"], 0.0025, 0.010)
    return p


def build_umbrella(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    p = {k: out / f"{k}.SLDPRT" for k in ("umb_shaft", "umb_hub", "umb_runner", "umb_rib", "umb_stretcher")}
    cylinder(p["umb_shaft"], 0.005, 0.400)
    block(p["umb_hub"], 0.052, 0.016, 0.020, 0.005, [(0.018, 0, 0.002), (-0.018, 0, 0.002)])
    block(p["umb_runner"], 0.046, 0.024, 0.016, 0.0052, [(0.015, 0, 0.002), (-0.015, 0, 0.002)])
    link(p["umb_rib"], 0.300, 0.008, 0.002, 0.004, holes=[(0, 0.002), (0.120, 0.002)])
    link(p["umb_stretcher"], 0.160, 0.008, 0.002, 0.004)
    return p


def build_drill_gearbox(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    names = ("drill_housing", "drill_ring48", "drill_ring45", "drill_sun12", "drill_planet18", "drill_gear15",
             "drill_carrier", "drill_shaft")
    p = {k: out / f"{k}.SLDPRT" for k in names}
    m = 0.0012
    plate(p["drill_housing"], -0.040, -0.040, 0.040, 0.040, [(0, 0, 0.0022)], 0.006)
    ring_gear(p["drill_ring48"], 48, m, 0.010, 0.035)
    ring_gear(p["drill_ring45"], 45, m, 0.010, 0.033)
    gear(p["drill_sun12"], 12, m, 0.010, 0.002)
    gear(p["drill_planet18"], 18, m, 0.010, 0.002)
    gear(p["drill_gear15"], 15, m, 0.010, 0.002)
    disk(p["drill_carrier"], 0.024, 0.004, [(0, 0, 0.002), (0.018, 0, 0.002)])
    cylinder(p["drill_shaft"], 0.002, 0.022)
    return p


def build_sewing_machine(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    names = ("sew_frame", "sew_shaft", "sew_crank", "sew_pin", "sew_rod", "sew_needle_bar", "sew_main_gear",
             "sew_hook_gear", "sew_hook", "sew_feed_crank", "sew_coupler", "sew_rocker", "sew_rocker_pin")
    p = {k: out / f"{k}.SLDPRT" for k in names}
    plate(p["sew_frame"], -0.080, -0.040, 0.070, 0.140,
          [(0, 0, 0.0052), (-0.045, 0, 0.0052), (0.036, -0.027, 0.0032)], 0.008)
    cylinder(p["sew_shaft"], 0.005, 0.032)
    link(p["sew_crank"], 0.018, 0.016, 0.005, 0.006)
    cylinder(p["sew_pin"], 0.005, 0.020)
    link(p["sew_rod"], 0.065, 0.014, 0.005, 0.006)
    cylinder(p["sew_needle_bar"], 0.010, 0.030, bore=0.005)
    gear(p["sew_main_gear"], 40, 0.0015, 0.008, 0.005)
    gear(p["sew_hook_gear"], 20, 0.0015, 0.008, 0.005)
    cylinder(p["sew_hook"], 0.020, 0.008, bore=0.005)
    link(p["sew_feed_crank"], 0.012, 0.014, 0.005, 0.006, holes=[(0, 0.005), (0.012, 0.003)])
    link(p["sew_coupler"], 0.050, 0.010, 0.003, 0.006)
    link(p["sew_rocker"], 0.036, 0.010, 0.003, 0.006)
    cylinder(p["sew_rocker_pin"], 0.003, 0.038)
    return p


def build_projector(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    close()
    names = ("proj_frame", "proj_shaft", "proj_gear24", "proj_idler18", "proj_pinion16", "proj_gear64",
             "proj_shutter", "proj_sprocket")
    p = {k: out / f"{k}.SLDPRT" for k in names}
    plate(p["proj_frame"], -0.060, -0.120, 0.090, 0.040,
          [(0, 0, 0.0052), (0.0315, 0, 0.0052), (0.063, 0, 0.0052), (0, -0.060, 0.0052)], 0.008)
    cylinder(p["proj_shaft"], 0.005, 0.050)
    gear(p["proj_gear24"], 24, 0.0015, 0.008, 0.005)
    gear(p["proj_idler18"], 18, 0.0015, 0.008, 0.005)
    gear(p["proj_pinion16"], 16, 0.0015, 0.008, 0.005)
    gear(p["proj_gear64"], 64, 0.0015, 0.008, 0.005)
    disk(p["proj_shutter"], 0.025, 0.003, [(0, 0, 0.005), (0.015, 0, 0.006), (-0.015, 0, 0.006)])
    cylinder(p["proj_sprocket"], 0.015, 0.016, bore=0.005)
    return p


BUILDERS = {
    "gearbox": build_gearbox,
    "engine": build_engine,
    "desk_lamp": build_desk_lamp,
    "umbrella": build_umbrella,
    "drill_gearbox": build_drill_gearbox,
    "sewing_machine": build_sewing_machine,
    "projector": build_projector,
}


def _bench_builder(name: str):
    def build(out: Path) -> dict[str, Path]:
        from eval.assembly_scratch.bench_cases import CASES, build_part
        out.mkdir(parents=True, exist_ok=True)
        close()
        p = {pid: out / f"{pid}.SLDPRT" for pid in CASES[name].parts}
        for pid, spec in CASES[name].parts.items():
            build_part(p[pid], spec)
        return p
    return build


def _register_bench() -> None:
    from eval.assembly_scratch.bench_cases import CASES
    for name in CASES:
        BUILDERS[name] = _bench_builder(name)


_register_bench()


def main() -> None:
    here = Path(__file__).resolve().parent
    dest = here / "gold_parts"
    spec = sys.argv[1] if len(sys.argv) > 1 else "all"
    built = {}
    for name, build in BUILDERS.items():
        if spec in ("all", name):
            built[name] = {k: str(v) for k, v in build(dest / name).items()}
    print(yaml.safe_dump(built, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
