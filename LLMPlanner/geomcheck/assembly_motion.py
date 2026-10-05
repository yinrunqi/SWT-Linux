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


# Animation 每帧转角按档位量化（实测 0.5 / 0.75 / 1.25 rad），这些都能正确展开；接近 180° 才会混叠
MAX_STEP_DEG = 120.0
MAX_DENSE_FRAMES = 481
SAMPLE_CHUNK = 121
# 十几个零件的装配，121 帧一次 motion-sample 在 SolidWorks 里就要 3 分钟以上
HTTP_TIMEOUT_S = 900


def _max_step(degrees: list[float]) -> float:
    return max((abs((b - a + 180) % 360 - 180) for a, b in zip(degrees, degrees[1:])), default=0.0)


def sample_chunked(sample, times: list[float]) -> list[dict]:
    """sample(times) -> frames。每次请求不超过 SAMPLE_CHUNK 帧：一次请求太多帧，SolidWorks 会超时甚至崩溃。"""
    frames: list[dict] = []
    for start in range(0, len(times), SAMPLE_CHUNK):
        frames += sample(times[start:start + SAMPLE_CHUNK])
    return frames


def refine_aliased(sample, driver: str, duration: float, frames: list[dict]) -> tuple[list[dict], dict]:
    """驱动件单帧转角超过 MAX_STEP_DEG 时加密一次（展开转角会混叠，比值、导程都会算错）。

    加密后单帧转角没有按比例变小，说明是位形跳变（机构卡死时求解器在两个解之间跳），不是混叠：
    保留原帧并标出 driver_jump，不再加密。"""
    if not frames or driver not in (frames[0].get("transforms") or {}):
        return frames, {}
    step = _max_step([_angle_z(f["transforms"][driver]) for f in frames])
    if step <= MAX_STEP_DEG:
        return frames, {}
    n = min(MAX_DENSE_FRAMES, int(len(frames) * step / (MAX_STEP_DEG / 2)) + 1)
    if n <= len(frames):
        return frames, {"aliasing_risk_deg": round(step, 2)}
    dense = sample_chunked(sample, [duration * i / (n - 1) for i in range(n)])
    new_step = _max_step([_angle_z(f["transforms"][driver]) for f in dense]) if dense else step
    if new_step > 0.75 * step:
        return frames, {"driver_jump_deg": round(new_step, 2)}
    return dense, {"resampled_frames": n, **({"aliasing_risk_deg": round(new_step, 2)}
                                             if new_step > MAX_STEP_DEG else {})}


# 正向计分：每个适用检查项给 [0,1] 的得分，按权重平均。两种模式共用同一组权重：
# 运动学（比值 / 导程 / 行程 / 位姿关键点）2、干涉 2、该动的动了 1、该固定的没漂 1、齿侧间隙 1。
CHECK_WEIGHTS = {"kinematics": 2.0, "interference": 2.0, "moving": 1.0, "fixed": 1.0, "clearance": 1.0}


def score_checks(checks: list[dict]) -> float:
    total = sum(c["weight"] for c in checks)
    if not total:
        return 0.0
    return round(100.0 * sum(c["weight"] * c["score"] for c in checks) / total, 2)


def make_check(name: str, score: float, **extra) -> dict:
    return {"name": name, "weight": CHECK_WEIGHTS[name], "score": round(float(score), 4), **extra}


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
    kin_items: list[bool] = []
    ratio_checks = []
    for item in spec.get("ratios") or []:
        raw_out = [_angle_z(frame["transforms"][name(item["role"])]) for frame in frames]
        out_angles = unwrap(raw_out)
        travel = out_angles[-1] - out_angles[0]
        measured = abs(travel / (sweep or 1e-9))
        expected = 1.0 / float(item["ratio"])
        check = {
            "role": item["role"],
            "measured": round(measured, 4),
            "expected": round(expected, 4),
            "max_step_deg": round(_max_step(raw_out), 2),
            # 相对误差 8%：1/20 这类小比值用绝对容差会把“不转”也判成通过
            "ok": abs(measured - expected) <= 0.08 * expected,
        }
        if item.get("same_direction") is not None:
            check["same_direction"] = travel * sweep > 0
            check["ok"] = check["ok"] and check["same_direction"] == bool(item["same_direction"])
        ratio_checks.append(check)
        ratio_ok = ratio_ok and check["ok"]
    if ratio_checks:
        kinematics["ratios"] = ratio_checks
    lead_checks = []
    for item in spec.get("leads") or []:
        axis = "xyz".index(str(item.get("axis") or "z").lower())
        pos = [_xyz(frame["transforms"][name(item["role"])])[axis] for frame in frames]
        travel = pos[-1] - pos[0]
        per_rev = float(travel / ((sweep or 1e-9) / 360.0))
        expected = float(item["mm_per_rev"])
        check = {"role": item["role"], "measured_mm_per_rev": round(per_rev, 4), "expected": expected,
                 "ok": bool(abs(abs(per_rev) - abs(expected)) <= 0.08 * abs(expected))}
        if expected < 0 or item.get("signed"):
            check["ok"] = check["ok"] and bool(per_rev * expected > 0)
        lead_checks.append(check)
        ratio_ok = ratio_ok and check["ok"]
    kin_items += [bool(c["ok"]) for c in ratio_checks + lead_checks]
    if lead_checks:
        kinematics["leads"] = lead_checks
    if spec.get("expect_ratio") and spec.get("expect_moving"):
        output = name(spec["expect_moving"][-1])
        raw_out = [_angle_z(frame["transforms"][output]) for frame in frames]
        kinematics["output_max_step_deg"] = round(_max_step(raw_out), 2)
        out_angles = unwrap(raw_out)
        measured = abs((out_angles[-1] - out_angles[0]) / (sweep or 1e-9))
        expected = 1.0 / float(spec["expect_ratio"])
        kinematics["output_ratio"] = round(measured, 4)
        kinematics["expected_ratio"] = expected
        legacy_ok = abs(measured - expected) <= 0.08
        if spec.get("expect_same_direction"):
            kinematics["same_direction"] = (out_angles[-1] - out_angles[0]) * sweep > 0
            legacy_ok = legacy_ok and kinematics["same_direction"]
        kin_items.append(bool(legacy_ok))
        ratio_ok = ratio_ok and legacy_ok

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
        kin_items.append(bool(timing_ok))

    stroke_ok = True
    if spec.get("expect_stroke_mm"):
        if spec.get("stroke_role"):
            slider = name(spec["stroke_role"])
        else:
            slider = name("piston_pin") if "piston_pin" in aliases else name(spec["expect_moving"][1])
        axis = "xyz".index(str(spec.get("stroke_axis") or "y").lower())
        ys = [_xyz(frame["transforms"][slider])[axis] for frame in frames]
        stroke = max(ys) - min(ys)
        kinematics["stroke_mm"] = round(stroke, 3)
        kinematics["expected_stroke_mm"] = spec["expect_stroke_mm"]
        stroke_ok = abs(stroke - float(spec["expect_stroke_mm"])) <= 2.0
        kin_items.append(bool(stroke_ok))
    stroke_checks = []
    for item in spec.get("strokes") or []:
        axis = "xyz".index(str(item.get("axis") or "y").lower())
        pos = [_xyz(frame["transforms"][name(item["role"])])[axis] for frame in frames]
        got = float(max(pos) - min(pos))
        stroke_checks.append({"role": item["role"], "axis": "xyz"[axis], "measured_mm": round(got, 3),
                              "expected": float(item["mm"]), "ok": bool(abs(got - float(item["mm"])) <= 2.0)})
    # 摇杆摆角：绕 Z 转角的极差（不展开到整周，摆动件不会转过 ±180°）
    for item in spec.get("swings") or []:
        ang = unwrap([_angle_z(frame["transforms"][name(item["role"])]) for frame in frames])
        got = float(max(ang) - min(ang))
        stroke_checks.append({"role": item["role"], "swing_deg": round(got, 3),
                              "expected": float(item["deg"]), "ok": bool(abs(got - float(item["deg"])) <= 2.0)})
    # 两件沿同一轴反相往复（双缸 180° 曲柄）：位置序列相关系数 < -0.9
    for item in spec.get("antiphase") or []:
        axis = "xyz".index(str(item.get("axis") or "y").lower())
        a, b = ([_xyz(frame["transforms"][name(role)])[axis] for frame in frames] for role in item["roles"])
        corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 1e-6 and np.std(b) > 1e-6 else 0.0
        stroke_checks.append({"roles": item["roles"], "correlation": round(corr, 3), "ok": bool(corr < -0.9)})
    if stroke_checks:
        kinematics["strokes"] = stroke_checks
        stroke_ok = stroke_ok and all(c["ok"] for c in stroke_checks)
        kin_items += [bool(c["ok"]) for c in stroke_checks]

    passed = not inter_frames and not stuck and not drifted and ratio_ok and stroke_ok and timing_ok
    legacy = 100.0
    if inter_frames:
        legacy -= 40
    if stuck:
        legacy -= 20
    if drifted:
        legacy -= 15
    if not ratio_ok:
        legacy -= 15
    if not stroke_ok:
        legacy -= 15
    if not timing_ok:
        legacy -= 15
    # “没有穿模 / 没有漂移”只在机构真的动起来时才有意义：按该动件实际在动的比例折算
    moving_roles = spec.get("expect_moving") or []
    moving_frac = 1.0 - len(stuck) / len(moving_roles) if moving_roles else 1.0
    n_frames = len(interference_frames if interference_frames is not None else frames) or 1
    checks = [make_check("interference", (1.0 - len(inter_frames) / n_frames) * moving_frac,
                     frames_with_interference=len(inter_frames), frames=n_frames)]
    if moving_roles:
        checks.append(make_check("moving", moving_frac))
    if spec.get("expect_fixed"):
        checks.append(make_check("fixed", (1.0 - len(drifted) / len(spec["expect_fixed"])) * moving_frac))
    if kin_items:
        checks.append(make_check("kinematics", sum(kin_items) / len(kin_items), passed=sum(kin_items),
                             items=len(kin_items)))
    return {
        "passed": passed,
        "score": score_checks(checks),
        "score_v1": max(0.0, legacy),
        "checks": checks,
        "interference_frames": inter_frames,
        "stuck": stuck,
        "drifted_fixed": drifted,
        "kinematics": kinematics,
    }


# ------------------------------------------------------------------ 多位姿（有限行程 / 多自由度机构）
# 马达驱动只适合整周回转；台灯、伞这类有限行程或多自由度的机构，改为逐个位姿加临时配合
# （角度 / 距离）把机构定到指定位形，量关键点位置并做静态干涉检查，最后删掉临时配合。

def local_to_world(matrix, local_mm) -> np.ndarray:
    """SolidWorks Transform2.ArrayData：0-2 / 3-5 / 6-8 是零件 X / Y / Z 轴在装配中的方向，9-11 是原点（m）。"""
    m = np.array(matrix[:12], float)
    axes = m[:9].reshape(3, 3)
    return m[9:12] * 1000 + np.asarray(local_mm, float) @ axes


def world_to_local(matrix, world_mm) -> np.ndarray:
    m = np.array(matrix[:12], float)
    axes = m[:9].reshape(3, 3)
    return axes @ (np.asarray(world_mm, float) - m[9:12] * 1000)


def _coord(p: np.ndarray, key: str) -> float:
    if key in ("x", "y", "z"):
        return float(p["xyz".index(key)])
    if key.startswith("r_") and len(key) == 4:
        i, j = "xyz".index(key[2]), "xyz".index(key[3])
        return float(math.hypot(p[i], p[j]))
    raise ValueError(f"未知坐标 {key}")


def score_pose_points(transforms: dict, aliases: dict[str, str], pose: dict, frame_role: str, tol_mm: float) -> dict:
    """pose.points: [{role, local: [x,y,z] mm, expect: {x|y|z|r_xy|r_xz|r_yz: mm}, abs: [x]}]。
    坐标在 frame_role 零件的局部坐标系里取；abs 里的坐标比绝对值（容许整体镜像摆放）。"""
    frame = transforms[aliases[frame_role]]
    checks = []
    for item in pose.get("points") or []:
        world = local_to_world(transforms[aliases[item["role"]]], item.get("local") or [0, 0, 0])
        p = world_to_local(frame, world)
        for key, want in (item.get("expect") or {}).items():
            got = _coord(p, key)
            if key in (item.get("abs") or []):
                got, want = abs(got), abs(float(want))
            err = abs(got - float(want))
            checks.append({"role": item["role"], "coord": key, "got": round(got, 3), "expected": float(want),
                           "err_mm": round(err, 3), "ok": err <= tol_mm})
    max_err = max((c["err_mm"] for c in checks), default=0.0)
    return {"checks": checks, "max_err_mm": max_err, "ok": all(c["ok"] for c in checks)}


def analyze_poses(results: list[dict], spec: dict) -> dict:
    limit = int(spec.get("max_interference") or 0)
    rows = []
    for item in results:
        points_ok = not item.get("error") and bool((item.get("points") or {}).get("ok"))
        clean = int(item.get("interference_count") or 0) <= limit
        still = not item.get("drifted_fixed")
        # 与马达模式同一组权重；位姿没摆对时“无穿模 / 无漂移”不给分
        checks = [] if item.get("error") else [
            make_check("kinematics", float(points_ok)),
            make_check("interference", float(clean and points_ok)),
            make_check("fixed", float(still and points_ok)),
        ]
        rows.append({**item, "ok": bool(points_ok and clean and still), "score": score_checks(checks),
                     "checks": checks})
    passed = sum(r["ok"] for r in rows)
    total = len(rows) or 1
    return {
        "mode": "poses",
        "passed": bool(rows) and passed == len(rows),
        "score": round(sum(r["score"] for r in rows) / total, 2),
        "score_v1": round(100.0 * passed / total, 2),
        "poses": rows,
    }


# 角度配合在 SolidWorks 里没有符号：转向由 flip 决定，平面法向朝向（零件正装/反装、翻面）由 align 决定。
# 这些是求解器与零件摆放的约定，不是被测装配的属性，所以评测逐个尝试，取点位误差最小的一种。
ANGLE_VARIANTS = ({"flip": False}, {"flip": True}, {"flip": False, "align": "anti"}, {"flip": True, "align": "anti"})


def _pose_offset(session, a: str, b: str, offset_mm: float) -> str:
    """带符号的临时距离配合。闭环机构里 SolidWorks 对不带 flip 的距离配合可能直接报过定义
    （连当前距离也不行），_offset_mate 来不及翻转，这里再试一次 flip。"""
    try:
        return session._offset_mate(a, b, offset_mm)["name"]
    except Exception:
        want = offset_mm / 1000
        name = session._mate("distance", a, b, distance=abs(want), align="aligned", flip=True)["name"]
        side = session._mate_side(name)
        if side is not None and side * want < 0:
            session._rollback_mates([name])
            raise
        return name


def run_poses(session, spec: dict) -> dict:
    """session: AssemblySession（已有装配体）。

    spec.poses[i].set 按运动链顺序列出临时配合，每项可带 points（只由该项及之前各项决定的点）：
    逐项加配合，按该项的 points 在 ANGLE_VARIANTS 中贪心挑选；最后对 pose.points 与各项 points 一起判定，
    再做静态干涉检查和固定件漂移检查，删掉本位姿的临时配合。"""
    aliases = session.aliases
    frame_role = spec.get("frame") or (spec.get("expect_fixed") or [None])[0]
    tol = float(spec.get("tolerance_mm") or 1.5)

    def transforms() -> dict:
        return {c["name"]: c["transform"] for c in session.asmwf("components").get("components") or []}

    def make(item: dict, variant: dict) -> str:
        kind = item["type"]
        if kind == "angle":
            extra = {"angleDeg": float(item["angle_deg"]), **variant}
            return session._mate("angle", item["a"], item["b"], **extra)["name"]
        if kind == "offset":
            return _pose_offset(session, item["a"], item["b"], float(item["offset_mm"]))
        if kind == "distance":
            return session._mate("distance", item["a"], item["b"], distance=float(item["distance_mm"]) / 1000)["name"]
        raise ValueError(f"位姿配合类型 {kind} 不支持")

    def broken(names: list[str]) -> list[str]:
        return [m.get("name") for m in session.asmwf("mates").get("mates") or []
                if m.get("name") in names and int(m.get("errorCode") or 0) != 0]

    base = transforms()
    fixed_roles = spec.get("expect_fixed") or []
    results = []
    for pose in spec.get("poses") or []:
        created: list[str] = []
        row: dict[str, Any] = {"name": pose.get("name"), "variants": []}
        for item in pose.get("set") or []:
            variants = ANGLE_VARIANTS if item["type"] == "angle" else ({},)
            best, kept, last_error = None, None, None
            for variant in variants:
                try:
                    name = make(item, variant)
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    continue
                if broken([name]):
                    last_error = f"临时配合无解: {name}"
                    session._rollback_mates([name])
                    continue
                points = score_pose_points(transforms(), aliases, {"points": item.get("points") or []},
                                           frame_role, tol)
                if best is None or points["max_err_mm"] < best[1]["max_err_mm"]:
                    best = (variant, points)
                if points["ok"]:
                    kept = name
                    break
                session._rollback_mates([name])
            if best is None:
                row["error"] = last_error or "无法建立临时配合"
                break
            if kept is None:
                try:
                    kept = make(item, best[0])
                except Exception as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    break
            created.append(kept)
            row["variants"].append(best[0])
        if "error" not in row:
            all_points = [p for item in pose.get("set") or [] for p in item.get("points") or []]
            all_points += pose.get("points") or []
            row["points"] = score_pose_points(transforms(), aliases, {"points": all_points}, frame_role, tol)
            inter = session.check_interference({})
            row["interference_count"] = int(inter.get("interferenceCount") or 0)
            row["interference_mm3"] = inter.get("totalVolume_mm3")
            if row["interference_count"]:
                row["interference_pairs"] = inter.get("interferences")
            now = transforms()
            row["drifted_fixed"] = [
                r for r in fixed_roles
                if sum(abs(float(a) - float(b)) for a, b in zip(base[aliases[r]], now[aliases[r]])) > 1e-4
            ]
        session._rollback_mates(created)
        results.append(row)
    return analyze_poses(results, spec)


def sample_motion(base_url: str, spec: dict, driver_sw: str, driver_ref: str) -> dict:
    import requests

    http = requests.Session()

    def post(path, body=None):
        data = http.post(f"{base_url.rstrip('/')}{path}", json=body or {}, timeout=HTTP_TIMEOUT_S).json()
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
    def sample(times):
        return post("/api/asmwf/motion-sample", {"times": times, "interference": False}).get("frames") or []

    kinematic = sample_chunked(sample, [duration * i / (dense - 1) for i in range(dense)])
    kinematic, sampling = refine_aliased(sample, driver_sw, duration, kinematic)
    sparse = post("/api/asmwf/motion-sample", {
        "times": [duration * i / (frames - 1) for i in range(frames)], "interference": True,
    })
    return {"frames": kinematic, "interference_frames": sparse.get("frames") or [], "sampling": sampling}


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
        # 与 local_to_world 同一约定：ArrayData 0-2 / 3-5 / 6-8 是零件 X / Y / Z 轴（行向量），9-11 是原点（m）。
        # gp_Trsf 按列向量书写，所以旋转取转置；STEP 读入后是 mm，平移也换算成 mm。
        trsf = gp_Trsf()
        m = [float(v) for v in matrix]
        trsf.SetValues(
            m[0], m[3], m[6], m[9] * 1000,
            m[1], m[4], m[7], m[10] * 1000,
            m[2], m[5], m[8], m[11] * 1000,
        )
        return shape.Located(TopLoc_Location(trsf))

    dist = BRepExtrema_DistShapeShape(locate(load(step_a), matrix_a), locate(load(step_b), matrix_b))
    dist.Perform()
    if not dist.IsDone():
        return None
    return float(dist.Value())
