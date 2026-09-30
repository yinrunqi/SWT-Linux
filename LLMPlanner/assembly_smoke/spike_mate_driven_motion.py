"""技术验证：配合驱动的运动（Motion Study 马达 + 同心/重合/齿轮配合），逐帧读 Transform 与干涉。

用法（SolidWorks 与 C# 服务已启动）：
    venv\\Scripts\\python.exe -X utf8 assembly_smoke\\spike_mate_driven_motion.py gears
    venv\\Scripts\\python.exe -X utf8 assembly_smoke\\spike_mate_driven_motion.py crank
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assembly_smoke.phase1_build_two_gears import generate_involute_outline

BASE = "http://localhost:5100"
OUT = ROOT / "log" / "spike_motion"
HTTP = requests.Session()


def call(path: str, body: dict | None = None, timeout: int = 600) -> dict:
    response = HTTP.post(f"{BASE}{path}", json=body or {}, timeout=timeout)
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(f"{path} failed: {data.get('message')}")
    return data.get("result", data)


def wf(op: str, body: dict | None = None) -> dict:
    return call(f"/api/asmwf/{op}", body)


# ---------------------------------------------------------------- 零件


def part_plate(path: Path, x0, y0, x1, y1, holes, thick):
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/rectangle", {"x1": x0, "y1": y0, "x2": x1, "y2": y1})
    for cx, cy, r in holes:
        call("/api/sketch/circle", {"cx": cx, "cy": cy, "radius": r})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})


def part_cylinder(path: Path, radius, length, bore=0.0):
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": radius})
    if bore:
        call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": bore})
    call("/api/feature/extrusion", {"depth": length})
    call("/api/save-part-as", {"savePath": str(path)})


def part_gear(path: Path, teeth, module_m, thick, bore):
    outline, _ = generate_involute_outline(tooth_count=teeth, phase_angle_deg=0, module_m=module_m,
                                           pressure_angle_deg=30.0)
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/polyline", {"points": [{"x": x, "y": y} for x, y in outline[:-1]], "closed": True})
    call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": bore})
    call("/api/feature/extrusion", {"depth": thick})
    call("/api/save-part-as", {"savePath": str(path)})


def part_link(path: Path, length, width, hole_r, thick):
    """两端圆头连杆：用两个圆 + 矩形会重叠，改用 slot 等价的多段线（直边 + 圆弧离散）。"""
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


# ---------------------------------------------------------------- 装配辅助


def insert(path: Path, x=0.0, y=0.0, z=0.0, angle_deg=0.0) -> str:
    name = call("/api/assembly/add-component", {"partPath": str(path), "x": x, "y": y, "z": z})
    name = name.get("componentName") if isinstance(name, dict) else name
    if angle_deg:
        call("/api/assembly/set-component-pose", {"componentName": name, "x": x, "y": y, "z": z,
                                                  "axisX": 0, "axisY": 0, "axisZ": 1, "angleDeg": angle_deg})
    return name


def faces(component: str, radius: float | None = None, max_faces: int = 80) -> list[dict]:
    body = {"component": component, "maxFaces": max_faces}
    if radius is not None:
        body["radius"] = radius
    return wf("component-faces", body)["faces"]


def cyl(component: str, radius: float, near: tuple[float, float] | None = None, *, bore: bool = False) -> str:
    """半径最接近 radius 的圆柱面。bore=True 时用中心孔射线，避免遍历齿轮齿面。"""
    if bore:
        return "bore:auto"
    items = [
        f for f in faces(component, radius, max_faces=8)
        if f["surfaceType"] == "cylinder" and abs(f.get("radius", -1) - radius) < 2e-4
    ]
    if near is not None:
        items.sort(key=lambda f: math.hypot(f["axisOrigin"][0] - near[0], f["axisOrigin"][1] - near[1]))
    if not items:
        raise RuntimeError(f"{component} 没有半径 {radius} 的圆柱面")
    return f"face:{items[0]['index']}"


def mate(kind: str, a: tuple[str, str], b: tuple[str, str], **kw) -> dict:
    body = {"type": kind, "a": {"component": a[0], "ref": a[1]}, "b": {"component": b[0], "ref": b[1]}, **kw}
    started = time.perf_counter()
    result = wf("mate", body)
    print(f"  mate {kind:10s} {a} ~ {b} -> {result['name']} ({time.perf_counter() - started:.1f}s)")
    return result


def angle_about_z(matrix: list[float]) -> float:
    r = np.array(matrix[:9]).reshape(3, 3)
    return math.degrees(math.atan2(r[0, 1], r[0, 0]))


def sample(driver: str, driver_ref: str, rpm: float, duration: float, frames: int) -> dict:
    wf("motion-drive", {"component": driver, "ref": driver_ref, "rpm": rpm, "durationSeconds": duration})
    times = [duration * i / (frames - 1) for i in range(frames)]
    started = time.perf_counter()
    result = wf("motion-sample", {"times": times, "interference": True})
    print(f"  sampled {frames} frames in {time.perf_counter() - started:.1f}s")
    return result


# ---------------------------------------------------------------- 齿轮系


def spike_gears() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    call("/api/close-all-documents")
    m, t = 0.003, 0.010
    x1, x2 = 0.054, 0.054 + 0.072
    paths = {name: OUT / f"{name}.SLDPRT" for name in ("base", "shaft", "z12", "z24", "z36")}
    if not all(p.is_file() for p in paths.values()):
        part_plate(paths["base"], -0.03, -0.04, 0.16, 0.04,
                   [(0, 0, 0.0052), (x1, 0, 0.0052), (x2, 0, 0.0052)], 0.010)
        part_cylinder(paths["shaft"], 0.005, 0.050)
        part_gear(paths["z12"], 12, m, t, 0.005)
        part_gear(paths["z24"], 24, m, t, 0.005)
        part_gear(paths["z36"], 36, m, t, 0.005)
    call("/api/close-all-documents")

    call("/api/assembly/new", {"savePath": str(OUT / "spike_gearbox.SLDASM")})
    base = insert(paths["base"])
    shafts = [insert(paths["shaft"], x, 0, 0) for x in (0.0, x1, x2)]
    # 初始相位：从动轮在中心线上让出齿槽（偶数齿数错开半个齿距）
    p1 = insert(paths["z12"], 0.0, 0, 0.015)
    w1 = insert(paths["z24"], x1, 0, 0.015, angle_deg=180 / 24)
    p2 = insert(paths["z12"], x1, 0, 0.030)
    w2 = insert(paths["z36"], x2, 0, 0.030, angle_deg=180 / 36)
    print("components:", base, shafts, p1, w1, p2, w2)

    shaft_face = cyl(shafts[0], 0.005)
    info = wf("component-faces", {"component": shafts[1]})
    print("shaft2 transform t:", info["transform"][9:12],
          "cylinder axisOrigin:", [f["axisOrigin"] for f in info["faces"] if f["surfaceType"] == "cylinder"])

    for shaft, x in zip(shafts, (0.0, x1, x2)):
        mate("concentric", (shaft, cyl(shaft, 0.005, bore=True)), (base, cyl(base, 0.0052, near=(x, 0))))
        mate("coincident", (shaft, "plane:front"), (base, "plane:front"), align="aligned")
    for gear, shaft, z in ((p1, shafts[0], 0.015), (w1, shafts[1], 0.015), (p2, shafts[1], 0.030),
                           (w2, shafts[2], 0.030)):
        mate("concentric", (gear, cyl(gear, 0.005, bore=True)), (shaft, cyl(shaft, 0.005, bore=True)))
        mate("distance", (gear, "plane:front"), (base, "plane:front"), distance=z, align="aligned")
    for gear, shaft in ((p1, shafts[0]), (w1, shafts[1]), (p2, shafts[1]), (w2, shafts[2])):
        mate("gear", (gear, cyl(gear, 0.005, bore=True)), (shaft, cyl(shaft, 0.005, bore=True)),
             ratioNumerator=1, ratioDenominator=1)
    mate("gear", (p1, cyl(p1, 0.005, bore=True)), (w1, cyl(w1, 0.005, bore=True)),
         ratioNumerator=12, ratioDenominator=24, reverse=True)
    mate("gear", (p2, cyl(p2, 0.005, bore=True)), (w2, cyl(w2, 0.005, bore=True)),
         ratioNumerator=12, ratioDenominator=36, reverse=True)
    call("/api/assembly/rebuild")
    print("components status:", [(c["name"], c["constrainedStatus"]) for c in wf("components")["components"]])
    print("static interference:", call("/api/assembly/check-interference")["interferenceCount"])

    result = sample(shafts[0], shaft_face, rpm=10, duration=6, frames=13)
    report = []
    for frame in result["frames"]:
        tf = frame["transforms"]
        report.append({
            "t": frame["time"],
            "input": round(angle_about_z(tf[p1]), 3),
            "w1": round(angle_about_z(tf[w1]), 3),
            "p2": round(angle_about_z(tf[p2]), 3),
            "out": round(angle_about_z(tf[w2]), 3),
            "shaft3": round(angle_about_z(tf[shafts[2]]), 3),
            "interference": frame["interference"]["interferenceCount"],
            "volume_mm3": round(frame["interference"]["totalVolume"] * 1e9, 4),
        })
    for row in report:
        print(" ", row)
    call("/api/assembly/save")
    (OUT / "gears_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 曲柄滑块


def spike_crank() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    call("/api/close-all-documents")
    r, L = 0.025, 0.090
    paths = {name: OUT / f"{name}.SLDPRT" for name in ("frame", "crank", "rod", "piston", "pin")}
    if not all(p.is_file() for p in paths.values()):
        part_plate(paths["frame"], -0.05, -0.05, 0.05, 0.16, [(0, 0, 0.0052)], 0.008)
        part_link(paths["crank"], r, 0.016, 0.005, 0.006)
        part_link(paths["rod"], L, 0.014, 0.005, 0.006)
        part_cylinder(paths["piston"], 0.012, 0.030, bore=0.005)
        part_cylinder(paths["pin"], 0.005, 0.040)
    call("/api/close-all-documents")

    call("/api/assembly/new", {"savePath": str(OUT / "spike_crank.SLDASM")})
    frame = insert(paths["frame"])
    crank_shaft = insert(paths["pin"], 0, 0, 0)
    crank = insert(paths["crank"], 0, 0, 0.010)
    crank_pin = insert(paths["pin"], r, 0, 0)
    rod = insert(paths["rod"], r, 0, 0.018)
    piston_pin = insert(paths["pin"], 0, r + L, 0)
    print("components:", frame, crank_shaft, crank, crank_pin, rod, piston_pin)

    shaft_face = cyl(crank_shaft, 0.005)
    mate("concentric", (crank_shaft, shaft_face), (frame, cyl(frame, 0.0052)))
    mate("coincident", (crank_shaft, "plane:front"), (frame, "plane:front"), align="aligned")
    # 曲柄固连在曲轴上：同心 + 1:1 齿轮配合
    mate("concentric", (crank, cyl(crank, 0.005, near=(0, 0))), (crank_shaft, shaft_face))
    mate("distance", (crank, "plane:front"), (frame, "plane:front"), distance=0.010, align="aligned")
    mate("gear", (crank, cyl(crank, 0.005, near=(0, 0))), (crank_shaft, shaft_face), ratioNumerator=1,
         ratioDenominator=1)
    # 曲柄销
    mate("concentric", (crank_pin, cyl(crank_pin, 0.005)), (crank, cyl(crank, 0.005, near=(r, 0))))
    mate("coincident", (crank_pin, "plane:front"), (frame, "plane:front"), align="aligned")
    # 连杆大头套曲柄销、小头套活塞销
    mate("concentric", (rod, cyl(rod, 0.005, near=(0, 0))), (crank_pin, cyl(crank_pin, 0.005)))
    mate("distance", (rod, "plane:front"), (frame, "plane:front"), distance=0.018, align="aligned")
    mate("concentric", (rod, cyl(rod, 0.005, near=(L, 0))), (piston_pin, cyl(piston_pin, 0.005)))
    mate("coincident", (piston_pin, "plane:front"), (frame, "plane:front"), align="aligned")
    # 活塞销沿机体 Y 轴滑动：销的右视基准面（YZ）与机体右视基准面重合
    mate("coincident", (piston_pin, "plane:right"), (frame, "plane:right"), align="aligned")
    call("/api/assembly/rebuild")
    print("components status:", [(c["name"], c["constrainedStatus"]) for c in wf("components")["components"]])

    result = sample(crank_shaft, shaft_face, rpm=10, duration=6, frames=13)
    report = []
    for f in result["frames"]:
        tf = f["transforms"]
        report.append({
            "t": f["time"],
            "crank_deg": round(angle_about_z(tf[crank]), 3),
            "piston_pin_y_mm": round(tf[piston_pin][10] * 1000, 3),
            "rod_deg": round(angle_about_z(tf[rod]), 3),
            "interference": f["interference"]["interferenceCount"],
        })
    for row in report:
        print(" ", row)
    call("/api/assembly/save")
    (OUT / "crank_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    {"gears": spike_gears, "crank": spike_crank}[sys.argv[1]]()
