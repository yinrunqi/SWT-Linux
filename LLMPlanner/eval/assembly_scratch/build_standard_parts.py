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

from planner.gears import generate_involute_outline

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
        call("/api/sketch/circle", {"cx": cx, "cy": cy, "radius": r})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def cylinder(path: Path, radius, length, bore=0.0, cx=0.0) -> None:
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"cx": cx, "cy": 0, "radius": radius})
    if bore:
        call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": bore})
    call("/api/feature/extrusion", {"depth": length})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def gear(path: Path, teeth: int, module_m: float, thick: float, bore: float) -> None:
    outline, _ = generate_involute_outline(tooth_count=teeth, module_m=module_m, pressure_angle_deg=30)
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/polyline", {
        "points": [{"x": x, "y": y} for x, y in outline[:-1]],
        "closed": True,
    }, timeout=300)
    call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": bore})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})
    export_pair(path)
    close()


def link(path: Path, length, width, hole_r, thick) -> None:
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
    call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": hole_r})
    call("/api/sketch/circle", {"cx": length, "cy": 0, "radius": hole_r})
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


def main() -> None:
    here = Path(__file__).resolve().parent
    dest = here / "gold_parts"
    spec = sys.argv[1] if len(sys.argv) > 1 else "all"
    built = {}
    if spec in ("all", "gearbox"):
        built["gearbox"] = {k: str(v) for k, v in build_gearbox(dest / "gearbox").items()}
    if spec in ("all", "engine"):
        built["engine"] = {k: str(v) for k, v in build_engine(dest / "engine").items()}
    print(yaml.safe_dump(built, allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    main()
