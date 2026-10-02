"""基于投影图像的三视图相似度：把实体沿三个正交方向投影成图像，与参考实体逐像素比较。

每个方向生成四张图（像素边长 = 参考件最大外形尺寸 / RES）：
- 轮廓：该像素的视线是否穿过实体；
- 近侧深度、远侧深度：视线第一次、最后一次碰到表面的位置（对应正面和背面两个视图）；
- 材料厚度：视线穿过材料的总长度（类似 X 光片，能看到盲腔、背面凹槽和内部空腔）。

一个像素“一致”指两件都有轮廓，且三种深度之差都不超过 TOL × 最大外形尺寸
（允许 1 个像素的位置误差，见 _compare_view）。
某方向的得分 = 一致像素数 / 两件轮廓并集的像素数；图像得分 = 100 × 三个方向得分的平均。

提交件的摆放不限：比较前在 24 种正交旋转中取得分最高的一种（不含镜像），
平移按各自包围盒对齐（图像平面内对齐中心，深度从包围盒最小面起算）。只用 numpy 光栅化，不依赖渲染库。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape

RES = 400
TOL = 0.005
VIEW_NAMES = {2: "front", 1: "top", 0: "right"}  # 观察方向沿参考件的 Z / Y / X 轴
_JITTER = (0.0137, 0.0071)  # 像素中心的微小偏移，避免采样点恰好落在三角形公共边上


@dataclass
class ViewMaps:
    hit: np.ndarray
    near: np.ndarray
    far: np.ndarray
    thick: np.ndarray


@dataclass
class Projection:
    px: float
    extent: np.ndarray  # 世界坐标包围盒尺寸
    views: dict[int, ViewMaps]  # 观察轴 k -> 图像，数组下标 [沿较小编号的另一轴, 沿较大编号的另一轴]


def triangles(shape: TopoDS_Shape, deflection: float) -> np.ndarray:
    """实体表面的三角形（外法向按右手顺序），形状 (T, 3, 3)。"""
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.2, True)
    out = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            nodes = np.array([tri.Node(i).Transformed(trsf).Coord() for i in range(1, tri.NbNodes() + 1)])
            idx = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
            if face.Orientation() == TopAbs_REVERSED:
                idx = idx[:, [0, 2, 1]]
            out.append(nodes[idx])
        exp.Next()
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _rasterize(uv: np.ndarray, w: np.ndarray, sign: np.ndarray, shape: tuple[int, int]) -> ViewMaps:
    n0, n1 = shape
    near = np.full(n0 * n1, np.inf)
    far = np.full(n0 * n1, -np.inf)
    thick = np.zeros(n0 * n1)
    hits = np.zeros(n0 * n1, dtype=np.int32)
    a, b, c = uv[:, 0], uv[:, 1], uv[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    keep = np.abs(area) > 1e-12
    a, b, c, w, sign, area = a[keep], b[keep], c[keep], w[keep], sign[keep], area[keep]
    lo = np.ceil(np.minimum(np.minimum(a, b), c) - 0.5 - _JITTER).astype(int)
    hi = np.floor(np.maximum(np.maximum(a, b), c) - 0.5 - _JITTER).astype(int)
    lo = np.maximum(lo, 0)
    hi = np.minimum(hi, [n0 - 1, n1 - 1])
    size = hi - lo + 1
    valid = np.all(size > 0, axis=1)

    def scatter(sel, i, j, inside, lam):
        tri = np.nonzero(sel)[0]
        rows, cols = np.nonzero(inside)
        t = tri[rows]
        flat = i[rows, cols] * n1 + j[rows, cols]
        depth = (lam[0][rows, cols] * w[t, 0] + lam[1][rows, cols] * w[t, 1] + lam[2][rows, cols] * w[t, 2])
        np.minimum.at(near, flat, depth)
        np.maximum.at(far, flat, depth)
        np.add.at(thick, flat, sign[t] * depth)
        np.add.at(hits, flat, 1)

    def batch(sel, k0, k1):
        if not np.any(sel):
            return
        oi, oj = np.meshgrid(np.arange(k0), np.arange(k1), indexing="ij")
        i = lo[sel, 0][:, None] + oi.ravel()[None, :]
        j = lo[sel, 1][:, None] + oj.ravel()[None, :]
        pu, pv = i + 0.5 + _JITTER[0], j + 0.5 + _JITTER[1]
        aa, bb, cc, ar = a[sel], b[sel], c[sel], area[sel][:, None]
        l0 = ((bb[:, 0, None] - pu) * (cc[:, 1, None] - pv) - (bb[:, 1, None] - pv) * (cc[:, 0, None] - pu)) / ar
        l1 = ((cc[:, 0, None] - pu) * (aa[:, 1, None] - pv) - (cc[:, 1, None] - pv) * (aa[:, 0, None] - pu)) / ar
        l2 = 1.0 - l0 - l1
        inside = (l0 >= 0) & (l1 >= 0) & (l2 >= 0)
        inside &= (i <= hi[sel, 0][:, None]) & (j <= hi[sel, 1][:, None])
        scatter(sel, i, j, inside, (l0, l1, l2))

    small = valid & np.all(size <= 8, axis=1)
    batch(small, 8, 8)
    for t in np.nonzero(valid & ~small)[0]:
        sel = np.zeros(len(a), bool)
        sel[t] = True
        batch(sel, int(size[t, 0]), int(size[t, 1]))
    hit = hits.reshape(shape) > 0
    near = np.where(hit, near.reshape(shape), 0.0)
    far = np.where(hit, far.reshape(shape), 0.0)
    return ViewMaps(hit, near, far, thick.reshape(shape))


def project(shape: TopoDS_Shape, px: float) -> Projection:
    tris = triangles(shape, min(0.05, px / 4))
    if len(tris) == 0:
        raise ValueError("实体没有可三角化的表面")
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    rel = tris - lo
    normal = np.cross(rel[:, 1] - rel[:, 0], rel[:, 2] - rel[:, 0])
    views = {}
    for k in range(3):
        a, b = [x for x in range(3) if x != k]
        ext = hi - lo
        shape2 = (max(1, math.ceil(ext[a] / px)), max(1, math.ceil(ext[b] / px)))
        # 网格以包围盒中心为中心，翻转后像素中心仍落在像素中心上
        uv = np.stack([(rel[:, :, a] - ext[a] / 2) / px + shape2[0] / 2,
                       (rel[:, :, b] - ext[b] / 2) / px + shape2[1] / 2], axis=-1)
        views[k] = _rasterize(uv, rel[:, :, k], np.sign(normal[:, k]), shape2)
    return Projection(px, hi - lo, views)


def _rotations():
    for perm in itertools.permutations(range(3)):
        parity = sum(1 for i in range(3) for j in range(i + 1, 3) if perm[i] > perm[j]) % 2
        for signs in itertools.product((1, -1), repeat=3):
            if (-1) ** parity * signs[0] * signs[1] * signs[2] == 1:
                yield perm, signs


def _rotated_view(p: Projection, perm, signs, i: int) -> ViewMaps:
    """候选件旋转后（新轴 i = signs[i] · 旧轴 perm[i]）沿新轴 i 观察的图像。"""
    k = perm[i]
    j, l = [x for x in range(3) if x != i]
    m = p.views[k]
    arrays = [m.hit, m.near, m.far, m.thick]
    old_a = min(x for x in range(3) if x != k)
    if perm[j] != old_a:
        arrays = [x.T for x in arrays]
    for axis, s in ((0, signs[j]), (1, signs[l])):
        if s < 0:
            arrays = [np.flip(x, axis=axis) for x in arrays]
    hit, near, far, thick = arrays
    if signs[i] < 0:
        ext = p.extent[k]
        near, far = np.where(hit, ext - far, 0.0), np.where(hit, ext - near, 0.0)
    return ViewMaps(hit, near, far, thick)


def _pad(x: np.ndarray, shape) -> np.ndarray:
    out = np.zeros(shape, dtype=x.dtype)
    o0, o1 = (shape[0] - x.shape[0]) // 2, (shape[1] - x.shape[1]) // 2
    out[o0: o0 + x.shape[0], o1: o1 + x.shape[1]] = x
    return out


def _shifts(x: np.ndarray, fill):
    p = np.pad(x, 1, constant_values=fill)
    n0, n1 = x.shape
    return np.stack([p[i: i + n0, j: j + n1] for i in range(3) for j in range(3)])


def _neighbourhood(values: np.ndarray, hit: np.ndarray):
    """3×3 邻域内（仅限有材料的像素）的最小值、最大值。"""
    v = _shifts(values, 0.0)
    h = _shifts(hit, False)
    return np.where(h, v, np.inf).min(axis=0), np.where(h, v, -np.inf).max(axis=0)


def _within(x, lo_hi, tol):
    lo, hi = lo_hi
    return (x >= lo - tol) & (x <= hi + tol)


def _compare_view(c: ViewMaps, r: ViewMaps, tol: float) -> dict:
    """允许 1 个像素的位置误差：深度落在对方 3×3 邻域取值范围内（加容差）即算一致，
    轮廓只差一圈像素的边缘不计入。小于 1 像素的特征本来就低于分辨率。"""
    shape = (max(c.hit.shape[0], r.hit.shape[0]), max(c.hit.shape[1], r.hit.shape[1]))
    ch, rh = _pad(c.hit, shape), _pad(r.hit, shape)
    both = ch & rh
    edge = (ch & ~rh & _shifts(rh, False).any(axis=0)) | (rh & ~ch & _shifts(ch, False).any(axis=0))
    union = (ch | rh) & ~edge
    good = both.copy()
    for name in ("near", "far", "thick"):
        cv, rv = _pad(getattr(c, name), shape), _pad(getattr(r, name), shape)
        good &= (np.abs(cv - rv) <= tol) | _within(cv, _neighbourhood(rv, rh), tol) | _within(rv, _neighbourhood(cv, ch), tol)
    n_union = int(union.sum())
    n_raw = int((ch | rh).sum())
    return {
        "score": float(good.sum() / n_union) if n_union else 0.0,
        "iou": float(both.sum() / n_raw) if n_raw else 0.0,
        "masks": (ch, rh, good | edge),
    }


def compare(cand: Projection, ref: Projection, tol: float) -> dict:
    """在 24 种正交旋转中取最好的一种；包围盒尺寸明显对不上的轴排列（比最优排列差出
    最大尺寸的 5% 以上）不可能得分更高，直接跳过。"""
    rots = list(_rotations())
    err = [float(np.abs(cand.extent[list(p)] - ref.extent).max()) for p, _ in rots]
    cutoff = min(err) + 0.05 * float(ref.extent.max())
    best = None
    for (perm, signs), e in zip(rots, err):
        if e > cutoff:
            continue
        per = {i: _compare_view(_rotated_view(cand, perm, signs, i), ref.views[i], tol) for i in range(3)}
        mean = sum(v["score"] for v in per.values()) / 3
        if best is None or mean > best[0] + 1e-12:
            best = (mean, perm, signs, per)
    mean, perm, signs, per = best
    return {
        "image_score": round(100 * mean, 2),
        "silhouette_iou": round(sum(v["iou"] for v in per.values()) / 3, 4),
        "views": {VIEW_NAMES[i]: {"score": round(v["score"], 4), "iou": round(v["iou"], 4)} for i, v in per.items()},
        "rotation": {"perm": list(perm), "signs": list(signs)},
        "_masks": {VIEW_NAMES[i]: v["masks"] for i, v in per.items()},
    }


class Reference:
    """参考实体的投影，按参考件尺寸确定像素边长和深度容差。"""

    def __init__(self, shape: TopoDS_Shape, res: int = RES, tol: float = TOL):
        tris = triangles(shape, 0.05)
        pts = tris.reshape(-1, 3)
        size = float((pts.max(axis=0) - pts.min(axis=0)).max())
        self.px = size / res
        self.tol = tol * size
        self.proj = project(shape, self.px)

    def score(self, shape: TopoDS_Shape) -> dict:
        return compare(project(shape, self.px), self.proj, self.tol)


def save_diff(result: dict, path: str | Path) -> None:
    """三个方向的差异图：灰 = 一致，红 = 都有材料但深度不一致，蓝 = 只有提交件，绿 = 只有参考件。"""
    from PIL import Image

    tiles = []
    for name in ("front", "top", "right"):
        ch, rh, good = result["_masks"][name]
        img = np.full(ch.shape + (3,), 255, np.uint8)
        img[ch & rh] = (220, 60, 60)
        img[good] = (150, 150, 150)
        img[ch & ~rh] = (60, 90, 230)
        img[rh & ~ch] = (60, 180, 80)
        tiles.append(np.flip(np.transpose(img, (1, 0, 2)), axis=0))
    h = max(t.shape[0] for t in tiles)
    parts = []
    for t in tiles:
        parts.append(np.pad(t, ((0, h - t.shape[0]), (0, 8), (0, 0)), constant_values=255))
    Image.fromarray(np.concatenate(parts, axis=1)).save(path)
