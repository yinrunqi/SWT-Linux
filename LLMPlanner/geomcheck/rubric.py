"""按 rubric 逐条检查最终几何；每条检查都是确定性的几何谓词。

用法（在 venv-occ 里）：
    python -m geomcheck.rubric --step part.step --rubric eval/rubrics/flange.yaml
    python -m geomcheck.rubric --artifacts eval/artifacts/<run> --rubric-dir eval/rubrics

坐标系约定（rubric 中的坐标都以 mm 为单位）：
- principal_frame / world_frame：原点在包围盒最小角，各轴坐标 ∈ [0, 尺寸]。
  principal_frame 的轴按外形尺寸由短到长排列（0 最短）。
- axis_frame：轴 0 沿选定实体的轴线，原点在该轴线上零件的低端；轴 1 指向 ``ref`` 实体（若给出）。
- base_frame：0 = 底座长边，1 = 宽边，2 = 向上；原点在底面外廓中心。
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
from OCP.Standard import Standard_Failure

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
    axes: np.ndarray  # 行向量，正交（可为左手系）
    lo: np.ndarray  # 零件在本坐标系下的范围（相对原点）
    hi: np.ndarray
    extra: dict = field(default_factory=dict)

    def world(self, coords) -> np.ndarray:
        return self.origin + np.asarray(coords, float) @ self.axes

    def local(self, p) -> np.ndarray:
        return self.axes @ (np.asarray(p, float) - self.origin)

    def at_fraction(self, f) -> np.ndarray:
        return self.world(self.lo + np.asarray(f, float) * (self.hi - self.lo))

    @property
    def size(self) -> np.ndarray:
        return self.hi - self.lo


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


def _pts(item) -> np.ndarray:
    return item.points if isinstance(item, Surf) else item["points"]


def _complete(d) -> np.ndarray:
    d = unit(d)
    seed = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = unit(seed - np.dot(seed, d) * d)
    return np.array([d, e1, np.cross(d, e1)])


def _param_extent(points: np.ndarray, ax: Axis) -> tuple[float, float]:
    t = (points - ax.o) @ ax.d
    return float(t.min()), float(t.max())


def _eps_ang(ctx: Ctx) -> float:
    return 1 - math.cos(math.radians(ctx.tol_ang))


# ---------------------------------------------------------------- frames


def _dominant_dirs(part: Part) -> list[list]:
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
    return weights


def principal_axes(part: Part) -> np.ndarray:
    """面积加权下总权重最大的一组正交主方向（平面法向与回转轴都计入）。"""
    dirs = _dominant_dirs(part)
    if not dirs:
        return np.eye(3)

    def weight(d):
        return sum(w for e, w in dirs if parallel(e, d, 1e-6))

    best = None
    for i, (d1, w1) in enumerate(dirs):
        for d2, w2 in dirs[i + 1:]:
            if abs(float(np.dot(d1, d2))) < 1e-6:
                score = w1 + w2 + weight(np.cross(d1, d2))
                if best is None or score > best[0] + 1e-6:
                    best = (score, d1, d2)
    if best is not None:
        _, d1, d2 = best
    else:
        d1 = dirs[0][0]
        basis = _complete(d1)
        e, _, _ = min_area_rect(part.points @ basis[1:].T)
        d2 = unit(e[0] * basis[1] + e[1] * basis[2])
    return np.array([d1, d2, np.cross(d1, d2)])


def _box_frame(part: Part, axes: np.ndarray, extra: dict | None = None) -> Frame:
    lo, hi = part.extents(axes)
    origin = lo @ axes
    return Frame(origin, axes, np.zeros(3), hi - lo, extra or {})


def _extreme_plane_area(ctx: Ctx, frame: Frame, axis: int, sign: int) -> float:
    d = sign * frame.axes[axis]
    base = float(np.dot(frame.origin, frame.axes[axis]))
    edge = (base + frame.hi[axis]) if sign > 0 else -(base + frame.lo[axis])
    return sum(
        s.area for s in ctx.part.surfs
        if s.kind == "plane" and float(np.dot(s.axis_d, d)) > 1 - 1e-6 and abs(s.offset - edge) < 1e-3
    )


def _flip_larger_area_low(ctx: Ctx, frame: Frame, axis: int, rebuild) -> Frame:
    low, high = _extreme_plane_area(ctx, frame, axis, -1), _extreme_plane_area(ctx, frame, axis, +1)
    if high > low * (1 + 1e-3) + 1e-6:
        axes = frame.axes.copy()
        axes[axis] = -axes[axis]
        return rebuild(axes)
    return frame


def _flip_wider_end_low(ctx: Ctx, frame: Frame, axis: int, rebuild) -> Frame:
    """两端各取轴向 2% 厚的一层表面采样点，面内包围盒面积较大的一端为低端（如底大顶小的键帽，
    与底面是否开槽、顶面是否倒圆无关）。"""
    c = (ctx.part.points - frame.origin) @ frame.axes.T
    band = 0.02 * float(frame.hi[axis] - frame.lo[axis])
    others = [k for k in range(3) if k != axis]

    def area(mask):
        sub = c[mask][:, others]
        return float(np.prod(sub.max(axis=0) - sub.min(axis=0))) if len(sub) else 0.0

    low = area(c[:, axis] <= frame.lo[axis] + band)
    high = area(c[:, axis] >= frame.hi[axis] - band)
    if high > low * (1 + 1e-3) + 1e-6:
        axes = frame.axes.copy()
        axes[axis] = -axes[axis]
        return rebuild(axes)
    return frame


_SIGN_RULES = {"larger_area_low": _flip_larger_area_low, "wider_end_low": _flip_wider_end_low}


def sel_principal_frame(ctx: Ctx, a: dict) -> Frame:
    part = ctx.part
    axes = principal_axes(part)
    lo, hi = part.extents(axes)
    axes = axes[np.argsort(hi - lo, kind="stable")]
    frame = _box_frame(part, axes)
    signs = dict(a.get("signs") or {})
    if a.get("up") == "open_end":
        signs[0] = "larger_area_low"
    for axis, rule in sorted(signs.items()):
        if rule not in _SIGN_RULES:
            raise ValueError(f"未知的方向规则 {rule}")
        frame = _SIGN_RULES[rule](ctx, frame, int(axis), lambda ax: _box_frame(part, ax))
    return frame


def sel_world_frame(ctx: Ctx, a: dict) -> Frame:
    """世界轴坐标系，原点在包围盒最小角。

    ``anchor_planes: [k, ...]``：这些轴上的原点改取主体的低端平面——法向为 −e_k、面积不小于
    该方向最大平面 1/4 的平面中坐标最小者，使伸出主体的小特征不带偏坐标。
    """
    frame = _box_frame(ctx.part, np.eye(3))
    anchors = a.get("anchor_planes") or []
    if not anchors:
        return frame
    origin = frame.origin.copy()
    for k in anchors:
        e = np.eye(3)[k]
        faces = [s for s in ctx.part.surfs if s.kind == "plane" and abs(float(np.dot(s.axis_d, e))) > 1 - 1e-6]
        low = [s for s in faces if float(np.dot(s.axis_d, e)) < 0]
        if not low:
            continue
        big = max(s.area for s in faces)
        origin[k] = min(-s.offset for s in low if s.area >= 0.25 * big)
    shift = origin - frame.origin
    return Frame(origin, frame.axes, frame.lo - shift, frame.hi - shift, frame.extra)


def sel_axis_frame(ctx: Ctx, a: dict) -> Frame:
    """沿实体轴线的坐标系：原点在轴线上零件的低端。"""
    part = ctx.part
    ax = _axis(ctx.items(a["axis"])[0])

    def build(d, e1):
        d = unit(d)
        e1 = unit(e1 - np.dot(e1, d) * d)
        axes = np.array([d, e1, np.cross(d, e1)])
        lo, hi = part.extents(axes)
        foot = ax.o - np.dot(ax.o, d) * d
        origin = foot + lo[0] * d
        shift = axes @ origin
        return Frame(origin, axes, lo - shift, hi - shift, {"axis": ax})

    if "ref" in a:
        ref = _axis(ctx.items(a["ref"])[0])
        e1 = ref.o - ax.o
    elif "ref_dir" in a:
        ref_item = ctx.items(a["ref_dir"])[0]
        e1 = _axis(ref_item).d
        # ref_dir_toward：轴 1 指向该实体所在一侧（如穿过主轴线的径向孔，轴线本身不带正负）
        if a.get("ref_dir_toward") and float(np.dot(_pts(ref_item).mean(axis=0) - ax.o, e1)) < 0:
            e1 = -e1
    else:
        e1 = _complete(ax.d)[1]
    frame = build(ax.d, e1)
    if a.get("low") == "larger_area":
        frame = _flip_larger_area_low(ctx, frame, 0, lambda axes: build(axes[0], axes[1]))
    return frame


def sel_base_frame(ctx: Ctx, a: dict) -> Frame | None:
    """底座坐标系：0 = 底座长边，1 = 宽边，2 = 向上；原点在底面外廓中心。"""
    part = ctx.part
    planes = [s for s in part.surfs if s.kind == "plane"]
    extreme = [s for s in planes if abs(float(np.max(part.points @ s.axis_d)) - s.offset) < 1e-3]
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

    def build(L, W):
        axes = np.array([L, W, up])
        lo, hi = part.extents(axes)
        shift = axes @ origin
        return Frame(origin, axes, lo - shift, hi - shift,
                     {"size": size, "bottom": bottom, "thickness": a["thickness"], "sym_origin": origin})

    frame = build(L, W)
    if a.get("tall_end"):
        # 让高出底座的主体（立板等）位于 +L 一侧
        half_l, half_w = size[0] / 2, size[1] / 2
        top = float(frame.hi[2]) + 1

        def vol(l0, l1):
            return part.common_volume(box_tool(origin, frame.axes, [l0, -half_w - 1, a["thickness"] + 1], [l1, half_w + 1, top]))

        if vol(-half_l - 1, 0) > vol(0, half_l + 1):
            frame = build(-L, -W)
    return frame


# ---------------------------------------------------------------- selectors


def _side_ok(s: Surf, a: dict) -> bool:
    return s.side == a.get("side", s.side)


def _along_ok(ctx: Ctx, s: Surf, a: dict) -> bool:
    """``along: {frame, axis}``：只取轴线平行于该坐标轴的曲面。"""
    if "along" not in a:
        return True
    frame = ctx.frame(a["along"]["frame"])
    return parallel(s.axis_d, frame.axes[a["along"]["axis"]], _eps_ang(ctx))


def sel_cylinder(ctx: Ctx, a: dict) -> list:
    r = a["d"] / 2
    return [s for s in ctx.part.surfs
            if s.kind == "cylinder" and ctx.near(s.radius, r) and _side_ok(s, a) and _along_ok(ctx, s, a)]


def sel_sphere(ctx: Ctx, a: dict) -> list:
    r = a["d"] / 2
    return [s for s in ctx.part.surfs if s.kind == "sphere" and ctx.near(s.radius, r) and _side_ok(s, a)]


def _max_radius(s: Surf) -> float:
    rel = s.points - s.axis_o
    t = rel @ s.axis_d
    return float(np.linalg.norm(rel - np.outer(t, s.axis_d), axis=1).max())


def sel_cone(ctx: Ctx, a: dict) -> list:
    """锥面；``max_r`` 限定锥面最大半径（如外缘倒角）。"""
    return [
        s for s in ctx.part.surfs
        if s.kind == "cone" and abs(s.semi_angle - a["semi_angle"]) <= ctx.tol_ang and _side_ok(s, a)
        and ("max_r" not in a or ctx.near(_max_radius(s), a["max_r"]))
    ]


def sel_bbox_axis(ctx: Ctx, a: dict) -> list:
    """frame 某坐标轴方向、穿过外形包围盒中心的直线（用于没有解析回转面的回转体）。"""
    frame = ctx.frame(a["frame"])
    return [{"o": frame.world((frame.lo + frame.hi) / 2), "d": frame.axes[a["axis"]], "points": ctx.part.points}]


def sel_torus(ctx: Ctx, a: dict) -> list:
    return [
        s for s in ctx.part.surfs
        if s.kind == "torus"
        and ctx.near(s.radius, a["minor_r"])
        and ("major_r" not in a or ctx.near(s.major, a["major_r"]))
        and _side_ok(s, a)
    ]


def sel_ring(ctx: Ctx, a: dict) -> list:
    """环形把手：圆截面（环面）或矩形截面（同轴内外圆柱 + 两端平面）。"""
    outer_r, width, thick = a["outer_d"] / 2, a["section"][0], a["section"][1]
    rings = []
    for s in ctx.part.surfs:
        if s.kind == "torus" and s.side == "outer" and ctx.near(2 * s.radius, width) \
                and ctx.near(s.major + s.radius, outer_r):
            rings.append({"o": s.axis_o, "d": s.axis_d, "center": s.axis_o, "surfs": [s], "points": s.points})
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
                    rings.append({"o": out.axis_o, "d": out.axis_d, "center": center,
                                  "surfs": [out, inn], "points": out.points})
    return rings


def sel_prism_pocket(ctx: Ctx, a: dict) -> list:
    """绕 ``about`` 轴的正多边形棱柱面：到轴距离 = 对边距/2。

    side=inner（默认）：凹槽/孔，侧面法向指向轴线；side=outer：外棱柱（如六角螺母外形）。
    """
    ax = _axis(ctx.items(a["about"])[0])
    half = a["across_flats"] / 2
    inner = a.get("side", "inner") == "inner"
    walls = []
    for s in ctx.part.surfs:
        if s.kind != "plane" or abs(float(np.dot(s.axis_d, ax.d))) > 1e-3:
            continue
        foot = ax.o + np.dot(s.points.mean(axis=0) - ax.o, ax.d) * ax.d
        dist = float(np.dot(s.axis_d, foot)) - s.offset
        if ctx.near(abs(dist), half) and (dist > 0) == inner:
            walls.append(s)
    normals: list[np.ndarray] = []
    for w in walls:
        if not any(float(np.dot(w.axis_d, n)) > 1 - 1e-6 for n in normals):
            normals.append(w.axis_d)
    if len(normals) != a["sides"]:
        return []
    if a.get("regular", True):
        basis = _complete(ax.d)
        angs = sorted(math.degrees(math.atan2(np.dot(n, basis[2]), np.dot(n, basis[1]))) % 360 for n in normals)
        gaps = np.diff(angs + [angs[0] + 360])
        if np.any(np.abs(gaps - 360 / a["sides"]) > ctx.tol_ang):
            return []
    pts = np.vstack([w.points for w in walls])
    return [{"o": ax.o, "d": ax.d, "surfs": walls, "points": pts, "normals": normals}]


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
    "sphere": sel_sphere,
    "cone": sel_cone,
    "bbox_axis": sel_bbox_axis,
    "torus": sel_torus,
    "ring": sel_ring,
    "prism_pocket": sel_prism_pocket,
    "principal_frame": sel_principal_frame,
    "world_frame": sel_world_frame,
    "axis_frame": sel_axis_frame,
    "base_frame": sel_base_frame,
    "slab": sel_slab,
}


# ---------------------------------------------------------------- checks

Result = tuple[bool, dict]


def _r(x, n=3):
    return round(float(x), n)


def ck_single_valid_solid(ctx: Ctx, a) -> Result:
    p = ctx.part
    ok = p.n_solids == 1 and p.is_valid()
    return ok, {"solids": p.n_solids, "shells": p.n_shells, "valid": p.is_valid()}


def ck_solid_count(ctx: Ctx, value) -> Result:
    """恰有 value 个实体且整体有效（如两个分开的零件）。"""
    p = ctx.part
    ok = p.n_solids == value and p.is_valid()
    return ok, {"solids": p.n_solids, "valid": p.is_valid()}


def ck_exists(ctx: Ctx, name) -> Result:
    found, count = ctx.entity(name)
    n = len(found) if isinstance(found, list) else int(found is not None)
    ok = n == count if count is not None else n >= 1
    return ok, {"found": n, "expected": count if count is not None else ">=1"}


def _coaxial(ctx: Ctx, x: Axis, y: Axis) -> bool:
    return parallel(x.d, y.d, _eps_ang(ctx)) and line_distance(y.o, y.d, x.o) <= ctx.tol_len


def ck_coaxial(ctx: Ctx, names) -> Result:
    """names[0] 的每个实体都与 names[1] 中某一个实体同轴。"""
    refs = [_axis(r) for r in ctx.items(names[1])]
    oks = [any(_coaxial(ctx, _axis(it), ref) for ref in refs) for it in ctx.items(names[0])]
    return all(oks), {"coaxial": oks}


def ck_parallel(ctx: Ctx, names) -> Result:
    ref = _axis(ctx.items(names[1])[0])
    oks = [parallel(_axis(it).d, ref.d, _eps_ang(ctx)) for it in ctx.items(names[0])]
    return all(oks), {"parallel": oks}


def ck_parallel_to_frame(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    d = frame.axes[a["axis"]]
    oks = [parallel(_axis(it).d, d, _eps_ang(ctx)) for it in ctx.items(a["items"])]
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
            lo, hi = _param_extent(_pts(it), _axis(it) if own else ax)
            vals.append(hi - lo)
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [_r(v) for v in vals]}


def ck_through(ctx: Ctx, a) -> Result:
    oks, detail = [], []
    for it in ctx.items(a["holes"]):
        ax = _axis(it)
        lo, hi = _param_extent(_pts(it), ax)
        inside = ctx.part.line_intervals(ax.o, ax.d, lo + _PROBE, hi - _PROBE)
        ends_open = not ctx.part.is_inside(ax.o + (lo - _PROBE) * ax.d) \
            and not ctx.part.is_inside(ax.o + (hi + _PROBE) * ax.d)
        oks.append(not inside and ends_open)
        detail.append({"length": _r(hi - lo), "blocked": bool(inside), "ends_open": ends_open})
    return all(oks), {"holes": detail}


def ck_axis_distance(ctx: Ctx, a) -> Result:
    ref = _axis(ctx.items(a["from"])[0])
    vals = [line_distance(ref.o, ref.d, _axis(it).o) for it in ctx.items(a["to"])]
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [_r(v) for v in vals]}


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
    return bool(np.all(np.abs(gaps - target) <= ctx.tol_ang)), {"gaps_deg": [_r(g) for g in gaps]}


def ck_volume(ctx: Ctx, value) -> Result:
    v = ctx.part.volume()
    return abs(v - value) <= ctx.tol_vol * value, {"measured": _r(v, 2), "expected": value}


def ck_volume_range(ctx: Ctx, rng) -> Result:
    v = ctx.part.volume()
    lo, hi = rng[0] * (1 - ctx.tol_vol), rng[1] * (1 + ctx.tol_vol)
    return lo <= v <= hi, {"measured": _r(v, 2), "range": [_r(lo, 2), _r(hi, 2)]}


def _sorted_extents(ctx: Ctx) -> list[float]:
    axes = principal_axes(ctx.part)
    lo, hi = ctx.part.extents(axes)
    return sorted(float(x) for x in hi - lo)


def _match_extents(ctx: Ctx, got: list[float], expected) -> bool:
    def ok(g, e):
        if isinstance(e, (list, tuple)):
            return e[0] - ctx.tol_len <= g <= e[1] + ctx.tol_len
        return ctx.near(g, e)

    exp = sorted(expected, key=lambda e: e[0] if isinstance(e, (list, tuple)) else e)
    return all(ok(g, e) for g, e in zip(got, exp))


def ck_bbox_sorted(ctx: Ctx, expected) -> Result:
    """外形尺寸（由短到长）；某项写成 [lo, hi] 表示取值区间。"""
    got = _sorted_extents(ctx)
    return _match_extents(ctx, got, expected), {"measured": [_r(g) for g in got]}


def ck_bbox_sorted_any(ctx: Ctx, options) -> Result:
    got = _sorted_extents(ctx)
    return any(_match_extents(ctx, got, e) for e in options), {"measured": [_r(g) for g in got]}


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
    return abs(span - a["value"]) <= ctx.tol_ang, {"measured": _r(span)}


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
                s = (_pts(it) - p) @ out
                match = (it, float(s.min()), float(s.max()))
                break
        if match is None:
            ends.append({"matched": False})
            continue
        items.remove(match[0])
        lo, hi = a["outward"]
        ends.append({"matched": True, "from": _r(match[1]), "to": _r(match[2]),
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
    return ok, {"axis_dot": _r(perp, 4), "plane_offset": _r(off)}


def ck_no_material_in(ctx: Ctx, a) -> Result:
    cav = ctx.items(a["cylinder_of"])[0]
    ax = _axis(cav)
    lo, hi = _param_extent(cav.points, ax)
    shrink = a.get("shrink", 0.1)
    tool = cylinder_tool(ax.o + (lo + shrink) * ax.d, ax.d, cav.radius - shrink, hi - lo - 2 * shrink)
    v = ctx.part.common_volume(tool)
    return v <= a.get("max_volume", 1.0), {"material_mm3": _r(v)}


def ck_pocket_depth(ctx: Ctx, a) -> Result:
    """每个凹槽/盲孔：沿轴深度、恰有一端被材料封底；可要求开口端位于零件端面。

    ``probe_r``：封底探测点离轴线的距离（底上另有同轴小孔时用，默认在轴线上）。

    孔底由沿轴探测线上的空段端点确定，孔口在零件端面时取该端面，因此孔口、孔底棱边上
    的圆角/倒角不影响深度；孔口不在端面时退回用孔壁面的轴向端点。
    """
    oks, details = [], []
    for it in ctx.items(a["pocket"]):
        ax = _axis(it)
        basis = _complete(ax.d)
        lo, hi = _param_extent(_pts(it), ax)
        plo, phi = ctx.part.extents(basis)
        shift = float(np.dot(ax.o, ax.d))
        e_lo, e_hi = float(plo[0]) - shift, float(phi[0]) - shift
        off = a.get("probe_r", 0.0) * basis[1]
        segs = ctx.part.line_intervals(ax.o + off, ax.d, e_lo - 1, e_hi + 1)
        mid = (lo + hi) / 2
        below = [s1 for s0, s1 in segs if s1 <= mid + 1e-6]
        above = [s0 for s0, s1 in segs if s0 >= mid - 1e-6]
        if any(s0 < mid < s1 for s0, s1 in segs):
            oks.append(False)
            details.append({"error": "探测线在凹槽中部遇到材料"})
            continue
        floor_lo, floor_hi = (max(below) if below else None), (min(above) if above else None)
        blind = (floor_lo is None) != (floor_hi is None)
        detail = {"blind": blind}
        if not blind:
            ok = not a.get("blind", True) and floor_lo is None and floor_hi is None
            detail["depth"] = _r(hi - lo)
            oks.append(ok and ctx.near(hi - lo, a["value"]))
            details.append(detail)
            continue
        open_low = floor_lo is None
        floor = floor_hi if open_low else floor_lo
        mouth = lo if open_low else hi
        if a.get("open_at_extreme"):
            edge = e_lo if open_low else e_hi
            r_wall = float(np.max(np.linalg.norm(
                (_pts(it) - ax.o) - np.outer((_pts(it) - ax.o) @ ax.d, ax.d), axis=1)))
            inward = 1.0 if open_low else -1.0
            at = any(ctx.part.is_inside(ax.o + (edge + inward * 0.3) * ax.d
                                        + (r_wall + 1.5) * (math.cos(th) * basis[1] + math.sin(th) * basis[2]))
                     for th in np.linspace(0, 2 * math.pi, 8, endpoint=False))
            detail["open_at_extreme"] = at
            if at:
                mouth = edge
        depth = abs(floor - mouth)
        detail["depth"] = _r(depth)
        ok = a.get("blind", True) and ctx.near(depth, a["value"]) and detail.get("open_at_extreme", True)
        oks.append(ok)
        details.append(detail)
    return all(oks), {"pockets": details}


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
    return ok, {"outer_len": [_r(h) for h in heads], "inner_len": [_r(s) for s in shafts]}


def ck_extreme_plane_areas(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    areas = sorted([_extreme_plane_area(ctx, frame, a["axis"], s) for s in (+1, -1)])
    exp = sorted(a["values"])
    ok = all(abs(g - e) <= ctx.tol_vol * e for g, e in zip(areas, exp))
    return ok, {"measured": [_r(x, 2) for x in areas]}


def ck_extreme_plane(ctx: Ctx, a) -> Result:
    """frame 某轴低端/高端存在平面，面积不小于 min_area（如“底部平坦”）。"""
    frame = ctx.frame(a["frame"])
    area = _extreme_plane_area(ctx, frame, a["axis"], -1 if a.get("side", "low") == "low" else +1)
    return area >= a.get("min_area", 1.0), {"area": _r(area, 2)}


def _dir_token(frame: Frame, token) -> np.ndarray:
    token = str(token)
    sign = -1.0 if token.startswith("-") else 1.0
    return sign * frame.axes[int(token.lstrip("+-"))]


def ck_ray_thickness(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    p = frame.at_fraction(a["from"])
    vals = []
    for token in a["dirs"]:
        segs = ctx.part.line_intervals(p, _dir_token(frame, token), 0, 1e4)
        vals.append(segs[0][1] - segs[0][0] if segs else 0.0)
    return all(ctx.near(v, a["value"]) for v in vals), {"measured": [_r(v) for v in vals]}


def ck_first_hit(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    p = frame.at_fraction(a["from"])
    segs = ctx.part.line_intervals(p, _dir_token(frame, a["dir"]), 0, 1e4)
    v = segs[0][0] if segs else float("inf")
    return ctx.near(v, a["value"]), {"measured": _r(v)}


def _frame_point(frame: Frame, a: dict, along: int | None = None) -> np.ndarray:
    """frame 坐标 ``at: [c0, c1, c2]``，或 axis_frame 中的 ``polar: [r, deg]`` 加 ``t``。"""
    if "polar" in a:
        r, deg = a["polar"]
        c = [a.get("t", 0.0), r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg))]
    else:
        c = [0.0 if v is None else float(v) for v in a["at"]]
    if along is not None:
        c[along] = 0.0
    return np.array(c, float)


def _match_intervals(ctx: Ctx, got: list, expect: list) -> bool:
    return len(got) == len(expect) and all(
        ctx.near(g[0], e[0]) and ctx.near(g[1], e[1]) for g, e in zip(got, expect)
    )


def ck_line_material(ctx: Ctx, a) -> Result:
    """沿 frame 轴 ``along`` 的一条直线上的材料区间（以该轴坐标表示）。"""
    frame = ctx.frame(a["frame"])
    along = int(a["along"])
    c = _frame_point(frame, a, along)
    start = float(frame.lo[along]) - 5
    c[along] = start
    p = frame.world(c)
    length = float(frame.hi[along] - frame.lo[along]) + 10
    segs = ctx.part.line_intervals(p, frame.axes[along], 0, length)
    got = [[_r(s0 + start), _r(s1 + start)] for s0, s1 in segs]
    options = a["expect_any"] if "expect_any" in a else [a["expect"]]
    return any(_match_intervals(ctx, got, e) for e in options), {"measured": got}


def ck_radial_material(ctx: Ctx, a) -> Result:
    """axis_frame 中，从轴线出发、方位角 deg、轴向坐标 t 的射线上的材料区间（以半径表示）。"""
    frame = ctx.frame(a["frame"])
    th = math.radians(a.get("deg", 0.0))
    d = math.cos(th) * frame.axes[1] + math.sin(th) * frame.axes[2]
    p = frame.world([a["t"], 0.0, 0.0])
    segs = ctx.part.line_intervals(p, d, 0, float(np.max(np.abs(np.r_[frame.lo[1:], frame.hi[1:]]))) * 2 + 5)
    got = [[_r(s0), _r(s1)] for s0, s1 in segs]
    return _match_intervals(ctx, got, a["expect"]), {"measured": got}


def ck_axis_positions(ctx: Ctx, a) -> Result:
    """实体轴线在 frame 两个坐标轴上的位置（轴线须平行于第三个坐标轴），按集合匹配。"""
    frame = ctx.frame(a["frame"])
    i, j = a["axes"]
    got = []
    for it in ctx.items(a["items"]):
        c = frame.local(_axis(it).o)
        got.append([_r(c[i]), _r(c[j])])
    remaining = list(got)
    ok = len(got) == len(a["expect"])
    for e in a["expect"]:
        hit = next((g for g in remaining if (e[0] is None or ctx.near(g[0], e[0])) and (e[1] is None or ctx.near(g[1], e[1]))), None)
        if hit is None:
            ok = False
            break
        remaining.remove(hit)
    for k in a.get("equal", []):  # 该坐标在所有实体间相同（如一排孔共线）
        ok = ok and all(ctx.near(g[k], got[0][k]) for g in got)
    return ok, {"measured": got}


def ck_axis_coords_in(ctx: Ctx, a) -> Result:
    """实体轴线在 frame 两个坐标轴上的位置落在给定区间内。"""
    frame = ctx.frame(a["frame"])
    i, j = a["axes"]
    (lo_i, hi_i), (lo_j, hi_j) = a["ranges"]
    got = []
    ok = True
    for it in ctx.items(a["items"]):
        c = frame.local(_axis(it).o)
        got.append([_r(c[i]), _r(c[j])])
        ok = ok and lo_i - ctx.tol_len <= c[i] <= hi_i + ctx.tol_len and lo_j - ctx.tol_len <= c[j] <= hi_j + ctx.tol_len
    return ok, {"measured": got}


def ck_pair_distance(ctx: Ctx, a) -> Result:
    """两个实体轴线之间的距离（轴线平行）。"""
    items = ctx.items(a["items"])
    if len(items) != 2:
        return False, {"error": f"需要 2 个实体，找到 {len(items)} 个"}
    x, y = _axis(items[0]), _axis(items[1])
    v = line_distance(x.o, x.d, y.o)
    return ctx.near(v, a["value"]), {"measured": _r(v)}


def ck_opening_size(ctx: Ctx, a) -> Result:
    """平板上的开口：在 frame 轴 normal 的中面上，从 at（或实体轴线）出发量两个面内方向的无材料范围。"""
    frame = ctx.frame(a["frame"])
    n = int(a["normal"])
    inplane = [k for k in range(3) if k != n]
    if "center" in a:
        c = frame.local(_axis(ctx.items(a["center"])[0]).o)
    else:
        c = np.array(a["at"], float)
    c[n] = (frame.lo[n] + frame.hi[n]) / 2
    p = frame.world(c)
    if ctx.part.is_inside(p):
        return False, {"error": "中心点在材料内"}
    sizes, halves = [], []
    for k in inplane:
        d = frame.axes[k]
        up = ctx.part.line_intervals(p, d, 0, 1e3)
        dn = ctx.part.line_intervals(p, -d, 0, 1e3)
        if not up or not dn:
            return False, {"error": "开口在面内方向未封闭"}
        halves.append([_r(dn[0][0]), _r(up[0][0])])
        sizes.append(up[0][0] + dn[0][0])
    through = not ctx.part.line_intervals(frame.world(np.where(np.arange(3) == n, frame.lo - 1, c)),
                                          frame.axes[n], 0, float(frame.hi[n] - frame.lo[n]) + 2)
    exp = sorted(a["size"])
    ok = all(ctx.near(g, e) for g, e in zip(sorted(sizes), exp))
    if a.get("through", True):
        ok = ok and through
    if a.get("centered"):
        ok = ok and all(ctx.near(h[0], h[1]) for h in halves)
    return ok, {"sizes": [_r(s) for s in sizes], "halves": halves, "through": through}


def _end_faces(ctx: Ctx, frame: Frame, ax: int, side: str) -> list[Surf]:
    sign = -1 if side == "low" else +1
    d = sign * frame.axes[ax]
    base = float(np.dot(frame.origin, frame.axes[ax]))
    edge = (base + frame.hi[ax]) if sign > 0 else -(base + frame.lo[ax])
    return [s for s in ctx.part.surfs
            if s.kind == "plane" and float(np.dot(s.axis_d, d)) > 1 - 1e-6 and abs(s.offset - edge) < 1e-3]


def _face_center(frame: Frame, faces: list[Surf], ax: int) -> tuple[np.ndarray, np.ndarray]:
    pts = np.vstack([f.points for f in faces])
    others = [k for k in range(3) if k != ax]
    flat = (pts - frame.origin) @ frame.axes[others].T
    return flat, (flat.min(axis=0) + flat.max(axis=0)) / 2


def ck_end_faces_coaxial(ctx: Ctx, a) -> Result:
    """frame 某轴两端的平面端面，外廓中心在另两轴上重合。"""
    frame = ctx.frame(a["frame"])
    ax = int(a["axis"])
    lo, hi = _end_faces(ctx, frame, ax, "low"), _end_faces(ctx, frame, ax, "high")
    if not lo or not hi:
        return False, {"error": "缺少端面"}
    c0, c1 = _face_center(frame, lo, ax)[1], _face_center(frame, hi, ax)[1]
    off = float(np.linalg.norm(c0 - c1))
    return off <= ctx.tol_len, {"offset": _r(off), "low": [_r(x) for x in c0], "high": [_r(x) for x in c1]}


def ck_end_face_shape(ctx: Ctx, a) -> Result:
    """frame 某轴端部的平面端面：圆（circle_d）或矩形（rect），以及面心在另两轴上的坐标。"""
    frame = ctx.frame(a["frame"])
    ax = int(a["axis"])
    faces = _end_faces(ctx, frame, ax, a.get("side", "low"))
    if not faces:
        return False, {"error": "该端没有平面"}
    flat, center = _face_center(frame, faces, ax)
    ok, det = True, {"center": [_r(x) for x in center]}
    if "circle_d" in a:
        r = np.linalg.norm(flat - center, axis=1)
        det["d"] = _r(2 * r.max())
        area = sum(f.area for f in faces)
        det["area"] = _r(area, 2)
        ok = ctx.near(2 * r.max(), a["circle_d"]) and abs(area - math.pi * a["circle_d"] ** 2 / 4) \
            <= ctx.tol_vol * math.pi * a["circle_d"] ** 2 / 4
    if "rect" in a:
        _, _, size = min_area_rect(flat)
        area = sum(f.area for f in faces)
        det["rect"], det["area"] = [_r(x) for x in size], _r(area, 2)
        exp = sorted(a["rect"], reverse=True)
        ok = ctx.near(size[0], exp[0]) and ctx.near(size[1], exp[1]) \
            and abs(area - exp[0] * exp[1]) <= ctx.tol_vol * exp[0] * exp[1]
    if "center" in a:
        ok = ok and all(ctx.near(g, e) for g, e in zip(center, a["center"]))
    return ok, det


def ck_frame_box(ctx: Ctx, a) -> Result:
    """frame 下的外形尺寸（size，null 表示不查）、世界坐标下限（world_lo）与 frame 原点的世界坐标
    （world_origin；配合 world_frame 的 anchor_planes 检查主体低端面位置）。仅用于 world_frame。"""
    frame = ctx.frame(a["frame"])
    ok, det = True, {"size": [_r(x) for x in frame.size]}
    for k, v in enumerate(a.get("size") or []):
        if v is not None and not ctx.near(float(frame.size[k]), v):
            ok = False
    if "world_origin" in a:
        det["world_origin"] = [_r(x) for x in frame.origin]
        for k, v in a["world_origin"].items():
            if not ctx.near(float(frame.origin[int(k)]), v):
                ok = False
    if "world_lo" in a:
        lo = frame.origin @ frame.axes.T + frame.lo
        det["world_lo"] = [_r(x) for x in lo]
        for k, v in a["world_lo"].items():
            if not ctx.near(float(lo[int(k)]), v):
                ok = False
    return ok, det


def ck_item_range(ctx: Ctx, a) -> Result:
    """实体在 frame 某轴上的坐标范围。"""
    frame = ctx.frame(a["frame"])
    ax = int(a["axis"])
    vals = []
    for it in ctx.items(a["items"]):
        s = (_pts(it) - frame.origin) @ frame.axes[ax]
        vals.append([_r(s.min()), _r(s.max())])
    ok = all(ctx.near(v[0], a["value"][0]) and ctx.near(v[1], a["value"][1]) for v in vals)
    return ok, {"measured": vals}


def ck_origin_centered(ctx: Ctx, a) -> Result:
    """零件沿实体轴线方向关于世界原点居中。"""
    ax = _axis(ctx.items(a["axis"])[0])
    lo, hi = ctx.part.extents(_complete(ax.d))
    return abs(float(lo[0] + hi[0])) <= ctx.tol_len, {"range": [_r(lo[0]), _r(hi[0])]}


def ck_ordered_along(ctx: Ctx, a) -> Result:
    """若干实体沿轴线的先后顺序（正反向均可）。"""
    ax = _axis(ctx.items(a["axis"])[0])
    centers = []
    for name in a["items"]:
        lo, hi = _param_extent(_pts(ctx.items(name)[0]), ax)
        centers.append((lo + hi) / 2)
    diffs = np.diff(centers)
    return bool(np.all(diffs > 0) or np.all(diffs < 0)), {"centers": [_r(c) for c in centers]}


def ck_parallel_faces(ctx: Ctx, a) -> Result:
    """存在一对反向平行平面，法向垂直于 frame 轴 perp_to，间距 = gap。"""
    frame = ctx.frame(a["frame"])
    ref = frame.axes[a["perp_to"]]
    planes = [s for s in ctx.part.surfs if s.kind == "plane" and abs(float(np.dot(s.axis_d, ref))) < 1e-3
              and s.area >= a.get("min_area", 1.0)]
    gaps, hits = [], []
    for i, p in enumerate(planes):
        for q in planes[i + 1:]:
            if float(np.dot(p.axis_d, q.axis_d)) < -1 + 1e-6:
                g = abs(p.offset + q.offset)
                gaps.append(g)
                if ctx.near(g, a["gap"]):
                    hits.append((p, q))
    ok = len(hits) >= a.get("min_pairs", 1)
    det: dict = {"gaps": sorted({_r(g) for g in gaps}), "pairs": len(hits)}
    if ok and "mid_contains" in a:
        # 至少有一对的中面同时通过所列实体的全部轴线
        axes = [_axis(it) for it in ctx.items(a["mid_contains"])]
        offs = []
        for p, q in hits:
            mid = (p.offset - q.offset) / 2
            offs.append(max(abs(float(np.dot(p.axis_d, x.o)) - mid) for x in axes))
        det["mid_offset"] = _r(min(offs))
        ok = min(offs) <= ctx.tol_len
    return ok, det


def ck_planar_face(ctx: Ctx, a) -> Result:
    """存在一个平面：法向与 frame 轴夹角 = angle，面积 = area（相对容差同体积）。"""
    frame = ctx.frame(a["frame"])
    ref = frame.axes[a["axis"]]
    found = []
    for s in ctx.part.surfs:
        if s.kind != "plane":
            continue
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(s.axis_d, ref))))))
        found.append((ang, s.area))
    ok = any(abs(ang - a["angle"]) <= ctx.tol_ang and abs(area - a["area"]) <= ctx.tol_vol * a["area"]
             for ang, area in found)
    return ok, {"planes": [[_r(x, 2), _r(y, 1)] for x, y in found if abs(x - a["angle"]) <= 5]}


def _section_area(ctx: Ctx, frame: Frame, axis: int, at: float, h: float = 0.02) -> float:
    lo, hi = frame.lo.copy() - 1, frame.hi.copy() + 1
    lo[axis], hi[axis] = at - h / 2, at + h / 2
    return ctx.part.common_volume(box_tool(frame.origin, frame.axes, lo, hi)) / h


def ck_section_areas(ctx: Ctx, a) -> Result:
    """frame 轴 axis 上若干位置的截面积：expect 为数值或 [lo, hi]；monotone: increasing/decreasing。"""
    frame = ctx.frame(a["frame"])
    vals = [_section_area(ctx, frame, a["axis"], float(t)) for t in a["at"]]
    ok = True
    for v, e in zip(vals, a.get("expect") or []):
        if e is None:
            continue
        if isinstance(e, (list, tuple)):
            ok = ok and e[0] * (1 - ctx.tol_vol) <= v <= e[1] * (1 + ctx.tol_vol)
        else:
            ok = ok and abs(v - e) <= ctx.tol_vol * e
    mono = a.get("monotone")
    if mono:
        d = np.diff(vals)
        ok = ok and bool(np.all(d < 0) if mono == "decreasing" else np.all(d > 0))
    return ok, {"areas": [_r(v, 2) for v in vals]}


def _find_arcs(mask: np.ndarray, min_len: int = 2) -> list[tuple[int, int]]:
    """环形布尔序列中连续 True 段 [i0, i1]（含端点，可跨越首尾）。

    短于 min_len 个采样的段视为探测圆恰好擦过开口边界产生的噪声，丢弃。
    """
    n = len(mask)
    if mask.all() or not mask.any():
        return []
    start = int(np.argmin(mask))  # 从一个 False 开始，避免段跨越起点
    arcs, i = [], 0
    while i < n:
        k = (start + i) % n
        if mask[k]:
            j = i
            while j + 1 < n and mask[(start + j + 1) % n]:
                j += 1
            if j - i + 1 >= min_len:
                arcs.append(((start + i) % n, (start + j) % n))
            i = j + 1
        else:
            i += 1
    return arcs


def ck_angular_openings(ctx: Ctx, a) -> Result:
    """axis_frame 中半径 r、轴向 t 处圆周上的无材料弧段：数量、均布、弧长/弦宽、径向范围。

    ``scan_r: [r0, r1, step]`` 时在该范围内找第一个满足数量的半径。
    """
    frame = ctx.frame(a["frame"])
    n_samples = a.get("samples", 720)
    ts = a.get("t_list") or [a["t"]]
    radii = [a["r"]] if "r" in a else list(np.arange(a["scan_r"][0], a["scan_r"][1] + 1e-9, a["scan_r"][2]))
    angles = np.linspace(0, 360, n_samples, endpoint=False)
    e1, e2 = frame.axes[1], frame.axes[2]

    def pt(t, r, deg):
        th = math.radians(deg)
        return frame.world([t, 0, 0]) + r * (math.cos(th) * e1 + math.sin(th) * e2)

    def empty(t, r, deg):
        return not ctx.part.is_inside(pt(t, r, deg))

    per_t = []
    for t in ts:
        found = None
        for r in radii:
            mask = np.array([empty(t, r, deg) for deg in angles])
            arcs = _find_arcs(mask)
            if len(arcs) == a["count"]:
                found = (r, arcs)
                break
        if found is None:
            per_t.append({"t": t, "count": len(arcs) if radii else 0, "ok": False})
            continue
        r, arcs = found
        step = 360 / n_samples
        openings = []
        for i0, i1 in arcs:
            a0 = angles[i0] - step
            lo_deg = _bisect(lambda x: empty(t, r, x), angles[i0], a0)
            hi_deg = _bisect(lambda x: empty(t, r, x), angles[i1], angles[i1] + step)
            span = (hi_deg - lo_deg) % 360
            center = (lo_deg + span / 2) % 360
            th = math.radians(center)
            d = math.cos(th) * e1 + math.sin(th) * e2
            segs = ctx.part.line_intervals(frame.world([t, 0, 0]), d, 0, 1e4)
            r_in = max([s1 for s0, s1 in segs if s1 <= r] or [0.0])
            outer = [s0 for s0, s1 in segs if s0 >= r]
            r_out = min(outer) if outer else None
            openings.append({"center": center, "arc_deg": span, "chord": 2 * r * math.sin(math.radians(span) / 2),
                             "r_in": r_in, "r_out": r_out})
        centers = sorted(o["center"] for o in openings)
        gaps = np.diff(centers + [centers[0] + 360])
        ok = bool(np.all(np.abs(gaps - 360 / a["count"]) <= ctx.tol_ang))
        if "arc_deg" in a:
            ok = ok and all(abs(o["arc_deg"] - a["arc_deg"]) <= ctx.tol_ang for o in openings)
        if "chord" in a:
            ok = ok and all(ctx.near(o["chord"], a["chord"]) for o in openings)
        if "radial" in a:
            want_in, want_out = a["radial"]
            for o in openings:
                ok = ok and (want_in is None or ctx.near(o["r_in"], want_in))
                ok = ok and (o["r_out"] is None if want_out == "open"
                             else (want_out is None or (o["r_out"] is not None and ctx.near(o["r_out"], want_out))))
        if a.get("identical"):
            ref = openings[0]
            for o in openings[1:]:
                ok = ok and abs(o["arc_deg"] - ref["arc_deg"]) <= ctx.tol_ang and ctx.near(o["r_in"], ref["r_in"]) \
                    and ((o["r_out"] is None) == (ref["r_out"] is None)) \
                    and (o["r_out"] is None or ctx.near(o["r_out"], ref["r_out"]))
        if "min_r_in" in a:
            ok = ok and all(o["r_in"] >= a["min_r_in"] for o in openings)
        per_t.append({
            "t": t, "r": _r(r), "count": len(openings), "gaps_deg": [_r(g, 2) for g in gaps],
            "arc_deg": [_r(o["arc_deg"], 2) for o in openings], "chord": [_r(o["chord"]) for o in openings],
            "r_in": [_r(o["r_in"]) for o in openings],
            "r_out": [None if o["r_out"] is None else _r(o["r_out"]) for o in openings], "ok": ok,
        })
    return all(p["ok"] for p in per_t), {"sections": per_t}


def _bisect(fn: Callable[[float], bool], a: float, b: float, n: int = 30) -> float:
    """fn(a) 为真、fn(b) 为假时的边界。"""
    for _ in range(n):
        m = (a + b) / 2
        a, b = (m, b) if fn(m) else (a, m)
    return (a + b) / 2


def ck_rotation_invariant(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    xor = ctx.part.rotate_xor_volume(frame.origin, frame.axes[0], a.get("angle_deg", 37.0))
    rel = xor / ctx.part.volume()
    return rel <= a.get("max_xor_rel", 1e-3), {"xor_rel": _r(rel, 5)}


def _outer_radius(ctx: Ctx, frame: Frame, t: float) -> float:
    segs = ctx.part.line_intervals(frame.world([t, 0, 0]), frame.axes[1], 0, 1e4)
    return segs[-1][1] if segs else 0.0


def ck_revolved_profile(ctx: Ctx, a) -> Result:
    """回转体外轮廓：最大直径及其位置、其上方最细处直径、顶端直径。

    ``max_at: [lo, hi]``（高度比例）：全局最大直径须在该高度段内取到（最大段可延伸到段外，
    如下半部为直筒）；瓶颈取该段内最后一个最大值位置上方的最细处。
    """
    frame = ctx.frame(a["frame"])
    height = float(frame.hi[0])
    step = a.get("step", 0.25)
    ts = np.arange(0.25, height - 0.05 + 1e-9, step)
    radii = np.array([_outer_radius(ctx, frame, t) for t in ts])
    lo, hi = a["max_at"]
    d_max = 2 * float(radii.max())
    band = np.nonzero((ts >= lo * height) & (ts <= hi * height) & (2 * radii >= d_max - ctx.tol_len))[0]
    k = int(band[-1]) if len(band) else int(np.argmax(radii))
    t_max = float(ts[k])
    above = radii[k:]
    j = int(np.argmin(above))
    d_neck, t_neck = 2 * float(above[j]), float(ts[k + j])
    t_top = height - 0.05
    d_top = 2 * _outer_radius(ctx, frame, t_top)
    segs = ctx.part.line_intervals(frame.world([t_top, 0, 0]), frame.axes[1], 0, 1e4)
    d_top_in = 2 * segs[0][0] if segs else 0.0
    top_ok = ctx.near(d_top, a["top_d"]) or (a.get("top_inner_ok") and ctx.near(d_top_in, a["top_d"]))
    ok = ctx.near(d_max, a["max_d"]) and len(band) > 0
    ok = ok and ctx.near(d_neck, a["neck_d"]) and t_neck < height - 1 and bool(top_ok)
    return ok, {"max_d": _r(d_max), "max_at": _r(t_max, 2), "neck_d": _r(d_neck), "neck_at": _r(t_neck, 2),
                "top_d": _r(d_top), "top_inner_d": _r(d_top_in), "height": _r(height)}


def ck_pits(ctx: Ctx, a) -> Result:
    """立方体各面上的圆形凹坑（球面或平底圆柱）：每面数量、对面数量和、深度。"""
    frame = ctx.frame(a["frame"])
    r = a["diameter"] / 2
    pits = [s for s in ctx.part.surfs if s.side == "inner" and ctx.near(s.radius, r)
            and ((s.kind == "sphere") or (s.kind == "cylinder" and s.angular_coverage()[2] > 300))]
    counts: dict[tuple[int, int], int] = {}
    depths = []
    for s in pits:
        c = frame.local(s.axis_o) if s.kind == "sphere" else frame.local(s.points.mean(axis=0))
        dists = [(float(c[i] - frame.lo[i]), i, -1) for i in range(3)] + [(float(frame.hi[i] - c[i]), i, +1) for i in range(3)]
        _, i, sgn = min(dists)
        counts[(i, sgn)] = counts.get((i, sgn), 0) + 1
        face = frame.lo[i] if sgn < 0 else frame.hi[i]
        q = c.copy()
        q[i] = face + sgn * 0.5
        segs = ctx.part.line_intervals(frame.world(q), -sgn * frame.axes[i], 0, 50)
        depths.append(segs[0][0] - 0.5 if segs else float("inf"))
    per_face = sorted(counts.values())
    ok = per_face == sorted(a["counts"]) and len(counts) == 6
    ok = ok and all(counts.get((i, -1), 0) + counts.get((i, 1), 0) == a["opposite_sum"] for i in range(3))
    ok = ok and all(ctx.near(d, a["depth"]) for d in depths)
    return ok, {"per_face": {f"{i}{'+' if s > 0 else '-'}": n for (i, s), n in sorted(counts.items())},
                "depth": sorted({_r(d) for d in depths})}


def ck_recess(ctx: Ctx, a) -> Result:
    """孔口的沉孔/沉头：孔口直径 top_d、深 depth（平底或锥形均可），并要求全部位于同一侧。"""
    oks, det, sides = [], [], []
    for it in ctx.items(a["holes"]):
        ax = _axis(it)
        basis = _complete(ax.d)
        plo, phi = ctx.part.extents(basis)
        shift = float(np.dot(ax.o, ax.d))
        best = None
        for sgn, edge in ((+1, phi[0]), (-1, plo[0])):
            q = ax.o + (edge - shift) * ax.d - sgn * _PROBE * ax.d
            segs = ctx.part.line_intervals(q, basis[1], 0, 1e3)
            r_top = segs[0][0] if segs else 0.0
            if best is None or r_top > best[1]:
                best = (sgn, r_top, edge)
        sgn, r_top, edge = best
        start = ax.o + (edge - shift) * ax.d + sgn * 1.0 * ax.d + (it.radius + _PROBE) * basis[1]
        segs = ctx.part.line_intervals(start, -sgn * ax.d, 0, 1e3)
        depth = segs[0][0] - 1.0 if segs else float("inf")
        oks.append(ctx.near(2 * r_top, a["top_d"]) and ctx.near(depth, a["depth"]))
        det.append({"top_d": _r(2 * r_top), "depth": _r(depth)})
        sides.append(sgn * ax.d)
    same = all(float(np.dot(s, sides[0])) > 0.99 for s in sides)
    return all(oks) and same, {"holes": det, "same_side": same}


def ck_cone_size(ctx: Ctx, a) -> Result:
    """每个锥面（倒角）的径向与轴向尺寸。"""
    vals = []
    for s in ctx.items(a["cones"]):
        rel = s.points - s.axis_o
        t = rel @ s.axis_d
        radial = np.linalg.norm(rel - np.outer(t, s.axis_d), axis=1)
        vals.append([_r(radial.max() - radial.min()), _r(t.max() - t.min())])
    ok = all(ctx.near(v[0], a["radial"]) and ctx.near(v[1], a["axial"]) for v in vals)
    return ok, {"measured": vals}


def ck_edge_notches(ctx: Ctx, a) -> Result:
    """条形部分一侧边缘上的缺口：数量与等距；另一侧无缺口。

    条形部分由一对间距 gap、法向垂直于 frame 轴 perp_to 的反向平行平面确定；
    ``exclude`` 实体（如钥匙头）半径 + 0.5 范围内不计。
    """
    frame = ctx.frame(a["frame"])
    ref = frame.axes[a["perp_to"]]
    planes = [s for s in ctx.part.surfs if s.kind == "plane" and abs(float(np.dot(s.axis_d, ref))) < 1e-3]
    pair = None
    for i, p in enumerate(planes):
        for q in planes[i + 1:]:
            if float(np.dot(p.axis_d, q.axis_d)) < -1 + 1e-6 and ctx.near(abs(p.offset + q.offset), a["gap"]):
                pair = (p, q)
                break
        if pair:
            break
    if pair is None:
        return False, {"error": "未找到条形部分"}
    p, q = pair
    n = p.axis_d
    mid = (p.offset - q.offset) / 2
    along = unit(np.cross(n, ref))
    mid_t = frame.world((frame.lo + frame.hi) / 2)
    base = mid_t + (mid - float(np.dot(n, mid_t))) * n
    s_all = ctx.part.points @ along
    s0, s1 = float(s_all.min()), float(s_all.max())
    ex = ctx.items(a["exclude"])[0]
    c = float(np.dot(_axis(ex).o, along))
    r = ex.radius + 0.5
    if abs(c - s0) < abs(c - s1):
        s0 = c + r
    else:
        s1 = c - r
    counts, pitch = [], []
    for sgn in (+1, -1):
        line_p = base + sgn * (a["gap"] / 2 - 0.3) * n + (s0 - float(np.dot(base, along))) * along
        segs = ctx.part.line_intervals(line_p, along, 0, s1 - s0)
        gaps = [(segs[k][1], segs[k + 1][0]) for k in range(len(segs) - 1)]
        if segs and segs[0][0] > 1e-6:
            gaps.insert(0, (0.0, segs[0][0]))
        if segs and segs[-1][1] < s1 - s0 - 1e-6:
            gaps.append((segs[-1][1], s1 - s0))
        counts.append(len(gaps))
        # 间距取相邻缺口同侧、未被条形端部（或排除区）截断的槽壁之差；两侧都截断时才退回用中点
        span = s1 - s0
        pp = []
        for (a0, a1), (b0, b1) in zip(gaps, gaps[1:]):
            if a0 > 1e-6 and b0 > 1e-6:
                pp.append(b0 - a0)
            elif a1 < span - 1e-6 and b1 < span - 1e-6:
                pp.append(b1 - a1)
            else:
                pp.append((b0 + b1 - a0 - a1) / 2)
        pitch.append([_r(x) for x in pp])
    k = 0 if counts[0] >= counts[1] else 1
    ok = counts[k] == a["count"] and counts[1 - k] == 0
    if a.get("equal_pitch") and len(pitch[k]) > 1:
        ok = ok and max(pitch[k]) - min(pitch[k]) <= ctx.tol_len
    return ok, {"counts": counts, "pitch": pitch[k]}


# ---- 底座类零件（手机支架等）


def ck_footprint(ctx: Ctx, a) -> Result:
    size = ctx.frame(a["frame"]).extra["size"]
    exp = sorted(a["value"], reverse=True)
    return all(ctx.near(g, e) for g, e in zip(size, exp)), {"measured": [_r(x) for x in size]}


def ck_bottom_planar(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    size = frame.extra["size"]
    fill = frame.extra["bottom"].area / float(size[0] * size[1])
    return fill >= a.get("min_fill", 0.7), {"bottom_fill": _r(fill)}


def ck_total_height(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    h = float(frame.hi[2])
    v = a["value"]
    if isinstance(v, (list, tuple)):
        return v[0] - ctx.tol_len <= h <= v[1] + ctx.tol_len, {"measured": _r(h)}
    return ctx.near(h, v), {"measured": _r(h)}


def ck_slab_thickness(ctx: Ctx, a) -> Result:
    t = ctx.items(a["slab"])[0]["thickness"]
    return ctx.near(t, a["value"]), {"measured": _r(t)}


def ck_slab_width(ctx: Ctx, a) -> Result:
    slab, frame = ctx.items(a["slab"])[0], ctx.frame(a["frame"])
    w = slab["points"] @ frame.axes[1]
    v = float(w.max() - w.min())
    return ctx.near(v, a["value"]), {"measured": _r(v)}


def _slab_angle(slab, frame: Frame) -> float:
    return math.degrees(math.acos(min(1.0, abs(float(np.dot(slab["n"], frame.axes[2]))))))


def ck_angle_to_bottom(ctx: Ctx, a) -> Result:
    ang = _slab_angle(ctx.items(a["slab"])[0], ctx.frame(a["frame"]))
    return abs(ang - a["value"]) <= ctx.tol_ang, {"measured": _r(ang)}


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
    return ok, {"fill": _r(fill), "material_above_mm3": _r(above)}


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
    return ok, {"excess_mm3": [_r(e, 1) for e in ends]}


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
        return _bisect(fn, a, b)

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
    return ok, {"height_vertical": _r(height), "height_in_plane": _r(height / pr.sin),
                "width": _r(width), "centered": centered, "through": through}


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
    return ok, {"count": len(slots), "heights_vertical": [_r(v) for v in sizes],
                "widths": [_r(s["width"]) for s in slots], "pitch_vertical": [_r(p) for p in pitches]}


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
        found.append([_r(x) for x in size])
    exp = sorted(a["size"], reverse=True)
    ok = len(found) == a["count"] and all(ctx.near(f[0], exp[0]) and ctx.near(f[1], exp[1]) for f in found)
    return ok, {"pockets": found}


def ck_blend_faces(ctx: Ctx, a) -> Result:
    """圆角面：半径 radius 的圆柱面（圆心角 ≤ 100°）或环面/球面；可限定凸凹与位置。

    ``near: {frame, axis, side: low|high, within}`` 要求圆角面全部位于该端 within 范围内。
    """
    r = a["radius"]
    near = a.get("near")
    frame = ctx.frame(near["frame"]) if near else None
    n = 0
    for s in ctx.part.surfs:
        if a.get("side") and s.side != a["side"]:
            continue
        hit = (s.kind == "cylinder" and ctx.near(s.radius, r) and s.angular_coverage()[2] <= 100) \
            or (s.kind == "torus" and ctx.near(s.radius, r)) \
            or (s.kind == "sphere" and ctx.near(s.radius, r) and a.get("spheres", False))
        if hit and frame is not None:
            c = (s.points - frame.origin) @ frame.axes[near["axis"]]
            if near.get("side", "high") == "high":
                hit = float(c.min()) >= float(frame.hi[near["axis"]]) - near["within"] - ctx.tol_len
            else:
                hit = float(c.max()) <= float(frame.lo[near["axis"]]) + near["within"] + ctx.tol_len
        n += bool(hit)
    return n >= a.get("min_count", 1), {"found": n}


def ck_mirror_symmetric(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    point = frame.extra.get("sym_origin")
    if point is None:
        point = frame.world((frame.lo + frame.hi) / 2)
    xor = ctx.part.mirror_xor_volume(point, frame.axes[a["normal"]])
    rel = xor / ctx.part.volume()
    return rel <= a.get("max_xor_rel", 1e-3), {"xor_rel": _r(rel, 5)}


def ck_tilts_away_from_lip(ctx: Ctx, a) -> Result:
    frame = ctx.frame(a["frame"])
    if "lip_sign" not in frame.extra:
        ck_end_block(ctx, a["lip"] | {"frame": a["frame"]})
    slab = ctx.items(a["slab"])[0]
    up = frame.axes[2]
    s = unit(up - np.dot(up, slab["n"]) * slab["n"])
    lean = float(np.dot(s, frame.axes[0])) * frame.extra["lip_sign"]
    return lean < -1e-3, {"lean_toward_lip": _r(lean, 4)}


CHECKS: dict[str, Callable[[Ctx, Any], Result]] = {
    name[3:]: fn for name, fn in globals().items() if name.startswith("ck_")
}


# ---------------------------------------------------------------- runner


def _plain(x):
    """把 numpy 标量等转成 JSON 可写的 Python 原生类型。"""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.floating):
        return float(x)
    return x


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
            except (ZeroDivisionError, ValueError, IndexError, Standard_Failure) as exc:
                # 几何退化（如背板与探测方向平行）：该条判为不满足，并记录原因
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
            checks.append({"check": name, "ok": bool(ok), **_plain(detail)})
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
