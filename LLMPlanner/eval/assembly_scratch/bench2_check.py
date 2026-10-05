"""bench_cases2 的离线自检，不需要 SolidWorks。

    python -m eval.assembly_scratch.bench2_check [case ...]

对每个测例做四项检查，任何一项不过都说明测例本身有错（而不是评测器）：

1. 关节：layout 在所有取样位形下满足 steps 里的每个关节——孔心重合、固定副相对转角不变、
   滑块导向面法向不变且偏距不变、齿轮中心距对且纯滚动、螺旋副导程对；
2. 穿模：按各角色的 z 层与平面轮廓（孔挖掉、销略缩小）逐帧两两求交，啮合齿轮对不查；
3. 装配 rubric：按 steps 合成 SolidWorks 形式的组件与配合（实体几何取自 layout），用 geomcheck.assembly_rubric
   打分，必须是 100（含解析锚点）；
4. 运动规格：马达测例合成整周的采样帧交给 analyze_frames，必须通过且 100 分；
   多位姿测例检查每个位姿的点位与角度配合和 layout 一致。

SolidWorks 求解器的行为（闭环跳解、求解超时）这里查不出来，仍要用 build_reference_assemblies 搭参考装配体、
rescore_assemblies --reference 做最终的阳性对照。
"""

from __future__ import annotations

import json
import math
import sys

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, box

from eval.assembly_scratch.bench_cases2 import (
    CASES2, Case2, attach, fold, holes_of, plane_normal, plane_offset, pose_of, thickness, unwrap_deg,
)
from geomcheck.assembly_motion import analyze_frames, score_pose_points
from geomcheck.assembly_rubric import CONCENTRIC, COINCIDENT, CYLINDER, DISTANCE, GEAR, PLANE, SCREW, evaluate

PIN_SHRINK = 0.97     # 销 / 轴与同径孔名义上贴合，略缩小以免多边形近似误报
AREA_TOL = 0.3        # mm²
POS_TOL = 1e-3        # mm


# ------------------------------------------------------------------ 几何

def _holes(shape, p):
    for x, y, d in holes_of(p):
        shape = shape.difference(Point(x, y).buffer(d / 2, quad_segs=32))
    return shape


def part_shape(p: dict):
    k = p["kind"]
    if k == "plate":
        return _holes(box(p["x"][0], p["y"][0], p["x"][1], p["y"][1]), p)
    if k == "link":
        return _holes(LineString([(0, 0), (p["L"], 0)]).buffer(p["w"] / 2, quad_segs=32), p)
    if k == "pin":
        return Point(0, 0).buffer(p["d"] / 2 * PIN_SHRINK, quad_segs=32)
    if k == "disk":
        return _holes(Point(0, 0).buffer(p["od"] / 2, quad_segs=48), p)
    if k == "gear":
        return _holes(Point(0, 0).buffer(p["m"] * (p["z"] + 2) / 2, quad_segs=64), p)
    if k == "ring":
        return Point(0, 0).buffer(p["od"] / 2, quad_segs=64).difference(
            Point(0, 0).buffer(p["m"] * (p["z"] - 2) / 2, quad_segs=64))
    raise ValueError(k)


def world_shape(c: Case2, s, role: str, cache: dict):
    p = c.parts[c.roles[role]]
    base = cache.setdefault(c.roles[role], part_shape(p))
    x, y, deg, _ = pose_of(c, s, role)
    return affinity.translate(affinity.rotate(base, deg, origin=(0, 0)), x, y)


def matrix(x, y, deg, z) -> list[float]:
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [ca, sa, 0.0, -sa, ca, 0.0, 0.0, 0.0, 1.0, x / 1000, y / 1000, z / 1000, 1.0, 0.0, 0.0, 0.0]


def hole_world(c: Case2, s, role: str, hole) -> tuple[float, float]:
    x, y, deg, _ = pose_of(c, s, role)
    hx, hy, _ = attach((x, y, deg), hole or (0.0, 0.0))
    return hx, hy


def _rel(a: float, b: float, period: float = 360.0) -> float:
    return (a - b + period / 2) % period - period / 2


# ------------------------------------------------------------------ 1. 关节

def check_joints(c: Case2) -> list[str]:
    errs: list[str] = []
    samples = list(c.samples)
    for step in c.steps:
        kind = step[0]
        if kind in ("fix", "rev", "cyl"):
            _, a, ha, b, hb = step
            rel0 = None
            for s in samples:
                pa, pb = hole_world(c, s, a, ha), hole_world(c, s, b, hb)
                gap = math.dist(pa, pb)
                if gap > POS_TOL:
                    errs.append(f"{kind} {a}-{b} @ {s}: 孔心相距 {gap:.4f} mm")
                    break
                if kind == "fix":
                    rel = _rel(pose_of(c, s, a)[2], pose_of(c, s, b)[2])
                    if rel0 is None:
                        rel0 = rel
                    elif abs(_rel(rel, rel0)) > 1e-6:
                        errs.append(f"fix {a}-{b} @ {s}: 相对转角变了 {rel - rel0:.4f}°")
                        break
        elif kind in ("slide", "coinc"):
            _, a, pa, b, pb = step
            off0 = None
            for s in samples:
                na = plane_normal(pose_of(c, s, a)[2], pa)
                nb = plane_normal(pose_of(c, s, b)[2], pb)
                if abs(abs(np.dot(na, nb)) - 1) > 1e-9:
                    errs.append(f"{kind} {a}.{pa}-{b}.{pb} @ {s}: 面不平行")
                    break
                off = plane_offset(c, s, a, pa, b, pb)
                if kind == "coinc" and abs(off) > POS_TOL:
                    errs.append(f"coinc {a}.{pa}-{b}.{pb} @ {s}: 面不重合，偏 {off:.4f} mm")
                    break
                if off0 is None:
                    off0 = off
                elif abs(off - off0) > POS_TOL:
                    errs.append(f"slide {a}.{pa}-{b}.{pb} @ {s}: 偏距由 {off0:.3f} 变为 {off:.3f}")
                    break
        elif kind == "mesh":
            _, a, b = step
            ga, gb = c.parts[c.roles[a]], c.parts[c.roles[b]]
            internal = "ring" in (ga["kind"], gb["kind"])
            m = ga["m"]
            if gb["m"] != m:
                errs.append(f"mesh {a}-{b}: 模数不同")
                continue
            want = m * (abs(ga["z"] - gb["z"]) if internal else ga["z"] + gb["z"]) / 2
            seq = []
            for s in samples:
                xa, ya, da, _ = pose_of(c, s, a)
                xb, yb, db, _ = pose_of(c, s, b)
                cd = math.hypot(xb - xa, yb - ya)
                if abs(cd - want) > POS_TOL:
                    errs.append(f"mesh {a}-{b} @ {s}: 中心距 {cd:.4f}，应为 {want:g}")
                    break
                seq.append((da, db, math.degrees(math.atan2(yb - ya, xb - xa))))
            else:
                da_u, db_u, ph_u = (unwrap_deg([v[i] for v in seq]) for i in range(3))
                for i in range(1, len(seq)):
                    ra = (da_u[i] - da_u[0]) - (ph_u[i] - ph_u[0])
                    rb = (db_u[i] - db_u[0]) - (ph_u[i] - ph_u[0])
                    resid = ra * ga["z"] - rb * gb["z"] if internal else ra * ga["z"] + rb * gb["z"]
                    if abs(resid) > 1e-4 * max(ga["z"], gb["z"]):
                        errs.append(f"mesh {a}-{b} @ {samples[i]}: 不是纯滚动（残差 {resid:.4f}）")
                        break
        elif kind == "screw":
            _, screw, nut, lead, _ = step
            seq = [(pose_of(c, s, screw)[2] - pose_of(c, s, nut)[2], pose_of(c, s, nut)[3] - pose_of(c, s, screw)[3])
                   for s in samples]
            rot = unwrap_deg([v[0] for v in seq])
            for i in range(1, len(seq)):
                dz = seq[i][1] - seq[0][1]
                if abs(abs(dz) - abs(lead * (rot[i] - rot[0]) / 360)) > POS_TOL:
                    errs.append(f"screw {screw}-{nut} @ {samples[i]}: 位移 {dz:.4f} 与导程不符")
                    break
    return errs


# ------------------------------------------------------------------ 2. 穿模

def check_interference(c: Case2) -> list[str]:
    skip = {frozenset(st[1:3]) for st in c.steps if st[0] == "mesh"}
    skip |= {frozenset(st[1:3]) for st in c.steps if st[0] == "screw"}
    roles = list(c.roles)
    cache: dict = {}
    hits: dict[frozenset, tuple] = {}
    for s in c.samples:
        shapes, zr = {}, {}
        for r in roles:
            shapes[r] = world_shape(c, s, r, cache)
            z = pose_of(c, s, r)[3]
            zr[r] = (z, z + thickness(c.parts[c.roles[r]]))
        for i, a in enumerate(roles):
            for b in roles[i + 1:]:
                key = frozenset((a, b))
                if key in skip or key in hits:
                    continue
                if min(zr[a][1], zr[b][1]) - max(zr[a][0], zr[b][0]) <= 1e-6:
                    continue
                area = shapes[a].intersection(shapes[b]).area
                if area > AREA_TOL:
                    hits[key] = (s, area, zr[a], zr[b])
    return [f"穿模 {'/'.join(sorted(k))} @ {v[0]}: {v[1]:.1f} mm²，z {v[2]} vs {v[3]}" for k, v in hits.items()]


# ------------------------------------------------------------------ 3. 合成配合跑装配 rubric

class _DSU:
    def __init__(self, items):
        self.p = {x: x for x in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def rigid_sets(c: Case2) -> _DSU:
    dsu = _DSU(c.roles)
    grounded = [st[1] for st in c.steps if st[0] == "ground"]
    for r in grounded[1:]:
        dsu.union(r, grounded[0])
    for st in c.steps:
        if st[0] == "fix":
            dsu.union(st[1], st[3])
    return dsu


def body_of(c: Case2, dsu: _DSU) -> dict[str, str]:
    """角色 → rubric 里的刚体组名；flex 销按参考搭法里它固定在哪一侧归组。"""
    by_role = {r: gname for gname, members in c.rubric["bodies"].items() for r in members}
    comp_body = {}
    for r, gname in by_role.items():
        comp_body.setdefault(dsu.find(r), gname)
    return {r: by_role.get(r) or comp_body.get(dsu.find(r)) for r in c.roles}


def analytic_anchors(c: Case2) -> dict:
    dsu = rigid_sets(c)
    bmap = body_of(c, dsu)
    bodies = c.rubric["bodies"]
    out = {}
    for index, spec in enumerate(c.rubric["joints"]):
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if not set(types) & {"revolute", "cylindrical", "screw"}:
            continue
        want = set(spec["bodies"])
        axis = None
        for st in c.steps:
            if st[0] in ("rev", "cyl") and {bmap[st[1]], bmap[st[3]]} == want:
                axis = hole_world(c, c.s0, st[1], st[2])
                break
            if st[0] == "screw" and {bmap[st[1]], bmap[st[2]]} == want:
                axis = hole_world(c, c.s0, st[1], None)
                break
        if axis is None:
            raise ValueError(f"{c.name}: 找不到关节 {spec['bodies']} 的搭建步骤")
        anchors = []
        for gname in spec["bodies"]:
            r0 = bodies[gname][0]
            x, y, deg, _ = pose_of(c, c.s0, r0)
            a = math.radians(deg)
            dx, dy = axis[0] - x, axis[1] - y
            local = (dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a))
            anchors.append({"role": r0, "point_mm": [round(local[0], 3), round(local[1], 3), 0.0],
                            "dir": [0.0, 0.0, 1.0]})
        item = {"anchors": anchors}
        if "screw" in types:
            item["lead_mm"] = next(float(st[3]) for st in c.steps if st[0] == "screw")
        out[str(index)] = item
    return out


def synth_assembly(c: Case2, s=None) -> tuple[list[dict], list[dict]]:
    s = c.s0 if s is None else s
    grounded = {st[1] for st in c.steps if st[0] == "ground"}
    poses = {r: pose_of(c, s, r) for r in c.roles}
    comps = [{"name": r, "transform": matrix(*poses[r][:3], poses[r][3]), "isFixed": r in grounded,
              "constrainedStatus": 0} for r in c.roles]

    def cyl(role, hole):
        x, y = hole_world(c, s, role, hole)
        return {"refType": CYLINDER, "params": [x / 1000, y / 1000, poses[role][3] / 1000, 0.0, 0.0, 1.0]}

    def pln(role, plane):
        x, y, deg, z = poses[role]
        return {"refType": PLANE, "params": [x / 1000, y / 1000, z / 1000, *plane_normal(deg, plane)]}

    mates = []

    def add(mtype, a, b, ents, **extra):
        mates.append({"name": f"m{len(mates)}", "mateType": mtype, "components": [a, b], "entities": ents,
                      "errorCode": 0, **extra})

    for st in c.steps:
        kind = st[0]
        if kind in ("fix", "rev", "cyl"):
            _, a, ha, b, hb = st
            add(CONCENTRIC, a, b, [cyl(a, ha), cyl(b, hb)], **({"lockRotation": True} if kind == "fix" else {}))
            if kind != "cyl":
                add(DISTANCE, a, b, [pln(a, "front"), pln(b, "front")])
        elif kind in ("slide", "coinc"):
            _, a, pa, b, pb = st
            add(DISTANCE if kind == "slide" else COINCIDENT, a, b, [pln(a, pa), pln(b, pb)])
        elif kind == "mesh":
            _, a, b = st
            ga, gb = c.parts[c.roles[a]], c.parts[c.roles[b]]
            internal = "ring" in (ga["kind"], gb["kind"])
            add(GEAR, a, b, [cyl(a, None), cyl(b, None)], gearRatioNumerator=ga["z"], gearRatioDenominator=gb["z"],
                reverse=not internal)
        elif kind == "screw":
            _, screw, nut, lead, hole = st
            add(SCREW, screw, nut, [cyl(screw, None), cyl(nut, hole)], revolutionValue=lead / 1000)
    return comps, mates


def check_rubric(c: Case2) -> tuple[list[str], dict]:
    comps, mates = synth_assembly(c)
    rubric = json.loads(json.dumps(c.rubric))
    for index, item in analytic_anchors(c).items():
        rubric["joints"][int(index)].update(item)
    result = evaluate(comps, mates, {r: r for r in c.roles}, rubric)
    errs = []
    if result["score"] != 100:
        bad = {k: v for k, v in result["parts"].items() if v != 1}
        errs.append(f"rubric {result['score']}：{bad}；活动度 {result['mobility']['actual']}/{result['mobility']['target']}；"
                    f"关节 {[j for j in result['joints'] if not j['ok']]}；刚体组 "
                    f"{[g_ for g_ in result['rigid_groups'] if not g_['ok']]}")
    return errs, result


# ------------------------------------------------------------------ 4. 运动规格

def _role_plane(ref: str) -> tuple[str, str]:
    role, rest = ref.split(":")
    return role, rest[rest.index("(") + 1:rest.index(")")]


def check_motion(c: Case2) -> tuple[list[str], dict]:
    spec = c.motion
    errs = []
    if spec.get("mode") == "poses":
        for pose in spec["poses"]:
            transforms = {r: matrix(*pose_of(c, pose["name"], r)[:3], pose_of(c, pose["name"], r)[3]) for r in c.roles}
            pts = [p for item in pose["set"] for p in item.get("points") or []] + (pose.get("points") or [])
            res = score_pose_points(transforms, {r: r for r in c.roles}, {"points": pts}, spec["frame"], 0.05)
            if not res["ok"]:
                errs.append(f"位姿 {pose['name']} 点位与 layout 不符：{[x for x in res['checks'] if not x['ok']]}")
            for item in pose["set"]:
                if item["type"] == "offset":
                    (ra, pa), (rb, pb) = (_role_plane(item[k]) for k in ("a", "b"))
                    got = plane_offset(c, pose["name"], ra, pa, rb, pb)
                    if abs(got - item["offset_mm"]) > 0.02:
                        errs.append(f"位姿 {pose['name']} 偏距 {ra}/{rb}: 规格 {item['offset_mm']}，layout {got:.3f}")
                    continue
                if item["type"] != "angle":
                    continue
                ra, rb = item["a"].split(":")[0], item["b"].split(":")[0]
                got = fold(pose_of(c, pose["name"], ra)[2] - pose_of(c, pose["name"], rb)[2])
                if abs(got - item["angle_deg"]) > 0.02:
                    errs.append(f"位姿 {pose['name']} 角度 {ra}/{rb}: 规格 {item['angle_deg']}，layout {got}")
        return errs, {}
    n = 121
    driver0 = float(c.s0)
    frames = []
    for i in range(n):
        s = driver0 + float(spec.get("sweep_deg") or 360) * i / (n - 1)
        frames.append({"transforms": {r: matrix(*pose_of(c, s, r)[:3], pose_of(c, s, r)[3]) for r in c.roles}})
    result = analyze_frames(frames, spec, {r: r for r in c.roles}, [{} for _ in range(13)])
    if not result["passed"] or result["score"] != 100:
        errs.append(f"运动规格不过：score={result['score']} kin={result['kinematics']} stuck={result['stuck']} "
                    f"drift={result['drifted_fixed']}")
    return errs, result


# ------------------------------------------------------------------ 汇总

def check_case(c: Case2) -> list[str]:
    errs = []
    try:
        errs += check_joints(c)
        errs += check_interference(c)
        errs += check_rubric(c)[0]
        errs += check_motion(c)[0]
    except Exception as exc:  # layout 在某个位形无解等
        errs.append(f"{type(exc).__name__}: {exc}")
    return errs


def main() -> None:
    wanted = sys.argv[1:]
    bad = 0
    for name, c in CASES2.items():
        if wanted and name not in wanted:
            continue
        errs = check_case(c)
        bad += bool(errs)
        print(f"{'OK  ' if not errs else 'FAIL'} {name:24s} comps={len(c.roles):2d} dof={c.rubric['mobility']}")
        for e in errs:
            print("     -", e)
    print(f"{bad} 个测例未通过" if bad else "全部通过")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
