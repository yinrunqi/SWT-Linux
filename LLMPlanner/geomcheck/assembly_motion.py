"""配合驱动的装配运动评测：Motion Study 采样、逐帧干涉、传动比/行程、可选齿侧间隙。"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np


def _angle_z(matrix) -> float:
    r = np.array(matrix[:9], float).reshape(3, 3)
    return math.degrees(math.atan2(r[1, 0], r[0, 0]))


def _xyz(matrix) -> np.ndarray:
    return np.array([matrix[9], matrix[10], matrix[11]], float) * 1000


def unwrap(degrees: list[float]) -> list[float]:
    out = [degrees[0]]
    for value in degrees[1:]:
        prev = out[-1]
        delta = (value - prev + 180) % 360 - 180
        out.append(prev + delta)
    return out


def _max_step(degrees: list[float]) -> float:
    return max((abs((b - a + 180) % 360 - 180) for a, b in zip(degrees, degrees[1:])), default=0.0)


def analyze_frames(
    frames: list[dict], spec: dict, aliases: dict[str, str], interference_frames: list[dict] | None = None,
) -> dict:
    def name(role: str) -> str:
        return aliases[role]

    driver = name(spec["driver"])
    first, last = frames[0]["transforms"], frames[-1]["transforms"]
    raw_driver = [_angle_z(frame["transforms"][driver]) for frame in frames]
    driver_angles = unwrap(raw_driver)
    sweep = driver_angles[-1] - driver_angles[0]
    inter_frames = []
    for index, frame in enumerate(interference_frames if interference_frames is not None else frames):
        inter = frame.get("interference") or {}
        if int(inter.get("interferenceCount") or 0) > int(spec.get("max_interference") or 0):
            inter_frames.append({
                "frame": index,
                "count": inter.get("interferenceCount"),
                "volume_mm3": float(inter.get("totalVolume") or 0) * 1e9,
            })

    stuck, drifted = [], []
    for role in spec.get("expect_moving") or []:
        sw = name(role)
        mid = frames[len(frames) // 2]["transforms"][sw]
        delta = max(
            sum(abs(float(a) - float(b)) for a, b in zip(first[sw], other))
            for other in (mid, last[sw])
        )
        if delta < 1e-5:
            stuck.append(role)
    for role in spec.get("expect_fixed") or []:
        sw = name(role)
        max_delta = 0.0
        for frame in frames:
            max_delta = max(
                max_delta,
                sum(abs(float(a) - float(b)) for a, b in zip(first[sw], frame["transforms"][sw])),
            )
        if max_delta > 1e-4:
            drifted.append(role)

    kinematics: dict[str, Any] = {
        "driver_sweep_deg": round(sweep, 3),
        "frames": len(frames),
        "driver_max_step_deg": round(_max_step(raw_driver), 2),
    }
    ratio_ok = True
    if spec.get("expect_ratio") and spec.get("expect_moving"):
        output = name(spec["expect_moving"][-1])
        raw_out = [_angle_z(frame["transforms"][output]) for frame in frames]
        kinematics["output_max_step_deg"] = round(_max_step(raw_out), 2)
        out_angles = unwrap(raw_out)
        measured = abs((out_angles[-1] - out_angles[0]) / (sweep or 1e-9))
        expected = 1.0 / float(spec["expect_ratio"])
        kinematics["output_ratio"] = round(measured, 4)
        kinematics["expected_ratio"] = expected
        ratio_ok = abs(measured - expected) <= 0.08
        if spec.get("expect_same_direction"):
            kinematics["same_direction"] = (out_angles[-1] - out_angles[0]) * sweep > 0
            ratio_ok = ratio_ok and kinematics["same_direction"]

    timing_ok = True
    if spec.get("timing_ratio") and spec.get("timing_driven"):
        driven = name(spec["timing_driven"])
        raw_t = [_angle_z(frame["transforms"][driven]) for frame in frames]
        t_angles = unwrap(raw_t)
        travel = t_angles[-1] - t_angles[0]
        measured = abs(travel / (sweep or 1e-9))
        expected = 1.0 / float(spec["timing_ratio"])
        kinematics["timing_ratio_measured"] = round(measured, 4)
        kinematics["timing_ratio_expected"] = expected
        kinematics["timing_max_step_deg"] = round(_max_step(raw_t), 2)
        timing_ok = abs(measured - expected) <= 0.08
        if spec.get("timing_opposite") is not None:
            opposite = travel * sweep < 0
            kinematics["timing_opposite"] = opposite
            timing_ok = timing_ok and opposite == bool(spec["timing_opposite"])

    stroke_ok = True
    if spec.get("expect_stroke_mm"):
        slider = name("piston_pin") if "piston_pin" in aliases else name(spec["expect_moving"][1])
        ys = [_xyz(frame["transforms"][slider])[1] for frame in frames]
        stroke = max(ys) - min(ys)
        kinematics["stroke_mm"] = round(stroke, 3)
        kinematics["expected_stroke_mm"] = spec["expect_stroke_mm"]
        stroke_ok = abs(stroke - float(spec["expect_stroke_mm"])) <= 2.0

    passed = not inter_frames and not stuck and not drifted and ratio_ok and stroke_ok and timing_ok
    score = 100.0
    if inter_frames:
        score -= 40
    if stuck:
        score -= 20
    if drifted:
        score -= 15
    if not ratio_ok:
        score -= 15
    if not stroke_ok:
        score -= 15
    if not timing_ok:
        score -= 15
    return {
        "passed": passed,
        "score": max(0.0, score),
        "interference_frames": inter_frames,
        "stuck": stuck,
        "drifted_fixed": drifted,
        "kinematics": kinematics,
    }


def sample_motion(base_url: str, spec: dict, driver_sw: str, driver_ref: str) -> dict:
    import requests

    http = requests.Session()

    def post(path, body=None):
        data = http.post(f"{base_url.rstrip('/')}{path}", json=body or {}, timeout=180).json()
        if not data.get("success"):
            raise RuntimeError(data.get("message"))
        return data.get("result", data)

    rpm = float(spec.get("rpm") or 10)
    sweep = float(spec.get("sweep_deg") or 360)
    frames = max(3, int(spec.get("frames") or 13))
    dense = max(frames, int(spec.get("dense_frames") or 121))
    duration = abs(sweep) / (rpm * 6)
    post("/api/asmwf/motion-drive", {
        "component": driver_sw, "ref": driver_ref, "rpm": rpm, "durationSeconds": duration,
    })
    # Animation 的马达并不按 rpm 转（实测快 5~10 倍），帧稀了角度会混叠，比值就算错。
    # 运动学用密采样，逐帧干涉检测较慢，只在稀疏帧上做。
    kinematic = post("/api/asmwf/motion-sample", {
        "times": [duration * i / (dense - 1) for i in range(dense)], "interference": False,
    })
    sparse = post("/api/asmwf/motion-sample", {
        "times": [duration * i / (frames - 1) for i in range(frames)], "interference": True,
    })
    return {"frames": kinematic.get("frames") or [], "interference_frames": sparse.get("frames") or []}


def clearance_mm(step_a: Path, step_b: Path, matrix_a, matrix_b) -> float | None:
    """两零件在给定装配 Transform 下的最小距离（mm）。相交为 0。"""
    try:
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopLoc import TopLoc_Location
        from OCP.gp import gp_Trsf
    except ImportError:
        return None

    def load(path: Path):
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != 1:
            raise RuntimeError(f"无法读 STEP {path}")
        reader.TransferRoots()
        return reader.OneShape()

    def locate(shape, matrix):
        trsf = gp_Trsf()
        m = [float(v) for v in matrix]
        trsf.SetValues(m[0], m[1], m[2], m[9], m[3], m[4], m[5], m[10], m[6], m[7], m[8], m[11])
        located = shape.Located(TopLoc_Location(trsf))
        return located

    dist = BRepExtrema_DistShapeShape(locate(load(step_a), matrix_a), locate(load(step_b), matrix_b))
    dist.Perform()
    if not dist.IsDone():
        return None
    return float(dist.Value()) * 1000
