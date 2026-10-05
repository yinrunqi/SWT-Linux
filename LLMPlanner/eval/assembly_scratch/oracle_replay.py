"""可达性验证：每个 bench_cases2 测例写出一条只用 v3 公开工具的装配轨迹，在 SolidWorks 里重放，
再用评测器同一套打分（score_assembly_rubric + _motion）确认能拿满分。不调用模型。

与 build_reference2 的区别：参考装配用了会话内部的带符号距离配合（_pose_offset），这里一律走
智能体可见的工具；会话按 assemble() 的方式创建（workflow=v3、只开放 v3 工具、默认超时），
收尾按智能体的做法 check_dof → check_motion / check_pose → save_assembly → finish。

  python eval/assembly_scratch/oracle_replay.py [name ...]     # 缺省全部
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from eval import eval_assembly_scratch as E  # noqa: E402
from eval.assembly_scratch.bench_cases2 import CASES2, Case2, plane_offset, pose_of, r2, ref  # noqa: E402

SW = "http://localhost:5100"
OUT = ROOT / "eval" / "artifacts" / "bench2_oracle"
# 与 build_reference_assemblies._session() 相同；智能体默认 180 s，大装配的单条配合在 SolidWorks 里就要 2–3 分钟
SESSION_TIMEOUT_S = 900


def trajectory(c: Case2, parts: dict[str, Path]) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    placed: dict[str, float] = {}

    def put(role: str) -> None:
        if role in placed:
            return
        x, y, deg, z = pose_of(c, c.s0, role)
        args = {"part": str(parts[role]), "name": role, "at": [r2(x), r2(y), r2(z)]}
        if abs(deg % 360) > 1e-9:
            args["rotate_deg"] = r2(deg % 360)
        calls.append(("insert_component", args))
        placed[role] = z

    for step in c.steps:
        kind = step[0]
        if kind == "ground":
            put(step[1])
            calls.append(("ground_component", {"component": step[1]}))
        elif kind in ("fix", "rev"):
            _, a, ha, b, hb = step
            put(b)
            put(a)
            calls.append(("joint", {"type": {"fix": "fixed", "rev": "revolute"}[kind],
                                    "a": ref(c, a, ha), "b": ref(c, b, hb),
                                    "face_a": f"{a}:plane(front)", "face_b": f"{b}:plane(front)",
                                    "offset": r2(placed[a] - placed[b])}))
        elif kind == "cyl":
            _, a, ha, b, hb = step
            put(b)
            put(a)
            calls.append(("joint", {"type": "cylindrical", "a": ref(c, a, ha), "b": ref(c, b, hb)}))
        elif kind in ("slide", "coinc"):
            _, a, pa, b, pb = step
            put(b)
            put(a)
            off = r2(plane_offset(c, c.s0, a, pa, b, pb)) if kind == "slide" else 0.0
            contact = _contact_faces(c, a, pa, b, pb, off)
            if abs(off) < 1e-9:
                calls.append(("mate", {"type": "coincident", "a": f"{a}:plane({pa})", "b": f"{b}:plane({pb})",
                                       "align": "closest"}))
            elif contact:
                calls.append(("mate", {"type": "coincident", "a": contact[0], "b": contact[1], "align": "anti"}))
            else:
                calls.append(("mate", {"type": "distance", "a": f"{a}:plane({pa})", "b": f"{b}:plane({pb})",
                                       "distance": abs(off), "align": "closest"}))
        elif kind == "mesh":
            _, a, b = step
            pa, pb = c.parts[c.roles[a]], c.parts[c.roles[b]]
            args = {"a": a, "b": b, "teeth_a": pa["z"], "teeth_b": pb["z"], "module": pa["m"]}
            if pb["kind"] == "ring" or pa["kind"] == "ring":
                args["internal"] = True
            calls.append(("mesh_gears", args))
        elif kind == "screw":
            _, screw, nut, lead, hole = step
            put(screw)
            put(nut)
            calls.append(("screw_pair", {"screw": screw, "nut": nut, "lead": lead,
                                         "screw_ref": ref(c, screw), "nut_ref": ref(c, nut, hole)}))
        else:
            raise ValueError(kind)
    calls.append(("check_dof", {"expected_mobility": c.rubric["mobility"]}))
    m = c.motion
    if m.get("mode") == "poses":
        calls.append(("check_pose", {"poses": m["poses"], "frame": m["frame"],
                                     "expect_fixed": m.get("expect_fixed") or [],
                                     "tolerance_mm": m.get("tolerance_mm") or 1.5}))
    else:
        calls.append(("check_motion", {"driver": m["driver"], "driver_ref": f"{m['driver']}:cyl(i=0)",
                                       **{k: m[k] for k in ("sweep_deg", "frames", "rpm") if k in m},
                                       "expect_moving": m.get("expect_moving") or [],
                                       "expect_fixed": m.get("expect_fixed") or []}))
    calls += [("save_assembly", {}), ("finish", {"summary": "oracle trajectory"})]
    return calls


def _extent(p: dict, axis: str):
    if p["kind"] == "plate" and axis in "XY":
        return p[axis.lower()]
    if axis == "Z" and p["kind"] in ("plate", "disk", "link"):
        return [0, p["t"]]
    if axis == "Z" and p["kind"] in ("gear", "ring"):
        return [0, p["face"]]
    return None


def _contact_faces(c: Case2, a: str, pa: str, b: str, pb: str, off: float):
    """非零偏距的平面滑动：公开 mate 的 distance 不能指定落在哪一侧（服务端的 flip 没开放），
    SolidWorks 选错侧就过定义。两件若各有一个外表面正好贴合（法向相反），改用面贴合。"""
    axis = {"front": "Z", "top": "Y", "right": "X"}.get(pa)
    if axis is None or pa != pb:
        return None
    if axis != "Z" and any(abs(pose_of(c, c.s0, r)[2] % 360) > 1e-6 for r in (a, b)):
        return None
    ea, eb = (_extent(c.parts[c.roles[r]], axis) for r in (a, b))
    if not ea or not eb:
        return None
    if abs(ea[0] + off - eb[1]) < 1e-6:
        return f"{a}:face(-{axis}, outermost)", f"{b}:face(+{axis}, outermost)"
    if abs(ea[1] + off - eb[0]) < 1e-6:
        return f"{a}:face(+{axis}, outermost)", f"{b}:face(-{axis}, outermost)"
    return None


def pose_audit(c: Case2, session) -> dict:
    """诊断用（不算轨迹）：配合求解后各角色的原点与转角，和 layout(s0) 的设计位形比。"""
    import math
    comps = {x["name"]: x.get("transform") for x in session.asmwf("components").get("components") or []}
    out = {}
    for role in c.roles:
        tf = comps.get(session.aliases.get(role, ""))
        if not tf:
            out[role] = "缺失"
            continue
        x, y, deg, z = pose_of(c, c.s0, role)
        got = [v * 1000 for v in tf[9:12]]
        d = math.dist(got, (x, y, z))
        rot = math.degrees(math.atan2(tf[1], tf[0]))
        drot = 0.0 if c.parts[c.roles[role]]["kind"] == "pin" else abs((rot - deg + 180) % 360 - 180)
        if d > 0.05 or drot > 0.05:
            out[role] = {"at": [round(v, 2) for v in got], "want": [r2(x), r2(y), r2(z)],
                         "deg": round(rot, 2), "want_deg": r2(deg % 360)}
    return out


def _short(value, n=300) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)[:n]


def replay(name: str) -> dict:
    from planner.assembly_workflow.session import AssemblySession
    from planner.assembly_workflow.tools import tool_names

    c = CASES2[name]
    case = E._load_case(name)
    gold = E.CASES_DIR / "gold_parts" / case["gold_dir"]
    parts = {role: gold / f"{pid}.SLDPRT" for role, pid in case["gold_roles"].items()}
    dest = OUT / name / "assembly.SLDASM"
    dest.parent.mkdir(parents=True, exist_ok=True)
    calls = trajectory(c, parts)
    E._close(SW)
    session = AssemblySession(base_url=SW, workflow="v3", allowed_tools=tool_names("v3"), timeout=SESSION_TIMEOUT_S)
    slow: list[dict] = []
    raw_request = session._request

    def timed_request(method, path, body=None):
        t = time.perf_counter()
        try:
            return raw_request(method, path, body)
        finally:
            dt = time.perf_counter() - t
            if dt > 5:
                slow.append({"path": path, "seconds": round(dt, 1),
                             "body": _short({k: v for k, v in (body or {}).items() if k != "points"}, 200)})

    session._request = timed_request
    session.start_assembly(str(dest))
    log, failed, started = [], None, time.perf_counter()
    for i, (tool, args) in enumerate(calls):
        t0 = time.perf_counter()
        slow.clear()
        out = session.call(tool, args)
        if slow:
            print(f"       慢请求：{_short(slow, 600)}", flush=True)
        ok = bool(out.get("ok"))
        body = {k: v for k, v in out.items() if k != "ok"}
        log.append({"i": i, "tool": tool, "args": args, "ok": ok, "seconds": round(time.perf_counter() - t0, 1),
                    "result": body if ok else None, "error": None if ok else out.get("error"),
                    "slow_requests": list(slow)})
        shown = {k: body.get(k) for k in ("passed", "mobility", "matches_expected", "interference_pairs", "stuck",
                                          "poses", "note", "verified_by") if k in body} if ok else out.get("error")
        print(f"  [{i:2d}] {tool:16s} {'OK ' if ok else 'ERR'} {round(time.perf_counter() - t0):4d}s "
              f"{_short(shown, 400)}", flush=True)
        if tool == "check_dof":
            audit = pose_audit(c, session)
            print(f"  位形偏差（>0.05 mm / 0.05°）：{audit or '无'}", flush=True)
            log[-1]["pose_audit"] = audit
        if not ok:
            failed = {"i": i, "tool": tool, "error": out.get("error")}
            break
    infra_error = None
    try:
        session.save_assembly({})
    except Exception as exc:
        infra_error = f"save_assembly: {exc}"
    rubric = E.score_assembly_rubric(session, case)
    try:
        motion = E._motion(session, case["motion"], parts, SW)
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    E._close(SW)
    row = {"name": name, "case_id": c.case_id, "tool_calls": len(log), "planned_calls": len(calls),
           "failed_call": failed, "infra_error": infra_error,
           "rubric_score": (rubric or {}).get("score"), "motion_score": motion.get("score"),
           "motion_passed": motion.get("passed"), "seconds": round(time.perf_counter() - started, 1),
           "rubric": rubric, "motion": motion, "aliases": dict(session.aliases)}
    (OUT / name / "trajectory.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    (OUT / name / "result.json").write_text(json.dumps(row, ensure_ascii=False, indent=1, default=str),
                                            encoding="utf-8")
    return row


def _infra(row: dict | None, exc: Exception | None) -> bool:
    """SolidWorks 卡死 / 超时的痕迹，与评测器 infra_flags 用同一组标记。"""
    text = str(exc) if exc else json.dumps([row.get("failed_call"), row.get("infra_error"),
                                            (row.get("motion") or {}).get("error")], ensure_ascii=False, default=str)
    return any(m in text for m in (*E.INFRA_MARKERS, "没有活动的装配体文档"))


def main() -> None:
    from eval.assembly_scratch.sw_restart import restart_solidworks

    names = sys.argv[1:] or list(CASES2)
    for name in names:
        for attempt in range(3):
            print(f"== {name}" + (f"（重试 {attempt}）" if attempt else ""), flush=True)
            row, err = None, None
            try:
                row = replay(name)
            except Exception as exc:
                err = exc
                print(f"!! {name} {type(exc).__name__}: {exc}", flush=True)
            if row is not None:
                (OUT / name / f"result_attempt{attempt}.json").write_text(
                    json.dumps(row, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            if not _infra(row, err):
                break
            reason = err if row is None else [row.get("failed_call"), row.get("infra_error"),
                                              (row.get("motion") or {}).get("error")]
            print(f"!! {name} 基础设施异常：{_short(reason, 600)}", flush=True)
            restart_solidworks()
        if row is None:
            continue
        print(f"RESULT {name} calls={row['tool_calls']}/{row['planned_calls']} failed={row['failed_call'] and row['failed_call']['tool']} "
              f"rubric={row['rubric_score']} motion={row['motion_score']} retries={attempt} {row['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
