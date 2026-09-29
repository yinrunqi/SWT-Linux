"""按 rubric 逐条检查最终几何；每条检查都是确定性的几何谓词。

用法（在 venv-occ 里）：
    python -m geomcheck.rubric --step part.step --rubric eval/rubrics/flange.yaml
    python -m geomcheck.rubric --artifacts eval/artifacts/<run> --rubric-dir eval/rubrics
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import yaml

from .occ_part import (
    Part,
    Surf,
    box_tool,
    cylinder_tool,
    line_distance,
    min_area_rect,
    parallel,
    unit,
)

_PROBE = 0.05  # mm，判定“孔端外侧/内侧”时离开端面的距离


class EntityMissing(Exception):
    pass


@dataclass
class Axis:
    o: np.ndarray
    d: np.ndarray


@dataclass
class Frame:
    origin: np.ndarray
    axes: np.ndarray  # 行向量，右手系
    lo: np.ndarray
    hi: np.ndarray
    extra: dict = field(default_factory=dict)

    def world(self, coords) -> np.ndarray:
        return self.origin + np.asarray(coords, float) @ self.axes

    def local(self, p) -> np.ndarray:
        return self.axes @ (np.asarray(p, float) - self.origin)

    def at_fraction(self, f) -> np.ndarray:
        return self.world(self.lo + np.asarray(f, float) * (self.hi - self.lo))


@dataclass
class Ctx:
    part: Part
    tol_len: float
    tol_ang: float
    tol_vol: float
    specs: dict
    cache: dict = field(default_factory=dict)

    def near(self, a: float, b: float) -> bool:
        return abs(a - b) <= self.tol_len + 1e-9

    def entity(self, name: str):
        if name not in self.cache:
            if name not in self.specs:
                raise KeyError(f"未定义的实体 {name}")
            spec = dict(self.specs[name])
            count = spec.pop("count", None)
            (kind, args), = spec.items()
            self.cache[name] = (SELECTORS[kind](self, args or {}), count)
        return self.cache[name]

    def items(self, name: str) -> list:
        found, _ = self.entity(name)
        if not found:
            raise EntityMissing(f"未找到实体 {name}")
        return found

    def frame(self, name: str) -> Frame:
        found, _ = self.entity(name)
        if found is None:
            raise EntityMissing(f"无法建立坐标系 {name}")
        return found


def _axis(item) -> Axis:
    if isinstance(item, Surf):
        return Axis(item.axis_o, item.axis_d)
    return Axis(item["o"], item["d"])


def _complete(d) -> np.ndarray:
    d = unit(d)
    seed = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = unit(seed - np.dot(seed, d) * d)
    return np.array([d, e1, np.cross(d, e1)])


def _param_extent(points: np.ndarray, ax: Axis) -> tuple[float, float]:
    t = (points - ax.o) @ ax.d
    return float(t.min()), float(t.max())


# ---------------------------------------------------------------- selectors


def sel_cylinder(ctx: Ctx, a: dict) -> list:
    r = a["d"] / 2
    return [
        s for s in ctx.part.surfs
        if s.kind == "cylinder" and ctx.near(s.radius, r) and s.side == a.get("side", s.side)
    ]


def sel_torus(ctx: Ctx, a: dict) -> list:
    return [
        s for s in ctx.part.surfs
        if s.kind == "torus"
        and ctx.near(s.radius, a["minor_r"])
        and ("major_r" not in a or ctx.near(s.major, a["major_r"]))
        and s.side == a.get("side", s.side)
    ]


def sel_ring(ctx: Ctx, a: dict) -> list:
    """环形把手：圆截面（环面）或矩形截面（同轴内外圆柱 + 两端平面）。"""
    outer_r, width, thick = a["outer_d"] / 2, a["section"][0], a["section"][1]
    rings = []
    for s in ctx.part.surfs:
        if s.kind == "torus" and s.side == "outer" and ctx.near(2 * s.radius, width) \
                and ctx.near(s.major + s.radius, outer_r):
            rings.append({"o": s.axis_o, "d": s.axis_d, "center": s.axis_o, "surfs": [s]})
    cyls = [s for s in ctx.part.surfs if s.kind == "cylinder"]
    for out in cyls:
        if out.side != "outer" or not ctx.near(out.radius, outer_r):
            continue
        for inn in cyls:
            if inn.side == "inner" and ctx.near(inn.radius, outer_r - width) \
                    and parallel(out.axis_d, inn.axis_d) \
                    and line_distance(out.axis_o, out.axis_d, inn.axis_o) <= ctx.tol_len:
                ax = Axis(out.axis_o, out.axis_d)
                lo, hi = _param_extent(out.points, ax)
                if ctx.near(hi - lo, thick):
                    center = out.axis_o + (lo + hi) / 2 * out.axis_d
                    rings.append({"o": out.axis_o, "d": out.axis_d, "center": center, "surfs": [out, inn]})
    return rings


def sel_prism_pocket(ctx: Ctx, a: dict) -> list:
    """绕 ``about`` 轴的正多边形凹槽：侧面法向指向轴线、到轴距离 = 对边距/2。"""
    ax = _axis(ctx.items(a["about"])[0])
    half = a["across_flats"] / 2
    walls = []
    for s in ctx.part.surfs:
        if s.kind != "plane" or abs(float(np.dot(s.axis_d, ax.d))) > 1e-3:
            continue
        foot = ax.o + np.dot(s.points.mean(axis=0) - ax.o, ax.d) * ax.d
        dist = float(np.dot(s.axis_d, foot)) - s.offset
        if ctx.near(abs(dist), half) and dist > 0:  # 法向朝向轴线 → 材料在外侧
            walls.append(s)
    if len(walls) != a["sides"]:
        return []
    pts = np.vstack([w.points for w in walls])
    return [{"o": ax.o, "d": ax.d, "surfs": walls, "points": pts}]


def _dominant_dirs(part: Part) -> list[tuple[np.ndarray, float]]:
    weights: list[list] = []
    for s in part.surfs:
        if s.kind in ("plane", "cylinder", "torus", "cone"):
            for entry in weights:
                if parallel(entry[0], s.axis_d, 1e-6):
                    entry[1] += s.area
                    break
            else:
                weights.append([unit(s.axis_d), s.area])
    weights.sort(key=lambda e: -e[1])
    return [(w[0], w[1]) for w in weights]


def principal_axes(part: Part) -> np.ndarray:
    dirs = _dominant_dirs(part)
    if not dirs:
        return np.eye(3)
    d1 = dirs[0][0]
    d2 = next((d for d, _ in dirs[1:] if abs(float(np.dot(d, d1))) < 1e-6), None)
    if d2 is None:
        basis = _complete(d1)
        flat = part.points @ basis[1:].T
        e, _, _ = min_area_rect(flat)
        d2 = unit(e[0] * basis[1] + e[1] * basis[2])
    return np.array([d1, d2, np.cross(d1, d2)])


def sel_principal_frame(ctx: Ctx, a: dict) -> Frame:
    axes = principal_axes(ctx.part)
    lo, hi = ctx.part.extents(axes)
    order = np.argsort(hi - lo)  # 由短到长
    axes = axes[order]
    if np.linalg.det(axes) < 0:
        axes[2] = -axes[2]
    lo, hi = ctx.part.extents(axes)
    frame = Frame(np.zeros(3), axes, lo, hi)
    if a.get("up") == "open_end":
        top, bottom = _extreme_plane_area(ctx, frame, 0, +1), _extreme_plane_area(ctx, frame, 0, -1)
        if top > bottom:
            axes = np.array([-axes[0], axes[1], -axes[2]])
            lo, hi = ctx.part.extents(axes)
            frame = Frame(np.zeros(3), axes, lo, hi)
    return frame


def sel_base_frame(ctx: Ctx, a: dict) -> Frame | None:
    """底座坐标系：L = 底座长边，W = 宽边，U = 向上；原点在底面外廓中心。"""
    part = ctx.part
    planes = [s for s in part.surfs if s.kind == "plane"]
    extreme = [
        s for s in planes
        if abs(float(np.max(part.points @ s.axis_d)) - s.offset) < 1e-3
    ]
    if not extreme:
        return None
    bottom = max(extreme, key=lambda s: s.area)
    up = -bottom.axis_d
    height = part.points @ up + bottom.offset
    basis = _complete(up)
    sel = part.points[(height > -1e-3) & (height < a["thickness"] - 1e-3)]
    flat = sel @ basis[1:].T
    e, n, size = min_area_rect(flat)
    L = unit(e[0] * basis[1] + e[1] * basis[2])
    W = unit(n[0] * basis[1] + n[1] * basis[2])
    if np.dot(np.cross(L, W), up) < 0:
        W = -W
    c2 = np.array([(flat @ e).min() + (flat @ e).max(), (flat @ n).min() + (flat @ n).max()]) / 2
    origin = (c2[0] * e[0] + c2[1] * n[0]) * basis[1] + (c2[0] * e[1] + c2[1] * n[1]) * basis[2]
    origin = origin + (-bottom.offset) * up
    axes = np.array([L, W, up])
    lo, hi = part.extents(axes)
    shift = axes @ origin
    return Frame(origin, axes, lo - shift, hi - shift,
                 {"size": size, "bottom": bottom, "thickness": a["thickness"]})


def sel_slab(ctx: Ctx, a: dict) -> list:
    """一对反向平行、距离不超过 max_thickness 的非水平大平面；取较小面积最大的一对。"""
    frame = ctx.frame(a["frame"])
    up = frame.axes[2]
    planes = [
        s for s in ctx.part.surfs
        if s.kind == "plane" and abs(float(np.dot(s.axis_d, up))) < 0.999 and s.area >= a.get("min_area", 1000)
    ]
    best = None
    for i, p in enumerate(planes):
        for q in planes[i + 1:]:
            if float(np.dot(p.axis_d, q.axis_d)) > -0.9999:
                continue
            gap = abs(p.offset + q.offset)
            if gap > a.get("max_thickness", 20):
                continue
            score = min(p.area, q.area)
            if best is None or score > best[0]:
                best = (score, p, q, gap)
    if best is None:
        return []
    _, p, q, gap = best
    mid = (p.offset - q.offset) / 2
    return [{"n": p.axis_d, "mid": mid, "thickness": gap, "surfs": [p, q],
             "points": np.vstack([p.points, q.points])}]


SELECTORS: dict[str, Callable] = {
    "cylinder": sel_cylinder,
    "torus": sel_torus,
    "ring": sel_ring,
    "prism_pocket": sel_prism_pocket,
    "principal_frame": sel_principal_frame,
    "base_frame": sel_base_frame,
    "slab": sel_slab,
}


# ---------------------------------------------------------------- checks

Result = tuple[bool, dict]


def ck_single_valid_solid(ctx: Ctx, a) -> Result:
    p = ctx.part
    ok = p.n_solids == 1 and p.is_valid()
    return ok, {"solids": p.n_solids, "shells": p.n_shells, "valid": p.is_valid()}


def ck_exists(ctx: Ctx, name) -> Result:
    found, count = ctx.entity(name)
    n = len(found) if isinstance(found, list) else int(found is not None)
    ok = n == count if count is not None else n >= 1
    return ok, {"found": n, "expected": count if count is not None else ">=1"}


def _coaxial(ctx: Ctx, x: Axis, y: Axis) -> bool:
    return parallel(x.d, y.d, 1 - math.cos(math.radians(ctx.tol_ang))) \
        and line_distance(y.o, y.d, x.o) <= ctx.tol_len


def ck_coaxial(ctx: Ctx, names) -> Result:
    ref = _axis(ctx.items(names[1])[0])
    oks = [_coaxial(ctx, _axis(it), ref) for it in ctx.items(names[0])]
    return all(oks), {"coaxial": oks}


def ck_parallel(ctx: Ctx, names) -> Result:
    ref = _axis(ctx.items(names[1])[0])
    eps = 1 - math.cos(math.radians(ctx.tol_ang))
    oks = [parallel(_axis(it).d, ref.d, eps) for it in ctx.items(names[0])]
    return all(oks), {"parallel": oks}


def ck_extent_along(ctx: Ctx, a) -> Result:
    ax = _axis(ctx.items(a["axis"])[0])
    if a.get("of", "all") == "all":
        lo, hi = ctx.part.extents(_complete(ax.d))
        vals = [float(hi[0] - lo[0])]
    else:
        own = a["of"] == a["axis"]  # 每个实体沿自身轴线量
        vals = []
        for it in ctx.items(a["of"]):
            pts = it.points if isinstance(it, Surf) else it["points"]
            lo, hi = _param_extent(pts, _axis(it) if own else ax)
            vals.append(hi - lo)
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [round(v, 3) for v in vals]}


def ck_through(ctx: Ctx, a) -> Result:
    oks, detail = [], []
    for it in ctx.items(a["holes"]):
        ax = _axis(it)
        lo, hi = _param_extent(it.points, ax)
        inside = ctx.part.line_intervals(ax.o, ax.d, lo + _PROBE, hi - _PROBE)
        ends_open = not ctx.part.is_inside(ax.o + (lo - _PROBE) * ax.d) \
            and not ctx.part.is_inside(ax.o + (hi + _PROBE) * ax.d)
        oks.append(not inside and ends_open)
        detail.append({"length": round(hi - lo, 3), "blocked": bool(inside), "ends_open": ends_open})
    return all(oks), {"holes": detail}


def ck_axis_distance(ctx: Ctx, a) -> Result:
    ref = _axis(ctx.items(a["from"])[0])
    vals = [line_distance(ref.o, ref.d, _axis(it).o) for it in ctx.items(a["to"])]
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [round(v, 3) for v in vals]}


def ck_equal_angular(ctx: Ctx, a) -> Result:
    ref = _axis(ctx.items(a["about"])[0])
    basis = _complete(ref.d)
    items = ctx.items(a["items"])
    angles = sorted(
        math.degrees(math.atan2(np.dot(_axis(it).o - ref.o, basis[2]), np.dot(_axis(it).o - ref.o, basis[1]))) % 360
        for it in items
    )
    gaps = np.diff(angles + [angles[0] + 360])
    target = 360 / len(items)
    return bool(np.all(np.abs(gaps - target) <= ctx.tol_ang)), {"gaps_deg": [round(g, 3) for g in gaps]}


def ck_volume(ctx: Ctx, value) -> Result:
    v = ctx.part.volume()
    return abs(v - value) <= ctx.tol_vol * value, {"measured": round(v, 2), "expected": value}


def ck_volume_range(ctx: Ctx, rng) -> Result:
    v = ctx.part.volume()
    lo, hi = rng[0] * (1 - ctx.tol_vol), rng[1] * (1 + ctx.tol_vol)
    return lo <= v <= hi, {"measured": round(v, 2), "range": [round(lo, 2), round(hi, 2)]}


def ck_bbox_sorted(ctx: Ctx, expected) -> Result:
    axes = principal_axes(ctx.part)
    lo, hi = ctx.part.extents(axes)
    got = sorted(float(x) for x in hi - lo)
    exp = sorted(expected)
    return all(ctx.near(g, e) for g, e in zip(got, exp)), {"measured": [round(g, 3) for g in got]}


def _torus_geom(t: Surf):
    e1, e2 = t.ref_axes()
    start, _, span = t.angular_coverage()

    def radial(theta_deg):
        th = math.radians(theta_deg)
        return math.cos(th) * e1 + math.sin(th) * e2

    return start, span, radial


def ck_sweep_angle(ctx: Ctx, a) -> Result:
    t = ctx.items(a["torus"])[0]
    span = t.angular_coverage()[2]
    return abs(span - a["value"]) <= ctx.tol_ang, {"measured": round(span, 3)}


def ck_material_at(ctx: Ctx, a) -> Result:
    t = ctx.items(a["centerline_of"])[0]
    start, span, radial = _torus_geom(t)
    oks = [ctx.part.is_inside(t.axis_o + t.major * radial(start + f * span)) for f in a["fractions"]]
    return all(oks), {"inside": oks}


def ck_flanges_at_arc_ends(ctx: Ctx, a) -> Result:
    t = ctx.items(a["torus"])[0]
    start, span, radial = _torus_geom(t)
    items = list(ctx.items(a["items"]))
    ends = []
    for theta, sign in ((start, -1.0), (start + span, 1.0)):
        p = t.axis_o + t.major * radial(theta)
        out = sign * unit(np.cross(t.axis_d, radial(theta)))
        match = None
        for it in items:
            ax = _axis(it)
            if parallel(ax.d, out, 1e-3) and line_distance(ax.o, ax.d, p) <= ctx.tol_len:
                s = (it.points - p) @ out
                match = (it, float(s.min()), float(s.max()))
                break
        if match is None:
            ends.append({"matched": False})
            continue
        items.remove(match[0])
        lo, hi = a["outward"]
        ends.append({"matched": True, "from": round(match[1], 3), "to": round(match[2], 3),
                     "ok": ctx.near(match[1], lo) and ctx.near(match[2], hi)})
    return all(e.get("ok") for e in ends), {"ends": ends}


def ck_genus(ctx: Ctx, value) -> Result:
    if ctx.part.n_solids != 1:
        return False, {"error": "不是单一实体", "solids": ctx.part.n_solids}
    g = ctx.part.genus()
    return g == value, {"measured": g}


def ck_plane_contains_axis(ctx: Ctx, a) -> Result:
    ring = ctx.items(a["ring"])[0]
    body = _axis(ctx.items(a["axis"])[0])
    perp = abs(float(np.dot(ring["d"], body.d)))
    off = abs(float(np.dot(ring["center"] - body.o, ring["d"])))
    ok = perp <= math.sin(math.radians(ctx.tol_ang)) and off <= ctx.tol_len
    return ok, {"axis_dot": round(perp, 4), "plane_offset": round(off, 3)}


def ck_no_material_in(ctx: Ctx, a) -> Result:
    cav = ctx.items(a["cylinder_of"])[0]
    ax = _axis(cav)
    lo, hi = _param_extent(cav.points, ax)
    shrink = a.get("shrink", 0.1)
    tool = cylinder_tool(ax.o + (lo + shrink) * ax.d, ax.d, cav.radius - shrink, hi - lo - 2 * shrink)
    v = ctx.part.common_volume(tool)
    return v <= a.get("max_volume", 1.0), {"material_mm3": round(v, 3)}


def ck_pocket_depth(ctx: Ctx, a) -> Result:
    it = ctx.items(a["pocket"])[0]
    ax = _axis(it)
    pts = it.points if isinstance(it, Surf) else it["points"]
    lo, hi = _param_extent(pts, ax)
    inside_lo = ctx.part.is_inside(ax.o + (lo - _PROBE) * ax.d)
    inside_hi = ctx.part.is_inside(ax.o + (hi + _PROBE) * ax.d)
    blind = inside_lo != inside_hi
    ok = ctx.near(hi - lo, a["value"]) and (blind if a.get("blind", True) else not (inside_lo or inside_hi))
    detail = {"depth": round(hi - lo, 3), "blind": blind}
    if a.get("open_at_extreme") and blind:
        plo, phi = ctx.part.extents(_complete(ax.d))
        shift = float(np.dot(ax.o, ax.d))
        open_t = lo if not inside_lo else hi
        at = ctx.near(open_t + shift, plo[0]) or ctx.near(open_t + shift, phi[0])
        ok = ok and at
        detail["open_at_extreme"] = at
    return ok, detail


def ck_stepped_length(ctx: Ctx, a) -> Result:
    """轴对称台阶（杆 + 头/法兰）：在两半径之间取探测线测头长，再由整体轴向范围得杆长。"""
    ax = _axis(ctx.items(a["axis"])[0])
    basis = _complete(ax.d)
    r = (a["inner_d"] + a["outer_d"]) / 4
    plo, phi = ctx.part.extents(basis)
    shift = float(np.dot(ax.o, ax.d))
    lo, hi = plo[0] - shift - 1, phi[0] - shift + 1
    heads, shafts = [], []
    for k in range(4):
        th = k * math.pi / 2
        off = r * (math.cos(th) * basis[1] + math.sin(th) * basis[2])
        segs = ctx.part.line_intervals(ax.o + off, ax.d, lo, hi)
        if not segs:
            return False, {"error": "探测线未穿过头部"}
        c0, c1 = max(segs, key=lambda s: s[1] - s[0])
        heads.append(c1 - c0)
        shafts.append(c0 - (lo + 1) if abs(c1 - (hi - 1)) < abs(c0 - (lo + 1)) else (hi - 1) - c1)
    ok = all(ctx.near(h, a["outer_len"]) for h in heads) and all(ctx.near(s, a["inner_len"]) for s in shafts)
    return ok, {"outer_len": [round(h, 3) for h in heads], "inner_len": [round(s, 3) for s in shafts]}


def _extreme_plane_area(ctx: Ctx, frame: Frame, axis: int, sign: int) -> float:
    d = sign * frame.axes[axis]
    edge = frame.hi[axis] if sign > 0 else -frame.lo[axis]
    edge += float(np.dot(frame.origin, d))
    return sum(
        s.area for s in ctx.part.surfs
        if s.kind == "plane" and float(np.dot(s.axis_d, d)) > 1 - 1e-6 and abs(s.offset - edge) < 1e-3
    )


def ck_extreme_plane_areas(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    areas = sorted([_extreme_plane_area(ctx, frame, a["axis"], s) for s in (+1, -1)])
    exp = sorted(a["values"])
    ok = all(abs(g - e) <= ctx.tol_vol * e for g, e in zip(areas, exp))
    return ok, {"measured": [round(x, 2) for x in areas]}


def _dir_token(frame: Frame, token: str) -> np.ndarray:
    sign = -1.0 if token.startswith("-") else 1.0
    return sign * frame.axes[int(token.lstrip("+-"))]


def ck_ray_thickness(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    p = frame.at_fraction(a["from"])
    vals = []
    for token in a["dirs"]:
        segs = ctx.part.line_intervals(p, _dir_token(frame, token), 0, 1e4)
        vals.append(segs[0][1] - segs[0][0] if segs else 0.0)
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [round(v, 3) for v in vals]}


def ck_first_hit(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    p = frame.at_fraction(a["from"])
    segs = ctx.part.line_intervals(p, _dir_token(frame, a["dir"]), 0, 1e4)
    v = segs[0][0] if segs else float("inf")
    return ctx.near(v, a["value"]), {"measured": round(v, 3)}


# ---- 底座类零件（手机支架）


def ck_footprint(ctx: Ctx, a) -> Result:
    size = ctx.frame(a["frame"]).extra["size"]
    exp = sorted(a["value"], reverse=True)
    return all(ctx.near(g, e) for g, e in zip(size, exp)), {"measured": [round(float(x), 3) for x in size]}


def ck_bottom_planar(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    size = frame.extra["size"]
    fill = frame.extra["bottom"].area / float(size[0] * size[1])
    return fill >= a.get("min_fill", 0.7), {"bottom_fill": round(fill, 3)}


def ck_total_height(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    h = float(frame.hi[2])
    return ctx.near(h, a["value"]), {"measured": round(h, 3)}


def ck_slab_thickness(ctx: Ctx, a) -> Result:
    t = ctx.items(a["slab"])[0]["thickness"]
    return ctx.near(t, a["value"]), {"measured": round(t, 3)}


def ck_slab_width(ctx: Ctx, a) -> Result:
    slab, frame = ctx.items(a["slab"])[0], ctx.frame(a["frame"])
    w = slab["points"] @ frame.axes[1]
    v = float(w.max() - w.min())
    return ctx.near(v, a["value"]), {"measured": round(v, 3)}


def _slab_angle(slab, frame: Frame) -> float:
    return math.degrees(math.acos(min(1.0, abs(float(np.dot(slab["n"], frame.axes[2]))))))


def ck_angle_to_bottom(ctx: Ctx, a) -> Result:
    ang = _slab_angle(ctx.items(a["slab"])[0], ctx.frame(a["frame"]))
    return abs(ang - a["value"]) <= ctx.tol_ang, {"measured": round(ang, 3)}


def _end_block_fill(ctx: Ctx, frame: Frame, sign: int, a) -> tuple[float, float]:
    half_l, half_w = frame.extra["size"][0] / 2, frame.extra["size"][1] / 2
    base_top, top, depth = frame.extra["thickness"], a["top"], a["depth"]
    l0, l1 = (half_l - depth, half_l) if sign > 0 else (-half_l, -half_l + depth)
    box = box_tool(frame.origin, frame.axes, [l0, -half_w, base_top], [l1, half_w, top])
    fill = ctx.part.common_volume(box) / (depth * 2 * half_w * (top - base_top))
    above = box_tool(frame.origin, frame.axes, [l0, -half_w, top + _PROBE], [l1, half_w, top + 5])
    return fill, ctx.part.common_volume(above)


def ck_end_block(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    res = {s: _end_block_fill(ctx, frame, s, a) for s in (+1, -1)}
    sign = max(res, key=lambda s: res[s][0])
    fill, above = res[sign]
    frame.extra["lip_sign"] = sign
    ok = fill >= a.get("min_fill", 0.9) and above <= 1.0
    return ok, {"fill": round(fill, 3), "material_above_mm3": round(above, 3)}


def ck_side_ribs(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    half_w, band = frame.extra["size"][1] / 2, a.get("band", 8)
    h0 = a.get("from_height", frame.extra["thickness"] + 2)
    h1 = float(frame.hi[2])
    l0, l1 = float(frame.lo[0]) - 1, float(frame.hi[0]) + 1

    def strip(w0, w1):
        return ctx.part.common_volume(box_tool(frame.origin, frame.axes, [l0, w0, h0], [l1, w1, h1]))

    # 对照带取在端部带内侧两个带宽处：避开居中的开槽，也不与端部的筋重叠
    ends = [
        strip(half_w - band, half_w) - strip(half_w - 3 * band, half_w - 2 * band),
        strip(-half_w, -half_w + band) - strip(-half_w + 2 * band, -half_w + 3 * band),
    ]
    ok = all(e >= a.get("min_excess", 200) for e in ends)
    return ok, {"excess_mm3": [round(e, 1) for e in ends]}


class _SlabProbe:
    """背板中面上的探测：(w, h) 为底座坐标系中的宽度坐标与高度。"""

    def __init__(self, ctx: Ctx, slab: dict, frame: Frame):
        self.ctx, self.slab, self.f = ctx, slab, frame
        self.n = slab["n"]
        w = slab["points"] @ frame.axes[1]
        self.w0 = float((w.max() + w.min()) / 2 - np.dot(frame.origin, frame.axes[1]))
        h = slab["points"] @ frame.axes[2] - np.dot(frame.origin, frame.axes[2])
        self.top = float(h.max())
        self.sin = math.sin(math.radians(_slab_angle(slab, frame)))

    def point(self, w: float, h: float) -> np.ndarray:
        base = self.f.world([0.0, w, h])
        L = self.f.axes[0]
        s = (self.slab["mid"] - float(np.dot(self.n, base))) / float(np.dot(self.n, L))
        return base + s * L

    def empty(self, w: float, h: float) -> bool:
        """中面上该点无材料；开口尺寸在中面上量。"""
        return not self.ctx.part.is_inside(self.point(w, h))

    def through(self, w: float, h: float) -> bool:
        """沿板法向整段穿过板厚都没有材料。"""
        half = self.slab["thickness"] / 2 + 0.3
        p = self.point(w, h) - half * self.n
        return not self.ctx.part.line_intervals(p, self.n, 0, 2 * half)

    def edge(self, fn: Callable[[float], bool], a: float, b: float) -> float:
        """fn(a) 为空、fn(b) 为实时二分求边界。"""
        for _ in range(30):
            m = (a + b) / 2
            a, b = (m, b) if fn(m) else (a, m)
        return (a + b) / 2

    def scan(self, fn: Callable[[float], bool], start: float, stop: float, step: float) -> float | None:
        x = start
        while (x <= stop) if step > 0 else (x >= stop):
            if not fn(x):
                return self.edge(fn, x - step, x)
            x += step
        return None

    def width_at(self, h: float, w: float) -> tuple[float, float] | None:
        if not self.empty(w, h):
            return None
        right = self.scan(lambda x: self.empty(x, h), w, w + 200, 0.5)
        left = self.scan(lambda x: self.empty(x, h), w, w - 200, -0.5)
        if right is None or left is None:
            return None
        return left, right


def _size_ok(ctx: Ctx, vertical: float, sin: float, value: float) -> bool:
    return ctx.near(vertical, value) or ctx.near(vertical / sin, value)


def ck_slab_notch(ctx: Ctx, a) -> Result:
    """背板底部向下开口的通槽（高度按竖直或沿板面量取其一）。"""
    frame = ctx.frame(a["frame"])
    pr = _SlabProbe(ctx, ctx.items(a["slab"])[0], frame)
    base_top = frame.extra["thickness"]
    if not pr.empty(pr.w0, base_top + 0.2):
        return False, {"error": "背板根部中央没有开口"}
    top = pr.scan(lambda h: pr.empty(pr.w0, h), base_top + 0.2, pr.top, 0.5)
    if top is None:
        return False, {"error": "开口一直通到板顶"}
    height = top - base_top
    span = pr.width_at(base_top + height / 2, pr.w0)
    width = span[1] - span[0] if span else 0.0
    centered = bool(span) and ctx.near((span[0] + span[1]) / 2, pr.w0)
    through = pr.through(pr.w0, base_top + height / 2)
    ok = _size_ok(ctx, height, pr.sin, a["height"]) and ctx.near(width, a["width"]) and centered and through
    return ok, {"height_vertical": round(height, 3), "height_in_plane": round(height / pr.sin, 3),
                "width": round(width, 3), "centered": centered, "through": through}


def ck_slab_slots(ctx: Ctx, a) -> Result:
    """背板上方的一组水平通槽：数量、尺寸与竖向间距。"""
    frame = ctx.frame(a["frame"])
    pr = _SlabProbe(ctx, ctx.items(a["slab"])[0], frame)
    base_top = frame.extra["thickness"]
    h = base_top + 0.2
    if pr.empty(pr.w0, h):  # 跳过根部的开口
        h = (pr.scan(lambda x: pr.empty(pr.w0, x), h, pr.top, 0.5) or pr.top) + 0.3
    slots = []
    step = 0.25
    while h < pr.top - 0.5:
        if pr.empty(pr.w0, h):
            lo = pr.edge(lambda x: not pr.empty(pr.w0, x), h - step, h)
            hi = pr.scan(lambda x: pr.empty(pr.w0, x), h, pr.top, step) or pr.top
            span = pr.width_at((lo + hi) / 2, pr.w0)
            slots.append({"h0": lo, "h1": hi, "width": span[1] - span[0] if span else 0.0,
                          "through": pr.through(pr.w0, (lo + hi) / 2)})
            h = hi + step
        else:
            h += step
    ok = len(slots) == a["count"]
    sizes = [s["h1"] - s["h0"] for s in slots]
    centers = [(s["h0"] + s["h1"]) / 2 for s in slots]
    pitches = list(np.diff(centers))
    ok = ok and all(_size_ok(ctx, v, pr.sin, a["short"]) for v in sizes)
    ok = ok and all(ctx.near(s["width"], a["long"]) and s["through"] for s in slots)
    ok = ok and all(_size_ok(ctx, p, pr.sin, a["pitch"]) for p in pitches)
    return ok, {"count": len(slots), "heights_vertical": [round(v, 3) for v in sizes],
                "widths": [round(s["width"], 3) for s in slots], "pitch_vertical": [round(float(p), 3) for p in pitches]}


def ck_bottom_pockets(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    up = frame.axes[2]
    found = []
    for s in ctx.part.surfs:
        if s.kind != "plane" or float(np.dot(s.axis_d, -up)) < 1 - 1e-6:
            continue
        h = float(np.mean(s.points @ up) - np.dot(frame.origin, up))
        if not ctx.near(h, a["depth"]):
            continue
        _, _, size = min_area_rect(s.points @ frame.axes[:2].T)
        found.append([round(float(x), 3) for x in size])
    exp = sorted(a["size"], reverse=True)
    ok = len(found) == a["count"] and all(ctx.near(f[0], exp[0]) and ctx.near(f[1], exp[1]) for f in found)
    return ok, {"pockets": found}


def ck_blend_faces(ctx: Ctx, a) -> Result:
    r = a["radius"]
    n = sum(
        1 for s in ctx.part.surfs
        if (s.kind == "cylinder" and ctx.near(s.radius, r) and s.angular_coverage()[2] <= 100)
        or (s.kind == "torus" and ctx.near(s.radius, r))
    )
    return n >= a.get("min_count", 1), {"found": n}


def ck_mirror_symmetric(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    xor = ctx.part.mirror_xor_volume(frame.origin, frame.axes[a["normal"]])
    rel = xor / ctx.part.volume()
    return rel <= a.get("max_xor_rel", 1e-3), {"xor_rel": round(rel, 5)}


def ck_tilts_away_from_lip(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    if "lip_sign" not in frame.extra:
        ck_end_block(ctx, a["lip"] | {"frame": a["frame"]})
    slab = ctx.items(a["slab"])[0]
    up = frame.axes[2]
    s = unit(up - np.dot(up, slab["n"]) * slab["n"])
    lean = float(np.dot(s, frame.axes[0])) * frame.extra["lip_sign"]
    return lean < -1e-3, {"lean_toward_lip": round(lean, 4)}


CHECKS: dict[str, Callable[[Ctx, Any], Result]] = {
    name[3:]: fn for name, fn in globals().items() if name.startswith("ck_")
}


# ---------------------------------------------------------------- runner


def evaluate(rubric: dict, part: Part) -> dict:
    tol = rubric.get("tolerance", {})
    ctx = Ctx(part, tol.get("length_mm", 0.1), tol.get("angle_deg", 0.5), tol.get("volume_rel", 0.01),
              rubric.get("entities", {}))
    rows = []
    for item in rubric["rubrics"]:
        checks, ok_all = [], True
        for chk in item["check"]:
            (name, args), = chk.items()
            try:
                ok, detail = CHECKS[name](ctx, args)
            except EntityMissing as exc:
                ok, detail = False, {"error": str(exc)}
            checks.append({"check": name, "ok": bool(ok), **detail})
            ok_all = ok_all and bool(ok)
        rows.append({
            "id": item["id"], "ok": ok_all, "hard": bool(item.get("hard")),
            "basis": item.get("basis", "explicit"), "weight": item.get("weight", 1.0), "checks": checks,
        })
    explicit = [r for r in rows if r["basis"] == "explicit"]
    implied = [r for r in rows if r["basis"] != "explicit"]
    total = sum(r["weight"] for r in explicit)
    score = 100.0 * sum(r["weight"] for r in explicit if r["ok"]) / total if total else 0.0
    return {
        "case": rubric["case"],
        "score": round(score, 2),
        "passed": sum(r["ok"] for r in explicit),
        "total": len(explicit),
        "implied_passed": sum(r["ok"] for r in implied),
        "implied_total": len(implied),
        "hard_ok": all(r["ok"] for r in rows if r["hard"]),
        "accepted": all(r["ok"] for r in explicit),
        "rubrics": rows,
    }


def load_rubric(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _print(res: dict, label: str) -> None:
    print(f"{label}: score={res['score']} explicit={res['passed']}/{res['total']} "
          f"implied={res['implied_passed']}/{res['implied_total']} hard_ok={res['hard_ok']} accepted={res['accepted']}")
    for r in res["rubrics"]:
        if not r["ok"]:
            bad = [c for c in r["checks"] if not c["ok"]]
            print(f"    FAIL {r['id']}{' [implied]' if r['basis'] != 'explicit' else ''}: "
                  + "; ".join(json.dumps(c, ensure_ascii=False) for c in bad))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step")
    ap.add_argument("--rubric")
    ap.add_argument("--artifacts", help="目录，内含 <case>_r<k>.step")
    ap.add_argument("--rubric-dir", default="eval/rubrics")
    ap.add_argument("--output")
    args = ap.parse_args()
    results = []
    if args.step:
        rubric = load_rubric(args.rubric)
        res = evaluate(rubric, Part.from_step(args.step))
        _print(res, Path(args.step).name)
        results.append({"step": args.step, **res})
    else:
        for step in sorted(Path(args.artifacts).glob("*.step")):
            case = re.sub(r"_r\d+$", "", step.stem)
            rubric_path = Path(args.rubric_dir) / f"{case}.yaml"
            if not rubric_path.exists():
                continue
            res = evaluate(load_rubric(rubric_path), Part.from_step(step))
            _print(res, step.name)
            results.append({"step": str(step), **res})
    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
