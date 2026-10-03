"""装配级 rubric：从 SolidWorks 实际配合推断关节与活动度，与工作流 / 工具无关。

每个配合按实体几何（装配坐标下的点、方向）写成两零件相对运动旋量 (v, ω) 上的线性约束行：
- 某一对刚体之间只用它们之间的直接配合，约束行的零空间就是这对的相对运动 → 关节类型；
- 全装配体所有配合（含齿轮等耦合）拼成雅可比，6·N_free − rank 就是当前位形下的瞬时活动度，
  闭环与空转自由度都会如实算进去。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# swMateType_e
COINCIDENT, CONCENTRIC, PERPENDICULAR, PARALLEL, TANGENT, DISTANCE, ANGLE = 0, 1, 2, 3, 4, 5, 6
GEAR, RACKPINION, LOCK, SCREW, LINEARCOUPLER, SLOT, HINGE = 10, 13, 16, 17, 18, 21, 22
COUPLINGS = {GEAR, RACKPINION, LINEARCOUPLER}
# swMateEntity2ReferenceType_e
POINT, LINE, CIRCLE, PLANE, CYLINDER, SPHERE, CONE = 0, 1, 2, 3, 4, 5, 7
AXIAL = {LINE, CIRCLE, CYLINDER, CONE}
# swConstrainedStatus_e
OVER_STATUS = {4: "over", 5: "no_solution", 6: "invalid"}

WEIGHTS = {"rigid_groups": 20, "joints": 25, "mobility": 15, "grounded": 10, "not_over_defined": 10, "couplings": 20}


# ------------------------------------------------------------------ 几何

def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _perp_basis(u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    helper = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = _unit(np.cross(u, helper))
    return e1, np.cross(u, e1)


def _entity(e: dict) -> tuple[int, np.ndarray, np.ndarray] | None:
    p = e.get("params")
    if p is None or len(p) < 6 or e.get("refType") is None:
        return None
    return int(e["refType"]), np.array(p[:3], float), _unit(p[3:6])


def _trans_row(n, p) -> np.ndarray:
    """沿 n 的平移约束（作用点 p）：n·(v + ω×p) = 0。"""
    return np.concatenate([n, np.cross(p, n)])


def _rot_row(axis) -> np.ndarray:
    return np.concatenate([np.zeros(3), axis])


def mate_rows(mate: dict) -> tuple[list[np.ndarray], str | None]:
    """一个低副配合在两零件相对旋量上的约束行。返回 (rows, 不支持的原因)。"""
    mtype = mate.get("mateType")
    if mate.get("kind") == "lock" or mtype == LOCK:
        return [np.eye(6)[i] for i in range(6)], None
    ents = [x for x in (_entity(e) for e in mate.get("entities") or []) if x]
    if len(ents) < 2:
        return [], "缺少实体几何"
    (ta, pa, ua), (tb, pb, ub) = ents[0], ents[1]
    types = {ta, tb}
    rows: list[np.ndarray] = []
    if mtype == DISTANCE and "distanceMin" in mate:
        return [], None  # 限位距离只限范围，不减少瞬时自由度
    if mtype in (COINCIDENT, DISTANCE, TANGENT):
        if ta == PLANE and tb == PLANE:
            e1, e2 = _perp_basis(ua)
            rows += [_trans_row(ua, pb), _rot_row(e1), _rot_row(e2)]
        elif PLANE in types and types & AXIAL:
            (_, pp, n), (_, pl, u) = (ents[0], ents[1]) if ta == PLANE else (ents[1], ents[0])
            if mtype == TANGENT:
                rows += [_trans_row(n, pl), _rot_row(_unit(np.cross(u, n)))]
            elif ta == CIRCLE or tb == CIRCLE:
                rows += [_trans_row(n, pl), *(_rot_row(e) for e in _perp_basis(n))]
            else:
                rows += [_trans_row(n, pl), _rot_row(_unit(np.cross(u, n)))]
        elif PLANE in types and POINT in types:
            (_, _, n), (_, pt, _) = (ents[0], ents[1]) if ta == PLANE else (ents[1], ents[0])
            rows.append(_trans_row(n, pt))
        elif types <= AXIAL:
            e1, e2 = _perp_basis(ua)
            rows += [_trans_row(e1, pb), _trans_row(e2, pb), _rot_row(e1), _rot_row(e2)]
        elif types == {POINT}:
            rows += [_trans_row(np.eye(3)[i], pb) for i in range(3)]
        elif POINT in types and types & AXIAL:
            (_, pl, u), (_, pt, _) = (ents[0], ents[1]) if ta in AXIAL else (ents[1], ents[0])
            e1, e2 = _perp_basis(u)
            rows += [_trans_row(e1, pt), _trans_row(e2, pt)]
        else:
            return [], f"未支持的实体组合 {sorted(types)}"
    elif mtype in (CONCENTRIC, HINGE):
        if types == {SPHERE} or (SPHERE in types and POINT in types):
            rows += [_trans_row(np.eye(3)[i], pb) for i in range(3)]
        elif types <= AXIAL | {POINT}:
            axis_ent = ents[0] if ta in AXIAL else ents[1]
            u, p = axis_ent[2], axis_ent[1]
            e1, e2 = _perp_basis(u)
            rows += [_trans_row(e1, p), _trans_row(e2, p)]
            if types <= AXIAL:
                rows += [_rot_row(e1), _rot_row(e2)]
            if mate.get("lockRotation"):
                rows.append(_rot_row(u))
        else:
            return [], f"未支持的同心实体 {sorted(types)}"
    elif mtype == PARALLEL:
        if ta in AXIAL | {PLANE} and tb in AXIAL | {PLANE}:
            if (ta == PLANE) == (tb == PLANE):
                rows += [_rot_row(e) for e in _perp_basis(ua)]
            else:
                rows.append(_rot_row(_unit(np.cross(ua, ub))))
    elif mtype in (PERPENDICULAR, ANGLE):
        cross = np.cross(ua, ub)
        if np.linalg.norm(cross) > 1e-6:
            rows.append(_rot_row(_unit(cross)))
        else:
            rows += [_rot_row(e) for e in _perp_basis(ua)]
    elif mtype == SCREW:
        e1, e2 = _perp_basis(ua)
        rows += [_trans_row(e1, pa), _trans_row(e2, pa), _rot_row(e1), _rot_row(e2)]
        lead = float(mate.get("revolutionValue") or 0)
        rows.append(np.concatenate([ua, np.cross(pa, ua) - lead / (2 * math.pi) * ua]))
    elif mtype == GEAR:
        if _gear_is_sync(mate) and np.linalg.norm(np.cross(ua, ub)) < 1e-6 \
                and np.linalg.norm(np.cross(pb - pa, ua)) < 1e-6:
            rows.append(_rot_row(ua))  # 同轴 1:1 同向齿轮 = 锁住相对转动
        else:
            return [], None
    elif mtype == SLOT:
        return [], "槽口配合未建模"
    else:
        return [], f"未支持的配合类型 {mtype}"
    return rows, None


def _gear_is_sync(mate: dict) -> bool:
    num, den = mate.get("gearRatioNumerator"), mate.get("gearRatioDenominator")
    return num is not None and den is not None and abs(float(num) - float(den)) < 1e-9 and not mate.get("reverse")


def classify_rows(rows: list[np.ndarray]) -> dict:
    """相对运动零空间 → 关节类型。"""
    if not rows:
        return {"type": "free", "dof": 6}
    m = np.vstack(rows)
    _, s, vt = np.linalg.svd(m)
    tol = max(m.shape) * (s[0] if len(s) else 1) * 1e-7
    rank = int((s > tol).sum())
    null = vt[rank:]
    dof = 6 - rank
    if dof == 0:
        return {"type": "fixed", "dof": 0}
    if dof == 1:
        v, w = null[0][:3], null[0][3:]
        if np.linalg.norm(w) < 1e-9:
            return {"type": "slider", "dof": 1, "axis": _unit(v).tolist()}
        pitch = float(np.dot(v, w) / np.dot(w, w))
        if abs(pitch) < 1e-6:
            return {"type": "revolute", "dof": 1, "axis": _unit(w).tolist()}
        return {"type": "screw", "dof": 1, "axis": _unit(w).tolist(), "lead_mm": 2 * math.pi * pitch * 1000}
    rot_dim = np.linalg.matrix_rank(null[:, 3:], tol=1e-7)
    if dof == 2:
        if rot_dim == 1:
            w = _unit(null[np.argmax(np.linalg.norm(null[:, 3:], axis=1))][3:])
            pure_t = [row[:3] for row in null if np.linalg.norm(row[3:]) < 1e-9]
            # 圆柱副：绕 w 转 + 沿 w 平移
            proj = null[:, :3] @ w
            return {"type": "cylindrical" if np.linalg.norm(proj) > 1e-9 or pure_t else "other", "dof": 2}
        return {"type": "planar_translation" if rot_dim == 0 else "other", "dof": 2}
    if dof == 3:
        if rot_dim == 3:
            return {"type": "ball", "dof": 3}
        if rot_dim == 1:
            return {"type": "planar", "dof": 3}
    return {"type": "other", "dof": dof}


def joint_axis(rows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray] | None:
    """两刚体相对运动里的转轴（装配坐标，m）：零空间中转动分量最大的旋量 (v, ω)，轴上一点 c = ω×v/|ω|²。"""
    if not rows:
        return None
    m = np.vstack(rows)
    _, s, vt = np.linalg.svd(m)
    tol = max(m.shape) * (s[0] if len(s) else 1) * 1e-7
    null = vt[int((s > tol).sum()):]
    if not len(null):
        return None
    twist = null[np.argmax(np.linalg.norm(null[:, 3:], axis=1))]
    v, w = twist[:3], twist[3:]
    if np.linalg.norm(w) < 1e-9:
        return None
    return np.cross(w, v) / np.dot(w, w), _unit(w)


def _to_local(matrix, world_mm: np.ndarray, direction: np.ndarray | None = None):
    m = np.array(matrix[:12], float)
    axes = m[:9].reshape(3, 3)
    point = axes @ (np.asarray(world_mm, float) - m[9:12] * 1000)
    return point, (None if direction is None else axes @ direction)


def derive_anchors(components: list[dict], mates: list[dict], aliases: dict[str, str], rubric: dict) -> dict:
    """从参考装配体导出每个关节的轴线锚点（两侧刚体组首个角色的局部坐标）与 screw 导程。
    返回 {joint 下标: {"anchors": [...], "lead_mm": ...}}；滑动副没有转轴，不导出锚点。"""
    by_name = {c["name"]: c for c in components}
    rows_out: list = []
    evaluate(components, mates, aliases, rubric, joint_rows_out=rows_out)
    bodies = rubric.get("bodies") or {}
    out: dict = {}
    for index, (spec, rows) in enumerate(zip(rubric.get("joints") or [], rows_out)):
        item: dict = {}
        # 只有单一转轴的关节才有确定的轴线；平面副、球副等的零空间里转轴位置不唯一
        axial = bool(rows) and classify_rows(rows)["type"] in ("revolute", "cylindrical", "screw")
        axis = joint_axis(rows) if axial else None
        if axis is not None:
            anchors = []
            for g in spec["bodies"]:
                role = (bodies.get(g) or [None])[0]
                sw = aliases.get(role)
                if sw not in by_name:
                    continue
                point, direction = _to_local(by_name[sw]["transform"], axis[0] * 1000, axis[1])
                direction = _unit(direction)
                direction = direction * np.sign(direction[np.argmax(np.abs(direction))])
                # 轴上离零件原点最近的点，与轴向位置无关
                point = point - np.dot(point, direction) * direction
                anchors.append({"role": role, "point_mm": [round(float(v), 3) for v in point],
                                "dir": [round(float(v), 6) for v in direction]})
            if anchors:
                item["anchors"] = anchors
        types = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if "screw" in types and rows:
            info = classify_rows(rows)
            if info.get("lead_mm") is not None:
                item["lead_mm"] = round(abs(float(info["lead_mm"])), 4)
        if item:
            out[str(index)] = item
    return out


def _line_gap(p1, d1, p2, d2) -> tuple[float, float]:
    """两条直线的 (距离 mm, |cos 夹角|)。"""
    p1, p2, d1, d2 = (np.asarray(x, float) for x in (p1, p2, d1, d2))
    cross = np.cross(d1, d2)
    if np.linalg.norm(cross) < 1e-6:
        dist = np.linalg.norm(np.cross(p2 - p1, d1))
    else:
        dist = abs(np.dot(p2 - p1, cross)) / np.linalg.norm(cross)
    return float(dist), float(abs(np.dot(d1, d2)))


def _anchor_consistency(axes: dict[int, tuple | None], specs: dict[int, list[dict]], tol_mm: float) -> dict[int, dict]:
    """同一角色上各关节转轴之间的距离 / 夹角，与参考装配体（锚点）的比较。

    只比相对关系，不比锚点在零件局部坐标里的绝对位置：生成的零件原点、朝向可以和标准零件不同
    （零件接口 rubric 按包围盒量孔位），但同一零件上两个孔的间距不变，所以“套错孔”仍能查出来。
    一个关节与超过一半的同伴对不上才判错，这样一个关节装错不会连累同一零件上装对的关节。"""
    by_role: dict[str, list[tuple[int, dict]]] = {}
    for index, anchors in specs.items():
        for item in anchors:
            by_role.setdefault(item["role"], []).append((index, item))
    fails: dict[int, int] = {i: 0 for i in specs}
    pairs: dict[int, int] = {i: 0 for i in specs}
    errs: dict[int, dict] = {i: {} for i in specs}
    for role, items in by_role.items():
        for x in range(len(items)):
            for y in range(x + 1, len(items)):
                (i, a), (j, b) = items[x], items[y]
                if i == j or axes.get(i) is None or axes.get(j) is None:
                    continue
                want_d, want_c = _line_gap(a["point_mm"], _unit(a["dir"]), b["point_mm"], _unit(b["dir"]))
                got_d, got_c = _line_gap(axes[i][0] * 1000, axes[i][1], axes[j][0] * 1000, axes[j][1])
                bad = abs(got_d - want_d) > tol_mm or abs(got_c - want_c) > 1e-3
                for k, other in ((i, j), (j, i)):
                    pairs[k] += 1
                    fails[k] += bad
                    errs[k][f"{role}:{other}"] = round(got_d - want_d, 3)
    return {i: {"anchor_err_mm": errs[i], "anchor_ok": fails[i] * 2 <= pairs[i]}
            for i in specs if pairs[i]}


# ------------------------------------------------------------------ 主流程

class _DSU:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        if a in self.parent and b in self.parent:
            self.parent[self.find(a)] = self.find(b)


def _gear_rolling_row(mate: dict, ea, eb) -> np.ndarray | None:
    """齿轮配合的旋量约束行 t·(v + ω×p)，p 为节点、t 为节点切向。轴线重合时返回 None（退回转速比约束）。
    约定 ω_b/ω_a = num/den = r_a/r_b；reverse 表示外啮合（两轴同向时），两轴反向时含义互换。"""
    (_, pa, ua), (_, pb, ub) = ea, eb
    d = pb - pa
    d = d - np.dot(d, ua) * ua
    dist = float(np.linalg.norm(d))
    if dist < 1e-9:
        return None
    u = d / dist
    k = float(mate.get("gearRatioNumerator") or 1) / float(mate.get("gearRatioDenominator") or 1)
    external = bool(mate.get("reverse")) != (float(np.dot(ua, ub)) < 0)
    if external:
        p = pa + u * dist * k / (1 + k)
    elif abs(k - 1) < 1e-9:
        return None
    elif k < 1:  # b 是内齿圈
        p = pa - u * dist * k / (1 - k)
    else:  # a 是内齿圈
        p = pa + u * dist * k / (k - 1)
    t = _unit(np.cross(ua, u))
    return np.concatenate([t, np.cross(p, t)])


def _mate_pair(mate: dict) -> tuple[str, str] | None:
    comps = [c for c in (mate.get("components") or []) if c]
    if len(comps) < 2 or comps[0] == comps[1]:
        return None
    return comps[0], comps[1]


def _global_mobility(names: list[str], fixed: set[str], mates: list[dict]) -> dict:
    m, free, skipped = _jacobian(names, fixed, mates)
    total = 6 * len(free)
    if m is None or total == 0:
        return {"mobility": total, "skipped": skipped}
    s = np.linalg.svd(m, compute_uv=False)
    rank = int((s > max(m.shape) * s[0] * 1e-7).sum())
    return {"mobility": total - rank, "skipped": skipped}


def mobility_modes(names: list[str], fixed: set[str], mates: list[dict]) -> dict:
    """全装配体的瞬时运动空间：活动度、冗余约束行数、每个非固定件相对机架剩余的自由度。
    rows 与 rank 的差就是冗余约束（平面闭环里重复约束同一轴向位置的那些行）。"""
    m, free, skipped = _jacobian(names, fixed, mates)
    total = 6 * len(free)
    if total == 0:
        return {"mobility": 0, "redundant": 0, "free": {}, "null": np.zeros((0, 0)), "columns": {}, "skipped": skipped}
    if m is None:
        null, rank, n_rows = np.eye(total), 0, 0
    else:
        _, s, vt = np.linalg.svd(m)
        rank = int((s > max(m.shape) * s[0] * 1e-7).sum())
        null, n_rows = vt[rank:].T, m.shape[0]
    columns = {n: i for i, n in enumerate(free)}
    per = {}
    for n, i in columns.items():
        block = null[6 * i:6 * i + 6]
        dof = int(np.linalg.matrix_rank(block, tol=1e-7)) if block.size else 0
        if dof:
            rot = int(np.linalg.matrix_rank(block[3:], tol=1e-7))
            per[n] = {"dof": dof, "rotations": rot, "translations": dof - rot}
    # 冗余 = 各对零件之间关节各自约束掉的自由度之和 − 全局秩（关节内部重复的行不算）
    groups: dict[frozenset, list[np.ndarray]] = {}
    pair_rank = 0
    for mate in mates:
        pair = _mate_pair(mate)
        if not pair or set(pair) <= fixed:
            continue
        local = mate_rows(mate)[0]
        if mate.get("mateType") == GEAR and not local:
            pair_rank += 1
        groups.setdefault(frozenset(pair), []).extend(local)
    for rows in groups.values():
        if rows:
            pair_rank += int(np.linalg.matrix_rank(np.vstack(rows), tol=1e-7))
    return {"mobility": total - rank, "redundant": max(0, pair_rank - rank), "free": per, "null": null,
            "columns": columns, "skipped": skipped, "rows": n_rows}


def relative_motion_free(modes: dict, a: str, b: str, row: np.ndarray) -> bool:
    """在当前运动空间里，b 相对 a 沿约束行 row（_trans_row / _rot_row）的速度是否还能非零。"""
    null, cols = modes["null"], modes["columns"]
    if not null.size:
        return False
    v = np.zeros(null.shape[0])
    if b in cols:
        v[6 * cols[b]:6 * cols[b] + 6] += row
    if a in cols:
        v[6 * cols[a]:6 * cols[a] + 6] -= row
    return bool(np.linalg.norm(v @ null) > 1e-6 * max(1.0, np.linalg.norm(row)))


def axial_row(point_m, axis) -> np.ndarray:
    return _trans_row(_unit(axis), np.asarray(point_m, float))


def _jacobian(names: list[str], fixed: set[str], mates: list[dict]):
    free = [n for n in names if n not in fixed]
    col = {n: i for i, n in enumerate(free)}
    rows = []
    skipped = []
    for mate in mates:
        pair = _mate_pair(mate)
        if not pair:
            continue
        a, b = pair
        mtype = mate.get("mateType")
        local, why = mate_rows(mate)
        if why:
            skipped.append({"mate": mate.get("name"), "reason": why})
        for r in local:
            row = np.zeros(6 * len(free))
            if b in col:
                row[6 * col[b]:6 * col[b] + 6] += r
            if a in col:
                row[6 * col[a]:6 * col[a] + 6] -= r
            rows.append(row)
        if mtype == GEAR and not local:
            ents = [x for x in (_entity(e) for e in mate.get("entities") or []) if x]
            if len(ents) >= 2:
                r = _gear_rolling_row(mate, ents[0], ents[1])
                row = np.zeros(6 * len(free))
                if r is not None:
                    # 节点处无滑滚动：两齿轮在节点的切向线速度相等（行星架转动时也成立）
                    if b in col:
                        row[6 * col[b]:6 * col[b] + 6] += r
                    if a in col:
                        row[6 * col[a]:6 * col[a] + 6] -= r
                else:
                    num = float(mate.get("gearRatioNumerator") or 1)
                    den = float(mate.get("gearRatioDenominator") or 1)
                    if a in col:
                        row[6 * col[a] + 3:6 * col[a] + 6] += num * ents[0][2]
                    if b in col:
                        row[6 * col[b] + 3:6 * col[b] + 6] -= den * ents[1][2]
                rows.append(row)
        elif mtype in (RACKPINION, LINEARCOUPLER):
            skipped.append({"mate": mate.get("name"), "reason": "耦合未计入活动度"})
    if not rows or not free:
        return None, free, skipped
    return np.vstack(rows), free, skipped


def evaluate(components: list[dict], mates: list[dict], aliases: dict[str, str], rubric: dict,
             joint_rows_out: list | None = None) -> dict:
    """joint_rows_out 非空时按 rubric.joints 顺序追加每个关节的约束行（两组合并时为 None）。"""
    names = [c["name"] for c in components]
    by_name = {c["name"]: c for c in components}
    role_of = {sw: role for role, sw in aliases.items()}
    fixed = {c["name"] for c in components if c.get("isFixed")}
    active = [m for m in mates if not m.get("suppressed")]
    broken = [m for m in active if int(m.get("errorCode") or 0) != 0]
    good = [m for m in active if int(m.get("errorCode") or 0) == 0]

    # 直接配合 → 每对零件的相对关节；fixed 的连成刚体组
    by_pair: dict[frozenset, list[dict]] = {}
    for mate in good:
        pair = _mate_pair(mate)
        if pair:
            by_pair.setdefault(frozenset(pair), []).append(mate)
    dsu = _DSU(names)
    ground_anchor = next(iter(fixed), None)
    for n in fixed:
        dsu.union(n, ground_anchor)
    pair_joints = {}
    for pair, items in by_pair.items():
        rows = [r for m in items for r in mate_rows(m)[0]]
        info = classify_rows(rows)
        pair_joints[pair] = info
        if info["type"] == "fixed":
            a, b = tuple(pair)
            dsu.union(a, b)

    def group_of(role: str) -> str | None:
        sw = aliases.get(role)
        return dsu.find(sw) if sw in by_name else None

    bodies: dict[str, list[str]] = rubric.get("bodies") or {}
    flex: dict[str, list[str]] = rubric.get("flex") or {}

    # 0) 完整度：角色要插入、经非耦合配合连到固定件，且不是被直接固定在空间里的活动件
    reach = set(fixed)
    changed = True
    while changed:
        changed = False
        for mate in good:
            pair = _mate_pair(mate)
            if pair and mate.get("mateType") not in COUPLINGS and (set(pair) & reach) and not set(pair) <= reach:
                reach |= set(pair)
                changed = True
    roles = list(dict.fromkeys([r for members in bodies.values() for r in members] + list(flex)))
    ground_role = rubric.get("ground")
    ground_body = next((g for g, members in bodies.items() if ground_role in members), None)
    may_fix = {r for r in bodies.get(ground_body, [])} | {r for r, allowed in flex.items() if ground_body in allowed}
    missing = [r for r in roles if aliases.get(r) not in by_name]
    wrongly_fixed = [r for r in roles if r not in missing and aliases[r] in fixed and r not in may_fix]
    connected = {r for r in roles if r not in missing and aliases[r] in reach and r not in wrongly_fixed}
    completeness = len(connected) / len(roles) if roles else 1.0

    # 1) 刚体组（只统计已连上的角色：两件都悬空时“没并到别的组”不算对）
    anchors = {g: group_of(members[0]) for g, members in bodies.items() if members}
    group_checks = []
    for g, members in bodies.items():
        for role in members:
            actual = group_of(role)
            others = [h for h, a in anchors.items() if h != g and a is not None and a == actual]
            ok = actual is not None and actual == anchors.get(g) and not others
            group_checks.append({"role": role, "body": g, "ok": ok and role in connected, "ok_v1": ok,
                                 **({"merged_with": others} if others else {})})
    for role, allowed in flex.items():
        actual = group_of(role)
        hits = [g for g in allowed if anchors.get(g) is not None and anchors[g] == actual]
        group_checks.append({"role": role, "body": "|".join(allowed), "ok": len(hits) == 1 and role in connected,
                             "ok_v1": len(hits) == 1})
    rigid_score = sum(c["ok"] for c in group_checks) / len(group_checks) if group_checks else 1.0
    rigid_v1 = sum(c["ok_v1"] for c in group_checks) / len(group_checks) if group_checks else 1.0

    def body_connected(g: str) -> bool:
        return any(r in connected for r in bodies.get(g) or [])

    # 2) 组间关节：两组所有成员之间的直接配合合起来分类
    members_of: dict[str, set[str]] = {}
    for n in names:
        members_of.setdefault(dsu.find(n), set()).add(n)
    joint_checks = []
    anchor_specs: dict[int, list[dict]] = {}
    joint_axes: dict[int, tuple | None] = {}
    for spec in rubric.get("joints") or []:
        ga, gb = spec["bodies"]
        allowed = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        ra, rb = anchors.get(ga), anchors.get(gb)
        if ra is None or rb is None or ra == rb:
            joint_checks.append({"bodies": [ga, gb], "expected": allowed, "actual": "merged" if ra == rb else None,
                                 "ok": False, "ok_v1": False})
            if joint_rows_out is not None:
                joint_rows_out.append(None)
            continue
        rows = []
        for pair, items in by_pair.items():
            a, b = tuple(pair)
            if (a in members_of[ra] and b in members_of[rb]) or (a in members_of[rb] and b in members_of[ra]):
                rows += [r for m in items for r in mate_rows(m)[0]]
        if joint_rows_out is not None:
            joint_rows_out.append(rows)
        info = classify_rows(rows)
        check = {"bodies": [ga, gb], "expected": allowed, "actual": info["type"], "ok_v1": info["type"] in allowed}
        ok = check["ok_v1"] and body_connected(ga) and body_connected(gb)
        if ok and spec.get("lead_mm") is not None:
            want = abs(float(spec["lead_mm"]))
            got = abs(float(info.get("lead_mm") or 0))
            check["lead_mm"] = round(got, 4)
            check["lead_ok"] = abs(got - want) <= max(0.05 * want, 1e-3)
            ok = ok and check["lead_ok"]
        if ok and spec.get("anchors"):
            anchor_specs[len(joint_checks)] = spec["anchors"]
            joint_axes[len(joint_checks)] = joint_axis(rows)
        check["ok"] = ok
        joint_checks.append(check)
    if anchor_specs:
        tol = float(rubric.get("anchor_tol_mm", 0.5))
        for index, result in _anchor_consistency(joint_axes, anchor_specs, tol).items():
            joint_checks[index].update(result)
            joint_checks[index]["ok"] = joint_checks[index]["ok"] and result["anchor_ok"]
    joint_score = sum(c["ok"] for c in joint_checks) / len(joint_checks) if joint_checks else 1.0
    joint_v1 = sum(c["ok_v1"] for c in joint_checks) / len(joint_checks) if joint_checks else 1.0

    # 3) 活动度：缺件或有悬空件时不给分，否则按与目标的差连续计分
    mob = _global_mobility(names, fixed, good)
    target = int(rubric.get("mobility", 1))
    mobility_ok = mob["mobility"] == target
    floating_roles = [r for r in roles if r not in missing and aliases[r] not in reach]
    if missing or floating_roles:
        mobility_score = 0.0
    else:
        mobility_score = max(0.0, 1.0 - abs(mob["mobility"] - target) / max(target, 1))

    # 4) 浮动件：配合图上连不到固定件
    floating = sorted(role_of.get(n, n) for n in names if n not in reach)
    ground_ok = ground_role is not None and aliases.get(ground_role) in fixed
    grounded_v1 = (len(names) - len(floating)) / len(names) if names else 0.0
    grounded_score = completeness
    if not ground_ok:
        grounded_score = grounded_v1 = 0.0

    # 5) 过定义（只在装得足够完整时才有意义，按完整度折算）
    over = {role_of.get(c["name"], c["name"]): OVER_STATUS[int(c.get("constrainedStatus") or 0)]
            for c in components if int(c.get("constrainedStatus") or 0) in OVER_STATUS}
    over_ok = not over and not broken

    # 6) 耦合：齿轮配合 + 齿数比 + 啮合方向（外啮合反向、内啮合同向）+ 中心距
    coupling_checks = []
    tol = float(rubric.get("center_distance_tol_mm", 0.2))
    for spec in rubric.get("couplings") or []:
        a, b = spec["a"], spec["b"]
        za, zb = spec["teeth"]
        check: dict[str, Any] = {"pair": [a, b]}
        ga, gb = group_of(a), group_of(b)
        gear = None
        for mate in good:
            pair = _mate_pair(mate)
            if mate.get("mateType") != GEAR or not pair or _gear_is_sync(mate):
                continue
            g1, g2 = dsu.find(pair[0]), dsu.find(pair[1])
            if {g1, g2} == {ga, gb} and ga != gb:
                gear = mate
                break
        check["mate"] = gear.get("name") if gear else None
        if gear:
            # 约定：ω(第二个实体) = ω(第一个实体)·num/den，实体顺序即选择顺序
            num, den = float(gear["gearRatioNumerator"]), float(gear["gearRatioDenominator"])
            first_is_a = dsu.find(_mate_pair(gear)[0]) == ga
            expected = za / zb if first_is_a else zb / za
            check["ratio"] = [num, den]
            check["ratio_ok"] = bool(abs(num / den - expected) < 1e-3)
            if spec.get("external") is not None:
                check["direction_ok"] = bool(gear.get("reverse")) == bool(spec["external"])
        sa, sb = aliases.get(a), aliases.get(b)
        if sa in by_name and sb in by_name:
            ta, tb = np.array(by_name[sa]["transform"], float), np.array(by_name[sb]["transform"], float)
            ua, ub = _unit(ta[6:9]), _unit(tb[6:9])
            d = (tb[9:12] - ta[9:12]) * 1000
            dist = float(np.linalg.norm(d - np.dot(d, ua) * ua))
            # 内啮合（external: false）中心距是齿数差的一半
            internal = spec.get("external") is False
            expected_cd = float(spec["module_mm"]) * (abs(za - zb) if internal else (za + zb)) / 2
            check["center_distance_mm"] = round(dist, 3)
            check["expected_mm"] = expected_cd
            check["center_ok"] = bool(abs(dist - expected_cd) <= tol and abs(abs(np.dot(ua, ub)) - 1) < 1e-4)
        parts = [bool(check.get("mate")), bool(check.get("ratio_ok")),
                 bool(gear) and check.get("direction_ok", True) is True, bool(check.get("center_ok"))]
        check["score_v1"] = sum(parts) / len(parts)
        # 两个齿轮都没连到机架时，插入点碰巧落在正确中心距上不算数
        if not (a in connected and b in connected):
            parts[3] = False
        check["score"] = sum(parts) / len(parts)
        coupling_checks.append(check)
    coupling_score = (sum(c["score"] for c in coupling_checks) / len(coupling_checks)) if coupling_checks else 1.0
    coupling_v1 = (sum(c["score_v1"] for c in coupling_checks) / len(coupling_checks)) if coupling_checks else 1.0

    parts = {
        "rigid_groups": rigid_score,
        "joints": joint_score,
        "mobility": mobility_score,
        "grounded": grounded_score,
        "not_over_defined": (1.0 if over_ok else 0.0) * completeness,
        "couplings": coupling_score,
    }
    # 规格里没有的子项（无齿轮、无关节、无刚体组）不白送分，按适用项的权重归一化
    applicable = {"rigid_groups": bool(group_checks), "joints": bool(joint_checks), "mobility": True,
                  "grounded": True, "not_over_defined": True, "couplings": bool(coupling_checks)}
    total = sum(WEIGHTS[k] for k, on in applicable.items() if on)
    score = 100.0 * sum(WEIGHTS[k] * v for k, v in parts.items() if applicable[k]) / total
    parts_v1 = {"rigid_groups": rigid_v1, "joints": joint_v1, "mobility": 1.0 if mobility_ok else 0.0,
                "grounded": grounded_v1, "not_over_defined": 1.0 if over_ok else 0.0, "couplings": coupling_v1}
    return {
        "score": round(score, 2),
        "score_v1": round(sum(WEIGHTS[k] * v for k, v in parts_v1.items()), 2),
        "parts": {k: round(v, 3) for k, v in parts.items() if applicable[k]},
        "completeness": round(completeness, 3),
        "missing_roles": missing,
        "wrongly_fixed": wrongly_fixed,
        "rigid_groups": group_checks,
        "joints": joint_checks,
        "mobility": {"actual": mob["mobility"], "target": target, "ok": mobility_ok, "skipped": mob["skipped"]},
        "floating": floating,
        "ground_ok": ground_ok,
        "over_defined": over,
        "broken_mates": [{"name": m.get("name"), "errorCode": m.get("errorCode")} for m in broken],
        "suppressed_mates": [m.get("name") for m in mates if m.get("suppressed")],
        "couplings": coupling_checks,
    }
