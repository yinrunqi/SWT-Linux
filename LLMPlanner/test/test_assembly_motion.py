"""运动评测的离线单测：多传动比、可配置行程、多位姿模式与内齿圈轮廓。"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geomcheck.assembly_motion import (  # noqa: E402
    analyze_frames, analyze_poses, local_to_world, refine_aliased, run_poses, sample_chunked, score_pose_points,
    world_to_local,
)
from planner.gears import generate_internal_cavity, generate_involute_outline  # noqa: E402


def _tf(theta_deg=0.0, x=0.0, y=0.0, z=0.0, axis="z"):
    """SolidWorks ArrayData：前 9 个数依次是零件 X/Y/Z 轴在装配中的方向。"""
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    if axis == "z":
        axes = [c, s, 0, -s, c, 0, 0, 0, 1]
    else:  # 绕 X
        axes = [1, 0, 0, 0, c, s, 0, -s, c]
    return [*axes, x, y, z, 1, 0, 0, 0]


def _frames(n, driver_deg, **others):
    """others: role -> (每帧角度函数, 每帧 y 函数)。"""
    out = []
    for i in range(n):
        t = i / (n - 1)
        tr = {"frame-1": _tf(), "driver-1": _tf(driver_deg * t)}
        for role, (ang, ypos) in others.items():
            tr[f"{role}-1"] = _tf(ang(t), 0, ypos(t), 0)
        out.append({"transforms": tr})
    return out


ALIASES = {"frame": "frame-1", "driver": "driver-1", "out": "out-1", "hook": "hook-1", "slider": "slider-1"}


def test_ratios_list_and_direction():
    sweep = 700
    frames = _frames(121, sweep,
                     out=(lambda t: sweep * t / 20, lambda t: 0.0),
                     hook=(lambda t: -sweep * t * 2 % 360, lambda t: 0.0),
                     slider=(lambda t: 0.0, lambda t: 0.018 * math.cos(math.radians(sweep * t))))
    spec = {"driver": "driver", "expect_fixed": ["frame"], "expect_moving": ["out", "hook", "slider"],
            "ratios": [{"role": "out", "ratio": 20, "same_direction": True},
                       {"role": "hook", "ratio": 0.5, "same_direction": False}],
            "expect_stroke_mm": 36, "stroke_role": "slider", "stroke_axis": "y"}
    out = analyze_frames(frames, spec, ALIASES, [])
    assert out["passed"], out
    assert out["score"] == 100
    checks = {c["role"]: c for c in out["kinematics"]["ratios"]}
    assert checks["out"]["ok"] and checks["hook"]["ok"]

    spec["ratios"][0]["same_direction"] = False
    out = analyze_frames(frames, spec, ALIASES, [])
    # 三项运动学（两个比值 + 行程）错一项：(干涉 2 + 动 1 + 固定 1 + 运动学 2·2/3) / 6
    assert not out["passed"] and out["score"] == pytest.approx(100 * (4 + 4 / 3) / 6, abs=0.01)
    assert out["score_v1"] == 85


def test_small_ratio_uses_relative_tolerance():
    # 1/20 的输出若完全不转，旧的绝对容差 0.08 会误判通过
    frames = _frames(121, 360, out=(lambda t: 0.0, lambda t: 0.0))
    spec = {"driver": "driver", "ratios": [{"role": "out", "ratio": 20}]}
    assert not analyze_frames(frames, spec, ALIASES, [])["passed"]


def test_legacy_expect_ratio_unchanged():
    frames = _frames(121, 360, out=(lambda t: -360 * t / 6, lambda t: 0.0))
    spec = {"driver": "driver", "expect_moving": ["out"], "expect_ratio": 6, "expect_fixed": ["frame"]}
    out = analyze_frames(frames, spec, ALIASES, [])
    assert out["passed"] and abs(out["kinematics"]["output_ratio"] - 1 / 6) < 1e-3


def test_leads_strokes_swings_antiphase():
    sweep = 720
    frames = _frames(121, sweep,
                     out=(lambda t: 25 * math.sin(math.radians(sweep * t)), lambda t: 0.004 * t),
                     slider=(lambda t: 0.0, lambda t: 0.015 * math.cos(math.radians(sweep * t))),
                     hook=(lambda t: 0.0, lambda t: -0.015 * math.cos(math.radians(sweep * t))))
    spec = {"driver": "driver", "expect_fixed": ["frame"],
            "leads": [{"role": "out", "axis": "y", "mm_per_rev": 2}],
            "swings": [{"role": "out", "deg": 50}],
            "strokes": [{"role": "slider", "axis": "y", "mm": 30}, {"role": "hook", "axis": "y", "mm": 30}],
            "antiphase": [{"roles": ["slider", "hook"], "axis": "y"}]}
    out = analyze_frames(frames, spec, ALIASES, [])
    assert out["passed"] and out["score"] == 100, out["kinematics"]
    assert abs(abs(out["kinematics"]["leads"][0]["measured_mm_per_rev"]) - 2) < 1e-6

    # 五项运动学（导程、摆角、两个行程、反相）错一项：(干涉 2 + 固定 1 + 运动学 2·4/5) / 5
    spec["leads"][0]["mm_per_rev"] = 3
    assert analyze_frames(frames, spec, ALIASES, [])["score"] == 92
    spec["leads"][0]["mm_per_rev"] = 2
    spec["swings"][0]["deg"] = 40
    assert analyze_frames(frames, spec, ALIASES, [])["score"] == 92
    spec["swings"][0]["deg"] = 50
    spec["antiphase"] = [{"roles": ["slider", "slider"], "axis": "y"}]
    out = analyze_frames(frames, spec, ALIASES, [])
    assert not out["passed"] and out["kinematics"]["strokes"][-1]["correlation"] > 0.99


def test_local_world_round_trip():
    tf = _tf(30, 0.01, 0.02, 0.03, axis="x")
    p = np.array([5.0, -7.0, 11.0])
    w = local_to_world(tf, p)
    assert np.allclose(world_to_local(tf, w), p)
    # 绕 X 转 90°：局部 +Y 变成装配 +Z
    w = local_to_world(_tf(90, axis="x"), [0, 10, 0])
    assert np.allclose(w, [0, 0, 10], atol=1e-9)


def test_pose_points_in_ground_frame_with_mirror():
    # 地面件本身绕 Z 转了 90° 放置，期望坐标在地面件局部系里取
    ground = _tf(90, 0.1, 0, 0)
    arm_world = local_to_world(ground, [-50.0, 80.0, 0.0]) / 1000
    transforms = {"base-1": ground, "arm-1": _tf(0, *arm_world)}
    aliases = {"base": "base-1", "arm": "arm-1"}
    pose = {"points": [{"role": "arm", "expect": {"x": 50, "y": 80}, "abs": ["x"]}]}
    res = score_pose_points(transforms, aliases, pose, "base", 1.0)
    assert res["ok"], res
    pose = {"points": [{"role": "arm", "local": [0, 0, 0], "expect": {"r_xy": math.hypot(50, 80)}}]}
    assert score_pose_points(transforms, aliases, pose, "base", 1.0)["ok"]
    pose = {"points": [{"role": "arm", "expect": {"x": 50}}]}
    assert not score_pose_points(transforms, aliases, pose, "base", 1.0)["ok"]


def test_analyze_poses_score():
    rows = [
        {"name": "a", "points": {"ok": True}, "interference_count": 0, "drifted_fixed": []},
        {"name": "b", "points": {"ok": True}, "interference_count": 2, "drifted_fixed": []},
        {"name": "c", "error": "临时配合无解"},
        {"name": "d", "points": {"ok": False}, "interference_count": 0},
    ]
    out = analyze_poses(rows, {"max_interference": 0})
    # a 满分；b 位姿对但有干涉 (2+1)/5；c 无解、d 位姿错都是 0
    assert out["score"] == 40 and out["score_v1"] == 25 and not out["passed"]
    assert [r["ok"] for r in out["poses"]] == [True, False, False, False]
    assert [r["score"] for r in out["poses"]] == [100, 60, 0, 0]


def test_stuck_mechanism_gets_no_free_credit():
    """输出件完全卡死时，“没穿模、机架没漂”不再白拿分（旧扣分制还剩 65）。"""
    frames = _frames(13, 360, out=(lambda t: 0.0, lambda t: 0.0))
    spec = {"driver": "driver", "expect_fixed": ["frame"], "expect_moving": ["out"],
            "swings": [{"role": "out", "deg": 40}]}
    out = analyze_frames(frames, spec, ALIASES, [])
    assert out["stuck"] == ["out"] and out["score"] == 0 and out["score_v1"] == 65


class FakeSession:
    """一根臂绕基座原点转；角度配合 flip=True 时求解成 +θ，否则 −θ。"""

    def __init__(self):
        self.aliases = {"base": "base-1", "arm": "arm-1"}
        self.mate_names: list[str] = []
        self.active: dict[str, float] = {}
        self.deleted: list[str] = []
        self.counter = 0

    def _mate(self, kind, a, b, **kw):
        self.counter += 1
        name = f"m{self.counter}"
        sign = 1 if kw.get("flip") else -1
        self.active[name] = sign * kw["angleDeg"]
        self.mate_names.append(name)
        return {"name": name}

    def _rollback_mates(self, names):
        for n in names:
            self.active.pop(n, None)
            self.deleted.append(n)

    def check_interference(self, args):
        return {"interferenceCount": 0, "totalVolume_mm3": 0}

    def asmwf(self, op, body=None):
        if op == "mates":
            return {"mates": [{"name": n, "errorCode": 0} for n in self.active]}
        theta = next(iter(self.active.values()), 0.0)
        return {"components": [
            {"name": "base-1", "transform": _tf()},
            {"name": "arm-1", "transform": _tf(theta)},
        ]}


def test_run_poses_tries_flip_and_cleans_up():
    sess = FakeSession()
    tip = {"role": "arm", "local": [100, 0, 0], "expect": {"x": 100 * math.cos(math.radians(30)), "y": 50}}
    spec = {"mode": "poses", "expect_fixed": ["base"], "tolerance_mm": 0.5, "poses": [
        {"name": "up30", "set": [{"type": "angle", "a": "arm:plane(top)", "b": "base:plane(top)",
                                  "angle_deg": 30, "points": [tip]}]},
    ]}
    out = run_poses(sess, spec)
    assert out["passed"], out
    assert out["poses"][0]["variants"] == [{"flip": True}]
    assert not sess.active, "临时配合必须全部删掉"

    tip["expect"]["y"] = 60
    out = run_poses(sess, spec)
    assert not out["passed"] and out["score"] == 0
    assert not sess.active


def test_internal_gear_cavity_meshes_planet():
    shapely = pytest.importorskip("shapely")
    from shapely import affinity
    from shapely.geometry import Polygon

    m = 0.0012
    cavity, params = generate_internal_cavity(48, module_m=m)
    assert abs(params["tipRadius"] - 23 * m) < 1e-12 and abs(params["rootRadius"] - 25.15 * m) < 1e-12
    ring = Polygon(cavity)
    a = m * (48 - 18) / 2
    for k in range(12):
        th = k * 360 / 48 / 12
        planet = affinity.rotate(Polygon(generate_involute_outline(18, 180 / 18, module_m=m)[0]),
                                 th * 48 / 18, origin=(0, 0))
        planet = affinity.translate(planet, a, 0)
        assert planet.difference(affinity.rotate(ring, th, origin=(0, 0))).area < 1e-12


def _rot_z(deg: float) -> list[float]:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, -s, 0, s, c, 0, 0, 0, 1, 0, 0, 0]


def test_refine_aliased_resamples_fast_driver_and_flags_jumps():
    """驱动件每帧转 170°：加密后单帧转角变小、总转角正确；位形跳变加密也消不掉，保留原帧并标出。"""
    duration, rate = 6.0, 170.0 * 120 / 6.0
    calls: list[int] = []

    def fast(times):
        calls.append(len(times))
        return [{"transforms": {"d": _rot_z(rate * t)}} for t in times]

    coarse = fast([duration * i / 120 for i in range(121)])
    frames, info = refine_aliased(fast, "d", duration, coarse)
    assert info["resampled_frames"] > 121 and max(calls[1:]) <= 121
    assert "aliasing_risk_deg" not in info and len(frames) == info["resampled_frames"]

    def jump(times):
        return [{"transforms": {"d": _rot_z(0 if t < duration / 2 else 180)}} for t in times]

    coarse = jump([duration * i / 120 for i in range(121)])
    frames, info = refine_aliased(jump, "d", duration, coarse)
    assert frames is coarse and info == {"driver_jump_deg": 180.0}
    assert len(sample_chunked(fast, [0.0] * 300)) == 300
